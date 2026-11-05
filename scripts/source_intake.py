#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

try:
    import yaml
except ImportError as exc:  # pragma: no cover - deployment guard
    raise SystemExit("PyYAML is required. Install it with: python -m pip install -r requirements.txt") from exc

if str(Path(__file__).resolve().parents[1]) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.phase12_common import (
    benchmark_id_for_path,
    canonical_fields_from_schema,
    configure_stdio,
    count_records,
    default_canonical_schema_path,
    discover_data_files,
    discover_evidence_files,
    infer_schema,
    load_canonical_schema,
    load_intake_config,
    load_phase_protocol,
    mapping_required_fields_from_schema,
    normalize_text,
    probe_mapping,
    required_fields_from_schema,
    resolve_path,
    sha256_file,
    suggest_field_mapping,
    write_json,
    write_jsonl,
)


STATUS_READY = "ready"
STATUS_NEEDS_REVIEW = "needs_review"
STATUS_FAILED = "failed"


def _field_names_from_mapping(field_mapping: Dict[str, str]) -> List[str]:
    return sorted({str(value) for value in field_mapping.values() if isinstance(value, str) and value.strip()})


def _benchmark_override(
    intake_config: Dict[str, Any],
    benchmark_id: str,
    source_path: Path,
) -> Dict[str, Any]:
    overrides = intake_config.get("benchmark_overrides")
    if not isinstance(overrides, dict):
        return {}
    for key in (benchmark_id, source_path.name, str(source_path)):
        entry = overrides.get(key)
        if isinstance(entry, dict):
            return entry
    return {}


def _benchmark_source_row(
    benchmark_id: str,
    dataset_name: str,
    source_id: str,
    source_path: Path,
    schema: Dict[str, Any],
    field_mapping: Dict[str, str],
    mapping_report: Dict[str, Any],
) -> Dict[str, Any]:
    return {
        "benchmark_id": benchmark_id,
        "dataset_name": dataset_name,
        "source_id": source_id,
        "asset_role": "benchmark",
        "asset_path": str(source_path),
        "asset_format": source_path.suffix.lstrip("."),
        "sha256": sha256_file(source_path),
        "byte_size": source_path.stat().st_size,
        "row_count": count_records(source_path),
        "observed_fields": "|".join(sorted(schema.get("fields", {}).keys())),
        "mapped_fields": "|".join(_field_names_from_mapping(field_mapping)),
        "mapping_complete": mapping_report["complete"],
        "notes": "",
    }


def _evidence_row(
    asset_path: Path,
    evidence_role: str,
    notes: str = "",
    benchmark_id: str = "",
    dataset_name: str = "",
    source_id: str = "",
    index: int = 1,
) -> Dict[str, Any]:
    return {
        "benchmark_id": benchmark_id,
        "dataset_name": dataset_name,
        "source_id": source_id,
        "asset_role": "evidence",
        "evidence_role": evidence_role,
        "asset_path": str(asset_path),
        "sha256": sha256_file(asset_path),
        "byte_size": asset_path.stat().st_size,
        "notes": notes,
        "index": index,
    }


def _catalog_entry(
    benchmark_id: str,
    dataset_name: str,
    source_id: str,
    source_row: Dict[str, Any],
    evidence_rows: List[Dict[str, Any]],
    field_mapping: Dict[str, str],
    mapping_report: Dict[str, Any],
    canonical_schema: Dict[str, Any],
) -> Dict[str, Any]:
    return {
        "benchmark_id": benchmark_id,
        "dataset_name": dataset_name,
        "source_id": source_id,
        "status": STATUS_READY if mapping_report["complete"] else STATUS_NEEDS_REVIEW,
        "source": {
            "path": source_row["asset_path"],
            "format": source_row["asset_format"],
            "sha256": source_row["sha256"],
            "row_count": source_row["row_count"],
        },
        "evidence": [
            {
                "path": row["asset_path"],
                "role": row["evidence_role"],
                "sha256": row["sha256"],
            }
            for row in evidence_rows
        ],
        "canonical_schema": {
            "schema_path": canonical_schema["schema_path"],
            "schema_name": canonical_schema["schema_name"],
            "version": canonical_schema["version"],
            "fields": canonical_fields_from_schema(canonical_schema),
            "required_fields": required_fields_from_schema(canonical_schema),
        },
        "field_mapping": field_mapping,
        "schema_probe": {
            "observed_fields": source_row["observed_fields"].split("|") if source_row["observed_fields"] else [],
            "mapping_report": mapping_report,
        },
        "quality_flags": [] if mapping_report["complete"] else ["mapping_incomplete"],
        "provenance": {
            "source_path": source_row["asset_path"],
            "source_sha256": source_row["sha256"],
            "evidence_count": len(evidence_rows),
            "source_id": source_id,
        },
    }


def _review_entry(
    benchmark_id: str,
    dataset_name: str,
    source_id: str,
    source_row: Dict[str, Any],
    mapping_report: Dict[str, Any],
    field_mapping: Dict[str, str],
) -> Dict[str, Any]:
    return {
        "benchmark_id": benchmark_id,
        "dataset_name": dataset_name,
        "source_id": source_id,
        "status": STATUS_NEEDS_REVIEW,
        "source_path": source_row["asset_path"],
        "missing_canonical_fields": mapping_report["missing_canonical_fields"],
        "ambiguous_mappings": mapping_report["ambiguous_mappings"],
        "unmapped_source_fields": mapping_report["unmapped_raw_fields"],
        "suggested_field_mapping": field_mapping,
        "instruction": "Fill the generated manifests/pending/<benchmark>.pending.yaml, then rerun source_intake with the completed mapping as intake config.",
        "requires_human_review": not mapping_report["complete"],
    }


def _failed_entry(source_path: Path, benchmark_root: Path, exc: Exception) -> Dict[str, Any]:
    return {
        "benchmark_id": benchmark_id_for_path(benchmark_root, source_path),
        "status": STATUS_FAILED,
        "source_path": str(source_path),
        "asset_format": source_path.suffix.lstrip("."),
        "sha256": sha256_file(source_path) if source_path.is_file() else None,
        "byte_size": source_path.stat().st_size if source_path.is_file() else None,
        "error_type": type(exc).__name__,
        "error": str(exc),
    }


def _pending_manifest_document(
    benchmark_id: str,
    dataset_name: str,
    source_id: str,
    source_row: Dict[str, Any],
    canonical_schema: Dict[str, Any],
    field_mapping: Dict[str, str],
    mapping_report: Dict[str, Any],
) -> Dict[str, Any]:
    required_fields = required_fields_from_schema(canonical_schema)
    missing_fields = mapping_report["missing_canonical_fields"]
    ambiguous_fields = set(mapping_report["ambiguous_mappings"])
    unresolved_fields = set(missing_fields) | ambiguous_fields
    return {
        "benchmark": benchmark_id,
        "status": STATUS_NEEDS_REVIEW,
        "dataset_name": dataset_name,
        "source_id": source_id,
        "source_path": source_row["asset_path"],
        "suggested_mapping": {
            field: field_mapping[field]
            for field in required_fields
            if field in field_mapping and field not in unresolved_fields
        },
        "missing_required_fields": {field: None for field in missing_fields},
        "ambiguous_mappings": mapping_report["ambiguous_mappings"],
        "unmapped_source_fields": mapping_report["unmapped_raw_fields"],
        "field_mapping": {
            field: None if field in ambiguous_fields else field_mapping.get(field)
            for field in required_fields
        },
    }


def _write_pending_manifest(path: Path, document: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(document, sort_keys=False, allow_unicode=True), encoding="utf-8")


def _mapping_report_entry(
    catalog_entry: Dict[str, Any],
    source_row: Dict[str, Any],
    mapping_report: Dict[str, Any],
    field_mapping: Dict[str, str],
) -> Dict[str, Any]:
    return {
        "benchmark_id": catalog_entry["benchmark_id"],
        "dataset_name": catalog_entry["dataset_name"],
        "source_id": catalog_entry["source_id"],
        "status": catalog_entry["status"],
        "source_path": source_row["asset_path"],
        "observed_fields": source_row["observed_fields"].split("|") if source_row["observed_fields"] else [],
        "field_mapping": field_mapping,
        "missing_required_fields": mapping_report["missing_canonical_fields"],
        "ambiguous_mappings": mapping_report["ambiguous_mappings"],
        "unmapped_source_fields": mapping_report["unmapped_raw_fields"],
    }


def _write_field_mapping_report(
    path: Path,
    ready_entries: List[Dict[str, Any]],
    review_entries: List[Dict[str, Any]],
    failed_entries: List[Dict[str, Any]],
) -> None:
    write_json(
        path,
        {
            "summary": {
                "total_files": len(ready_entries) + len(review_entries) + len(failed_entries),
                "ready": len(ready_entries),
                "needs_review": len(review_entries),
                "failed": len(failed_entries),
            },
            "ready": ready_entries,
            "needs_review": review_entries,
            "failed": failed_entries,
        },
    )


def write_inventory_csv(path: Path, rows: List[Dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "benchmark_id",
        "dataset_name",
        "source_id",
        "asset_role",
        "asset_path",
        "asset_format",
        "sha256",
        "byte_size",
        "row_count",
        "observed_fields",
        "mapped_fields",
        "mapping_complete",
        "notes",
    ]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _process_benchmark(
    source_path: Path,
    benchmark_root: Path,
    canonical_schema: Dict[str, Any],
    intake_config: Dict[str, Any],
    sample_size: int,
) -> Tuple[Dict[str, Any], Dict[str, Any], List[Dict[str, Any]], Dict[str, str], Dict[str, Any]]:
    benchmark_id = benchmark_id_for_path(benchmark_root, source_path)
    override = _benchmark_override(intake_config, benchmark_id, source_path)
    dataset_name = normalize_text(override.get("dataset_name")) or benchmark_id
    source_id = normalize_text(override.get("source_id")) or benchmark_id

    schema = infer_schema(source_path, sample_size)
    if not schema.get("fields"):
        raise ValueError("no structured records or fields could be inferred")
    raw_fields = schema.get("fields", {}).keys()
    canonical_fields = canonical_fields_from_schema(canonical_schema)
    mapping_required_fields = mapping_required_fields_from_schema(canonical_schema)
    override_mapping = override.get("field_mapping") if isinstance(override.get("field_mapping"), dict) else {}
    field_mapping = suggest_field_mapping(raw_fields, canonical_fields, overrides=override_mapping)
    mapping_report = probe_mapping(
        mapping_required_fields,
        raw_fields,
        field_mapping,
        overrides=override_mapping,
    )

    source_row = _benchmark_source_row(
        benchmark_id,
        dataset_name,
        source_id,
        source_path,
        schema,
        field_mapping,
        mapping_report,
    )
    catalog_entry = _catalog_entry(
        benchmark_id,
        dataset_name,
        source_id,
        source_row,
        [],
        field_mapping,
        mapping_report,
        canonical_schema,
    )
    return source_row, catalog_entry, [], field_mapping, mapping_report


def _collect_config_evidence(
    intake_config: Dict[str, Any],
    config_dir: Path,
) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for index, item in enumerate(intake_config.get("evidence", []), 1):
        raw_path = item.get("path")
        if not isinstance(raw_path, str) or not raw_path.strip():
            continue
        path = resolve_path(config_dir, Path(raw_path))
        if not path.is_file():
            raise FileNotFoundError(f"evidence file does not exist: {path}")
        rows.append(
            _evidence_row(
                path,
                normalize_text(item.get("role")) or "whitepaper",
                normalize_text(item.get("notes")) or "",
                index=index,
            )
        )
    return rows


def main(argv: list[str] | None = None) -> int:
    configure_stdio()
    parser = argparse.ArgumentParser(
        description="Phase 1 source intake: discover benchmarks and evidence, auto-map fields, emit catalog."
    )
    parser.add_argument("--benchmark-root", type=Path, help="Root directory containing benchmark data files.")
    parser.add_argument("--evidence-root", type=Path, help="Root directory containing whitepaper/evidence files.")
    parser.add_argument("--intake-config", type=Path, help="Optional YAML/JSON overrides for mapping and evidence.")
    parser.add_argument("--canonical-schema", type=Path, help="Canonical schema YAML. Defaults to protocol/canonical_schema.yaml.")
    parser.add_argument("--output-dir", type=Path, default=Path("work") / "phase1")
    parser.add_argument("--protocol-dir", type=Path, default=Path("protocol"))
    parser.add_argument("--sample-size", type=int, default=100)
    parser.add_argument("--format", choices=("text", "json"), default="text")
    args = parser.parse_args(argv)

    try:
        if args.benchmark_root is None:
            raise ValueError("--benchmark-root is required")

        protocol = load_phase_protocol(args.protocol_dir)
        schema_path = (args.canonical_schema or default_canonical_schema_path(args.protocol_dir)).expanduser().resolve()
        canonical_schema = load_canonical_schema(schema_path)
        intake_config = load_intake_config(args.intake_config.expanduser().resolve() if args.intake_config else None)
        config_dir = args.intake_config.expanduser().resolve().parent if args.intake_config else Path.cwd()

        benchmark_root = args.benchmark_root.expanduser().resolve()
        benchmark_paths = discover_data_files(benchmark_root)
        if not benchmark_paths:
            raise ValueError(f"no benchmark data files found under {benchmark_root}")

        inventory_rows: List[Dict[str, Any]] = []
        catalog_entries: List[Dict[str, Any]] = []
        ready_reports: List[Dict[str, Any]] = []
        needs_review: List[Dict[str, Any]] = []
        failed_files: List[Dict[str, Any]] = []

        for source_path in benchmark_paths:
            try:
                source_row, catalog_entry, _, field_mapping, mapping_report = _process_benchmark(
                    source_path,
                    benchmark_root,
                    canonical_schema,
                    intake_config,
                    args.sample_size,
                )
                inventory_rows.append(source_row)
                report_entry = _mapping_report_entry(catalog_entry, source_row, mapping_report, field_mapping)
                if mapping_report["complete"]:
                    catalog_entries.append(catalog_entry)
                    ready_reports.append(report_entry)
                else:
                    review_entry = _review_entry(
                        catalog_entry["benchmark_id"],
                        catalog_entry["dataset_name"],
                        catalog_entry["source_id"],
                        source_row,
                        mapping_report,
                        field_mapping,
                    )
                    needs_review.append(review_entry)
                    pending_path = (
                        args.output_dir.resolve()
                        / "manifests"
                        / "pending"
                        / f"{catalog_entry['benchmark_id']}.pending.yaml"
                    )
                    _write_pending_manifest(
                        pending_path,
                        _pending_manifest_document(
                            catalog_entry["benchmark_id"],
                            catalog_entry["dataset_name"],
                            catalog_entry["source_id"],
                            source_row,
                            canonical_schema,
                            field_mapping,
                            mapping_report,
                        ),
                    )
            except Exception as exc:
                failed_files.append(_failed_entry(source_path, benchmark_root, exc))

        evidence_rows = _collect_config_evidence(intake_config, config_dir)
        if args.evidence_root is not None:
            evidence_root = args.evidence_root.expanduser().resolve()
            start_index = len(evidence_rows) + 1
            for offset, path in enumerate(discover_evidence_files(evidence_root), start_index):
                evidence_rows.append(_evidence_row(path, "whitepaper", index=offset))
        inventory_rows.extend(evidence_rows)

        output_dir = args.output_dir.resolve()
        inventory_path = output_dir / "source_inventory.csv"
        evidence_inventory_path = output_dir / "evidence_inventory.jsonl"
        catalog_path = output_dir / "dataset_catalog.json"
        reports_dir = output_dir / "reports"
        field_mapping_report_path = reports_dir / "field_mapping_report.json"
        needs_review_path = reports_dir / "needs_review.json"
        failed_files_path = reports_dir / "failed_files.json"
        write_inventory_csv(inventory_path, inventory_rows)
        write_jsonl(evidence_inventory_path, evidence_rows)
        write_json(
            catalog_path,
            {
                "phase": "phase1",
                "benchmark_root": str(benchmark_root),
                "evidence_root": str(args.evidence_root.resolve()) if args.evidence_root else None,
                "intake_config": intake_config.get("config_path"),
                "protocol_version": protocol["protocol"].get("protocol_version"),
                "taxonomy_version": protocol["taxonomy"].get("version"),
                "metric_version": protocol["metric"].get("version"),
                "canonical_schema": {
                    "schema_path": canonical_schema["schema_path"],
                    "schema_name": canonical_schema["schema_name"],
                    "version": canonical_schema["version"],
                    "fields": canonical_fields_from_schema(canonical_schema),
                    "required_fields": required_fields_from_schema(canonical_schema),
                },
                "status_policy": {
                    "ready": "Required mapping fields are resolved and the benchmark can enter Phase 2.",
                    "needs_review": "Source data is readable, but required field mapping is incomplete.",
                    "failed": "Source data could not be read or its record structure could not be inferred.",
                },
                "benchmarks": catalog_entries,
            },
        )
        _write_field_mapping_report(field_mapping_report_path, ready_reports, needs_review, failed_files)
        write_json(needs_review_path, needs_review)
        write_json(failed_files_path, failed_files)
        result = {
            "ok": True,
            "benchmarks": len(catalog_entries),
            "ready": len(catalog_entries),
            "needs_review": len(needs_review),
            "failed": len(failed_files),
            "evidence_assets": len(evidence_rows),
            "outputs": {
                "source_inventory": str(inventory_path),
                "evidence_inventory": str(evidence_inventory_path),
                "dataset_catalog": str(catalog_path),
                "field_mapping_report": str(field_mapping_report_path),
                "needs_review": str(needs_review_path),
                "failed_files": str(failed_files_path),
                "pending_manifests": str(output_dir / "manifests" / "pending"),
            },
        }
        if args.format == "json":
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            print(f"benchmarks: {result['benchmarks']}")
            print(f"ready: {result['ready']}")
            print(f"needs_review: {result['needs_review']}")
            print(f"failed: {result['failed']}")
            print(f"evidence_assets: {result['evidence_assets']}")
            print(f"dataset_catalog: {catalog_path}")
            print(f"source_inventory: {inventory_path}")
            print(f"evidence_inventory: {evidence_inventory_path}")
            print(f"field_mapping_report: {field_mapping_report_path}")
            print(f"needs_review: {needs_review_path}")
            print(f"failed_files: {failed_files_path}")
        return 0
    except Exception as exc:
        print(f"error: {exc}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
