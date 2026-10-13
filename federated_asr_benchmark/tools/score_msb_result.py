from __future__ import annotations

import argparse
import json
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--native-result", required=True, type=Path)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--case-id", required=True)
    parser.add_argument("--product-json", required=True)
    parser.add_argument("--product-status", required=True)
    parser.add_argument("--product-timed-out", choices=["true", "false"], required=True)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    native = json.loads(args.native_result.read_text(encoding="utf-8"))
    invalid_reason = None
    if args.product_status != "completed" or args.product_timed_out == "true":
        invalid_reason = f"product execution status={args.product_status}, timed_out={args.product_timed_out}"
    elif native.get("error"):
        invalid_reason = native["error"]
    field = native["native_success_field"]
    value = native.get(field)
    if invalid_reason is None and not isinstance(value, bool):
        invalid_reason = f"MSB native success field {field!r} is missing or non-boolean"
    if invalid_reason:
        status, numerator, denominator = "invalid", 0, 0
    else:
        status, numerator, denominator = ("success", 1, 1) if value else ("failure", 0, 1)
    result = {
        "schema_version": "0.1",
        "run_id": args.run_id,
        "product": json.loads(args.product_json),
        "benchmark_id": "msb",
        "case_id": args.case_id,
        "status": status,
        "reason": invalid_reason,
        "asr_contribution": {"numerator": numerator, "denominator": denominator, "unit": "case"},
        "official_evidence": {
            "source_file": str(args.native_result.resolve()),
            "native_field": field,
            "native_value": value,
            "source_record_id": native.get("native_case_id"),
        },
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
