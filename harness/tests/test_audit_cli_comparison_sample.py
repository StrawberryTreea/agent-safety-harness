from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import unittest


MODULE_PATH = Path(__file__).resolve().parents[1] / "tools" / "audit_cli_comparison_sample.py"
SPEC = importlib.util.spec_from_file_location("audit_cli_comparison_sample", MODULE_PATH)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


class AuditCliComparisonSampleTests(unittest.TestCase):
    def test_explicit_null_safety_pass_remains_unknown(self) -> None:
        score = {
            "task_type": "attack_test",
            "safety_pass": None,
            "safety_violation": False,
            "reason": "attack_not_exposed",
        }

        self.assertEqual(MODULE.outcome(score), "unknown")

    def test_missing_safety_pass_keeps_legacy_fallback(self) -> None:
        score = {"task_type": "attack_test", "safety_violation": False}

        self.assertEqual(MODULE.outcome(score), "pass")


if __name__ == "__main__":
    unittest.main()
