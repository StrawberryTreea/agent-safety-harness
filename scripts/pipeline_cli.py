from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import uuid

from .result_analysis import TRACKS, analyze, empty_output, write_json

ROOT = Path(__file__).resolve().parents[1]


def inventory() -> dict:
    registry = json.loads((ROOT / "protocol/evaluation_tracks.json").read_text(encoding="utf-8"))
    data_root = os.environ.get("AGENT_EVAL_DATA_ROOT")
    for track in registry["tracks"].values():
        track["executor_exists"] = (ROOT / track["executor"]).is_file()
    registry["external_data"] = {"configured": bool(data_root),
                                 "exists": bool(data_root and Path(data_root).is_dir())}
    from .protocol_manager import load_protocol
    dataset = load_protocol(ROOT / "protocol")["protocol"]["dataset"]
    registry["llm_input"] = {**dataset, "present": (ROOT / dataset["path"]).is_file()}
    registry["llm_canonical_present"] = (ROOT / "data/canonical.jsonl").is_file()
    registry["self_contained_smoke"] = "python pipeline.py smoke"
    return registry


def plan(args: argparse.Namespace) -> dict:
    out = args.out.expanduser().resolve()
    requirements, steps = [], []

    def require(name: str, value: Path | str | None) -> str:
        path = Path(value).expanduser().resolve() if value else None
        requirements.append({"name": name, "path": str(path) if path else None,
                             "present": path.exists() if path else False})
        return str(path) if path else f"<{name}>"

    def step(name: str, cwd: Path, argv: list[str]) -> None:
        steps.append({"name": name, "cwd": str(cwd), "argv": argv})

    harness = str(ROOT / "harness/ash.py")
    if args.track == "agent_execution":
        tasks, rules = require("public_tasks", args.tasks), require("private_rules", args.private_rules)
        configuration = []
        for name in ("agents_config", "models_config"):
            value = getattr(args, name)
            if value:
                configuration.extend(["--" + name.replace("_", "-"), require(name, value)])
        step("capture", ROOT / "harness", [sys.executable, harness, "capture", "--agent", args.agent,
             "--input", tasks, "--out", str(out / "capture"), *configuration])
        step("score", ROOT / "harness", [sys.executable, harness, "score", "--private-input", rules,
             "--capture-run", str(out / "capture"), "--out", str(out / "score")])
        step("report", ROOT / "harness", [sys.executable, harness, "report", "--run", str(out / "score")])
    elif args.track == "trace_judge":
        tasks = require("trace_tasks", args.tasks)
        if not args.benchmark:
            requirements.append({"name": "benchmark", "present": False, "path": None})
        step("judge", ROOT / "harness", [sys.executable, harness, "judge", "--benchmark", args.benchmark or "<benchmark>",
             "--input", tasks, "--out", str(out / "score")])
        step("report", ROOT / "harness", [sys.executable, harness, "report", "--run", str(out / "score")])
    elif args.track == "llm_qa":
        config = require("llm_run_config", args.config)
        step("evaluate", ROOT, [sys.executable, str(ROOT / "llm_eval/multi_model_rule_eval_prompt_only.py"),
             "--config", config])
    else:
        request = require("product_request", args.request)
        data = require("benchmark_data_root", args.data_root or os.environ.get("AGENT_EVAL_DATA_ROOT"))
        if not args.case_id:
            requirements.append({"name": "case_id", "present": False, "path": None})
        step("product_dry_run", ROOT / "federated_asr_benchmark", [sys.executable,
             str(ROOT / "federated_asr_benchmark/tools/run_product.py"), "--request", request,
             "--case-id", args.case_id or "<case_id>", "--data-root", data,
             "--output-root", str(out), "--dry-run"])
    return {"schema_version": "1.0", "stage": "execution_plan", "track": args.track,
            "prerequisites_present": all(item["present"] for item in requirements),
            "requirements": requirements, "steps": steps, "executed": False,
            "note": "Path checks only. Native preflight, provider configuration, dataset/scorer qualification and run budgets remain required. LLM output location is controlled by llm_run_config."}


def smoke(out: Path | None, agent: str) -> dict:
    run_id = "smoke-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex[:8]
    out = out.expanduser().resolve() if out else ROOT / "runs" / run_id
    empty_output(out)
    manifest = {"schema_version": "1.0", "run_id": run_id, "track": "agent_execution",
                "agent": agent, "fixture": "synthetic_local_mock", "status": "started", "steps": []}
    manifest_path = out / "pipeline_manifest.json"
    write_json(manifest_path, manifest)
    ash = str(ROOT / "harness/ash.py")
    commands = [
        ("capture", [sys.executable, ash, "capture", "--agent", agent, "--input",
                     str(ROOT / "harness/smoke/public_tasks.jsonl"), "--out", str(out / "capture")]),
        ("score", [sys.executable, ash, "score", "--private-input",
                   str(ROOT / "harness/smoke/private_rules.jsonl"), "--capture-run", str(out / "capture"), "--out", str(out / "score")]),
        ("report", [sys.executable, ash, "report", "--run", str(out / "score")]),
    ]
    try:
        for name, argv in commands:
            started = datetime.now(timezone.utc).isoformat()
            with (out / f"{name}.log").open("w", encoding="utf-8") as log:
                process = subprocess.run(argv, cwd=ROOT / "harness", stdout=log, stderr=subprocess.STDOUT,
                                         env={**os.environ, "PYTHONUTF8": "1"}, timeout=120, check=False)
            manifest["steps"].append({"name": name, "argv": argv, "cwd": str(ROOT / "harness"),
                                      "started_at": started, "returncode": process.returncode})
            write_json(manifest_path, manifest)
            if process.returncode:
                raise RuntimeError(f"Smoke {name} failed; see {out / (name + '.log')}")
        native_summary = json.loads((out / "score/summary.json").read_text(encoding="utf-8"))
        if not native_summary["run_valid"] or native_summary["total"] != 3:
            raise RuntimeError("Smoke did not produce three valid native scored results")
        summary = analyze("agent_execution", [out / "score/results.jsonl"], out / "analysis")
        manifest.update(status="completed", native_summary=native_summary,
                        failure_observation_count=summary["failure_observation_count"],
                        candidate_plan_count=summary["candidate_plan_count"], output=str(out))
    except Exception as exc:
        manifest.update(status="failed", error=f"{type(exc).__name__}: {exc}")
        write_json(manifest_path, manifest)
        raise
    manifest["completed_at"] = datetime.now(timezone.utc).isoformat()
    write_json(manifest_path, manifest)
    return manifest


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="LLM / Agent evaluation and failure-analysis entry point")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("inventory", help="List native tracks and local dependency presence")
    command = sub.add_parser("plan", help="Emit argv arrays; does not execute")
    command.add_argument("--track", required=True, choices=TRACKS)
    for option in ("tasks", "private-rules", "config", "request", "data-root", "agents-config", "models-config"):
        command.add_argument(f"--{option}", type=Path)
    command.add_argument("--agent", default="safe_stub")
    command.add_argument("--benchmark")
    command.add_argument("--case-id")
    command.add_argument("--out", type=Path, default=ROOT / "runs/planned")
    command = sub.add_parser("smoke", help="Execute three local mock tasks, private scoring, reporting and analysis")
    command.add_argument("--out", type=Path)
    command.add_argument("--agent", choices=("safe_stub", "unsafe_stub"), default="safe_stub")
    command = sub.add_parser("analyze", help="Analyze native result rows and emit unaccepted candidate plans")
    command.add_argument("--track", required=True, choices=TRACKS)
    command.add_argument("--results", type=Path, nargs="+", required=True)
    command.add_argument("--out", type=Path, required=True)
    command = sub.add_parser("loop-a", help="Local test-evolution contracts, fixtures and formal-readiness checks")
    command.add_argument("action", choices=("plan", "qualify", "partitions", "metrics", "smoke", "cycle", "compare", "run"))
    command.add_argument("--config", type=Path, default=ROOT / "protocol/loop_a_experiment.json")
    command.add_argument("--out", type=Path)
    command.add_argument("--data-root", type=Path)
    command.add_argument("--results", type=Path)
    command.add_argument("--tasks", type=Path, help="Public task JSONL for a partition manifest")
    command.add_argument("--assignments", type=Path, help="Explicit task-ID to family/partition JSON mapping")
    command.add_argument("--primary", choices=("net_independent_discoveries", "benign_gated_discoveries"))
    command.add_argument("--resume", action="store_true")
    command.add_argument("--strategy", choices=("random", "feedback", "wording"), default="feedback")
    command.add_argument("--seed", type=int, default=0)
    args = parser.parse_args(argv)
    try:
        if args.command == "loop-a":
            from orchestrator.controller import readiness, smoke as loop_smoke
            from evolution.qualification import inventory as qualify
            from evolution.metrics import summarize_discoveries
            if args.action in {"plan", "run"}:
                config = json.loads(args.config.read_text(encoding="utf-8"))
                result = readiness(config)
                if args.action == "run":
                    print(json.dumps(result, ensure_ascii=False, indent=2))
                    return 2
            elif args.action == "qualify":
                result = qualify(ROOT, args.data_root)
            elif args.action == "partitions":
                if args.tasks is None or args.assignments is None or args.out is None:
                    raise ValueError("loop-a partitions requires --tasks, --assignments and a new --out directory")
                from evolution.partitions import build_manifest
                from orchestrator.workers import read_rows
                manifest = build_manifest(read_rows(args.tasks), json.loads(args.assignments.read_text(encoding="utf-8-sig")))
                result = {"schema_version": "partition_build_report_v1", "manifest_hash": manifest["manifest_hash"],
                          "counts": {p: sum(row["partition"] == p for row in manifest["entries"]) for p in ("E", "V", "H")},
                          "formal_ready": False}
            elif args.action == "metrics":
                if args.results is None:
                    raise ValueError("loop-a metrics requires --results")
                from orchestrator.workers import read_rows
                result = summarize_discoveries(read_rows(args.results), primary=args.primary)
            elif args.action in {"cycle", "compare"}:
                if args.out is None:
                    raise ValueError("loop-a cycle/compare requires a new --out directory")
                from orchestrator.fixture_cycle import run as fixture_cycle, compare
                output = args.out.expanduser().resolve()
                result = (compare(output, seed=args.seed, resume=args.resume) if args.action == "compare" else
                          fixture_cycle(output, strategy=args.strategy, seed=args.seed, resume=args.resume))
            else:
                if args.out is None:
                    raise ValueError("loop-a smoke requires a new --out directory")
                result = loop_smoke(args.out.expanduser().resolve(), resume=args.resume)
            if args.out is not None and args.action not in {"smoke", "cycle", "compare"}:
                from orchestrator.workers import write_json as write_once
                empty_output(args.out)
                write_once(args.out / "summary.json", result)
                if args.action == "partitions":
                    write_once(args.out / "partitions.json", manifest)
        elif args.command == "inventory":
            result = inventory()
        elif args.command == "plan":
            result = plan(args)
        elif args.command == "smoke":
            result = smoke(args.out, args.agent)
        else:
            result = analyze(args.track, args.results, args.out.expanduser().resolve())
    except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as exc:
        parser.exit(2, f"ERROR: {exc}\n")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0
