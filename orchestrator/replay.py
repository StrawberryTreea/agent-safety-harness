"""Execute fixture replays with the native public-capture/private-score path."""
from __future__ import annotations

import copy
import json
from pathlib import Path

from .registry import content_hash
from .workers import read_rows, run_fixture, write_json
from evolution.diagnosis import summarize_replays


def bind_private(public: dict, private_parent: dict) -> dict:
    """Apply a public transform while retaining the evaluator-owned oracle."""
    if public.get("task_id") != private_parent.get("task_id"):
        raise ValueError("Public/private task ID mismatch")
    return {**copy.deepcopy(public), **{k: copy.deepcopy(private_parent[k]) for k in
                                      ("evaluation", "attack_goal") if k in private_parent}}


def observe(row: dict, receipt: dict, *, run_valid: bool, evidence_ref: str) -> dict:
    rules = row.get("evidence", {}).get("rule_evaluation", {})
    exposure = rules.get("attack_exposure") or {}
    violation = row.get("safety_violation")
    capture_error = row.get("evidence", {}).get("capture_evidence", {}).get("error_type")
    valid = (run_valid is True and row.get("policy_verdict") in {"pass", "fail"}
             and type(violation) is bool and not rules.get("validation_errors")
             and capture_error in {None, "EvaluationRuleUnavailable"})
    return dict(task_id=row["task_id"], valid=valid, attack_exposure=exposure.get("value"),
                safety_violation=violation, task_success=row.get("task_success"),
                identity_hash=receipt["identity_hash"], evidence_ref=evidence_ref,
                rule_id=(rules.get("safety_violation") or {}).get("rule_id"), kind="fixture")


def execute(public: dict, private: dict, *, out: Path, ledger, bucket: str, job_id: str) -> dict:
    receipt = run_fixture([public], [private], out=out, agent="unsafe_stub", ledger=ledger,
                          bucket=bucket, job_id=job_id)
    rows = read_rows(out/receipt["results_ref"])
    summary = json.loads((out/"score/summary.json").read_text(encoding="utf-8"))
    if len(rows) != 1 or rows[0]["task_id"] != public["task_id"]:
        raise ValueError("Native result population mismatch")
    return observe(rows[0], receipt, run_valid=summary.get("run_valid"),
                   evidence_ref=str(out/receipt["results_ref"]))


def replay(public: dict, private: dict, *, out: Path, ledger, job_prefix: str, attempts=3) -> dict:
    if type(attempts) is not int or attempts < 1:
        raise ValueError("Positive replay count required")
    observations = [execute(public, private, out=out/f"attempt-{i+1}", ledger=ledger, bucket="replay",
                            job_id=f"{job_prefix}:{i+1}") for i in range(attempts)]
    report = {**summarize_replays(observations), "attempts": observations,
              "public_task_hash": content_hash(public), "private_rules_hash": content_hash(private["evaluation"]),
              "kind": "fixture", "formal_qualified": False}
    destination = out/"replay.json"
    if destination.exists():
        if json.loads(destination.read_text(encoding="utf-8")) != report:
            raise ValueError("Replay report changed on resume")
    else:
        write_json(destination, report)
    return report
