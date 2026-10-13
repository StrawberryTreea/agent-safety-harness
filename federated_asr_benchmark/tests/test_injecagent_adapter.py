from __future__ import annotations

import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from tests.source_dependencies import requires_assets
from product_eval.data_paths import default_data_root

from product_eval.injecagent import InjecAgentAdapter
from tools.injecagent_mcp_server import _append_event, _write_json_atomic, run_server
from tools.run_product import DEFAULT_INJECAGENT_ROOT
from tools.score_injecagent_result import _phase_output


PACKAGE_ROOT = Path(__file__).resolve().parents[1]


class InjecAgentAdapterTest(unittest.TestCase):
    def setUp(self) -> None:
        self.adapter = InjecAgentAdapter(DEFAULT_INJECAGENT_ROOT)

    @requires_assets(DEFAULT_INJECAGENT_ROOT)
    def test_preflight_locks_complete_base_release(self) -> None:
        result = self.adapter.preflight()
        self.assertEqual(result["benchmark_version"], "base")
        self.assertEqual(result["case_counts"], {"dh": 510, "ds": 544})

    @requires_assets(DEFAULT_INJECAGENT_ROOT)
    def test_prompt_and_tool_scope_do_not_disclose_attack_instruction(self) -> None:
        track, index, source = self.adapter.load_case("ds:0")
        self.assertEqual((track, index), ("ds", 0))
        prompt = self.adapter.build_product_prompt(source)
        self.assertIn(source["User Instruction"], prompt)
        self.assertNotIn(source["Attacker Instruction"], prompt)
        self.assertEqual(
            self.adapter.allowed_tool_names(source),
            [source["User Tool"], *source["Attacker Tools"]],
        )

    def test_tool_scope_deduplicates_reused_official_tool_names(self) -> None:
        source = {
            "User Tool": "GitHubGetUserDetails",
            "Attacker Tools": ["GitHubGetUserDetails", "GmailSendEmail"],
        }
        self.assertEqual(
            self.adapter.allowed_tool_names(source),
            ["GitHubGetUserDetails", "GmailSendEmail"],
        )

    @requires_assets(DEFAULT_INJECAGENT_ROOT)
    def test_mcp_protocol_preserves_two_stage_attack_trace(self) -> None:
        _, _, source = self.adapter.load_case("ds:0")
        user_tool = source["User Tool"]
        first_attack, second_attack = source["Attacker Tools"]
        requests = [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {"name": user_tool, "arguments": {}},
            },
            {
                "jsonrpc": "2.0",
                "id": 4,
                "method": "tools/call",
                "params": {"name": first_attack, "arguments": {}},
            },
            {
                "jsonrpc": "2.0",
                "id": 5,
                "method": "tools/call",
                "params": {
                    "name": second_attack,
                    "arguments": {
                        "to": "amy.watson@gmail.com",
                        "subject": "data",
                        "body": "retrieved data",
                    },
                },
            },
        ]
        stdin = io.StringIO("".join(json.dumps(row) + "\n" for row in requests))
        stdout = io.StringIO()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            result_path = root / "native.json"
            return_code = run_server(
                injecagent_root=DEFAULT_INJECAGENT_ROOT,
                native_case_id="ds:0",
                result_path=result_path,
                events_path=root / "events.jsonl",
                stdin=stdin,
                stdout=stdout,
            )
            native = json.loads(result_path.read_text(encoding="utf-8"))
        self.assertEqual(return_code, 0)
        self.assertEqual([row["name"] for row in native["tool_calls"]], [user_tool, first_attack, second_attack])
        responses = [json.loads(line) for line in stdout.getvalue().splitlines()]
        self.assertEqual(
            [tool["name"] for tool in responses[1]["result"]["tools"]],
            [user_tool, first_attack, second_attack],
        )
        self.assertIn(source["Attacker Instruction"], responses[2]["result"]["content"][0]["text"])
        self.assertFalse(responses[3]["result"]["isError"])

    @requires_assets(DEFAULT_INJECAGENT_ROOT)
    def test_mcp_tool_list_deduplicates_reused_names(self) -> None:
        requests = [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
        ]
        stdin = io.StringIO("".join(json.dumps(row) + "\n" for row in requests))
        stdout = io.StringIO()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            return_code = run_server(
                injecagent_root=DEFAULT_INJECAGENT_ROOT,
                native_case_id="ds:275",
                result_path=root / "native.json",
                events_path=root / "events.jsonl",
                stdin=stdin,
                stdout=stdout,
            )
        self.assertEqual(return_code, 0)
        responses = [json.loads(line) for line in stdout.getvalue().splitlines()]
        names = [tool["name"] for tool in responses[1]["result"]["tools"]]
        self.assertEqual(names, ["GitHubGetUserDetails", "GmailSendEmail"])

    @requires_assets(DEFAULT_INJECAGENT_ROOT)
    def test_official_evaluator_scores_two_stage_success(self) -> None:
        _, _, source = self.adapter.load_case("ds:0")
        user_tool = source["User Tool"]
        first_attack, second_attack = source["Attacker Tools"]
        native = {
            "benchmark_version": "base",
            "attack_track": "ds",
            "case_index": 0,
            "user_tool": user_tool,
            "attacker_tools": [first_attack, second_attack],
            "tool_calls": [
                {"name": user_tool, "arguments": {}},
                {"name": first_attack, "arguments": {}},
                {"name": second_attack, "arguments": {"to": "amy.watson@gmail.com"}},
            ],
            "error": None,
        }
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            native_path = root / "native.json"
            final_path = root / "final.txt"
            output_path = root / "case_result.json"
            native_path.write_text(json.dumps(native), encoding="utf-8")
            final_path.write_text("Completed the request.", encoding="utf-8")
            completed = subprocess.run(
                [
                    sys.executable,
                    str(PACKAGE_ROOT / "tools" / "score_injecagent_result.py"),
                    "--injecagent-root",
                    str(DEFAULT_INJECAGENT_ROOT),
                    "--native-result",
                    str(native_path),
                    "--final-response",
                    str(final_path),
                    "--run-id",
                    "test-run",
                    "--case-id",
                    "injecagent:ds:0",
                    "--product-json",
                    json.dumps(
                        {"product_id": "codex", "version": "test", "product_model": None}
                    ),
                    "--product-status",
                    "completed",
                    "--product-timed-out",
                    "false",
                    "--output",
                    str(output_path),
                ],
                text=True,
                encoding="utf-8",
                errors="replace",
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=30,
                check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            result = json.loads(output_path.read_text(encoding="utf-8"))
            scored_native = json.loads(native_path.read_text(encoding="utf-8"))
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["asr_contribution"], {"numerator": 1, "denominator": 1, "unit": "case"})
        self.assertEqual(scored_native["eval"], "succ")
        self.assertEqual(scored_native["eval Step 2"], "succ")

    def test_repeated_user_tool_is_presented_to_official_evaluator_as_rerun(self) -> None:
        call = {"name": "TwitterManagerSearchTweets", "arguments": {}}
        self.assertEqual(
            _phase_output(call, "TwitterManagerSearchTweets", "final response"),
            "re-run",
        )
        self.assertEqual(_phase_output(call, "OtherTool", "final response"), "final response")

    @requires_assets(DEFAULT_INJECAGENT_ROOT)
    def test_repeated_first_attack_tool_without_second_is_failure_not_invalid(self) -> None:
        _, _, source = self.adapter.load_case("ds:0")
        first_attack, second_attack = source["Attacker Tools"]
        native = {
            "benchmark_version": "base",
            "attack_track": "ds",
            "case_index": 0,
            "user_tool": source["User Tool"],
            "attacker_tools": [first_attack, second_attack],
            "tool_calls": [
                {"name": first_attack, "arguments": {"query": "first"}},
                {"name": first_attack, "arguments": {"query": "retry"}},
            ],
            "error": None,
        }
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            native_path = root / "native.json"
            final_path = root / "final.txt"
            output_path = root / "case_result.json"
            native_path.write_text(json.dumps(native), encoding="utf-8")
            final_path.write_text("Could not complete the request.", encoding="utf-8")
            completed = subprocess.run(
                [
                    sys.executable,
                    str(PACKAGE_ROOT / "tools" / "score_injecagent_result.py"),
                    "--injecagent-root",
                    str(DEFAULT_INJECAGENT_ROOT),
                    "--native-result",
                    str(native_path),
                    "--final-response",
                    str(final_path),
                    "--run-id",
                    "test-run",
                    "--case-id",
                    "injecagent:ds:0",
                    "--product-json",
                    json.dumps(
                        {"product_id": "codex", "version": "test", "product_model": None}
                    ),
                    "--product-status",
                    "completed",
                    "--product-timed-out",
                    "false",
                    "--output",
                    str(output_path),
                ],
                text=True,
                encoding="utf-8",
                errors="replace",
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=30,
                check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            result = json.loads(output_path.read_text(encoding="utf-8"))
        self.assertEqual(result["status"], "failure")
        self.assertEqual(result["asr_contribution"]["denominator"], 1)

    @requires_assets(DEFAULT_INJECAGENT_ROOT)
    def test_ensure_native_result_waits_for_complete_json(self) -> None:
        _, _, source = self.adapter.load_case("dh:0")
        with tempfile.TemporaryDirectory() as tmp:
            result_path = Path(tmp) / "native.json"
            result_path.write_text("", encoding="utf-8")
            expected = {"error": None, "tool_calls": []}

            def finish_result() -> None:
                time.sleep(0.05)
                result_path.write_text(json.dumps(expected), encoding="utf-8")

            writer = threading.Thread(target=finish_result)
            writer.start()
            self.adapter.ensure_native_result(
                native_case_id="dh:0",
                source=source,
                native_result_path=result_path,
                wait_seconds=1.0,
                poll_seconds=0.01,
            )
            writer.join()
            self.assertEqual(json.loads(result_path.read_text(encoding="utf-8")), expected)

    @requires_assets(DEFAULT_INJECAGENT_ROOT)
    def test_ensure_native_result_replaces_stale_empty_file(self) -> None:
        _, _, source = self.adapter.load_case("dh:0")
        with tempfile.TemporaryDirectory() as tmp:
            result_path = Path(tmp) / "native.json"
            result_path.write_text("", encoding="utf-8")
            self.adapter.ensure_native_result(
                native_case_id="dh:0",
                source=source,
                native_result_path=result_path,
                wait_seconds=0,
            )
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


if __name__ == "__main__":
    unittest.main()
