"""Only explicitly allowed public text crosses the generator boundary."""
from __future__ import annotations

from orchestrator.registry import canonical_json, content_hash


def scan_canaries(value, canaries: list[str]) -> dict:
    if not canaries or any(not isinstance(c, str) or not c for c in canaries):
        raise ValueError("Nonempty canaries are required; empty scans do not prove isolation")
    surface = canonical_json(value)
    # Return opaque identifiers rather than the private canary tokens themselves.
    hits = [content_hash(token) for token in canaries if token in surface]
    return dict(status="fail" if hits else "pass", reason_code="private_canary_detected" if hits else "no_canary_hit",
                canary_ids=hits, checked_count=len(canaries),
                limitation="Exact-string sentinel check; not proof against semantic or encoded leakage")


def build_feedback(public_task: dict, observation: dict, canaries: list[str]) -> dict:
    allowed_signals = {"safety_violation", "attack_success", "task_failure", "rule_eval_failure"}
    signals = observation.get("failures", [])
    if not isinstance(signals, list) or any(s not in allowed_signals for s in signals):
        raise ValueError("Unsupported feedback signals")
    # No metadata, labels, initial_state, oracle, arbitrary reason, or raw evidence.
    task = {k: public_task[k] for k in ("task_id", "benchmark", "profile", "user_goal") if k in public_task}
    view = dict(schema_version="feedback_view_v1", public_task=task, observed_signals=sorted(set(signals)),
                root_cause="unconfirmed")
    result = scan_canaries(view, canaries)
    if result["status"] != "pass":
        raise ValueError("private_canary_detected: feedback blocked")
    return view
