from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest


MODULE_PATH = Path(__file__).resolve().parents[1] / "tools" / "audit_capture_health.py"
SPEC = importlib.util.spec_from_file_location("audit_capture_health", MODULE_PATH)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def capture(error_type: str) -> dict:
    return {
        "task_id": f"task-{error_type}",
        "benchmark": "msb",
        "evidence": {
            "error_type": error_type,
            "artifacts": {
                "returncode": 0,
                "benchmark_environment_validated": True,
                "benchmark_tools_available": True,
                "benchmark_mcp_events": [],
            },
        },
    }


class AuditCaptureHealthTests(unittest.TestCase):
    def test_private_scoring_deferral_is_not_reported_as_runtime_error(self) -> None:
        report = MODULE.audit(
            [capture("EvaluationRuleUnavailable"), capture("ProtocolError")],
            Path("capture.jsonl"),
        )

        self.assertEqual(
            report["capture_stage_status_counts"],
            {"EvaluationRuleUnavailable": 1, "ProtocolError": 1},
        )
        self.assertEqual(report["runtime_error_type_counts"], {"ProtocolError": 1})
        self.assertNotIn("error_type_counts", report)

    def test_read_jsonl_ignores_only_an_unfinished_trailing_record(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "capture.jsonl"
            path.write_text('{"task_id":"complete"}\n{"task_id":', encoding="utf-8")

            records = MODULE.read_jsonl(path)

            self.assertEqual(records, [{"task_id": "complete"}])

    def test_read_jsonl_rejects_malformed_complete_record(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "capture.jsonl"
            path.write_text('{"task_id":"complete"}\n{"task_id":}\n', encoding="utf-8")

            with self.assertRaises(Exception):
                MODULE.read_jsonl(path)


if __name__ == "__main__":
    unittest.main()
