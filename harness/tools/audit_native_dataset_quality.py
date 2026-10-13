from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import Any


SUPPORTED_IMPLEMENTATIONS = {
    "record_query",
    "outbound_sink",
    "filesystem",
    "calendar",
    "sql",
    "clock",
    "source_action",
    "benchmark_server",
}


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit native ASH tasks for runtime and oracle quality anomalies.")
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--pilot", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    records = read_jsonl(args.input)
    pilot = read_jsonl(args.pilot) if args.pilot else []
    report = audit(records, pilot)
    args.output.mkdir(parents=True, exist_ok=False)
    (args.output / "quality_audit.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (args.output / "quality_audit.md").write_text(render_markdown(report), encoding="utf-8")
    print(json.dumps({key: report[key] for key in ("records", "valid", "error_count", "warning_count")}, indent=2))
    return 0 if report["valid"] else 1


def audit(records: list[dict[str, Any]], pilot: list[dict[str, Any]]) -> dict[str, Any]:
    errors: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    ids = [str(record.get("task_id") or "") for record in records]
    source_counts = Counter(str(record.get("benchmark") or "") for record in records)
    profile_counts = Counter(str(record.get("profile") or "") for record in records)
    implementation_counts: Counter[str] = Counter()
    unsafe_call_counts: Counter[str] = Counter()
    safe_call_counts: Counter[str] = Counter()
    placeholder_value_counts: Counter[str] = Counter()
    absolute_path_counts: Counter[str] = Counter()
    goal_groups: defaultdict[str, list[str]] = defaultdict(list)
    signature_groups: defaultdict[str, list[str]] = defaultdict(list)

    if any(not task_id for task_id in ids):
        errors.append(issue("empty_task_id", "One or more tasks have an empty task_id."))
    duplicate_ids = duplicates(ids)
    if duplicate_ids:
        errors.append(issue("duplicate_task_ids", "Task IDs must be unique.", duplicate_ids[:20]))

    for record in records:
        task_id = str(record.get("task_id") or "")
        source = str(record.get("benchmark") or "")
        goal = str(record.get("user_goal") or "").strip()
        if not goal:
            errors.append(issue("empty_user_goal", "Task has an empty user goal.", [task_id]))
        goal_groups[goal].append(task_id)

        metadata = record.get("metadata") or {}
        runtime = metadata.get("ash_runtime") or {}
        environment = runtime.get("environment") or {}
        if environment.get("contract_version") != "ash_native_environment_v1":
            errors.append(issue("missing_native_environment", "Native environment contract is missing.", [task_id]))
        if runtime.get("source_benchmark") != source or environment.get("source_benchmark") != source:
            errors.append(issue("source_contract_mismatch", "Runtime source benchmark differs from the task.", [task_id]))

        public_tools = {str(tool.get("name") or "") for tool in record.get("tools") or []}
        runtime_tools = runtime.get("tools") or []
        runtime_names = {str(tool.get("name") or "") for tool in runtime_tools}
        if public_tools != runtime_names:
            errors.append(issue("tool_surface_mismatch", "Public and runtime tool names differ.", [task_id]))
        for tool in runtime_tools:
            implementation = tool.get("implementation")
            kind = str((implementation or {}).get("kind") or "")
            implementation_counts[f"{source}:{kind or 'missing'}"] += 1
            if not isinstance(implementation, dict) or kind not in SUPPORTED_IMPLEMENTATIONS:
                errors.append(
                    issue(
                        "unsupported_tool_implementation",
                        f"Runtime tool {tool.get('name')!r} has no supported native implementation.",
                        [task_id],
                    )
                )

        reference = metadata.get("attack_success_reference") or {}
        safe_events = normalize_events((reference.get("safe_replay") or {}).get("tool_events"))
        unsafe_events = normalize_events((reference.get("unsafe_replay") or {}).get("tool_events"))
        safe_call_counts[f"{source}:{len(safe_events)}"] += 1
        unsafe_call_counts[f"{source}:{len(unsafe_events)}"] += 1
        if len(safe_events) > 20 or len(unsafe_events) > 20:
            errors.append(issue("reference_exceeds_tool_limit", "Reference replay exceeds 20 tool calls.", [task_id]))
        missing_attack_tools = [str(event.get("name") or "") for event in unsafe_events if event.get("name") not in public_tools]
        if missing_attack_tools:
            errors.append(
                issue("missing_attack_tool", f"Unsafe replay tools are not exposed: {missing_attack_tools}", [task_id])
            )

        serialized_visible = json.dumps(
            {"user_goal": record.get("user_goal"), "tools": record.get("tools"), "runtime": runtime},
            ensure_ascii=False,
            sort_keys=True,
        )
        if "{pid}" in serialized_visible:
            errors.append(issue("unresolved_pid_placeholder", "Model-visible {pid} was not rendered.", [task_id]))
        if "/ABSOLUTE/PATH/TO/SPACE/" in serialized_visible:
            absolute_path_counts[source] += 1
        if contains_scalar(unsafe_events, "value"):
            placeholder_value_counts[source] += 1

        signature = stable_hash(
            {
                "benchmark": source,
                "profile": record.get("profile"),
                "user_goal": record.get("user_goal"),
                "tools": record.get("tools"),
                "evaluation": record.get("evaluation"),
                "runtime": runtime,
            }
        )
        signature_groups[signature].append(task_id)

    exact_duplicates = [task_ids for task_ids in signature_groups.values() if len(task_ids) > 1]
    if exact_duplicates:
        errors.append(
            issue(
                "exact_operational_duplicates",
                "Tasks are operationally identical after removing IDs and provenance.",
                [task_id for group in exact_duplicates[:10] for task_id in group[:5]],
            )
        )

    duplicate_goal_groups = [task_ids for task_ids in goal_groups.values() if len(task_ids) > 1]
    if duplicate_goal_groups:
        warnings.append(
            issue(
                "reused_user_goals",
                "Some source benchmarks intentionally combine one benign goal with multiple attack variants.",
                [task_id for group in sorted(duplicate_goal_groups, key=len, reverse=True)[:5] for task_id in group[:3]],
            )
        )
    if placeholder_value_counts:
        warnings.append(
            issue(
                "reference_placeholder_values",
                "InjecAgent sequence-only references use schema-valid placeholder arguments; arguments are not scored.",
            )
        )
    if absolute_path_counts:
        warnings.append(
            issue(
                "isolated_absolute_path_fixtures",
                "MSB preserves official placeholder paths as keys inside the task-local filesystem mirror.",
            )
        )

    pilot_ids = [str(record.get("task_id") or "") for record in pilot]
    pilot_count = round(len(records) * 0.2)
    pilot_prefix = pilot_ids[:pilot_count] if pilot else []
    pilot_sources = Counter(
        str(record.get("benchmark") or "") for record in pilot[:pilot_count]
    ) if pilot else Counter()
    if pilot and (len(pilot_ids) != len(records) or set(pilot_ids) != set(ids)):
        errors.append(issue("pilot_membership_mismatch", "Pilot ordering is not a permutation of the full dataset."))

    return {
        "schema_version": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "records": len(records),
        "unique_task_ids": len(set(ids)),
        "valid": not errors,
        "error_count": len(errors),
        "warning_count": len(warnings),
        "source_counts": dict(sorted(source_counts.items())),
        "profile_count": len(profile_counts),
        "implementation_counts": dict(sorted(implementation_counts.items())),
        "safe_reference_call_counts": dict(sorted(safe_call_counts.items())),
        "unsafe_reference_call_counts": dict(sorted(unsafe_call_counts.items())),
        "maximum_safe_reference_calls": maximum_reference_calls(safe_call_counts),
        "maximum_unsafe_reference_calls": maximum_reference_calls(unsafe_call_counts),
        "duplicate_goal_groups": len(duplicate_goal_groups),
        "maximum_tasks_per_goal": max((len(group) for group in goal_groups.values()), default=0),
        "exact_operational_duplicate_groups": len(exact_duplicates),
        "placeholder_value_counts": dict(sorted(placeholder_value_counts.items())),
        "isolated_absolute_path_counts": dict(sorted(absolute_path_counts.items())),
        "pilot": {
            "configured": bool(pilot),
            "records": len(pilot_prefix),
            "source_counts": dict(sorted(pilot_sources.items())),
            "unique_task_ids": len(set(pilot_prefix)),
        },
        "errors": errors,
        "warnings": warnings,
    }


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Native Dataset Quality Audit",
        "",
        f"- Records: {report['records']}",
        f"- Unique task IDs: {report['unique_task_ids']}",
        f"- Valid: {str(report['valid']).lower()}",
        f"- Errors: {report['error_count']}",
        f"- Warnings: {report['warning_count']}",
        f"- Exact operational duplicate groups: {report['exact_operational_duplicate_groups']}",
        f"- Maximum unsafe reference calls: {report['maximum_unsafe_reference_calls']}",
        "",
        "## Source Counts",
        "",
    ]
    lines.extend(f"- {name}: {count}" for name, count in report["source_counts"].items())
    lines.extend(["", "## Pilot", ""])
    lines.append(f"- Records: {report['pilot']['records']}")
    lines.extend(f"- {name}: {count}" for name, count in report["pilot"]["source_counts"].items())
    for title, key in (("Errors", "errors"), ("Warnings", "warnings")):
        lines.extend(["", f"## {title}", ""])
        if not report[key]:
            lines.append("- None")
        for item in report[key]:
            suffix = f" Examples: {', '.join(item['examples'])}." if item.get("examples") else ""
            lines.append(f"- `{item['code']}`: {item['message']}{suffix}")
    lines.append("")
    return "\n".join(lines)


def issue(code: str, message: str, examples: list[str] | None = None) -> dict[str, Any]:
    return {"code": code, "message": message, "examples": examples or []}


def normalize_events(value: Any) -> list[dict[str, Any]]:
    if not value:
        return []
    if isinstance(value, dict):
        return [value]
    return list(value)


def maximum_reference_calls(counts: Counter[str]) -> int:
    values = [int(key.rsplit(":", 1)[1]) for key in counts]
    return max(values, default=0)


def stable_hash(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def contains_scalar(value: Any, target: str) -> bool:
    if isinstance(value, dict):
        return any(contains_scalar(item, target) for item in value.values())
    if isinstance(value, list):
        return any(contains_scalar(item, target) for item in value)
    return value == target


def duplicates(values: list[str]) -> list[str]:
    counts = Counter(values)
    return [value for value, count in counts.items() if count > 1]


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open("r", encoding="utf-8-sig") as handle:
        return [json.loads(line) for line in handle if line.strip()]


if __name__ == "__main__":
    raise SystemExit(main())
