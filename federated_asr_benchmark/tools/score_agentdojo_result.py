from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


PACKAGE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PACKAGE_ROOT))

from agentdojo.functions_runtime import FunctionCall
from agentdojo.task_suite.load_suites import get_suite
from agentdojo.types import text_content_block_from_string
from product_eval.agentdojo import map_security_result


def main() -> int:
    parser = argparse.ArgumentParser(description="Score one product trace with AgentDojo's official validator.")
    parser.add_argument("--native-result", required=True, type=Path)
    parser.add_argument("--final-response", required=True, type=Path)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--case-id", required=True)
    parser.add_argument("--product-json", required=True)
    parser.add_argument("--product-status", required=True)
    parser.add_argument("--product-timed-out", choices=["true", "false"], required=True)
    parser.add_argument("--benchmark-id", default="agentdojo")
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()

    native = json.loads(args.native_result.read_text(encoding="utf-8"))
    product = json.loads(args.product_json)
    suite = get_suite(native["benchmark_version"], native["suite_name"])
    user_task = suite.get_user_task_by_id(native["user_task_id"])
    injection_task = suite.get_injection_task_by_id(native["injection_task_id"])
    if native.get("pre_environment") is None or native.get("post_environment") is None:
        post_environment = suite.load_and_inject_default_environment(native["injections"])
        post_environment = user_task.init_environment(post_environment)
        pre_environment = post_environment.model_copy(deep=True)
    else:
        pre_environment = suite.environment_type.model_validate(native["pre_environment"])
        post_environment = suite.environment_type.model_validate(native["post_environment"])
    traces = [FunctionCall.model_validate(row) for row in native["function_calls"]]
    final_response = args.final_response.read_text(encoding="utf-8", errors="replace")

    invalid_reason = None
    if args.product_status != "completed" or args.product_timed_out == "true":
        invalid_reason = (
            f"product execution status={args.product_status}, timed_out={args.product_timed_out}"
        )
    elif native.get("error"):
        invalid_reason = native["error"]
    if invalid_reason:
        security = None
    else:
        security = suite._check_injection_task_security(
            injection_task,
            [text_content_block_from_string(final_response)],
            pre_environment,
            post_environment,
            traces,
        )
        if type(security) is not bool:
            invalid_reason = "native security verdict is unknown or non-boolean"
    status, numerator, denominator = map_security_result(security, invalid_reason)

    result = {
        "schema_version": "0.1",
        "run_id": args.run_id,
        "product": product,
        "benchmark_id": args.benchmark_id,
        "case_id": args.case_id,
        "status": status,
        "reason": invalid_reason,
        "asr_contribution": {"numerator": numerator, "denominator": denominator, "unit": "case"},
        "official_evidence": {
            "source_file": str(args.native_result.resolve()),
            "native_field": "security",
            "native_value": security,
            "source_record_id": (
                f"{native['suite_name']}:{native['user_task_id']}:"
                f"{native['injection_task_id']}:{native['attack_type']}"
            ),
        },
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
