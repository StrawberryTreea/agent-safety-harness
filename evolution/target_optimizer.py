"""Build deterministic prompt/policy candidates from an explicit, auditable recipe."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
from typing import Any

import yaml

from scripts.result_analysis import TRACKS, empty_output, write_json
from .target_loop import register_target_candidate


SUPPORTED_CHANGE_TYPES = ("prompt", "policy")
SUPPORTED_OPERATIONS = ("set", "append_text", "prepend_text")
PATH_PART = re.compile(r"^[A-Za-z_][A-Za-z0-9_-]*$")
BLOCKED_PATH_PARTS = {
    "answer",
    "answers",
    "api_key",
    "api_key_env",
    "answer_key",
    "auth_token",
    "backend",
    "base_url",
    "benchmark_mcp_server_command",
    "command",
    "credential",
    "credentials",
    "evaluation",
    "evaluator",
    "expected_answer",
    "expected_output",
    "endpoint",
    "env",
    "env_allowlist",
    "executable",
    "ground_truth",
    "judge",
    "judge_prompt",
    "inherit_env",
    "models_config",
    "agents_config",
    "oracle",
    "private_oracle",
    "reference_answer",
    "response_file",
    "rubric",
    "score",
    "scorer",
    "scoring",
    "secret",
    "secrets",
    "token",
    "url",
}

PROMPT_PATH_LEAVES = {"prompt", "system_prompt", "instructions", "template", "prefix", "suffix"}
POLICY_PATH_LEAVES = {"agent_id", "temperature", "max_tokens", "timeout_seconds"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _sha256_value(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _load_mapping(path: Path, *, label: str) -> dict[str, Any]:
    resolved = path.expanduser().resolve()
    if resolved.suffix.lower() not in {".json", ".yaml", ".yml"}:
        raise ValueError(f"{label} must be JSON or YAML: {resolved}")
    value = yaml.safe_load(resolved.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise ValueError(f"{label} must contain an object: {resolved}")
    try:
        json.dumps(value, ensure_ascii=False)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must contain JSON-compatible values: {exc}") from exc
    return value


def _load_failure_signals(path: Path, *, track: str) -> set[str]:
    resolved = (path / "failures.jsonl" if path.is_dir() else path).expanduser().resolve()
    signals: set[str] = set()
    with resolved.open(encoding="utf-8-sig") as handle:
        for line_no, line in enumerate(handle, 1):
            if not line.strip():
                continue
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ValueError(f"Failure row must be an object at {resolved}:{line_no}")
            if value.get("track") != track or value.get("outcome") != "fail":
                raise ValueError(f"Failure evidence does not match track/outcome at {resolved}:{line_no}")
            row_signals = value.get("failures")
            if not isinstance(row_signals, list) or not row_signals or not all(
                isinstance(item, str) and item.strip() for item in row_signals
            ):
                raise ValueError(f"Failure evidence lacks observed signals at {resolved}:{line_no}")
            signals.update(row_signals)
    if not signals:
        raise ValueError("Candidate building requires at least one observed failure signal")
    return signals


def _parse_path(value: Any) -> tuple[str, ...]:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("Operation paths must be non-empty dot-separated strings")
    parts = tuple(value.split("."))
    if not all(PATH_PART.fullmatch(part) for part in parts):
        raise ValueError(f"Unsupported operation path: {value!r}")
    blocked = sorted({part.lower().replace("-", "_") for part in parts} & BLOCKED_PATH_PARTS)
    if blocked:
        raise ValueError(f"Operation path touches protected evaluator/oracle data: {value!r}")
    return parts


def _validate_change_path(change_type: str, path: str) -> None:
    parts = _parse_path(path)
    leaf = parts[-1].lower().replace("-", "_")
    if change_type == "prompt" and leaf not in PROMPT_PATH_LEAVES:
        raise ValueError(f"Prompt recipe path is not a prompt field: {path!r}")
    if change_type == "policy" and leaf not in POLICY_PATH_LEAVES:
        raise ValueError(f"Policy recipe path is not a bounded policy field: {path!r}")


def _reject_protected_value_keys(value: Any, *, path: str) -> None:
    if isinstance(value, dict):
        normalized = {str(key).lower().replace("-", "_") for key in value}
        blocked = sorted(normalized & BLOCKED_PATH_PARTS)
        if blocked:
            raise ValueError(f"Operation value at {path!r} contains protected keys: {', '.join(blocked)}")
        for key, child in value.items():
            _reject_protected_value_keys(child, path=f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _reject_protected_value_keys(child, path=f"{path}[{index}]")


def _parent_and_key(document: dict[str, Any], path: tuple[str, ...]) -> tuple[dict[str, Any], str]:
    parent: Any = document
    for part in path[:-1]:
        if not isinstance(parent, dict) or part not in parent:
            raise ValueError(f"Operation path does not already exist: {'.'.join(path)}")
        parent = parent[part]
    key = path[-1]
    if not isinstance(parent, dict) or key not in parent:
        raise ValueError(f"Operation path does not already exist: {'.'.join(path)}")
    return parent, key


def _apply_operation(document: dict[str, Any], operation: dict[str, Any]) -> dict[str, Any]:
    op = operation.get("op")
    if op not in SUPPORTED_OPERATIONS:
        raise ValueError(f"Unsupported candidate operation: {op!r}")
    allowed_fields = {"op", "path", "value"} | ({"separator"} if op != "set" else set())
    unknown = sorted(set(operation) - allowed_fields)
    if unknown:
        raise ValueError(f"Unknown fields for {op}: {', '.join(unknown)}")
    path = _parse_path(operation.get("path"))
    parent, key = _parent_and_key(document, path)
    before = deepcopy(parent[key])
    value = operation.get("value")
    _reject_protected_value_keys(value, path=".".join(path))
    if op == "set":
        after = deepcopy(value)
    else:
        if not isinstance(before, str) or not isinstance(value, str):
            raise ValueError(f"{op} requires string input and value at {'.'.join(path)}")
        separator = operation.get("separator", "\n")
        if not isinstance(separator, str):
            raise ValueError(f"{op} separator must be a string")
        after = before + separator + value if op == "append_text" else value + separator + before
    parent[key] = after
    return {
        "op": op,
        "path": ".".join(path),
        "before_sha256": _sha256_value(before),
        "after_sha256": _sha256_value(after),
    }


def build_target_candidate(
    *,
    baseline_asset: Path,
    recipe_path: Path,
    failures: Path,
    out: Path,
) -> dict[str, Any]:
    """Apply a bounded recipe and register the resulting immutable candidate."""
    baseline_path = baseline_asset.expanduser().resolve()
    recipe_resolved = recipe_path.expanduser().resolve()
    baseline = _load_mapping(baseline_path, label="Baseline target asset")
    recipe = _load_mapping(recipe_resolved, label="Candidate recipe")
    known_fields = {
        "schema_version", "track", "baseline_id", "candidate_id", "change_type",
        "allowed_paths", "applicable_failure_signals", "operations", "notes",
    }
    unknown = sorted(set(recipe) - known_fields)
    if unknown:
        raise ValueError(f"Unknown candidate recipe fields: {', '.join(unknown)}")
    if recipe.get("schema_version") != "1.0":
        raise ValueError("Candidate recipe schema_version must be 1.0")
    track = recipe.get("track")
    baseline_id, candidate_id = recipe.get("baseline_id"), recipe.get("candidate_id")
    change_type = recipe.get("change_type")
    if track not in TRACKS:
        raise ValueError(f"Unsupported recipe track: {track}")
    if change_type not in SUPPORTED_CHANGE_TYPES:
        raise ValueError("Deterministic candidate building supports prompt or policy changes only")
    if not isinstance(track, str) or not isinstance(baseline_id, str) or not isinstance(candidate_id, str):
        raise ValueError("Recipe track, baseline_id and candidate_id must be strings")
    if not baseline_id.strip() or not candidate_id.strip() or baseline_id == candidate_id:
        raise ValueError("Recipe baseline_id and candidate_id must be non-empty and different")

    allowed_raw = recipe.get("allowed_paths")
    if not isinstance(allowed_raw, list) or not allowed_raw:
        raise ValueError("Recipe allowed_paths must be a non-empty array")
    allowed_paths = {".".join(_parse_path(value)) for value in allowed_raw}
    if len(allowed_paths) != len(allowed_raw):
        raise ValueError("Recipe allowed_paths must not contain duplicates")
    for path in sorted(allowed_paths):
        _validate_change_path(change_type, path)
    operations = recipe.get("operations")
    if not isinstance(operations, list) or not operations or not all(isinstance(item, dict) for item in operations):
        raise ValueError("Recipe operations must be a non-empty object array")
    operation_paths = [str(item.get("path")) for item in operations]
    if len(operation_paths) != len(set(operation_paths)):
        raise ValueError("A recipe may change each path at most once")
    unauthorized = sorted(set(operation_paths) - allowed_paths)
    if unauthorized:
        raise ValueError(f"Operations are outside allowed_paths: {', '.join(unauthorized)}")

    observed_signals = _load_failure_signals(failures, track=track)
    applicable = recipe.get("applicable_failure_signals", [])
    if not isinstance(applicable, list) or not all(isinstance(item, str) and item.strip() for item in applicable):
        raise ValueError("Recipe applicable_failure_signals must be a string array")
    matched_signals = sorted(observed_signals & set(applicable))
    if applicable and not matched_signals:
        raise ValueError("Recipe does not match any observed failure signal")

    candidate = deepcopy(baseline)
    changes = [_apply_operation(candidate, operation) for operation in operations]
    if _sha256_value(candidate) == _sha256_value(baseline):
        raise ValueError("Candidate recipe produced no effective change")

    output = out.expanduser().resolve()
    empty_output(output)
    suffix = ".json" if baseline_path.suffix.lower() == ".json" else ".yaml"
    candidate_path = output / f"candidate{suffix}"
    if suffix == ".json":
        write_json(candidate_path, candidate)
    else:
        candidate_path.write_text(yaml.safe_dump(candidate, sort_keys=False, allow_unicode=True), encoding="utf-8")
    registration_dir = output / "registration"
    record = register_target_candidate(
        track=track,
        baseline_id=baseline_id,
        candidate_id=candidate_id,
        change_type=change_type,
        candidate_asset=candidate_path,
        baseline_asset=baseline_path,
        failures=failures,
        notes=str(recipe.get("notes")) if recipe.get("notes") is not None else None,
        out=registration_dir,
    )
    record["generation"] = {
        "mode": "deterministic_bounded_recipe",
        "automatic_optimization": False,
        "model_generated": False,
        "root_cause_confirmed": False,
        "recipe": {"path": str(recipe_resolved), "sha256": _sha256_file(recipe_resolved)},
        "allowed_paths": sorted(allowed_paths),
        "changes": changes,
        "observed_failure_signals": sorted(observed_signals),
        "matched_failure_signals": matched_signals,
    }
    write_json(registration_dir / "target_candidate.json", record)
    created_at = _now()
    manifest = {
        "schema_version": "1.0",
        "stage": "target_candidate_build",
        "status": "completed",
        "track": track,
        "baseline_id": baseline_id,
        "candidate_id": candidate_id,
        "baseline_asset": {"path": str(baseline_path), "sha256": _sha256_file(baseline_path)},
        "candidate_asset": {"path": str(candidate_path), "sha256": _sha256_file(candidate_path)},
        "recipe": {"path": str(recipe_resolved), "sha256": _sha256_file(recipe_resolved)},
        "registration": str(registration_dir / "target_candidate.json"),
        "execution_performed": False,
        "deployment_performed": False,
        "created_at": created_at,
    }
    write_json(output / "manifest.json", manifest)
    return manifest
