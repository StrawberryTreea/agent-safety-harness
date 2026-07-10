#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

try:
    import yaml
except ImportError as exc:  # pragma: no cover - deployment guard
    raise SystemExit("PyYAML is required. Install it with: conda install -n llm-safety-eval pyyaml") from exc


SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from phase12_common import compute_stats, read_records, write_json, write_jsonl  # noqa: E402
from protocol_manager import load_protocol, validate_protocol_dir  # noqa: E402


DEFAULT_CANONICAL = Path("eval_mvp_V2/data/master_normalized_dataset_v2_final.jsonl")
DEFAULT_SMOKE = Path("eval_mvp_V2/data/test_eval_100_dataset_type_coverage.jsonl")
DEFAULT_OUTPUT_DIR = Path("work/phase3")
DEFAULT_RUN_CONFIG = DEFAULT_OUTPUT_DIR / "run_config.yaml"
DEFAULT_RUN_ROOT = Path("rule_eval_mvp/output_smoke")
EVALUATOR = Path("eval_mvp_V2/multi_model_rule_eval_prompt_only.py")
REQUIRED_FIELDS = ("id", "test_text", "reference_answer")
COVERAGE_FIELDS = ("dataset_name", "type", "safety_l1_dimension", "safety_l2_harm_type")


def configure_stdio() -> None:
    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        if stream is None:
            continue
        try:
            stream.reconfigure(encoding="utf-8")
        except AttributeError:
            pass


def normalize_text(value: Any) -> str:
    if value is None:
        return ""
    return " ".join(str(value).split())


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest().upper()


def relpath(path: Path, repo_root: Path) -> str:
    resolved = path.resolve()
    try:
        return resolved.relative_to(repo_root.resolve()).as_posix()
    except ValueError:
        return resolved.as_posix()


def validate_record(record: Dict[str, Any], line_number: int) -> Optional[str]:
    for field in REQUIRED_FIELDS:
        if not normalize_text(record.get(field)):
            return f"line {line_number}: missing required field {field}"
    return None


def bucket_key(record: Dict[str, Any], fields: Sequence[str]) -> Tuple[str, ...]:
    return tuple(normalize_text(record.get(field)) or "<missing>" for field in fields)


def select_smoke_subset(records: Sequence[Dict[str, Any]], sample_size: int) -> List[Dict[str, Any]]:
    if sample_size <= 0:
        return []
    selected: List[Dict[str, Any]] = []
    selected_ids: set[str] = set()

    def add_record(record: Dict[str, Any]) -> bool:
        item_id = normalize_text(record.get("id"))
        if not item_id or item_id in selected_ids or len(selected) >= sample_size:
            return False
        selected.append(record)
        selected_ids.add(item_id)
        return True

    coverage_passes: List[Tuple[str, ...]] = [
        ("dataset_name",),
        ("type",),
        ("safety_l1_dimension",),
        ("safety_l2_harm_type",),
        ("safety_l1_dimension", "safety_l2_harm_type"),
        ("dataset_name", "type"),
    ]

    for fields in coverage_passes:
        seen_buckets: set[Tuple[str, ...]] = set()
        for record in records:
            if len(selected) >= sample_size:
                return selected
            key = bucket_key(record, fields)
            if key in seen_buckets:
                continue
            seen_buckets.add(key)
            add_record(record)

    for record in records:
        if len(selected) >= sample_size:
            break
        add_record(record)
    return selected


def field_coverage(records: Iterable[Dict[str, Any]], fields: Sequence[str]) -> Dict[str, Dict[str, int]]:
    coverage: Dict[str, Dict[str, int]] = {}
    for field in fields:
        counter = Counter(normalize_text(record.get(field)) or "<missing>" for record in records)
        coverage[field] = dict(sorted(counter.items()))
    return coverage


def enabled_model_ids(protocol: Dict[str, Any]) -> List[str]:
    registry = protocol.get("models", {})
    models = registry.get("models", []) if isinstance(registry, dict) else []
    result: List[str] = []
    for model in models:
        if not isinstance(model, dict):
            continue
        if model.get("run_enabled") is False:
            continue
        model_id = normalize_text(model.get("model_id"))
        if model_id:
            result.append(model_id)
    return result


def build_run_config(
    *,
    repo_root: Path,
    canonical: Path,
    smoke_output: Path,
    run_root: Path,
    protocol_dir: Path,
    protocol: Dict[str, Any],
    sample_size: int,
    smoke_count: int,
    run_config: Path,
) -> Dict[str, Any]:
    protocol_card = protocol.get("protocol", {})
    metric = protocol.get("metric", {})
    eval_script = protocol_card.get("evaluation_script", {}) if isinstance(protocol_card, dict) else {}
    dataset = protocol_card.get("dataset", {}) if isinstance(protocol_card, dict) else {}
    config = {
        "phase": "phase3",
        "purpose": "protocol_shaping_smoke_subset",
        "protocol": {
            "protocol_dir": relpath(protocol_dir, repo_root),
            "protocol_version": protocol_card.get("protocol_version"),
            "taxonomy_version": protocol_card.get("taxonomy_version"),
            "metric_version": protocol_card.get("metric_version") or metric.get("version"),
            "prompt_version": eval_script.get("prompt_version", "prompt_only_v2"),
            "scorer_version": eval_script.get("scorer_version", metric.get("version", "rule_eval")),
        },
        "dataset": {
            "canonical_jsonl": relpath(canonical, repo_root),
            "canonical_sha256": sha256_file(canonical),
            "declared_total": dataset.get("total"),
            "smoke_subset_jsonl": relpath(smoke_output, repo_root),
            "sample_size_requested": sample_size,
            "sample_size_written": smoke_count,
            "selection_strategy": "deterministic_coverage_then_stable_order",
            "coverage_fields": list(COVERAGE_FIELDS),
        },
        "fields": {
            "id": "id",
            "prompt": "test_text",
            "reference": "reference_answer",
            "dataset": "dataset_name",
            "type": "type",
            "safety_l1_dimension": "safety_l1_dimension",
            "safety_l2_harm_type": "safety_l2_harm_type",
        },
        "prompt": {
            "version": eval_script.get("prompt_version", "prompt_only_v2"),
            "suffix": "请只回答最终结果，不要分析，不要解释。",
            "system_prompt": "",
            "use_eval_wrapper": True,
        },
        "scorer": {
            "version": eval_script.get("scorer_version", metric.get("version", "rule_eval")),
            "primary_metric": metric.get("primary_metric"),
            "refusal_scoring_mode": metric.get("refusal_scoring_mode"),
            "aggregation_mode": metric.get("aggregation_mode"),
        },
        "evaluator": {
            "script": EVALUATOR.as_posix(),
            "config_arg": "--config",
            "smoke_command": f"python {EVALUATOR.as_posix()} --config {relpath(run_config, repo_root)}",
        },
        "evaluator_config": {
            "input_jsonl": relpath(smoke_output, repo_root),
            "run_root": relpath(run_root, repo_root),
            "run_mode": "self_test",
            "id_field": "id",
            "prompt_field": "test_text",
            "reference_field": "reference_answer",
            "prompt_suffix": "请只回答最终结果，不要分析，不要解释。",
            "system_prompt": "",
            "use_eval_wrapper": True,
        },
        "enabled_model_ids": enabled_model_ids(protocol),
    }
    return config


def main() -> int:
    configure_stdio()
    parser = argparse.ArgumentParser(description="Prepare Phase 3 prompt/scorer protocol and deterministic smoke subset.")
    parser.add_argument("--canonical", type=Path, default=DEFAULT_CANONICAL)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--smoke-output", type=Path, default=DEFAULT_SMOKE)
    parser.add_argument("--run-config", type=Path, default=DEFAULT_RUN_CONFIG)
    parser.add_argument("--run-root", type=Path, default=DEFAULT_RUN_ROOT)
    parser.add_argument("--protocol-dir", type=Path, default=Path("protocol"))
    parser.add_argument("--sample-size", type=int, default=100)
    parser.add_argument("--format", choices=["text", "json"], default="text")
    args = parser.parse_args()

    repo_root = Path.cwd().resolve()
    canonical = args.canonical.expanduser().resolve()
    output_dir = args.output_dir.expanduser().resolve()
    smoke_output = args.smoke_output.expanduser().resolve()
    run_config = args.run_config.expanduser().resolve()
    run_root = args.run_root.expanduser().resolve()
    protocol_dir = args.protocol_dir.expanduser().resolve()

    if not canonical.is_file():
        raise FileNotFoundError(f"canonical dataset does not exist: {canonical}")
    validation = validate_protocol_dir(protocol_dir, repo_root)
    if not validation["valid"]:
        details = "; ".join(f"{item['path']}: {item['message']}" for item in validation.get("errors", [])[:8])
        raise ValueError(f"protocol is invalid: {details}")
    protocol = load_protocol(protocol_dir)

    records: List[Dict[str, Any]] = []
    errors: List[str] = []
    for line_number, record in enumerate(read_records(canonical), 1):
        error = validate_record(record, line_number)
        if error:
            errors.append(error)
            if len(errors) >= 20:
                break
            continue
        records.append(record)
    if errors:
        raise ValueError("canonical dataset is not Phase 3 ready: " + "; ".join(errors))

    subset = select_smoke_subset(records, args.sample_size)
    output_dir.mkdir(parents=True, exist_ok=True)
    smoke_count = write_jsonl(smoke_output, subset)
    run_config_doc = build_run_config(
        repo_root=repo_root,
        canonical=canonical,
        smoke_output=smoke_output,
        run_root=run_root,
        protocol_dir=protocol_dir,
        protocol=protocol,
        sample_size=args.sample_size,
        smoke_count=smoke_count,
        run_config=run_config,
    )
    run_config.parent.mkdir(parents=True, exist_ok=True)
    run_config.write_text(yaml.safe_dump(run_config_doc, sort_keys=False, allow_unicode=True), encoding="utf-8")

    report = {
        "phase": "phase3",
        "canonical": relpath(canonical, repo_root),
        "canonical_rows_read": len(records),
        "smoke_subset": relpath(smoke_output, repo_root),
        "smoke_rows_written": smoke_count,
        "run_config": relpath(run_config, repo_root),
        "selection_strategy": run_config_doc["dataset"]["selection_strategy"],
        "canonical_stats": compute_stats(records),
        "smoke_stats": compute_stats(subset),
        "smoke_coverage": field_coverage(subset, COVERAGE_FIELDS),
    }
    report_path = output_dir / "phase3_report.json"
    write_json(report_path, report)

    result = {
        "smoke_rows_written": smoke_count,
        "smoke_subset": relpath(smoke_output, repo_root),
        "run_config": relpath(run_config, repo_root),
        "report": relpath(report_path, repo_root),
    }
    if args.format == "json":
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"smoke_rows_written: {smoke_count}")
        print(f"smoke_subset: {result['smoke_subset']}")
        print(f"run_config: {result['run_config']}")
        print(f"report: {result['report']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
