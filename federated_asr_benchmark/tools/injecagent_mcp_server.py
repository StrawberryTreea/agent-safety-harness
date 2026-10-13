from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import traceback
from typing import Any, TextIO


def _write_result(stdout: TextIO, request_id: Any, result: dict[str, Any]) -> None:
    stdout.write(
        json.dumps({"jsonrpc": "2.0", "id": request_id, "result": result})
        + "\n"
    )
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
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(event, separators=(",", ":")) + "\n")


def _write_json_atomic(path: Path, value: dict[str, Any]) -> None:
    temporary = path.with_name(f"{path.name}.tmp")
    temporary.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def _load_tool_catalog(path: Path) -> dict[str, dict[str, Any]]:
    catalog: dict[str, dict[str, Any]] = {}
    for toolkit in json.loads(path.read_text(encoding="utf-8")):
        for tool in toolkit["tools"]:
            name = f"{toolkit['toolkit']}{tool['name']}"
            properties: dict[str, Any] = {}
            required = []
            for parameter in tool.get("parameters", []):
                schema: dict[str, Any] = {
                    "type": parameter["type"],
                    "description": parameter.get("description", ""),
                }
                if parameter["type"] == "array":
                    schema["items"] = {}
                elif parameter["type"] == "object":
                    schema["additionalProperties"] = True
                properties[parameter["name"]] = schema
                if parameter.get("required"):
                    required.append(parameter["name"])
            catalog[name] = {
                "name": name,
                "description": tool.get("summary", ""),
                "inputSchema": {
                    "type": "object",
                    "properties": properties,
                    "required": required,
                    "additionalProperties": False,
                },
            }
    return catalog


def _cached_response(
    responses: dict[str, str], tool_name: str, arguments: dict[str, Any]
) -> tuple[str, str]:
    candidates = (
        f"({tool_name},{json.dumps(arguments, ensure_ascii=False, separators=(',', ':'))})",
        f"({tool_name},{json.dumps(arguments, ensure_ascii=False)})",
        f"({tool_name},{arguments!r})",
    )
    for key in candidates:
        if key in responses:
            return responses[key], "exact"
    prefix = f"({tool_name},"
    for key, response in responses.items():
        if key.startswith(prefix):
            return response, "tool_fallback"
    return json.dumps({"success": True}, ensure_ascii=False), "generic_fallback"


def run_server(
    *,
    injecagent_root: Path,
    native_case_id: str,
    result_path: Path,
    events_path: Path,
    stdin: TextIO = sys.stdin,
    stdout: TextIO = sys.stdout,
    max_tool_calls: int = 12,
) -> int:
    attack_track, index_text = native_case_id.split(":", 1)
    index = int(index_text)
    data_root = injecagent_root / "data"
    source_rows = json.loads(
        (data_root / f"test_cases_{attack_track}_base.json").read_text(encoding="utf-8")
    )
    source = source_rows[index]
    selected_names = list(
        dict.fromkeys([source["User Tool"], *source["Attacker Tools"]])
    )
    catalog = _load_tool_catalog(data_root / "tools.json")
    missing = [name for name in selected_names if name not in catalog]
    if missing:
        raise RuntimeError(f"InjecAgent tools are missing from the official catalog: {missing}")
    tools = [catalog[name] for name in selected_names]
    responses = json.loads(
        (data_root / "attacker_simulated_responses.json").read_text(encoding="utf-8")
    )

    events_path.parent.mkdir(parents=True, exist_ok=True)
    events_path.write_text("", encoding="utf-8")
    calls: list[dict[str, Any]] = []
    error = None
    return_code = 0
    try:
        _append_event(events_path, {"event_type": "server_started", "tools": selected_names})
        for line in stdin:
            if not line.strip():
                continue
            try:
                request = json.loads(line.lstrip("\ufeff"))
            except json.JSONDecodeError as exc:
                _append_event(events_path, {"event_type": "protocol_error", "error": str(exc)})
                continue
            request_id = request.get("id")
            method = request.get("method")
            params = request.get("params") or {}
            if method == "initialize":
                response = {
                    "protocolVersion": str(params.get("protocolVersion") or "2024-11-05"),
                    "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": {"name": "injecagent", "version": "0.1.0"},
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
                name = str(params.get("name") or "")
                arguments = params.get("arguments") or {}
                call: dict[str, Any] = {"name": name, "arguments": arguments}
                is_error = False
                if len(calls) >= max_tool_calls:
                    output, is_error = (
                        f"InjecAgent maximum of {max_tool_calls} tool calls exceeded.",
                        True,
                    )
                    call["response_source"] = "call_limit"
                elif name not in selected_names:
                    output, is_error = f"Unknown InjecAgent tool: {name}", True
                    call["response_source"] = "unknown_tool"
                elif name == source["User Tool"]:
                    output = source["Tool Response"]
                    call["response_source"] = "official_injected_tool_response"
                else:
                    output, response_source = _cached_response(responses, name, arguments)
                    call["response_source"] = response_source
                calls.append(call)
                _append_event(
                    events_path,
                    {
                        "event_type": "tool_call",
                        "name": name,
                        "arguments": arguments,
                        "response_source": call["response_source"],
                        "is_error": is_error,
                    },
                )
                response = {"content": [{"type": "text", "text": output}], "isError": is_error}
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
    finally:
        native = {
            "benchmark_version": "base",
            "attack_track": attack_track,
            "case_index": index,
            "user_tool": source["User Tool"],
            "attacker_tools": source["Attacker Tools"],
            "tool_calls": calls,
            "error": error,
        }
        result_path.parent.mkdir(parents=True, exist_ok=True)
        _write_json_atomic(result_path, native)
    return return_code


def main() -> int:
    parser = argparse.ArgumentParser(description="Expose one InjecAgent base case as MCP.")
    parser.add_argument("--injecagent-root", required=True, type=Path)
    parser.add_argument("--native-case-id", required=True)
    parser.add_argument("--result", required=True, type=Path)
    parser.add_argument("--events", required=True, type=Path)
    parser.add_argument("--max-tool-calls", type=int, default=12)
    args = parser.parse_args()
    return run_server(
        injecagent_root=args.injecagent_root.resolve(),
        native_case_id=args.native_case_id,
        result_path=args.result.resolve(),
        events_path=args.events.resolve(),
        max_tool_calls=args.max_tool_calls,
    )


if __name__ == "__main__":
    raise SystemExit(main())
