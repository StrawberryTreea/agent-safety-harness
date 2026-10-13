from __future__ import annotations

from collections import defaultdict
import argparse
import json
from pathlib import Path
import sys
from typing import Any


PACKAGE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PACKAGE_ROOT))

from tools.build_stage_selections import (
    STRATIFICATION_FIELDS,
    balanced_sample,
    marginal_counts,
    read_jsonl,
    write_jsonl,
)


SEED = "product-federated-asr-v0.2-smoke5-ten-percent"
REMAINING_TWENTY_STAGE = "remaining20"
CUMULATIVE_STAGE = "cumulative28"
TEN_PERCENT_COUNTS = {
    "injecagent": 105,
    "msb": 200,
    "agentdojo": 63,
    "agentleak": 50,
    "saber": 72,
    "agentpoison_strategyqa": 49,
}
PRODUCTS = {
    "codex": ("codex", "0.122.0", "deepseek-v4-flash"),
    "claude_code": ("claude", "2.1.195", "deepseek-v4-flash"),
    "qoder": ("qodercli", "1.0.48", "${QODER_DEEPSEEK_MODEL_ID}"),
}


def _request(product_id: str, benchmark_id: str) -> dict[str, Any]:
    executable, version, model = PRODUCTS[product_id]
    return {
        "run_id": f"{product_id}-{version}-{benchmark_id}-v0.2",
        "benchmark_id": benchmark_id,
        "product": {
            "product_id": product_id,
            "executable": executable,
            "version": version,
            "product_model": model,
            "state_source": None,
        },
        "adapter": {
            "adapter_id": f"{benchmark_id}-product-adapter",
            "adapter_version": "0.2.0",
            "mcp_config": None,
        },
        "isolation": {
            "mode": "disposable_workspace",
            "fresh_product_state": True,
            "network_policy": "benchmark_defined",
            "tool_permission_mode": "autonomous_preapproved_in_sandbox",
        },
        "selection": {"case_index": f"../../../selections/v0.2/ten_percent/{benchmark_id}.jsonl"},
        "output_dir": f"../../../.codex_tmp/federated_asr_runs/v0.2/{product_id}/{benchmark_id}",
        "timeout_seconds": 600,
    }


def build_plan(index_path: Path, output_root: Path, request_root: Path) -> dict[str, Any]:
    by_benchmark: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in read_jsonl(index_path):
        by_benchmark[row["benchmark_id"]].append(row)
    if set(by_benchmark) != set(TEN_PERCENT_COUNTS):
        raise ValueError("case index benchmarks do not match the v0.2 plan")

    manifest: dict[str, Any] = {
        "schema_version": "0.2",
        "dataset_id": "product-federated-asr-v0.2",
        "seed": SEED,
        "shared_across_products": True,
        "smoke_is_subset_of_ten_percent": True,
        "remaining20_is_disjoint_from_ten_percent": True,
        "cumulative28_is_ten_percent_union_remaining20": True,
        "benchmarks": {},
    }
    combined_smoke: list[dict[str, Any]] = []
    combined_ten_percent: list[dict[str, Any]] = []
    combined_remaining20: list[dict[str, Any]] = []
    combined_cumulative28: list[dict[str, Any]] = []
    for benchmark_id, target in TEN_PERCENT_COUNTS.items():
        pool = by_benchmark[benchmark_id]
        fields = STRATIFICATION_FIELDS[benchmark_id]
        smoke = balanced_sample(pool, 5, fields, SEED, "smoke5")
        smoke_ids = {row["case_id"] for row in smoke}
        remainder = [row for row in pool if row["case_id"] not in smoke_ids]
        extension = balanced_sample(remainder, target - 5, fields, SEED, "ten_percent")
        ten_percent = smoke + extension
        ten_percent_ids = {row["case_id"] for row in ten_percent}
        untested = [row for row in pool if row["case_id"] not in ten_percent_ids]
        remaining20_target = (len(untested) + 2) // 5
        remaining20 = balanced_sample(
            untested, remaining20_target, fields, SEED, REMAINING_TWENTY_STAGE
        )
        cumulative28 = ten_percent + remaining20
        smoke_path = output_root / "smoke5" / f"{benchmark_id}.jsonl"
        ten_path = output_root / "ten_percent" / f"{benchmark_id}.jsonl"
        remaining20_path = output_root / REMAINING_TWENTY_STAGE / f"{benchmark_id}.jsonl"
        cumulative28_path = output_root / CUMULATIVE_STAGE / f"{benchmark_id}.jsonl"
        write_jsonl(smoke_path, smoke)
        write_jsonl(ten_path, ten_percent)
        write_jsonl(remaining20_path, remaining20)
        write_jsonl(cumulative28_path, cumulative28)
        combined_smoke.extend(smoke)
        combined_ten_percent.extend(ten_percent)
        combined_remaining20.extend(remaining20)
        combined_cumulative28.extend(cumulative28)
        manifest["benchmarks"][benchmark_id] = {
            "population_count": len(pool),
            "source_index_count": len(by_benchmark[benchmark_id]),
            "smoke5": {
                "case_count": len(smoke),
                "marginal_counts": marginal_counts(smoke, fields),
            },
            "ten_percent": {
                "case_count": len(ten_percent),
                "marginal_counts": marginal_counts(ten_percent, fields),
            },
            REMAINING_TWENTY_STAGE: {
                "population_basis": len(untested),
                "fraction_of_remaining": 0.2,
                "case_count": len(remaining20),
                "marginal_counts": marginal_counts(remaining20, fields),
            },
            CUMULATIVE_STAGE: {
                "case_count": len(cumulative28),
                "marginal_counts": marginal_counts(cumulative28, fields),
            },
        }
    for stage, rows in (
        ("smoke5", combined_smoke),
        ("ten_percent", combined_ten_percent),
        (REMAINING_TWENTY_STAGE, combined_remaining20),
        (CUMULATIVE_STAGE, combined_cumulative28),
    ):
        write_jsonl(output_root / stage / "all.jsonl", rows)

    request_root.mkdir(parents=True, exist_ok=True)
    for product_id in PRODUCTS:
        for benchmark_id in TEN_PERCENT_COUNTS:
            path = request_root / f"{product_id}_{benchmark_id}.json"
            path.write_text(
                json.dumps(_request(product_id, benchmark_id), ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
    manifest["request_count"] = len(PRODUCTS) * len(TEN_PERCENT_COUNTS)
    manifest_path = output_root / "manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description="Build the v0.2 smoke5 and ten-percent plan.")
    parser.add_argument("--case-index", type=Path, default=PACKAGE_ROOT / "case_index.jsonl")
    parser.add_argument("--output-root", type=Path, default=PACKAGE_ROOT / "selections" / "v0.2")
    parser.add_argument(
        "--request-root", type=Path, default=PACKAGE_ROOT / "examples" / "requests" / "v0.2"
    )
    args = parser.parse_args()
    manifest = build_plan(
        args.case_index.resolve(), args.output_root.resolve(), args.request_root.resolve()
    )
    print(
        json.dumps(
            {
                benchmark: stages["ten_percent"]["case_count"]
                for benchmark, stages in manifest["benchmarks"].items()
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
