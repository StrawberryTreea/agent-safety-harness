import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from tools import isolated_agentdojo_batch as batch
from tools import run_product_batch as entry

PACKAGE = batch.engine.PACKAGE

class IsolatedBatchTests(unittest.TestCase):
    def test_batch_exit_status_distinguishes_partial_invalid_and_offline_checks(self):
        complete = dict(selected_count=20, observed_count=20, valid_count=20,
                        invalid_count=0, offline=False, request_audit_complete=True)
        self.assertEqual(entry.isolated_batch_exit_code(complete, dry_run=False), 0)
        for changes in ({'observed_count': 1}, {'valid_count': 18, 'invalid_count': 2},
                        {'request_audit_complete': False}, {'selected_count': 0},
                        {'offline': True, 'offline_checks_passed': False}):
            with self.subTest(changes=changes):
                self.assertEqual(entry.isolated_batch_exit_code(
                    {**complete, **changes}, dry_run=False), 2)
        self.assertEqual(entry.isolated_batch_exit_code(
            {**complete, 'offline': True, 'valid_count': 0, 'invalid_count': 20,
             'offline_checks_passed': True}, dry_run=False), 0)
        self.assertEqual(entry.isolated_batch_exit_code({}, dry_run=True), 0)

    def test_resume_never_retries_invalid_and_detects_artifact_changes(self):
        request = json.loads((PACKAGE / 'examples/requests/codex_deepseek_flash_agentdojo_smoke20.json').read_text())
        cases = batch.engine.frozen_case_ids()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            selection = root / 'selection.jsonl'
            selection.write_text('frozen')
            profile = {'selection_sha256': batch.engine.digest(selection), 'cases': {}}
            for case_id in cases:
                suite, task, injection, attack = case_id.split(':', 1)[1].split(':', 3)
                source = root / 'AgentDojo/runs/claude-3-5-sonnet-20240620' / suite / task / attack / (injection + '.json')
                source.parent.mkdir(parents=True, exist_ok=True)
                source.write_text('{}')
                profile['cases'][case_id] = {'source_sha256': batch.engine.digest(source)}
            def dispatch(request, *, output, case_id, **kwargs):
                output.mkdir()
                report = {'case_id': case_id, 'passed': False}
                result = {'case_id': case_id, 'run_id': request['run_id'], 'status': 'invalid'}
                (output / 'summary.json').write_text(json.dumps(report))
                (output / 'case_result.json').write_text(json.dumps(result))
                return report
            kwargs = dict(data_root=root, selection_path=selection, output=root/'out', run_id='test', offline=True, keep_going=True)
            with patch.object(batch.engine, 'load_profile', return_value=profile), patch.object(batch.engine, 'preflight', return_value={'execution_permitted': True}), patch.object(batch.engine, 'dispatch', side_effect=dispatch) as call:
                report = batch.execute_batch(request, **kwargs)
                self.assertEqual(call.call_count, 20)
                self.assertEqual(report['invalid_count'], 20)
                self.assertFalse(report['asr_computed'])
                v2 = copy.deepcopy(request)
                v2['benchmark_options']['isolated_profile_id'] = batch.engine.SMOKE_V2_PROFILE_ID
                with self.assertRaisesRegex(ValueError, 'fresh_complete_population_no_import'):
                    batch.execute_batch(v2, imported_run=root/'old-run', dry_run=True, **kwargs)
                batch.execute_batch(request, resume=True, **kwargs)
                self.assertEqual(call.call_count, 20)
                with self.assertRaisesRegex(ValueError, 'explicit_matching_resume'):
                    batch.execute_batch(request, **kwargs)
                artifact = root/'out'/cases[0].replace(':', '__')/'case_result.json'
                artifact.write_text(artifact.read_text() + ' ')
                with self.assertRaisesRegex(ValueError, 'artifact_changed'):
                    batch.execute_batch(request, resume=True, **kwargs)
                self.assertEqual(call.call_count, 20)
                changed = copy.deepcopy(request)
                changed['selection']['case_ids'].reverse()
                with self.assertRaisesRegex(ValueError, 'complete_frozen_smoke20'):
                    batch.execute_batch(changed, dry_run=True, **kwargs)

    def test_import_rejects_empty_cleanup_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            (root/'summary.json').write_text(json.dumps({'passed': True, 'offline': False, 'cleanup': {}}))
            (root/'case_result.json').write_text(json.dumps({'case_id': 'case', 'status': 'failure', 'asr_contribution': {'denominator': 1}}))
            with self.assertRaisesRegex(ValueError, 'not_valid'):
                batch.import_result(root, {'cases': {'case': {}}})
