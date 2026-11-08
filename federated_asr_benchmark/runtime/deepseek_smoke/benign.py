"""Trusted AgentDojo service. Never installed in the product or gateway image."""
import argparse
import hashlib
import importlib.metadata
import json
from pathlib import Path
import socket
import time

SUITE, TASK = 'banking', 'user_task_13'


def public_task():
    from agentdojo.task_suite.load_suites import get_suite
    if importlib.metadata.version('agentdojo') != '0.1.35':
        raise ValueError('agentdojo_version_mismatch')
    suite = get_suite('v1', SUITE)
    tools = json.loads(Path('/opt/banking.json').read_text())['tools']
    if {t['name'] for t in tools} != {t.name for t in suite.tools}:
        raise ValueError('incomplete_native_tool_set')
    return {'benchmark_version': 'v1', 'suite': SUITE, 'task': TASK,
            'prompt': suite.get_user_task_by_id(TASK).PROMPT, 'tools': tools, 'injections': {}}


def source_record(public):
    return {'suite_name': SUITE, 'user_task_id': TASK, 'injection_task_id': 'injection_task_1',
            'attack_type': 'none_benign_control', 'injections': {},
            'messages': [{'role': 'user', 'content': public['prompt']}]}


def utility(native, final=''):
    if native.get('error') or native.get('pre_environment') is None or native.get('post_environment') is None:
        return None
    from agentdojo.functions_runtime import FunctionCall
    from agentdojo.task_suite.load_suites import get_suite
    from agentdojo.types import text_content_block_from_string
    suite = get_suite('v1', SUITE)
    return suite._check_user_task_utility(suite.get_user_task_by_id(TASK),
        [text_content_block_from_string(final)],
        suite.environment_type.model_validate(native['pre_environment']),
        suite.environment_type.model_validate(native['post_environment']),
        [FunctionCall.model_validate(c) for c in native['function_calls']])


def reference():
    import agentdojo_mcp_server as native
    public = public_task()
    # Emit/freeze public population before touching private reference or utility.
    print(json.dumps({'event': 'public_capture', 'public_task': public,
        'public_task_sha256': hashlib.sha256(json.dumps(public, sort_keys=True).encode()).hexdigest()}), flush=True)
    suite, task, runtime, pre, post = native._load_runtime(source_record(public), 'v1')
    calls, errors = [], []
    for call in task.ground_truth(pre):
        _, error = runtime.run_function(post, call.function, call.args)
        calls.append(call)
        if error:
            errors.append(type(error).__name__)
    snapshot = native._native_snapshot(source_record(public), 'v1', pre, post, calls,
                                       'reference_tool_error' if errors else None)
    negative = native._native_snapshot(source_record(public), 'v1', pre, pre, [], None)
    solved, unsolved = utility(snapshot), utility(negative)
    report = {'event': 'private_reference_score', 'reference_utility': solved,
              'no_action_utility': unsolved, 'reference_tool_calls': len(calls),
              'passed': solved is True and unsolved is False and not errors,
              'formal_ready': False, 'product_executed': False}
    print(json.dumps(report), flush=True)
    return 0 if report['passed'] else 2


def serve():
    import agentdojo_mcp_server as native
    source_path = Path('/work/source.json')
    source_path.write_text(json.dumps(source_record(public_task())))
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as listener:
        listener.bind('/environment/agentdojo.sock')
        import os
        os.chmod('/environment/agentdojo.sock', 0o666)
        listener.listen(1)
        connection, _ = listener.accept()
        with connection, connection.makefile('r') as reader, connection.makefile('w') as writer:
            native.run_server(source_result_path=source_path, tool_manifest_path=Path('/opt/banking.json'),
                result_path=Path('/work/native.json'), events_path=Path('/work/events.jsonl'),
                stdin=reader, stdout=writer)
    # Keep private tmpfs state alive until the trusted controller scores after product exit.
    Path('/work/capture-complete').touch()
    while True:
        time.sleep(1)


def score():
    for _ in range(100):
        if Path('/work/capture-complete').exists():
            break
        time.sleep(0.1)
    else:
        raise ValueError('capture_not_complete')
    native = json.loads(Path('/work/native.json').read_text())
    final = json.load(__import__('sys').stdin)['final_text']
    print(json.dumps({'native_utility': utility(native, final),
        'native_tool_calls': len(native['function_calls']), 'native_error': native.get('error'),
        'native_result': native, 'formal_ready': False, 'asr_computed': False}))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('role', choices=['reference', 'serve', 'score'])
    args = parser.parse_args()
    if args.role == 'reference':
        raise SystemExit(reference())
    elif args.role == 'serve':
        serve()
    else:
        score()
