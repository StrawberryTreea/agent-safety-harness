from __future__ import annotations

import copy
import json
import unittest
from pathlib import Path

from tests.temp_workspace import WorkspaceTempDir
from orchestrator.budget import BudgetLedger
from orchestrator.fixture_cycle import CANARIES, fixture_inputs, run
from orchestrator.registry import content_hash
from orchestrator.replay import observe
from orchestrator.workers import run_fixture
from evolution.diagnosis import summarize_replays
from evolution.feedback_view import build_feedback, scan_canaries
from evolution.strategies import WORDING_TEMPLATES, select


class StrategyTests(unittest.TestCase):
    def test_shared_pool_is_deterministic_and_feedback_uses_observed_signals(self):
        parents = [dict(task_id=str(i), user_goal=f"fixture goal {i}") for i in range(4)]
        views = {p["task_id"]: build_feedback(p, {"failures": ["safety_violation"] if p["task_id"] == "3" else []}, CANARIES)
                 for p in parents}
        options = dict(seed=9, limit=12)
        random = select(parents, views, strategy="random", **options)
        feedback = select(parents, views, strategy="feedback", **options)
        self.assertEqual(random, select(list(reversed(parents)), views, strategy="random", **options))
        self.assertEqual(feedback[0]["parent_task_id"], "3")
        signature = lambda rows: {(r["parent_task_id"], r["template_id"], r["replacement"]) for r in rows}
        self.assertEqual(signature(random), signature(feedback))
        self.assertEqual(signature(random), signature(select(parents, views, strategy="wording", **options)))
        self.assertNotEqual(random, select(parents, views, strategy="random", seed=10, limit=12))
        modified = copy.deepcopy(views)
        for view in modified.values():
            view["private_reason"] = "must not affect selection"
        self.assertEqual(feedback, select(parents, modified, strategy="feedback", **options))

    def test_private_population_mismatch_is_blocked_before_dispatch(self):
        public, private, _, _ = fixture_inputs()
        private["user_goal"] += " changed"
        with WorkspaceTempDir() as root:
            ledger = BudgetLedger(root/"budget.db", 5, 1, "fixture")
            with self.assertRaisesRegex(ValueError, "runtime fields differ"):
                run_fixture([public], [private], out=root/"native", agent="unsafe_stub", ledger=ledger,
                            bucket="replay", job_id="mismatch")
            self.assertEqual(ledger.summary()["executions_committed_or_reserved"], 0)

    def test_missing_exposure_and_invalid_scoring_never_prove_reproduction(self):
        receipt = {"identity_hash": "fixture"}
        row = dict(task_id="task", policy_verdict="fail", safety_violation=True, evidence={})
        observation = observe(row, receipt, run_valid=True, evidence_ref="fixture")
        self.assertIsNone(observation["attack_exposure"])
        self.assertEqual(summarize_replays([observation]*3)["replay_outcome"], "incomplete")
        row["evidence"] = {"rule_evaluation": {"attack_exposure": {"value": True}, "validation_errors": ["broken"]}}
        self.assertFalse(observe(row, receipt, run_valid=True, evidence_ref="fixture")["valid"])
        row["evidence"] = {"capture_evidence": {"error_type": "TimeoutError"},
                           "rule_evaluation": {"attack_exposure": {"value": True}}}
        self.assertFalse(observe(row, receipt, run_valid=True, evidence_ref="fixture")["valid"])


class CycleTests(unittest.TestCase):
    def test_native_cycle_replays_controls_and_does_not_invent_new_discoveries(self):
        with WorkspaceTempDir() as root:
            out = root/"cycle"
            report = run(out)
            self.assertEqual(report["replay_outcome"], "reproducible")
            self.assertTrue(report["benign_pair_valid"])
            self.assertTrue(report["benign_baseline_success"])
            self.assertTrue(report["benign_candidate_success"])
            self.assertTrue(report["observed_key_already_in_baseline"])
            self.assertEqual(report["metrics"]["attack_new_discoveries"], 0)
            self.assertIsNone(report["metrics"]["primary_metric"])
            self.assertEqual(report["admitted_candidate_count"], 0)
            self.assertFalse(report["formal_ready"])
            self.assertEqual(report["budget"]["executions_committed_or_reserved"], 6)
            self.assertEqual(report["budget"]["unsettled_jobs"], 0)
            self.assertEqual({b["bucket"]: b["executions"] for b in report["budget"]["buckets"]},
                             {"baseline": 1, "benign": 2, "generation": 0, "replay": 3})
            evidence = json.loads((out/"admission.json").read_text(encoding="utf-8"))
            for name in ("replay", "benign_solvability", "private_oracle"):
                self.assertEqual(evidence["gates"][name]["status"], "pass")
            self.assertEqual(evidence["gates"]["review"]["status"], "pending")
            surfaces = {name: json.loads((out/name).read_text(encoding="utf-8")) for name in ("feedback.json", "candidate.json")}
            self.assertEqual(scan_canaries(surfaces, CANARIES)["status"], "pass")
            self.assertIn(CANARIES[0], (out/"baseline/private.jsonl").read_text(encoding="utf-8"))
            failure = json.loads((out/"failure_evidence.json").read_text(encoding="utf-8"))
            self.assertTrue(all((out/relative).is_file() for relative in failure["trace_refs"]))
            self.assertEqual(report, run(out, resume=True))
            with self.assertRaisesRegex(ValueError, "stale_identity"):
                run(out, strategy="random", resume=True)
            (out/"replays/attempt-1/score/results.jsonl").write_text("tampered", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "manifest mismatch"):
                run(out, resume=True)
