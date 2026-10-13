from __future__ import annotations

import argparse
from collections import defaultdict
import hashlib
import json
from pathlib import Path
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from agent_safety_harness.io import read_jsonl, write_json, write_jsonl


def main() -> int:
    parser = argparse.ArgumentParser(description="Select a deterministic all-source execution smoke set.")
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--seed", type=int, default=20260805)
    args = parser.parse_args()

    records = read_jsonl(args.input)
    groups: dict[tuple[str, str, bool], list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        key = (
            str(record["benchmark"]),
            str((record.get("labels") or {}).get("expected_behavior")),
            bool(record.get("attack_goal")),
        )
        groups[key].append(record)
    selected: list[dict[str, Any]] = []
    selected_by_group: dict[tuple[str, str, bool], dict[str, Any]] = {}
    for key, items in sorted(groups.items()):
        items.sort(key=lambda record: stable_order(str(record["task_id"]), args.seed))
        selected_by_group[key] = items[0]
        selected.append(items[0])
    selected.sort(key=lambda record: (str(record["benchmark"]), str(record["task_id"])))
    write_jsonl(args.output, selected)
    manifest = {
        "schema_version": 1,
        "source": str(args.input.resolve()),
        "seed": args.seed,
        "records": len(selected),
        "benchmarks": sorted({str(record["benchmark"]) for record in selected}),
        "covered_groups": [
            {
                "benchmark": key[0],
                "expected_behavior": key[1],
                "attack_present": key[2],
                "task_id": selected_record["task_id"],
            }
            for key, selected_record in sorted(selected_by_group.items())
        ],
    }
    write_json(args.manifest, manifest)
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


def stable_order(task_id: str, seed: int) -> str:
    return hashlib.sha256(f"{seed}:{task_id}".encode("utf-8")).hexdigest()


if __name__ == "__main__":
    raise SystemExit(main())
