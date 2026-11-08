import hashlib
import json
from pathlib import Path
import tempfile
import unittest

import yaml
from tools.inspect_isolated_agentdojo_run import inspect_run


class InspectRunTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def write(self, name, value):
        (self.root / name).write_text(json.dumps(value), encoding='utf-8')

    def completed(self, status='invalid', calls=None):
        self.write('summary.json', {'passed': status != 'invalid', 'cleanup': {'container': True},
                                   'product': {'tool_calls': calls or [], 'returncode': 1, 'timed_out': False}})
        self.write('case_result.json', {'status': status, 'asr_contribution': {'numerator': 0, 'denominator': 0}})

    def test_started_is_not_claimed_running_or_completed(self):
        self.write('plan.json', {})
        self.write('execution_started.json', {})
        self.assertEqual(inspect_run(self.root)['state'], 'started_without_final_result')

    def test_invalid_preserved_and_missing_audit_unknown(self):
        self.completed()
        report = inspect_run(self.root)
        self.assertEqual(report['state'], 'completed_invalid')
        self.assertEqual(report['attack_outcome'], 'not_scored')
        self.assertIsNone(report['request_audit']['exact_attempts'])

    def test_native_failure_means_attack_failed(self):
        self.completed('failure')
        report = inspect_run(self.root)
        self.assertEqual(report['state'], 'completed_valid')
        self.assertEqual(report['attack_outcome'], 'attack_failed')

    def test_budget_denial_and_audit_hash_required(self):
        self.completed()
        audit = self.root / 'gateway-audit.jsonl'
        audit.write_text('\n'.join(json.dumps({'status': status}) for status in ['attempted', 'forwarded', 'budget_denied']))
        self.write('capture_inventory.json', {'gateway-audit.jsonl': {'captured': True,
                   'sha256': hashlib.sha256(audit.read_bytes()).hexdigest()}})
        report = inspect_run(self.root)
        self.assertEqual(report['request_audit']['exact_attempts'], 1)
        self.assertIn('gateway_request_budget_denied', report['observed_signals'])
        audit.write_text(audit.read_text() + '\n')
        self.assertIsNone(inspect_run(self.root)['request_audit']['exact_attempts'])

    def test_public_yaml_comparison_requires_native_witness(self):
        channel = 'External_\nlong name\n'
        text = yaml.safe_dump(['general', channel])
        calls = [{'tool': 'get_channels', 'arguments': {}, 'status': 'completed',
                  'result': {'content': [{'type': 'text', 'text': text}]}},
                 {'tool': 'send_channel_message', 'arguments': {'channel': 'External_'}, 'status': 'failed'}]
        self.completed(calls=calls)
        self.assertIsNone(inspect_run(self.root)['send_argument_comparisons'][0]['exact_public_channel_match'])
        event = {'event_type': 'tool_call', 'name': 'get_channels', 'args': {},
                 'output_sha256': hashlib.sha256(text.encode()).hexdigest()}
        self.write('native-events.jsonl', event)
        self.assertFalse(inspect_run(self.root)['send_argument_comparisons'][0]['exact_public_channel_match'])
        calls[1]['arguments']['channel'] = channel
        self.completed(calls=calls)
        self.assertTrue(inspect_run(self.root)['send_argument_comparisons'][0]['exact_public_channel_match'])

    def test_partial_json_does_not_report_completion(self):
        (self.root / 'summary.json').write_text('{')
        report = inspect_run(self.root)
        self.assertEqual(report['state'], 'started_without_final_result')
        self.assertTrue(report['warnings'])

    def test_inspection_does_not_mutate_evidence(self):
        self.completed()
        before = {p.name: p.read_bytes() for p in self.root.iterdir()}
        inspect_run(self.root)
        self.assertEqual(before, {p.name: p.read_bytes() for p in self.root.iterdir()})
