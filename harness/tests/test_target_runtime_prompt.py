from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from agent_safety_harness.agents import CliAgent, OpenAICompatibleAgent, _expand_cli_arg
from agent_safety_harness.schema import TaskSpec


class RecordingClient:
    model = "recording-model"

    def __init__(self) -> None:
        self.messages: list[dict[str, str]] = []

    def complete_with_usage(self, messages: list[dict[str, str]]) -> SimpleNamespace:
        self.messages = messages
        return SimpleNamespace(content="ok", usage={"total_tokens": 1})


def sample_task() -> TaskSpec:
    return TaskSpec.from_record({
        "task_id": "runtime-prompt-1",
        "benchmark": "runtime_prompt_test",
        "profile": "local_cli",
        "user_goal": "Return the benign marker.",
        "labels": {"expected_behavior": "complete"},
    })


class TargetRuntimePromptTests(unittest.TestCase):
    def test_cli_runtime_expands_python_and_harness_entrypoint_placeholders(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            expanded = _expand_cli_arg(
                "{python_executable}|{harness_ash}|{task_json}|{mcp_events}",
                workspace_path=workspace,
                task_json=workspace / "task.json",
                prompt_file=workspace / "prompt.txt",
                mcp_config=workspace / "mcp.json",
                mcp_events=workspace / "events.jsonl",
                mcp_allowed_tools="",
                task=sample_task(),
            )

        python_path, ash_path, task_path, events_path = expanded.split("|")
        self.assertEqual(Path(python_path).resolve(), Path(sys.executable).resolve())
        self.assertEqual(Path(ash_path).resolve(), (ROOT / "ash.py").resolve())
        self.assertEqual(Path(task_path).name, "task.json")
        self.assertEqual(Path(events_path).name, "events.jsonl")

    def test_model_config_system_prompt_reaches_chat_messages(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            config_path = Path(directory) / "models.json"
            config_path.write_text(json.dumps({
                "models": {
                    "candidate": {
                        "provider": "openai_compatible",
                        "base_url": "https://example.invalid/v1",
                        "api_key_env": "TEST_ONLY_API_KEY",
                        "model": "candidate-model",
                        "system_prompt": "MODEL_POLICY_MARKER",
                    }
                }
            }), encoding="utf-8")
            agent = OpenAICompatibleAgent.from_config("candidate", str(config_path))
            client = RecordingClient()
            agent.client = client
            turn = agent.run(sample_task())

        self.assertEqual(turn.response, "ok")
        self.assertEqual(client.messages[0], {"role": "system", "content": "MODEL_POLICY_MARKER"})
        self.assertEqual(client.messages[1]["content"], "Return the benign marker.")

    def test_cli_config_system_prompt_reaches_stdin_not_command(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            config_path = Path(directory) / "agents.json"
            config_path.write_text(json.dumps({
                "agents": {
                    "candidate": {
                        "backend": "cli",
                        "command": [sys.executable, "-c", "import sys; print(sys.stdin.read())"],
                        "pass_prompt_stdin": True,
                        "system_prompt": "CLI_POLICY_MARKER",
                    }
                }
            }), encoding="utf-8")
            agent = CliAgent.from_config("candidate", str(config_path))
            turn = agent.run(sample_task())

        self.assertIn("Target system policy:\nCLI_POLICY_MARKER", turn.response)
        self.assertIn("Return the benign marker.", turn.response)
        self.assertIn("CLI_POLICY_MARKER", turn.artifacts["prompt_text"])
        self.assertFalse(any("CLI_POLICY_MARKER" in part for part in turn.artifacts["command"]))


if __name__ == "__main__":
    unittest.main()
