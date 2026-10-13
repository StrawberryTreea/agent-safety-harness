from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PACKAGE_ROOT))

from tools.audit_smoke import BENCHMARKS, PRODUCTS, audit_cell


def _root_overrides(values: list[str]) -> dict[str, Path]:
    result: dict[str, Path] = {}
    for value in values:
        benchmark, separator, path = value.partition("=")
        if not separator or benchmark not in BENCHMARKS or not path:
            raise ValueError(
                "--benchmark-root must use BENCHMARK=PATH with a known benchmark"
            )
        result[benchmark] = Path(path).expanduser().resolve()
    return result


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Audit formal product-native ASR runs with optional per-benchmark roots."
    )
    parser.add_argument(
        "--run-root",
        type=Path,
        default=PACKAGE_ROOT.parent / "runs" / "product",
    )
    parser.add_argument(
        "--selection-root",
        type=Path,
        default=PACKAGE_ROOT / "selections" / "pilot100",
    )
    parser.add_argument("--benchmark-root", action="append", default=[])
    parser.add_argument("--benchmarks", nargs="+", choices=BENCHMARKS, default=list(BENCHMARKS))
    parser.add_argument("--products", nargs="+", choices=PRODUCTS, default=list(PRODUCTS))
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    run_root = args.run_root.resolve()
    selection_root = args.selection_root.resolve()
    try:
        overrides = _root_overrides(args.benchmark_root)
    except ValueError as exc:
        parser.error(str(exc))

    cells = {
        product: {
            benchmark: audit_cell(
                overrides.get(benchmark, run_root), selection_root, product, benchmark
            )
            for benchmark in args.benchmarks
        }
        for product in args.products
    }
    gate_failures = [
        f"{product} x {benchmark}"
        for product in args.products
        for benchmark in args.benchmarks
        if not cells[product][benchmark]["gate_pass"]
    ]
    report = {
        "schema_version": "0.2.1",
        "primary_metric": "ASR",
        "default_run_root": str(run_root),
        "benchmark_run_roots": {
            benchmark: str(overrides.get(benchmark, run_root))
            for benchmark in args.benchmarks
        },
        "selection_root": str(selection_root),
        "cells": cells,
        "gate_failures": gate_failures,
        "all_gates_pass": not gate_failures,
    }
    output = args.output or run_root / "formal_audit.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["all_gates_pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
