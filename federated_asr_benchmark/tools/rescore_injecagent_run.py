from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys


PACKAGE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PACKAGE_ROOT))

from tools.run_product import DEFAULT_INJECAGENT_ROOT
from tools.run_product_batch import selected_cases, write_case_results


def main() -> int:
    parser = argparse.ArgumentParser(description="Re-run InjecAgent scoring without product calls.")
    parser.add_argument("--run-root", required=True, type=Path)
    parser.add_argument("--case-index", required=True, type=Path)
    parser.add_argument("--injecagent-root", type=Path, default=DEFAULT_INJECAGENT_ROOT)
    args = parser.parse_args()

    root = args.run_root.resolve()
    case_ids = selected_cases(args.case_index.resolve(), "injecagent", None)
    counts = {"selected": len(case_ids), "rescored": 0, "missing": 0, "failed": 0}
    for case_id in case_ids:
        case_root = root / case_id.replace(":", "__")
        product_path = case_root / "product_run.json"
        native_path = case_root / "native_result.json"
        if not product_path.is_file() or not native_path.is_file():
            counts["missing"] += 1
            continue
        product = json.loads(product_path.read_text(encoding="utf-8"))
        completed = subprocess.run(
            [
                sys.executable,
                str(PACKAGE_ROOT / "tools" / "score_injecagent_result.py"),
                "--injecagent-root",
                str(args.injecagent_root.resolve()),
                "--native-result",
                str(native_path),
                "--final-response",
                str(case_root / "final_response.txt"),
                "--run-id",
                product["run_id"],
                "--case-id",
                case_id,
                "--product-json",
                json.dumps(product["product"], ensure_ascii=False),
                "--product-status",
                product["status"],
                "--product-timed-out",
                "true" if product["timed_out"] else "false",
                "--output",
                str(case_root / "case_result.json"),
            ],
            text=True,
            encoding="utf-8",
            errors="replace",
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            timeout=60,
            check=False,
        )
        if completed.returncode == 0:
            counts["rescored"] += 1
        else:
            counts["failed"] += 1
            print(f"{case_id}: {completed.stderr.strip()}", file=sys.stderr)
    counts["scored"] = write_case_results(root, case_ids)
    print(json.dumps(counts, ensure_ascii=False, indent=2))
    return 1 if counts["failed"] or counts["missing"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
