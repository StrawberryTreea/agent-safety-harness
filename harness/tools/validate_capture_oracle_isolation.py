from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from agent_safety_harness.io import read_jsonl, write_json


FORBIDDEN_KEYS = {
    "attack_success_reference",
    "source_evaluator",
    "unsafe_replay",
    "safe_replay",
    "near_miss_replays",
}


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Verify that runtime input and capture artifacts contain no private scoring oracle."
    )
    parser.add_argument("--runtime-input", type=Path, required=True)
    parser.add_argument("--capture-run", type=Path, required=True)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()

    failures: list[str] = []
    runtime_records = read_jsonl(args.runtime_input)
    capture_path = args.capture_run / "capture_results.jsonl"
    capture_records = read_jsonl(capture_path)
    manifest = json.loads(
        (args.capture_run / "manifest.json").read_text(encoding="utf-8-sig")
    )

    for scope, records in (
        ("runtime", runtime_records),
        ("capture", capture_records),
    ):
        for index, record in enumerate(records):
            leaked = sorted(_find_forbidden_keys(record))
            if leaked:
                failures.append(f"{scope}[{index}] forbidden keys: {', '.join(leaked)}")
            for evaluation in _find_values_for_key(record, "evaluation"):
                if evaluation not in ({}, None):
                    failures.append(f"{scope}[{index}] contains non-empty evaluation")
                    break
    if manifest.get("contains_private_oracle") is not False:
        failures.append("capture manifest does not declare contains_private_oracle=false")

    report = {
        "valid": not failures,
        "runtime_records": len(runtime_records),
        "capture_records": len(capture_records),
        "forbidden_keys": sorted(FORBIDDEN_KEYS),
        "failures": failures,
        "scope": "artifact-content isolation only; Docker mount isolation is a separate gate",
    }
    if args.report:
        write_json(args.report, report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["valid"] else 1


def _find_forbidden_keys(value: Any) -> set[str]:
    if isinstance(value, dict):
        found = set(value).intersection(FORBIDDEN_KEYS)
        for child in value.values():
            found.update(_find_forbidden_keys(child))
        return found
    if isinstance(value, list):
        found: set[str] = set()
        for child in value:
            found.update(_find_forbidden_keys(child))
        return found
    return set()


def _find_values_for_key(value: Any, key: str) -> list[Any]:
    if isinstance(value, dict):
        found = [value[key]] if key in value else []
        for child in value.values():
            found.extend(_find_values_for_key(child, key))
        return found
    if isinstance(value, list):
        return [item for child in value for item in _find_values_for_key(child, key)]
    return []


if __name__ == "__main__":
    raise SystemExit(main())
