from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Select task records named in capture-health issue examples."
    )
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--audit", type=Path, action="append", required=True)
    parser.add_argument("--issue", default="tool_limit_hits")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    args = parser.parse_args()

    if args.output.exists() or args.manifest.exists():
        parser.error("output and manifest paths must not already exist")
    selected_ids: set[str] = set()
    for path in args.audit:
        report = json.loads(path.read_text(encoding="utf-8-sig"))
        selected_ids.update(str(value) for value in (report.get("examples") or {}).get(args.issue) or [])
    if not selected_ids:
        parser.error(f"no task IDs found for issue {args.issue}")

    records = read_jsonl(args.input)
    selected = [record for record in records if str(record.get("task_id") or "") in selected_ids]
    found = {str(record.get("task_id") or "") for record in selected}
    missing = sorted(selected_ids - found)
    if missing:
        parser.error(f"task IDs are missing from input: {missing}")

    args.output.parent.mkdir(parents=True, exist_ok=True)
    write_jsonl(args.output, selected)
    args.manifest.write_text(
        json.dumps(
            {
                "input": str(args.input.resolve()),
                "issue": args.issue,
                "records": len(selected),
                "task_ids": [str(record["task_id"]) for record in selected],
                "audits": [str(path.resolve()) for path in args.audit],
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print(json.dumps({"records": len(selected), "unique_task_ids": len(found)}, indent=2))
    return 0


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open("r", encoding="utf-8-sig") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def write_jsonl(path: Path, records: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")


if __name__ == "__main__":
    raise SystemExit(main())
