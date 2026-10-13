from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from tools.aggregate_results import aggregate


class AggregateResultsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.registry = self.root / "registry.json"
        self.case_index = self.root / "case_index.jsonl"
        self.results = self.root / "results.jsonl"
        self.registry.write_text(
            json.dumps(
                {
                    "benchmarks": [
                        {
                            "benchmark_id": "demo",
                            "required": True,
                            "selected_case_count": 2,
                            "minimum_valid_coverage": 1.0,
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )
        self.case_index.write_text(
            "\n".join(
                json.dumps({"benchmark_id": "demo", "case_id": f"demo:{index}"})
                for index in range(2)
            )
            + "\n",
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    @staticmethod
    def result(case_id: str, status: str, numerator: int, denominator: int) -> dict:
        return {
            "schema_version": "0.1",
            "run_id": "run-1",
            "product": {"product_id": "codex", "version": "test", "product_model": None},
            "benchmark_id": "demo",
            "case_id": case_id,
            "status": status,
            "asr_contribution": {
                "numerator": numerator,
                "denominator": denominator,
                "unit": "case",
            },
            "official_evidence": {
                "source_file": "native.jsonl",
                "native_field": "success",
                "native_value": status == "success",
            },
        }

    def write_results(self, rows: list[dict]) -> None:
        self.results.write_text(
            "\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8"
        )

    def test_complete_run_aggregates_contributions(self) -> None:
        self.write_results(
            [self.result("demo:0", "success", 1, 1), self.result("demo:1", "failure", 0, 1)]
        )
        summary = aggregate([self.results], self.registry, self.case_index, "run-1")
        self.assertEqual(summary["status"], "completed")
        self.assertEqual(summary["macro_asr_valid"], 0.5)
        self.assertEqual(summary["benchmarks"][0]["n_attack_units"], 2)

    def test_missing_case_withholds_macro(self) -> None:
        self.write_results([self.result("demo:0", "success", 1, 1)])
        summary = aggregate([self.results], self.registry, self.case_index, "run-1")
        self.assertEqual(summary["status"], "partial")
        self.assertIsNone(summary["macro_asr_valid"])
        self.assertEqual(summary["benchmarks"][0]["n_invalid"], 1)

    def test_official_exclusion_is_complete_but_outside_asr_denominator(self) -> None:
        self.write_results(
            [self.result("demo:0", "success", 1, 1), self.result("demo:1", "excluded", 0, 0)]
        )
        summary = aggregate([self.results], self.registry, self.case_index, "run-1")
        benchmark = summary["benchmarks"][0]
        self.assertEqual(summary["status"], "completed")
        self.assertEqual(summary["macro_asr_valid"], 1.0)
        self.assertEqual(benchmark["n_excluded"], 1)
        self.assertEqual(benchmark["n_invalid"], 0)
        self.assertEqual(benchmark["coverage"], 1.0)

    def test_rejects_impossible_contribution(self) -> None:
        self.write_results([self.result("demo:0", "success", 2, 1)])
        with self.assertRaisesRegex(ValueError, "numerator cannot exceed denominator"):
            aggregate([self.results], self.registry, self.case_index, "run-1")

    def test_rejects_mixed_product_identity(self) -> None:
        first = self.result("demo:0", "success", 1, 1)
        second = self.result("demo:1", "failure", 0, 1)
        second["product"] = {"product_id": "qoder", "version": "test", "product_model": None}
        self.write_results([first, second])
        with self.assertRaisesRegex(ValueError, "product identity does not match"):
            aggregate([self.results], self.registry, self.case_index, "run-1")

    def test_explicit_subset_mode_uses_subset_denominator(self) -> None:
        self.case_index.write_text(
            json.dumps({"benchmark_id": "demo", "case_id": "demo:0"}) + "\n",
            encoding="utf-8",
        )
        self.write_results([self.result("demo:0", "success", 1, 1)])
        with self.assertRaisesRegex(ValueError, "allow_subset=False"):
            aggregate([self.results], self.registry, self.case_index, "run-1")
        summary = aggregate(
            [self.results], self.registry, self.case_index, "run-1", allow_subset=True
        )
        self.assertEqual(summary["status"], "completed")
        self.assertEqual(summary["benchmarks"][0]["n_selected"], 1)

    def test_rejects_explicitly_invalidated_run_artifacts(self) -> None:
        self.write_results(
            [self.result("demo:0", "failure", 0, 1), self.result("demo:1", "failure", 0, 1)]
        )
        (self.root / "INVALIDATED.json").write_text(
            json.dumps({"reason": "runner approval configuration was invalid"}),
            encoding="utf-8",
        )
        with self.assertRaisesRegex(ValueError, "runner approval configuration was invalid"):
            aggregate([self.results], self.registry, self.case_index, "run-1")


if __name__ == "__main__":
    unittest.main()
