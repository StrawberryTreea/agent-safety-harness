from __future__ import annotations

import argparse
import importlib.util
from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

SPEC = importlib.util.spec_from_file_location(
    "audit_benchmark_coverage",
    ROOT / "tools" / "audit_benchmark_coverage.py",
)
assert SPEC and SPEC.loader
coverage = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(coverage)


def task_record(task_id: str, risk_type: str, *, benchmark: str = "demo") -> dict:
    return {
        "task_id": task_id,
        "benchmark": benchmark,
        "profile": "tool_use_task",
        "user_goal": "Perform the test task.",
        "attack_goal": None,
        "tools": [{"name": "demo.lookup", "risk": "medium"}],
        "initial_state": {},
        "labels": {
            "expected_behavior": "complete",
            "safety_category": "test",
            "risk_type": risk_type,
        },
        "evaluation": {},
        "metadata": {},
    }


def audit_args(*, max_cardinality: int, high_cardinality_samples: int = 3) -> argparse.Namespace:
    return argparse.Namespace(
        seed=20260805,
        min_per_benchmark=1,
        max_categorical_cardinality=max_cardinality,
        high_cardinality_samples=high_cardinality_samples,
    )


class BenchmarkCoverageTests(unittest.TestCase):
    def test_finite_risk_types_are_all_covered(self) -> None:
        records = [task_record(f"demo:{index}", risk) for index, risk in enumerate(("a", "b", "c"))]

        report, selected = coverage.audit_benchmark("demo", records, audit_args(max_cardinality=3))

        self.assertTrue(report["risk_type_full_coverage"])
        self.assertEqual({record["labels"]["risk_type"] for record in selected}, {"a", "b", "c"})

    def test_high_cardinality_sample_additions_use_distinct_risk_types(self) -> None:
        records = [task_record(f"demo:{index}", f"risk-{index}") for index in range(10)]

        report, selected = coverage.audit_benchmark(
            "demo",
            records,
            audit_args(max_cardinality=2, high_cardinality_samples=4),
        )

        selected_risks = [record["labels"]["risk_type"] for record in selected]
        self.assertFalse(report["risk_type_full_coverage"])
        self.assertEqual(len(selected_risks), len(set(selected_risks)))
        self.assertGreaterEqual(len(selected_risks), 4)

    def test_sampling_is_reproducible_for_fixed_seed(self) -> None:
        records = [task_record(f"demo:{index}", f"risk-{index % 5}") for index in range(20)]
        args = audit_args(max_cardinality=3, high_cardinality_samples=4)

        first_report, first = coverage.audit_benchmark("demo", records, args)
        second_report, second = coverage.audit_benchmark("demo", records, args)

        self.assertEqual(first_report["selected_task_ids"], second_report["selected_task_ids"])
        self.assertEqual(
            [record["task_id"] for record in first],
            [record["task_id"] for record in second],
        )

    def test_matrix_reports_rule_and_environment_blockers_together(self) -> None:
        record = task_record("agentharm:test", "harm", benchmark="agentharm")
        task = coverage.TaskSpec.from_record(record)
        report = {"environment_ready": False}

        row = coverage.sample_matrix_row(task, report)

        self.assertFalse(row["rule_ready"])
        self.assertFalse(row["environment_ready"])
        self.assertFalse(row["formal_ready"])
        self.assertIn("EvaluationRuleUnavailable", row["block_reason"])
        self.assertIn("EnvironmentAdapterUnavailable", row["block_reason"])


if __name__ == "__main__":
    unittest.main()
