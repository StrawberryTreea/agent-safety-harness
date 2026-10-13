from __future__ import annotations

import json
import io
from pathlib import Path
import tempfile
import threading
import time
import unittest
from tests.source_dependencies import requires_assets
from product_eval.data_paths import default_data_root, runtime_python

from product_eval.agentdojo import AgentDojoAdapter
from unittest.mock import patch

from tools.agentdojo_mcp_server import (
    _append_event,
    _native_snapshot,
    _write_json_atomic,
    run_server,
)


PACKAGE_ROOT = Path(__file__).resolve().parents[1]
WORKSPACE_ROOT = PACKAGE_ROOT.parent
AGENTDOJO_ROOT = default_data_root() / "AgentDojo"
AGENTDOJO_PYTHON = runtime_python("agentdojo")


class AgentDojoAdapterTest(unittest.TestCase):
    def setUp(self) -> None:
        self.adapter = AgentDojoAdapter(AGENTDOJO_ROOT, AGENTDOJO_PYTHON)
        self.native_id = "travel:user_task_14:injection_task_4:important_instructions"

    @requires_assets(AGENTDOJO_ROOT)
    def test_pinned_case_prompt_and_complete_v1_tool_set(self) -> None:
        source = self.adapter.load_source_result(self.native_id)
        prompt = self.adapter.build_product_prompt(source)
        tools = self.adapter.allowed_tool_names(source)
        self.assertIn("User request:", prompt)
        self.assertIn("MCP server", prompt)
        self.assertIn("at most 30 tool calls", prompt)
        self.assertIn("stop immediately", prompt)
        self.assertIn("get_all_car_rental_companies_in_city", tools)
        self.assertIn("reserve_hotel", tools)
        self.assertEqual(len(tools), 28)

    @requires_assets(AGENTDOJO_ROOT, AGENTDOJO_PYTHON)
    def test_preflight_locks_v1_environment(self) -> None:
        result = self.adapter.preflight()
        self.assertEqual(result["benchmark_version"], "v1")
        self.assertTrue(Path(result["python"]).is_file())

    @requires_assets(AGENTDOJO_ROOT)
    def test_ensure_native_result_waits_for_complete_json(self) -> None:
        source = self.adapter.load_source_result(self.native_id)
        with tempfile.TemporaryDirectory() as tmp:
            result_path = Path(tmp) / "native_result.json"
            result_path.write_text("", encoding="utf-8")
            expected = {"error": None, "function_calls": []}

            def finish_result() -> None:
                time.sleep(0.05)
                result_path.write_text(json.dumps(expected), encoding="utf-8")

            writer = threading.Thread(target=finish_result)
            writer.start()
            self.adapter.ensure_native_result(
                source,
                result_path,
                wait_seconds=1.0,
                poll_seconds=0.01,
            )
            writer.join()
            self.assertEqual(json.loads(result_path.read_text(encoding="utf-8")), expected)

    @requires_assets(AGENTDOJO_ROOT)
    def test_ensure_native_result_replaces_stale_empty_file(self) -> None:
        source = self.adapter.load_source_result(self.native_id)
        with tempfile.TemporaryDirectory() as tmp:
            result_path = Path(tmp) / "native_result.json"
            result_path.write_text("", encoding="utf-8")
            self.adapter.ensure_native_result(source, result_path, wait_seconds=0)
            native = json.loads(result_path.read_text(encoding="utf-8"))
            self.assertIn("did not finalize", native["error"])

    def test_server_artifacts_escape_isolated_unicode_surrogates(self) -> None:
        value = {"arguments": {"text": "bad-surrogate-\udc93"}}
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            events_path = root / "events.jsonl"
            result_path = root / "native.json"
            _append_event(events_path, value)
            _write_json_atomic(result_path, value)
            self.assertEqual(json.loads(events_path.read_text(encoding="utf-8")), value)
            self.assertEqual(json.loads(result_path.read_text(encoding="utf-8")), value)

    @requires_assets(AGENTDOJO_ROOT)
    def test_native_checkpoint_contains_environment_and_calls(self) -> None:
        source = self.adapter.load_source_result(self.native_id)

        class Dumpable:
            def __init__(self, value: dict) -> None:
                self.value = value

            def model_dump(self, *, mode: str) -> dict:
                self.assert_mode = mode
                return self.value

        snapshot = _native_snapshot(
            source,
            "v1",
            Dumpable({"phase": "before"}),
            Dumpable({"phase": "after"}),
            [Dumpable({"function": "search", "args": {"query": "test"}})],
            None,
        )
        self.assertEqual(snapshot["pre_environment"]["phase"], "before")
        self.assertEqual(snapshot["post_environment"]["phase"], "after")
        self.assertEqual(snapshot["function_calls"][0]["function"], "search")
        self.assertIsNone(snapshot["error"])

    def test_server_publishes_tool_catalog_before_loading_heavy_runtime(self) -> None:
        source = {
            "suite_name": "travel",
            "user_task_id": "user_task_1",
            "injection_task_id": "injection_task_1",
            "attack_type": "important_instructions",
            "injections": {},
            "messages": [],
        }
        manifest = {
            "tools": [
                {
                    "name": "search",
                    "description": "Search",
                    "inputSchema": {"type": "object", "properties": {}},
                }
            ]
        }
        stdin = io.StringIO(
            '\n'.join(
                (
                    json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize"}),
                    json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}),
                )
            )
        )
        stdout = io.StringIO()

        class Dumpable:
            def model_dump(self, *, mode: str) -> dict:
                return {"phase": mode}

        def load_after_handshake(*_args):
            responses = [json.loads(line) for line in stdout.getvalue().splitlines()]
            self.assertEqual(responses[1]["result"]["tools"][0]["name"], "search")
            return object(), object(), object(), Dumpable(), Dumpable()

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source_path = root / "source.json"
            manifest_path = root / "manifest.json"
            source_path.write_text(json.dumps(source), encoding="utf-8")
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            with patch(
                "tools.agentdojo_mcp_server._load_runtime",
                side_effect=load_after_handshake,
            ) as mocked_load:
                exit_code = run_server(
                    source_result_path=source_path,
                    tool_manifest_path=manifest_path,
                    result_path=root / "result.json",
                    events_path=root / "events.jsonl",
                    stdin=stdin,
                    stdout=stdout,
                )
            self.assertEqual(exit_code, 0)
            mocked_load.assert_called_once()


if __name__ == "__main__":
    unittest.main()
