from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from dataclasses import dataclass
import json
import math
from pathlib import Path
import random
from typing import Any, Iterable


@dataclass(frozen=True)
class AgentRun:
    name: str
    root: Path
    scores: list[dict[str, Any]]
    captures: list[dict[str, Any]]


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Compare aligned CLI safety evaluations and export an auditable sample."
    )
    parser.add_argument(
        "--run",
        action="append",
        required=True,
        metavar="NAME=AGENT_RUN_DIR",
        help="Agent directory containing score/ and capture/. Repeat for each CLI.",
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--expected-count", type=int, default=0)
    parser.add_argument("--sample-fraction", type=float, default=0.10)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    if not 0 < args.sample_fraction <= 1:
        parser.error("--sample-fraction must be in (0, 1]")
    if args.output.exists():
        parser.error(f"output already exists: {args.output}")

    runs = [load_run(value) for value in args.run]
    if len({run.name for run in runs}) != len(runs):
        parser.error("agent names must be unique")
    report, aligned_ids = compare(runs, expected_count=args.expected_count)

    target = math.ceil(len(aligned_ids) * args.sample_fraction)
    sampled_ids = stratified_sample(runs, aligned_ids, target=target, seed=args.seed)

    args.output.mkdir(parents=True)
    write_json(args.output / "comparison.json", report)
    (args.output / "comparison.md").write_text(render_markdown(report), encoding="utf-8")
    export_aligned_index(args.output / "aligned_results.jsonl", runs, aligned_ids)
    export_sample(args.output, runs, sampled_ids, args.seed, args.sample_fraction)
    print(
        json.dumps(
            {
                "records": len(aligned_ids),
                "sampled": len(sampled_ids),
                "agents": [run.name for run in runs],
                "output": str(args.output.resolve()),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def load_run(spec: str) -> AgentRun:
    if "=" not in spec:
        raise ValueError(f"invalid --run value: {spec}")
    name, raw_path = spec.split("=", 1)
    root = Path(raw_path).resolve()
    score_path = root / "score" / "results.jsonl"
    capture_path = root / "capture" / "capture_results.jsonl"
    if not score_path.is_file() or not capture_path.is_file():
        raise FileNotFoundError(f"missing score or capture results under {root}")
    return AgentRun(name.strip(), root, read_jsonl(score_path), read_jsonl(capture_path))


def compare(runs: list[AgentRun], *, expected_count: int = 0) -> tuple[dict[str, Any], list[str]]:
    if len(runs) < 2:
        raise ValueError("at least two runs are required")

    score_maps: dict[str, dict[str, dict[str, Any]]] = {}
    capture_maps: dict[str, dict[str, dict[str, Any]]] = {}
    for run in runs:
        score_maps[run.name] = unique_map(run.scores, f"{run.name} scores")
        capture_maps[run.name] = unique_map(run.captures, f"{run.name} captures")
        if set(score_maps[run.name]) != set(capture_maps[run.name]):
            raise ValueError(f"{run.name}: score and capture task_id sets differ")

    reference_name = runs[0].name
    reference_ids = set(score_maps[reference_name])
    for run in runs[1:]:
        if set(score_maps[run.name]) != reference_ids:
            missing = sorted(reference_ids - set(score_maps[run.name]))[:10]
            extra = sorted(set(score_maps[run.name]) - reference_ids)[:10]
            raise ValueError(f"{run.name}: task_id set mismatch; missing={missing}, extra={extra}")
    if expected_count and len(reference_ids) != expected_count:
        raise ValueError(f"expected {expected_count} tasks, found {len(reference_ids)}")

    aligned_ids = sorted(reference_ids)
    agents: dict[str, Any] = {}
    for run in runs:
        agent_scores = score_maps[run.name]
        outcomes = [outcome(agent_scores[task_id]) for task_id in aligned_ids]
        unknown = sum(value == "unknown" for value in outcomes)
        evaluated = len(outcomes) - unknown
        pass_count = sum(value == "pass" for value in outcomes)
        health = capture_health([capture_maps[run.name][task_id] for task_id in aligned_ids])
        if health["invalid_capture_count"]:
            raise ValueError(f"{run.name}: {health['invalid_capture_count']} invalid captures")
        if health["calls_missing_state_diff_count"]:
            raise ValueError(
                f"{run.name}: {health['calls_missing_state_diff_count']} successful tool calls "
                "are missing state_diff"
            )
        agents[run.name] = {
            "records": len(aligned_ids),
            "evaluated_count": evaluated,
            "pass_count": pass_count,
            "fail_count": sum(value == "fail" for value in outcomes),
            "unknown_count": unknown,
            "run_valid": unknown == 0,
            "safety_pass_rate": rate(pass_count, evaluated) if unknown == 0 else None,
            "provisional_safety_pass_rate": rate(pass_count, evaluated),
            "by_benchmark": grouped_metrics(agent_scores, aligned_ids, "benchmark"),
            "by_profile": grouped_metrics(agent_scores, aligned_ids, "profile"),
            "capture_health": health,
        }

    patterns = Counter(
        "|".join(f"{run.name}={outcome(score_maps[run.name][task_id])}" for run in runs)
        for task_id in aligned_ids
    )
    pairwise: dict[str, Any] = {}
    for index, left in enumerate(runs):
        for right in runs[index + 1 :]:
            agree = sum(
                outcome(score_maps[left.name][task_id]) == outcome(score_maps[right.name][task_id])
                for task_id in aligned_ids
            )
            pairwise[f"{left.name}__{right.name}"] = {
                "agreement_count": agree,
                "disagreement_count": len(aligned_ids) - agree,
                "agreement_rate": rate(agree, len(aligned_ids)),
            }

    return (
        {
            "schema_version": 2,
            "task_count": len(aligned_ids),
            "task_id_sets_aligned": True,
            "agent_order": [run.name for run in runs],
            "agents": agents,
            "outcome_patterns": dict(sorted(patterns.items())),
            "pairwise": pairwise,
        },
        aligned_ids,
    )


def stratified_sample(
    runs: list[AgentRun], task_ids: list[str], *, target: int, seed: int
) -> list[str]:
    score_maps = {run.name: unique_map(run.scores, run.name) for run in runs}
    strata: dict[tuple[str, str], list[str]] = defaultdict(list)
    for task_id in task_ids:
        first = score_maps[runs[0].name][task_id]
        benchmark = str(first.get("benchmark") or "unknown")
        pattern = "|".join(outcome(score_maps[run.name][task_id]) for run in runs)
        strata[(benchmark, pattern)].append(task_id)

    quotas = proportional_quotas({key: len(ids) for key, ids in strata.items()}, target)
    rng = random.Random(seed)
    selected: list[str] = []
    for key in sorted(strata):
        ids = sorted(strata[key])
        rng.shuffle(ids)
        selected.extend(ids[: quotas[key]])
    return sorted(selected)


def proportional_quotas(sizes: dict[Any, int], target: int) -> dict[Any, int]:
    total = sum(sizes.values())
    if target < 0 or target > total:
        raise ValueError("sample target is outside the population")
    nonempty = {key: size for key, size in sizes.items() if size > 0}
    guarantee_coverage = target >= len(nonempty)
    quotas = {key: (1 if guarantee_coverage and size > 0 else 0) for key, size in sizes.items()}
    remaining = target - sum(quotas.values())
    capacities = {key: size - quotas[key] for key, size in sizes.items()}
    capacity_total = sum(capacities.values())
    raw = {
        key: (remaining * capacity / capacity_total if capacity_total else 0.0)
        for key, capacity in capacities.items()
    }
    for key, capacity in capacities.items():
        quotas[key] += min(capacity, int(math.floor(raw[key])))
    remaining = target - sum(quotas.values())
    order = sorted(
        sizes,
        key=lambda key: (
            raw[key] - math.floor(raw[key]),
            capacities[key],
            str(key),
        ),
        reverse=True,
    )
    for key in order:
        if not remaining:
            break
        if quotas[key] < sizes[key]:
            quotas[key] += 1
            remaining -= 1
    if remaining:
        raise ValueError("could not allocate the requested sample")
    return quotas


def grouped_metrics(
    records: dict[str, dict[str, Any]], task_ids: list[str], field: str
) -> dict[str, Any]:
    buckets: dict[str, list[str]] = defaultdict(list)
    for task_id in task_ids:
        record = records[task_id]
        if field == "profile":
            capture = ((record.get("evidence") or {}).get("capture_evidence") or {})
            task_record = capture.get("task_record") or {}
            value = task_record.get("profile")
        else:
            value = record.get(field)
        buckets[str(value or "unknown")].append(outcome(record))
    return {
        key: {
            "records": len(values),
            "evaluated_count": sum(value != "unknown" for value in values),
            "pass_count": sum(value == "pass" for value in values),
            "fail_count": sum(value == "fail" for value in values),
            "unknown_count": sum(value == "unknown" for value in values),
            "run_valid": all(value != "unknown" for value in values),
            "safety_pass_rate": (
                rate(sum(value == "pass" for value in values), len(values))
                if all(value != "unknown" for value in values)
                else None
            ),
            "provisional_safety_pass_rate": rate(
                sum(value == "pass" for value in values),
                sum(value != "unknown" for value in values),
            ),
        }
        for key, values in sorted(buckets.items())
    }


def capture_health(records: list[dict[str, Any]]) -> dict[str, Any]:
    invalid = 0
    zero_calls = 0
    zero_successful = 0
    limit_hits = 0
    missing_diffs = 0
    total_calls = 0
    for record in records:
        evidence = record.get("evidence") or {}
        artifacts = evidence.get("artifacts") or {}
        events = artifacts.get("benchmark_mcp_events") or []
        calls = [event for event in events if event.get("event_type") == "tool_call"]
        successful = [event for event in calls if event.get("ok") is True]
        total_calls += len(calls)
        if not calls:
            zero_calls += 1
        if not successful:
            zero_successful += 1
        if any("20 tool-call" in str(event.get("error") or "") for event in calls):
            limit_hits += 1
        missing_diffs += sum(
            not isinstance(event.get("state_diff"), dict) for event in successful
        )
        valid = (
            artifacts.get("returncode") == 0
            and artifacts.get("benchmark_environment_validated") is True
            and artifacts.get("benchmark_tools_available") is True
        )
        invalid += not valid
    return {
        "valid_capture_count": len(records) - invalid,
        "invalid_capture_count": invalid,
        "tool_call_count": total_calls,
        "zero_tool_call_count": zero_calls,
        "zero_successful_tool_call_count": zero_successful,
        "tool_limit_hit_count": limit_hits,
        "calls_missing_state_diff_count": missing_diffs,
    }


def export_aligned_index(path: Path, runs: list[AgentRun], task_ids: list[str]) -> None:
    score_maps = {run.name: unique_map(run.scores, run.name) for run in runs}
    rows = []
    for task_id in task_ids:
        first = score_maps[runs[0].name][task_id]
        rows.append(
            {
                "task_id": task_id,
                "benchmark": first.get("benchmark"),
                "agents": {
                    run.name: {
                        "outcome": outcome(score_maps[run.name][task_id]),
                        "reason": score_maps[run.name][task_id].get("reason"),
                    }
                    for run in runs
                },
            }
        )
    write_jsonl(path, rows)


def export_sample(
    output: Path,
    runs: list[AgentRun],
    sampled_ids: list[str],
    seed: int,
    fraction: float,
) -> None:
    selected = set(sampled_ids)
    score_maps = {run.name: unique_map(run.scores, run.name) for run in runs}
    capture_maps = {run.name: unique_map(run.captures, run.name) for run in runs}
    index_rows = []
    for task_id in sampled_ids:
        first = score_maps[runs[0].name][task_id]
        index_rows.append(
            {
                "task_id": task_id,
                "benchmark": first.get("benchmark"),
                "outcomes": {run.name: outcome(score_maps[run.name][task_id]) for run in runs},
            }
        )
    write_jsonl(output / "sample_10pct_index.jsonl", index_rows)
    write_json(
        output / "sample_manifest.json",
        {
            "seed": seed,
            "sample_fraction": fraction,
            "sample_count": len(sampled_ids),
            "stratification": "benchmark_x_joint_outcome_with_nonempty_stratum_coverage",
            "task_ids": sampled_ids,
        },
    )

    for run in runs:
        agent_output = output / "sample_logs" / run.name
        agent_output.mkdir(parents=True)
        write_jsonl(agent_output / "score_results.jsonl", [score_maps[run.name][task_id] for task_id in sampled_ids])
        write_jsonl(
            agent_output / "capture_results.jsonl",
            [capture_maps[run.name][task_id] for task_id in sampled_ids],
        )
        filter_jsonl(run.root / "capture" / "traces.jsonl", agent_output / "traces.jsonl", selected)


def filter_jsonl(source: Path, destination: Path, task_ids: set[str]) -> None:
    with source.open("r", encoding="utf-8-sig") as input_handle, destination.open(
        "w", encoding="utf-8", newline="\n"
    ) as output_handle:
        for line in input_handle:
            if not line.strip():
                continue
            record = json.loads(line)
            if str(record.get("task_id") or "") in task_ids:
                output_handle.write(json.dumps(record, ensure_ascii=False) + "\n")


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


def rate(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open("r", encoding="utf-8-sig") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Parallel CLI Safety Comparison",
        "",
        f"- Aligned tasks: {report['task_count']}",
        f"- Task ID sets aligned: {report['task_id_sets_aligned']}",
        "",
        "## Overall",
        "",
        "| Agent | Pass | Fail | Unknown | Official rate | Provisional rate | Run valid | Invalid captures |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for name in report["agent_order"]:
        item = report["agents"][name]
        lines.append(
            f"| {name} | {item['pass_count']} | {item['fail_count']} | {item['unknown_count']} | "
            f"{format_rate(item['safety_pass_rate'])} | "
            f"{format_rate(item['provisional_safety_pass_rate'])} | "
            f"{item['run_valid']} | {item['capture_health']['invalid_capture_count']} |"
        )
    lines.extend(["", "## Pairwise Agreement", ""])
    for pair, item in report["pairwise"].items():
        lines.append(
            f"- `{pair}`: {item['agreement_rate']:.2%} "
            f"({item['agreement_count']} agree, {item['disagreement_count']} disagree)"
        )
    lines.append("")
    return "\n".join(lines)


def format_rate(value: Any) -> str:
    return f"{value:.2%}" if isinstance(value, (int, float)) else "not published"


if __name__ == "__main__":
    raise SystemExit(main())
