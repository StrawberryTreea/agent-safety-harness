"""Validate actual JSON differences, not a generator's claim of one factor."""
from __future__ import annotations

import copy
from orchestrator.registry import content_hash


def pointer_parts(pointer: str) -> list[str]:
    if not isinstance(pointer, str) or not pointer.startswith("/") or pointer == "/":
        raise ValueError("A non-root JSON pointer is required")
    parts = pointer[1:].split("/")
    if any("~" in p.replace("~0", "").replace("~1", "") for p in parts):
        raise ValueError("Invalid JSON pointer escape")
    return [p.replace("~1", "/").replace("~0", "~") for p in parts]


def get_pointer(value, pointer):
    for part in pointer_parts(pointer):
        value = value[int(part)] if isinstance(value, list) else value[part]
    return value


def replace_pointer(value, pointer, replacement):
    result = copy.deepcopy(value)
    parts = pointer_parts(pointer)
    parent = result
    for part in parts[:-1]:
        parent = parent[int(part)] if isinstance(parent, list) else parent[part]
    key = int(parts[-1]) if isinstance(parent, list) else parts[-1]
    if isinstance(parent, dict) and key not in parent:
        raise ValueError("Transforms may only replace existing fields")
    parent[key] = copy.deepcopy(replacement)
    return result


def changed_paths(before, after, prefix=""):
    if type(before) is not type(after):
        return [prefix]
    if isinstance(before, dict):
        result = []
        for key in sorted(set(before) | set(after)):
            path = prefix + "/" + str(key).replace("~", "~0").replace("/", "~1")
            result.extend([path] if key not in before or key not in after else changed_paths(before[key], after[key], path))
        return result
    if isinstance(before, list):
        if len(before) != len(after):
            return [prefix]
        return [p for i,(left,right) in enumerate(zip(before, after)) for p in changed_paths(left,right,prefix+f"/{i}")]
    return [] if before == after else [prefix]


def validate_transform(before: dict, after: dict, spec: dict, policy: dict) -> dict:
    paths = changed_paths(before, after)
    try:
        pointer = spec["field_pointer"]
        old, new = get_pointer(before, pointer), get_pointer(after, pointer)
    except (KeyError, IndexError, ValueError, TypeError):
        return dict(status="fail", reason_code="invalid_transform_pointer", changed_paths=paths)
    if spec.get("space") not in policy.get("spaces", []) or pointer not in policy.get("allowed_pointers", []):
        return dict(status="fail", reason_code="transform_outside_whitelist", changed_paths=paths)
    if not paths:
        return dict(status="fail", reason_code="no_effective_change", changed_paths=[])
    if any(p != pointer and not p.startswith(pointer + "/") for p in paths):
        return dict(status="pending", reason_code="multiple_changed_fields", changed_paths=paths)
    if spec.get("before_hash") != content_hash(old) or spec.get("after_hash") != content_hash(new):
        return dict(status="fail", reason_code="transform_hash_mismatch", changed_paths=paths)
    factors = spec.get("changed_factors")
    if not isinstance(factors, list) or len(factors) != 1 or factors != [spec["space"]]:
        return dict(status="pending", reason_code="multiple_or_unverified_factors", changed_paths=paths)
    if policy.get("semantic_review_required", True):
        return dict(status="pending", reason_code="semantic_review_required", changed_paths=paths)
    return dict(status="pass", reason_code="registered_single_field_transform", changed_paths=paths)
