#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, List

from phase12_common import (
    canonical_fields_from_schema,
    compute_stats,
    configure_stdio,
    default_canonical_schema_path,
    load_canonical_schema,
    load_json,
    load_phase_protocol,
    normalize_text,
    read_records,
    required_fields_from_schema,
    sha256_file,
    taxonomy_names,
    write_json,
)


def validate_canonical(
    canonical_schema: Dict[str, Any],
    canonical: Path,
    stats_path: Path,
    protocol_dir: Path,
) -> Dict[str, Any]:
    protocol = load_phase_protocol(protocol_dir)
    allowed_l1, allowed_l2 = taxonomy_names(protocol)
    canonical_fields = canonical_fields_from_schema(canonical_schema)
    required_fields = required_fields_from_schema(canonical_schema)
    errors: List[Dict[str, Any]] = []
    warnings: List[Dict[str, Any]] = []
    records: List[Dict[str, Any]] = []
    seen_ids: set[str] = set()

    for line_number, record in enumerate(read_records(canonical), 1):
        records.append(record)
        record_id = normalize_text(record.get("id"))
        if not record_id:
            errors.append({"code": "missing_id", "line": line_number, "message": "id is required"})
        elif record_id in seen_ids:
            errors.append({"code": "duplicate_id", "line": line_number, "id": record_id})
        else:
            seen_ids.add(record_id)
        for field in canonical_fields:
            if field in required_fields and normalize_text(record.get(field)) == "":
                errors.append({"code": "missing_field", "line": line_number, "field": field})
        extra_fields = sorted(set(record) - set(canonical_fields))
        if extra_fields:
            warnings.append({"code": "extra_fields", "line": line_number, "fields": extra_fields})
        if normalize_text(record.get("safety_l1_dimension")) not in allowed_l1:
            errors.append({"code": "unknown_taxonomy_l1", "line": line_number, "value": record.get("safety_l1_dimension")})
        if normalize_text(record.get("safety_l2_harm_type")) not in allowed_l2:
            errors.append({"code": "unknown_taxonomy_l2", "line": line_number, "value": record.get("safety_l2_harm_type")})

    computed_stats = compute_stats(records)
    expected_hash = sha256_file(canonical)
    protocol_dataset = protocol["protocol"].get("dataset", {})
    if protocol_dataset.get("total") != computed_stats["total"]:
        errors.append(
            {
                "code": "protocol_total_mismatch",
                "path": "protocol.dataset.total",
                "message": f"protocol={protocol_dataset.get('total')} actual={computed_stats['total']}",
            }
        )
    if str(protocol_dataset.get("sha256", "")).upper() != expected_hash:
        errors.append(
            {
                "code": "protocol_sha256_mismatch",
                "path": "protocol.dataset.sha256",
                "message": f"actual SHA-256 is {expected_hash}",
            }
        )

    stats_matches = False
    if stats_path.exists():
        existing_stats = load_json(stats_path)
        stats_matches = existing_stats == computed_stats
        if not stats_matches:
            errors.append({"code": "stats_mismatch", "path": str(stats_path), "message": "stats snapshot differs from canonical JSONL"})
    else:
        warnings.append({"code": "missing_stats", "path": str(stats_path), "message": "stats snapshot does not exist"})

    return {
        "valid": not errors,
        "canonical_schema_path": canonical_schema["schema_path"],
        "canonical": str(canonical),
        "canonical_sha256": expected_hash,
        "computed_stats": computed_stats,
        "stats_path": str(stats_path),
        "stats_matches": stats_matches,
        "errors": errors,
        "warnings": warnings,
    }


def main() -> int:
    configure_stdio()
    parser = argparse.ArgumentParser(description="Validate canonical JSONL, stats, taxonomy, and protocol identity.")
    parser.add_argument("--canonical-schema", type=Path, help="Canonical schema YAML. Defaults to protocol/canonical_schema.yaml.")
    parser.add_argument("--canonical", type=Path, default=Path("eval_mvp_V2") / "data" / "master_normalized_dataset_v2_final.jsonl")
    parser.add_argument("--stats", type=Path, default=Path("output") / "doc" / "v2_stats.json")
    parser.add_argument("--protocol-dir", type=Path, default=Path("protocol"))
    parser.add_argument("--write-stats", action="store_true", help="Rewrite stats from canonical before validation.")
    parser.add_argument("--report", type=Path, help="Optional JSON report path.")
    parser.add_argument("--format", choices=("text", "json"), default="text")
    args = parser.parse_args()

    try:
        if not args.canonical.is_file():
            raise ValueError(f"canonical JSONL does not exist: {args.canonical}")
        schema_path = (args.canonical_schema or default_canonical_schema_path(args.protocol_dir)).expanduser().resolve()
        canonical_schema = load_canonical_schema(schema_path)
        if args.write_stats:
            records = list(read_records(args.canonical))
            write_json(args.stats, compute_stats(records))
        result = validate_canonical(canonical_schema, args.canonical, args.stats, args.protocol_dir)
        if args.report:
            write_json(args.report, result)
        if args.format == "json":
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            print(f"valid: {str(result['valid']).lower()}")
            print(f"canonical_sha256: {result['canonical_sha256']}")
            print(f"total: {result['computed_stats']['total']}")
            for error in result["errors"][:20]:
                print(f"ERROR {error.get('code')}: {error.get('message') or error}")
            for warning in result["warnings"][:20]:
                print(f"WARNING {warning.get('code')}: {warning.get('message') or warning}")
        return 0 if result["valid"] else 1
    except Exception as exc:
        print(f"error: {exc}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
