from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any


PACKAGE_ROOT = Path(__file__).resolve().parents[1]
WORKSPACE_ROOT = PACKAGE_ROOT.parent
sys.path.insert(0, str(PACKAGE_ROOT))

from product_eval.saber import SaberAdapter


DEFAULT_SABER_ROOT = WORKSPACE_ROOT / "数据集" / "评测数据" / "saber-main" / "saber-main"


def selected_saber_cases(case_index: Path) -> list[dict[str, Any]]:
    rows = []
    for line in case_index.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        record = json.loads(line)
        if record["benchmark_id"] == "saber":
            rows.append(record)
    return rows


def missing_result(
    *, run_id: str, product: dict[str, Any], case_id: str, expected_path: Path
) -> dict[str, Any]:
    return {
        "schema_version": "0.1",
        "run_id": run_id,
        "product": product,
        "benchmark_id": "saber",
        "case_id": case_id,
        "status": "invalid",
        "reason": "official SABER judged result is missing",
        "asr_contribution": {"numerator": 0, "denominator": 0, "unit": "case"},
        "official_evidence": {
            "source_file": str(expected_path),
            "native_field": "harmful",
            "native_value": None,
            "source_record_id": case_id.split(":", 1)[1],
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Convert official SABER judged files to case_result JSONL.")
    parser.add_argument("--run-id", required=True)
    parser.add_argument(
        "--product-id",
        required=True,
        choices=["codex", "claude_code", "qoder", "codebuddy"],
    )
    parser.add_argument("--product-version", required=True)
    parser.add_argument("--product-model")
    parser.add_argument("--product-slug", required=True, help="Directory name below SABER judged/")
    parser.add_argument("--saber-root", type=Path, default=DEFAULT_SABER_ROOT)
    parser.add_argument("--case-index", type=Path, default=PACKAGE_ROOT / "case_index.jsonl")
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()

    adapter = SaberAdapter(args.saber_root)
    product = {
        "product_id": args.product_id,
        "version": args.product_version,
        "product_model": args.product_model,
    }
    results = []
    for indexed in selected_saber_cases(args.case_index):
        native_id = indexed["native_case_id"]
        task_path = adapter.task_path(native_id)
        scenario = task_path.parts[-3]
        category = task_path.parts[-2]
        judged_path = args.saber_root / "judged" / args.product_slug / scenario / category / f"{native_id}.json"
        if judged_path.is_file():
            result = adapter.case_result_from_judged(
                run_id=args.run_id,
                product=product,
                case_id=indexed["case_id"],
                judged_path=judged_path,
            )
        else:
            result = missing_result(
                run_id=args.run_id,
                product=product,
                case_id=indexed["case_id"],
                expected_path=judged_path,
            )
        results.append(result)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in results),
        encoding="utf-8",
    )
    counts = {
        status: sum(row["status"] == status for row in results)
        for status in ("success", "failure", "excluded", "invalid")
    }
    print(json.dumps({"output": str(args.output), "case_count": len(results), "counts": counts}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
