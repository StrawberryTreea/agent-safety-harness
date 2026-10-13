from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys


PACKAGE_ROOT = Path(__file__).resolve().parents[1]
WORKSPACE_ROOT = PACKAGE_ROOT.parent
RUN_ONE = PACKAGE_ROOT / "tools" / "run_product.py"
sys.path.insert(0, str(PACKAGE_ROOT))

from product_eval.saber import SaberAdapter
from product_eval.api_judge import OpenAICompatibleJudge, load_immutable_judge_profile
from product_eval.product_model_policy import resolve_requested_model
from tools.run_product import (
    DEFAULT_AGENTDYN_PYTHON,
    DEFAULT_AGENTDYN_ROOT,
    DEFAULT_AGENTHARM_DATA_ROOT,
    DEFAULT_AGENTHARM_RUNTIME_ROOT,
    DEFAULT_AGENTDOJO_PYTHON,
    DEFAULT_AGENTDOJO_ROOT,
    DEFAULT_AGENTLEAK_ROOT,
    DEFAULT_AGENTPOISON_ROOT,
    DEFAULT_INJECAGENT_ROOT,
    DEFAULT_MSB_ROOT,
    DEFAULT_SABER_ROOT,
    apply_data_root,
)


def run_one_for_request(request: dict) -> Path:
    if request["product"]["product_id"] == "qoder":
        return PACKAGE_ROOT / "tools" / "run_product_qoder_byok.py"
    return RUN_ONE


def selected_cases(case_index: Path, benchmark_id: str, requested: list[str] | None) -> list[str]:
    result = []
    requested_set = set(requested) if requested is not None else None
    for line in case_index.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        record = json.loads(line)
        if record["benchmark_id"] != benchmark_id:
            continue
        if requested_set is not None and record["case_id"] not in requested_set:
            continue
        result.append(record["case_id"])
    if requested_set is not None:
        missing = requested_set - set(result)
        if missing:
            raise ValueError(f"Requested case IDs are not selected: {sorted(missing)}")
    return result


def output_root(request_path: Path, request: dict) -> Path:
    path = Path(request["output_dir"]).expanduser()
    return path if path.is_absolute() else (request_path.parent / path).resolve()


def case_index_path(
    request_path: Path, request: dict, override: Path | None = None
) -> Path:
    if override is not None:
        return override.expanduser().resolve()
    configured = request.get("selection", {}).get("case_index")
    if configured:
        path = Path(configured).expanduser()
        return path.resolve() if path.is_absolute() else (request_path.parent / path).resolve()
    return PACKAGE_ROOT / "case_index.jsonl"


def completed_case(root: Path, case_id: str) -> bool:
    artifact = root / case_id.replace(":", "__") / "product_run.json"
    if not artifact.is_file():
        return False
    try:
        completed = json.loads(artifact.read_text(encoding="utf-8")).get("status") == "completed"
        if not completed:
            return False
        scored = artifact.parent / "case_result.json"
        if not scored.is_file():
            return False
        return json.loads(scored.read_text(encoding="utf-8")).get("status") in {
            "success", "failure", "excluded", "invalid",
        }
    except Exception:
        return False


def clear_case_markers(root: Path, case_id: str) -> None:
    case_root = root / case_id.replace(":", "__")
    for name in ("product_run.json", "case_result.json", "native_result.json"):
        path = case_root / name
        if path.is_file():
            path.unlink()


def requires_judge_preflight(*, case_count: int, dry_run: bool, allow_unjudged_traces: bool) -> bool:
    return case_count > 1 and not dry_run and not allow_unjudged_traces


def write_case_results(root: Path, case_ids: list[str]) -> int:
    destination = root / "case_result.jsonl"
    merged = {}
    if destination.is_file():
        for line in destination.read_text(encoding="utf-8").splitlines():
            if line.strip():
                row = json.loads(line)
                merged[row["case_id"]] = row
    current_rows = []
    for case_id in case_ids:
        path = root / case_id.replace(":", "__") / "case_result.json"
        if path.is_file():
            row = json.loads(path.read_text(encoding="utf-8"))
            merged[case_id] = row
            current_rows.append(row)
    if merged:
        destination.write_text(
            "".join(
                json.dumps(merged[case_id], ensure_ascii=False, sort_keys=True) + "\n"
                for case_id in sorted(merged)
            ),
            encoding="utf-8",
        )
    return len(current_rows)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run a resumable product-native benchmark batch.")
    parser.add_argument("--request", required=True, type=Path)
    parser.add_argument("--case-index", type=Path)
    parser.add_argument("--data-root", type=Path)
    parser.add_argument("--saber-root", type=Path, default=DEFAULT_SABER_ROOT)
    parser.add_argument("--agentdojo-root", type=Path, default=DEFAULT_AGENTDOJO_ROOT)
    parser.add_argument("--agentdojo-python", type=Path, default=DEFAULT_AGENTDOJO_PYTHON)
    parser.add_argument("--agentdyn-root", type=Path, default=DEFAULT_AGENTDYN_ROOT)
    parser.add_argument("--agentdyn-python", type=Path, default=DEFAULT_AGENTDYN_PYTHON)
    parser.add_argument("--agentharm-data-root", type=Path, default=DEFAULT_AGENTHARM_DATA_ROOT)
    parser.add_argument(
        "--agentharm-runtime-root", type=Path, default=DEFAULT_AGENTHARM_RUNTIME_ROOT
    )
    parser.add_argument("--injecagent-root", type=Path, default=DEFAULT_INJECAGENT_ROOT)
    parser.add_argument("--msb-root", type=Path, default=DEFAULT_MSB_ROOT)
    parser.add_argument("--agentleak-root", type=Path, default=DEFAULT_AGENTLEAK_ROOT)
    parser.add_argument("--agentpoison-root", type=Path, default=DEFAULT_AGENTPOISON_ROOT)
    parser.add_argument("--state-source", type=Path)
    parser.add_argument("--run-id", help="Override request.run_id for a staged execution.")
    parser.add_argument("--output-root", type=Path, help="Override request.output_dir.")
    parser.add_argument("--limit", type=int, default=0, help="0 means all selected cases")
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--publish-native-result", action="store_true")
    parser.add_argument("--keep-going", action="store_true")
    parser.add_argument(
        "--allow-unjudged-traces",
        action="store_true",
        help="Explicitly allow a multi-case paid run when the official judge is not ready.",
    )
    args = parser.parse_args()
    apply_data_root(args)

    request_path = args.request.resolve()
    request = json.loads(request_path.read_text(encoding="utf-8"))
    benchmark_id = request["benchmark_id"]
    product = request["product"]
    resolve_requested_model(product["product_id"], product.get("product_model"))
    supported = {
        "saber", "agentdojo", "agentdyn", "agentharm", "injecagent", "msb", "agentleak", "agentpoison_strategyqa"
    }
    if benchmark_id not in supported:
        raise NotImplementedError(f"Implemented benchmark adapters: {', '.join(sorted(supported))}")
    cases = selected_cases(
        case_index_path(request_path, request, args.case_index),
        benchmark_id,
        request.get("selection", {}).get("case_ids"),
    )
    cases = cases[args.offset :]
    if args.limit > 0:
        cases = cases[: args.limit]
    if benchmark_id == "saber" and requires_judge_preflight(
        case_count=len(cases),
        dry_run=args.dry_run,
        allow_unjudged_traces=args.allow_unjudged_traces,
    ):
        try:
            judge = SaberAdapter(args.saber_root).preflight_judge()
        except Exception as exc:
            parser.error(
                f"multi-case SABER execution blocked because official ASR judging is not ready: {exc}. "
                "Use --allow-unjudged-traces only when collecting trajectories without ASR is intentional."
            )
        print(
            "SABER judge preflight passed: "
            + json.dumps(judge, ensure_ascii=False, sort_keys=True)
        )
    if benchmark_id == "agentharm" and requires_judge_preflight(
        case_count=len(cases),
        dry_run=args.dry_run,
        allow_unjudged_traces=False,
    ):
        try:
            profile = load_immutable_judge_profile(
                PACKAGE_ROOT / "release" / "evaluation_config.json",
                PACKAGE_ROOT / "benchmark_registry.json",
            )
            judge = OpenAICompatibleJudge(profile)
            binding = judge.preflight()
        except Exception as exc:
            parser.error(
                f"multi-case AgentHarm execution blocked because the locked API judge "
                f"is not ready: {exc}"
            )
        print(
            "AgentHarm judge preflight passed: "
            + json.dumps(binding, ensure_ascii=False, sort_keys=True)
        )
    root = args.output_root.expanduser().resolve() if args.output_root else output_root(request_path, request)
    run_one = run_one_for_request(request)
    counts = {"selected": len(cases), "completed": 0, "failed": 0, "skipped": 0}

    for index, case_id in enumerate(cases, start=1):
        if args.resume and completed_case(root, case_id):
            counts["skipped"] += 1
            print(f"[{index}/{len(cases)}] skip completed {case_id}")
            continue
        clear_case_markers(root, case_id)
        command = [
            sys.executable,
            str(run_one),
            "--request",
            str(request_path),
            "--case-index",
            str(case_index_path(request_path, request, args.case_index)),
            "--saber-root",
            str(args.saber_root.resolve()),
            "--agentdojo-root",
            str(args.agentdojo_root.resolve()),
            "--agentdojo-python",
            str(args.agentdojo_python.resolve()),
            "--agentdyn-root",
            str(args.agentdyn_root.resolve()),
            "--agentdyn-python",
            str(args.agentdyn_python.resolve()),
            "--agentharm-data-root",
            str(args.agentharm_data_root.resolve()),
            "--agentharm-runtime-root",
            str(args.agentharm_runtime_root.resolve()),
            "--injecagent-root",
            str(args.injecagent_root.resolve()),
            "--msb-root",
            str(args.msb_root.resolve()),
            "--agentleak-root",
            str(args.agentleak_root.resolve()),
            "--agentpoison-root",
            str(args.agentpoison_root.resolve()),
            "--case-id",
            case_id,
        ]
        if args.state_source:
            command.extend(["--state-source", str(args.state_source.resolve())])
        if args.run_id:
            command.extend(["--run-id", args.run_id])
        if args.output_root:
            command.extend(["--output-root", str(root)])
        if args.dry_run:
            command.append("--dry-run")
        if args.publish_native_result:
            command.append("--publish-native-result")
        print(f"[{index}/{len(cases)}] run {case_id}")
        completed = subprocess.run(command, check=False)
        if completed.returncode == 0:
            counts["completed"] += 1
        else:
            counts["failed"] += 1
            if not args.keep_going:
                break

    counts["scored"] = write_case_results(root, cases)

    print(json.dumps(counts, ensure_ascii=False, indent=2))
    return 1 if counts["failed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
