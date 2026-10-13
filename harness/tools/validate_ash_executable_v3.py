from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from agent_safety_harness.adapters.executable_v2 import ExecutableTaskRuntime
from agent_safety_harness.evaluation import evaluate_task
from agent_safety_harness.schema import TaskSpec


def main() -> int:
    parser = argparse.ArgumentParser(description="Replay safe and intentionally unsafe traces for ASH executable tasks.")
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()

    records = read_jsonl(args.input)
    safe_failures = []
    unsafe_failures = []
    safe_counts: Counter[str] = Counter()
    unsafe_counts: Counter[str] = Counter()
    non_applicable: Counter[str] = Counter()

    for record in records:
        task = TaskSpec.from_record(record)
        reference = (task.metadata.get("attack_success_reference") or {})
        safe_replay = reference.get("safe_replay") if isinstance(reference, dict) else None
        unsafe_replay = reference.get("unsafe_replay") if isinstance(reference, dict) else None
        runtime_spec = task.metadata.get("ash_runtime") or {}
        if isinstance(safe_replay, dict) and isinstance(unsafe_replay, dict):
            safe_specs = list(safe_replay.get("tool_events") or [])
            safe_response = str(safe_replay.get("response") or "")
            unsafe_specs = list(unsafe_replay.get("tool_events") or [])
            unsafe_response = str(unsafe_replay.get("response") or "")
            expect_task_success = False
        else:
            safe_specs = events_for_true(task.evaluation["task_success"], runtime_spec)
            safe_response = ""
            unsafe_specs = events_for_true(task.evaluation["safety_violation"], runtime_spec)
            unsafe_response = ""
            expect_task_success = True

        safe, safe_events = run_replay(task, safe_specs, safe_response)
        safe_ok = (
            safe.available
            and safe.attack_exposure is True
            and safe.safety_violation is False
            and replay_calls_match(safe_specs, safe_events)
        )
        if expect_task_success:
            safe_ok = safe_ok and safe.task_success is True
        if safe_ok:
            safe_counts[task.benchmark] += 1
        else:
            safe_failures.append(failure_record(task, safe, safe_events))

        if unsafe_specs is None:
            non_applicable[task.benchmark] += 1
        else:
            unsafe, unsafe_events = run_replay(task, unsafe_specs, unsafe_response)
            if (
                unsafe.available
                and unsafe.attack_exposure is True
                and unsafe.safety_violation is True
                and replay_calls_match(unsafe_specs, unsafe_events)
            ):
                unsafe_counts[task.benchmark] += 1
            else:
                unsafe_failures.append(failure_record(task, unsafe, unsafe_events))

    summary = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "input": str(args.input.resolve()),
        "records": len(records),
        "safe_replay_passed": sum(safe_counts.values()),
        "safe_replay_failures": len(safe_failures),
        "unsafe_replay_passed": sum(unsafe_counts.values()),
        "unsafe_replay_not_applicable": sum(non_applicable.values()),
        "unsafe_replay_failures": len(unsafe_failures),
        "isolated_replay_state": True,
        "replay_call_expectations_checked": True,
        "attack_exposure_checked": True,
        "safe_by_source": dict(safe_counts),
        "unsafe_by_source": dict(unsafe_counts),
        "unsafe_not_applicable_by_source": dict(non_applicable),
        "passed": not safe_failures and not unsafe_failures,
        "safe_failure_examples": safe_failures[:20],
        "unsafe_failure_examples": unsafe_failures[:20],
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if summary["passed"] else 1


def run_replay(
    task: TaskSpec, specs: list[dict[str, Any]] | None, response: str
) -> tuple[Any, list[dict[str, Any]]]:
    runtime = ExecutableTaskRuntime(task)
    try:
        events = execute_events(runtime, specs)
        outcome = evaluate_task(
            task,
            response=response,
            tool_events=events,
            state_diff=runtime.state_diff(),
        )
        return outcome, events
    finally:
        runtime.close()


def replay_calls_match(
    specs: list[dict[str, Any]] | None, events: list[dict[str, Any]]
) -> bool:
    expected = specs or []
    if len(expected) != len(events):
        return False
    return all(
        event.get("name") == spec.get("name")
        and event.get("ok") is bool(spec.get("ok", True))
        for spec, event in zip(expected, events)
    )


def events_for_true(rule: dict[str, Any], runtime: dict[str, Any]) -> list[dict[str, Any]] | None:
    op = rule["op"]
    if op == "constant":
        return [] if rule["value"] is True else None
    if op == "tool_called":
        return [{"name": rule.get("name"), "args": materialize(rule.get("args") or {}, runtime), "ok": True}]
    if op == "tool_args_contains_any":
        return [
            {
                "name": rule.get("name") or "finish_task",
                "args": {"status": "completed", "summary": str(rule["values"][0])},
                "ok": True,
            }
        ]
    if op == "tool_sequence":
        return [{"name": name, "args": {}, "ok": True} for name in rule["names"]]
    if op == "all":
        events: list[dict[str, Any]] = []
        for child in rule["rules"]:
            child_events = events_for_true(child, runtime)
            if child_events is None:
                return None
            events.extend(child_events)
        return events
    if op == "any":
        for child in rule["rules"]:
            child_events = events_for_true(child, runtime)
            if child_events is not None:
                return child_events
        return None
    if op == "not":
        child = events_for_true(rule["rule"], runtime)
        return [] if child else None
    raise ValueError(f"Replay generator does not support rule operation {op!r}")


def execute_events(runtime: ExecutableTaskRuntime, specs: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    if specs is None:
        return []
    tools = {tool["name"]: tool for tool in runtime.runtime["tools"]}
    events = []
    for index, spec in enumerate(specs):
        name = str(spec.get("name") or "")
        arguments = dict(spec.get("args") or {})
        fill_required(arguments, tools.get(name, {}), runtime.runtime)
        outcome = runtime.call_tool(name, arguments)
        events.append(
            {
                "index": index,
                "name": name,
                "args": arguments,
                "ok": outcome.ok,
                "result": outcome.observation,
                "error": outcome.error,
            }
        )
    return events


def fill_required(arguments: dict[str, Any], tool: dict[str, Any], runtime: dict[str, Any]) -> None:
    schema = tool.get("parameters") or tool.get("inputSchema") or {}
    properties = schema.get("properties") or {}
    for name in schema.get("required") or []:
        if name in arguments:
            continue
        spec = properties.get(name) or {}
        if "const" in spec:
            arguments[name] = spec["const"]
        elif spec.get("enum"):
            arguments[name] = spec["enum"][0]
        elif name == "summary":
            arguments[name] = "Benchmark outcome recorded."
        elif name == "path":
            arguments[name] = "/workspace/task-input"
        elif name == "command":
            arguments[name] = "pwd"
        else:
            arguments[name] = example_for_type(spec.get("type"))


def materialize(value: Any, runtime: dict[str, Any]) -> Any:
    if isinstance(value, list):
        return [materialize(item, runtime) for item in value]
    if not isinstance(value, dict):
        return value
    if value and all(str(key).startswith("$") for key in value):
        if "$eq" in value:
            return value["$eq"]
        if "$in" in value:
            return value["$in"][0]
        if "$contains" in value:
            return str(value["$contains"])
        if "$regex" in value:
            pattern = str(value["$regex"])
            for candidate in collect_strings(runtime):
                try:
                    if re.search(pattern, candidate):
                        return candidate
                except re.error:
                    break
            return simple_regex_example(pattern)
        return "value"
    return {key: materialize(item, runtime) for key, item in value.items()}


def collect_strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        result = []
        for item in value.values():
            result.extend(collect_strings(item))
        return result
    if isinstance(value, list):
        result = []
        for item in value:
            result.extend(collect_strings(item))
        return result
    return []


def simple_regex_example(pattern: str) -> str:
    try:
        generated = emit_regex_tokens(re._parser.parse(pattern, 0))
        if re.search(pattern, generated):
            return generated
    except (AttributeError, re.error, TypeError, ValueError):
        pass
    value = pattern
    value = re.sub(r"\\s[+*]?", " ", value)
    value = re.sub(r"\\d[+*]?", "1", value)
    value = re.sub(r"\\w[+*]?", "x", value)
    value = value.replace(".*", " sample ").replace(".+", "sample")
    value = re.sub(r"\(\?:([^)]*)\)", lambda match: match.group(1).split("|")[0], value)
    value = re.sub(r"\[\^?([^]]+)\][+*]?", lambda match: match.group(1)[:1] or "x", value)
    value = re.sub(r"[\^$?*+(){}\[\]|]", "", value)
    value = value.replace("\\/", "/").replace("\\.", ".").replace("\\-", "-")
    return value.replace("\\", "") or "matching value"


def emit_regex_tokens(tokens: Any) -> str:
    constants = re._constants
    output = []
    for operation, argument in tokens:
        if operation is constants.LITERAL:
            output.append(chr(argument))
        elif operation is constants.NOT_LITERAL:
            output.append("x" if argument != ord("x") else "y")
        elif operation is constants.ANY:
            output.append("x")
        elif operation is constants.IN:
            output.append(emit_character_class(argument))
        elif operation in {constants.MAX_REPEAT, constants.MIN_REPEAT, constants.POSSESSIVE_REPEAT}:
            minimum, _, children = argument
            output.append(emit_regex_tokens(children) * minimum)
        elif operation is constants.SUBPATTERN:
            output.append(emit_regex_tokens(argument[-1]))
        elif operation is constants.BRANCH:
            branches = argument[1]
            output.append(emit_regex_tokens(branches[0]) if branches else "")
        elif operation is constants.CATEGORY:
            output.append(category_example(argument))
        elif operation is constants.GROUPREF:
            output.append("x")
        elif operation in {constants.ASSERT, constants.ASSERT_NOT, constants.AT}:
            continue
    return "".join(output)


def emit_character_class(items: Any) -> str:
    constants = re._constants
    negate = any(operation is constants.NEGATE for operation, _ in items)
    for operation, argument in items:
        if operation is constants.LITERAL:
            candidate = chr(argument)
            if not negate:
                return candidate
        elif operation is constants.RANGE and not negate:
            return chr(argument[0])
        elif operation is constants.CATEGORY and not negate:
            return category_example(argument)
    excluded = {chr(argument) for operation, argument in items if operation is constants.LITERAL}
    return next((candidate for candidate in "x0_- " if candidate not in excluded), "z")


def category_example(category: Any) -> str:
    constants = re._constants
    if category in {constants.CATEGORY_DIGIT, constants.CATEGORY_UNI_DIGIT}:
        return "0"
    if category in {constants.CATEGORY_SPACE, constants.CATEGORY_UNI_SPACE}:
        return " "
    if category in {constants.CATEGORY_NOT_SPACE, constants.CATEGORY_UNI_NOT_SPACE}:
        return "x"
    if category in {constants.CATEGORY_NOT_DIGIT, constants.CATEGORY_UNI_NOT_DIGIT}:
        return "x"
    return "x"


def example_for_type(value: Any) -> Any:
    return {"object": {}, "array": [], "boolean": True, "integer": 1, "number": 1.0}.get(value, "value")


def failure_record(task: TaskSpec, outcome: Any, events: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "task_id": task.task_id,
        "benchmark": task.benchmark,
        "task_success": outcome.task_success,
        "attack_exposure": outcome.attack_exposure,
        "safety_violation": outcome.safety_violation,
        "reason": outcome.reason,
        "events": events,
    }


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    records = []
    with path.open("r", encoding="utf-8-sig") as handle:
        for line in handle:
            if line.strip():
                records.append(json.loads(line))
    return records


if __name__ == "__main__":
    raise SystemExit(main())
