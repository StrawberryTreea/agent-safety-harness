"""Fixed smoke20 orchestration over the registered container dispatcher, with no automatic retries."""
import copy
import hashlib
import json
import os
from pathlib import Path

from . import isolated_agentdojo_dispatch as engine


def stable_hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def import_result(run_dir, profile):
    summary = json.loads((run_dir / 'summary.json').read_text(encoding='utf-8'))
    result = json.loads((run_dir / 'case_result.json').read_text(encoding='utf-8'))
    case_id = result['case_id']
    if (case_id not in profile['cases'] or result['status'] not in {'success', 'failure'}
            or result['asr_contribution']['denominator'] != 1 or not summary.get('passed')
            or summary.get('offline') is not False or not summary.get('cleanup')
            or not all(summary['cleanup'].values())):
        raise ValueError('imported_case_not_valid')
    if result['product'] != {'product_id': 'codex', 'version': '0.122.0', 'product_model': 'deepseek-flash'}:
        raise ValueError('imported_product_identity_changed')
    if (summary.get('source_sha256') != profile['cases'][case_id]['source_sha256']
            or summary['runtime_pins']['product_image_id'] != profile['runtime_pins']['product_image_id']):
        raise ValueError('imported_source_or_product_image_changed')
    # The old environment image is retained in lineage. Require the same native core/scorer/tools.
    for path in ('tools/agentdojo_mcp_server.py', 'tools/score_agentdojo_result.py',
                 'product_eval/agentdojo.py', 'manifests/agentdojo_v1/banking.json'):
        if summary['runtime_pins']['files'].get(path) != profile['runtime_pins']['files'].get(path):
            raise ValueError('imported_native_contract_changed')
    native_path = run_dir / 'native-result.json'
    native = json.loads(native_path.read_text(encoding='utf-8'))
    if native.get('error') or not native.get('attack_delivery'):
        raise ValueError('imported_native_capture_invalid')
    return result, {'original_run_id': result['run_id'], 'original_run_dir': str(run_dir.resolve()),
        'original_result_sha256': engine.digest(run_dir / 'case_result.json'),
        'original_summary_sha256': engine.digest(run_dir / 'summary.json'),
        'original_native_sha256': engine.digest(native_path),
        'original_environment_image_id': summary['runtime_pins']['environment_image_id']}


def execute_batch(request, *, data_root, selection_path, output, run_id, resume=False,
                  imported_run=None, offline=False, keep_going=False, dry_run=False):
    from jsonschema import validate
    validate(request, json.loads((engine.PACKAGE / 'schemas/run_request.schema.json').read_text()))
    engine.validate_request(request)
    profile_id = request['benchmark_options']['isolated_profile_id']
    if profile_id not in engine.SMOKE_PROFILES:
        raise ValueError('batch_requires_smoke20_profile')
    profile = engine.load_profile(profile_id)
    cases = engine.frozen_case_ids()
    if request['selection']['case_ids'] != cases or engine.digest(selection_path) != profile['selection_sha256']:
        raise ValueError('batch_requires_complete_frozen_smoke20')
    for case_id, record in profile['cases'].items():
        suite, task, injection, attack = case_id.split(':', 1)[1].split(':', 3)
        source = data_root / 'AgentDojo/runs/claude-3-5-sonnet-20240620' / suite / task / attack / (injection + '.json')
        if engine.digest(source) != record['source_sha256']:
            raise ValueError('batch_source_population_changed')
    imported, lineage = ({}, {})
    if imported_run and profile_id in {engine.SMOKE_V2_PROFILE_ID, engine.SMOKE_V3_PROFILE_ID}:
        raise ValueError('new_budget_requires_fresh_complete_population_no_import')
    if imported_run:
        result, origin = import_result(imported_run, profile)
        imported[result['case_id']] = result
        lineage[result['case_id']] = origin
    plan = {'kind': 'isolated_agentdojo_smoke20_plan', 'selected_count': 20,
        'imported_count': len(imported), 'maximum_new_cases': 20 - len(imported),
        'maximum_new_api_requests': profile.get('budget', {}).get('maximum_requests', 4) * (20 - len(imported)), 'offline': offline,
        'automatic_retry': False, 'formal_ready': False, 'paid_calls_dispatched': 0}
    if dry_run:
        return plan
    if not offline and not os.environ.get('DEEPSEEK_API_KEY', '').strip():
        raise ValueError('DEEPSEEK_API_KEY_missing_in_this_process')
    preflight = engine.preflight(request)
    if not preflight['execution_permitted']:
        raise ValueError('batch_preflight_failed:' + ','.join(preflight['blockers']))
    effective_request = copy.deepcopy(request)
    effective_request['run_id'] = run_id
    identity = stable_hash({'request': effective_request, 'offline': offline, 'run_id': run_id,
        'profile_sha256': engine.digest(engine.PACKAGE / 'release/isolated_product_profiles.json'),
        'lineage': lineage})
    manifest_path = output / 'batch_manifest.json'
    if output.exists():
        if not resume or not manifest_path.is_file():
            raise ValueError('nonempty_batch_output_requires_explicit_matching_resume')
        manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
        if manifest.get('identity') != identity:
            raise ValueError('batch_resume_identity_changed')
    else:
        output.mkdir(parents=True)
        manifest = {'identity': identity, 'plan': plan, 'lineage': lineage, 'attempted_cases': [],
                    'completed_artifacts': {}}
        manifest_path.write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    rows, checks, imported_count = [], [], 0
    for case_id in cases:
        case_dir = output / case_id.replace(':', '__')
        if case_id in imported:
            result = copy.deepcopy(imported[case_id])
            result['run_id'] = run_id  # Derived aggregation row, original evidence stays immutable.
            rows.append(result)
            imported_count += 1
            continue
        summary_path = case_dir / 'summary.json'
        if summary_path.exists():
            expected = manifest['completed_artifacts'].get(case_id)
            actual = {name: engine.digest(case_dir / name)
                      for name in ('summary.json', 'case_result.json')}
            if expected != actual:
                raise ValueError('resumed_case_artifact_changed_or_uncommitted')
            report = json.loads(summary_path.read_text(encoding='utf-8'))
            result = json.loads((case_dir / 'case_result.json').read_text(encoding='utf-8'))
            if report.get('case_id') != case_id or result.get('run_id') != run_id:
                raise ValueError('resumed_case_identity_mismatch')
            # Invalid is a terminal observation too; never spend calls retrying it implicitly.
        else:
            if case_id in manifest['attempted_cases'] or case_dir.exists():
                raise ValueError('interrupted_case_requires_review_not_automatic_retry')
            manifest['attempted_cases'].append(case_id)
            manifest_path.write_text(json.dumps(manifest, indent=2), encoding='utf-8')
            report = engine.dispatch(effective_request, data_root=data_root, selection_path=selection_path,
                output=case_dir, offline=offline, case_id=case_id)
            result = json.loads((case_dir / 'case_result.json').read_text(encoding='utf-8'))
            manifest['completed_artifacts'][case_id] = {
                name: engine.digest(case_dir / name) for name in ('summary.json', 'case_result.json')}
            manifest_path.write_text(json.dumps(manifest, indent=2), encoding='utf-8')
        rows.append(result)
        checks.append(report)
        if not report['passed'] and not keep_going:
            break
    if not offline:
        result_path = output / 'case_result.jsonl'
        result_path.write_text('\n'.join(json.dumps(row) for row in rows) + '\n', encoding='utf-8')
        from tools.aggregate_results import aggregate
        aggregate_result = aggregate([result_path], engine.PACKAGE / 'benchmark_registry.json', selection_path,
                                     run_id, allow_subset=True)
        (output / 'native_aggregate.json').write_text(json.dumps(aggregate_result, indent=2), encoding='utf-8')
    audits = []
    audit_complete = True
    for case_id in manifest['attempted_cases']:
        audit_path = output / case_id.replace(':', '__') / 'gateway-audit.jsonl'
        if audit_path.exists():
            audits.extend(json.loads(line) for line in audit_path.read_text().splitlines())
        else:
            audit_complete = False
    observed_requests = sum(row.get('status') == 'attempted' for row in audits)
    summary = {**plan, 'kind': 'isolated_agentdojo_smoke20_execution', 'run_id': run_id,
        'observed_count': len(rows), 'imported_count': imported_count,
        'valid_count': sum(row['status'] in {'success', 'failure'} for row in rows),
        'invalid_count': sum(row['status'] == 'invalid' for row in rows),
        'new_cases_attempted': len(manifest['attempted_cases']),
        'api_request_attempts_observed': observed_requests, 'request_audit_complete': audit_complete,
        'paid_calls_dispatched': 0 if offline else (observed_requests if audit_complete else None),
        'offline_checks_passed': bool(checks) and all(r['passed'] for r in checks) if offline else None,
        'asr_computed': not offline, 'formal_ready': False,
        'note': 'Imported row has derived batch run_id; original run/image identity retained in batch_manifest lineage.'}
    (output / 'summary.json').write_text(json.dumps(summary, indent=2), encoding='utf-8')
    return summary
