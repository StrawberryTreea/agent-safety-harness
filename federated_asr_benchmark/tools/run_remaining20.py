from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Any


PACKAGE_ROOT = Path(__file__).resolve().parents[1]
RUN_BATCH = PACKAGE_ROOT / "tools" / "run_product_batch.py"
SELECTION_ROOT = PACKAGE_ROOT / "selections" / "v0.2" / "remaining20"
RUN_ROOT = PACKAGE_ROOT / ".codex_tmp" / "federated_asr_runs"
BENCHMARKS = (
    "injecagent",
    "msb",
    "agentdojo",
    "agentleak",
    "agentpoison_strategyqa",
    "saber",
)
PRODUCTS = ("codex", "claude_code", "qoder")


def output_root(product: str, benchmark: str) -> Path:
    version = "v0.2.1" if benchmark == "msb" else "v0.2"
    return RUN_ROOT / version / product / benchmark


def write_progress(product: str, report: dict[str, Any]) -> None:
    path = RUN_ROOT / "v0.2" / f"remaining20_worker_{product}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    temporary.replace(path)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run one product's fixed remaining20 benchmark queue."
    )
    parser.add_argument("--product", required=True, choices=PRODUCTS)
    args = parser.parse_args()

    if args.product == "qoder" and not os.environ.get("DEEPSEEK_API_KEY"):
        parser.error("DEEPSEEK_API_KEY is required for the Qoder external-provider route")

    report: dict[str, Any] = {
        "schema_version": "0.1",
        "selection_stage": "remaining20",
        "product": args.product,
        "status": "running",
        "benchmarks": {},
    }
    write_progress(args.product, report)
    failed = False

    for benchmark in BENCHMARKS:
        request = PACKAGE_ROOT / "examples" / "requests" / "v0.2" / f"{args.product}_{benchmark}.json"
        selection = SELECTION_ROOT / f"{benchmark}.jsonl"
        destination = output_root(args.product, benchmark)
        command = [
            sys.executable,
            str(RUN_BATCH),
            "--request",
            str(request),
            "--case-index",
            str(selection),
            "--run-id",
            f"{args.product}-{benchmark}-remaining20-v0.2",
            "--output-root",
            str(destination),
            "--resume",
            "--keep-going",
        ]
        if benchmark == "saber":
            command.append("--publish-native-result")
        report["benchmarks"][benchmark] = {
            "status": "running",
            "selection": str(selection),
            "output_root": str(destination),
        }
        write_progress(args.product, report)
        completed = subprocess.run(command, cwd=PACKAGE_ROOT, check=False)
        status = "completed" if completed.returncode == 0 else "failed"
        report["benchmarks"][benchmark]["status"] = status
        report["benchmarks"][benchmark]["returncode"] = completed.returncode
        write_progress(args.product, report)
        failed = failed or completed.returncode != 0

    report["status"] = "failed" if failed else "completed"
    write_progress(args.product, report)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
