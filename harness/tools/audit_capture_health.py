from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit CLI capture health independently of safety scoring.")
    parser.add_argument("--capture", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    records = read_jsonl(args.capture)
    report = audit(records, args.capture)
    args.output.mkdir(parents=True, exist_ok=False)
    (args.output / "capture_health.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (args.output / "capture_health.md").write_text(render_markdown(report), encoding="utf-8")
    print(json.dumps({key: report[key] for key in ("records", "unique_task_ids", "valid_capture_count", "invalid_capture_count")}, indent=2))
    return 0


def audit(records: list[dict[str, Any]], input_path: Path) -> dict[str, Any]:
    task_ids = [str(record.get("task_id") or "") for record in records]
    by_source = Counter(str(record.get("benchmark") or "") for record in records)
    returncodes: Counter[str] = Counter()
    capture_stage_statuses: Counter[str] = Counter()
    runtime_error_types: Counter[str] = Counter()
    failure_reasons: Counter[str] = Counter()
    tool_calls_by_source: Counter[str] = Counter()
    zero_calls_by_source: Counter[str] = Counter()
    zero_successful_by_source: Counter[str] = Counter()
    twenty_or_more_by_source: Counter[str] = Counter()
    limit_hits_by_source: Counter[str] = Counter()
    valid = 0
    zero_calls = []
    zero_successful = []
    twenty_or_more = []
    limit_hits = []
    missing_state_diff = []
    invalid_ids = []

    for record in records:
        task_id = str(record.get("task_id") or "")
        source = str(record.get("benchmark") or "")
        evidence = record.get("evidence") or {}
        artifacts = evidence.get("artifacts") or {}
        events = artifacts.get("benchmark_mcp_events") or []
        calls = [event for event in events if event.get("event_type") == "tool_call"]
        successful = [event for event in calls if event.get("ok") is True]
        tool_calls_by_source[source] += len(calls)
        returncode = artifacts.get("returncode")
        returncodes[str(returncode)] += 1
        error_type = str(evidence.get("error_type") or "none")
        capture_stage_statuses[error_type] += 1
        if error_type not in {"none", "EvaluationRuleUnavailable"}:
            runtime_error_types[error_type] += 1
        reason = str(evidence.get("reason") or record.get("reason") or "none")
        environment_validated = artifacts.get("benchmark_environment_validated") is True
        capture_valid = returncode == 0 and environment_validated and artifacts.get("benchmark_tools_available") is True
        if capture_valid:
            valid += 1
        else:
            invalid_ids.append(task_id)
            failure_reasons[categorize_failure(artifacts, evidence)] += 1
        if not calls:
            zero_calls.append(task_id)
            zero_calls_by_source[source] += 1
        if not successful:
            zero_successful.append(task_id)
            zero_successful_by_source[source] += 1
        if len(calls) >= 20:
            twenty_or_more.append(task_id)
            twenty_or_more_by_source[source] += 1
        if any("20 tool-call" in str(event.get("error") or "") for event in calls):
            limit_hits.append(task_id)
            limit_hits_by_source[source] += 1
        for event in calls:
            if environment_validated and event.get("ok") is True and not isinstance(event.get("state_diff"), dict):
                missing_state_diff.append(f"{task_id}:{event.get('name')}")

    duplicates = [task_id for task_id, count in Counter(task_ids).items() if count > 1]
    return {
        "schema_version": 2,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "input": str(input_path.resolve()),
        "records": len(records),
        "unique_task_ids": len(set(task_ids)),
        "duplicate_task_id_groups": len(duplicates),
        "valid_capture_count": valid,
        "invalid_capture_count": len(records) - valid,
        "source_counts": dict(sorted(by_source.items())),
        "returncode_counts": dict(sorted(returncodes.items())),
        "capture_stage_status_counts": dict(sorted(capture_stage_statuses.items())),
        "runtime_error_type_counts": dict(sorted(runtime_error_types.items())),
        "failure_reason_counts": dict(sorted(failure_reasons.items())),
        "tool_call_count": sum(tool_calls_by_source.values()),
        "tool_calls_by_source": dict(sorted(tool_calls_by_source.items())),
        "zero_tool_call_count": len(zero_calls),
        "zero_tool_calls_by_source": dict(sorted(zero_calls_by_source.items())),
        "zero_successful_tool_call_count": len(zero_successful),
        "zero_successful_tool_calls_by_source": dict(sorted(zero_successful_by_source.items())),
        "twenty_or_more_tool_call_count": len(twenty_or_more),
        "twenty_or_more_tool_calls_by_source": dict(sorted(twenty_or_more_by_source.items())),
        "tool_limit_hit_count": len(limit_hits),
        "tool_limit_hits_by_source": dict(sorted(limit_hits_by_source.items())),
        "calls_missing_state_diff_count": len(missing_state_diff),
        "examples": {
            "duplicate_task_ids": duplicates[:20],
            "invalid_capture_ids": invalid_ids[:20],
            "zero_tool_calls": zero_calls[:20],
            "zero_successful_tool_calls": zero_successful[:20],
            "twenty_or_more_tool_calls": twenty_or_more[:20],
            "tool_limit_hits": limit_hits[:20],
            "calls_missing_state_diff": missing_state_diff[:20],
        },
    }


def categorize_failure(artifacts: dict[str, Any], evidence: dict[str, Any]) -> str:
    text = "\n".join(
        str(value or "")
        for value in (artifacts.get("stderr"), evidence.get("reason"), evidence.get("error_type"))
    ).casefold()
    if "401 unauthorized" in text or "authentication fails" in text:
        return "authentication_unauthorized"
    if "timed out" in text or "timeout" in text:
        return "timeout"
    if "mcp" in text and ("cancel" in text or "unavailable" in text or "connect" in text):
        return "mcp_transport"
    if artifacts.get("returncode") not in (0, None):
        return "cli_nonzero_exit"
    return "other"


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Capture Health Audit",
        "",
        f"- Records: {report['records']}",
        f"- Unique task IDs: {report['unique_task_ids']}",
        f"- Duplicate task ID groups: {report['duplicate_task_id_groups']}",
        f"- Valid captures: {report['valid_capture_count']}",
        f"- Invalid captures: {report['invalid_capture_count']}",
        f"- Tool calls: {report['tool_call_count']}",
        f"- Zero tool calls: {report['zero_tool_call_count']}",
        f"- Zero successful tool calls: {report['zero_successful_tool_call_count']}",
        f"- Tasks with at least 20 calls: {report['twenty_or_more_tool_call_count']}",
        f"- Explicit 20-call limit hits: {report['tool_limit_hit_count']}",
        f"- Calls missing state_diff: {report['calls_missing_state_diff_count']}",
        f"- Runtime error markers: {sum(report['runtime_error_type_counts'].values())}",
        f"- Awaiting private scoring (expected): "
        f"{report['capture_stage_status_counts'].get('EvaluationRuleUnavailable', 0)}",
        "",
        "## Failure Reasons",
        "",
    ]
    if not report["failure_reason_counts"]:
        lines.append("- None")
    lines.extend(f"- {name}: {count}" for name, count in report["failure_reason_counts"].items())
    lines.append("")
    return "\n".join(lines)


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    text = path.read_text(encoding="utf-8-sig")
    lines = text.splitlines(keepends=True)
    records = []
    for index, line in enumerate(lines):
        if not line.strip():
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            is_unfinished_tail = index == len(lines) - 1 and not text.endswith(("\n", "\r"))
            if is_unfinished_tail:
                break
            raise
    return records


if __name__ == "__main__":
    raise SystemExit(main())
