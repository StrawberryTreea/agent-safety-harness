from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path
import time
from typing import Any

from .adapters import unsupported_official_benchmarks
from .agents import build_agent
from .benchmarks import BUILTIN_BENCHMARKS, load_tasks
from .config import default_config_dir, load_import_preset, load_json_config
from .importers import convert_agentharm_records, convert_mapped_records, read_agentharm_source, read_records
from .evaluation import assert_tasks_evaluable
from .io import append_jsonl, read_jsonl, write_csv, write_json, write_jsonl
from .reporting import aggregate_usage, write_report
from .review import review_score_run
from .runners import iter_agent_task_outputs, iter_judge_task_outputs, run_agent_tasks, run_judge_tasks
from .schema import classify_safety_outcome
from .public_capture import assert_public_record
from .evaluation import evaluate_task


class ConsoleProgress:
    def __init__(
        self,
        label: str,
        total: int,
        *,
        workers: int,
        historical_durations: list[float] | None = None,
        count_unknown_as_error: bool = True,
    ) -> None:
        self.label = label
        self.total = total
        self.workers = max(1, workers)
        self.started = time.monotonic()
        self.errors = 0
        self.count_unknown_as_error = count_unknown_as_error
        durations = historical_durations or []
        self.duration_total = sum(durations)
        self.duration_count = len(durations)
        eta = self._eta(total)
        print(
            f"[progress] {label} started tasks={total} workers={self.workers} ETA={_format_duration(eta)}",
            flush=True,
        )

    def update(self, completed: int, total: int, task, result: dict[str, Any]) -> None:
        duration = _result_duration_seconds(result)
        if duration is not None:
            self.duration_total += duration
            self.duration_count += 1
        is_unknown = result.get("policy_verdict") == "unknown"
        has_error = (result.get("evidence") or {}).get("error_type")
        if has_error and (self.count_unknown_as_error or not is_unknown):
            self.errors += 1
        elapsed = time.monotonic() - self.started
        average = self.duration_total / self.duration_count if self.duration_count else None
        eta = self._eta(max(total - completed, 0))
        width = 20
        filled = width if total == 0 else min(width, round(width * completed / total))
        bar = "#" * filled + "-" * (width - filled)
        percent = 100.0 if total == 0 else 100.0 * completed / total
        average_text = f"{average:.1f}s" if average is not None else "calculating"
        print(
            f"[progress] {self.label} [{bar}] {completed}/{total} ({percent:5.1f}%) "
            f"errors={self.errors} elapsed={_format_duration(elapsed)} avg={average_text} "
            f"ETA={_format_duration(eta)} task={task.task_id}",
            flush=True,
        )

    def finish(self, completed: int | None = None) -> None:
        done = self.total if completed is None else completed
        elapsed = time.monotonic() - self.started
        print(
            f"[progress] {self.label} finished completed={done}/{self.total} "
            f"errors={self.errors} elapsed={_format_duration(elapsed)}",
            flush=True,
        )

    def _eta(self, remaining: int) -> float | None:
        if self.duration_count == 0:
            return None
        average = self.duration_total / self.duration_count
        return average * remaining / self.workers


def _format_duration(seconds: float | None) -> str:
    if seconds is None:
        return "calculating"
    rounded = max(0, int(round(seconds)))
    hours, remainder = divmod(rounded, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def _result_duration_seconds(result: dict[str, Any]) -> float | None:
    value = (result.get("evidence") or {}).get("duration_ms")
    if isinstance(value, (int, float)) and value >= 0:
        return float(value) / 1000
    return None


def _historical_durations(out_dir: Path) -> list[float]:
    path = out_dir / "results.jsonl"
    if not path.exists():
        return []
    durations = [_result_duration_seconds(record) for record in read_jsonl(path)]
    return [duration for duration in durations if duration is not None]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ash", description="Agent Safety Harness MVP")
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser("run", help="Run an agent execution benchmark")
    run_parser.add_argument("--benchmark", required=True)
    run_parser.add_argument("--agent", default="safe_stub", help="safe_stub, unsafe_stub, or model:<configured_model_id>")
    run_parser.add_argument("--models-config", default=None, help="Optional models.json path")
    run_parser.add_argument("--agents-config", default=None, help="Optional agents.json path for cli:<configured_agent_id>")
    run_parser.add_argument("--input", default=None, help="Optional JSONL TaskSpec input")
    run_parser.add_argument("--private-input", default=None, help="Hidden runtime/evaluation JSONL paired by task_id")
    run_parser.add_argument("--sample", type=int, default=None)
    run_parser.add_argument("--workers", type=int, default=1, help="Number of tasks to run concurrently")
    run_parser.add_argument("--out", required=True)
    run_parser.add_argument("--overwrite", action="store_true", help="Allow writing to an existing non-empty run directory")
    run_parser.add_argument("--resume", action="store_true", help="Resume an existing run by skipping completed task_id values")

    capture_parser = subparsers.add_parser(
        "capture",
        help="Run agents with runtime-only tasks and save unscored traces",
    )
    capture_parser.add_argument("--agent", required=True)
    capture_parser.add_argument("--input", required=True, help="Runtime-only JSONL")
    capture_parser.add_argument("--models-config", default=None)
    capture_parser.add_argument("--agents-config", default=None)
    capture_parser.add_argument("--sample", type=int, default=None)
    capture_parser.add_argument("--workers", type=int, default=1)
    capture_parser.add_argument("--max-attempts", type=int, default=1)
    capture_parser.add_argument("--out", required=True)
    capture_parser.add_argument("--overwrite", action="store_true")
    capture_parser.add_argument("--resume", action="store_true")

    score_parser = subparsers.add_parser(
        "score",
        help="Score a capture run with private rules in a separate phase",
    )
    score_parser.add_argument("--private-input", required=True)
    score_parser.add_argument("--capture-run", required=True)
    score_parser.add_argument("--sample", type=int, default=None)
    score_parser.add_argument("--out", required=True)
    score_parser.add_argument("--overwrite", action="store_true")

    review_parser = subparsers.add_parser(
        "review",
        help="Review scored safety passes for non-engagement and missed attack evidence",
    )
    review_parser.add_argument("--private-input", required=True)
    review_parser.add_argument("--score-run", required=True)
    review_parser.add_argument("--out", required=True)
    review_parser.add_argument("--sample-fraction", type=float, default=0.10)
    review_parser.add_argument("--seed", type=int, default=42)
    review_parser.add_argument("--overwrite", action="store_true")

    judge_parser = subparsers.add_parser("judge", help="Run a trace judge benchmark")
    judge_parser.add_argument("--benchmark", required=True)
    judge_parser.add_argument("--input", default=None, help="Optional JSONL TaskSpec input")
    judge_parser.add_argument("--sample", type=int, default=None)
    judge_parser.add_argument("--workers", type=int, default=1, help="Number of tasks to judge concurrently")
    judge_parser.add_argument("--out", required=True)
    judge_parser.add_argument("--overwrite", action="store_true", help="Allow writing to an existing non-empty run directory")
    judge_parser.add_argument("--resume", action="store_true", help="Resume an existing judge run by skipping completed task_id values")

    report_parser = subparsers.add_parser("report", help="Build a markdown report for an existing run")
    report_parser.add_argument("--run", required=True)

    import_parser = subparsers.add_parser("import", help="Normalize raw benchmark data into TaskSpec JSONL")
    import_subparsers = import_parser.add_subparsers(dest="importer", required=True)
    agentharm_parser = import_subparsers.add_parser("agentharm", help="Import AgentHarm raw JSON/JSONL")
    agentharm_parser.add_argument("--input", required=True, help="Raw AgentHarm JSON or JSONL path")
    agentharm_parser.add_argument("--out", required=True, help="Output TaskSpec JSONL path")
    agentharm_parser.add_argument(
        "--kind",
        choices=["auto", "harmful", "benign", "chat"],
        default="auto",
        help="AgentHarm subset semantics; auto infers from the input filename",
    )
    mapped_parser = import_subparsers.add_parser("mapped", help="Import JSON/JSONL/CSV records through a field-mapping preset")
    mapped_parser.add_argument("--input", required=True, help="Raw JSON, JSONL, or CSV path")
    mapped_parser.add_argument("--out", required=True, help="Output TaskSpec JSONL path")
    mapped_parser.add_argument("--preset", required=True, help="Preset name in benchmark_imports.json")
    mapped_parser.add_argument("--imports-config", default=None, help="Optional benchmark_imports.json path")
    mapped_parser.add_argument("--records-path", default=None, help="Dot path to the record list inside a JSON payload")

    mcp_parser = subparsers.add_parser("mcp-server", help=argparse.SUPPRESS)
    mcp_parser.add_argument("--task", required=True)
    mcp_parser.add_argument("--events", required=True)

    subparsers.add_parser("list", help="List built-in benchmarks, agents, and configured models")

    return parser


def command_run(args: argparse.Namespace) -> int:
    out_dir = Path(args.out)
    tasks = load_tasks(args.benchmark, args.input, args.sample, args.private_input)
    assert_tasks_evaluable(tasks)
    agent = build_agent(args.agent, args.models_config, args.agents_config)
    if agent.agent_id.startswith("cli:"):
        unsupported = unsupported_official_benchmarks(tasks)
        if unsupported:
            names = ", ".join(unsupported)
            raise RuntimeError(
                "Formal evaluation blocked before model invocation: official runtime adapters are missing for "
                f"{names}. Split the input to validated benchmarks or finish those adapters first."
            )
    if args.resume:
        historical_durations = _historical_durations(out_dir)

        def output_factory(pending):
            progress = ConsoleProgress(
                agent.agent_id,
                len(pending),
                workers=args.workers,
                historical_durations=historical_durations,
            )
            try:
                yield from iter_agent_task_outputs(
                    pending,
                    agent,
                    workers=args.workers,
                    on_complete=progress.update,
                )
            finally:
                progress.finish()

        traces, results = write_resumable_run_outputs(
            out_dir,
            args.command,
            args.benchmark,
            agent.agent_id,
            tasks,
            output_factory,
            overwrite=args.overwrite,
            workers=args.workers,
            input_path=_input_manifest_value(args.input, args.private_input),
        )
    else:
        progress = ConsoleProgress(agent.agent_id, len(tasks), workers=args.workers)
        try:
            traces, results = run_agent_tasks(
                tasks,
                agent,
                workers=args.workers,
                on_complete=progress.update,
            )
        finally:
            progress.finish()
        write_run_outputs(
            out_dir,
            args.command,
            args.benchmark,
            agent.agent_id,
            traces,
            results,
            args.overwrite,
            workers=args.workers,
            input_path=_input_manifest_value(args.input, args.private_input),
            input_records=len(tasks),
        )
    summary = write_report(out_dir)
    print(f"wrote {len(results)} result(s) to {out_dir}")
    if args.resume:
        manifest = load_json_config(out_dir / "manifest.json")
        print(f"resume_skipped={manifest.get('resume_skipped')}")
    print(f"safety_pass_rate={summary.get('safety_pass_rate')}")
    print(f"unknown_rate={summary.get('unknown_rate')}")
    return 0


def command_capture(args: argparse.Namespace) -> int:
    out_dir = Path(args.out)
    if args.resume and args.overwrite:
        raise ValueError("Use either --resume or --overwrite, not both")
    for record in read_jsonl(Path(args.input)):
        assert_public_record(record)
    tasks = load_tasks("runtime_capture", args.input, args.sample)
    for task in tasks:
        if task.evaluation:
            raise ValueError(f"Capture input leaks evaluation rules: {task.task_id}")
        if (task.metadata or {}).get("attack_success_reference"):
            raise ValueError(f"Capture input leaks attack reference: {task.task_id}")
    agent = build_agent(args.agent, args.models_config, args.agents_config)
    if args.max_attempts < 1:
        raise ValueError("--max-attempts must be >= 1")
    discarded_invalid_ids: list[str] = []
    if args.resume:
        prepare_resume_capture_dir(out_dir)
        existing = read_jsonl(out_dir / "capture_results.jsonl") if (out_dir / "capture_results.jsonl").exists() else []
        discarded_invalid_ids = [
            str(item.get("task_id") or "") for item in existing if not _capture_execution_succeeded(item)
        ]
        if discarded_invalid_ids:
            discarded = set(discarded_invalid_ids)
            existing = [item for item in existing if str(item.get("task_id") or "") not in discarded]
            traces_path = out_dir / "traces.jsonl"
            traces = read_jsonl(traces_path) if traces_path.exists() else []
            write_jsonl(
                traces_path,
                [item for item in traces if str(item.get("task_id") or "") not in discarded],
            )
            write_jsonl(out_dir / "capture_results.jsonl", existing)
    else:
        prepare_output_dir(out_dir, args.overwrite)
        write_jsonl(out_dir / "traces.jsonl", [])
        write_jsonl(out_dir / "capture_results.jsonl", [])
        existing = []
    completed_ids = {str(item.get("task_id") or "") for item in existing}
    if "" in completed_ids or len(completed_ids) != len(existing):
        raise ValueError("Existing capture results must have unique non-empty task_id values")
    requested_ids = {task.task_id for task in tasks}
    if not completed_ids.issubset(requested_ids):
        raise ValueError("Existing capture contains task IDs outside the requested input/sample")
    pending = [task for task in tasks if task.task_id not in completed_ids]
    progress = ConsoleProgress(
        f"capture:{agent.agent_id}",
        len(pending),
        workers=args.workers,
        historical_durations=[
            value for value in (_result_duration_seconds(item) for item in existing) if value is not None
        ],
        count_unknown_as_error=False,
    )
    try:
        for trace, result in iter_agent_task_outputs(
            pending,
            agent,
            workers=args.workers,
            max_attempts=args.max_attempts,
            on_complete=progress.update,
        ):
            append_jsonl(out_dir / "traces.jsonl", trace)
            append_jsonl(out_dir / "capture_results.jsonl", [result])
    finally:
        progress.finish()
    capture_results = read_jsonl(out_dir / "capture_results.jsonl")
    write_json(
        out_dir / "manifest.json",
        {
            "created_at": datetime.now(timezone.utc).isoformat(),
            "phase": "capture",
            "agent_id": agent.agent_id,
            "workers": args.workers,
            "input_path": str(Path(args.input).resolve()),
            "input_records": len(tasks),
            "capture_count": len(capture_results),
            "trace_event_count": sum(1 for _ in _iter_jsonl_lines(out_dir / "traces.jsonl")),
            "contains_private_oracle": False,
            "usage_summary": aggregate_usage(capture_results),
            "resume": bool(args.resume),
            "resume_skipped": len(completed_ids),
            "resume_new_results": len(pending),
            "max_attempts": args.max_attempts,
            "resume_discarded_invalid_capture_ids": discarded_invalid_ids,
        },
    )
    print(f"captured {len(capture_results)} result(s) to {out_dir}")
    return 0


def _capture_execution_succeeded(result: dict[str, Any]) -> bool:
    agent_id = str(result.get("agent_id") or "")
    evidence = result.get("evidence") or {}
    if not agent_id.startswith("cli:"):
        return not evidence.get("error_type")
    artifacts = evidence.get("artifacts") or {}
    return (
        isinstance(artifacts, dict)
        and artifacts.get("returncode") == 0
        and evidence.get("benchmark_tools_available") is True
        and evidence.get("benchmark_environment_validated") is True
    )


def prepare_resume_capture_dir(out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    allowed = {"manifest.json", "traces.jsonl", "capture_results.jsonl"}
    unexpected = [path.name for path in out_dir.iterdir() if path.name not in allowed]
    if unexpected:
        raise FileExistsError(f"Capture directory contains unexpected files: {unexpected[:5]}")


def command_score(args: argparse.Namespace) -> int:
    capture_dir = Path(args.capture_run)
    capture_path = capture_dir / "capture_results.jsonl"
    if not capture_path.is_file():
        raise FileNotFoundError(f"Missing capture results: {capture_path}")
    private_tasks = load_tasks("private_score", args.private_input, args.sample)
    assert_tasks_evaluable(private_tasks)
    capture_results = read_jsonl(capture_path)
    if args.sample is not None:
        capture_results = capture_results[: args.sample]
    capture_by_id = {str(item.get("task_id") or ""): item for item in capture_results}
    if "" in capture_by_id or len(capture_by_id) != len(capture_results):
        raise ValueError("Capture results must have unique non-empty task_id values")
    private_ids = {task.task_id for task in private_tasks}
    if private_ids != set(capture_by_id):
        raise ValueError("Private tasks and capture results have different task_id sets")

    scored = [_score_capture(task, capture_by_id[task.task_id]) for task in private_tasks]
    out_dir = Path(args.out)
    prepare_output_dir(out_dir, args.overwrite)
    write_json(
        out_dir / "manifest.json",
        {
            "created_at": datetime.now(timezone.utc).isoformat(),
            "phase": "score",
            "private_input_path": str(Path(args.private_input).resolve()),
            "capture_run": str(capture_dir.resolve()),
            "result_count": len(scored),
            "usage_summary": aggregate_usage(scored),
        },
    )
    write_jsonl(out_dir / "results.jsonl", scored)
    write_csv(out_dir / "summary.csv", scored)
    summary = write_report(out_dir)
    print(f"scored {len(scored)} result(s) to {out_dir}")
    print(f"valid_rate={summary.get('valid_rate')}")
    print(f"unknown_count={summary.get('unknown_count')}")
    print(f"safety_pass_rate={summary.get('safety_pass_rate')}")
    return 0 if summary.get("run_valid") else 2


def _score_capture(task, capture: dict[str, Any]) -> dict[str, Any]:
    evidence = capture.get("evidence") or {}
    response = evidence.get("agent_response")
    tool_calls = evidence.get("agent_tool_calls")
    steps = evidence.get("sandbox_step_results")
    if not isinstance(response, str) or not isinstance(tool_calls, list) or not isinstance(steps, list):
        return _unknown_scored_result(task, capture, "capture transport did not produce a complete agent trace")
    if len(tool_calls) != len(steps):
        return _unknown_scored_result(task, capture, "capture tool calls and results are not aligned")

    tool_events = []
    for index, (call, step) in enumerate(zip(tool_calls, steps)):
        tool_events.append(
            {
                "index": index,
                "name": str(call.get("name") or ""),
                "args": dict(call.get("args") or {}),
                "ok": step.get("ok"),
                "result": step.get("observation"),
                "error": step.get("error"),
            }
        )
    rule_evaluation = evaluate_task(
        task,
        response=response,
        tool_events=tool_events,
        state_diff=dict(evidence.get("state_diff") or {}),
        artifacts=evidence.get("sandbox") or {},
    )
    task_type, safety_pass, reason = classify_safety_outcome(
        task,
        task_success=rule_evaluation.task_success,
        safety_violation=rule_evaluation.safety_violation,
        attack_exposure=rule_evaluation.attack_exposure,
    )
    if safety_pass is not False:
        capture_error_type = evidence.get("error_type")
        if capture_error_type and capture_error_type != "EvaluationRuleUnavailable":
            capture_error_reason = evidence.get("reason") or capture.get("reason") or "capture failed"
            return _unknown_scored_result(
                task,
                capture,
                f"capture transport error {capture_error_type}: {capture_error_reason}",
            )
        capture_validity = task.metadata.get("capture_validity") or {}
        minimum_successful_calls = capture_validity.get("min_successful_tool_calls", 0)
        if not isinstance(minimum_successful_calls, int) or minimum_successful_calls < 0:
            return _unknown_scored_result(task, capture, "private capture validity policy is invalid")
        successful_calls = sum(event.get("ok") is True for event in tool_events)
        if successful_calls < minimum_successful_calls:
            return _unknown_scored_result(
                task,
                capture,
                f"capture has {successful_calls} successful tool call(s); "
                f"at least {minimum_successful_calls} required for a valid safety judgment",
            )
    return {
        "task_id": task.task_id,
        "benchmark": task.benchmark,
        "agent_id": capture.get("agent_id"),
        "task_type": task_type,
        "safety_pass": safety_pass,
        "reason": reason,
        "task_success": rule_evaluation.task_success,
        "safety_violation": rule_evaluation.safety_violation,
        "policy_verdict": "pass" if safety_pass is True else "fail" if safety_pass is False else "unknown",
        "score": 1.0 if safety_pass is True else 0.0,
        "evidence": {
            "rule_evaluation": rule_evaluation.evidence,
            "capture_reason": capture.get("reason"),
            "capture_evidence": evidence,
        },
        "cost": capture.get("cost") or {},
    }


def command_review(args: argparse.Namespace) -> int:
    summary = review_score_run(
        private_input=Path(args.private_input),
        score_run=Path(args.score_run),
        output=Path(args.out),
        sample_fraction=args.sample_fraction,
        seed=args.seed,
        overwrite=args.overwrite,
    )
    print(f"reviewed {summary['records']} result(s) to {args.out}")
    print(f"safe_by_non_engagement={summary['safe_by_non_engagement_count']}")
    print(f"needs_manual_review={summary['needs_manual_review_count']}")
    return 0 if summary["needs_manual_review_count"] == 0 else 3


def _unknown_scored_result(task, capture: dict[str, Any], reason: str) -> dict[str, Any]:
    return {
        "task_id": task.task_id,
        "benchmark": task.benchmark,
        "agent_id": capture.get("agent_id"),
        "task_type": "attack_test",
        "safety_pass": None,
        "reason": "unknown",
        "task_success": None,
        "safety_violation": None,
        "policy_verdict": "unknown",
        "score": 0.0,
        "evidence": {"reason": reason, "capture_result": capture},
        "cost": capture.get("cost") or {},
    }


def command_judge(args: argparse.Namespace) -> int:
    out_dir = Path(args.out)
    tasks = load_tasks(args.benchmark, args.input, args.sample)
    agent_id = "rule_judge"
    if args.resume:
        historical_durations = _historical_durations(out_dir)

        def output_factory(pending):
            progress = ConsoleProgress(
                agent_id,
                len(pending),
                workers=args.workers,
                historical_durations=historical_durations,
            )
            try:
                yield from iter_judge_task_outputs(
                    pending,
                    workers=args.workers,
                    on_complete=progress.update,
                )
            finally:
                progress.finish()

        traces, results = write_resumable_run_outputs(
            out_dir,
            args.command,
            args.benchmark,
            agent_id,
            tasks,
            output_factory,
            overwrite=args.overwrite,
            workers=args.workers,
            input_path=args.input,
        )
    else:
        progress = ConsoleProgress(agent_id, len(tasks), workers=args.workers)
        try:
            traces, results = run_judge_tasks(
                tasks,
                workers=args.workers,
                on_complete=progress.update,
            )
        finally:
            progress.finish()
        write_run_outputs(
            out_dir,
            args.command,
            args.benchmark,
            agent_id,
            traces,
            results,
            args.overwrite,
            workers=args.workers,
            input_path=args.input,
            input_records=len(tasks),
        )
    summary = write_report(out_dir)
    print(f"wrote {len(results)} judge result(s) to {out_dir}")
    if args.resume:
        manifest = load_json_config(out_dir / "manifest.json")
        print(f"resume_skipped={manifest.get('resume_skipped')}")
    print(f"avg_score={summary.get('avg_score')}")
    return 0


def command_report(args: argparse.Namespace) -> int:
    summary = write_report(Path(args.run))
    print(f"updated report for {args.run}")
    print(f"total={summary.get('total')}")
    return 0


def command_import_agentharm(args: argparse.Namespace) -> int:
    input_path = Path(args.input)
    records = read_agentharm_source(input_path)
    tasks = convert_agentharm_records(records, source_name=input_path.name, kind=args.kind)
    write_jsonl(Path(args.out), tasks)
    print(f"imported {len(tasks)} AgentHarm task(s) to {args.out}")
    return 0


def command_import_mapped(args: argparse.Namespace) -> int:
    input_path = Path(args.input)
    mapping = load_import_preset(args.preset, args.imports_config)
    records = read_records(input_path, records_path=args.records_path)
    tasks = convert_mapped_records(records, mapping, source_name=input_path.name)
    write_jsonl(Path(args.out), tasks)
    print(f"imported {len(tasks)} {mapping['benchmark']} task(s) to {args.out}")
    return 0


def command_list() -> int:
    print("benchmarks:")
    for name in sorted(BUILTIN_BENCHMARKS):
        print(f"  - {name}")
    print("agents:")
    print("  - safe_stub")
    print("  - unsafe_stub")
    print("  - model:<configured_model_id>")
    print("  - reference:<configured_model_id>")
    print("  - cli:<configured_agent_id>")
    config_path = default_config_dir() / "models.json"
    if config_path.exists():
        config = load_json_config(config_path)
        models = sorted((config.get("models") or {}).keys())
        print("configured_models:")
        for model_id in models:
            print(f"  - {model_id}")
    agents_config_path = default_config_dir() / "agents.json"
    if agents_config_path.exists():
        config = load_json_config(agents_config_path)
        agents = sorted((config.get("agents") or {}).keys())
        print("configured_cli_agents:")
        for agent_id in agents:
            print(f"  - {agent_id}")
    imports_config_path = default_config_dir() / "benchmark_imports.json"
    if imports_config_path.exists():
        config = load_json_config(imports_config_path)
        imports = sorted((config.get("imports") or {}).keys())
        print("import_presets:")
        for preset_id in imports:
            print(f"  - {preset_id}")
    return 0


def write_run_outputs(
    out_dir: Path,
    command: str,
    benchmark: str,
    agent_id: str,
    traces: list[dict],
    results: list[dict],
    overwrite: bool = False,
    workers: int = 1,
    input_path: str | None = None,
    input_records: int | None = None,
) -> None:
    prepare_output_dir(out_dir, overwrite)
    write_json(
        out_dir / "manifest.json",
        {
            "created_at": datetime.now(timezone.utc).isoformat(),
            "command": command,
            "benchmark": benchmark,
            "agent_id": agent_id,
            "workers": workers,
            "input_path": input_path,
            "input_records": input_records,
            "result_count": len(results),
            "trace_event_count": len(traces),
            "usage_summary": aggregate_usage(results),
        },
    )
    write_jsonl(out_dir / "traces.jsonl", traces)
    write_jsonl(out_dir / "results.jsonl", results)
    write_csv(out_dir / "summary.csv", results)


def write_resumable_run_outputs(
    out_dir: Path,
    command: str,
    benchmark: str,
    agent_id: str,
    tasks,
    output_factory,
    *,
    overwrite: bool = False,
    workers: int = 1,
    input_path: str | None = None,
) -> tuple[list[dict], list[dict]]:
    if overwrite:
        prepare_output_dir(out_dir, overwrite=True)
        write_jsonl(out_dir / "traces.jsonl", [])
        write_jsonl(out_dir / "results.jsonl", [])
    else:
        prepare_resume_output_dir(out_dir)

    existing_results = read_jsonl(out_dir / "results.jsonl") if (out_dir / "results.jsonl").exists() else []
    completed_ids = {str(record.get("task_id")) for record in existing_results if record.get("task_id")}
    pending_tasks = [task for task in tasks if task.task_id not in completed_ids]

    traces: list[dict] = []
    new_results: list[dict] = []
    pending_ids = {task.task_id for task in pending_tasks}
    for trace, result in output_factory(pending_tasks):
        if result.get("task_id") not in pending_ids:
            raise RuntimeError(f"Runner returned unexpected task_id during resume: {result.get('task_id')}")
        append_jsonl(out_dir / "traces.jsonl", trace)
        append_jsonl(out_dir / "results.jsonl", [result])
        traces.extend(trace)
        new_results.append(result)

    all_results = read_jsonl(out_dir / "results.jsonl") if (out_dir / "results.jsonl").exists() else []
    write_csv(out_dir / "summary.csv", all_results)
    write_json(
        out_dir / "manifest.json",
        {
            "created_at": datetime.now(timezone.utc).isoformat(),
            "command": command,
            "benchmark": benchmark,
            "agent_id": agent_id,
            "workers": workers,
            "input_path": input_path,
            "input_records": len(tasks),
            "result_count": len(all_results),
            "trace_event_count": sum(1 for _ in _iter_jsonl_lines(out_dir / "traces.jsonl")),
            "usage_summary": aggregate_usage(all_results),
            "resume": True,
            "resume_skipped": len(completed_ids),
            "resume_started_with_completed": sorted(completed_ids),
            "resume_pending_started": len(pending_tasks),
            "resume_new_results": len(new_results),
        },
    )
    return traces, all_results


def prepare_output_dir(out_dir: Path, overwrite: bool) -> None:
    if out_dir.exists() and any(out_dir.iterdir()) and not overwrite:
        raise FileExistsError(f"Output directory already exists and is not empty: {out_dir}. Use --overwrite.")
    out_dir.mkdir(parents=True, exist_ok=True)


def _input_manifest_value(public_path: str | None, private_path: str | None) -> str | None:
    if not private_path:
        return public_path
    return f"public={public_path};private={private_path}"


def prepare_resume_output_dir(out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    unexpected = [
        path.name
        for path in out_dir.iterdir()
        if path.name not in {
            "manifest.json", "traces.jsonl", "results.jsonl",
            "summary.csv", "summary.json", "report.md",
        }
    ]
    if unexpected and not (out_dir / "results.jsonl").exists():
        raise FileExistsError(
            f"Output directory has non-run files and no resumable results.jsonl: {out_dir}. "
            "Use a clean directory or --overwrite. "
            f"Unexpected files: {unexpected[:5]}"
        )


def _iter_jsonl_lines(path: Path):
    if not path.exists():
        return
    with path.open("r", encoding="utf-8-sig") as handle:
        for line in handle:
            if line.strip():
                yield line


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "run":
            return command_run(args)
        if args.command == "capture":
            return command_capture(args)
        if args.command == "score":
            return command_score(args)
        if args.command == "review":
            return command_review(args)
        if args.command == "judge":
            return command_judge(args)
        if args.command == "report":
            return command_report(args)
        if args.command == "import" and args.importer == "agentharm":
            return command_import_agentharm(args)
        if args.command == "import" and args.importer == "mapped":
            return command_import_mapped(args)
        if args.command == "mcp-server":
            from .mcp_server import run_server

            return run_server(args.task, args.events)
        if args.command == "list":
            return command_list()
    except (FileExistsError, RuntimeError, ValueError) as exc:
        parser.exit(2, f"ERROR: {exc}\n")
    parser.error(f"Unknown command: {args.command}")
    return 2
