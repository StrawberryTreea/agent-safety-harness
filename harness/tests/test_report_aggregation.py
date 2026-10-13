from __future__ import annotations

import unittest
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from agent_safety_harness.reporting import summarize


class ReportAggregationTests(unittest.TestCase):
    def test_explicit_null_safety_pass_is_not_backfilled(self) -> None:
        summary = summarize(
            [
                {
                    "task_type": "attack_test",
                    "safety_pass": None,
                    "safety_violation": False,
                    "reason": "attack_not_exposed",
                }
            ]
        )

        self.assertEqual(summary["unknown_total"], 1)
        self.assertFalse(summary["run_valid"])
        self.assertIsNone(summary["safety_pass_rate"])

    def test_missing_safety_pass_allows_legacy_attack_test_fallback(self) -> None:
        summary = summarize(
            [{"task_type": "attack_test", "safety_violation": False}]
        )

        self.assertEqual(summary["pass_total"], 1)
        self.assertTrue(summary["run_valid"])
        self.assertEqual(summary["safety_pass_rate"], 1.0)

    def test_safety_pass_requires_known_success_and_safety(self) -> None:
        summary = summarize(
            [
                {
                    "score": 1.0,
                    "safety_violation": False,
                    "attack_success": None,
                    "unsafe_tool_call": None,
                    "data_leak": None,
                    "refusal": None,
                    "task_success": None,
                },
                {
                    "score": 0.0,
                    "policy_verdict": "fail",
                    "safety_violation": True,
                    "attack_success": True,
                    "unsafe_tool_call": True,
                    "data_leak": False,
                    "refusal": False,
                    "task_success": False,
                },
            ]
        )
        self.assertEqual(summary["total"], 2)
        self.assertEqual(summary["evaluated_total"], 1)
        self.assertEqual(summary["unknown_total"], 1)
        self.assertEqual(summary["pass_total"], 0)
        self.assertEqual(summary["fail_total"], 1)
        self.assertFalse(summary["run_valid"])
        self.assertEqual(summary["valid_rate"], 0.5)
        self.assertIsNone(summary["safety_pass_rate"])
        self.assertEqual(summary["provisional_safety_pass_rate"], 0.0)
        self.assertEqual(summary["unknown_rate"], 0.5)

    def test_unknown_results_are_reported_separately_from_known_score(self) -> None:
        summary = summarize(
            [
                {
                    "score": 1.0,
                    "policy_verdict": "pass",
                    "safety_violation": False,
                    "task_success": True,
                },
                {
                    "score": 0.0,
                    "policy_verdict": "unknown",
                    "safety_violation": None,
                },
            ]
        )
        self.assertEqual(summary["total"], 2)
        self.assertEqual(summary["evaluated_total"], 1)
        self.assertEqual(summary["unknown_total"], 1)
        self.assertFalse(summary["run_valid"])
        self.assertIsNone(summary["safety_pass_rate"])
        self.assertEqual(summary["provisional_safety_pass_rate"], 1.0)
        self.assertEqual(summary["unknown_rate"], 0.5)

    def test_incomplete_known_result_is_a_failure(self) -> None:
        summary = summarize(
            [
                {
                    "score": 1.0,
                    "policy_verdict": "pass",
                    "safety_violation": False,
                    "task_success": True,
                },
                {
                    "score": 0.0,
                    "policy_verdict": "fail",
                    "safety_violation": False,
                    "task_success": False,
                },
            ]
        )
        self.assertEqual(summary["evaluated_total"], 2)
        self.assertEqual(summary["unknown_total"], 0)
        self.assertTrue(summary["run_valid"])
        self.assertEqual(summary["valid_rate"], 1.0)
        self.assertEqual(summary["pass_total"], 1)
        self.assertEqual(summary["fail_total"], 1)
        self.assertEqual(summary["safety_pass_rate"], 0.5)
        self.assertEqual(summary["unknown_rate"], 0.0)

    def test_token_usage_is_aggregated(self) -> None:
        summary = summarize(
            [
                {
                    "score": 1.0,
                    "policy_verdict": "pass",
                    "cost": {
                        "usage_source": "provider_usage",
                        "component": "agent",
                        "model": "model-a",
                        "prompt_tokens": 10,
                        "completion_tokens": 5,
                        "total_tokens": 15,
                    },
                },
                {
                    "score": 1.0,
                    "policy_verdict": "pass",
                    "cost": {
                        "usage_source": "estimated_usage",
                        "component": "agent",
                        "model": "cli-a",
                        "prompt_tokens": 20,
                        "completion_tokens": 8,
                    },
                },
            ]
        )
        usage = summary["usage_summary"]
        self.assertEqual(usage["usage_records"], 2)
        self.assertEqual(usage["prompt_tokens"], 30)
        self.assertEqual(usage["completion_tokens"], 13)
        self.assertEqual(usage["total_tokens"], 43)
        self.assertEqual(usage["by_source"]["provider_usage"]["total_tokens"], 15)
        self.assertEqual(usage["by_source"]["estimated_usage"]["total_tokens"], 28)


if __name__ == "__main__":
    unittest.main()
