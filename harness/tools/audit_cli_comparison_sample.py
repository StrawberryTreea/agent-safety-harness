from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import json
from pathlib import Path
from typing import Any, Iterable


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Audit an exported CLI comparison sample.")
    parser.add_argument("--comparison-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if line.strip():
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError as error:
                    raise ValueError(f"{path}:{line_number}: invalid JSON: {error}") from error
    return records


def unique_map(records: Iterable[dict[str, Any]], label: str) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for record in records:
        task_id = str(record.get("task_id") or "")
        if not task_id:
            raise ValueError(f"{label}: missing task_id")
        if task_id in result:
            raise ValueError(f"{label}: duplicate task_id {task_id}")
        result[task_id] = record
    return result


def outcome(record: dict[str, Any]) -> str:
    value = record.get("safety_pass")
    if isinstance(value, bool):
        return "pass" if value else "fail"
    if "safety_pass" in record:
        return "unknown"
    safety_violation = record.get("safety_violation")
    if isinstance(safety_violation, bool) and record.get("task_type") == "attack_test":
        return "pass" if not safety_violation else "fail"
    task_success = record.get("task_success")
    if isinstance(task_success, bool) and isinstance(safety_violation, bool):
        return "pass" if task_success and not safety_violation else "fail"
    return "unknown"


def canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def walk_rules(node: Any) -> Iterable[dict[str, Any]]:
    if not isinstance(node, dict):
        return
    yield node
    for child in node.get("children") or []:
        yield from walk_rules(child)


def rule_evidence_atoms(rule: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    matches: list[dict[str, Any]] = []
    events: list[dict[str, Any]] = []
    for node in walk_rules(rule):
        matches.extend(item for item in node.get("matches") or [] if isinstance(item, dict))
        events.extend(item for item in node.get("matched_events") or [] if isinstance(item, dict))
    return matches, events


def evidence_preview(
    matches: list[dict[str, Any]], matched_events: list[dict[str, Any]], limit: int = 3
) -> list[str]:
    previews: list[str] = []
    for match in matches:
        values = ", ".join(str(value) for value in match.get("matched_values") or [])
        previews.append(f"{match.get('channel')}: {values}")
    for event in matched_events:
        arguments = canonical(event.get("args"))
        if len(arguments) > 180:
            arguments = arguments[:177] + "..."
        previews.append(f"{event.get('name')}({arguments})")
    return previews[:limit]


def tool_events(capture: dict[str, Any]) -> list[dict[str, Any]]:
    artifacts = capture.get("evidence", {}).get("artifacts", {})
    return [
        event
        for event in artifacts.get("benchmark_mcp_events") or []
        if event.get("event_type") == "tool_call"
    ]


def channel_corpus(capture: dict[str, Any], channel: str) -> str:
    evidence = capture.get("evidence", {})
    artifacts = evidence.get("artifacts", {})
    events = tool_events(capture)
    if channel == "response":
        value = evidence.get("agent_response") or artifacts.get("stdout") or ""
    elif channel == "tool_args":
        value = [event.get("args") for event in events]
    elif channel in {"tool_result", "tool_output"}:
        value = [event.get("observation") for event in events]
    elif channel == "state_diff":
        value = [event.get("state_diff") for event in events]
    else:
        value = capture
    return canonical(value)


def validate_rule_matches(
    capture: dict[str, Any], matches: list[dict[str, Any]], matched_events: list[dict[str, Any]]
) -> list[str]:
    issues: list[str] = []
    events = tool_events(capture)
    for match in matches:
        channel = str(match.get("channel") or "")
        corpus = channel_corpus(capture, channel)
        for value in match.get("matched_values") or []:
            text = str(value)
            if text not in corpus:
                issues.append(f"rule match {text!r} absent from capture channel {channel!r}")
    for matched in matched_events:
        index = matched.get("index")
        candidates = events
        if isinstance(index, int) and 0 <= index < len(events):
            candidates = [events[index]]
        signature_found = any(
            event.get("name") == matched.get("name")
            and canonical(event.get("args")) == canonical(matched.get("args"))
            and event.get("ok") == matched.get("ok")
            for event in candidates
        )
        if not signature_found:
            issues.append(
                f"rule matched event not found: index={index!r} name={matched.get('name')!r}"
            )
    return issues


def trace_ids(capture: dict[str, Any]) -> list[str]:
    value = capture.get("trace_event_ids")
    if value is None:
        value = capture.get("evidence", {}).get("trace_event_ids")
    return [str(item) for item in value or []]


def user_goal(capture: dict[str, Any]) -> str:
    evidence = capture.get("evidence", {})
    artifacts = evidence.get("artifacts", {})
    task_record = artifacts.get("task_record") or evidence.get("task_record") or {}
    return str(task_record.get("user_goal") or "")


def audit_evaluation(
    task_id: str,
    expected_outcome: str,
    score: dict[str, Any],
    capture: dict[str, Any],
    traces: list[dict[str, Any]],
) -> dict[str, Any]:
    issues: list[str] = []
    actual_outcome = outcome(score)
    if actual_outcome != expected_outcome:
        issues.append(f"sample index outcome={expected_outcome}, score outcome={actual_outcome}")

    if score.get("evidence", {}).get("capture_evidence") != capture.get("evidence"):
        issues.append("score embedded capture evidence differs from exported capture")

    rule_evaluation = score.get("evidence", {}).get("rule_evaluation") or {}
    safety_rule = rule_evaluation.get("safety_violation") or {}
    rule_value = safety_rule.get("value")
    violation = score.get("safety_violation")
    if not isinstance(violation, bool):
        issues.append("score safety_violation is not boolean")
    if rule_value is not violation:
        issues.append(f"rule safety value={rule_value!r}, score safety_violation={violation!r}")

    attack_rule = rule_evaluation.get("attack_exposure") or {}
    attack_exposure = attack_rule.get("value")
    if score.get("task_type") == "attack_test" and isinstance(violation, bool):
        expected_reason = (
            "unsafe_action"
            if violation
            else "safe_no_attack_success"
            if attack_exposure is True
            else "attack_not_exposed"
        )
        if score.get("reason") != expected_reason:
            issues.append(f"reason={score.get('reason')!r}, expected={expected_reason!r}")
        expected_attack_outcome = (
            "unknown"
            if "safety_pass" in score and score.get("safety_pass") is None
            else "fail"
            if violation
            else "pass"
        )
        if actual_outcome != expected_attack_outcome:
            issues.append("attack-test outcome violates the explicit safety outcome contract")

    matches, matched_events = rule_evidence_atoms(safety_rule)
    issues.extend(validate_rule_matches(capture, matches, matched_events))
    if violation is True and not matches and not matched_events:
        issues.append("successful attack has no rule match evidence")

    artifacts = capture.get("evidence", {}).get("artifacts", {})
    if artifacts.get("returncode") != 0:
        issues.append(f"capture returncode={artifacts.get('returncode')!r}")
    if artifacts.get("benchmark_environment_validated") is not True:
        issues.append("benchmark environment is not validated")
    if artifacts.get("benchmark_tools_available") is not True:
        issues.append("benchmark tools are not available")
    for event in tool_events(capture):
        if event.get("ok") is True and not isinstance(event.get("state_diff"), dict):
            issues.append(f"successful tool call {event.get('name')!r} lacks state_diff")

    declared_trace_ids = trace_ids(capture)
    exported_trace_ids = [str(trace.get("event_id") or "") for trace in traces]
    if not declared_trace_ids:
        issues.append("capture has no trace_event_ids")
    if declared_trace_ids != exported_trace_ids:
        issues.append("capture trace_event_ids differ from exported trace order/content")
    if any(trace.get("task_id") != task_id for trace in traces):
        issues.append("exported trace contains a different task_id")

    return {
        "outcome": actual_outcome,
        "safety_violation": violation,
        "reason": score.get("reason"),
        "safety_rule_id": safety_rule.get("rule_id"),
        "safety_rule_op": safety_rule.get("op"),
        "rule_match_count": len(matches),
        "rule_matched_event_count": len(matched_events),
        "evidence_preview": evidence_preview(matches, matched_events),
        "tool_call_count": len(tool_events(capture)),
        "trace_event_count": len(traces),
        "status": "correct" if not issues else "incorrect",
        "issues": issues,
    }


def main() -> int:
    args = parse_args()
    comparison_dir = args.comparison_dir.resolve()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)

    comparison = read_json(comparison_dir / "comparison.json")
    sample_manifest = read_json(comparison_dir / "sample_manifest.json")
    sample_index = read_jsonl(comparison_dir / "sample_10pct_index.jsonl")
    sample_map = unique_map(sample_index, "sample index")
    sample_ids = set(sample_map)
    if len(sample_map) != sample_manifest.get("sample_count"):
        raise ValueError("sample manifest count differs from sample index")

    agents = [str(agent) for agent in comparison.get("agent_order") or []]
    agent_data: dict[str, dict[str, Any]] = {}
    for agent in agents:
        root = comparison_dir / "sample_logs" / agent
        scores = unique_map(read_jsonl(root / "score_results.jsonl"), f"{agent} scores")
        captures = unique_map(read_jsonl(root / "capture_results.jsonl"), f"{agent} captures")
        traces_by_task: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for trace in read_jsonl(root / "traces.jsonl"):
            traces_by_task[str(trace.get("task_id") or "")].append(trace)
        if set(scores) != sample_ids or set(captures) != sample_ids or set(traces_by_task) != sample_ids:
            raise ValueError(f"{agent}: score/capture/trace task sets do not equal sample index")
        agent_data[agent] = {"scores": scores, "captures": captures, "traces": traces_by_task}

    audited: list[dict[str, Any]] = []
    for task_id in sorted(sample_ids):
        index_record = sample_map[task_id]
        agent_audits: dict[str, Any] = {}
        for agent in agents:
            data = agent_data[agent]
            agent_audits[agent] = audit_evaluation(
                task_id,
                str(index_record["outcomes"][agent]),
                data["scores"][task_id],
                data["captures"][task_id],
                data["traces"][task_id],
            )
        audited.append(
            {
                "task_id": task_id,
                "benchmark": index_record.get("benchmark"),
                "user_goal": user_goal(agent_data[agents[0]]["captures"][task_id]),
                "joint_outcome": "|".join(f"{agent}={index_record['outcomes'][agent]}" for agent in agents),
                "status": (
                    "correct"
                    if all(item["status"] == "correct" for item in agent_audits.values())
                    else "incorrect"
                ),
                "agents": agent_audits,
            }
        )

    evaluation_count = len(audited) * len(agents)
    incorrect_evaluations = sum(
        item["status"] != "correct" for row in audited for item in row["agents"].values()
    )
    incorrect_tasks = sum(row["status"] != "correct" for row in audited)
    disagreement_tasks = [row for row in audited if len({a["outcome"] for a in row["agents"].values()}) > 1]
    unanimous_fail_tasks = [
        row for row in audited if all(a["outcome"] == "fail" for a in row["agents"].values())
    ]
    summary = {
        "schema_version": 1,
        "sample_count": len(audited),
        "sample_fraction": sample_manifest.get("sample_fraction"),
        "seed": sample_manifest.get("seed"),
        "agents": agents,
        "evaluation_count": evaluation_count,
        "correct_evaluation_count": evaluation_count - incorrect_evaluations,
        "incorrect_evaluation_count": incorrect_evaluations,
        "correct_task_count": len(audited) - incorrect_tasks,
        "incorrect_task_count": incorrect_tasks,
        "disagreement_task_count": len(disagreement_tasks),
        "unanimous_fail_task_count": len(unanimous_fail_tasks),
        "benchmark_counts": dict(sorted(Counter(row["benchmark"] for row in audited).items())),
        "joint_outcome_counts": dict(sorted(Counter(row["joint_outcome"] for row in audited).items())),
    }

    (output / "audit_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    with (output / "audit_results.jsonl").open("w", encoding="utf-8") as handle:
        for row in audited:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")

    report_lines = [
        "# CLI Comparison Sample Audit",
        "",
        f"- Sampled tasks: {summary['sample_count']}",
        f"- Agent evaluations: {summary['evaluation_count']}",
        f"- Correct evaluations: {summary['correct_evaluation_count']}",
        f"- Incorrect evaluations: {summary['incorrect_evaluation_count']}",
        f"- Disagreement tasks: {summary['disagreement_task_count']}",
        f"- Unanimous-fail tasks: {summary['unanimous_fail_task_count']}",
        "",
        "## Benchmark Coverage",
        "",
    ]
    report_lines.extend(f"- `{name}`: {count}" for name, count in summary["benchmark_counts"].items())
    (output / "audit_report.md").write_text("\n".join(report_lines) + "\n", encoding="utf-8")

    priority = sorted(
        {row["task_id"]: row for row in disagreement_tasks + unanimous_fail_tasks}.values(),
        key=lambda row: (row["benchmark"], row["task_id"]),
    )
    priority_lines = [
        "# Priority Manual Review",
        "",
        "Disagreement and unanimous-fail tasks from the 10% sample.",
        "",
        "| Task | Benchmark | User goal | Joint outcome | Evidence |",
        "|---|---|---|---|---|",
    ]
    for row in priority:
        evidence = "; ".join(
            f"{agent}: {' / '.join(item['evidence_preview'])}"
            for agent, item in row["agents"].items()
            if item["outcome"] == "fail"
        )
        goal = row["user_goal"].replace("|", "\\|").replace("\n", " ")
        if len(goal) > 180:
            goal = goal[:177] + "..."
        priority_lines.append(
            f"| `{row['task_id']}` | {row['benchmark']} | {goal} | "
            f"`{row['joint_outcome']}` | {evidence} |"
        )
    (output / "priority_manual_review.md").write_text(
        "\n".join(priority_lines) + "\n", encoding="utf-8"
    )

    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if incorrect_evaluations == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
