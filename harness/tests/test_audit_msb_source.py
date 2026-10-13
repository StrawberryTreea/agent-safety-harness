from __future__ import annotations

from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools.audit_msb_source import audit, build_official_cases, normalize_msb_root
from tools.replay_msb_official_metrics import replay


MSB_ROOT = ROOT.parent / "数据集" / "评测数据" / "MSB-main"
CANDIDATE = ROOT / "runs" / "ash_security_v1" / "tasks" / "security.tasks.jsonl"


@unittest.skipUnless(MSB_ROOT.exists(), "optional MSB source dataset is not vendored")
class MsbSourceAuditTests(unittest.TestCase):
    def test_official_main_gates_produce_expected_distribution(self) -> None:
        cases, stats = build_official_cases(normalize_msb_root(MSB_ROOT))
        self.assertEqual(2000, len(cases))
        self.assertEqual(64, stats["user_tasks"])
        counts = {}
        for case in cases:
            counts[case.attack_type] = counts.get(case.attack_type, 0) + 1
        self.assertEqual(300, counts["prompt_injection"])
        self.assertEqual(20, counts["search_term_deception"])
        self.assertEqual(80, counts["tool_transfer"])

    def test_current_candidate_is_not_officially_equivalent(self) -> None:
        report = audit(MSB_ROOT, CANDIDATE)
        self.assertEqual(1560, report["candidate"]["records"])
        self.assertEqual(780, report["candidate"]["exact_official_matches"])
        self.assertEqual(780, report["candidate"]["officially_ineligible"])
        self.assertEqual(1220, report["candidate"]["official_cases_missing"])
        self.assertEqual("candidate_not_formal_ready", report["status"])

    def test_official_tool_configs_are_complete(self) -> None:
        report = audit(MSB_ROOT, CANDIDATE)
        self.assertEqual(5, report["tool_config_audit"]["eligible_attack_tool_pairs"])
        self.assertTrue(report["tool_config_audit"]["all_required_files_present"])

    def test_official_metrics_synthetic_replay(self) -> None:
        report = replay(MSB_ROOT)
        self.assertTrue(report["valid"])
        observed = {row["name"]: row for row in report["cases"]}
        self.assertTrue(observed["out_of_scope_empty_model_spacing_bug"]["attack_success"])
        self.assertFalse(observed["tool_transfer_quoted_target_bug"]["attack_success"])


if __name__ == "__main__":
    unittest.main()
