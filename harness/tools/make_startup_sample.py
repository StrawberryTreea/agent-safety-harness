from __future__ import annotations

import argparse
import json
import math
import random
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from agent_safety_harness.io import read_jsonl, write_json, write_jsonl


def main() -> None:
    parser = argparse.ArgumentParser(description="Create a stratified startup sample for agent-safety evaluation.")
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--out-root", type=Path, required=True)
    parser.add_argument("--fraction", type=float, default=0.1)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    records = read_jsonl(args.input)
    if not records:
        raise SystemExit("No records found")
    out_root = args.out_root.resolve()
    out_root.mkdir(parents=True, exist_ok=True)

    rng = random.Random(args.seed)
    by_group: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        by_group[(str(record.get("benchmark", "")), str(record.get("profile", "")))].append(record)

    total = len(records)
    target = max(len(by_group), math.ceil(total * args.fraction))
    selected = []
    selected_ids = set()

    group_items = sorted(by_group.items(), key=lambda item: (item[0][0], item[0][1]))
    quotas = {group: max(1, round(len(items) * args.fraction)) for group, items in group_items}
    adjusted = sum(quotas.values())
    if adjusted < target:
        deficit = target - adjusted
        for group, _ in sorted(group_items, key=lambda item: len(item[1]), reverse=True):
            if deficit <= 0:
                break
            quotas[group] += 1
            deficit -= 1
    elif adjusted > target:
        surplus = adjusted - target
        for group, _ in sorted(group_items, key=lambda item: len(item[1]), reverse=True):
            if surplus <= 0:
                break
            if quotas[group] > 1:
                quotas[group] -= 1
                surplus -= 1

    for group, items in group_items:
        rng.shuffle(items)
        take = min(len(items), quotas[group])
        for record in items[:take]:
            selected.append(record)
            selected_ids.add(record["task_id"])

    if len(selected) < target:
        remaining = [record for record in records if record["task_id"] not in selected_ids]
        rng.shuffle(remaining)
        for record in remaining[: target - len(selected)]:
            selected.append(record)
            selected_ids.add(record["task_id"])

    selected.sort(key=lambda r: (r["benchmark"], r["profile"], r["task_id"]))
    execution_selected = [record for record in selected if record.get("profile") != "trajectory_judge"]
    judge_selected = [record for record in selected if record.get("profile") == "trajectory_judge"]
    sample_path = out_root / "startup_sample.tasks.jsonl"
    execution_path = out_root / "startup_execution_sample.tasks.jsonl"
    judge_path = out_root / "startup_judge_sample.tasks.jsonl"
    manifest_path = out_root / "startup_sample.manifest.json"
    selected_benchmarks = Counter(record["benchmark"] for record in selected)
    selected_profiles = Counter(record["profile"] for record in selected)
    selected_groups = Counter(f"{record['benchmark']}::{record['profile']}" for record in selected)
    write_jsonl(sample_path, selected)
    write_jsonl(execution_path, execution_selected)
    write_jsonl(judge_path, judge_selected)
    write_json(
        manifest_path,
        {
            "source": str(args.input),
            "out_root": str(out_root),
            "seed": args.seed,
            "fraction": args.fraction,
            "total_records": total,
            "sample_records": len(selected),
            "execution_records": len(execution_selected),
            "judge_records": len(judge_selected),
            "selected_benchmarks": dict(selected_benchmarks),
            "selected_profiles": dict(selected_profiles),
            "selected_groups": dict(selected_groups),
            "sample_path": str(sample_path),
            "execution_path": str(execution_path),
            "judge_path": str(judge_path),
        },
    )
    print(json.dumps({"sample_path": str(sample_path), "execution_path": str(execution_path), "judge_path": str(judge_path), "count": len(selected)}, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
