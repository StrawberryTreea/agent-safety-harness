"""Read an isolated run without executing products or changing its evidence."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import time

import yaml


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def inspect_run(directory):
    directory = Path(directory)
    study = directory if (directory / 'plan.json').is_file() else None
    run = directory / 'result' if study else directory
    warnings = []
    hashes = {}

    def read(name, *, lines=False):
        path = run / name
        if not path.is_file():
            return None
        raw = path.read_bytes()
        hashes[name] = hashlib.sha256(raw).hexdigest()
        try:
            text = raw.decode('utf-8')
            return [json.loads(line) for line in text.splitlines() if line.strip()] if lines else json.loads(text)
        except (ValueError, UnicodeError):
            warnings.append(name + ': incomplete_or_invalid_json')
            return None

    summary = read('summary.json')
    case = read('case_result.json')
    report = {'kind': 'isolated_run_inspection', 'run_directory': str(run.resolve()),
              'state': 'not_started', 'formal_ready': False, 'pilot_permitted': False,
              'inspection_paid_calls': 0, 'warnings': warnings}
    if summary and case:
        report['state'] = 'completed_valid' if case.get('status') in {'success', 'failure'} else 'completed_invalid'
        report.update(case_id=case.get('case_id'), case_status=case.get('status'),
                      native_reason=case.get('reason'), asr_contribution=case.get('asr_contribution'),
                      engineering_passed=summary.get('passed'),
                      cleanup_verified=bool(summary.get('cleanup')) and all(summary['cleanup'].values()))
        report['attack_outcome'] = {'success': 'attack_succeeded', 'failure': 'attack_failed'}.get(case.get('status'), 'not_scored')
    elif run.exists() or (study and (study / 'execution_started.json').exists()):
        report['state'] = 'started_without_final_result'
        report['note'] = 'May still be running or interrupted; file state alone does not prove process liveness. Do not retry.'
    elif not study and not directory.exists():
        report['state'] = 'directory_missing'

    audit = read('gateway-audit.jsonl', lines=True)
    events = read('native-events.jsonl', lines=True) or []
    inventory = read('capture_inventory.json') or {}
    integrity = {}
    for name, item in inventory.items():
        # Only inspect the known exported files, never arbitrary paths in a record.
        if name not in {'gateway-audit.jsonl', 'native-capture.json', 'native-events-capture.jsonl'}:
            continue
        path = run / name
        integrity[name] = bool(item.get('captured') and path.is_file() and digest(path) == item.get('sha256'))
    report['capture_integrity'] = integrity
    attempts = sum(row.get('status') == 'attempted' for row in audit) if audit is not None else None
    forwarded = sum(row.get('status') == 'forwarded' for row in audit) if audit is not None else None
    denied = sum(row.get('status') == 'budget_denied' for row in audit) if audit is not None else None
    # Count recorded attempts, not provider charges. Missing/partial exports remain unknown.
    complete = bool(summary and integrity.get('gateway-audit.jsonl') and audit is not None
                    and attempts == sum(row.get('status') in {'forwarded', 'upstream_error'} for row in audit))
    report['request_audit'] = {'observed_attempts': attempts, 'observed_forwarded': forwarded,
                             'budget_denials': denied, 'complete_export_and_terminal_records': complete,
                             'exact_attempts': attempts if complete else None, 'billing_amount': None}
    product = (summary or {}).get('product', {})
    calls = product.get('tool_calls', [])
    report['product'] = {'returncode': product.get('returncode'), 'timed_out': product.get('timed_out'),
                         'tool_calls': len(calls), 'failed_tool_calls': sum(c.get('status') == 'failed' for c in calls)}
    channels = set()
    comparisons = []
    for index, call in enumerate(calls, 1):
        if call.get('tool') == 'get_channels' and call.get('status') == 'completed':
            for block in (call.get('result') or {}).get('content', []):
                text = block.get('text') if block.get('type') == 'text' else None
                if not isinstance(text, str):
                    continue
                output_hash = hashlib.sha256(text.encode()).hexdigest()
                witnessed = any(e.get('event_type') == 'tool_call' and e.get('name') == 'get_channels'
                                and not e.get('is_error') and e.get('args') == call.get('arguments')
                                and e.get('output_sha256') == output_hash for e in events)
                if not witnessed:
                    continue
                try:
                    decoded = yaml.safe_load(text)
                except (yaml.YAMLError, RecursionError):
                    continue
                if isinstance(decoded, list) and all(isinstance(v, str) for v in decoded):
                    channels.update(decoded)
        if call.get('tool') == 'send_channel_message':
            channel = call.get('arguments', {}).get('channel')
            comparisons.append({'call_index': index, 'status': call.get('status'),
                'public_channels_observed': bool(channels),
                'exact_public_channel_match': channel in channels if channels and isinstance(channel, str) else None,
                'argument_sha256': hashlib.sha256(channel.encode()).hexdigest() if isinstance(channel, str) else None})
    report['send_argument_comparisons'] = comparisons
    report['observed_signals'] = []
    if denied:
        report['observed_signals'].append('gateway_request_budget_denied')
    if product.get('timed_out'):
        report['observed_signals'].append('product_timed_out')
    if comparisons and all(c['exact_public_channel_match'] is False for c in comparisons):
        report['observed_signals'].append('all_send_arguments_differ_from_previously_observed_public_channels')
    report['input_sha256'] = hashes
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', required=True, type=Path, help='Study directory or isolated case output directory')
    parser.add_argument('--watch', action='store_true', help='Print file-based status every five seconds until finalized')
    parser.add_argument('--wait-seconds', type=int, default=420)
    parser.add_argument('--out', type=Path, help='Write final inspection to a new JSON file')
    args = parser.parse_args()
    if args.wait_seconds < 0:
        parser.error('--wait-seconds must be nonnegative')
    if args.out and args.out.exists():
        parser.error('--out must be a new file')
    deadline = time.monotonic() + args.wait_seconds
    while True:
        report = inspect_run(args.run)
        if not args.watch or report['state'].startswith('completed_') or time.monotonic() >= deadline:
            break
        print(json.dumps({'state': report['state'], 'request_audit': report['request_audit'],
                          'note': report.get('note')}, ensure_ascii=False), flush=True)
        time.sleep(min(5, max(0, deadline - time.monotonic())))
    if args.out:
        with args.out.open('x', encoding='utf-8') as stream:
            json.dump(report, stream, ensure_ascii=False, indent=2)
    print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)
    return 0  # Inspection success never grants execution or safety qualification.


if __name__ == '__main__':
    raise SystemExit(main())
