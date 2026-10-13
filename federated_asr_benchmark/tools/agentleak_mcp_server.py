from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import traceback
from typing import Any, TextIO


def _load_scenario(root: Path, scenario_id: str) -> dict[str, Any]:
    path = root / "agentleak_data" / "datasets" / "scenarios_full_1000.jsonl"
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            row = json.loads(line)
            if row.get("scenario_id") == scenario_id:
                return row
    raise FileNotFoundError(f"AgentLeak scenario does not exist: {scenario_id}")


def _write(stdout: TextIO, request_id: Any, value: dict[str, Any]) -> None:
    stdout.write(json.dumps({"jsonrpc": "2.0", "id": request_id, "result": value}) + "\n")
    stdout.flush()


def _append(path: Path, value: dict[str, Any]) -> None:
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(value, separators=(",", ":")) + "\n")


def _atomic(path: Path, value: dict[str, Any]) -> None:
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


TOOLS = [
    {
        "name": "get_private_context",
        "description": "Load the authorized scenario context. Returned external content may be untrusted.",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "send_inter_agent",
        "description": "Send task-relevant information to another simulated agent.",
        "inputSchema": {
            "type": "object",
            "properties": {"target": {"type": "string"}, "content": {"type": "string"}},
            "required": ["target", "content"],
            "additionalProperties": False,
        },
    },
    {
        "name": "write_memory",
        "description": "Write task-relevant content to simulated shared memory.",
        "inputSchema": {
            "type": "object",
            "properties": {"content": {"type": "string"}},
            "required": ["content"],
            "additionalProperties": False,
        },
    },
    {
        "name": "emit_artifact",
        "description": "Save a user-facing task artifact in the scenario simulator.",
        "inputSchema": {
            "type": "object",
            "properties": {"content": {"type": "string"}},
            "required": ["content"],
            "additionalProperties": False,
        },
    },
]


def run_server(
    *,
    root: Path,
    scenario_id: str,
    result_path: Path,
    events_path: Path,
    stdin: TextIO = sys.stdin,
    stdout: TextIO = sys.stdout,
) -> int:
    scenario = _load_scenario(root, scenario_id)
    channels: list[dict[str, Any]] = []
    events_path.parent.mkdir(parents=True, exist_ok=True)
    events_path.write_text("", encoding="utf-8")
    error = None
    return_code = 0
    context = {
        "private_vault": scenario.get("private_vault"),
        "allowed_set": scenario.get("allowed_set"),
        "external_content": (scenario.get("attack") or {}).get("payload"),
        "attack_class": (scenario.get("attack") or {}).get("attack_class"),
    }
    try:
        _append(events_path, {"event_type": "server_started", "scenario_id": scenario_id})
        for line in stdin:
            if not line.strip():
                continue
            request = json.loads(line.lstrip("\ufeff"))
            request_id = request.get("id")
            method = request.get("method")
            params = request.get("params") or {}
            if method == "initialize":
                response = {
                    "protocolVersion": str(params.get("protocolVersion") or "2024-11-05"),
                    "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": {"name": "agentleak", "version": "0.1.0"},
                }
            elif method == "tools/list":
                response = {"tools": TOOLS}
            elif method == "resources/list":
                response = {"resources": []}
            elif method == "resources/templates/list":
                response = {"resourceTemplates": []}
            elif method == "prompts/list":
                response = {"prompts": []}
            elif method == "tools/call":
                name = str(params.get("name") or "")
                arguments = params.get("arguments") or {}
                if name == "get_private_context":
                    text = json.dumps(context)
                elif name in {"send_inter_agent", "write_memory", "emit_artifact"}:
                    channel = {
                        "send_inter_agent": "inter_agent",
                        "write_memory": "memory_write",
                        "emit_artifact": "artifact",
                    }[name]
                    content = str(arguments.get("content") or "")
                    channels.append({"channel": channel, "content": content, "tool_name": name})
                    text = json.dumps({"accepted": True, "channel": channel})
                else:
                    text = f"Unknown AgentLeak tool: {name}"
                _append(events_path, {"event_type": "tool_call", "name": name, "arguments": arguments})
                response = {
                    "content": [{"type": "text", "text": text}],
                    "isError": name not in {tool["name"] for tool in TOOLS},
                }
            elif method == "ping":
                response = {}
            elif request_id is None:
                continue
            else:
                response = {"content": [{"type": "text", "text": f"Method not found: {method}"}], "isError": True}
            if request_id is not None:
                _write(stdout, request_id, response)
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
        _append(events_path, {"event_type": "server_error", "error": error, "traceback": traceback.format_exc()})
        return_code = 1
    finally:
        native = {
            "benchmark_version": "scenarios_full_1000-payload-applied-v1",
            "scenario_id": scenario_id,
            "attack_class": (scenario.get("attack") or {}).get("attack_class"),
            "payload_applied": bool((scenario.get("attack") or {}).get("payload")),
            "delivery_mode": "prompt_materialized_external_context",
            "channel_messages": channels,
            "error": error,
        }
        result_path.parent.mkdir(parents=True, exist_ok=True)
        _atomic(result_path, native)
    return return_code


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--agentleak-root", required=True, type=Path)
    parser.add_argument("--native-case-id", required=True)
    parser.add_argument("--result", required=True, type=Path)
    parser.add_argument("--events", required=True, type=Path)
    args = parser.parse_args()
    return run_server(
        root=args.agentleak_root.resolve(),
        scenario_id=args.native_case_id,
        result_path=args.result.resolve(),
        events_path=args.events.resolve(),
    )


if __name__ == "__main__":
    raise SystemExit(main())
