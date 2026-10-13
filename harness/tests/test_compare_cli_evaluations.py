from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest


MODULE_PATH = Path(__file__).resolve().parents[1] / "tools" / "compare_cli_evaluations.py"
SPEC = importlib.util.spec_from_file_location("compare_cli_evaluations", MODULE_PATH)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def record(task_id: str, safety_pass: bool, benchmark: str = "ras_eval") -> dict:
    return {
        "task_id": task_id,
        "benchmark": benchmark,
        "safety_pass": safety_pass,
        "evidence": {"capture_evidence": {"task_record": {"profile": "tool_trace_attack"}}},
    }


def capture(task_id: str) -> dict:
    return {
        "task_id": task_id,
        "evidence": {
            "artifacts": {
                "returncode": 0,
                "benchmark_environment_validated": True,
                "benchmark_tools_available": True,
                "benchmark_mcp_events": [
                    {"event_type": "tool_call", "ok": True, "state_diff": {"changed": {}}}
                ],
            }
        },
    }


class CompareCliEvaluationsTests(unittest.TestCase):
    def test_compare_requires_aligned_valid_runs(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            first = MODULE.AgentRun(
                "codex",
                root,
                [record("a", True), record("b", False, "msb")],
                [capture("a"), capture("b")],
            )
            second = MODULE.AgentRun(
                "claude",
                root,
                [record("a", False), record("b", False, "msb")],
                [capture("a"), capture("b")],
            )

            report, task_ids = MODULE.compare([first, second], expected_count=2)

        self.assertEqual(task_ids, ["a", "b"])
        self.assertEqual(report["agents"]["codex"]["safety_pass_rate"], 0.5)
        self.assertEqual(report["agents"]["claude"]["safety_pass_rate"], 0.0)
        self.assertEqual(report["pairwise"]["codex__claude"]["disagreement_count"], 1)

    def test_compare_rejects_successful_call_without_state_diff(self) -> None:
        invalid_capture = capture("a")
        invalid_capture["evidence"]["artifacts"]["benchmark_mcp_events"][0].pop("state_diff")
        first = MODULE.AgentRun("codex", Path("."), [record("a", True)], [invalid_capture])
        second = MODULE.AgentRun("claude", Path("."), [record("a", True)], [capture("a")])

        with self.assertRaisesRegex(ValueError, "successful tool calls are missing state_diff"):
            MODULE.compare([first, second], expected_count=1)

    def test_rejected_call_does_not_require_state_diff(self) -> None:
        rejected_capture = capture("a")
        rejected_capture["evidence"]["artifacts"]["benchmark_mcp_events"].append(
            {
                "event_type": "tool_call",
                "ok": False,
                "error": "Benchmark maximum of 20 tool-call rounds exceeded.",
                "state_diff": None,
            }
        )
        health = MODULE.capture_health([rejected_capture])

        self.assertEqual(health["calls_missing_state_diff_count"], 0)
        self.assertEqual(health["tool_limit_hit_count"], 1)

    def test_explicit_null_attack_not_exposed_remains_unknown(self) -> None:
        score = {
            "task_id": "a",
            "task_type": "attack_test",
            "safety_pass": None,
            "safety_violation": False,
            "reason": "attack_not_exposed",
        }

        self.assertEqual(MODULE.outcome(score), "unknown")

    def test_missing_safety_pass_uses_legacy_attack_test_fallback(self) -> None:
        score = {
            "task_id": "a",
            "task_type": "attack_test",
            "safety_violation": False,
            "reason": "safe_no_attack_success",
        }

        self.assertEqual(MODULE.outcome(score), "pass")

    def test_regular_task_uses_completion_and_safety_fallback(self) -> None:
        score = {
            "task_id": "a",
            "task_type": "safe_task_with_risk",
            "task_success": True,
            "safety_violation": False,
        }

        self.assertEqual(MODULE.outcome(score), "pass")

    def test_compare_keeps_unknown_and_marks_run_invalid(self) -> None:
        first = MODULE.AgentRun(
            "codex",
            Path("."),
            [record("a", True), {"task_id": "b", "safety_pass": None}],
            [capture("a"), capture("b")],
        )
        second = MODULE.AgentRun(
            "claude",
            Path("."),
            [record("a", True), record("b", True)],
            [capture("a"), capture("b")],
        )

        report, _ = MODULE.compare([first, second], expected_count=2)

        codex = report["agents"]["codex"]
        self.assertFalse(codex["run_valid"])
        self.assertEqual(codex["unknown_count"], 1)
        self.assertIsNone(codex["safety_pass_rate"])
        self.assertEqual(codex["provisional_safety_pass_rate"], 1.0)

    def test_unresolved_score_remains_unknown(self) -> None:
        self.assertEqual(MODULE.outcome({"task_id": "a", "safety_pass": None}), "unknown")

    def test_proportional_quotas_are_exact(self) -> None:
        quotas = MODULE.proportional_quotas({"a": 80, "b": 15, "c": 5}, 10)
        self.assertEqual(sum(quotas.values()), 10)
        self.assertEqual(quotas, {"a": 7, "b": 2, "c": 1})

    def test_proportional_quotas_fall_back_when_target_is_too_small(self) -> None:
        quotas = MODULE.proportional_quotas({"a": 80, "b": 15, "c": 5}, 2)
        self.assertEqual(sum(quotas.values()), 2)
        self.assertEqual(quotas, {"a": 2, "b": 0, "c": 0})


if __name__ == "__main__":
    unittest.main()
