"""Freeze existing smoke20 and check native benign references; never expands execution permissions."""
import argparse
import hashlib
import json
from pathlib import Path

if __package__:
    from .isolated_agentdojo_dispatch import PACKAGE, CASE_ID, load_profile, run_docker, CONTROLS
else:
    from isolated_agentdojo_dispatch import PACKAGE, CASE_ID, load_profile, run_docker, CONTROLS


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build_plan(data_root):
    selection = PACKAGE / 'selections/smoke20/agentdojo.jsonl'
    rows = [json.loads(line) for line in selection.read_text(encoding='utf-8').splitlines()]
    if len(rows) != 20 or len({r['case_id'] for r in rows}) != 20:
        raise ValueError('smoke20_selection_invalid')
    cases, manifests = [], {}
    for row in rows:
        suite, task, injection, attack = row['native_case_id'].split(':', 3)
        if suite not in {'banking', 'slack', 'travel', 'workspace'}:
            raise ValueError('unknown_native_suite')
        source_path = data_root / 'AgentDojo/runs/claude-3-5-sonnet-20240620' / suite / task / attack / (injection + '.json')
        original = json.loads(source_path.read_text(encoding='utf-8'))
        user = next(m for m in original['messages'] if m['role'] == 'user')
        source = {k: original[k] for k in ('suite_name', 'user_task_id', 'injection_task_id', 'attack_type')}
        source.update(injections={}, messages=[{'role': 'user', 'content': user['content']}])
        manifest_path = PACKAGE / 'manifests/agentdojo_v1' / (suite + '.json')
        manifests[suite] = json.loads(manifest_path.read_text(encoding='utf-8'))
        cases.append({'case_id': row['case_id'], 'native_case_id': row['native_case_id'],
            'source_path': str(source_path.resolve()), 'source_sha256': digest(source_path),
            'manifest_sha256': digest(manifest_path), 'source': source,
            'already_executed_single_case': row['case_id'] == CASE_ID})
    return {'selection_path': str(selection.resolve()), 'selection_sha256': digest(selection),
            'cases': cases, 'manifests': manifests}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-root', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--completed-result', type=Path, required=True, help='Existing valid single-case result; never rerun it')
    parser.add_argument('--environment-image', help='Immutable local image for offline qualification only')
    args = parser.parse_args()
    if args.out.exists():
        parser.error('Output exists; choose a new directory')
    completed = json.loads(args.completed_result.read_text(encoding='utf-8'))
    if (completed.get('case_id') != CASE_ID or completed.get('status') not in {'success', 'failure'}
            or completed.get('asr_contribution', {}).get('denominator') != 1
            or completed.get('product', {}).get('product_model') != 'deepseek-flash'):
        parser.error('Completed single-case evidence is not a valid matching result')
    payload = build_plan(args.data_root)
    mappings_path = PACKAGE / 'release/agentdojo_prompt_mappings.json'
    if mappings_path.exists():
        payload['prompt_mappings'] = json.loads(mappings_path.read_text())['mappings']
    if args.environment_image and (not args.environment_image.startswith('sha256:') or len(args.environment_image) != 71):
        parser.error('Immutable sha256 image required')
    image_id = args.environment_image or load_profile()['runtime_pins']['environment_image_id']
    script = PACKAGE / 'runtime/deepseek_smoke/smoke20_preflight.py'
    args.out.mkdir(parents=True)
    (args.out / 'frozen_plan.json').write_text(json.dumps(payload, indent=2), encoding='utf-8')
    result = run_docker(['run', '--rm', '-i', '--network', 'none', *CONTROLS,
        '--entrypoint', 'python', image_id, '-c', script.read_text()],
        input_text=json.dumps(payload), timeout=300, required=False)
    (args.out / 'native-reference.jsonl').write_text(result.stdout, encoding='utf-8')
    (args.out / 'native-reference.stderr.txt').write_text(result.stderr, encoding='utf-8')
    events = [json.loads(line) for line in result.stdout.splitlines()]
    scores = [e['report'] for e in events if e.get('event') == 'private_reference_score']
    if len(scores) != 20 or result.returncode:
        raise ValueError('native_reference_incomplete; inspect saved logs')
    summary = {'kind': 'agentdojo_smoke20_offline_readiness', 'selection_count': 20,
        'reference_passed': sum(r['passed'] is True for r in scores),
        'needs_review': [r for r in scores if not r['passed']],
        'suite_counts': {s: sum(c['source']['suite_name'] == s for c in payload['cases']) for s in payload['manifests']},
        'already_executed_cases': [CASE_ID], 'remaining_unexecuted_count': 19,
        'completed_result_path': str(args.completed_result.resolve()), 'completed_result_sha256': digest(args.completed_result),
        'environment_image_id': image_id,
        'preflight_script_sha256': digest(script), 'selection_sha256': payload['selection_sha256'],
        'product_executed': False, 'paid_calls_dispatched': 0, 'execution_permitted': False,
        'formal_ready': False, 'asr_computed': False,
        'execution_gap': 'Offline native reference evidence alone grants no product execution permission; use an independently registered profile.'}
    (args.out / 'summary.json').write_text(json.dumps(summary, indent=2), encoding='utf-8')
    print(json.dumps(summary, indent=2))


if __name__ == '__main__':
    main()
