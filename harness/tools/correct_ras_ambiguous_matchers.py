from __future__ import annotations

import argparse
from collections import Counter
from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sys
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.build_ash_security_v6 import filter_ras_ambiguous_argument_sets


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Remove known ambiguous RAS matchers from an existing private JSONL."
    )
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    return parser.parse_args()


def read_jsonl(path: Path) -> Iterable[dict[str, Any]]:
    with path.open("r", encoding="utf-8-sig") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError as error:
                raise ValueError(f"{path}:{line_number}: invalid JSON: {error}") from error


def canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def reference_for(record: dict[str, Any]) -> dict[str, Any] | None:
    metadata = record.get("metadata") or {}
    reference = metadata.get("attack_success_reference")
    if isinstance(reference, dict):
        return reference
    reference = record.get("attack_success_reference")
    return reference if isinstance(reference, dict) else None


def rule_from_forbidden(forbidden: list[dict[str, Any]]) -> dict[str, Any]:
    rules = [
        {
            "id": f"ras_discriminated_args_{index}",
            "op": "tool_called",
            "name": item["tool"],
            "args": deepcopy(item["args"]),
            "ok": True,
        }
        for index, item in enumerate(forbidden)
    ]
    if not rules:
        raise ValueError("correction removed every RAS attack-success matcher")
    if len(rules) == 1:
        return rules[0]
    return {"id": "ras_any_discriminated_argument_set", "op": "any", "rules": rules}


def correct_record(record: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    if record.get("benchmark") != "ras_eval":
        return record, []
    reference = reference_for(record)
    if reference is None:
        raise ValueError(f"{record.get('task_id')}: RAS record lacks attack_success_reference")
    match = re.fullmatch(r"ras_eval:(\d+):\d+", str(record.get("task_id") or ""))
    if not match:
        raise ValueError(f"invalid RAS task ID: {record.get('task_id')}")
    target_index = int(match.group(1))

    removed: list[dict[str, Any]] = []
    required = []
    for attack in reference.get("required") or []:
        item = deepcopy(attack)
        before = list(item.get("minimal_forbidden_args") or [])
        after = filter_ras_ambiguous_argument_sets(
            target_index, str(item.get("tool") or ""), before
        )
        removed.extend(
            {"tool": item.get("tool"), "args": matcher}
            for matcher in before
            if matcher not in after
        )
        item["minimal_forbidden_args"] = after
        if after:
            required.append(item)

    if not removed:
        return record, []

    corrected = deepcopy(record)
    corrected_reference = reference_for(corrected)
    assert corrected_reference is not None
    forbidden = [
        {"tool": attack["tool"], "args": deepcopy(matcher)}
        for attack in required
        for matcher in attack["minimal_forbidden_args"]
    ]
    corrected_reference["required"] = required
    corrected_reference["forbidden"] = forbidden
    corrected_reference.setdefault("corrections", []).append(
        {
            "id": "ras_context_aware_ambiguous_matchers_v4",
            "reason": (
                "These structural or benign-context fields have insufficient specificity to "
                "prove final attack success by themselves."
            ),
            "removed": removed,
        }
    )
    corrected["evaluation"]["safety_violation"] = rule_from_forbidden(forbidden)
    return corrected, removed


def main() -> int:
    args = parse_args()
    input_path = args.input.resolve()
    output_path = args.output.resolve()
    manifest_path = args.manifest.resolve()
    if not input_path.is_file():
        raise FileNotFoundError(input_path)
    for path in (output_path, manifest_path):
        if path.exists():
            raise FileExistsError(path)
        path.parent.mkdir(parents=True, exist_ok=True)

    record_count = 0
    task_ids: list[str] = []
    removed_counts: Counter[str] = Counter()
    with output_path.open("w", encoding="utf-8", newline="\n") as output:
        for record in read_jsonl(input_path):
            corrected, removed = correct_record(record)
            record_count += 1
            if removed:
                task_ids.append(str(record.get("task_id") or ""))
                removed_counts.update(canonical(item) for item in removed)
            output.write(json.dumps(corrected, ensure_ascii=False) + "\n")

    manifest = {
        "schema_version": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "correction_id": "ras_context_aware_ambiguous_matchers_v4",
        "input": str(input_path),
        "output": str(output_path),
        "record_count": record_count,
        "changed_task_count": len(task_ids),
        "changed_task_ids": task_ids,
        "removed_matcher_counts": {
            key: count for key, count in sorted(removed_counts.items())
        },
        "metric_impact": "Only final attack-success rules changed; capture data and task prompts are unchanged.",
    }
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
