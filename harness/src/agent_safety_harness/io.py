from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any, Iterable


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8-sig") as handle:
        for line_number, line in enumerate(handle, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise ValueError(f"Invalid JSONL at {path}:{line_number}: {exc}") from exc
    return records


def write_json(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")


def write_jsonl(path: Path, records: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True))
            handle.write("\n")


def append_jsonl(path: Path, records: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True))
            handle.write("\n")


def write_csv(path: Path, records: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "task_id",
        "benchmark",
        "agent_id",
        "task_type",
        "safety_pass",
        "reason",
        "task_success",
        "safety_violation",
        "attack_success",
        "unsafe_tool_call",
        "data_leak",
        "refusal",
        "policy_verdict",
        "score",
        "usage_source",
        "prompt_tokens",
        "completion_tokens",
        "total_tokens",
        "usage_model",
        "usage_component",
    ]
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(_flatten_usage(record) for record in records)


def _flatten_usage(record: dict[str, Any]) -> dict[str, Any]:
    row = dict(record)
    cost = record.get("cost") or {}
    if isinstance(cost, dict):
        row["usage_source"] = cost.get("usage_source")
        row["prompt_tokens"] = cost.get("prompt_tokens")
        row["completion_tokens"] = cost.get("completion_tokens")
        row["total_tokens"] = cost.get("total_tokens")
        row["usage_model"] = cost.get("model")
        row["usage_component"] = cost.get("component")
    return row
