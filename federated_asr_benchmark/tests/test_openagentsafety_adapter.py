from __future__ import annotations

from pathlib import Path
import unittest
from tests.source_dependencies import requires_assets
from product_eval.data_paths import default_data_root, runtime_python

from product_eval.openagentsafety import OpenAgentSafetyAdapter


PACKAGE_ROOT = Path(__file__).resolve().parents[1]
DATA_ROOT = default_data_root()
OPENAGENTSAFETY_ROOT = (
    DATA_ROOT / "OpenAgentsafety-dataset" / "openagentsafety"
)


class OpenAgentSafetyAdapterTest(unittest.TestCase):
    def setUp(self) -> None:
        self.adapter = OpenAgentSafetyAdapter(OPENAGENTSAFETY_ROOT)

    @requires_assets(OPENAGENTSAFETY_ROOT)
    def test_preflight_excludes_incomplete_sources_and_evaluators(self) -> None:
        result = self.adapter.preflight()
        self.assertEqual(result["csv_row_count"], 356)
        self.assertEqual(result["named_candidate_count"], 355)
        self.assertEqual(result["source_complete_count"], 348)
        self.assertEqual(result["evaluator_contract_complete_count"], 338)
        self.assertEqual(len(result["incomplete_source_cases"]), 7)
        self.assertEqual(len(result["incomplete_evaluator_cases"]), 10)
        self.assertEqual(result["qualification_status"], "blocked_harness_runtime")
        self.assertFalse(result["formal_release_ready"])
        self.assertFalse(result["prerecorded_labels_allowed_for_scoring"])

    @requires_assets(OPENAGENTSAFETY_ROOT)
    def test_preflight_locks_services_scenarios_and_isolation_inputs(self) -> None:
        result = self.adapter.preflight()
        self.assertEqual(
            result["service_dependency_counts"],
            {"owncloud": 83, "gitlab": 47, "plane": 7},
        )
        self.assertEqual(result["workspace_case_count"], 173)
        self.assertEqual(result["scenario_case_count"], 189)
        self.assertEqual(result["scenario_model_counts"], {"gpt-4o": 224})
        self.assertEqual(len(result["unindexed_task_directories"]), 12)

    @requires_assets(OPENAGENTSAFETY_ROOT)
    def test_only_task_markdown_is_exposed_to_the_product(self) -> None:
        case = self.adapter.load_case("abusive-apology")
        prompt = self.adapter.build_product_prompt(case)
        self.assertEqual(prompt, case["prompt"])
        self.assertIn("utter buffoon", prompt)
        self.assertNotIn("Checkpoint 1", prompt)

    @requires_assets(OPENAGENTSAFETY_ROOT)
    def test_incomplete_indexed_case_fails_closed(self) -> None:
        with self.assertRaisesRegex(FileNotFoundError, "incomplete source artifacts"):
            self.adapter.load_case("clear-space-cachefiles")


if __name__ == "__main__":
    unittest.main()
