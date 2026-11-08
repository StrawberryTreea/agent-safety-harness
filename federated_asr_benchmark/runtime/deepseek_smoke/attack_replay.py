"""Offline source-trace replay in a trusted environment; never runs a product/model."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import socket
import time
import os


def execution_source(original):
    """Only public user input and environment injection; no prior assistant answers/oracles."""
    user = next(m for m in original['messages'] if m['role'] == 'user')
    return {key: original[key] for key in
            ('suite_name', 'user_task_id', 'injection_task_id', 'attack_type', 'injections')} | {
                'messages': [{'role': 'user', 'content': user['content']}]}


def serve_attack():
    import agentdojo_mcp_server as native
    source = execution_source(json.load(sys.stdin))
    Path('/work/source.json').write_text(json.dumps(source))
    suite = source['suite_name']
    if suite not in {'banking', 'slack', 'travel', 'workspace'}:
        raise ValueError('unsupported_native_suite')
    manifest = Path('/opt/manifests') / (suite + '.json')
    if not manifest.exists() and suite == 'banking':
        manifest = Path('/opt/banking.json')
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as listener:
        listener.bind('/environment/agentdojo.sock')
        os.chmod('/environment/agentdojo.sock', 0o666)
        listener.listen(1)
        connection, _ = listener.accept()
        with connection, connection.makefile('r') as reader, connection.makefile('w') as writer:
            native.run_server(source_result_path=Path('/work/source.json'),
                tool_manifest_path=manifest, result_path=Path('/work/native.json'),
                events_path=Path('/work/events.jsonl'), stdin=reader, stdout=writer)
    Path('/work/capture-complete').touch()
    while True:
        time.sleep(1)


def score_attack():
    for _ in range(100):
        if Path('/work/capture-complete').exists():
            break
        time.sleep(.1)
    else:
        raise ValueError('capture_not_finalized')
    metadata = json.load(sys.stdin)
    Path('/work/final.txt').write_text(metadata['final_text'])
    completed = subprocess.run([sys.executable, '/opt/tools/score_agentdojo_result.py',
        '--native-result', '/work/native.json', '--final-response', '/work/final.txt',
        '--run-id', metadata['run_id'], '--case-id', metadata['case_id'],
        '--product-json', json.dumps(metadata['product']), '--product-status', metadata['product_status'],
        '--product-timed-out', 'true' if metadata.get('product_timed_out') else 'false',
        '--output', '/work/case-result.json'],
        capture_output=True, text=True, timeout=60)
    if completed.returncode:
        raise RuntimeError('native_security_scorer_failed')
    print(json.dumps({'case_result': json.loads(Path('/work/case-result.json').read_text()),
        'native': json.loads(Path('/work/native.json').read_text()),
        'events': Path('/work/events.jsonl').read_text()}))


def main():
    import agentdojo_mcp_server as native
    from agentdojo.functions_runtime import FunctionCall
    original = json.load(sys.stdin)
    source = execution_source(original)
    public = {'kind': 'attack_public_capture', 'suite': source['suite_name'],
              'user_task_id': source['user_task_id'], 'messages': source['messages']}
    print(json.dumps({'event': 'public_capture', 'public_task': public,
        'sha256': hashlib.sha256(json.dumps(public, sort_keys=True).encode()).hexdigest()}), flush=True)
    # Native initialization and source replay happen after freezing the public task.
    suite, task, runtime, pre, post = native._load_runtime(source, 'v1')
    calls, errors, delivery = [], [], []
    for message in original['messages']:
        if message.get('role') != 'assistant':
            continue
        for row in message.get('tool_calls') or []:
            if len(calls) >= 30:
                raise ValueError('source_trace_exceeds_native_budget')
            call = FunctionCall(function=row['function'], args=row['args'])
            value, error = runtime.run_function(post, call.function, call.args)
            calls.append(call)
            if error:
                errors.append(type(error).__name__)
            delivery.extend(native._attack_delivery_evidence(source, native._format_tool_result(value),
                tool_name=call.function, call_index=len(calls)))
    snapshot = native._native_snapshot(source, 'v1', pre, post, calls,
                                       'replay_tool_error' if errors else None, delivery)
    Path('/work/native.json').write_text(json.dumps(snapshot))
    # A replay of a previous answer is never a new product score.
    final = next((m.get('content', '') for m in reversed(original['messages'])
                  if m.get('role') == 'assistant' and not m.get('tool_calls')), '')
    final = native._message_content_text(final)
    Path('/work/final.txt').write_text(final)
    case_id = 'agentdojo:' + ':'.join(source[k] for k in
              ('suite_name', 'user_task_id', 'injection_task_id', 'attack_type'))
    results = {}
    for label, status in [('replayed', 'completed'), ('invalid_control', 'failed')]:
        output = Path('/work/' + label + '.json')
        completed = subprocess.run([sys.executable, '/opt/tools/score_agentdojo_result.py',
            '--native-result', '/work/native.json', '--final-response', '/work/final.txt',
            '--run-id', 'offline-source-replay-only', '--case-id', case_id,
            '--product-json', json.dumps({'product_id': 'codex', 'version': '0.122.0',
                                         'product_model': 'deepseek-flash'}),
            '--product-status', status, '--product-timed-out', 'false', '--output', str(output)],
            capture_output=True, text=True, timeout=60)
        if completed.returncode:
            print(json.dumps({'event': 'private_scorer_error', 'stderr': completed.stderr[-8000:]}), flush=True)
            raise RuntimeError('native_scorer_failed')
        results[label] = json.loads(output.read_text())
    replay, invalid = results['replayed'], results['invalid_control']
    expected = original.get('security')
    report = {'kind': 'source_attack_trace_replay_only', 'product_executed': False,
        'paid_calls_dispatched': 0, 'asr_computed': False, 'formal_ready': False,
        'source_security': expected, 'replayed_security': replay['official_evidence']['native_value'],
        'native_tool_calls': len(calls), 'attack_delivery_count': len(delivery),
        'invalid_control': invalid['asr_contribution'], 'invalid_control_status': invalid['status'],
        'replayed_mapping': replay['asr_contribution'],
        'passed': (not errors and type(expected) is bool
            and replay['official_evidence']['native_value'] is expected and len(delivery) > 0
            and invalid['status'] == 'invalid' and invalid['asr_contribution']['denominator'] == 0)}
    print(json.dumps({'event': 'private_replay_score', 'report': report}), flush=True)
    return 0 if report['passed'] else 2


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('role', nargs='?', default='replay', choices=['replay', 'serve', 'score'])
    args = parser.parse_args()
    if args.role == 'serve':
        serve_attack()
    elif args.role == 'score':
        score_attack()
    else:
        raise SystemExit(main())
