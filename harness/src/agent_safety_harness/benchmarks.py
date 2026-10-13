from __future__ import annotations

from importlib.resources import files
from pathlib import Path
from typing import Any

from .io import read_jsonl
from .schema import TaskSpec


BUILTIN_BENCHMARKS = {
    "agentharm": "agentharm_sample.jsonl",
    "agentdojo": "agentdojo_sample.jsonl",
    "r_judge": "r_judge_sample.jsonl",
}


def builtin_path(benchmark: str) -> Path:
    try:
        filename = BUILTIN_BENCHMARKS[benchmark]
    except KeyError as exc:
        known = ", ".join(sorted(BUILTIN_BENCHMARKS))
        raise ValueError(f"Unknown benchmark {benchmark!r}. Known: {known}") from exc
    return Path(str(files("agent_safety_harness").joinpath("data", filename)))


def load_tasks(
    benchmark: str,
    input_path: str | None = None,
    sample: int | None = None,
    private_input_path: str | None = None,
) -> list[TaskSpec]:
    path = Path(input_path) if input_path else builtin_path(benchmark)
    records = read_jsonl(path)
    if private_input_path:
        records = merge_public_private_records(records, read_jsonl(Path(private_input_path)))
    tasks = [TaskSpec.from_record(record) for record in records]
    if sample is not None:
        tasks = tasks[:sample]
    return tasks


def merge_public_private_records(
    public_records: list[dict[str, Any]], private_records: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    private_by_id = {str(record.get("task_id") or ""): record for record in private_records}
    if "" in private_by_id or len(private_by_id) != len(private_records):
        raise ValueError("Private task records must have unique non-empty task_id values")
    public_ids = [str(record.get("task_id") or "") for record in public_records]
    if set(public_ids) != set(private_by_id):
        missing_private = sorted(set(public_ids) - set(private_by_id))[:10]
        missing_public = sorted(set(private_by_id) - set(public_ids))[:10]
        raise ValueError(
            f"Public/private task_id mismatch: missing_private={missing_private}, missing_public={missing_public}"
        )
    merged = []
    for public in public_records:
        private = private_by_id[str(public["task_id"])]
        source = private.get("source") if isinstance(private.get("source"), dict) else {}
        metadata = dict(source)
        metadata["ash_runtime"] = private.get("runtime")
        merged.append(
            {
                **public,
                "attack_goal": None,
                "initial_state": {},
                "labels": private.get("labels") or {},
                "evaluation": private.get("evaluation") or {},
                "metadata": metadata,
            }
        )
    return merged
