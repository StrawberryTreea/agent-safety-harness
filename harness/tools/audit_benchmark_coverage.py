from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import csv
import hashlib
import json
from pathlib import Path
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from agent_safety_harness.adapters import adapter_for_task
from agent_safety_harness.evaluation import validate_evaluation_spec
from agent_safety_harness.io import read_jsonl, write_json, write_jsonl
from agent_safety_harness.schema import TaskSpec


DIMENSIONS = ("profile", "expected_behavior", "safety_category", "attack_present", "tool_count", "tool_risk_set")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build a deterministic all-benchmark coverage audit sample.")
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--out-root", type=Path, required=True)
    parser.add_argument("--seed", type=int, default=20260805)
    parser.add_argument("--min-per-benchmark", type=int, default=3)
    parser.add_argument("--max-categorical-cardinality", type=int, default=64)
    parser.add_argument("--high-cardinality-samples", type=int, default=12)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    records = read_jsonl(args.input)
    by_benchmark: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        by_benchmark[str(record.get("benchmark") or "unknown")].append(record)

    out_root = args.out_root.resolve()
    out_root.mkdir(parents=True, exist_ok=True)
    sampled: list[dict[str, Any]] = []
    matrix: list[dict[str, Any]] = []
    benchmark_reports: dict[str, dict[str, Any]] = {}

    for benchmark, items in sorted(by_benchmark.items()):
        report, selected = audit_benchmark(benchmark, items, args)
        benchmark_reports[benchmark] = report
        sampled.extend(selected)
        for record in selected:
            task = TaskSpec.from_record(record)
            matrix.append(sample_matrix_row(task, report))

    write_jsonl(out_root / "coverage_sample.tasks.jsonl", sampled)
    write_json(out_root / "coverage_manifest.json", build_manifest(args, records, sampled, benchmark_reports))
    write_matrix(out_root / "coverage_matrix.csv", matrix)
    write_report(out_root / "coverage_report.md", args, records, sampled, benchmark_reports)
    print(
        json.dumps(
            {
                "benchmarks": len(by_benchmark),
                "source_records": len(records),
                "sample_records": len(sampled),
                "formal_ready_sample_records": sum(1 for row in matrix if row["formal_ready"]),
                "out_root": str(out_root),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def audit_benchmark(
    benchmark: str,
    records: list[dict[str, Any]],
    args: argparse.Namespace,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    tasks: list[TaskSpec] = []
    schema_errors: list[str] = []
    for record in records:
        try:
            tasks.append(TaskSpec.from_record(record))
        except (TypeError, ValueError) as exc:
            schema_errors.append(f"{record.get('task_id')}: {exc}")

    risk_types = sorted({str(task.labels.get("risk_type") or "<missing>") for task in tasks})
    include_all_risk_types = len(risk_types) <= args.max_categorical_cardinality
    features_by_id = {
        task.task_id: task_features(task, include_risk_type=include_all_risk_types)
        for task in tasks
    }
    universe = set().union(*features_by_id.values()) if features_by_id else set()
    selected_tasks = greedy_cover(tasks, features_by_id, universe, args.seed)

    if not include_all_risk_types:
        represented = {str(task.labels.get("risk_type") or "<missing>") for task in selected_tasks}
        candidates = [task for task in tasks if str(task.labels.get("risk_type") or "<missing>") not in represented]
        candidates.sort(key=lambda task: stable_order(task.task_id, args.seed))
        added_risk_types: set[str] = set()
        for task in candidates:
            risk_type = str(task.labels.get("risk_type") or "<missing>")
            if risk_type in added_risk_types:
                continue
            selected_tasks.append(task)
            added_risk_types.add(risk_type)
            if len(added_risk_types) >= args.high_cardinality_samples:
                break

    selected_ids = {task.task_id for task in selected_tasks}
    if len(selected_tasks) < min(args.min_per_benchmark, len(tasks)):
        remaining = [task for task in tasks if task.task_id not in selected_ids]
        remaining.sort(key=lambda task: stable_order(task.task_id, args.seed))
        selected_tasks.extend(remaining[: args.min_per_benchmark - len(selected_tasks)])

    selected_ids = {task.task_id for task in selected_tasks}
    selected = [record for record in records if str(record.get("task_id")) in selected_ids]
    rule_ready = sum(1 for task in tasks if not rule_errors(task))
    adapter = adapter_for_task(tasks[0]) if tasks else None
    duplicated_asb = sum(
        1
        for task in tasks
        if task.benchmark == "agentlab" and str(task.labels.get("safety_category")) == "agent_safetybench"
    )
    dimensions = dimension_values(tasks)
    report = {
        "source_records": len(records),
        "valid_tasks": len(tasks),
        "schema_error_count": len(schema_errors),
        "schema_errors_preview": schema_errors[:10],
        "selected_records": len(selected),
        "selected_task_ids": [str(record.get("task_id")) for record in selected],
        "dimension_values": dimensions,
        "risk_type_cardinality": len(risk_types),
        "risk_type_full_coverage": include_all_risk_types,
        "selected_risk_types": sorted({str(task.labels.get("risk_type") or "<missing>") for task in selected_tasks}),
        "environment_adapter": adapter.__class__.__name__ if adapter else None,
        "environment_ready": adapter is not None,
        "rule_ready_records": rule_ready,
        "formal_ready_records": rule_ready if adapter is not None else 0,
        "agentlab_embedded_asb_records": duplicated_asb,
    }
    return report, selected


def task_features(task: TaskSpec, *, include_risk_type: bool) -> set[str]:
    labels = task.labels
    values = {
        f"profile={task.profile}",
        f"expected_behavior={labels.get('expected_behavior', '<missing>')}",
        f"safety_category={labels.get('safety_category', '<missing>')}",
        f"attack_present={bool(task.attack_goal)}",
        f"tool_count={tool_count_bucket(len(task.tools))}",
        f"tool_risk_set={','.join(sorted({str(tool.get('risk') or '<missing>') for tool in task.tools})) or '<none>'}",
    }
    if include_risk_type:
        values.add(f"risk_type={labels.get('risk_type', '<missing>')}")
    return values


def greedy_cover(
    tasks: list[TaskSpec],
    features_by_id: dict[str, set[str]],
    universe: set[str],
    seed: int,
) -> list[TaskSpec]:
    remaining = list(tasks)
    uncovered = set(universe)
    selected: list[TaskSpec] = []
    while uncovered and remaining:
        best = max(
            remaining,
            key=lambda task: (len(features_by_id[task.task_id] & uncovered), stable_order(task.task_id, seed)),
        )
        gain = features_by_id[best.task_id] & uncovered
        if not gain:
            break
        selected.append(best)
        uncovered -= gain
        remaining.remove(best)
    return selected


def dimension_values(tasks: list[TaskSpec]) -> dict[str, list[str]]:
    values: dict[str, set[str]] = {dimension: set() for dimension in DIMENSIONS}
    for task in tasks:
        values["profile"].add(task.profile)
        values["expected_behavior"].add(str(task.labels.get("expected_behavior") or "<missing>"))
        values["safety_category"].add(str(task.labels.get("safety_category") or "<missing>"))
        values["attack_present"].add(str(bool(task.attack_goal)))
        values["tool_count"].add(tool_count_bucket(len(task.tools)))
        values["tool_risk_set"].add(
            ",".join(sorted({str(tool.get("risk") or "<missing>") for tool in task.tools})) or "<none>"
        )
    return {key: sorted(items) for key, items in values.items()}


def rule_errors(task: TaskSpec) -> list[str]:
    names = {str(tool.get("name")) for tool in task.tools if tool.get("name")}
    return validate_evaluation_spec(task.evaluation, tool_names=names)


def sample_matrix_row(task: TaskSpec, report: dict[str, Any]) -> dict[str, Any]:
    errors = rule_errors(task)
    blockers = [f"EvaluationRuleUnavailable: {'; '.join(errors)}"] if errors else []
    if not report["environment_ready"]:
        blockers.append("EnvironmentAdapterUnavailable")
    return {
        "benchmark": task.benchmark,
        "task_id": task.task_id,
        "profile": task.profile,
        "expected_behavior": task.labels.get("expected_behavior"),
        "safety_category": task.labels.get("safety_category"),
        "risk_type": task.labels.get("risk_type"),
        "attack_present": bool(task.attack_goal),
        "tool_count": len(task.tools),
        "environment_ready": report["environment_ready"],
        "rule_ready": not errors,
        "formal_ready": report["environment_ready"] and not errors,
        "block_reason": "; ".join(blockers),
    }


def build_manifest(
    args: argparse.Namespace,
    records: list[dict[str, Any]],
    sampled: list[dict[str, Any]],
    reports: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "input": str(args.input.resolve()),
        "seed": args.seed,
        "source_records": len(records),
        "sample_records": len(sampled),
        "benchmark_count": len(reports),
        "coverage_dimensions": list(DIMENSIONS) + ["risk_type_when_cardinality_lte_64"],
        "benchmarks": reports,
    }


def write_matrix(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]) if rows else [])
        if rows:
            writer.writeheader()
            writer.writerows(rows)


def write_report(
    path: Path,
    args: argparse.Namespace,
    records: list[dict[str, Any]],
    sampled: list[dict[str, Any]],
    reports: dict[str, dict[str, Any]],
) -> None:
    schema_valid = sum(report["valid_tasks"] for report in reports.values())
    sampled_tasks = [TaskSpec.from_record(record) for record in sampled]
    rule_blocked = sum(bool(rule_errors(task)) for task in sampled_tasks)
    environment_blocked = sum(
        not reports[task.benchmark]["environment_ready"] for task in sampled_tasks
    )
    formal_ready = sum(
        not rule_errors(task) and reports[task.benchmark]["environment_ready"] for task in sampled_tasks
    )
    agentlab_duplicates = sum(report["agentlab_embedded_asb_records"] for report in reports.values())
    lines = [
        "# 全 Benchmark 覆盖审计",
        "",
        f"- 来源任务：{len(records)}",
        f"- Schema 有效：{schema_valid}/{len(records)}",
        f"- 执行 benchmark：{len(reports)}",
        f"- 分层抽样任务：{len(sampled)}",
        f"- 抽样缺确定性规则：{rule_blocked}",
        f"- 抽样缺官方环境：{environment_blocked}",
        f"- 当前正式就绪：{formal_ready}",
        f"- 随机种子：{args.seed}",
        "",
        "| Benchmark | 全量 | 抽样 | risk_type覆盖 | 环境就绪 | 规则就绪 | 正式就绪 |",
        "|---|---:|---:|---|---|---:|---:|",
    ]
    for benchmark, report in reports.items():
        risk_coverage = "全部" if report["risk_type_full_coverage"] else f"高基数抽样({len(report['selected_risk_types'])})"
        lines.append(
            f"| {benchmark} | {report['source_records']} | {report['selected_records']} | {risk_coverage} | "
            f"{'是' if report['environment_ready'] else '否'} | {report['rule_ready_records']} | {report['formal_ready_records']} |"
        )
    lines.extend(
        [
            "",
            "## 判定",
            "",
            "- 有限类别的 profile、expected_behavior、safety_category、risk_type、攻击状态、工具数量和工具风险均被覆盖。",
            "- AgentLAB 的 risk_type 是高基数自由文本，不能作为稳定分层标签，只抽取代表项并标记为数据质量问题。",
            f"- AgentLAB 中有 {agentlab_duplicates} 条内嵌 Agent-SafetyBench 记录，正式汇总前必须去重。",
            "- 环境或两项规则缺失的样本只做门禁审计，不调用模型，也不产生安全率。",
            "- `coverage_matrix.csv` 给出每个抽样任务的具体阻塞原因。",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def tool_count_bucket(count: int) -> str:
    if count <= 0:
        return "0"
    if count == 1:
        return "1"
    if count == 2:
        return "2"
    return "3+"


def stable_order(task_id: str, seed: int) -> int:
    digest = hashlib.sha256(f"{seed}:{task_id}".encode("utf-8")).hexdigest()
    return int(digest[:16], 16)


if __name__ == "__main__":
    raise SystemExit(main())
