#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

try:
    import yaml
except ImportError as exc:  # pragma: no cover - exercised by deployment environments
    raise SystemExit("PyYAML is required. Install it with: conda install -n llm-safety-eval pyyaml") from exc


SCRIPT_DIR = Path(__file__).resolve().parent
SKILL_ROOT = SCRIPT_DIR.parent
TEMPLATE_DIR = SKILL_ROOT / "assets" / "templates"
PROTOCOL_FILES = (
    "evaluation_protocol_card.md",
    "model_registry.json",
    "taxonomy_registry.yaml",
    "metric_registry.yaml",
)
SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")


@dataclass
class Finding:
    code: str
    path: str
    message: str


def configure_stdio() -> None:
    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        if stream is None:
            continue
        try:
            stream.reconfigure(encoding="utf-8")
        except AttributeError:
            pass


def _is_placeholder(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    text = value.strip().lower()
    return not text or ("<" in text and ">" in text) or text.startswith("example_")


def _load_yaml(path: Path) -> Dict[str, Any]:
    value = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("expected a YAML mapping")
    return value


def _load_json(path: Path) -> Dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("expected a JSON object")
    return value


def _load_markdown_frontmatter(path: Path) -> Dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        raise ValueError("missing YAML frontmatter")
    try:
        end = next(i for i, line in enumerate(lines[1:], 1) if line.strip() == "---")
    except StopIteration as exc:
        raise ValueError("unterminated YAML frontmatter") from exc
    value = yaml.safe_load("\n".join(lines[1:end]))
    if not isinstance(value, dict):
        raise ValueError("frontmatter must be a mapping")
    return value


def load_protocol(protocol_dir: Path) -> Dict[str, Dict[str, Any]]:
    return {
        "protocol": _load_markdown_frontmatter(protocol_dir / "evaluation_protocol_card.md"),
        "models": _load_json(protocol_dir / "model_registry.json"),
        "taxonomy": _load_yaml(protocol_dir / "taxonomy_registry.yaml"),
        "metric": _load_yaml(protocol_dir / "metric_registry.yaml"),
    }


def _required(mapping: Dict[str, Any], key: str, path: str, errors: List[Finding]) -> Any:
    value = mapping.get(key)
    if value is None or _is_placeholder(value):
        errors.append(Finding("required", f"{path}.{key}", "value is required and cannot be a placeholder"))
    return value


def _reject_secret_fields(value: Any, path: str, errors: List[Finding]) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            key_text = str(key).lower()
            if any(token in key_text for token in ("api_key", "secret", "password", "access_token")):
                errors.append(Finding("secret_field", f"{path}.{key}", "protocol artifacts must not contain secrets"))
            _reject_secret_fields(child, f"{path}.{key}", errors)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _reject_secret_fields(child, f"{path}[{index}]", errors)


def _validate_protocol_card(
    card: Dict[str, Any], models: Dict[str, Any], taxonomy: Dict[str, Any], metric: Dict[str, Any]
) -> Tuple[List[Finding], List[Finding]]:
    errors: List[Finding] = []
    warnings: List[Finding] = []
    _required(card, "protocol_version", "protocol", errors)
    _required(card, "task_goal", "protocol", errors)

    dataset = card.get("dataset")
    if not isinstance(dataset, dict):
        errors.append(Finding("type", "protocol.dataset", "must be a mapping"))
    else:
        _required(dataset, "path", "protocol.dataset", errors)
        _required(dataset, "version", "protocol.dataset", errors)
        total = _required(dataset, "total", "protocol.dataset", errors)
        if not isinstance(total, int) or isinstance(total, bool) or total <= 0:
            errors.append(Finding("range", "protocol.dataset.total", "must be a positive integer"))
        sha256 = _required(dataset, "sha256", "protocol.dataset", errors)
        if not isinstance(sha256, str) or not SHA256_RE.fullmatch(sha256):
            errors.append(Finding("format", "protocol.dataset.sha256", "must be a 64-character SHA-256 hex digest"))
        id_field = _required(dataset, "id_field", "protocol.dataset", errors)
        if id_field != "id":
            errors.append(Finding("contract", "protocol.dataset.id_field", "current canonical contract requires 'id'"))

    taxonomy_version = _required(card, "taxonomy_version", "protocol", errors)
    if taxonomy_version != taxonomy.get("version"):
        errors.append(Finding("version_mismatch", "protocol.taxonomy_version", "must match taxonomy_registry.yaml"))
    metric_version = _required(card, "metric_version", "protocol", errors)
    if metric_version != metric.get("version"):
        errors.append(Finding("version_mismatch", "protocol.metric_version", "must match metric_registry.yaml"))

    card_models = card.get("models")
    registry_models = models.get("models")
    enabled_ids = {
        item.get("model_id")
        for item in registry_models or []
        if isinstance(item, dict) and item.get("run_enabled") is True
    }
    if not isinstance(card_models, list) or not card_models:
        errors.append(Finding("required", "protocol.models", "must contain at least one model_id"))
    else:
        for index, model_id in enumerate(card_models):
            if _is_placeholder(model_id):
                errors.append(Finding("placeholder", f"protocol.models[{index}]", "replace the model placeholder"))
            elif model_id not in enabled_ids:
                errors.append(Finding("unknown_model", f"protocol.models[{index}]", "model is not enabled in model_registry.json"))
    for index, model in enumerate(registry_models or []):
        if not isinstance(model, dict) or model.get("run_enabled") is not True:
            continue
        if model.get("endpoint") == "not_recorded":
            warnings.append(
                Finding(
                    "incomplete_provenance",
                    f"models.models[{index}].endpoint",
                    "record the evaluated endpoint or alias before Phase 7 publication",
                )
            )
        elif model.get("historical_endpoint_verified") is False:
            warnings.append(
                Finding(
                    "unverified_historical_endpoint",
                    f"models.models[{index}].endpoint",
                    "documented endpoint exists, but confirm which endpoint was used by the historical run",
                )
            )

    evaluation = card.get("evaluation_script")
    if not isinstance(evaluation, dict):
        errors.append(Finding("type", "protocol.evaluation_script", "must be a mapping"))
    else:
        _required(evaluation, "path", "protocol.evaluation_script", errors)
        script_hash = _required(evaluation, "sha256", "protocol.evaluation_script", errors)
        if not isinstance(script_hash, str) or not SHA256_RE.fullmatch(script_hash):
            errors.append(Finding("format", "protocol.evaluation_script.sha256", "must be a SHA-256 hex digest"))
        _required(evaluation, "prompt_version", "protocol.evaluation_script", errors)
        _required(evaluation, "scorer_version", "protocol.evaluation_script", errors)

    comparison = card.get("comparability")
    if not isinstance(comparison, dict):
        errors.append(Finding("type", "protocol.comparability", "must be a mapping"))
    else:
        status = _required(comparison, "status", "protocol.comparability", errors)
        allowed = {"new_baseline", "comparable", "conditional", "incompatible"}
        if status not in allowed:
            errors.append(Finding("enum", "protocol.comparability.status", f"must be one of {sorted(allowed)}"))
        _required(comparison, "statement", "protocol.comparability", errors)
        if status != "new_baseline" and not comparison.get("baseline_protocol"):
            errors.append(Finding("required", "protocol.comparability.baseline_protocol", "required unless status is new_baseline"))

    policy = _required(card, "publish_policy", "protocol", errors)
    if policy not in {"draft", "internal", "candidate", "authoritative"}:
        errors.append(Finding("enum", "protocol.publish_policy", "must be draft, internal, candidate, or authoritative"))
    if policy == "authoritative":
        warnings.append(Finding("approval", "protocol.publish_policy", "authoritative publication still requires Phase 7 validation"))
    return errors, warnings


def _validate_models(models: Dict[str, Any]) -> List[Finding]:
    errors: List[Finding] = []
    entries = models.get("models")
    if not isinstance(entries, list) or not entries:
        return [Finding("required", "models.models", "must contain at least one model")]
    seen: set[str] = set()
    for index, model in enumerate(entries):
        path = f"models.models[{index}]"
        if not isinstance(model, dict):
            errors.append(Finding("type", path, "must be an object"))
            continue
        model_id = _required(model, "model_id", path, errors)
        _required(model, "provider", path, errors)
        _required(model, "endpoint", path, errors)
        _required(model, "model_version", path, errors)
        if model_id in seen:
            errors.append(Finding("duplicate", f"{path}.model_id", "model_id must be unique"))
        elif isinstance(model_id, str):
            seen.add(model_id)
        if not isinstance(model.get("run_enabled"), bool):
            errors.append(Finding("type", f"{path}.run_enabled", "must be a boolean"))
    _reject_secret_fields(models, "models", errors)
    return errors


def _validate_taxonomy(taxonomy: Dict[str, Any]) -> List[Finding]:
    errors: List[Finding] = []
    _required(taxonomy, "version", "taxonomy", errors)
    _required(taxonomy, "source", "taxonomy", errors)
    _required(taxonomy, "l1_field", "taxonomy", errors)
    _required(taxonomy, "l2_field", "taxonomy", errors)
    l1_items = taxonomy.get("l1_dimensions")
    l2_items = taxonomy.get("l2_dimensions")
    if not isinstance(l1_items, list):
        errors.append(Finding("type", "taxonomy.l1_dimensions", "must be a list"))
        l1_items = []
    if not isinstance(l2_items, list):
        errors.append(Finding("type", "taxonomy.l2_dimensions", "must be a list"))
        l2_items = []
    expected_l1 = taxonomy.get("expected_l1_count")
    expected_l2 = taxonomy.get("expected_l2_count")
    if expected_l1 != len(l1_items):
        errors.append(Finding("count", "taxonomy.l1_dimensions", f"expected {expected_l1}, found {len(l1_items)}"))
    if expected_l2 != len(l2_items):
        errors.append(Finding("count", "taxonomy.l2_dimensions", f"expected {expected_l2}, found {len(l2_items)}"))

    l1_ids: set[str] = set()
    l2_ids: set[str] = set()
    for index, item in enumerate(l1_items):
        path = f"taxonomy.l1_dimensions[{index}]"
        if not isinstance(item, dict):
            errors.append(Finding("type", path, "must be a mapping"))
            continue
        item_id = _required(item, "id", path, errors)
        _required(item, "name", path, errors)
        if item_id in l1_ids:
            errors.append(Finding("duplicate", f"{path}.id", "L1 id must be unique"))
        elif isinstance(item_id, str):
            l1_ids.add(item_id)
    for index, item in enumerate(l2_items):
        path = f"taxonomy.l2_dimensions[{index}]"
        if not isinstance(item, dict):
            errors.append(Finding("type", path, "must be a mapping"))
            continue
        item_id = _required(item, "id", path, errors)
        _required(item, "name", path, errors)
        parent = _required(item, "parent", path, errors)
        if item_id in l2_ids:
            errors.append(Finding("duplicate", f"{path}.id", "L2 id must be unique"))
        elif isinstance(item_id, str):
            l2_ids.add(item_id)
        if parent not in l1_ids:
            errors.append(Finding("unknown_parent", f"{path}.parent", "must reference a declared L1 id"))
    return errors


def _validate_metric(metric: Dict[str, Any]) -> List[Finding]:
    errors: List[Finding] = []
    _required(metric, "version", "metric", errors)
    primary = _required(metric, "primary_metric", "metric", errors)
    formulas = metric.get("formulas")
    if not isinstance(formulas, dict):
        errors.append(Finding("type", "metric.formulas", "must be a mapping"))
    else:
        formula = re.sub(r"\s+", "", str(formulas.get(primary, "")))
        if formula != "passed/(passed+failed)":
            errors.append(Finding("contract", f"metric.formulas.{primary}", "must be passed / (passed + failed)"))
    if metric.get("refusal_scoring_mode") != "failure_and_separate_counter":
        errors.append(Finding("contract", "metric.refusal_scoring_mode", "must freeze refusal as failure plus a separate counter"))
    if metric.get("aggregation_mode") != "latest_by_id":
        errors.append(Finding("contract", "metric.aggregation_mode", "must aggregate only the latest row per id"))
    required_formulas = {
        "accuracy": "passed/(passed+failed)",
        "api_success_rate": "api_success/total",
        "refusal_rate": "refusal/total",
    }
    if isinstance(formulas, dict):
        for metric_name, expected_formula in required_formulas.items():
            actual = re.sub(r"\s+", "", str(formulas.get(metric_name, "")))
            if actual != expected_formula:
                errors.append(
                    Finding("contract", f"metric.formulas.{metric_name}", f"must be {expected_formula}")
                )
    published_metrics = metric.get("published_metrics")
    required_published = {"accuracy_excluding_api_errors", "api_success_rate", "refusal_rate"}
    if not isinstance(published_metrics, list) or not required_published.issubset(set(published_metrics)):
        errors.append(
            Finding("contract", "metric.published_metrics", f"must include {sorted(required_published)}")
        )
    slices = metric.get("reporting_slices")
    required_slices = {"overall", "safety_l1_dimension", "safety_l2_harm_type", "question_type"}
    slice_ids = {
        item.get("id") for item in slices or [] if isinstance(item, dict) and isinstance(item.get("id"), str)
    }
    if not isinstance(slices, list) or not required_slices.issubset(slice_ids):
        errors.append(Finding("contract", "metric.reporting_slices", f"must include {sorted(required_slices)}"))
    semantics = metric.get("status_semantics")
    expected = {
        "invalid_prediction": (False, True),
        "refusal": (False, True),
        "api_error": (None, False),
        "process_error": (None, False),
    }
    if not isinstance(semantics, dict):
        errors.append(Finding("type", "metric.status_semantics", "must be a mapping"))
    else:
        for status, (passed, in_denom) in expected.items():
            value = semantics.get(status)
            path = f"metric.status_semantics.{status}"
            if not isinstance(value, dict):
                errors.append(Finding("required", path, "status semantics are required"))
                continue
            if value.get("passed") is not passed:
                errors.append(Finding("contract", f"{path}.passed", f"must be {passed!r}"))
            if value.get("in_primary_denominator") is not in_denom:
                errors.append(Finding("contract", f"{path}.in_primary_denominator", f"must be {in_denom}"))
    return errors


def _resolve_bound_path(repo_root: Path, relative_path: Any, field_path: str, errors: List[Finding]) -> Optional[Path]:
    if not isinstance(relative_path, str) or _is_placeholder(relative_path):
        return None
    path = Path(relative_path)
    if path.is_absolute():
        errors.append(Finding("absolute_path", field_path, "must be relative to --repo-root"))
        return None
    resolved = (repo_root / path).resolve()
    try:
        resolved.relative_to(repo_root)
    except ValueError:
        errors.append(Finding("path_escape", field_path, "must remain inside --repo-root"))
        return None
    return resolved


def _hash_and_line_count(path: Path) -> Tuple[str, int]:
    digest = hashlib.sha256()
    lines = 0
    last_byte = b""
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
            lines += chunk.count(b"\n")
            last_byte = chunk[-1:]
    if path.stat().st_size and last_byte != b"\n":
        lines += 1
    return digest.hexdigest().upper(), lines


def _validate_bound_files(bundle: Dict[str, Dict[str, Any]], repo_root: Path) -> Tuple[List[Finding], Dict[str, Any]]:
    errors: List[Finding] = []
    verified: Dict[str, Any] = {"repo_root": str(repo_root)}
    card = bundle["protocol"]
    dataset = card.get("dataset") if isinstance(card.get("dataset"), dict) else {}
    dataset_path = _resolve_bound_path(repo_root, dataset.get("path"), "protocol.dataset.path", errors)
    if dataset_path is not None:
        if not dataset_path.is_file():
            errors.append(Finding("missing_bound_file", "protocol.dataset.path", str(dataset_path)))
        else:
            actual_hash, actual_total = _hash_and_line_count(dataset_path)
            verified["dataset"] = {"path": str(dataset_path), "sha256": actual_hash, "total": actual_total}
            if actual_hash != str(dataset.get("sha256", "")).upper():
                errors.append(Finding("hash_mismatch", "protocol.dataset.sha256", f"actual SHA-256 is {actual_hash}"))
            if actual_total != dataset.get("total"):
                errors.append(Finding("total_mismatch", "protocol.dataset.total", f"actual JSONL line count is {actual_total}"))

    evaluation = card.get("evaluation_script") if isinstance(card.get("evaluation_script"), dict) else {}
    script_path = _resolve_bound_path(repo_root, evaluation.get("path"), "protocol.evaluation_script.path", errors)
    if script_path is not None:
        if not script_path.is_file():
            errors.append(Finding("missing_bound_file", "protocol.evaluation_script.path", str(script_path)))
        else:
            actual_hash, _ = _hash_and_line_count(script_path)
            verified["evaluation_script"] = {"path": str(script_path), "sha256": actual_hash}
            if actual_hash != str(evaluation.get("sha256", "")).upper():
                errors.append(
                    Finding("hash_mismatch", "protocol.evaluation_script.sha256", f"actual SHA-256 is {actual_hash}")
                )
    return errors, verified


def validate_protocol_dir(protocol_dir: Path, repo_root: Optional[Path] = None) -> Dict[str, Any]:
    protocol_dir = protocol_dir.resolve()
    errors: List[Finding] = []
    warnings: List[Finding] = []
    for name in PROTOCOL_FILES:
        if not (protocol_dir / name).is_file():
            errors.append(Finding("missing_file", name, "required protocol artifact is missing"))
    if errors:
        return {"valid": False, "protocol_dir": str(protocol_dir), "errors": [asdict(x) for x in errors], "warnings": []}

    try:
        bundle = load_protocol(protocol_dir)
    except (OSError, ValueError, json.JSONDecodeError, yaml.YAMLError) as exc:
        errors.append(Finding("parse_error", str(protocol_dir), str(exc)))
        return {"valid": False, "protocol_dir": str(protocol_dir), "errors": [asdict(x) for x in errors], "warnings": []}

    card_errors, card_warnings = _validate_protocol_card(
        bundle["protocol"], bundle["models"], bundle["taxonomy"], bundle["metric"]
    )
    errors.extend(card_errors)
    warnings.extend(card_warnings)
    errors.extend(_validate_models(bundle["models"]))
    errors.extend(_validate_taxonomy(bundle["taxonomy"]))
    errors.extend(_validate_metric(bundle["metric"]))
    verified_files: Optional[Dict[str, Any]] = None
    if repo_root is not None:
        bound_errors, verified_files = _validate_bound_files(bundle, repo_root.expanduser().resolve())
        errors.extend(bound_errors)
    return {
        "valid": not errors,
        "protocol_dir": str(protocol_dir),
        "summary": {
            "protocol_version": bundle["protocol"].get("protocol_version"),
            "taxonomy_version": bundle["taxonomy"].get("version"),
            "metric_version": bundle["metric"].get("version"),
            "models": [item.get("model_id") for item in bundle["models"].get("models", []) if isinstance(item, dict)],
        },
        "errors": [asdict(x) for x in errors],
        "warnings": [asdict(x) for x in warnings],
        "verified_files": verified_files,
    }


def initialize_protocol(output_dir: Path, force: bool = False) -> Dict[str, Any]:
    output_dir = output_dir.resolve()
    targets = [output_dir / name for name in PROTOCOL_FILES]
    if not force:
        existing = [target for target in targets if target.exists()]
        if existing:
            raise FileExistsError(f"refusing to overwrite existing file: {existing[0]}")
    output_dir.mkdir(parents=True, exist_ok=True)
    copied: List[str] = []
    for name, target in zip(PROTOCOL_FILES, targets):
        source = TEMPLATE_DIR / name
        shutil.copy2(source, target)
        copied.append(str(target))
    return {"initialized": True, "protocol_dir": str(output_dir), "files": copied}


def _change(path: str, before: Any, after: Any) -> Dict[str, Any]:
    return {"path": path, "before": before, "after": after}


def compare_protocol_dirs(base_dir: Path, candidate_dir: Path) -> Dict[str, Any]:
    base_validation = validate_protocol_dir(base_dir)
    candidate_validation = validate_protocol_dir(candidate_dir)
    if not base_validation["valid"] or not candidate_validation["valid"]:
        return {
            "status": "invalid",
            "comparable": False,
            "base_validation": base_validation,
            "candidate_validation": candidate_validation,
            "breaking_changes": [],
            "conditional_changes": [],
        }

    base = load_protocol(base_dir.resolve())
    candidate = load_protocol(candidate_dir.resolve())
    breaking: List[Dict[str, Any]] = []
    conditional: List[Dict[str, Any]] = []
    critical_paths: Iterable[Tuple[str, Any, Any]] = (
        ("protocol.dataset", base["protocol"].get("dataset"), candidate["protocol"].get("dataset")),
        ("protocol.evaluation_script", base["protocol"].get("evaluation_script"), candidate["protocol"].get("evaluation_script")),
        ("taxonomy", base["taxonomy"], candidate["taxonomy"]),
        ("metric", base["metric"], candidate["metric"]),
    )
    for path, before, after in critical_paths:
        if before != after:
            breaking.append(_change(path, before, after))

    base_models = base["models"].get("models")
    candidate_models = candidate["models"].get("models")
    if base_models != candidate_models:
        conditional.append(_change("models", base_models, candidate_models))

    status = "incompatible" if breaking else "conditional" if conditional else "comparable"
    return {
        "status": status,
        "comparable": status == "comparable",
        "base_protocol_dir": str(base_dir.resolve()),
        "candidate_protocol_dir": str(candidate_dir.resolve()),
        "breaking_changes": breaking,
        "conditional_changes": conditional,
    }


def render_text(result: Dict[str, Any]) -> str:
    if "valid" in result:
        lines = [f"valid: {str(result['valid']).lower()}", f"protocol_dir: {result['protocol_dir']}"]
        for finding in result.get("errors", []):
            lines.append(f"ERROR {finding['code']} {finding['path']}: {finding['message']}")
        for finding in result.get("warnings", []):
            lines.append(f"WARNING {finding['code']} {finding['path']}: {finding['message']}")
        return "\n".join(lines)
    if "status" in result:
        lines = [f"status: {result['status']}", f"comparable: {str(result['comparable']).lower()}"]
        for change in result.get("breaking_changes", []):
            lines.append(f"BREAKING {change['path']}")
        for change in result.get("conditional_changes", []):
            lines.append(f"CONDITIONAL {change['path']}")
        return "\n".join(lines)
    return json.dumps(result, ensure_ascii=False, indent=2)


def emit(result: Dict[str, Any], output_format: str) -> None:
    if output_format == "json":
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(render_text(result))


def main() -> int:
    configure_stdio()
    parser = argparse.ArgumentParser(description="Initialize, validate, and compare Phase 0 protocol artifacts.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    init_parser = subparsers.add_parser("init", help="Copy protocol templates into a working directory.")
    init_parser.add_argument("--output-dir", type=Path, required=True)
    init_parser.add_argument("--force", action="store_true")
    init_parser.add_argument("--format", choices=("text", "json"), default="text")

    validate_parser = subparsers.add_parser("validate", help="Validate a protocol directory.")
    validate_parser.add_argument("--protocol-dir", type=Path, required=True)
    validate_parser.add_argument("--repo-root", type=Path, help="Also verify bound dataset and evaluation-script files.")
    validate_parser.add_argument("--format", choices=("text", "json"), default="text")

    compare_parser = subparsers.add_parser("compare", help="Compare two valid protocol directories.")
    compare_parser.add_argument("--base", type=Path, required=True)
    compare_parser.add_argument("--candidate", type=Path, required=True)
    compare_parser.add_argument("--format", choices=("text", "json"), default="text")

    args = parser.parse_args()
    try:
        if args.command == "init":
            result = initialize_protocol(args.output_dir, force=args.force)
            emit(result, args.format)
            return 0
        if args.command == "validate":
            result = validate_protocol_dir(args.protocol_dir, args.repo_root)
            emit(result, args.format)
            return 0 if result["valid"] else 1
        result = compare_protocol_dirs(args.base, args.candidate)
        emit(result, args.format)
        return 0 if result["status"] in {"comparable", "conditional"} else 1
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
