"""Frozen family partitions, enforced before feedback selection or generation."""
from __future__ import annotations

import copy
from types import MappingProxyType

from orchestrator.registry import content_hash
from .contracts import validate_contract


def assert_public(task):
    if not isinstance(task, dict) or any(key in task for key in ("evaluation", "attack_goal")):
        raise ValueError("generation_private_fields_forbidden")
    if "attack_success_reference" in task.get("metadata", {}):
        raise ValueError("generation_private_fields_forbidden")
    if not isinstance(task.get("task_id"), str) or not task["task_id"].strip():
        raise ValueError("generation_task_id_required")


def build_manifest(tasks: list[dict], assignments: dict[str, dict]) -> dict:
    if not isinstance(tasks, list) or not isinstance(assignments, dict):
        raise ValueError("partition_inputs_invalid")
    entries, seen = [], set()
    for task in tasks:
        assert_public(task)
        task_id = task["task_id"]
        if task_id in seen or task_id not in assignments:
            raise ValueError("partition_assignment_missing_or_duplicate")
        assignment = assignments[task_id]
        if not isinstance(assignment, dict) or set(assignment) != {"family_id", "partition"}:
            raise ValueError("partition_assignment_fields_invalid")
        seen.add(task_id)
        entries.append(dict(task_id=task_id, family_id=assignment["family_id"],
                            partition=assignment["partition"], public_task_hash=content_hash(task)))
    if seen != set(assignments):
        raise ValueError("partition_assignment_population_mismatch")
    payload = dict(schema_version="partition_manifest_v1", entries=sorted(entries, key=lambda row: row["task_id"]))
    manifest = {**payload, "manifest_hash": content_hash(payload)}
    GenerationScope(manifest)
    return manifest


class GenerationScope:
    """The controller owns the registry; a candidate cannot choose its partition."""

    def __init__(self, manifest: dict):
        validate_contract("PartitionManifest", manifest)
        value = copy.deepcopy(manifest)
        if value["manifest_hash"] != content_hash({k: v for k, v in value.items() if k != "manifest_hash"}):
            raise ValueError("partition_manifest_hash_mismatch")
        entries, families = {}, {}
        for row in value["entries"]:
            if row["task_id"] in entries:
                raise ValueError("partition_duplicate_task_id")
            family, partition = row["family_id"], row["partition"]
            if family in families and families[family] != partition:
                raise ValueError("partition_family_overlap")
            families[family] = partition
            entries[row["task_id"]] = MappingProxyType(row)
        self._entries = MappingProxyType(entries)
        self._manifest = value

    @property
    def manifest_hash(self):
        return self._manifest["manifest_hash"]

    def snapshot(self):
        return copy.deepcopy(self._manifest)

    def authorize(self, task: dict, *, family: str | None = None) -> dict:
        # Reject by ID/partition before reading text, labels, or feedback.
        task_id = task.get("task_id") if isinstance(task, dict) else None
        if not isinstance(task_id, str) or not task_id.strip():
            raise ValueError("generation_task_id_required")
        row = self._entries.get(task_id)
        if row is None:
            raise ValueError("generation_task_not_registered")
        if row["partition"] != "E":
            raise ValueError("generation_partition_excluded")
        assert_public(task)
        if family is not None and family != row["family_id"]:
            raise ValueError("generation_family_mismatch")
        if content_hash(task) != row["public_task_hash"]:
            raise ValueError("generation_parent_hash_mismatch")
        return dict(row)


def validate_feedback(parent: dict, feedback: dict):
    validate_contract("FeedbackView", feedback)
    expected = {k: parent[k] for k in ("task_id", "benchmark", "profile", "user_goal") if k in parent}
    if feedback["public_task"] != expected:
        raise ValueError("generation_feedback_parent_mismatch")
