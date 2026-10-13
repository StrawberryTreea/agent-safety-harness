from __future__ import annotations

import argparse
import json
import random
import shutil
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]


def display_path(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(ROOT))
    except ValueError:
        return str(path)


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8-sig") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise ValueError(f"Invalid JSONL at {path}:{line_number}: {exc}") from exc
    return records


def write_json(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")


def write_jsonl(path: Path, records: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True))
            handle.write("\n")


def task_group(record: dict[str, Any]) -> tuple[str, str, str, str, str]:
    labels = record.get("labels") or {}
    return (
        str(record.get("benchmark", "")),
        str(record.get("profile", "")),
        str(labels.get("expected_behavior", "")),
        str(labels.get("safety_category", "")),
        str(labels.get("risk_type", "")),
    )


def is_judge(record: dict[str, Any]) -> bool:
    return record.get("profile") == "trajectory_judge" or record.get("benchmark") == "r_judge"


def stratified_validation_sample(records: list[dict[str, Any]], size: int, seed: int) -> list[dict[str, Any]]:
    if size > len(records):
        raise ValueError(f"Requested validation size {size} exceeds dataset size {len(records)}")

    rng = random.Random(seed)
    selected_by_id: dict[str, dict[str, Any]] = {}

    def add(record: dict[str, Any]) -> None:
        if len(selected_by_id) < size:
            selected_by_id.setdefault(str(record["task_id"]), record)

    by_benchmark: dict[str, list[dict[str, Any]]] = defaultdict(list)
    by_benchmark_profile: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    by_group: dict[tuple[str, str, str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        by_benchmark[str(record.get("benchmark", ""))].append(record)
        by_benchmark_profile[(str(record.get("benchmark", "")), str(record.get("profile", "")))].append(record)
        by_group[task_group(record)].append(record)

    for group in sorted(by_benchmark_profile):
        items = list(by_benchmark_profile[group])
        rng.shuffle(items)
        add(items[0])

    for benchmark in sorted(by_benchmark):
        items = list(by_benchmark[benchmark])
        rng.shuffle(items)
        add(items[0])

    if not any(not is_judge(record) for record in selected_by_id.values()):
        add(next(record for record in records if not is_judge(record)))
    if not any(is_judge(record) for record in selected_by_id.values()):
        add(next(record for record in records if is_judge(record)))

    groups = list(by_group.items())
    rng.shuffle(groups)
    groups.sort(key=lambda item: len(item[1]), reverse=True)
    cursor = 0
    while len(selected_by_id) < size and groups:
        group, items = groups[cursor % len(groups)]
        unused = [record for record in items if str(record["task_id"]) not in selected_by_id]
        if unused:
            rng.shuffle(unused)
            add(unused[0])
        cursor += 1
        if cursor > len(groups) * 3 and len(selected_by_id) < size:
            break

    remaining = [record for record in records if str(record["task_id"]) not in selected_by_id]
    rng.shuffle(remaining)
    for record in remaining:
        if len(selected_by_id) >= size:
            break
        add(record)

    selected = list(selected_by_id.values())
    selected.sort(key=lambda record: (str(record.get("benchmark")), str(record.get("profile")), str(record.get("task_id"))))
    return selected


def validate_records(records: list[dict[str, Any]]) -> dict[str, Any]:
    required = {"task_id", "benchmark", "profile", "user_goal", "attack_goal", "tools", "initial_state", "labels", "metadata"}
    ids: set[str] = set()
    duplicates: list[str] = []
    missing: list[dict[str, Any]] = []
    for index, record in enumerate(records, start=1):
        absent = sorted(required - set(record))
        labels = record.get("labels") if isinstance(record.get("labels"), dict) else {}
        if "expected_behavior" not in labels:
            absent.append("labels.expected_behavior")
        if absent:
            missing.append({"line": index, "task_id": record.get("task_id"), "missing": absent})
        task_id = str(record.get("task_id", ""))
        if task_id in ids:
            duplicates.append(task_id)
        ids.add(task_id)
    return {
        "valid": not duplicates and not missing,
        "duplicate_task_ids": duplicates,
        "missing_required_fields": missing[:20],
        "missing_required_field_count": len(missing),
    }


def manifest(records: list[dict[str, Any]], *, source: str, seed: int | None = None) -> dict[str, Any]:
    by_benchmark = Counter(str(record.get("benchmark", "")) for record in records)
    by_profile = Counter(str(record.get("profile", "")) for record in records)
    by_expected = Counter(str((record.get("labels") or {}).get("expected_behavior", "")) for record in records)
    by_category = Counter(str((record.get("labels") or {}).get("safety_category", "")) for record in records)
    by_group = Counter(f"{record.get('benchmark')}::{record.get('profile')}" for record in records)
    return {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source": source,
        "seed": seed,
        "total_records": len(records),
        "execution_records": sum(1 for record in records if not is_judge(record)),
        "judge_records": sum(1 for record in records if is_judge(record)),
        "by_benchmark": dict(sorted(by_benchmark.items())),
        "by_profile": dict(sorted(by_profile.items())),
        "by_expected_behavior": dict(sorted(by_expected.items())),
        "by_safety_category": dict(sorted(by_category.items())),
        "by_benchmark_profile": dict(sorted(by_group.items())),
        "validation": validate_records(records),
    }


def write_split(root: Path, records: list[dict[str, Any]], *, source: str, seed: int | None) -> None:
    root.mkdir(parents=True, exist_ok=True)
    execution = [record for record in records if not is_judge(record)]
    judge = [record for record in records if is_judge(record)]
    write_jsonl(root / "all_computer_agent_safety.tasks.jsonl", records)
    write_jsonl(root / "agent_execution_computer_safety.tasks.jsonl", execution)
    write_jsonl(root / "trace_judge_computer_safety.tasks.jsonl", judge)
    write_json(root / "manifest.json", manifest(records, source=source, seed=seed))


def main() -> None:
    parser = argparse.ArgumentParser(description="Package validation and full TaskSpec datasets for release.")
    parser.add_argument(
        "--source-root",
        type=Path,
        default=ROOT / "runs" / "curated_benchmark_v3" / "harness_tasks",
        help="Directory containing the curated full TaskSpec JSONL files.",
    )
    parser.add_argument(
        "--out-root",
        type=Path,
        default=ROOT / "release" / "datasets",
        help="Output directory for packaged validation_50 and full datasets.",
    )
    parser.add_argument("--validation-size", type=int, default=50)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    source_all = args.source_root / "all_computer_agent_safety.tasks.jsonl"
    source_execution = args.source_root / "agent_execution_computer_safety.tasks.jsonl"
    source_judge = args.source_root / "trace_judge_computer_safety.tasks.jsonl"
    if not source_all.exists():
        raise FileNotFoundError(source_all)

    records = read_jsonl(source_all)
    validation = stratified_validation_sample(records, args.validation_size, args.seed)
    source_display = display_path(source_all)
    write_split(args.out_root / "validation_50", validation, source=source_display, seed=args.seed)

    full_root = args.out_root / "full"
    full_root.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source_all, full_root / source_all.name)
    if source_execution.exists():
        shutil.copy2(source_execution, full_root / source_execution.name)
    else:
        write_jsonl(full_root / "agent_execution_computer_safety.tasks.jsonl", [record for record in records if not is_judge(record)])
    if source_judge.exists():
        shutil.copy2(source_judge, full_root / source_judge.name)
    else:
        write_jsonl(full_root / "trace_judge_computer_safety.tasks.jsonl", [record for record in records if is_judge(record)])
    write_json(full_root / "manifest.json", manifest(records, source=source_display, seed=None))

    top_manifest = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source_root": display_path(args.source_root),
        "out_root": display_path(args.out_root),
        "validation_size": args.validation_size,
        "seed": args.seed,
        "versions": {
            "validation_50": display_path(args.out_root / "validation_50"),
            "full": display_path(args.out_root / "full"),
        },
    }
    write_json(args.out_root / "manifest.json", top_manifest)
    print(json.dumps(top_manifest, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
