"""Offline fault injection: all credentials and API responses here are synthetic."""
from __future__ import annotations

from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from threading import Thread
import unittest
import urllib.error
from unittest.mock import patch

from scripts import run_deepseek_smoke as smoke
from tests.temp_workspace import WorkspaceTempDir
from tests.test_deepseek_smoke import response


@contextmanager
def local_api():
    calls = []
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            self.rfile.read(int(self.headers.get("Content-Length", 0)))
            calls.append(self.path)
            if self.path == "/redirect":
                self.send_response(307)
                self.send_header("Location", "/credential-destination")
                self.end_headers()
                return
            if self.path == "/limit":
                self.send_response(429)
                self.end_headers()
                self.wfile.write(b"synthetic-secret must not enter output")
                return
            body = (b"{" if self.path == "/bad-json" else b"x" * 1_000_001
                    if self.path == "/oversize" else json.dumps(response()).encode())
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", calls
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


class DeepSeekFaultTests(unittest.TestCase):
    def run_fake(self, root, send):
        with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "synthetic-secret"}):
            return smoke.run(root / "run", live=True, send=send)

    def test_wrong_or_missing_model_cannot_score_as_requested_target(self):
        for model in ("different-model", None, {}, ""):
            with self.subTest(model=model), WorkspaceTempDir() as root:
                result = self.run_fake(root, lambda body, key: response(model=model))
                self.assertEqual(result["attempted_requests"], 1)
                self.assertEqual((result["passed"], result["invalid"]), (0, 1))
                self.assertEqual(result["budget"]["unsettled_jobs"], 0)

    def test_backend_drift_stops_before_mixing_populations(self):
        with WorkspaceTempDir() as root:
            fingerprints = iter(["backend-1", "backend-2", "backend-2"])
            result = self.run_fake(root, lambda body, key: response(
                model=smoke.MODEL, system_fingerprint=next(fingerprints)))
            self.assertEqual(result["attempted_requests"], 2)
            self.assertEqual((result["completed_valid"], result["invalid"]), (1, 1))
            self.assertEqual(result["budget"]["unsettled_jobs"], 0)

    def test_nonempty_answer_with_zero_generated_tokens_is_invalid(self):
        with WorkspaceTempDir() as root:
            usage = {"prompt_tokens": 10, "prompt_cache_hit_tokens": 0,
                     "prompt_cache_miss_tokens": 10, "completion_tokens": 0, "total_tokens": 10}
            result = self.run_fake(root, lambda body, key: response(model=smoke.MODEL, usage=usage))
            self.assertEqual((result["attempted_requests"], result["passed"], result["invalid"]), (1, 0, 1))

    def test_request_or_money_limit_stops_before_dispatch(self):
        for limits in ({"MAX_REQUESTS": 1}, {"LIMIT_MICROCNY": 500_000}):
            with self.subTest(limits=limits), WorkspaceTempDir() as root, patch.multiple(smoke, **limits):
                calls = []
                def send(body, key):
                    calls.append(1)
                    return response(model=smoke.MODEL)
                result = self.run_fake(root, send)
                self.assertEqual(len(calls), 1)
                self.assertEqual(result["status"], "stopped_budget")
                self.assertEqual(result["budget"]["executions_committed_or_reserved"], 1)
                persisted = json.loads((root / "run/summary.json").read_text(encoding="utf-8"))
                self.assertEqual(persisted, result)

    def test_summary_replacement_failure_preserves_previous_evidence(self):
        with WorkspaceTempDir() as root:
            path = root / "summary.json"
            smoke.write_json(path, {"status": "before"})
            before = path.read_bytes()
            with patch("os.replace", side_effect=OSError("injected disk failure")):
                with self.assertRaises(OSError):
                    smoke.write_json(path, {"status": "after"})
            self.assertEqual(path.read_bytes(), before)
            self.assertEqual(list(root.iterdir()), [path])

    def test_http_auth_rate_limit_and_server_failures_keep_unknown_liability(self):
        for code in (401, 429, 500, 503):
            with self.subTest(code=code), WorkspaceTempDir() as root:
                calls = []
                def send(body, key):
                    calls.append(1)
                    raise urllib.error.HTTPError(smoke.ENDPOINT, code, "synthetic-secret", {}, None)
                result = self.run_fake(root, send)
                self.assertEqual(len(calls), 1)
                self.assertEqual(result["budget"]["cost_committed_or_reserved"], 500_000)
                row = json.loads((root / "run/results.jsonl").read_text(encoding="utf-8"))
                self.assertEqual(row["http_status"], code)
                self.assertNotIn("synthetic-secret", json.dumps(row))

    def test_interrupted_call_is_not_automatically_replayed(self):
        with WorkspaceTempDir() as root:
            def interrupt(body, key):
                raise KeyboardInterrupt()
            with self.assertRaises(KeyboardInterrupt):
                self.run_fake(root, interrupt)
            persisted = json.loads((root / "run/summary.json").read_text(encoding="utf-8"))
            self.assertEqual(persisted["attempted_requests"], 1)
            self.assertEqual(persisted["budget"]["unsettled_jobs"], 1)
            with self.assertRaises(FileExistsError):
                self.run_fake(root, lambda body, key: self.fail("replayed uncertain call"))

    def test_malformed_choices_never_pass_and_still_settle_reported_cost(self):
        for choices in (None, [], [None], [{}], [{"message": None}], [{"message": {"content": ["A"]}, "finish_reason": "stop"}]):
            with self.subTest(choices=choices), WorkspaceTempDir() as root:
                result = self.run_fake(root, lambda body, key: response(model=smoke.MODEL, choices=choices))
                self.assertEqual((result["attempted_requests"], result["passed"]), (1, 0))
                self.assertEqual(result["budget"]["unsettled_jobs"], 0)
                self.assertEqual(result["budget"]["cost_committed_or_reserved"], 28)

    def test_real_http_transport_rejects_redirect_and_never_forwards_credential(self):
        with local_api() as (base, calls), patch.dict(os.environ, {"NO_PROXY": "127.0.0.1", "no_proxy": "127.0.0.1"}):
            with patch.object(smoke, "ENDPOINT", base + "/redirect"):
                with self.assertRaises(urllib.error.HTTPError) as raised:
                    smoke.transport({"model": "fixture"}, "synthetic-secret")
            self.assertEqual(raised.exception.code, 307)
            self.assertEqual(calls, ["/redirect"])

    def test_real_http_limit_and_bad_bodies_stop_without_retry(self):
        with local_api() as (base, calls), patch.dict(os.environ, {"NO_PROXY": "127.0.0.1", "no_proxy": "127.0.0.1"}):
            for route in ("/limit", "/bad-json", "/oversize"):
                with self.subTest(route=route), WorkspaceTempDir() as root, patch.object(smoke, "ENDPOINT", base + route):
                    result = self.run_fake(root, None)
                    self.assertEqual((result["attempted_requests"], result["api_errors"]), (1, 1))
                    self.assertEqual(result["budget"]["unsettled_jobs"], 1)
                    self.assertNotIn("synthetic-secret", (root / "run/results.jsonl").read_text(encoding="utf-8"))
            self.assertEqual(calls, ["/limit", "/bad-json", "/oversize"])
