from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path
import random
from typing import Any


def main() -> int:
    parser = argparse.ArgumentParser(description="Create a deterministic per-source native-runtime smoke set.")
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--per-source", type=int, default=3)
    parser.add_argument("--seed", type=int, default=20260807)
    args = parser.parse_args()

    records = read_jsonl(args.input)
    rng = random.Random(args.seed)
    grouped: dict[str, list[dict[str, Any]]] = {}
    for record in records:
        grouped.setdefault(str(record.get("benchmark") or ""), []).append(record)
    selected: list[dict[str, Any]] = []
    for source in sorted(grouped):
        candidates = list(grouped[source])
        rng.shuffle(candidates)
        selected.extend(sorted(candidates[: args.per_source], key=lambda item: str(item["task_id"])))
    selected.sort(key=lambda item: (str(item.get("benchmark") or ""), str(item["task_id"])))

    write_jsonl(args.output, selected)
    manifest = {
        "schema_version": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "input": str(args.input.resolve()),
        "seed": args.seed,
        "per_source": args.per_source,
        "records": len(selected),
        "source_counts": dict(Counter(str(item.get("benchmark") or "") for item in selected)),
        "task_ids": [str(item["task_id"]) for item in selected],
    }
    args.manifest.parent.mkdir(parents=True, exist_ok=True)
    args.manifest.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open("r", encoding="utf-8-sig") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def write_jsonl(path: Path, records: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")


if __name__ == "__main__":
    raise SystemExit(main())
