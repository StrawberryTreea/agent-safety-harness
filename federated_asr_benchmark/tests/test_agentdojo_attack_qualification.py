import importlib.util
import json
from pathlib import Path
import unittest
import sys

from tools.qualify_agentdojo_attack import PACKAGE, PROFILE_ID, build_sources
sys.path.insert(0, str(PACKAGE.parent))

spec = importlib.util.spec_from_file_location('attack_replay_test',
    PACKAGE / 'runtime/deepseek_smoke/attack_replay.py')
replay = importlib.util.module_from_spec(spec)
spec.loader.exec_module(replay)


class AttackQualificationTests(unittest.TestCase):
    def test_execution_input_keeps_injection_but_excludes_prior_answers_and_oracles(self):
        original = {'suite_name': 'banking', 'user_task_id': 'user_task_13',
            'injection_task_id': 'injection_task_1', 'attack_type': 'important_instructions',
            'injections': {'note': 'untrusted attack payload'}, 'security': True,
            'ground_truth': 'PRIVATE ORACLE', 'utility': True,
            'messages': [{'role': 'system', 'content': 'PRIVATE SYSTEM'},
                {'role': 'user', 'content': 'public request'},
                {'role': 'assistant', 'content': 'PRIVATE PREVIOUS ANSWER'}]}
        public_source = replay.execution_source(original)
        self.assertEqual(public_source['injections'], original['injections'])
        self.assertEqual(public_source['messages'], [{'role': 'user', 'content': 'public request'}])
        self.assertNotIn('PRIVATE', json.dumps(public_source))
        self.assertNotIn('security', public_source)
        self.assertNotIn('utility', public_source)

    def test_build_allowlist_uses_existing_explicit_code_files_only(self):
        sources = build_sources()
        self.assertIn('score_agentdojo_result.py', sources)
        self.assertIn('product_eval/agentdojo.py', sources)
        for name, path in sources.items():
            self.assertTrue(path.is_file(), name)
            self.assertTrue(path.resolve().is_relative_to(PACKAGE.resolve()))
            self.assertIn(path.suffix, {'.py', '.Dockerfile', '.json'})

    def test_profile_registration_cannot_grant_execution_to_incomplete_request(self):
        from orchestrator.execution_guard import product_preflight
        profile = json.loads((PACKAGE / 'release/isolated_product_profiles.json').read_text())['profiles'][PROFILE_ID]
        self.assertFalse(profile['formal_ready'])
        report = product_preflight({'isolation': {'mode': 'container', 'fresh_product_state': True,
            'network_policy': 'deny_except_benchmark', 'tool_permission_mode': 'autonomous_preapproved_in_sandbox'},
            'benchmark_options': {'profile_id': PROFILE_ID}}, dry_run=False)
        self.assertFalse(report['execution_permitted'])
