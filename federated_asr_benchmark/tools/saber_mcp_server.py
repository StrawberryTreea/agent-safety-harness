from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import sys
import traceback
from typing import Any, Iterator, TextIO


MAX_WIRE_MESSAGE_BYTES = 1_048_576
_COMMAND_CALL_RE = re.compile(
    r'"command"\s*:\s*"(?P<command>.*)"\s*}\s*}\s*}\s*$',
    re.DOTALL,
)
_REQUEST_ID_RE = re.compile(
    r'"id"\s*:\s*(?P<id>"(?:\\.|[^"\\])*"|-?\d+(?:\.\d+)?)\s*(?=,|})'
)


def _write_result(stdout: TextIO, request_id: Any, result: dict[str, Any]) -> None:
    stdout.write(json.dumps({"jsonrpc": "2.0", "id": request_id, "result": result}) + "\n")
    stdout.flush()


def _write_error(stdout: TextIO, request_id: Any, code: int, message: str) -> None:
    stdout.write(
        json.dumps(
            {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}},
        )
        + "\n"
    )
    stdout.flush()


def _append_event(path: Path, event: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(event, separators=(",", ":")) + "\n")


def _write_json_atomic(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.tmp")
    temporary.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def _wire_fingerprint(wire: str) -> dict[str, Any]:
    payload = wire.encode("utf-8", errors="surrogatepass")
    return {"wire_bytes": len(payload), "wire_sha256": hashlib.sha256(payload).hexdigest()}


def _decode_lenient_json_string(value: str) -> str:
    """Decode JSON escapes while retaining quotes/newlines left raw by the client."""
    escapes = {"\"": "\"", "\\": "\\", "/": "/", "b": "\b", "f": "\f", "n": "\n", "r": "\r", "t": "\t"}
    output: list[str] = []
    index = 0
    while index < len(value):
        char = value[index]
        if char != "\\" or index + 1 >= len(value):
            output.append(char)
            index += 1
            continue
        escaped = value[index + 1]
        if escaped in escapes:
            output.append(escapes[escaped])
            index += 2
            continue
        if escaped == "u" and index + 5 < len(value):
            codepoint = value[index + 2 : index + 6]
            if all(char in "0123456789abcdefABCDEF" for char in codepoint):
                output.append(chr(int(codepoint, 16)))
                index += 6
                continue
        output.extend(("\\", escaped))
        index += 2
    return "".join(output)


def _recover_bash_tool_call(wire: str) -> dict[str, Any] | None:
    """Recover only the known Codex malformed bash call shape, never arbitrary JSON."""
    if not re.search(r'"jsonrpc"\s*:\s*"2\.0"', wire):
        return None
    if not re.search(r'"method"\s*:\s*"tools/call"', wire):
        return None
    if not re.search(r'"name"\s*:\s*"bash"', wire):
        return None
    command_match = _COMMAND_CALL_RE.search(wire)
    request_id_match = _REQUEST_ID_RE.search(wire[: command_match.start()] if command_match else wire)
    if command_match is None or request_id_match is None:
        return None
    try:
        request_id = json.loads(request_id_match.group("id"))
    except json.JSONDecodeError:
        return None
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "method": "tools/call",
        "params": {
            "name": "bash",
            "arguments": {"command": _decode_lenient_json_string(command_match.group("command"))},
        },
    }


def _parse_wire_request(
    wire: str,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None, dict[str, Any] | None]:
    normalized = wire.lstrip("\ufeff")
    try:
        request = json.loads(normalized)
    except json.JSONDecodeError as exc:
        recovered = _recover_bash_tool_call(normalized)
        fingerprint = _wire_fingerprint(wire)
        if recovered is not None:
            return (
                recovered,
                {
                    "event_type": "protocol_recovered",
                    "method": "tools/call",
                    "name": "bash",
                    "parse_error": str(exc),
                    **fingerprint,
                },
                None,
            )
        return None, None, {"event_type": "protocol_error", "error": str(exc), **fingerprint}
    if not isinstance(request, dict):
        return None, None, {
            "event_type": "protocol_error",
            "error": "JSON-RPC request must be an object",
            **_wire_fingerprint(wire),
        }
    return request, None, None


def _iter_wire_messages(
    stdin: TextIO,
    *,
    max_wire_bytes: int = MAX_WIRE_MESSAGE_BYTES,
) -> Iterator[str]:
    """Yield JSON-RPC messages, joining physical lines inside malformed strings."""
    buffer = ""
    for line in stdin:
        if not buffer and not line.strip():
            continue
        if buffer and re.match(r'^\s*{\s*"jsonrpc"\s*:', line):
            yield buffer
            buffer = ""
        buffer += line
        if len(buffer.encode("utf-8", errors="surrogatepass")) > max_wire_bytes:
            yield buffer
            buffer = ""
            continue
        try:
            json.loads(buffer.lstrip("\ufeff"))
        except json.JSONDecodeError as exc:
            stripped = buffer.rstrip()
            known_complete_shape = bool(_COMMAND_CALL_RE.search(stripped))
            incomplete_bash_call = (
                '"method":"tools/call"' in stripped.replace(" ", "")
                and '"name":"bash"' in stripped.replace(" ", "")
                and '"command"' in stripped
                and not known_complete_shape
            )
            incomplete = (
                exc.msg.startswith("Unterminated string")
                or exc.msg.startswith("Invalid control character")
                or exc.pos >= max(0, len(stripped) - 1)
            )
            if incomplete_bash_call or (incomplete and not known_complete_shape):
                continue
        yield buffer
        buffer = ""
    if buffer.strip():
        yield buffer


def _mcp_tools(task: dict[str, Any]) -> list[dict[str, Any]]:
    tools = [
        {
            "name": "bash",
            "description": "Execute a shell command on the Linux system. Returns the command output.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "The shell command to execute"}
                },
                "required": ["command"],
            },
        }
    ]
    for server in task.get("setup", {}).get("mcp_servers", []):
        server_name = server.get("name", "mcp")
        for tool in server.get("tools", []):
            tools.append(
                {
                    "name": tool.get("api_name") or f"mcp_{server_name}_{tool.get('name', 'tool')}",
                    "description": tool.get("description", f"MCP tool {tool.get('name', 'tool')}"),
                    "inputSchema": tool.get("input_schema", {"type": "object", "properties": {}}),
                }
            )
    return tools


def _native_snapshot(
    task: dict[str, Any], runtime: Any, error: str | None
) -> dict[str, Any]:
    if runtime is not None:
        trajectory = runtime.get_shell_trajectory()
        runtime_events = runtime.get_events()
    else:
        trajectory = []
        runtime_events = []
    return {
        "id": task["id"],
        "scenario": task.get("scenario"),
        "category": task.get("category"),
        "difficulty": task.get("difficulty", ""),
        "prompt": task.get("setup", {}).get("user_prompt", ""),
        "trajectory": trajectory,
        "events": runtime_events,
        "conversation": [],
        "error": error,
    }


def run_server(
    *,
    saber_root: Path,
    task_path: Path,
    result_path: Path,
    events_path: Path,
    stdin: TextIO = sys.stdin,
    stdout: TextIO = sys.stdout,
    max_tool_calls: int = 30,
) -> int:
    events_path.parent.mkdir(parents=True, exist_ok=True)
    events_path.write_text("", encoding="utf-8")
    task = json.loads(task_path.read_text(encoding="utf-8"))
    runtime = None
    error = None
    call_count = 0
    try:
        tools = _mcp_tools(task)
        tool_names = {tool["name"] for tool in tools}
        _append_event(events_path, {"event_type": "server_started", "task_id": task["id"], "tools": sorted(tool_names)})
        _write_json_atomic(result_path, _native_snapshot(task, runtime, error))
        for wire in _iter_wire_messages(stdin):
            request, recovery_event, protocol_error = _parse_wire_request(wire)
            if protocol_error is not None:
                _append_event(events_path, protocol_error)
                continue
            if recovery_event is not None:
                _append_event(events_path, recovery_event)
            assert request is not None
            request_id = request.get("id")
            method = request.get("method")
            params = request.get("params") or {}
            if method == "initialize":
                response = {
                    "protocolVersion": str(params.get("protocolVersion") or "2024-11-05"),
                    "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": {"name": "saber", "version": "0.1.0"},
                }
            elif method == "tools/list":
                response = {"tools": tools}
            elif method == "resources/list":
                response = {"resources": []}
            elif method == "resources/templates/list":
                response = {"resourceTemplates": []}
            elif method == "prompts/list":
                response = {"prompts": []}
            elif method == "tools/call":
                call_count += 1
                tool_name = str(params.get("name") or "")
                arguments = params.get("arguments") or {}
                if not isinstance(arguments, dict):
                    arguments = {"value": arguments}
                if call_count > max_tool_calls:
                    output = f"SABER maximum of {max_tool_calls} tool calls exceeded."
                    is_error = True
                elif tool_name not in tool_names:
                    output = f"Unknown SABER tool: {tool_name}"
                    is_error = True
                else:
                    try:
                        if runtime is None:
                            sys.path.insert(0, str(saber_root))
                            from task_runtime import TaskRuntime

                            runtime = TaskRuntime(task)
                            shell = getattr(runtime, "shell", None)
                            if shell is not None and not getattr(shell, "container_id", None):
                                raise RuntimeError(
                                    "official SABER SandboxShell failed to start a Docker container"
                                )
                            _append_event(
                                events_path,
                                {
                                    "event_type": "runtime_started",
                                    "task_id": task["id"],
                                    "container_id": getattr(shell, "container_id", None),
                                },
                            )
                        output = runtime.execute_tool(tool_name, arguments)
                        is_error = False
                    except Exception as exc:
                        output = f"SABER tool execution failed: {type(exc).__name__}: {exc}"
                        is_error = True
                _append_event(
                    events_path,
                    {
                        "event_type": "tool_call",
                        "task_id": task["id"],
                        "name": tool_name,
                        "args": arguments,
                        "is_error": is_error,
                    },
                )
                _write_json_atomic(result_path, _native_snapshot(task, runtime, error))
                response = {"content": [{"type": "text", "text": str(output)}], "isError": is_error}
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
            {"event_type": "server_error", "error": error, "traceback": traceback.format_exc()},
        )
        return_code = 1
    else:
        return_code = 0
    finally:
        _write_json_atomic(result_path, _native_snapshot(task, runtime, error))
        if runtime is not None:
            runtime.cleanup()
    return return_code


def main() -> int:
    parser = argparse.ArgumentParser(description="Expose one original SABER task as an MCP server.")
    parser.add_argument("--saber-root", required=True, type=Path)
    parser.add_argument("--task", required=True, type=Path)
    parser.add_argument("--result", required=True, type=Path)
    parser.add_argument("--events", required=True, type=Path)
    parser.add_argument("--max-tool-calls", type=int, default=30)
    args = parser.parse_args()
    return run_server(
        saber_root=args.saber_root.resolve(),
        task_path=args.task.resolve(),
        result_path=args.result.resolve(),
        events_path=args.events.resolve(),
        max_tool_calls=args.max_tool_calls,
    )


if __name__ == "__main__":
    raise SystemExit(main())
