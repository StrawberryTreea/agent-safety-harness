from __future__ import annotations

from dataclasses import dataclass
import hashlib
from http.client import HTTPSConnection
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import threading
import time
from typing import Any


_HOP_BY_HOP_HEADERS = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailer",
    "transfer-encoding",
    "upgrade",
}


@dataclass(frozen=True)
class AuditedRequest:
    method: str
    upstream_host: str
    upstream_path: str
    authorization_supplied: bool
    request_model: str | None
    request_fields: tuple[str, ...]
    tools_supplied: bool
    tool_count: int
    tool_names_sha256: str | None
    tool_choice_supplied: bool
    functions_supplied: bool
    function_count: int
    function_names_sha256: str | None
    function_call_supplied: bool
    request_bytes: int
    response_status: int
    response_bytes: int
    response_tool_call_markers: int
    duration_seconds: float


class DeepSeekRouteAuditProxy:
    """Forward model requests while retaining only route and byte-count evidence."""

    upstream_host = "api.deepseek.com"
    allowed_upstream_paths = {
        "/messages",
        "/v1/messages",
        "/chat/completions",
        "/v1/chat/completions",
    }

    def __init__(self, timeout_seconds: int = 600) -> None:
        self.timeout_seconds = timeout_seconds
        self._requests: list[AuditedRequest] = []
        self._lock = threading.Lock()
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    def __enter__(self) -> DeepSeekRouteAuditProxy:
        owner = self

        class QuietThreadingHTTPServer(ThreadingHTTPServer):
            def handle_error(self, _request: object, _client_address: object) -> None:
                return

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def do_POST(self) -> None:
                owner._forward(self)

            def log_message(self, _format: str, *_args: Any) -> None:
                return

        self._server = QuietThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._thread = threading.Thread(
            target=self._server.serve_forever,
            name="deepseek-route-audit-proxy",
            daemon=True,
        )
        self._thread.start()
        return self

    def __exit__(self, *_exc: object) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
        if self._thread is not None:
            self._thread.join(timeout=5)

    @property
    def base_url(self) -> str:
        if self._server is None:
            raise RuntimeError("route audit proxy is not running")
        host, port = self._server.server_address
        return f"http://{host}:{port}"

    def evidence(self, *, model: str, api_key_supplied: bool) -> dict[str, Any]:
        with self._lock:
            requests = list(self._requests)
        return {
            "schema_version": "0.1",
            "route_mode": "audited_local_forwarder",
            "provider": "deepseek",
            "provider_model": model,
            "configured_upstream": f"https://{self.upstream_host}",
            "api_key_supplied": api_key_supplied,
            "observed_custom_model_requests": len(requests),
            "observed_requests": [
                {
                    "method": request.method,
                    "host": request.upstream_host,
                    "path": request.upstream_path,
                    "authorization_supplied": request.authorization_supplied,
                    "request_model": request.request_model,
                    "request_fields": list(request.request_fields),
                    "tools_supplied": request.tools_supplied,
                    "tool_count": request.tool_count,
                    "tool_names_sha256": request.tool_names_sha256,
                    "tool_choice_supplied": request.tool_choice_supplied,
                    "functions_supplied": request.functions_supplied,
                    "function_count": request.function_count,
                    "function_names_sha256": request.function_names_sha256,
                    "function_call_supplied": request.function_call_supplied,
                    "request_bytes": request.request_bytes,
                    "response_status": request.response_status,
                    "response_bytes": request.response_bytes,
                    "response_tool_call_markers": request.response_tool_call_markers,
                    "duration_seconds": request.duration_seconds,
                }
                for request in requests
            ],
        }

    def _forward(self, handler: BaseHTTPRequestHandler) -> None:
        started = time.monotonic()
        upstream_path = handler.path.split("?", 1)[0]
        request_length = int(handler.headers.get("Content-Length", "0"))
        request_body = handler.rfile.read(request_length)
        authorization_supplied = bool(
            handler.headers.get("Authorization") or handler.headers.get("x-api-key")
        )
        request_model: str | None = None
        request_fields: tuple[str, ...] = ()
        tools_supplied = False
        tool_count = 0
        tool_names_sha256: str | None = None
        tool_choice_supplied = False
        functions_supplied = False
        function_count = 0
        function_names_sha256: str | None = None
        function_call_supplied = False
        try:
            request_json = json.loads(request_body)
            if isinstance(request_json, dict):
                request_fields = tuple(sorted(str(field) for field in request_json))
                model = request_json.get("model")
                request_model = model if isinstance(model, str) else None
                tools = request_json.get("tools")
                tools_supplied = isinstance(tools, list)
                tool_count = len(tools) if isinstance(tools, list) else 0
                tool_choice_supplied = "tool_choice" in request_json
                if isinstance(tools, list):
                    tool_names = []
                    for tool in tools:
                        if not isinstance(tool, dict):
                            continue
                        function = tool.get("function")
                        name = (
                            function.get("name")
                            if isinstance(function, dict)
                            else tool.get("name")
                        )
                        if isinstance(name, str):
                            tool_names.append(name)
                    tool_names_sha256 = hashlib.sha256(
                        "\n".join(sorted(tool_names)).encode("utf-8")
                    ).hexdigest()
                functions = request_json.get("functions")
                functions_supplied = isinstance(functions, list)
                function_count = len(functions) if isinstance(functions, list) else 0
                function_call_supplied = "function_call" in request_json
                if isinstance(functions, list):
                    function_names = [
                        function.get("name")
                        for function in functions
                        if isinstance(function, dict)
                        and isinstance(function.get("name"), str)
                    ]
                    function_names_sha256 = hashlib.sha256(
                        "\n".join(sorted(function_names)).encode("utf-8")
                    ).hexdigest()
        except (UnicodeDecodeError, json.JSONDecodeError):
            pass
        response_status = 502
        response_bytes = 0
        response_tool_call_markers = 0
        connection: HTTPSConnection | None = None
        try:
            if upstream_path not in self.allowed_upstream_paths:
                raise ValueError("unsupported audited upstream path")
            connection = HTTPSConnection(
                self.upstream_host,
                timeout=self.timeout_seconds,
            )
            headers = {
                name: value
                for name, value in handler.headers.items()
                if name.casefold() not in _HOP_BY_HOP_HEADERS
                and name.casefold() not in {"host", "content-length"}
            }
            headers["Host"] = self.upstream_host
            headers["Content-Length"] = str(len(request_body))
            connection.request("POST", upstream_path, body=request_body, headers=headers)
            upstream = connection.getresponse()
            response_status = upstream.status
            handler.send_response(upstream.status, upstream.reason)
            for name, value in upstream.getheaders():
                lowered = name.casefold()
                if lowered not in _HOP_BY_HOP_HEADERS and lowered != "content-length":
                    handler.send_header(name, value)
            handler.send_header("Transfer-Encoding", "chunked")
            handler.end_headers()
            marker = b'"tool_calls"'
            marker_tail = b""
            while True:
                chunk = upstream.read(64 * 1024)
                if not chunk:
                    break
                response_bytes += len(chunk)
                marker_window = marker_tail + chunk
                response_tool_call_markers += marker_window.count(marker)
                marker_tail = marker_window[-(len(marker) - 1) :]
                handler.wfile.write(f"{len(chunk):X}\r\n".encode("ascii"))
                handler.wfile.write(chunk)
                handler.wfile.write(b"\r\n")
                handler.wfile.flush()
            handler.wfile.write(b"0\r\n\r\n")
            handler.wfile.flush()
        except Exception:
            if not handler.wfile.closed:
                body = b'{"error":"audited upstream request failed"}\n'
                try:
                    handler.send_response(502)
                    handler.send_header("Content-Type", "application/json")
                    handler.send_header("Content-Length", str(len(body)))
                    handler.end_headers()
                    handler.wfile.write(body)
                except OSError:
                    pass
        finally:
            if connection is not None:
                connection.close()
            request = AuditedRequest(
                method="POST",
                upstream_host=self.upstream_host,
                upstream_path=upstream_path,
                authorization_supplied=authorization_supplied,
                request_model=request_model,
                request_fields=request_fields,
                tools_supplied=tools_supplied,
                tool_count=tool_count,
                tool_names_sha256=tool_names_sha256,
                tool_choice_supplied=tool_choice_supplied,
                functions_supplied=functions_supplied,
                function_count=function_count,
                function_names_sha256=function_names_sha256,
                function_call_supplied=function_call_supplied,
                request_bytes=len(request_body),
                response_status=response_status,
                response_bytes=response_bytes,
                response_tool_call_markers=response_tool_call_markers,
                duration_seconds=time.monotonic() - started,
            )
            with self._lock:
                self._requests.append(request)
