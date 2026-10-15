"""Deterministic template backend for local development, without paid API calls."""
from __future__ import annotations

from orchestrator.registry import content_hash
from .feedback_view import scan_canaries
from .transforms import get_pointer, replace_pointer


def generate(parent_task: dict, *, family: str, feedback: dict, pointer: str,
             replacement: str, space: str, policy: dict, canaries: list[str]) -> dict:
    if parent_task.get("evaluation") or parent_task.get("attack_goal") or parent_task.get("metadata", {}).get("attack_success_reference"):
        raise ValueError("Generator requires a public task, never a private oracle")
    if feedback.get("schema_version") != "feedback_view_v1" or feedback.get("public_task", {}).get("task_id") != parent_task.get("task_id"):
        raise ValueError("Feedback must be bound to the public parent")
    scan = scan_canaries({"public_parent": parent_task, "feedback": feedback, "replacement": replacement}, canaries)
    if scan["status"] != "pass":
        raise ValueError("private_canary_detected: generation blocked")
    if pointer not in policy["allowed_pointers"] or space not in policy["spaces"]:
        raise ValueError("Transform outside the registered space")
    before = get_pointer(parent_task, pointer)
    after = replace_pointer(parent_task, pointer, replacement)
    spec = dict(space=space, field_pointer=pointer, before_hash=content_hash(before),
                after_hash=content_hash(replacement), changed_factors=[space])
    result = dict(schema_version="test_candidate_v1", parent_family_id=family, partition="E",
                  parent_task=parent_task, public_task=after, transform_spec=spec,
                  feedback_view_ref=content_hash(feedback), generator={"backend": "local_template", "version": "1", "kind": "fixture"})
    result["candidate_id"] = "candidate-" + content_hash(result)[:24]
    return result
