from __future__ import annotations

import copy
from concurrent.futures import ThreadPoolExecutor
import unittest
from tests.temp_workspace import WorkspaceTempDir
from orchestrator.registry import content_hash
from orchestrator.budget import BudgetLedger, BudgetExceeded
from evolution.admission import evaluate, evaluate_batch, GATES
from evolution.contracts import validate_contract
from evolution.dataset_version import publish
from evolution.diagnosis import summarize_replays
from evolution.feedback_view import build_feedback, scan_canaries
from evolution.test_generator import generate
from evolution.transforms import validate_transform
from evolution.partitions import build_manifest, GenerationScope

CANARIES = ["PRIVATE-CANARY-fixture-unique"]


def policy():
    return {"version": "fixture-only-v1", "transform": {"spaces": ["wording"], "allowed_pointers": ["/user_goal"],
                                                        "semantic_review_required": False},
            "minimum_valid_replays": 2, "dedup_threshold": .9, "family_quotas": {"family": 1}}


def candidate(text="Please look up the current status using the selected benchmark tool."):
    task = {"task_id": "parent", "user_goal": "Look up the current status.", "benchmark": "fixture"}
    view = build_feedback(task, {"failures": ["safety_violation"]}, CANARIES)
    return generate(task, family="family", feedback=view, pointer="/user_goal", replacement=text,
                    space="wording", policy=policy()["transform"], canaries=CANARIES, scope=scope_for(task))


def scope_for(task=None):
    task = task or {"task_id": "parent", "user_goal": "Look up the current status.", "benchmark": "fixture"}
    return GenerationScope(build_manifest([task], {task["task_id"]: {"family_id": "family", "partition": "E"}}))


def evidence(c):
    return {"kind": "mock", "candidate_hash": content_hash(c), "policy_hash": content_hash(policy()),
            "source_and_license": {"approved": True, "evidence_ref": "fixture/source"},
            "replay": {"valid_attempts": 3, "evidence_refs": ["fixture/replay"]},
            "benign_solvability": {"baseline_success": True, "candidate_success": True, "completion_rule_id": "fixture-completion", "evidence_ref": "fixture/benign"},
            "private_oracle": {"unchanged": True, "rule_id": "private-rule", "evidence_ref": "fixture/oracle"},
            "review": {"approved": True, "reviewer_id": "synthetic-fixture", "evidence_ref": "fixture/review"}}


def decide(c=None, e=None, **kwargs):
    c = c or candidate()
    return evaluate(c, e or evidence(c), policy(), canaries=CANARIES, existing_texts=[],
                    holdout_families=set(), admitted_counts={}, scope=scope_for(), **kwargs)


class AdmissionTests(unittest.TestCase):
    def test_explicit_denials_and_benign_failures_are_rejected(self):
        c = candidate()
        for name, field in [("source_and_license", "approved"), ("review", "approved"),
                            ("benign_solvability", "baseline_success"), ("benign_solvability", "candidate_success")]:
            with self.subTest(gate=name, field=field):
                e = evidence(c)
                e[name][field] = False
                self.assertEqual(decide(c, e, mode="fixture")["status"], "rejected")

    def test_invalid_policy_cannot_silently_change_thresholds(self):
        c = candidate()
        for key, value in [("dedup_threshold", 0), ("dedup_threshold", 1.5),
                           ("minimum_valid_replays", 0), ("family_quotas", {"family": True})]:
            p = policy()
            p[key] = value
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                evaluate(c, evidence(c), p, canaries=CANARIES, existing_texts=[], holdout_families=set(),
                         admitted_counts={}, scope=scope_for(), mode="fixture")

    def test_fixture_requires_all_eight_gates_and_has_no_formal_claim(self):
        result = decide(mode="fixture")
        self.assertTrue(result["admitted"])
        self.assertEqual(set(result["gates"]), set(GATES))
        self.assertFalse(result["formal_qualified"])
        for g in result["gates"].values():
            validate_contract("GateResult", g)

    def test_mock_evidence_never_qualifies_formal_run(self):
        self.assertFalse(decide(mode="formal")["admitted"])

    def test_claiming_real_evidence_cannot_bypass_native_qualification(self):
        c = candidate()
        e = evidence(c)
        e["kind"] = "real"
        result = decide(c, e, mode="formal")
        self.assertFalse(result["admitted"])
        self.assertEqual(result["gates"]["review"]["reason_code"], "native_qualification_not_verified")

    def test_each_missing_evidence_gate_blocks_admission(self):
        c = candidate()
        for name in ["source_and_license", "replay", "benign_solvability", "private_oracle", "review"]:
            e = evidence(c)
            del e[name]
            self.assertFalse(decide(c, e, mode="fixture")["admitted"], name)

    def test_oracle_changes_stay_pending(self):
        c = candidate()
        e = evidence(c)
        e["private_oracle"]["changed"] = True
        self.assertEqual(decide(c, e, mode="fixture")["status"], "pending_review")

    def test_actual_multiple_field_change_is_not_trusted(self):
        c = candidate()
        c["public_task"]["benchmark"] = "changed"
        self.assertEqual(decide(c, mode="fixture")["status"], "pending_review")

    def test_no_change_and_false_hash_are_rejected(self):
        c = candidate()
        c["transform_spec"]["after_hash"] = "0"*64
        self.assertEqual(decide(c, mode="fixture")["status"], "rejected")
        c = candidate("Look up the current status.")
        self.assertEqual(decide(c, mode="fixture")["status"], "rejected")

    def test_semantic_uncertainty_defaults_to_review(self):
        p = policy()["transform"]
        del p["semantic_review_required"]
        c = candidate()
        self.assertEqual(validate_transform(c["parent_task"], c["public_task"], c["transform_spec"], p)["status"], "pending")

    def test_canary_failure_and_holdout_family_are_rejected(self):
        c = candidate()
        c["public_task"]["user_goal"] += CANARIES[0]
        self.assertEqual(decide(c, mode="fixture")["status"], "rejected")
        c = candidate()
        self.assertEqual(evaluate(c, evidence(c), policy(), canaries=CANARIES, existing_texts=[],
                                 holdout_families={"family"}, admitted_counts={}, scope=scope_for(), mode="fixture")["status"], "rejected")

    def test_dedup_and_quotas_are_deterministic(self):
        candidates = [candidate(), candidate("Return a compact status update for the selected account.")]
        e = {content_hash(c): evidence(c) for c in candidates}
        kwargs = dict(canaries=CANARIES, existing_texts=[], holdout_families=set(), scope=scope_for(), mode="fixture")
        first = evaluate_batch(candidates, e, policy(), **kwargs)
        second = evaluate_batch(list(reversed(candidates)), e, policy(), **kwargs)
        self.assertEqual(first, second)
        self.assertEqual(sum(r["admitted"] for r in first), 1)
        c = candidates[0]
        self.assertEqual(evaluate(c, evidence(c), policy(), canaries=CANARIES, existing_texts=[c["public_task"]["user_goal"]], holdout_families=set(), admitted_counts={}, scope=scope_for(), mode="fixture")["gates"]["deduplication"]["status"], "fail")

    def test_feedback_does_not_copy_raw_private_evidence(self):
        task = candidate()["parent_task"]
        view = build_feedback(task, {"failures": ["safety_violation"], "reason": CANARIES[0], "evidence": {"oracle": CANARIES[0]}}, CANARIES)
        self.assertEqual(scan_canaries(view, CANARIES)["status"], "pass")
        task["user_goal"] += CANARIES[0]
        with self.assertRaises(ValueError):
            build_feedback(task, {"failures": []}, CANARIES)

    def test_empty_canary_list_cannot_claim_isolation(self):
        with self.assertRaises(ValueError):
            scan_canaries({}, [])

    def test_candidate_contract_disallows_untracked_fields(self):
        c = candidate()
        validate_contract("TestCandidate", c)
        c["unknown_field"] = "untracked"
        with self.assertRaises(Exception):
            validate_contract("TestCandidate", c)

    def test_replay_does_not_assert_causality_or_mix_identities(self):
        rows = [dict(valid=True, attack_exposure=True, safety_violation=True, identity_hash="fixed") for _ in range(3)]
        self.assertTrue(summarize_replays(rows)["reproducible"])
        self.assertEqual(summarize_replays(rows)["root_cause"], "unconfirmed")
        rows[-1]["identity_hash"] = "different"
        self.assertFalse(summarize_replays(rows)["reproducible"])

    def test_versions_are_idempotent_and_fixture_cannot_become_formal(self):
        c = candidate()
        d = decide(c, mode="fixture")
        with WorkspaceTempDir() as root:
            options = dict(parent_version=None, policy_hash=content_hash(policy()), seed=1, kind="fixture")
            v = publish(root, [c], [d], **options)
            validate_contract("DatasetVersion", v)
            self.assertEqual(v, publish(root, [c], [d], **options))
            with self.assertRaises(ValueError):
                publish(root, [c], [d], **{**options, "kind": "formal"})
            path = root / v["version"] / "candidates.jsonl"
            path.write_text("tampered", encoding="utf-8")
            with self.assertRaises(ValueError):
                publish(root, [c], [d], **options)

    def test_formal_publish_cannot_be_enabled_by_a_forged_qualification_flag(self):
        c = candidate()
        d = decide(c, mode="fixture")
        d.update(mode="formal", formal_qualified=True)
        with WorkspaceTempDir() as root, self.assertRaisesRegex(ValueError, "registered native qualification"):
            publish(root, [c], [d], parent_version=None, policy_hash=content_hash(policy()), seed=0, kind="formal")


class BudgetTests(unittest.TestCase):
    def test_either_limit_stops_and_retry_counts(self):
        with WorkspaceTempDir() as root:
            ledger = BudgetLedger(root/"ledger.db", 2, 100, "fixture")
            ledger.reserve("a", "attack", 1, 60)
            ledger.start("a")
            ledger.settle("a", 50)
            with self.assertRaises(BudgetExceeded):
                ledger.reserve("expensive", "retry", 1, 51)
            ledger.reserve("b", "retry", 1, 20)
            with self.assertRaises(BudgetExceeded):
                ledger.reserve("c", "attack", 1, 0)
            self.assertEqual(ledger.summary()["executions_committed_or_reserved"], 2)

    def test_concurrent_workers_cannot_overreserve(self):
        with WorkspaceTempDir() as root:
            ledger = BudgetLedger(root/"ledger.db", 3, 100, "fixture")
            def reserve(i):
                try:
                    return ledger.reserve(str(i), "attack", 1, 1)
                except BudgetExceeded:
                    return False
            with ThreadPoolExecutor(max_workers=8) as pool:
                self.assertEqual(sum(pool.map(reserve, range(16))), 3)

    def test_crash_reservation_cannot_be_silently_refunded(self):
        with WorkspaceTempDir() as root:
            ledger = BudgetLedger(root/"ledger.db", 3, 100, "fixture")
            ledger.reserve("a", "attack", 1, 60)
            ledger.start("a")
            with self.assertRaises(ValueError):
                ledger.cancel_before_start("a")
            with self.assertRaises(BudgetExceeded):
                ledger.reserve("b", "retry", 1, 60)

    def test_settlement_is_idempotent_and_limits_cannot_change(self):
        with WorkspaceTempDir() as root:
            ledger = BudgetLedger(root/"ledger.db", 3, 100, "fixture")
            ledger.reserve("a", "generation", 0, 20)
            ledger.start("a")
            ledger.settle("a", 10)
            ledger.settle("a", 10)
            self.assertEqual(ledger.summary()["cost_committed_or_reserved"], 10)
            with self.assertRaises(ValueError):
                ledger.settle("a", 11)
            with self.assertRaises(ValueError):
                BudgetLedger(root/"ledger.db", 4, 100, "fixture")

    def test_unexpected_liability_is_recorded_and_blocks_dispatch(self):
        with WorkspaceTempDir() as root:
            ledger = BudgetLedger(root/"ledger.db", 3, 100, "fixture")
            ledger.reserve("a", "attack", 1, 20)
            ledger.start("a")
            with self.assertRaises(BudgetExceeded):
                ledger.settle("a", 25)
            self.assertEqual(ledger.summary()["cost_committed_or_reserved"], 25)
            with self.assertRaises(BudgetExceeded):
                ledger.reserve("b", "attack", 1, 1)
