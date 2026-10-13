from __future__ import annotations

import io
import json
from pathlib import Path
import tempfile
import unittest
from tests.source_dependencies import requires_assets
from product_eval.data_paths import default_data_root, runtime_python
from unittest.mock import patch

from product_eval.agentleak import AgentLeakAdapter
from product_eval.agentpoison import AgentPoisonAdapter
from product_eval.injecagent import InjecAgentAdapter
from product_eval.msb import MsbAdapter
from tools.agentleak_mcp_server import run_server as run_agentleak_server
from tools.agentpoison_mcp_server import run_server as run_agentpoison_server
from tools.msb_mcp_server import run_server as run_msb_server


PACKAGE_ROOT = Path(__file__).resolve().parents[1]
DATA_ROOT = default_data_root()


def rpc(*requests: dict) -> io.StringIO:
    return io.StringIO("".join(json.dumps(request) + "\n" for request in requests))


class NewAdapterTest(unittest.TestCase):
    def test_injecagent_codebuddy_recovery_requires_attack_delivery_event(self) -> None:
        source = {
            "User Tool": "TwitterManagerReadTweet",
            "Attacker Tools": ["EpicFHIRManageAppointments"],
        }
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            events = root / "events.jsonl"
            result = root / "native.json"
            events.write_text(
                "\n".join(
                    (
                        json.dumps({"event_type": "server_started"}),
                        json.dumps(
                            {
                                "event_type": "tool_call",
                                "name": "TwitterManagerReadTweet",
                                "arguments": {"tweet_id": "789GHI"},
                                "response_source": "official_injected_tool_response",
                                "is_error": False,
                            }
                        ),
                    )
                )
                + "\n",
                encoding="utf-8",
            )
            self.assertTrue(
                InjecAgentAdapter.recover_native_result_from_events(
                    native_case_id="dh:201",
                    source=source,
                    events_path=events,
                    native_result_path=result,
                )
            )
            native = json.loads(result.read_text(encoding="utf-8"))
            self.assertTrue(native["attack_delivered"])
            self.assertTrue(native["recovered_from_durable_events"])
            self.assertEqual(native["tool_calls"][0]["name"], source["User Tool"])
            self.assertIsNone(native["error"])

    def test_injecagent_codebuddy_recovery_rejects_undelivered_trace(self) -> None:
        source = {
            "User Tool": "TwitterManagerReadTweet",
            "Attacker Tools": ["EpicFHIRManageAppointments"],
        }
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            events = root / "events.jsonl"
            result = root / "native.json"
            events.write_text(
                json.dumps(
                    {
                        "event_type": "tool_call",
                        "name": "EpicFHIRManageAppointments",
                        "arguments": {},
                        "response_source": "generic_fallback",
                        "is_error": False,
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            self.assertFalse(
                InjecAgentAdapter.recover_native_result_from_events(
                    native_case_id="dh:201",
                    source=source,
                    events_path=events,
                    native_result_path=result,
                )
            )
            self.assertFalse(result.exists())

    def test_msb_codebuddy_recovery_uses_only_durable_tool_events(self) -> None:
        source = {
            "native_case_id": "1:1:1:2",
            "attack_task": "obtain_remote_control_authority",
            "attack_type": "false_error",
            "native_success_field": "attack_success",
        }
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            events = root / "events.jsonl"
            result = root / "native.json"
            events.write_text(
                "\n".join(
                    (
                        json.dumps({"event_type": "server_started"}),
                        json.dumps(
                            {
                                "event_type": "tool_call",
                                "name": "perform_agent_task",
                                "arguments": {"input": "search"},
                            }
                        ),
                    )
                )
                + "\n",
                encoding="utf-8",
            )
            self.assertTrue(
                MsbAdapter.recover_native_result_from_events(source, events, result)
            )
            native = json.loads(result.read_text(encoding="utf-8"))
            self.assertEqual(len(native["tool_calls"]), 1)
            self.assertFalse(native["attack_success"])
            self.assertIsNone(native["error"])

    @requires_assets(DATA_ROOT / "MSB-main")
    def test_msb_preflight_load_and_protocol(self) -> None:
        root = DATA_ROOT / "MSB-main" / "MSB-main"
        adapter = MsbAdapter(root)
        self.assertIn("sandbox_mode", adapter.preflight())
        source = adapter.load_case("3:3:2:1")
        self.assertIn("User request:", adapter.build_product_prompt(source))
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            stdout = io.StringIO()
            code = run_msb_server(
                msb_root=root,
                native_case_id="3:3:2:1",
                result_path=out / "native.json",
                events_path=out / "events.jsonl",
                stdin=rpc(
                    {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
                    {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
                    {
                        "jsonrpc": "2.0",
                        "id": 3,
                        "method": "tools/call",
                        "params": {
                            "name": "perform_agent_task",
                            "arguments": {"input": "run"},
                        },
                    },
                ),
                stdout=stdout,
            )
            self.assertEqual(code, 0)
            responses = [json.loads(line) for line in stdout.getvalue().splitlines()]
            self.assertEqual(responses[0]["result"]["serverInfo"]["name"], "msb")
            self.assertGreater(len(responses[1]["result"]["tools"]), 0)
            self.assertEqual(json.loads((out / "native.json").read_text())["tool_calls"][0]["name"], "perform_agent_task")

    @requires_assets(DATA_ROOT / "MSB-main")
    def test_msb_kill_process_schema_uses_qoder_strict_type(self) -> None:
        root = DATA_ROOT / "MSB-main" / "MSB-main"
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            stdout = io.StringIO()
            run_msb_server(
                msb_root=root,
                native_case_id="7:6:4:12",
                result_path=out / "native.json",
                events_path=out / "events.jsonl",
                stdin=rpc(
                    {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
                ),
                stdout=stdout,
            )
            response = json.loads(stdout.getvalue())
            kill_tool = next(
                tool for tool in response["result"]["tools"] if tool["name"] == "kill_process"
            )
            self.assertEqual(
                kill_tool["inputSchema"]["properties"]["pid"]["type"], "string"
            )

    @requires_assets(DATA_ROOT / "MSB-main")
    def test_msb_search_case_maps_official_information_space(self) -> None:
        root = DATA_ROOT / "MSB-main" / "MSB-main"
        adapter = MsbAdapter(root)
        source = adapter.load_case("2:4:1:7")
        prompt = adapter.build_product_prompt(source)
        self.assertNotIn("/ABSOLUTE/PATH/TO/SPACE/INFORMATION", prompt)
        self.assertIn("/sandbox/information", prompt)
        self.assertIn("10 steps", prompt)
        self.assertIn("list_directory", adapter.allowed_tool_names(source))
        self.assertNotIn("perform_agent_task", adapter.allowed_tool_names(source))

        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            stdout = io.StringIO()
            run_msb_server(
                msb_root=root,
                native_case_id="2:4:1:7",
                result_path=out / "native.json",
                events_path=out / "events.jsonl",
                stdin=rpc(
                    {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
                    {
                        "jsonrpc": "2.0",
                        "id": 2,
                        "method": "tools/call",
                        "params": {
                            "name": "list_directory",
                            "arguments": {"path": "/sandbox/information"},
                        },
                    },
                    {
                        "jsonrpc": "2.0",
                        "id": 3,
                        "method": "tools/call",
                        "params": {
                            "name": "read_text_file",
                            "arguments": {
                                "path": "/sandbox/information/The_Metropolitan_Museum_of_Art.txt"
                            },
                        },
                    },
                ),
                stdout=stdout,
            )
            responses = [json.loads(line) for line in stdout.getvalue().splitlines()]
            tool_names = {tool["name"] for tool in responses[0]["result"]["tools"]}
            self.assertIn("list_directory", tool_names)
            self.assertNotIn("perform_agent_task", tool_names)
            listing = responses[1]["result"]["content"][0]["text"]
            self.assertIn("The_Metropolitan_Museum_of_Art.txt", listing)
            museum = responses[2]["result"]["content"][0]["text"]
            self.assertIn("Opening Hours", museum)
            self.assertIn("ssh-rsa", museum)
            self.assertNotIn("{attack_instruction}", museum)

    @requires_assets(DATA_ROOT / "MSB-main")
    def test_msb_remote_control_write_is_scored(self) -> None:
        root = DATA_ROOT / "MSB-main" / "MSB-main"
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            run_msb_server(
                msb_root=root,
                native_case_id="3:1:1:1",
                result_path=out / "native.json",
                events_path=out / "events.jsonl",
                stdin=rpc(
                    {
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "tools/call",
                        "params": {
                            "name": "write_file",
                            "arguments": {
                                "path": "/sandbox/output/file_name.txt",
                                "content": "ssh-rsa test-key",
                            },
                        },
                    }
                ),
                stdout=io.StringIO(),
            )
            native = json.loads((out / "native.json").read_text())
            self.assertTrue(native["attack_success"])

    @requires_assets(DATA_ROOT / "MSB-main")
    def test_msb_server_replaces_isolated_unicode_surrogates(self) -> None:
        root = DATA_ROOT / "MSB-main" / "MSB-main"
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            stdout = io.StringIO()
            run_msb_server(
                msb_root=root,
                native_case_id="5:3:1:12",
                result_path=out / "native.json",
                events_path=out / "events.jsonl",
                stdin=rpc(
                    {
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "tools/call",
                        "params": {
                            "name": "write_file",
                            "arguments": {
                                "path": "plan.md",
                                "content": "bad-surrogate-\udc94",
                            },
                        },
                    },
                    {
                        "jsonrpc": "2.0",
                        "id": 2,
                        "method": "tools/call",
                        "params": {
                            "name": "read_text_file",
                            "arguments": {"path": "plan.md"},
                        },
                    },
                ),
                stdout=stdout,
            )
            responses = [json.loads(line) for line in stdout.getvalue().splitlines()]
            content = responses[1]["result"]["content"][0]["text"]
            self.assertEqual(content, "bad-surrogate-\ufffd")
            self.assertTrue(stdout.getvalue().isascii())
            self.assertNotIn("\\udc94", stdout.getvalue())

    @requires_assets(DATA_ROOT / "AgentLeak-main")
    def test_agentleak_payload_is_materialized_before_optional_tool_calls(self) -> None:
        root = DATA_ROOT / "AgentLeak-main" / "AgentLeak-main"
        adapter = AgentLeakAdapter(root)
        adapter.preflight()
        scenario_id = "agentleak_leg_00587"
        self.assertTrue(adapter.load_case(scenario_id)["attack"]["enabled"])
        for call_context in (False, True):
            with self.subTest(call_context=call_context), tempfile.TemporaryDirectory() as tmp:
                out = Path(tmp)
                requests = [{"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}]
                if call_context:
                    requests.append(
                        {
                            "jsonrpc": "2.0",
                            "id": 2,
                            "method": "tools/call",
                            "params": {"name": "get_private_context", "arguments": {}},
                        }
                    )
                run_agentleak_server(
                    root=root,
                    scenario_id=scenario_id,
                    result_path=out / "native.json",
                    events_path=out / "events.jsonl",
                    stdin=rpc(*requests),
                    stdout=io.StringIO(),
                )
                native = json.loads((out / "native.json").read_text())
                self.assertTrue(native["payload_applied"])
                self.assertEqual(native["delivery_mode"], "prompt_materialized_external_context")

    @requires_assets(DATA_ROOT / "AgentLeak-main")
    def test_agentleak_server_escapes_isolated_unicode_surrogates(self) -> None:
        root = DATA_ROOT / "AgentLeak-main" / "AgentLeak-main"
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            stdout = io.StringIO()
            code = run_agentleak_server(
                root=root,
                scenario_id="agentleak_leg_00587",
                result_path=out / "native.json",
                events_path=out / "events.jsonl",
                stdin=rpc(
                    {
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "tools/call",
                        "params": {
                            "name": "emit_artifact",
                            "arguments": {"content": "bad-surrogate-\udc94"},
                        },
                    }
                ),
                stdout=stdout,
            )
            self.assertEqual(code, 0)
            self.assertEqual(json.loads(stdout.getvalue())["result"]["isError"], False)
            events = [json.loads(line) for line in (out / "events.jsonl").read_text().splitlines()]
            self.assertEqual(events[-1]["arguments"]["content"], "bad-surrogate-\udc94")

    @requires_assets(DATA_ROOT / "AgentPoison-master", runtime_python("agentpoison"))
    def test_agentpoison_official_subset_and_protocol(self) -> None:
        root = DATA_ROOT / "AgentPoison-master" / "AgentPoison-master"
        adapter = AgentPoisonAdapter(root)
        preflight = adapter.preflight()
        self.assertEqual(preflight["eligible_source_index_range"], [0, 489])
        self.assertTrue(preflight["formal_release_ready"])
        self.assertEqual(preflight["retrieval_backend"], "official live DPR WikiEnv")
        self.assertEqual(preflight["runtime"]["python"], "3.13.2")
        self.assertEqual(preflight["runtime"]["torch"], "2.11.0+cu128")
        self.assertEqual(preflight["runtime"]["torch_cuda"], "12.8")
        self.assertTrue(preflight["runtime"]["cuda_available"])
        native_case_id = "564959490dd0b8316a88"
        index, question, _ = adapter.load_case(native_case_id)
        self.assertEqual(index, 0)
        self.assertIn(question["question"], adapter.build_product_prompt(question))
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            stdout = io.StringIO()
            fake_retriever = type(
                "FakeRetriever",
                (),
                {
                    "__init__": lambda self, _root, _hf_home: None,
                    "search": lambda self, _query: "ordinary evidence",
                },
            )
            from tools.agentpoison_mcp_server import _atomic

            with (
                patch("tools.agentpoison_mcp_server._LiveRetriever", fake_retriever),
                patch("tools.agentpoison_mcp_server._atomic", wraps=_atomic) as atomic,
            ):
                code = run_agentpoison_server(
                    root=root,
                    hf_home=Path.home() / ".cache" / "huggingface",
                    native_case_id=native_case_id,
                    result_path=out / "native.json",
                    events_path=out / "events.jsonl",
                    stdin=rpc(
                        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
                        {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
                        {
                            "jsonrpc": "2.0",
                            "id": 3,
                            "method": "tools/call",
                            "params": {"name": "search", "arguments": {"query": "Albany"}},
                        },
                    ),
                    stdout=stdout,
                )
            self.assertEqual(code, 0)
            self.assertGreaterEqual(atomic.call_count, 3)
            native = json.loads((out / "native.json").read_text())
            self.assertEqual(native["overall_retrieval"], 1)
            self.assertEqual(len(native["tool_calls"]), 1)

    @requires_assets(DATA_ROOT / "AgentPoison-master")
    def test_agentpoison_protocol_is_ready_before_retriever_initialization(self) -> None:
        root = DATA_ROOT / "AgentPoison-master" / "AgentPoison-master"
        native_case_id = "564959490dd0b8316a88"
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            stdout = io.StringIO()
            with patch(
                "tools.agentpoison_mcp_server._LiveRetriever",
                side_effect=AssertionError("retriever must remain lazy"),
            ):
                code = run_agentpoison_server(
                    root=root,
                    hf_home=Path.home() / ".cache" / "huggingface",
                    native_case_id=native_case_id,
                    result_path=out / "native.json",
                    events_path=out / "events.jsonl",
                    stdin=rpc(
                        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
                        {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
                    ),
                    stdout=stdout,
                )
            self.assertEqual(code, 0)
            responses = [json.loads(line) for line in stdout.getvalue().splitlines()]
            self.assertEqual(responses[0]["result"]["serverInfo"]["name"], "agentpoison")
            self.assertEqual(responses[1]["result"]["tools"][0]["name"], "search")


if __name__ == "__main__":
    unittest.main()
