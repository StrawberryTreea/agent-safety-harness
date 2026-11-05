"""Validate original public records before normalization can discard private fields."""
from __future__ import annotations

from typing import Any


PRIVATE_FIELDS = frozenset({"evaluation", "reference_answer", "label_mapping", "attack_success_reference", "private_rules", "private_oracle"})


def assert_public_record(record: dict[str, Any]) -> None:
    def inspect(value: Any, path: str) -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                if (key in PRIVATE_FIELDS or (key == "rule" and path.endswith(".note"))) and child not in (None, "", {}, []):
                    raise ValueError(f"Capture input leaks private scoring field: {path}.{key}")
                inspect(child, f"{path}.{key}")
        elif isinstance(value, list):
            for index, child in enumerate(value):
                inspect(child, f"{path}[{index}]")
    inspect(record, str(record.get("task_id", "task")))
