from __future__ import annotations

import copy
import io
import json
from argparse import Namespace
from contextlib import redirect_stdout
from pathlib import Path
import tempfile
import sys
import unittest
from unittest.mock import patch

from tools import run_product, run_product_batch
from orchestrator.execution_guard import product_preflight, require_product_execution, product_identity, bind_product_run

ROOT = Path(__file__).resolve().parents[1]


def request():
    return json.loads((ROOT/"examples/requests/codex_agentdojo.json").read_text(encoding="utf-8"))


class ExecutionGuardTests(unittest.TestCase):
    def test_declarations_do_not_enable_host_execution(self):
        for mode in ("container", "vm", "disposable_workspace"):
            r = request()
            r["isolation"]["mode"] = mode
            with self.subTest(mode=mode), self.assertRaisesRegex(ValueError, "isolation_unverified"):
                require_product_execution(r, dry_run=False)
            self.assertFalse(product_preflight(r, dry_run=False)["execution_permitted"])

    def test_dry_run_is_plan_only(self):
        report = require_product_execution(request(), dry_run=True)
        self.assertEqual(report["status"], "plan_only")
        self.assertFalse(report["isolation_verified"])
        self.assertFalse(report["formal_ready"])

    def test_missing_or_self_attested_isolation_is_invalid(self):
        for value in (None, {}, {**request()["isolation"], "verified": True},
                      {**request()["isolation"], "fresh_product_state": False}):
            r = request()
            r["isolation"] = value
            with self.subTest(value=value), self.assertRaises(ValueError):
                product_preflight(r, dry_run=True)

    def test_single_entry_blocks_before_selection_or_product_calls(self):
        argv = ["run_product", "--request", "unused.json", "--case-id", "unused"]
        with patch.object(sys, "argv", argv), patch.object(run_product, "load_request", return_value=request()), \
                patch.object(run_product, "selected_cases") as selection, patch.object(run_product, "ProductRunner") as runner:
            with self.assertRaisesRegex(ValueError, "isolation_unverified"):
                run_product.main()
            selection.assert_not_called()
            runner.assert_not_called()

    def test_batch_blocks_before_judge_subprocess_and_resume_markers(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/"request.json"
            path.write_text(json.dumps(request()), encoding="utf-8")
            with patch.object(sys, "argv", ["batch", "--request", str(path), "--resume"]), \
                    patch.object(run_product_batch, "selected_cases") as selection, \
                    patch.object(run_product_batch, "completed_case") as completed, \
                    patch.object(run_product_batch.subprocess, "run") as execute:
                with self.assertRaisesRegex(ValueError, "isolation_unverified"):
                    run_product_batch.main()
                selection.assert_not_called()
                completed.assert_not_called()
                execute.assert_not_called()

    def test_readonly_preflight_reports_blocking_without_runtime_assets(self):
        with patch.object(sys, "argv", ["single", "--request", "unused", "--case-id", "unused", "--preflight-only"]), \
                patch.object(run_product, "load_request", return_value=request()), redirect_stdout(io.StringIO()) as output:
            self.assertEqual(run_product.main(), 2)
            self.assertEqual(json.loads(output.getvalue())["status"], "blocked")

    def test_identity_changes_with_target_selection_isolation_and_runtime(self):
        with tempfile.TemporaryDirectory() as tmp:
            index = Path(tmp)/"index.jsonl"
            index.write_text('{"case_id":"x"}\n', encoding="utf-8")
            original = request()
            base = product_identity(original, index, Namespace())
            for key, value in [("product", {**original["product"], "version": "changed"}),
                               ("isolation", {**original["isolation"], "network_policy": "deny_all"}),
                               ("selection", {**original["selection"], "case_ids": ["changed"]})]:
                changed = copy.deepcopy(original)
                changed[key] = value
                self.assertNotEqual(base, product_identity(changed, index, Namespace()))
            self.assertNotEqual(base, product_identity(original, index, Namespace(state_source=Path(tmp)/"different")))
            index.write_text('{"case_id":"changed"}\n', encoding="utf-8")
            self.assertNotEqual(base, product_identity(original, index, Namespace()))

    def test_scheduling_does_not_change_identity_and_legacy_or_stale_resume_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            index = root/"index.jsonl"
            index.write_text('{}\n', encoding="utf-8")
            original = request()
            identity = product_identity(original, index, Namespace())
            other = {**original, "output_dir": "different", "run_id": "different"}
            self.assertEqual(identity, product_identity(other, index, Namespace(limit=1, offset=1)))
            out = root/"bound"
            report = product_preflight(original, dry_run=True)
            bind_product_run(out, identity, report)
            bind_product_run(out, identity, report)
            changed = copy.deepcopy(original)
            changed["product"]["version"] = "new"
            with self.assertRaisesRegex(ValueError, "stale_identity"):
                bind_product_run(out, product_identity(changed, index, Namespace()), report)
            legacy = root/"legacy"
            legacy.mkdir()
            (legacy/"old_results.json").write_text('{}', encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "legacy_identity_missing"):
                bind_product_run(legacy, identity, report)
