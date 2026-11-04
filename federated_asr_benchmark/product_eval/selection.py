"""Single source of truth for request-relative staged selections."""
from __future__ import annotations

import json
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parents[1]


def case_index_path(request_path: Path, request: dict, override: Path | None = None) -> Path:
    if override is not None:
        return override.expanduser().resolve()
    configured = request.get("selection", {}).get("case_index")
    if configured:
        path = Path(configured).expanduser()
        return path.resolve() if path.is_absolute() else (request_path.parent / path).resolve()
    return PACKAGE_ROOT / "case_index.jsonl"


def selected_cases(case_index: Path, benchmark_id: str, requested: list[str] | None) -> list[str]:
    rows = [json.loads(line) for line in case_index.read_text(encoding="utf-8-sig").splitlines() if line.strip()]
    seen = set()
    for row in rows:
        case_id = row.get("case_id")
        if not isinstance(case_id, str) or not case_id or case_id in seen:
            raise ValueError("Selection case IDs must be unique nonempty strings")
        seen.add(case_id)
    requested_set = set(requested) if requested is not None else None
    result = [row["case_id"] for row in rows if row["benchmark_id"] == benchmark_id
              and (requested_set is None or row["case_id"] in requested_set)]
    if requested_set is not None and requested_set - set(result):
        raise ValueError(f"Requested case IDs are not selected: {sorted(requested_set - set(result))}")
    return result
