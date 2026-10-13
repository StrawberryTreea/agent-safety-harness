from __future__ import annotations

import unittest
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
for path in (ROOT, ROOT / "src"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from agent_safety_harness.evaluation.rules import EvaluationContext, evaluate_rule
from tools.audit_injecagent_ras_v6 import strict_attack_match


class RasProjectionAuditTests(unittest.TestCase):
    def setUp(self) -> None:
        self.rule = {
            "id": "ras_attack_args",
            "op": "tool_called",
            "name": "target_tool",
            "args": {"identifier": "attacker-value"},
        }

    def evaluate(self, args: dict, *, ok: bool) -> bool:
        context = EvaluationContext(
            response="",
            tool_events=[
                {"index": 0, "name": "target_tool", "args": args, "ok": ok}
            ],
            state={},
        )
        return evaluate_rule(self.rule, context).value

    def test_current_rule_matches_superset(self) -> None:
        self.assertTrue(
            self.evaluate(
                {"identifier": "attacker-value", "unrelated": "benign"}, ok=True
            )
        )

    def test_current_rule_matches_failed_call(self) -> None:
        self.assertTrue(self.evaluate({"identifier": "attacker-value"}, ok=False))

    def test_strict_executed_match_rejects_superset_and_failure(self) -> None:
        references = [
            {"tool": "target_tool", "args": {"identifier": "attacker-value"}}
        ]
        self.assertTrue(
            strict_attack_match(
                references,
                "target_tool",
                {"identifier": "attacker-value"},
                True,
            )
        )
        self.assertFalse(
            strict_attack_match(
                references,
                "target_tool",
                {"identifier": "attacker-value", "unrelated": "benign"},
                True,
            )
        )
        self.assertFalse(
            strict_attack_match(
                references,
                "target_tool",
                {"identifier": "attacker-value"},
                False,
            )
        )


if __name__ == "__main__":
    unittest.main()
