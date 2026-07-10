#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Any, Dict, Iterable, List

from phase12_common import (
    canonical_fields_from_schema,
    compute_stats,
    configure_stdio,
    default_canonical_schema_path,
    load_canonical_schema,
    load_phase_protocol,
    normalize_text,
    read_records,
    required_fields_from_schema,
    sha256_file,
    stable_record_id,
    taxonomy_names,
    write_json,
)


def _catalog_benchmarks(catalog: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    return {
        str(item.get("benchmark_id")): item
        for item in catalog.get("benchmarks", [])
        if isinstance(item, dict) and str(item.get("benchmark_id")).strip()
    }


def _is_structured_source(path: Path) -> bool:
    return path.suffix.lower() in {".jsonl", ".json", ".csv", ".tsv"}


def _mapping_complete(entry: Dict[str, Any]) -> bool:
    report = entry.get("schema_probe", {}).get("mapping_report", {})
    return bool(report.get("complete"))


def _coerce_schema_value(value: Any, field_type: str, field: str) -> Any:
    if value is None:
        return None
    normalized_type = field_type.lower()
    if normalized_type in {"str", "string"}:
        return normalize_text(value)
    if normalized_type in {"list", "array"}:
        candidate = json.loads(value) if isinstance(value, str) else value
        if not isinstance(candidate, list):
            raise ValueError(f"{field} expected list, got {type(candidate).__name__}")
        return candidate
    if normalized_type in {"dict", "object", "map"}:
        candidate = json.loads(value) if isinstance(value, str) else value
        if not isinstance(candidate, dict):
            raise ValueError(f"{field} expected object, got {type(candidate).__name__}")
        return candidate
    if normalized_type in {"int", "integer"}:
        if isinstance(value, bool):
            raise ValueError(f"{field} expected integer, got bool")
        return int(value)
    if normalized_type in {"float", "number"}:
        if isinstance(value, bool):
            raise ValueError(f"{field} expected number, got bool")
        return float(value)
    if normalized_type in {"bool", "boolean"}:
        if isinstance(value, bool):
            return value
        if isinstance(value, str) and value.strip().lower() in {"true", "false"}:
            return value.strip().lower() == "true"
        raise ValueError(f"{field} expected boolean, got {type(value).__name__}")
    raise ValueError(f"{field} has unsupported canonical type {field_type!r}")


def _canonicalize_record(
    raw: Dict[str, Any],
    benchmark_entry: Dict[str, Any],
    canonical_fields: List[str],
    field_types: Dict[str, str],
    source_record_number: int,
) -> Dict[str, Any]:
    field_mapping = benchmark_entry.get("field_mapping") if isinstance(benchmark_entry.get("field_mapping"), dict) else {}
    record: Dict[str, Any] = {
        field: "" if field_types.get(field, "str").lower() in {"str", "string"} else None
        for field in canonical_fields
    }
    dataset_name = normalize_text(benchmark_entry.get("dataset_name")) or normalize_text(benchmark_entry.get("benchmark_id"))
    benchmark_id = normalize_text(benchmark_entry.get("benchmark_id")) or dataset_name
    generated_source_id = f"{benchmark_id}:{source_record_number:08d}"

    for field in canonical_fields:
        raw_field = field_mapping.get(field)
        if isinstance(raw_field, str) and raw_field in raw:
            record[field] = _coerce_schema_value(raw.get(raw_field), field_types.get(field, "str"), field)

    if "dataset_name" in record and not normalize_text(record["dataset_name"]):
        record["dataset_name"] = dataset_name
    if "source_id" in record and not normalize_text(record["source_id"]):
        record["source_id"] = generated_source_id

    if "id" in record and not normalize_text(record["id"]):
        test_text = record.get("test_text", "")
        reference_answer = record.get("reference_answer", "")
        record["id"] = stable_record_id(dataset_name, record.get("source_id", generated_source_id), test_text, reference_answer)

    for field in canonical_fields:
        if field_types.get(field, "str").lower() in {"str", "string"}:
            record[field] = normalize_text(record.get(field))
    return record


def _validate_record(record: Dict[str, Any], required_fields: List[str], allowed_l1: set[str], allowed_l2: set[str]) -> List[str]:
    errors: List[str] = []
    for field in required_fields:
        if normalize_text(record.get(field)) == "":
            errors.append(f"missing {field}")
    if normalize_text(record.get("safety_l1_dimension")) not in allowed_l1:
        errors.append("unknown safety_l1_dimension")
    if normalize_text(record.get("safety_l2_harm_type")) not in allowed_l2:
        errors.append("unknown safety_l2_harm_type")
    return errors


def _source_summary_row(
    benchmark_entry: Dict[str, Any],
    source_path: Path,
    row_count: int | None,
    canonical_rows: int,
    quarantined_rows: int,
) -> Dict[str, Any]:
    return {
        "dataset_name": normalize_text(benchmark_entry.get("dataset_name")) or normalize_text(benchmark_entry.get("benchmark_id")),
        "benchmark_id": normalize_text(benchmark_entry.get("benchmark_id")) or source_path.stem,
        "source_id": normalize_text(benchmark_entry.get("source_id")) or normalize_text(benchmark_entry.get("benchmark_id")) or source_path.stem,
        "source_path": str(source_path),
        "sha256": sha256_file(source_path),
        "row_count": row_count,
        "canonical_rows": canonical_rows,
        "quarantined_rows": quarantined_rows,
    }


def _assert_source_hash_matches_catalog(benchmark_id: str, source_path: Path, source_spec: Dict[str, Any]) -> str:
    expected_hash = normalize_text(source_spec.get("sha256")).upper()
    if not expected_hash:
        raise ValueError(f"benchmark {benchmark_id} is missing Phase 1 source sha256 in dataset_catalog.json")
    actual_hash = sha256_file(source_path)
    if actual_hash != expected_hash:
        raise ValueError(
            "source hash mismatch for "
            f"{benchmark_id}: catalog sha256={expected_hash}, actual sha256={actual_hash}, path={source_path}"
        )
    return actual_hash


def _write_dataset_sources_clean(path: Path, rows: Iterable[Dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = ["dataset_name", "benchmark_id", "source_id", "source_path", "sha256", "row_count", "canonical_rows", "quarantined_rows"]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    configure_stdio()
    parser = argparse.ArgumentParser(description="Phase 2 canonical standardization from Phase 1 catalog and canonical schema.")
    parser.add_argument("--catalog", type=Path, required=True, help="Phase 1 dataset_catalog.json.")
    parser.add_argument("--canonical-schema", type=Path, help="Canonical schema YAML. Defaults to protocol/canonical_schema.yaml.")
    parser.add_argument("--output", type=Path, default=Path("eval_mvp_V2") / "data" / "master_normalized_dataset_v2_final.jsonl")
    parser.add_argument("--stats", type=Path, default=Path("output") / "doc" / "v2_stats.json")
    parser.add_argument("--dataset-sources-clean", type=Path, default=Path("work") / "phase2" / "dataset_sources_clean.csv")
    parser.add_argument("--quarantine", type=Path, default=Path("work") / "phase2" / "quarantine.jsonl")
    parser.add_argument("--membership-diff", type=Path, default=Path("work") / "phase2" / "membership_diff.json")
    parser.add_argument("--report", type=Path, default=Path("work") / "phase2" / "canonicalization_report.json")
    parser.add_argument("--protocol-dir", type=Path, default=Path("protocol"))
    parser.add_argument("--format", choices=("text", "json"), default="text")
    args = parser.parse_args()

    try:
        protocol = load_phase_protocol(args.protocol_dir)
        allowed_l1, allowed_l2 = taxonomy_names(protocol)
        schema_path = (args.canonical_schema or default_canonical_schema_path(args.protocol_dir)).expanduser().resolve()
        canonical_schema = load_canonical_schema(schema_path)
        canonical_fields = canonical_fields_from_schema(canonical_schema)
        field_types = canonical_schema["field_types"]
        required_fields = required_fields_from_schema(canonical_schema)

        catalog = json.loads(args.catalog.read_text(encoding="utf-8-sig"))
        catalog_benchmarks = _catalog_benchmarks(catalog)
        if not catalog_benchmarks:
            raise ValueError("catalog contains no benchmarks")

        previous_ids: set[str] = set()
        if args.output.exists():
            previous_ids = {normalize_text(record.get("id")) for record in read_records(args.output) if normalize_text(record.get("id"))}

        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.quarantine.parent.mkdir(parents=True, exist_ok=True)
        canonical_rows: List[Dict[str, Any]] = []
        quarantined_rows = 0
        source_rows: List[Dict[str, Any]] = []
        with args.quarantine.open("w", encoding="utf-8", newline="\n") as quarantine_handle:
            for benchmark_id, catalog_entry in catalog_benchmarks.items():
                if not _mapping_complete(catalog_entry):
                    raise ValueError(
                        f"benchmark mapping incomplete for {benchmark_id}; fix mapping_suggestions.jsonl or intake_config before Phase 2"
                    )

                source_spec = catalog_entry.get("source", {})
                source_path = Path(str(source_spec.get("path", "")))
                if not source_path.is_file():
                    raise FileNotFoundError(f"benchmark source does not exist: {source_path}")
                if not _is_structured_source(source_path):
                    raise ValueError(f"benchmark source must be structured data: {source_path}")
                _assert_source_hash_matches_catalog(benchmark_id, source_path, source_spec)

                source_canonical = 0
                source_quarantined = 0
                source_row_count = source_spec.get("row_count")
                for source_record_number, raw in enumerate(read_records(source_path), 1):
                    try:
                        record = _canonicalize_record(
                            raw,
                            catalog_entry,
                            canonical_fields,
                            field_types,
                            source_record_number,
                        )
                    except (TypeError, ValueError, json.JSONDecodeError) as exc:
                        quarantine_handle.write(
                            json.dumps(
                                {
                                    "benchmark_id": benchmark_id,
                                    "source_id": catalog_entry.get("source_id"),
                                    "dataset_name": catalog_entry.get("dataset_name"),
                                    "errors": [f"schema type conversion failed: {exc}"],
                                    "raw": raw,
                                    "candidate": None,
                                },
                                ensure_ascii=False,
                            )
                            + "\n"
                        )
                        quarantined_rows += 1
                        source_quarantined += 1
                        continue
                    errors = _validate_record(record, required_fields, allowed_l1, allowed_l2)
                    if record["id"] in {row["id"] for row in canonical_rows}:
                        errors.append("duplicate id")
                    if errors:
                        quarantine_handle.write(
                            json.dumps(
                                {
                                    "benchmark_id": benchmark_id,
                                    "source_id": catalog_entry.get("source_id"),
                                    "dataset_name": catalog_entry.get("dataset_name"),
                                    "errors": errors,
                                    "raw": raw,
                                    "candidate": record,
                                },
                                ensure_ascii=False,
                            )
                            + "\n"
                        )
                        quarantined_rows += 1
                        source_quarantined += 1
                        continue
                    canonical_rows.append(record)
                    source_canonical += 1

                source_rows.append(
                    _source_summary_row(
                        catalog_entry,
                        source_path,
                        source_row_count,
                        source_canonical,
                        source_quarantined,
                    )
                )

        canonical_rows.sort(key=lambda row: row["id"])
        with args.output.open("w", encoding="utf-8", newline="\n") as handle:
            for row in canonical_rows:
                handle.write(json.dumps(row, ensure_ascii=False) + "\n")
        stats = compute_stats(canonical_rows)
        write_json(args.stats, stats)
        _write_dataset_sources_clean(args.dataset_sources_clean, source_rows)
        current_ids = {row["id"] for row in canonical_rows}
        membership_diff = {
            "previous_total": len(previous_ids),
            "current_total": len(current_ids),
            "added_total": len(current_ids - previous_ids),
            "removed_total": len(previous_ids - current_ids),
            "unchanged_total": len(current_ids & previous_ids),
            "added_ids": sorted(current_ids - previous_ids)[:1000],
            "removed_ids": sorted(previous_ids - current_ids)[:1000],
            "truncated": len(current_ids - previous_ids) > 1000 or len(previous_ids - current_ids) > 1000,
        }
        write_json(args.membership_diff, membership_diff)
        dataset_hash = sha256_file(args.output)
        protocol_dataset = protocol["protocol"].get("dataset", {})
        report = {
            "ok": True,
            "catalog_path": str(args.catalog.resolve()),
            "canonical_schema_path": str(schema_path),
            "canonical_rows": len(canonical_rows),
            "quarantined_rows": quarantined_rows,
            "canonical_path": str(args.output),
            "canonical_sha256": dataset_hash,
            "stats_path": str(args.stats),
            "protocol_dataset": protocol_dataset,
            "protocol_drift": {
                "total_changed": protocol_dataset.get("total") != len(canonical_rows),
                "sha256_changed": str(protocol_dataset.get("sha256", "")).upper() != dataset_hash,
            },
            "outputs": {
                "canonical": str(args.output),
                "stats": str(args.stats),
                "dataset_sources_clean": str(args.dataset_sources_clean),
                "quarantine": str(args.quarantine),
                "membership_diff": str(args.membership_diff),
                "report": str(args.report),
            },
        }
        write_json(args.report, report)
        if args.format == "json":
            print(json.dumps(report, ensure_ascii=False, indent=2))
        else:
            print(f"canonical_rows: {len(canonical_rows)}")
            print(f"quarantined_rows: {quarantined_rows}")
            print(f"canonical_sha256: {dataset_hash}")
            print(f"report: {args.report}")
        return 0
    except Exception as exc:
        print(f"error: {exc}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
