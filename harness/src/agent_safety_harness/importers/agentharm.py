from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable

from ..io import read_jsonl


PROMPT_FIELDS = ("prompt", "user_goal", "goal", "task", "instruction", "user_request", "query")
ID_FIELDS = ("id", "task_id", "behavior_id", "source_id")
CATEGORY_FIELDS = ("category", "harm_category", "risk_type", "safety_category")


def read_agentharm_source(path: Path) -> list[dict[str, Any]]:
    """Read AgentHarm raw JSON/JSONL or already-flattened record files."""
    if path.suffix.lower() == ".jsonl":
        return read_jsonl(path)

    with path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)

    if isinstance(payload, list):
        return [record for record in payload if isinstance(record, dict)]
    if isinstance(payload, dict):
        behaviors = payload.get("behaviors")
        if isinstance(behaviors, list):
            return [record for record in behaviors if isinstance(record, dict)]
        rows = payload.get("rows")
        if isinstance(rows, list):
            return [_unwrap_viewer_row(row) for row in rows]
        return [payload]
    raise ValueError(f"Unsupported AgentHarm source shape in {path}")


def convert_agentharm_records(
    records: Iterable[dict[str, Any]],
    *,
    source_name: str = "agentharm",
    kind: str = "auto",
) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for index, record in enumerate(records, start=1):
        normalized.append(convert_agentharm_record(record, index=index, source_name=source_name, kind=kind))
    return normalized


def convert_agentharm_record(
    record: dict[str, Any],
    *,
    index: int,
    source_name: str,
    kind: str,
) -> dict[str, Any]:
    raw = _unwrap_viewer_row(record)
    task_id = _first_non_empty(raw, ID_FIELDS) or f"{source_name}_{index:06d}"
    prompt = _first_non_empty(raw, PROMPT_FIELDS)
    if not prompt:
        raise ValueError(f"AgentHarm record {task_id!r} has no prompt-like field")

    inferred_kind = infer_agentharm_kind(raw, source_name, kind)
    target_functions = _normalize_string_list(
        raw.get("target_functions") or raw.get("tools") or raw.get("target_tools") or raw.get("functions")
    )
    category = _first_non_empty(raw, CATEGORY_FIELDS)

    return {
        "task_id": f"agentharm:{task_id}",
        "benchmark": "agentharm",
        "modality": "agent",
        "profile": _profile_for_kind(inferred_kind),
        "user_goal": str(prompt),
        "attack_goal": str(prompt) if inferred_kind != "benign" else None,
        "tools": [_tool_spec(name, inferred_kind) for name in target_functions],
        "initial_state": {},
        "labels": {
            "expected_behavior": "complete" if inferred_kind == "benign" else "refuse",
            "safety_category": "benign" if inferred_kind == "benign" else "harmful_action",
            "risk_type": category,
        },
        "metadata": {
            "harness_dataset_role": "source_derived_task",
            "source_dataset": "ai-safety-institute/AgentHarm",
            "source_name": source_name,
            "source_record_index": index,
            "source_id": raw.get("id"),
            "id_original": raw.get("id_original"),
            "name": raw.get("name"),
            "category": category,
            "detailed_prompt": raw.get("detailed_prompt"),
            "hint_included": raw.get("hint_included"),
            "target_functions": target_functions,
            "agentharm_kind": inferred_kind,
            "grading_function": raw.get("grading_function"),
            "raw": raw,
        },
    }


def infer_agentharm_kind(record: dict[str, Any], source_name: str, kind: str) -> str:
    if kind != "auto":
        if kind not in {"harmful", "benign", "chat"}:
            raise ValueError(f"Unsupported AgentHarm kind: {kind}")
        return kind

    surface = " ".join(
        str(value).lower()
        for value in [
            source_name,
            record.get("split"),
            record.get("config"),
            record.get("subset"),
            record.get("kind"),
            record.get("agentharm_kind"),
        ]
        if value is not None
    )
    if "benign" in surface or "harmless" in surface:
        return "benign"
    if "chat" in surface:
        return "chat"
    return "harmful"


def _profile_for_kind(kind: str) -> str:
    if kind == "benign":
        return "benign_agent_task"
    if kind == "chat":
        return "harmful_chat_task"
    return "harmful_agent_task"


def _tool_spec(name: str, kind: str) -> dict[str, Any]:
    return {
        "name": name,
        "type": "function",
        "risk": "medium" if kind == "benign" else "high",
        "source": "agentharm_target_functions",
    }


def _first_non_empty(record: dict[str, Any], fields: Iterable[str]) -> Any:
    for field in fields:
        value = record.get(field)
        if value not in (None, ""):
            return value
    return None


def _normalize_string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [str(key) for key in value.keys()]
    if isinstance(value, list):
        names: list[str] = []
        for item in value:
            if isinstance(item, str):
                names.append(item)
            elif isinstance(item, dict):
                name = item.get("name") or item.get("function") or item.get("id")
                if name:
                    names.append(str(name))
            elif item is not None:
                names.append(str(item))
        return names
    return [str(value)]


def _unwrap_viewer_row(record: dict[str, Any]) -> dict[str, Any]:
    row = record.get("row") if isinstance(record, dict) else None
    if isinstance(row, dict):
        return row
    return record
