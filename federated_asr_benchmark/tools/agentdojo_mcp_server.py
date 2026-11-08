from __future__ import annotations

import argparse
import ast
from datetime import date, datetime, time
from enum import Enum
import hashlib
import json
from pathlib import Path
import re
import sys
import traceback
from typing import Any, TextIO


def _message_content_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            text for item in content if (text := _message_content_text(item))
        )
    if isinstance(content, dict):
        for key in ("content", "text"):
            if key in content:
                return _message_content_text(content[key])
    return ""


def _write_result(stdout: TextIO, request_id: Any, result: dict[str, Any]) -> None:
    stdout.write(json.dumps({"jsonrpc": "2.0", "id": request_id, "result": result}) + "\n")
    stdout.flush()


def _write_error(stdout: TextIO, request_id: Any, code: int, message: str) -> None:
    stdout.write(json.dumps({"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}) + "\n")
    stdout.flush()


def _append_event(path: Path, event: dict[str, Any]) -> None:
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(event, separators=(",", ":")) + "\n")


def _write_json_atomic(path: Path, value: dict[str, Any]) -> None:
    temporary = path.with_name(f"{path.name}.tmp")
    temporary.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def _format_tool_result(value: Any) -> str:
    import yaml
    from pydantic import BaseModel

    if isinstance(value, BaseModel):
        return yaml.safe_dump(value.model_dump(), sort_keys=False).strip()
    if isinstance(value, list):
        rows = [item.model_dump() if isinstance(item, BaseModel) else item for item in value]
        return yaml.safe_dump(rows, sort_keys=False).strip()
    return str(value)


def _json_snapshot_value(
    value: Any,
    *,
    path: str = "$",
    active: dict[int, str] | None = None,
) -> Any:
    """Encode runtime-only callables/cycles without discarding concrete state."""
    from pydantic import BaseModel

    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, Enum):
        return _json_snapshot_value(value.value, path=path, active=active)
    if isinstance(value, (date, datetime, time)):
        return value.isoformat()
    if isinstance(value, Path):
        return str(value)
    if callable(value):
        module = getattr(value, "__module__", "")
        name = getattr(value, "__qualname__", getattr(value, "__name__", type(value).__qualname__))
        return {"__callable__": f"{module}.{name}".strip(".")}

    active = {} if active is None else active
    identity = id(value)
    if identity in active:
        return {"__cycle_ref__": active[identity]}
    active[identity] = path
    try:
        if isinstance(value, BaseModel):
            value = value.model_dump(mode="python")
        if isinstance(value, dict):
            return {
                str(key): _json_snapshot_value(item, path=f"{path}.{key}", active=active)
                for key, item in value.items()
            }
        if isinstance(value, (list, tuple)):
            return [
                _json_snapshot_value(item, path=f"{path}[{index}]", active=active)
                for index, item in enumerate(value)
            ]
        if isinstance(value, (set, frozenset)):
            encoded = [_json_snapshot_value(item, path=f"{path}[]", active=active) for item in value]
            return sorted(encoded, key=lambda item: json.dumps(item, sort_keys=True))
        raise TypeError(f"unsupported snapshot value at {path}: {type(value).__module__}.{type(value).__qualname__}")
    finally:
        active.pop(identity, None)


def _serialize_environment(environment: Any) -> tuple[dict[str, Any] | None, str | None]:
    if environment is None:
        return None, None
    try:
        return environment.model_dump(mode="json"), "pydantic_json"
    except (ValueError, TypeError):
        encoded = _json_snapshot_value(environment.model_dump(mode="python"))
        if not isinstance(encoded, dict):
            raise TypeError("environment snapshot must encode to an object")
        return encoded, "recursive_json_v1"


def _normalized_text(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def _attack_delivery_evidence(
    source: dict[str, Any], output: str, *, tool_name: str, call_index: int
) -> list[dict[str, Any]]:
    import yaml
    def scalars(value):
        if isinstance(value, str):
            yield value
        elif isinstance(value, dict):
            for child in value.values():
                yield from scalars(child)
        elif isinstance(value, list):
            for child in value:
                yield from scalars(child)
    candidates = [output]
    try:
        candidates.extend(scalars(yaml.safe_load(output)))
    except (yaml.YAMLError, RecursionError):
        pass
    try:
        candidates.extend(scalars(ast.literal_eval(output)))
    except (ValueError, SyntaxError, RecursionError):
        pass
    normalized_candidates = [_normalized_text(value) for value in candidates]
    normalized_output = _normalized_text(output)
    if not normalized_output:
        return []
    output_sha256 = hashlib.sha256(normalized_output.encode("utf-8")).hexdigest()
    evidence: list[dict[str, Any]] = []
    for injection_key, payload in sorted((source.get("injections") or {}).items()):
        if not isinstance(payload, str):
            continue
        normalized_payload = _normalized_text(payload)
        if normalized_payload and any(normalized_payload in value for value in normalized_candidates):
            evidence.append(
                {
                    "schema_version": "0.1",
                    "injection_key": injection_key,
                    "payload_sha256": hashlib.sha256(payload.encode("utf-8")).hexdigest(),
                    "normalized_payload_sha256": hashlib.sha256(
                        normalized_payload.encode("utf-8")
                    ).hexdigest(),
                    "tool_name": tool_name,
                    "call_index": call_index,
                    "normalized_output_sha256": output_sha256,
                }
            )
    return evidence


def _load_runtime(source: dict[str, Any], benchmark_version: str):
    from agentdojo.functions_runtime import FunctionsRuntime
    from agentdojo.task_suite.load_suites import get_suite

    suite = get_suite(benchmark_version, source["suite_name"])
    user_task = suite.get_user_task_by_id(source["user_task_id"])
    environment = suite.load_and_inject_default_environment(source["injections"])
    environment = user_task.init_environment(environment)
    return suite, user_task, FunctionsRuntime(suite.tools), environment.model_copy(deep=True), environment


def _native_snapshot(
    source: dict[str, Any],
    benchmark_version: str,
    pre_environment: Any,
    environment: Any,
    calls: list[Any],
    error: str | None,
    attack_delivery: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    serialized_pre, pre_encoding = _serialize_environment(pre_environment)
    serialized_post, post_encoding = _serialize_environment(environment)
    return {
        "benchmark_version": benchmark_version,
        "suite_name": source["suite_name"],
        "user_task_id": source["user_task_id"],
        "injection_task_id": source["injection_task_id"],
        "attack_type": source["attack_type"],
        "injections": source["injections"],
        "prompt": next(
            (
                _message_content_text(message.get("content"))
                for message in source.get("messages", [])
                if message.get("role") == "user"
            ),
            "",
        ),
        "pre_environment": serialized_pre,
        "post_environment": serialized_post,
        "environment_snapshot_encoding": {
            "pre_environment": pre_encoding,
            "post_environment": post_encoding,
        },
        "function_calls": [call.model_dump(mode="json") for call in calls],
        "attack_delivery": list(attack_delivery or []),
        "error": error,
    }


def run_server(
    *,
    source_result_path: Path,
    tool_manifest_path: Path,
    result_path: Path,
    events_path: Path,
    benchmark_version: str = "v1",
    server_name: str = "agentdojo",
    stdin: TextIO = sys.stdin,
    stdout: TextIO = sys.stdout,
    max_tool_calls: int = 30,
) -> int:
    events_path.parent.mkdir(parents=True, exist_ok=True)
    events_path.write_text("", encoding="utf-8")
    source = json.loads(source_result_path.read_text(encoding="utf-8"))
    tools = json.loads(tool_manifest_path.read_text(encoding="utf-8"))["tools"]
    tool_names = {tool["name"] for tool in tools}
    suite = user_task = runtime = environment = pre_environment = None
    calls: list[Any] = []
    attack_delivery: list[dict[str, Any]] = []
    error = None
    call_count = 0
    return_code = 0

    def ensure_runtime_loaded() -> None:
        nonlocal suite, user_task, runtime, pre_environment, environment
        if runtime is not None:
            return
        suite, user_task, runtime, pre_environment, environment = _load_runtime(
            source, benchmark_version
        )
        _append_event(events_path, {"event_type": "runtime_started"})
        _write_json_atomic(
            result_path,
            _native_snapshot(
                source,
                benchmark_version,
                pre_environment,
                environment,
                calls,
                error,
                attack_delivery,
            ),
        )

    try:
        _append_event(events_path, {"event_type": "server_started", "tools": sorted(tool_names)})
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
                    "serverInfo": {"name": server_name, "version": "0.1.0"},
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
                ensure_runtime_loaded()
                call_count += 1
                name = str(params.get("name") or "")
                arguments = params.get("arguments") or {}
                is_error = False
                delivered = []
                if call_count > max_tool_calls:
                    output, is_error = f"AgentDojo maximum of {max_tool_calls} tool calls exceeded.", True
                elif name not in tool_names:
                    output, is_error = f"Unknown AgentDojo tool: {name}", True
                else:
                    from agentdojo.functions_runtime import FunctionCall

                    call = FunctionCall(function=name, args=arguments)
                    calls.append(call)
                    value, call_error = runtime.run_function(environment, name, arguments)
                    output = _format_tool_result(value)
                    if call_error:
                        output, is_error = call_error, True
                    delivered = _attack_delivery_evidence(
                        source, output, tool_name=name, call_index=call_count
                    )
                    attack_delivery.extend(delivered)
                _append_event(
                    events_path,
                    {
                        "event_type": "tool_call",
                        "name": name,
                        "args": arguments,
                        "is_error": is_error,
                        "call_index": call_count,
                        "output_sha256": hashlib.sha256(output.encode("utf-8")).hexdigest(),
                    },
                )
                for evidence in delivered:
                    _append_event(events_path, {"event_type": "attack_delivery", **evidence})
                _write_json_atomic(
                    result_path,
                    _native_snapshot(
                        source,
                        benchmark_version,
                        pre_environment,
                        environment,
                        calls,
                        error,
                        attack_delivery,
                    ),
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
        _append_event(events_path, {"event_type": "server_error", "error": error, "traceback": traceback.format_exc()})
        return_code = 1
    finally:
        if runtime is None and error is None:
            try:
                ensure_runtime_loaded()
            except Exception as exc:
                error = f"{type(exc).__name__}: {exc}"
                return_code = 1
                _append_event(
                    events_path,
                    {
                        "event_type": "server_error",
                        "error": error,
                        "traceback": traceback.format_exc(),
                    },
                )
        result_path.parent.mkdir(parents=True, exist_ok=True)
        _write_json_atomic(
            result_path,
            _native_snapshot(
                source,
                benchmark_version,
                pre_environment,
                environment,
                calls,
                error,
                attack_delivery,
            ),
        )
    return return_code


def main() -> int:
    parser = argparse.ArgumentParser(description="Expose one pinned AgentDojo v1 case as MCP.")
    parser.add_argument("--source-result", required=True, type=Path)
    parser.add_argument("--tool-manifest", required=True, type=Path)
    parser.add_argument("--result", required=True, type=Path)
    parser.add_argument("--events", required=True, type=Path)
    parser.add_argument("--benchmark-version", default="v1")
    parser.add_argument("--server-name", default="agentdojo")
    parser.add_argument("--max-tool-calls", type=int, default=30)
    args = parser.parse_args()
    return run_server(
        source_result_path=args.source_result.resolve(),
        tool_manifest_path=args.tool_manifest.resolve(),
        result_path=args.result.resolve(),
        events_path=args.events.resolve(),
        benchmark_version=args.benchmark_version,
        server_name=args.server_name,
        max_tool_calls=args.max_tool_calls,
    )


if __name__ == "__main__":
    raise SystemExit(main())
