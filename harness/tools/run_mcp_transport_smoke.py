from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys
from typing import Any


def main() -> int:
    parser = argparse.ArgumentParser(description="Exercise native benchmark environments over MCP stdio transport.")
    parser.add_argument("--runtime", type=Path, required=True)
    parser.add_argument("--private", type=Path, required=True)
    parser.add_argument("--ash", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    runtime_records = index_by_task_id(read_jsonl(args.runtime))
    private_records = index_by_task_id(read_jsonl(args.private))
    if runtime_records.keys() != private_records.keys():
        raise ValueError("Runtime/private task IDs do not match")

    args.output.mkdir(parents=True, exist_ok=False)
    task_root = args.output / "tasks"
    task_root.mkdir()
    results: list[dict[str, Any]] = []
    for task_id, runtime_record in runtime_records.items():
        private_record = private_records[task_id]
        result = run_task(args.ash, task_root, runtime_record, private_record)
        results.append(result)
        print(f"[{result['status']}] {task_id}: calls={result['tool_call_count']}")

    passed = sum(item["status"] == "pass" for item in results)
    report = {
        "schema_version": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "runtime_input": str(args.runtime.resolve()),
        "private_input": str(args.private.resolve()),
        "ash_entrypoint": str(args.ash.resolve()),
        "task_count": len(results),
        "passed": passed,
        "failed": len(results) - passed,
        "all_environment_validated": all(item["environment_validated"] for item in results),
        "all_calls_have_state_diff": all(item["all_calls_have_state_diff"] for item in results),
        "max_tool_call_count": max((item["tool_call_count"] for item in results), default=0),
        "twenty_call_limit_hits": sum(item["hit_twenty_call_limit"] for item in results),
        "results": results,
    }
    (args.output / "transport_smoke.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (args.output / "transport_smoke.md").write_text(render_markdown(report), encoding="utf-8")
    return 0 if passed == len(results) else 1


def run_task(
    ash_entrypoint: Path,
    task_root: Path,
    runtime_record: dict[str, Any],
    private_record: dict[str, Any],
) -> dict[str, Any]:
    task_id = str(runtime_record["task_id"])
    task_dir = task_root / safe_name(task_id)
    task_dir.mkdir()
    task_path = task_dir / "runtime.task.json"
    events_path = task_dir / "mcp_events.jsonl"
    protocol_path = task_dir / "protocol.jsonl"
    task_path.write_text(json.dumps(runtime_record, ensure_ascii=False) + "\n", encoding="utf-8")

    calls = reference_calls(runtime_record, private_record)
    command = [
        sys.executable,
        str(ash_entrypoint.resolve()),
        "mcp-server",
        "--task",
        str(task_path.resolve()),
        "--events",
        str(events_path.resolve()),
    ]
    process = subprocess.Popen(
        command,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        cwd=str(ash_entrypoint.resolve().parent),
    )
    assert process.stdin is not None
    assert process.stdout is not None
    transcript: list[dict[str, Any]] = []
    next_id = 1

    def exchange(method: str, params: dict[str, Any]) -> dict[str, Any]:
        nonlocal next_id
        request = {"jsonrpc": "2.0", "id": next_id, "method": method, "params": params}
        next_id += 1
        process.stdin.write(json.dumps(request, ensure_ascii=False) + "\n")
        process.stdin.flush()
        response_line = process.stdout.readline()
        if not response_line:
            raise RuntimeError(f"MCP server closed before responding to {method}")
        response = json.loads(response_line)
        transcript.extend(({"direction": "request", "message": request}, {"direction": "response", "message": response}))
        return response

    errors: list[str] = []
    call_responses: list[dict[str, Any]] = []
    stderr = ""
    returncode: int | None = None
    try:
        initialized = exchange(
            "initialize",
            {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "ash-native-transport-smoke", "version": "1.0.0"},
            },
        )
        if initialized.get("result", {}).get("serverInfo", {}).get("name") != "agent-safety-benchmark-tools":
            errors.append("initialize did not return the benchmark MCP server")
        notification = {"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}}
        process.stdin.write(json.dumps(notification) + "\n")
        process.stdin.flush()
        transcript.append({"direction": "request", "message": notification})
        listed = exchange("tools/list", {})
        exposed_names = {
            str(tool.get("name")) for tool in listed.get("result", {}).get("tools", []) if isinstance(tool, dict)
        }
        if not exposed_names:
            errors.append("tools/list returned no tools")
        for call in calls:
            name = str(call["name"])
            if name not in exposed_names:
                errors.append(f"reference tool was not exposed: {name}")
                continue
            response = exchange("tools/call", {"name": name, "arguments": dict(call.get("args") or {})})
            call_responses.append(response)
            if response.get("result", {}).get("isError") is True:
                errors.append(f"reference tool call failed: {name}")
    except Exception as exc:
        errors.append(f"transport exception: {type(exc).__name__}: {exc}")
    finally:
        process.stdin.close()
        try:
            process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
            errors.append("MCP server did not exit after stdin closed")
        stderr = process.stderr.read() if process.stderr is not None else ""
        returncode = process.returncode
        if returncode != 0:
            errors.append(f"MCP server exited with code {returncode}: {stderr.strip()[:300]}")

    write_jsonl(protocol_path, transcript)
    events = read_jsonl(events_path) if events_path.exists() else []
    server_started = next((event for event in events if event.get("event_type") == "server_started"), {})
    tool_events = [event for event in events if event.get("event_type") == "tool_call"]
    if server_started.get("environment_validated") is not True:
        errors.append("server_started.environment_validated is not true")
    if len(tool_events) != len(calls):
        errors.append(f"expected {len(calls)} tool events, observed {len(tool_events)}")
    calls_without_diff = [event.get("name") for event in tool_events if not isinstance(event.get("state_diff"), dict)]
    if calls_without_diff:
        errors.append(f"tool events missing state_diff: {calls_without_diff}")
    failed_events = [event.get("name") for event in tool_events if event.get("ok") is not True]
    if failed_events:
        errors.append(f"tool events were not successful: {failed_events}")
    limit_hits = [event for event in tool_events if "20 tool-call" in str(event.get("error") or "")]

    mutations = []
    for event in tool_events:
        diff = event.get("state_diff") or {}
        changed = {key: value for key, value in diff.items() if key != "last_call"}
        if changed:
            mutations.append({"tool": event.get("name"), "state_diff": changed})
    result = {
        "task_id": task_id,
        "benchmark": runtime_record.get("benchmark"),
        "status": "pass" if not errors else "fail",
        "environment_adapter": server_started.get("environment_adapter"),
        "environment_validated": server_started.get("environment_validated") is True,
        "environment_source_revision": server_started.get("environment_source_revision"),
        "reference_calls": calls,
        "tool_call_count": len(tool_events),
        "all_calls_have_state_diff": len(tool_events) == len(calls) and not calls_without_diff,
        "successful_tool_calls": sum(event.get("ok") is True for event in tool_events),
        "state_mutations": mutations,
        "hit_twenty_call_limit": bool(limit_hits),
        "server_returncode": returncode,
        "stderr": stderr,
        "artifacts": {
            "protocol": str(protocol_path.resolve()),
            "events": str(events_path.resolve()),
        },
        "errors": errors,
    }
    (task_dir / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return result


def reference_calls(runtime_record: dict[str, Any], private_record: dict[str, Any]) -> list[dict[str, Any]]:
    reference = private_record.get("metadata", {}).get("attack_success_reference", {})
    unsafe_events = reference.get("unsafe_replay", {}).get("tool_events") or []
    if isinstance(unsafe_events, dict):
        unsafe_events = [unsafe_events]
    calls = [{"name": str(event["name"]), "args": dict(event.get("args") or {})} for event in unsafe_events]
    if calls:
        return calls
    if runtime_record.get("benchmark") != "agentleak":
        raise ValueError(f"No transport reference calls for {runtime_record.get('task_id')}")
    return agentleak_reference_calls(runtime_record, reference)


def agentleak_reference_calls(runtime_record: dict[str, Any], reference: dict[str, Any]) -> list[dict[str, Any]]:
    unsafe_value = str(reference.get("unsafe_replay", {}).get("response") or "")
    runtime_tools = runtime_record.get("metadata", {}).get("ash_runtime", {}).get("tools", [])
    for spec in runtime_tools:
        implementation = spec.get("implementation") or {}
        if implementation.get("kind") != "record_query":
            continue
        for record in implementation.get("records") or []:
            if unsafe_value and unsafe_value in json.dumps(record, ensure_ascii=False):
                fields = record.get("fields") or {}
                selector_key = next(
                    (key for key in fields if key.endswith("_id")),
                    next((key for key in ("name", "email") if key in fields), None),
                )
                if selector_key is None:
                    raise ValueError("AgentLeak matching record has no deterministic selector")
                properties = spec.get("parameters", {}).get("properties", {})
                capability = next(iter(properties), None)
                args = {selector_key: fields[selector_key]}
                if capability:
                    args = {capability: args}
                return [
                    {"name": str(spec["name"]), "args": args},
                    {"name": "write_log", "args": {"message": unsafe_value}},
                ]
    raise ValueError("AgentLeak unsafe reference value was not found in the task-local source records")


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Native MCP Transport Smoke",
        "",
        f"- Tasks: {report['task_count']}",
        f"- Passed: {report['passed']}",
        f"- Failed: {report['failed']}",
        f"- All environments validated: {str(report['all_environment_validated']).lower()}",
        f"- All calls include state_diff: {str(report['all_calls_have_state_diff']).lower()}",
        f"- Maximum calls in one task: {report['max_tool_call_count']}",
        f"- 20-call-limit hits: {report['twenty_call_limit_hits']}",
        "",
        "| Task | Source | Status | Calls | Successful | State mutations |",
        "|---|---|---:|---:|---:|---:|",
    ]
    for item in report["results"]:
        lines.append(
            f"| {item['task_id']} | {item['benchmark']} | {item['status']} | "
            f"{item['tool_call_count']} | {item['successful_tool_calls']} | {len(item['state_mutations'])} |"
        )
    lines.extend(
        [
            "",
            "MSB services, files, and processes are task-local mirrors. They do not connect to live user accounts, "
            "the host filesystem, host processes, or external services.",
            "",
        ]
    )
    return "\n".join(lines)


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open("r", encoding="utf-8-sig") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def write_jsonl(path: Path, records: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")


def index_by_task_id(records: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    indexed = {str(record["task_id"]): record for record in records}
    if len(indexed) != len(records):
        raise ValueError("Duplicate task IDs")
    return indexed


def safe_name(value: str) -> str:
    return "".join(character if character.isalnum() or character in "-_" else "_" for character in value)


if __name__ == "__main__":
    raise SystemExit(main())
