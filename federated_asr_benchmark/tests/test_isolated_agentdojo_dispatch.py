import copy
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from tools import isolated_agentdojo_dispatch as dispatcher


class IsolatedDispatcherTests(unittest.TestCase):
    def test_historical_profiles_block_before_local_artifacts_or_docker(self):
        for profile_id in (dispatcher.PROFILE_ID, dispatcher.SMOKE_PROFILE_ID,
                           dispatcher.SMOKE_V2_PROFILE_ID):
            with self.subTest(profile_id=profile_id):
                with self.assertRaisesRegex(ValueError, 'historical_non_executable'):
                    dispatcher.load_profile(profile_id)
                request = self.request()
                request['benchmark_options']['isolated_profile_id'] = profile_id
                with patch.object(dispatcher, 'run_docker') as docker:
                    for dry_run in (False, True):
                        report = dispatcher.preflight(request, dry_run=dry_run)
                        self.assertFalse(report['execution_permitted'])
                        self.assertEqual(report['blockers'],
                                         ['isolated_profile_historical_non_executable'])
                    docker.assert_not_called()

    def test_failure_evidence_export_survives_unavailable_files(self):
        def docker(argv, **kwargs):
            if argv[-1] == '/work/native.json':
                return SimpleNamespace(returncode=1, stdout='')
            return SimpleNamespace(returncode=0, stdout='captured')
        with tempfile.TemporaryDirectory() as tmp, patch.object(dispatcher, 'run_docker', side_effect=docker):
            root = Path(tmp)
            inventory = dispatcher.export_failure_evidence(root, 'gateway', 'env', ['gateway', 'env'])
            self.assertTrue(inventory['gateway-audit.jsonl']['captured'])
            self.assertFalse(inventory['native-capture.json']['captured'])
            self.assertEqual((root/'native-events-capture.jsonl').read_text(), 'captured')
            self.assertTrue((root/'capture_inventory.json').exists())

    def request(self):
        return json.loads((dispatcher.PACKAGE / 'examples/requests/codex_deepseek_flash_agentdojo_isolated.json').read_text())

    def test_exact_case_and_model_and_adapter_required_before_docker(self):
        dispatcher.validate_request(self.request())
        for section, key, value in [('product', 'product_model', 'other-model'),
                ('product', 'state_source', 'personal-state'),
                ('selection', 'case_ids', ['another-case']), ('adapter', 'mcp_config', 'external.json'),
                ('isolation', 'mode', 'disposable_workspace')]:
            request = self.request()
            request[section][key] = value
            with self.subTest(section=section, key=key), patch.object(dispatcher, 'run_docker') as run:
                report = dispatcher.preflight(request)
                self.assertFalse(report['execution_permitted'])
                self.assertTrue(report['blockers'])
                run.assert_not_called()

    def info(self):
        return {'HostConfig': {'NetworkMode': 'none', 'ReadonlyRootfs': True,
            'CapDrop': ['ALL'], 'SecurityOpt': ['no-new-privileges'], 'Privileged': False},
            'Config': {'User': '10001:10001', 'Env': ['PATH=/bin']}, 'Mounts': []}

    def test_actual_mounts_and_secrets_and_network_checked_before_start(self):
        dispatcher.check_container(self.info(), network='none', allowed_volumes=set())
        variants = []
        info = self.info()
        info['Mounts'] = [{'Type': 'bind', 'Source': 'private-scoring-files', 'Destination': '/private'}]
        variants.append(info)
        info = self.info()
        info['Config']['Env'].append('DEEPSEEK_API_KEY=DO_NOT_EXPOSE')
        variants.append(info)
        info = self.info()
        info['HostConfig']['NetworkMode'] = 'host'
        variants.append(info)
        info = self.info()
        info['Mounts'] = [{'Type': 'volume', 'Name': 'private-state', 'Destination': '/work', 'RW': True}]
        variants.append(info)
        for variant in variants:
            with self.subTest(info=variant), self.assertRaises(ValueError):
                dispatcher.check_container(variant, network='none', allowed_volumes=set())

    def test_live_gateway_requires_explicit_bridge_and_names_failed_control(self):
        gateway = self.info()
        gateway['HostConfig']['NetworkMode'] = 'bridge'
        gateway['Config']['Env'].append('DEEPSEEK_API_KEY=synthetic-test-value')
        dispatcher.check_container(gateway, network='bridge', allowed_volumes=set())
        for unsafe in ('default', 'host', 'container:other', 'none'):
            gateway['HostConfig']['NetworkMode'] = unsafe
            with self.subTest(network=unsafe), self.assertRaisesRegex(ValueError, 'failed:network_mode'):
                dispatcher.check_container(gateway, network='bridge', allowed_volumes=set())

    def test_wrong_image_source_is_rejected_even_when_uid_matches(self):
        request = json.loads((dispatcher.PACKAGE / 'examples/requests/codex_deepseek_flash_agentdojo_smoke20_v3.json').read_text())
        def docker(argv, **kwargs):
            if argv[:2] == ['image', 'inspect']:
                return SimpleNamespace(stdout=json.dumps([{'Os': 'linux', 'Config': {'User': '10001:10001'}}]))
            return SimpleNamespace(stdout='wronghash  /opt/runtime.py\n')
        with patch.object(dispatcher, 'run_docker', side_effect=docker):
            report = dispatcher.preflight(request)
        self.assertFalse(report['execution_permitted'])
        self.assertEqual(report['blockers'], ['isolated_embedded_source_mismatch'])
