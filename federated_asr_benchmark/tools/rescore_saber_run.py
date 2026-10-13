from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import sys


PACKAGE_ROOT = Path(__file__).resolve().parents[1]
WORKSPACE_ROOT = PACKAGE_ROOT.parent
sys.path.insert(0, str(PACKAGE_ROOT))

from product_eval.saber import SaberAdapter
from tools.run_product import DEFAULT_SABER_ROOT
from tools.run_product_batch import write_case_results


def selected_cases(path: Path) -> list[dict]:
    return [
        row
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and (row := json.loads(line))["benchmark_id"] == "saber"
    ]


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Rebuild SABER case results from official judgments without rerunning products."
    )
    parser.add_argument(
        "--product",
        required=True,
        choices=["codex", "claude_code", "qoder", "codebuddy"],
    )
    parser.add_argument("--run-root", required=True, type=Path)
    parser.add_argument("--case-index", required=True, type=Path)
    parser.add_argument("--saber-root", type=Path, default=DEFAULT_SABER_ROOT)
    parser.add_argument("--retry-judge-errors", action="store_true")
    args = parser.parse_args()

    adapter = SaberAdapter(args.saber_root)
    rows = selected_cases(args.case_index.resolve())
    product_root = args.run_root.resolve() / args.product / "saber"
    counts: Counter[str] = Counter()
    retried = 0

    for row in rows:
        case_id = row["case_id"]
        case_root = product_root / case_id.replace(":", "__")
        run_path = case_root / "product_run.json"
        native_path = case_root / "native_result.json"
        judgment_path = case_root / "official_judgment.json"
        if not run_path.is_file() or not native_path.is_file():
            counts["missing"] += 1
            continue

        run = json.loads(run_path.read_text(encoding="utf-8"))
        if judgment_path.is_file():
            try:
                judged = adapter.load_judged_result(judgment_path)
            except (OSError, UnicodeDecodeError, json.JSONDecodeError):
                judged = {"judge_err": "unreadable judged result"}
        else:
            judged = None
        if args.retry_judge_errors and (judged is None or judged.get("judge_err")):
            task = adapter.load_task(row["native_case_id"])
            version = str(run["product"]["version"]).replace(".", "_")
            slug = f"product_{args.product}_{version}"
            adapter.publish_native_result(native_path, slug)
            official = adapter.judge_native_result(product_slug=slug, task=task)
            judgment_path.write_text(
                json.dumps(
                    adapter.load_judged_result(official), ensure_ascii=False, indent=2
                )
                + "\n",
                encoding="utf-8",
            )
            retried += 1
        elif judged is None:
            counts["missing"] += 1
            continue

        result = adapter.case_result_from_judged(
            run_id=run["run_id"],
            product=run["product"],
            case_id=case_id,
            judged_path=judgment_path,
        )
        (case_root / "case_result.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        counts[result["status"]] += 1

    write_case_results(product_root, [row["case_id"] for row in rows])
    print(
        json.dumps(
            {"product": args.product, "selected": len(rows), "retried": retried, "counts": counts},
            ensure_ascii=False,
            indent=2,
        )
    )
    return 1 if counts["missing"] or counts["invalid"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
