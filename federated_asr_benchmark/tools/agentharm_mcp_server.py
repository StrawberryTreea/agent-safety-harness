from __future__ import annotations

import argparse
import asyncio
import importlib.util
import json
from pathlib import Path
import sys
import traceback
import types
from typing import Any, TextIO


PACKAGE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PACKAGE_ROOT))

from product_eval.agentharm import AgentHarmAdapter


def _write_result(stdout: TextIO, request_id: Any, result: dict[str, Any]) -> None:
    stdout.write(json.dumps({"jsonrpc": "2.0", "id": request_id, "result": result}) + "\n")
    stdout.flush()


def _write_error(stdout: TextIO, request_id: Any, code: int, message: str) -> None:
    stdout.write(
        json.dumps(
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {"code": code, "message": message},
            }
        )
        + "\n"
    )
    stdout.flush()


def _append_event(path: Path, event: dict[str, Any]) -> None:
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(event, separators=(",", ":")) + "\n")


def _write_json_atomic(path: Path, value: dict[str, Any]) -> None:
    temporary = path.with_name(f"{path.name}.tmp")
    temporary.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def _install_inspect_tool_stub() -> None:
    inspect_module = types.ModuleType("inspect_ai")
    tool_module = types.ModuleType("inspect_ai.tool")

    class Tool:
        pass

    def tool(function=None):
        if function is None:
            return lambda decorated: decorated
        return function

    tool_module.Tool = Tool
    tool_module.tool = tool
    inspect_module.tool = tool_module
    sys.modules["inspect_ai"] = inspect_module
    sys.modules["inspect_ai.tool"] = tool_module


def _load_tool(tools_root: Path, name: str):
    _install_inspect_tool_stub()
    path = tools_root / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"agentharm_tool_{name}", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load AgentHarm tool: {name}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    factory = getattr(module, name)
    return factory()


def _validate_arguments(definition: dict[str, Any], arguments: Any) -> str | None:
    if not isinstance(arguments, dict):
        return "Tool arguments must be an object."
    schema = definition["inputSchema"]
    properties = schema["properties"]
    missing = [name for name in schema["required"] if name not in arguments]
    unexpected = sorted(set(arguments) - set(properties))
    if missing:
        return f"Missing required arguments: {', '.join(missing)}"
    if unexpected:
        return f"Unexpected arguments: {', '.join(unexpected)}"
    return None


def run_server(
    *,
    data_root: Path,
    runtime_root: Path,
    native_case_id: str,
    result_path: Path,
    events_path: Path,
    server_name: str = "agentharm",
    stdin: TextIO = sys.stdin,
    stdout: TextIO = sys.stdout,
    max_tool_calls: int = 30,
) -> int:
    adapter = AgentHarmAdapter(data_root, runtime_root)
    split, case = adapter.load_case(native_case_id)
    definitions = adapter.tool_definitions(case)
    definition_index = {definition["name"]: definition for definition in definitions}
    events_path.parent.mkdir(parents=True, exist_ok=True)
    events_path.write_text("", encoding="utf-8")
    calls: list[dict[str, Any]] = []
    error = None
    return_code = 0
    call_count = 0
    try:
        _append_event(
            events_path,
            {"event_type": "server_started", "tools": sorted(definition_index)},
        )
        for line in stdin:
            if not line.strip():
                continue
            try:
                request = json.loads(line.lstrip("\ufeff"))
            except json.JSONDecodeError as exc:
                _append_event(
                    events_path, {"event_type": "protocol_error", "error": str(exc)}
                )
                continue
            request_id = request.get("id")
            method = request.get("method")
            params = request.get("params") or {}
            if method == "initialize":
                response = {
                    "protocolVersion": str(
                        params.get("protocolVersion") or "2024-11-05"
                    ),
                    "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": {"name": server_name, "version": "0.1.0"},
                }
            elif method == "tools/list":
                response = {"tools": definitions}
            elif method == "resources/list":
                response = {"resources": []}
            elif method == "resources/templates/list":
                response = {"resourceTemplates": []}
            elif method == "prompts/list":
                response = {"prompts": []}
            elif method == "tools/call":
                call_count += 1
                name = str(params.get("name") or "")
                arguments = params.get("arguments") or {}
                is_error = False
                if call_count > max_tool_calls:
                    output, is_error = (
                        f"AgentHarm maximum of {max_tool_calls} tool calls exceeded.",
                        True,
                    )
                elif name not in definition_index:
                    output, is_error = f"Unknown AgentHarm tool: {name}", True
                elif validation_error := _validate_arguments(
                    definition_index[name], arguments
                ):
                    output, is_error = validation_error, True
                else:
                    try:
                        official_tool = _load_tool(adapter.tools_root, name)
                        output = str(asyncio.run(official_tool(**arguments)))
                    except Exception as exc:
                        output, is_error = f"{type(exc).__name__}: {exc}", True
                call = {
                    "name": name,
                    "arguments": arguments,
                    "response": output,
                    "is_error": is_error,
                }
                calls.append(call)
                _append_event(events_path, {"event_type": "tool_call", **call})
                response = {
                    "content": [{"type": "text", "text": output}],
                    "isError": is_error,
                }
            elif method == "ping":
                response = {}
            elif request_id is None:
                continue
            else:
                _write_error(stdout, request_id, -32601, f"Method not found: {method}")
                continue
            if request_id is not None:
                _write_result(stdout, request_id, response)
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
        _append_event(
            events_path,
            {
                "event_type": "server_error",
                "error": error,
                "traceback": traceback.format_exc(),
            },
        )
        return_code = 1
    finally:
        native = {
            "benchmark_version": adapter.benchmark_version,
            "native_case_id": native_case_id,
            "split": split,
            "behavior_id": case.get("id"),
            "category": case.get("category"),
            "grading_function": case.get("grading_function"),
            "target_functions": case.get("target_functions"),
            "tool_calls": calls,
            "error": error,
        }
        result_path.parent.mkdir(parents=True, exist_ok=True)
        _write_json_atomic(result_path, native)
    return return_code


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Expose one pinned AgentHarm case through its official tool emulators."
    )
    parser.add_argument("--data-root", required=True, type=Path)
    parser.add_argument("--runtime-root", required=True, type=Path)
    parser.add_argument("--native-case-id", required=True)
    parser.add_argument("--result", required=True, type=Path)
    parser.add_argument("--events", required=True, type=Path)
    parser.add_argument("--server-name", default="agentharm")
    parser.add_argument("--max-tool-calls", type=int, default=30)
    args = parser.parse_args()
    return run_server(
        data_root=args.data_root.resolve(),
        runtime_root=args.runtime_root.resolve(),
        native_case_id=args.native_case_id,
        result_path=args.result.resolve(),
        events_path=args.events.resolve(),
        server_name=args.server_name,
        max_tool_calls=args.max_tool_calls,
    )


if __name__ == "__main__":
    raise SystemExit(main())
