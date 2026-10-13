from __future__ import annotations

from pathlib import Path
import unittest
from tests.source_dependencies import requires_assets
from product_eval.data_paths import default_data_root

from product_eval.ras_eval import RASEvalAdapter


PACKAGE_ROOT = Path(__file__).resolve().parents[1]
RAS_ROOT = default_data_root() / "RAS-Eval-master" / "RAS-Eval-master"


class RASEvalAdapterTest(unittest.TestCase):
    def setUp(self) -> None:
        self.adapter = RASEvalAdapter(RAS_ROOT)

    @requires_assets(RAS_ROOT)
    def test_preflight_separates_native_attacks_and_asb_derived_rows(self) -> None:
        result = self.adapter.preflight()
        self.assertEqual(result["native_attack_count"], 3802)
        self.assertEqual(result["asb_derived_excluded_count"], 2680)
        self.assertEqual(result["task_count"], 80)
        self.assertEqual(result["target_task_count"], 80)
        self.assertEqual(result["tool_count"], 29)
        self.assertEqual(result["missing_tool_schemas"], [])
        self.assertEqual(result["qualification_status"], "blocked_paired_runtime")

    def test_official_workflow_score_and_paired_asr_are_fail_closed(self) -> None:
        task = {"workflow": ["first", "second"]}
        clean = self.adapter.workflow_score(task, ["first", "second"])
        attacked = self.adapter.workflow_score(task, ["first"])
        self.assertTrue(clean["completed"])
        self.assertFalse(attacked["completed"])
        self.assertEqual(
            self.adapter.paired_attack_outcome(clean, attacked),
            {"eligible": True, "attack_success": True, "exclusion_reason": None},
        )
        incapable = self.adapter.workflow_score(task, [])
        self.assertEqual(
            self.adapter.paired_attack_outcome(incapable, attacked),
            {
                "eligible": False,
                "attack_success": False,
                "exclusion_reason": "clean_control_not_completed",
            },
        )

    @requires_assets(RAS_ROOT)
    def test_native_case_preserves_attack_and_clean_task_identity(self) -> None:
        attack, task = self.adapter.load_attack("0")
        self.assertEqual(attack["target_index"], task["index"])
        self.assertEqual(attack["attack"][0]["mode"], "tool_input")
        self.assertIn(task["prompt"], self.adapter.build_product_prompt(task))


if __name__ == "__main__":
    unittest.main()
