from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

from ..io import read_jsonl


def read_records(path: Path, records_path: str | None = None) -> list[dict[str, Any]]:
    if path.suffix.lower() == ".csv":
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            return [dict(row) for row in csv.DictReader(handle)]

    if path.suffix.lower() in {".jsonl", ".ndjson"}:
        return [_unwrap_row(record) for record in read_jsonl(path) if isinstance(record, dict)]

    payload = json.loads(path.read_text(encoding="utf-8"))
    records = _get_path(payload, records_path) if records_path else payload
    if isinstance(records, dict):
        for key in ("records", "tasks", "examples", "data", "behaviors", "test_cases"):
            value = records.get(key)
            if isinstance(value, list):
                records = value
                break
    if not isinstance(records, list):
        raise ValueError(f"Mapped importer expected a record list in {path}")
    return [_unwrap_row(record) for record in records if isinstance(record, dict)]


def convert_mapped_records(records: list[dict[str, Any]], mapping: dict[str, Any], source_name: str) -> list[dict[str, Any]]:
    return [convert_mapped_record(record, mapping, source_name, index) for index, record in enumerate(records, start=1)]


def convert_mapped_record(record: dict[str, Any], mapping: dict[str, Any], source_name: str, index: int) -> dict[str, Any]:
    fields = mapping.get("fields") or {}
    labels = _deep_merge_dict(dict(mapping.get("labels") or {}), dict(mapping.get("static_labels") or {}))
    label_fields = mapping.get("label_fields") or {}
    metadata_fields = mapping.get("metadata_fields") or {}
    benchmark = str(mapping["benchmark"])
    profile = str(mapping["profile"])
    raw_id = _first_value(record, fields.get("task_id")) or f"{benchmark}_{index:06d}"
    user_goal = _format_template(record, fields, mapping.get("user_goal_template")) or _first_value(
        record, fields.get("user_goal")
    )
    if not user_goal:
        raise ValueError(f"Mapped record {raw_id!r} has no user_goal field")
    attack_goal = _first_value(record, fields.get("attack_goal"))
    tools = _normalize_tools(mapping.get("static_tools"), str(mapping.get("tool_default_risk", "medium")))
    tools.extend(_normalize_tools(_first_value(record, fields.get("tools")), str(mapping.get("tool_default_risk", "medium"))))
    initial_state = _deep_merge_dict(
        _normalize_state(mapping.get("static_initial_state")),
        _normalize_state(_first_value(record, fields.get("initial_state"))),
    )
    secrets = _first_value(record, fields.get("secrets"))
    if secrets:
        initial_state["secrets"] = _normalize_string_list(secrets)

    risk_type = _first_value(record, fields.get("risk_type"))
    if risk_type is not None:
        labels["risk_type"] = risk_type
    expected_behavior = _first_value(record, fields.get("expected_behavior"))
    if expected_behavior:
        labels["expected_behavior"] = expected_behavior
    for out_key, path_spec in label_fields.items():
        value = _first_value(record, path_spec)
        if value is not None:
            labels[out_key] = _normalize_label_value(value)
    labels.setdefault("expected_behavior", mapping.get("expected_behavior", "complete"))
    labels.setdefault("safety_category", mapping.get("safety_category", profile))

    metadata = _deep_merge_dict(
        dict(mapping.get("static_metadata") or {}),
        {
            "harness_dataset_role": "source_derived_task",
            "source_name": source_name,
            "source_dataset": mapping.get("source_dataset", benchmark),
            "source_record_index": index,
            "raw": record,
        },
    )
    for out_key, path_spec in metadata_fields.items():
        value = _first_value(record, path_spec)
        if value is not None:
            metadata[out_key] = value
    for noisy_key in ("agentharm_kind", "grading_function", "attacker_instruction", "user_tool", "attacker_tool", "injection", "malicious_instruction"):
        if noisy_key in labels:
            metadata[noisy_key] = labels.pop(noisy_key)
    trace = _first_value(record, fields.get("trace"))
    if trace is not None:
        metadata["trace"] = _normalize_trace(trace, f"{benchmark}:{raw_id}")

    return {
        "task_id": f"{benchmark}:{raw_id}",
        "benchmark": benchmark,
        "modality": "agent",
        "profile": profile,
        "user_goal": str(user_goal),
        "attack_goal": str(attack_goal) if attack_goal else None,
        "tools": tools,
        "initial_state": initial_state,
        "labels": labels,
        "metadata": metadata,
    }


def _first_value(record: dict[str, Any], path_spec: Any) -> Any:
    if path_spec is None:
        return None
    paths = path_spec if isinstance(path_spec, list) else [path_spec]
    for path in paths:
        value = _get_path(record, path)
        if value not in (None, ""):
            return value
    return None


def _get_path(record: Any, path: Any) -> Any:
    if not path:
        return None
    current = record
    for part in str(path).split("."):
        if isinstance(current, dict):
            current = current.get(part)
        elif isinstance(current, list) and part.isdigit():
            index = int(part)
            current = current[index] if index < len(current) else None
        else:
            return None
        if current is None:
            return None
    return current


def _format_template(record: dict[str, Any], fields: dict[str, Any], template: Any) -> str | None:
    if not template:
        return None
    context = _SafeFormatDict()
    context.update({key: value for key, value in record.items() if isinstance(key, str)})
    for key, path_spec in fields.items():
        value = _first_value(record, path_spec)
        context[key] = _stringify_template_value(value)
    return str(template).format_map(context).strip()


def _normalize_tools(value: Any, default_risk: str) -> list[dict[str, Any]]:
    if value is None:
        return []
    if isinstance(value, dict):
        return [_tool_from_value(key, spec, default_risk) for key, spec in value.items()]
    if isinstance(value, list):
        return [_tool_from_value(None, item, default_risk) for item in value]
    if isinstance(value, str):
        return [_tool_from_value(None, value, default_risk)]
    return [_tool_from_value(None, str(value), default_risk)]


def _tool_from_value(name_hint: str | None, value: Any, default_risk: str) -> dict[str, Any]:
    if isinstance(value, dict):
        name = value.get("name") or value.get("tool") or value.get("function") or value.get("id") or name_hint
        return {
            "name": str(name or "unknown_tool"),
            "type": value.get("type", "function"),
            "risk": value.get("risk", default_risk),
            "description": value.get("description"),
        }
    return {"name": str(value if name_hint is None else name_hint), "type": "function", "risk": default_risk}


def _normalize_state(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _normalize_string_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value if item is not None]
    return [str(value)]


def _normalize_label_value(value: Any) -> Any:
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"true", "yes", "1", "unsafe", "violation", "fail"}:
            return True
        if lowered in {"false", "no", "0", "safe", "pass"}:
            return False
    return value


def _normalize_trace(value: Any, task_id: str) -> list[dict[str, Any]]:
    events = value
    if isinstance(value, str):
        try:
            events = json.loads(value)
        except json.JSONDecodeError:
            return [{"event_id": f"{task_id}:1", "role": "agent", "event_type": "message", "content": {"text": value}}]
    if not isinstance(events, list):
        return []
    normalized = []
    for index, event in enumerate(events, start=1):
        if not isinstance(event, dict):
            normalized.append(
                {
                    "event_id": f"{task_id}:{index}",
                    "role": "agent",
                    "event_type": "message",
                    "content": {"text": str(event)},
                }
            )
            continue
        content = event.get("content")
        if not isinstance(content, dict):
            content = {"text": str(content if content is not None else event.get("text", ""))}
        normalized.append(
            {
                "event_id": str(event.get("event_id") or f"{task_id}:{index}"),
                "role": event.get("role", "agent"),
                "event_type": event.get("event_type", "message"),
                "content": content,
                **({"timestamp": event["timestamp"]} if event.get("timestamp") else {}),
            }
        )
    return normalized


def _stringify_template_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _deep_merge_dict(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    merged: dict[str, Any] = dict(base)
    for key, value in overlay.items():
        if key in merged and isinstance(merged[key], dict) and isinstance(value, dict):
            merged[key] = _deep_merge_dict(dict(merged[key]), value)
        else:
            merged[key] = value
    return merged


class _SafeFormatDict(dict):
    def __missing__(self, key: str) -> str:
        return ""


def _unwrap_row(record: dict[str, Any]) -> dict[str, Any]:
    row = record.get("row")
    return row if isinstance(row, dict) else record
