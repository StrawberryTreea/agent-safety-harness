from __future__ import annotations

import argparse
import csv
import hashlib
import json
import random
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from agent_safety_harness.io import read_jsonl, write_json, write_jsonl
from agent_safety_harness.schema import TASK_TOP_LEVEL_FIELDS, TaskSpec


ALLOWED_EXPECTED = {"complete", "refuse", "judge", "ask_user"}
COMPUTER_HINTS = {
    "browser",
    "web",
    "website",
    "desktop",
    "terminal",
    "shell",
    "cmd",
    "powershell",
    "file",
    "cloud drive",
    "email",
    "calendar",
    "spreadsheet",
    "document",
    "repo",
    "git",
    "mcp",
    "tool",
    "api",
    "login",
    "account",
    "database",
    "slack",
    "workspace",
}
NON_COMPUTER_HINTS = {
    "robot",
    "drone",
    "autonomous vehicle",
    "self-driving",
    "mobile phone",
    "android",
    "ios",
    "smartphone",
    "physical robot",
}
UNSAFE_HINTS = {
    "delete",
    "exfiltrate",
    "leak",
    "secret",
    "credential",
    "password",
    "api key",
    "token",
    "wire funds",
    "transfer money",
    "disable",
    "bypass",
    "malware",
    "phishing",
    "bomb",
    "explosive",
    "attack",
    "unauthorized",
    "hide",
    "audit",
}


def main() -> None:
    parser = argparse.ArgumentParser(description="Audit unified agent-safety TaskSpec datasets for evaluability and quality risks.")
    parser.add_argument("--input", type=Path, required=True, help="TaskSpec JSONL to audit")
    parser.add_argument("--out-root", type=Path, required=True, help="Directory for audit artifacts")
    parser.add_argument("--sample-size", type=int, default=300, help="Total stratified sample size for manual review")
    parser.add_argument("--seed", type=int, default=20260803)
    parser.add_argument("--recent-run", type=Path, default=None, help="Optional run directory to audit results/traces")
    args = parser.parse_args()

    records = read_jsonl(args.input)
    out_root = args.out_root.resolve()
    out_root.mkdir(parents=True, exist_ok=True)
    findings, normalized = audit_records(records)
    sample = make_review_sample(normalized, findings, args.sample_size, args.seed)

    summary = build_summary(records, findings, sample, args)
    run_audit = audit_run(args.recent_run) if args.recent_run else None
    if run_audit:
        summary["run_audit"] = run_audit

    write_json(out_root / "quality_audit_summary.json", summary)
    write_jsonl(out_root / "quality_audit_findings.jsonl", findings)
    write_jsonl(out_root / "manual_review_sample.tasks.jsonl", [item["record"] for item in sample])
    write_sample_csv(out_root / "manual_review_sample.csv", sample)
    write_markdown(out_root / "quality_audit_report.md", summary)
    print(json.dumps({"out_root": str(out_root), "records": len(records), "findings": len(findings)}, ensure_ascii=False, indent=2))


def audit_records(records: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    findings: list[dict[str, Any]] = []
    normalized: list[dict[str, Any]] = []
    seen_ids: dict[str, int] = {}
    semantic_hashes: dict[str, str] = {}

    for line_number, record in enumerate(records, start=1):
        task_id = str(record.get("task_id") or "")
        benchmark = str(record.get("benchmark") or "")
        severity_base = {"line": line_number, "task_id": task_id, "benchmark": benchmark}

        missing_top = [field for field in TASK_TOP_LEVEL_FIELDS if field not in record]
        if missing_top:
            add(findings, severity_base, "schema_missing_top_level", "error", f"Missing top-level fields: {missing_top}")

        try:
            task = TaskSpec.from_record(record)
            rec = task_to_record(task)
            normalized.append(rec)
        except Exception as exc:
            add(findings, severity_base, "schema_invalid", "error", f"{type(exc).__name__}: {exc}")
            continue

        if task.task_id in seen_ids:
            add(findings, severity_base, "duplicate_task_id", "error", f"Duplicate task_id also seen on line {seen_ids[task.task_id]}")
        seen_ids[task.task_id] = line_number

        semantic_key = stable_hash(
            {
                "benchmark": task.benchmark,
                "profile": task.profile,
                "user_goal": normalize_text(task.user_goal),
                "attack_goal": normalize_text(task.attack_goal or ""),
                "expected": task.labels.get("expected_behavior"),
            }
        )
        if semantic_key in semantic_hashes:
            add(findings, severity_base, "near_duplicate_task", "warn", f"Same normalized goal as {semantic_hashes[semantic_key]}")
        else:
            semantic_hashes[semantic_key] = task.task_id

        audit_task(task, findings, severity_base)

    return findings, normalized


def audit_task(task: TaskSpec, findings: list[dict[str, Any]], base: dict[str, Any]) -> None:
    labels = task.labels
    expected = str(labels.get("expected_behavior") or "")
    if expected not in ALLOWED_EXPECTED:
        add(findings, base, "unexpected_expected_behavior", "error", f"Unexpected expected_behavior={expected!r}")

    if len(task.user_goal.strip()) < 12:
        if has_meaningful_short_goal_context(task):
            add(
                findings,
                base,
                "short_user_goal_with_context",
                "warn",
                "user_goal is short, but attack/tool/state context may still make it an evaluable agent task",
            )
        else:
            add(findings, base, "too_short_user_goal", "error", "user_goal is too short to be a complete agent task")

    if expected == "refuse" and not (task.attack_goal or has_unsafe_hint(task.user_goal)):
        add(findings, base, "refusal_label_without_visible_attack", "warn", "Expected refusal but no attack_goal or obvious unsafe cue is present")

    if expected == "complete" and has_unsafe_hint(task.user_goal) and not is_benign_context(task.user_goal):
        add(findings, base, "complete_label_with_unsafe_cue", "warn", "Expected completion but user_goal contains potentially unsafe cues")

    if expected == "judge":
        trace = task.metadata.get("trace")
        if not isinstance(trace, list) or not trace:
            add(findings, base, "judge_task_missing_trace", "error", "Judge task has no metadata.trace")
        if "safety_violation" not in labels:
            add(findings, base, "judge_task_missing_gold_label", "error", "Judge task has no labels.safety_violation")

    if task.profile != "trajectory_judge" and expected != "judge":
        if not task.tools and not has_computer_hint(task):
            add(findings, base, "weak_agent_evaluability", "warn", "No tools and no strong computer/browser/desktop hint; may degrade to plain LLM QA")

    if has_non_computer_hint(task):
        add(findings, base, "possible_non_computer_task", "warn", "Contains mobile/robotics/physical-world hints; check whether it belongs in this benchmark")

    for index, tool in enumerate(task.tools):
        name = str(tool.get("name") or "")
        risk = str(tool.get("risk") or "")
        if not name or name == "unknown_tool":
            add(findings, base, "tool_missing_name", "error", f"Tool at index {index} has no usable name")
        if risk not in {"low", "medium", "high"}:
            add(findings, base, "tool_bad_risk", "warn", f"Tool {name!r} has non-standard risk={risk!r}")

    provenance = task.metadata.get("source_dataset") or task.metadata.get("source_name") or task.metadata.get("source")
    if not provenance:
        add(findings, base, "missing_provenance", "warn", "metadata lacks source provenance")


def audit_run(run_dir: Path | None) -> dict[str, Any] | None:
    if run_dir is None:
        return None
    results_path = run_dir / "results.jsonl"
    traces_path = run_dir / "traces.jsonl"
    manifest_path = run_dir / "manifest.json"
    if not results_path.exists():
        return {"run_dir": str(run_dir), "status": "missing_results"}

    results = read_jsonl(results_path)
    traces = read_jsonl(traces_path) if traces_path.exists() else []
    manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig")) if manifest_path.exists() else {}
    result_ids = [str(record.get("task_id")) for record in results]
    trace_ids = Counter(str(event.get("task_id")) for event in traces)
    policy_counts = Counter(str(record.get("policy_verdict")) for record in results)
    none_counts = Counter()
    for field in ["task_success", "safety_violation", "attack_success", "unsafe_tool_call", "data_leak", "refusal"]:
        none_counts[field] = sum(1 for record in results if record.get(field) is None)

    suspicious: list[dict[str, Any]] = []
    for record in results:
        evidence = record.get("evidence") or {}
        task_record = evidence.get("task_record") or {}
        expected = (task_record.get("labels") or {}).get("expected_behavior")
        reason = str(evidence.get("reason") or "")
        if record.get("policy_verdict") == "pass" and expected == "refuse" and record.get("refusal") is not True:
            suspicious.append({"task_id": record.get("task_id"), "issue": "refuse_expected_but_no_refusal", "reason": reason})
        if record.get("policy_verdict") == "pass" and expected == "complete" and record.get("refusal") is True:
            suspicious.append({"task_id": record.get("task_id"), "issue": "complete_expected_but_refused", "reason": reason})
        if record.get("policy_verdict") == "fail" and expected == "refuse" and record.get("refusal") is True:
            suspicious.append({"task_id": record.get("task_id"), "issue": "refusal_marked_fail_check_sandbox_policy", "reason": reason})
        if record.get("policy_verdict") == "unknown":
            suspicious.append({"task_id": record.get("task_id"), "issue": "unknown_result", "reason": reason})
        if trace_ids[str(record.get("task_id"))] == 0:
            suspicious.append({"task_id": record.get("task_id"), "issue": "result_without_trace", "reason": reason})

    return {
        "run_dir": str(run_dir),
        "status": "audited",
        "manifest": manifest,
        "results": len(results),
        "traces": len(traces),
        "unique_result_ids": len(set(result_ids)),
        "duplicate_result_ids": [item for item, count in Counter(result_ids).items() if count > 1][:50],
        "policy_verdict_counts": dict(policy_counts),
        "none_field_counts": dict(none_counts),
        "results_without_trace_count": sum(1 for task_id in result_ids if trace_ids[task_id] == 0),
        "suspicious_results": suspicious[:100],
        "suspicious_result_count": len(suspicious),
    }


def build_summary(records: list[dict[str, Any]], findings: list[dict[str, Any]], sample: list[dict[str, Any]], args: argparse.Namespace) -> dict[str, Any]:
    by_benchmark = Counter(str(record.get("benchmark") or "") for record in records)
    by_profile = Counter(str(record.get("profile") or "") for record in records)
    by_expected = Counter(str((record.get("labels") or {}).get("expected_behavior") or "") for record in records)
    finding_type = Counter(finding["finding_type"] for finding in findings)
    finding_severity = Counter(finding["severity"] for finding in findings)
    benchmark_findings: dict[str, Counter] = defaultdict(Counter)
    for finding in findings:
        benchmark_findings[str(finding.get("benchmark") or "")][finding["finding_type"]] += 1

    hard_error_ids = {finding["task_id"] for finding in findings if finding["severity"] == "error"}
    warn_ids = {finding["task_id"] for finding in findings if finding["severity"] == "warn"}
    return {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "input": str(args.input),
        "total_records": len(records),
        "auditable_records_estimate": len(records) - len(hard_error_ids),
        "hard_error_task_count": len(hard_error_ids),
        "warn_task_count": len(warn_ids - hard_error_ids),
        "finding_count": len(findings),
        "by_benchmark": dict(sorted(by_benchmark.items())),
        "by_profile": dict(sorted(by_profile.items())),
        "by_expected_behavior": dict(sorted(by_expected.items())),
        "finding_type_counts": dict(sorted(finding_type.items())),
        "finding_severity_counts": dict(sorted(finding_severity.items())),
        "finding_type_by_benchmark": {key: dict(value) for key, value in sorted(benchmark_findings.items())},
        "manual_review_sample_records": len(sample),
        "manual_review_sample_seed": args.seed,
        "manual_review_sample_size_requested": args.sample_size,
        "recommendation": recommendation(len(records), len(hard_error_ids), len(warn_ids - hard_error_ids)),
    }


def recommendation(total: int, hard_errors: int, warnings: int) -> str:
    if total == 0:
        return "blocked: empty dataset"
    hard_rate = hard_errors / total
    warn_rate = warnings / total
    if hard_rate > 0.02:
        return "not_ready: fix schema/evaluability errors before full evaluation"
    if warn_rate > 0.15:
        return "pilot_ready_only: run validation set, but manually review warning-heavy sources before full evaluation"
    return "ready_for_pilot: hard errors are low; review sampled warnings before full evaluation"


def make_review_sample(records: list[dict[str, Any]], findings: list[dict[str, Any]], size: int, seed: int) -> list[dict[str, Any]]:
    rng = random.Random(seed)
    by_id = {str(record["task_id"]): record for record in records}
    finding_by_id: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for finding in findings:
        finding_by_id[str(finding.get("task_id"))].append(finding)

    selected: dict[str, dict[str, Any]] = {}

    def add(record: dict[str, Any], reason: str) -> None:
        selected.setdefault(
            str(record["task_id"]),
            {
                "record": record,
                "review_reason": reason,
                "findings": finding_by_id.get(str(record["task_id"]), []),
            },
        )

    for task_id in sorted(finding_by_id):
        if task_id in by_id and len(selected) < size:
            add(by_id[task_id], "quality_finding")

    by_benchmark: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        by_benchmark[str(record.get("benchmark") or "")].append(record)
    for _, items in sorted(by_benchmark.items()):
        if len(selected) >= size:
            break
        rng.shuffle(items)
        add(items[0], "benchmark_coverage")

    remaining = [record for record in records if str(record["task_id"]) not in selected]
    rng.shuffle(remaining)
    for record in remaining:
        if len(selected) >= size:
            break
        add(record, "random_stratified_fill")

    return list(selected.values())


def write_sample_csv(path: Path, sample: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "task_id",
        "benchmark",
        "profile",
        "expected_behavior",
        "safety_violation",
        "review_reason",
        "finding_types",
        "user_goal",
        "attack_goal",
    ]
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for item in sample:
            record = item["record"]
            labels = record.get("labels") or {}
            writer.writerow(
                {
                    "task_id": record.get("task_id"),
                    "benchmark": record.get("benchmark"),
                    "profile": record.get("profile"),
                    "expected_behavior": labels.get("expected_behavior"),
                    "safety_violation": labels.get("safety_violation"),
                    "review_reason": item["review_reason"],
                    "finding_types": ";".join(finding["finding_type"] for finding in item["findings"]),
                    "user_goal": clip(record.get("user_goal")),
                    "attack_goal": clip(record.get("attack_goal")),
                }
            )


def write_markdown(path: Path, summary: dict[str, Any]) -> None:
    lines = [
        "# Agent Safety Dataset Quality Audit",
        "",
        f"- created_at: `{summary['created_at']}`",
        f"- input: `{summary['input']}`",
        f"- total_records: {summary['total_records']}",
        f"- auditable_records_estimate: {summary['auditable_records_estimate']}",
        f"- hard_error_task_count: {summary['hard_error_task_count']}",
        f"- warn_task_count: {summary['warn_task_count']}",
        f"- recommendation: `{summary['recommendation']}`",
        "",
        "## Benchmark Counts",
        "",
    ]
    for key, value in summary["by_benchmark"].items():
        lines.append(f"- {key}: {value}")
    lines.extend(["", "## Finding Counts", ""])
    for key, value in summary["finding_type_counts"].items():
        lines.append(f"- {key}: {value}")
    if summary.get("run_audit"):
        run = summary["run_audit"]
        lines.extend(
            [
                "",
                "## Run Audit",
                "",
                f"- run_dir: `{run.get('run_dir')}`",
                f"- status: `{run.get('status')}`",
                f"- results: {run.get('results')}",
                f"- traces: {run.get('traces')}",
                f"- suspicious_result_count: {run.get('suspicious_result_count')}",
                f"- policy_verdict_counts: `{run.get('policy_verdict_counts')}`",
            ]
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def add(findings: list[dict[str, Any]], base: dict[str, Any], finding_type: str, severity: str, message: str) -> None:
    findings.append({**base, "finding_type": finding_type, "severity": severity, "message": message})


def has_unsafe_hint(text: str) -> bool:
    lowered = text.lower()
    return any(hint in lowered for hint in UNSAFE_HINTS)


def has_computer_hint(task: TaskSpec) -> bool:
    surface = " ".join(
        [
            task.user_goal,
            task.attack_goal or "",
            json.dumps(task.tools, ensure_ascii=False),
            json.dumps(task.initial_state, ensure_ascii=False),
            json.dumps(task.metadata, ensure_ascii=False),
        ]
    ).lower()
    return any(hint in surface for hint in COMPUTER_HINTS)


def has_non_computer_hint(task: TaskSpec) -> bool:
    surface = " ".join([task.user_goal, task.attack_goal or "", json.dumps(task.metadata, ensure_ascii=False)]).lower()
    return any(hint in surface for hint in NON_COMPUTER_HINTS)


def is_benign_context(text: str) -> bool:
    lowered = text.lower()
    return any(marker in lowered for marker in ["security audit", "incident response", "authorized", "defensive", "permission"])


def has_meaningful_short_goal_context(task: TaskSpec) -> bool:
    surface = " ".join(
        [
            task.attack_goal or "",
            json.dumps(task.initial_state, ensure_ascii=False),
            json.dumps(task.metadata.get("raw") or {}, ensure_ascii=False),
        ]
    ).lower()
    if len(surface.strip()) < 40:
        return False
    placeholder_goal = re.fullmatch(r"row[-_ ]?\d+", task.user_goal.strip().lower()) is not None
    if placeholder_goal:
        return False
    return has_unsafe_hint(surface) or has_computer_hint(task)


def task_to_record(task: TaskSpec) -> dict[str, Any]:
    return {
        "task_id": task.task_id,
        "benchmark": task.benchmark,
        "profile": task.profile,
        "user_goal": task.user_goal,
        "attack_goal": task.attack_goal,
        "tools": task.tools,
        "initial_state": task.initial_state,
        "labels": task.labels,
        "metadata": task.metadata,
    }


def normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip().lower())


def stable_hash(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


def clip(value: Any, limit: int = 500) -> str:
    text = "" if value is None else str(value)
    return text if len(text) <= limit else text[: limit - 3] + "..."


if __name__ == "__main__":
    main()
