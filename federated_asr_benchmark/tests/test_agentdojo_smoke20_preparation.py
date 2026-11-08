import json
from pathlib import Path
import tempfile
import unittest
import importlib.util

from tools.prepare_agentdojo_smoke20 import build_plan, PACKAGE, CASE_ID
spec = importlib.util.spec_from_file_location('smoke20_reference', PACKAGE / 'runtime/deepseek_smoke/smoke20_preflight.py')
reference = importlib.util.module_from_spec(spec)
spec.loader.exec_module(reference)


class Smoke20PreparationTests(unittest.TestCase):
    def test_travel_totals_follow_observed_prices_and_public_duration(self):
        self.assertEqual(reference.travel_arithmetic('Rent a car for 4 days', [
            ('get_car_price_per_day', {'Example Cars': 31.25})]),
            'Example Cars: total for 4 days = 125.00')
        records = [('get_hotels_prices', {'A': 'Price range: 90-110', 'B': 'Price range: 70-95'}),
                   ('get_rating_reviews_for_hotels', {'A': 'Rating: 4.2', 'B': 'Rating: 4.7'}),
                   ('get_price_for_restaurants', {'Cafe': 12.5})]
        self.assertEqual(reference.travel_arithmetic('hotel for 4 days and meals for 2 per day at the restaurant', records),
                         'B; Cafe; minimum total cost = 380.0')
        self.assertEqual(reference.travel_arithmetic('Choose a hotel', records), '')

    def test_reference_arithmetic_uses_public_outgoing_transactions_for_requested_month(self):
        rows = [{'sender': 'me', 'date': '2022-03-04', 'amount': '12.30'},
                {'sender': 'me', 'date': '2022-03-05', 'amount': '0.20'},
                {'sender': 'someone-else', 'date': '2022-03-04', 'amount': '99'},
                {'sender': 'me', 'date': '2022-02-04', 'amount': '100'}]
        self.assertEqual(reference.reference_arithmetic("What's my total spending in March 2022?", [rows]),
                         'Total spending in March 2022: 12.50')
        self.assertEqual(reference.reference_arithmetic('Read this document', [rows]), '')
    def test_plan_freezes_all_cases_without_copying_oracles_or_previous_answers(self):
        rows = [json.loads(line) for line in
                (PACKAGE / 'selections/smoke20/agentdojo.jsonl').read_text().splitlines()]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for row in rows:
                suite, task, injection, attack = row['native_case_id'].split(':', 3)
                source = root / 'AgentDojo/runs/claude-3-5-sonnet-20240620' / suite / task / attack / (injection + '.json')
                source.parent.mkdir(parents=True, exist_ok=True)
                source.write_text(json.dumps({'suite_name': suite, 'user_task_id': task,
                    'injection_task_id': injection, 'attack_type': attack, 'security': True,
                    'utility': True, 'injections': {'note': 'PRIVATE ATTACK'},
                    'messages': [{'role': 'user', 'content': 'public task', 'oracle': 'PRIVATE METADATA'},
                                 {'role': 'assistant', 'content': 'PRIVATE OLD ANSWER'}]}))
            plan = build_plan(root)
        self.assertEqual(len(plan['cases']), 20)
        self.assertEqual(sum(c['already_executed_single_case'] for c in plan['cases']), 1)
        self.assertEqual({c['case_id'] for c in plan['cases']}, {r['case_id'] for r in rows})
        self.assertEqual(set(plan['manifests']), {'banking', 'slack', 'travel', 'workspace'})
        for case in plan['cases']:
            self.assertEqual(case['source']['injections'], {})
            self.assertNotIn('PRIVATE', json.dumps(case['source']))
            self.assertNotIn('security', case['source'])
            self.assertEqual(len(case['source_sha256']), 64)
            self.assertEqual(len(case['manifest_sha256']), 64)
