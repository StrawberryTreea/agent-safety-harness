from __future__ import annotations

import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import types
import unittest
from tests.source_dependencies import requires_assets
from product_eval.data_paths import default_data_root, runtime_python
from unittest.mock import patch

from product_eval.saber import SaberAdapter
from tools.saber_mcp_server import (
    _append_event,
    _iter_wire_messages,
    _parse_wire_request,
    _write_json_atomic,
    _write_result,
    run_server,
)


PACKAGE_ROOT = Path(__file__).resolve().parents[1]
WORKSPACE_ROOT = PACKAGE_ROOT.parent
SABER_ROOT = default_data_root() / "saber-main" / "saber-main"


class SaberAdapterTest(unittest.TestCase):
    def setUp(self) -> None:
        if (SABER_ROOT / "tasks").is_dir():
            self.adapter = SaberAdapter(SABER_ROOT)
        else:
            fixture = tempfile.TemporaryDirectory()
            self.addCleanup(fixture.cleanup)
            fixture_root = Path(fixture.name)
            (fixture_root / "tasks").mkdir()
            self.adapter = SaberAdapter(fixture_root)

    @requires_assets(SABER_ROOT)
    def test_locates_existing_task_and_rejects_manifest_ghost(self) -> None:
        self.assertTrue(self.adapter.task_path("A_fs_001").is_file())
        with self.assertRaises(FileNotFoundError):
            self.adapter.task_path("A_persist_035")

    @requires_assets(SABER_ROOT)
    def test_product_prompt_routes_tools_without_exposing_ground_truth(self) -> None:
        task = self.adapter.load_task("A_fs_001")
        prompt = self.adapter.build_product_prompt(task)
        self.assertIn("MCP server", prompt)
        self.assertIn("at most 30 tool calls", prompt)
        self.assertIn("stop immediately", prompt)
        self.assertIn(task["setup"]["user_prompt"], prompt)
        self.assertNotIn(task["ground_truth"]["harm_description"], prompt)
        self.assertNotIn("untrusted", prompt.casefold())

    def test_judged_mapping_matches_official_effective_denominator(self) -> None:
        product = {"product_id": "codex", "version": "test", "product_model": None}
        with tempfile.TemporaryDirectory() as tmp:
            judged = Path(tmp) / "judged.json"
            judged.write_text(
                json.dumps({"id": "A_fs_001", "harmful": True, "termination": "Harmful Completion"}),
                encoding="utf-8",
            )
            success = self.adapter.case_result_from_judged(
                run_id="run", product=product, case_id="saber:A_fs_001", judged_path=judged
            )
            self.assertEqual(success["status"], "success")
            self.assertEqual(success["asr_contribution"], {"numerator": 1, "denominator": 1, "unit": "case"})

            judged.write_text(
                json.dumps({"id": "A_fs_001", "harmful": False, "termination": "Incapable"}),
                encoding="utf-8",
            )
            excluded = self.adapter.case_result_from_judged(
                run_id="run", product=product, case_id="saber:A_fs_001", judged_path=judged
            )
            self.assertEqual(excluded["status"], "excluded")
            self.assertEqual(excluded["asr_contribution"]["denominator"], 0)

    def test_runtime_preflight_fails_when_official_image_is_missing(self) -> None:
        from unittest.mock import patch

        completed = type("Completed", (), {"returncode": 1, "stdout": "", "stderr": "missing"})()
        with patch("product_eval.saber.subprocess.run", return_value=completed):
            with self.assertRaisesRegex(RuntimeError, "osbench-sandbox"):
                self.adapter.preflight_runtime()

    def test_codebuddy_cleanup_requires_exact_saber_container_evidence(self) -> None:
        container_id = "a" * 64
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            events = root / "events.jsonl"
            audit = root / "cleanup.json"
            events.write_text(
                json.dumps(
                    {
                        "event_type": "runtime_started",
                        "task_id": "A_test_001",
                        "container_id": container_id,
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            inspect = subprocess.CompletedProcess(
                [], 0, f"{container_id}|/osbench-test|osbench-sandbox\n", ""
            )
            remove = subprocess.CompletedProcess([], 0, container_id + "\n", "")
            with patch(
                "product_eval.saber.subprocess.run", side_effect=[inspect, remove]
            ) as run:
                result = self.adapter.cleanup_runtime_from_events(events, audit)
            self.assertEqual(result["removed_container_ids"], [container_id])
            self.assertEqual(run.call_args_list[1].args[0], ["docker", "rm", "-f", container_id])
            self.assertEqual(json.loads(audit.read_text())["removed_container_ids"], [container_id])

    def test_judge_retries_a_transient_judge_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "tasks").mkdir()
            adapter = SaberAdapter(root)
            judged = root / "judged" / "product_test" / "A" / "fs_destruction" / "A_fs_001.json"
            attempts = 0

            def run_judge(*_args, **kwargs):
                nonlocal attempts
                attempts += 1
                self.assertEqual(kwargs["env"]["PYTHONUTF8"], "1")
                self.assertEqual(kwargs["env"]["PYTHONIOENCODING"], "utf-8")
                judged.parent.mkdir(parents=True, exist_ok=True)
                result = {
                    "id": "A_fs_001",
                    "harmful": False,
                    "termination": "Safe Completion",
                    "judge_err": "SSL EOF" if attempts == 1 else "",
                }
                judged.write_text(json.dumps(result), encoding="utf-8")
                return type("Completed", (), {"returncode": 0, "stdout": "", "stderr": ""})()

            with patch.object(adapter, "preflight_judge", return_value={}), patch(
                "product_eval.saber.subprocess.run", side_effect=run_judge
            ):
                result_path = adapter.judge_native_result(
                    product_slug="product_test",
                    task={"id": "A_fs_001", "scenario": "A", "category": "fs_destruction"},
                )

            self.assertEqual(attempts, 2)
            self.assertEqual(json.loads(result_path.read_text(encoding="utf-8"))["judge_err"], "")

    def test_published_judge_copy_replaces_isolated_surrogates(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "tasks").mkdir()
            native = root / "native.json"
            native.write_text(
                json.dumps(
                    {
                        "id": "A_fs_001",
                        "scenario": "A",
                        "category": "fs_destruction",
                        "text": "bad-\udc80",
                    }
                ),
                encoding="utf-8",
            )
            published = SaberAdapter(root).publish_native_result(native, "product_test")

            self.assertIn("bad-\\udc80", native.read_text(encoding="utf-8"))
            self.assertEqual(
                json.loads(published.read_text(encoding="utf-8"))["text"], "bad-\ufffd"
            )

    def test_judged_result_accepts_official_windows_encoding(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            judged = Path(tmp) / "judged.json"
            expected = {"id": "A_data_014", "harmful": True, "command": "\u6267\u884c\u6d4b\u8bd5"}
            for encoding in ("utf-8", "utf-8-sig", "gb18030"):
                with self.subTest(encoding=encoding), patch(
                    "product_eval.saber.locale.getpreferredencoding", return_value="cp1252"
                ):
                    judged.write_bytes(json.dumps(expected, ensure_ascii=False).encode(encoding))
                    self.assertEqual(self.adapter.load_judged_result(judged), expected)

    def test_judge_preflight_rejects_missing_and_placeholder_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "tasks").mkdir()
            adapter = SaberAdapter(root)
            with self.assertRaisesRegex(RuntimeError, "config.json is missing"):
                adapter.preflight_judge()

            (root / "config.json").write_text(
                json.dumps(
                    {
                        "base_url": "https://your-default-api-proxy.example.com",
                        "judge": {
                            "id": "judge-model",
                            "type": "anthropic",
                            "key": "YOUR_JUDGE_KEY_HERE",
                        },
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(RuntimeError, "placeholder"):
                adapter.preflight_judge()

    def test_judge_preflight_returns_only_non_secret_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "tasks").mkdir()
            (root / "config.json").write_text(
                json.dumps(
                    {
                        "judge": {
                            "id": "judge-model",
                            "type": "openai",
                            "key": "secret-not-returned",
                            "base_url": "https://judge.internal",
                        }
                    }
                ),
                encoding="utf-8",
            )
            metadata = SaberAdapter(root).preflight_judge()
            self.assertEqual(
                metadata,
                {
                    "id": "judge-model",
                    "type": "openai",
                    "base_url": "https://judge.internal",
                    "key_env": None,
                    "temperature": 0.0,
                },
            )

    def test_judge_preflight_accepts_key_from_environment(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "tasks").mkdir()
            (root / "config.json").write_text(
                json.dumps(
                    {
                        "judge": {
                            "id": "deepseek-v4-flash",
                            "type": "openai",
                            "key_env": "TEST_SABER_JUDGE_KEY",
                            "base_url": "https://api.deepseek.com",
                        }
                    }
                ),
                encoding="utf-8",
            )
            with patch.dict("os.environ", {"TEST_SABER_JUDGE_KEY": "secret-not-returned"}):
                metadata = SaberAdapter(root).preflight_judge()
            self.assertEqual(
                metadata,
                {
                    "id": "deepseek-v4-flash",
                    "type": "openai",
                    "base_url": "https://api.deepseek.com",
                    "key_env": "TEST_SABER_JUDGE_KEY",
                    "temperature": 0.0,
                },
            )


class SaberMcpServerTest(unittest.TestCase):
    def test_server_outputs_escape_isolated_unicode_surrogates(self) -> None:
        value = {"text": "bad-surrogate-\udc93"}
        output = io.StringIO()
        _write_result(output, 1, value)
        wire_bytes = output.getvalue().encode("utf-8")
        self.assertEqual(json.loads(wire_bytes)["result"], value)

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            events_path = root / "events.jsonl"
            result_path = root / "native.json"
            _append_event(events_path, value)
            _write_json_atomic(result_path, value)
            self.assertEqual(json.loads(events_path.read_text(encoding="utf-8")), value)
            self.assertEqual(json.loads(result_path.read_text(encoding="utf-8")), value)

    def test_recovers_raw_quotes_and_newlines_in_bash_tool_call(self) -> None:
        malformed = (
            '{"jsonrpc":"2.0","id":7,"method":"tools/call",'
            '"params":{"name":"bash","arguments":{"command":"'
            'printf "hello"\nnext line'
            '"}}}\n'
        )
        following = '{"jsonrpc":"2.0","id":8,"method":"ping","params":{}}\n'
        messages = list(_iter_wire_messages(io.StringIO(malformed + following)))
        self.assertEqual(len(messages), 2)

        request, recovered, error = _parse_wire_request(messages[0])
        self.assertIsNone(error)
        self.assertEqual(request["params"]["arguments"]["command"], 'printf "hello"\nnext line')
        self.assertEqual(recovered["event_type"], "protocol_recovered")
        self.assertIn("wire_sha256", recovered)
        self.assertNotIn("printf", json.dumps(recovered))

        request, recovered, error = _parse_wire_request(messages[1])
        self.assertEqual(request["method"], "ping")
        self.assertIsNone(recovered)
        self.assertIsNone(error)

    def test_does_not_recover_unknown_malformed_request(self) -> None:
        malformed = (
            '{"jsonrpc":"2.0","id":9,"method":"tools/call",'
            '"params":{"name":"other","arguments":{"command":"echo "bad""}}}'
        )
        request, recovered, error = _parse_wire_request(malformed)
        self.assertIsNone(request)
        self.assertIsNone(recovered)
        self.assertEqual(error["event_type"], "protocol_error")
        self.assertIn("wire_sha256", error)
        self.assertNotIn("echo", json.dumps(error))

    def test_protocol_and_native_result_capture(self) -> None:
        class FakeRuntime:
            cleaned = False

            def __init__(self, task):
                self.events = []

            def get_tools(self, provider_type):
                return [
                    {
                        "name": "bash",
                        "description": "Run a command",
                        "input_schema": {
                            "type": "object",
                            "properties": {"command": {"type": "string"}},
                            "required": ["command"],
                        },
                    }
                ]

            def execute_tool(self, name, arguments):
                self.events.append({"tool_name": name, "input": arguments})
                return "ok"

            def get_shell_trajectory(self):
                return [{"step": 1, "command": "pwd", "output": "ok", "deltas": []}]

            def get_events(self):
                return list(self.events)

            def cleanup(self):
                FakeRuntime.cleaned = True

        fake_module = types.ModuleType("task_runtime")
        fake_module.TaskRuntime = FakeRuntime
        previous = sys.modules.get("task_runtime")
        sys.modules["task_runtime"] = fake_module
        try:
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                task_path = root / "task.json"
                result_path = root / "result.json"
                events_path = root / "events.jsonl"
                task_path.write_text(
                    json.dumps(
                        {
                            "id": "A_test_001",
                            "scenario": "A",
                            "category": "test",
                            "setup": {"user_prompt": "test"},
                        }
                    ),
                    encoding="utf-8",
                )
                requests = "\n".join(
                    json.dumps(item)
                    for item in (
                        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
                        {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
                        {
                            "jsonrpc": "2.0",
                            "id": 3,
                            "method": "tools/call",
                            "params": {"name": "bash", "arguments": {"command": "pwd"}},
                        },
                    )
                )
                output = io.StringIO()
                code = run_server(
                    saber_root=root,
                    task_path=task_path,
                    result_path=result_path,
                    events_path=events_path,
                    stdin=io.StringIO(requests + "\n"),
                    stdout=output,
                )
                responses = [json.loads(line) for line in output.getvalue().splitlines()]
                native = json.loads(result_path.read_text(encoding="utf-8"))
                self.assertEqual(code, 0)
                self.assertEqual(responses[1]["result"]["tools"][0]["name"], "bash")
                self.assertEqual(responses[2]["result"]["content"][0]["text"], "ok")
                self.assertEqual(native["trajectory"][0]["command"], "pwd")
                self.assertTrue(FakeRuntime.cleaned)
        finally:
            if previous is None:
                sys.modules.pop("task_runtime", None)
            else:
                sys.modules["task_runtime"] = previous


if __name__ == "__main__":
    unittest.main()
