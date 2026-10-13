from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from agent_safety_harness.agents import CliAgent, _isolate_env_directories


class CliEnvironmentIsolationTests(unittest.TestCase):
    def test_default_child_environment_does_not_inherit_unlisted_secret(self) -> None:
        agent = CliAgent(agent_id="test", command=[sys.executable, "--version"])
        with patch.dict(os.environ, {"UNRELATED_PRODUCTION_API_KEY": "do-not-leak"}, clear=False):
            child_env = agent._build_child_env()

        self.assertNotIn("UNRELATED_PRODUCTION_API_KEY", child_env)
        self.assertFalse(agent.inherit_env)

    def test_allowlisted_and_explicit_values_are_passed(self) -> None:
        agent = CliAgent(
            agent_id="test",
            command=[sys.executable, "--version"],
            env={"CLI_CONFIG_DIR": "isolated-state"},
            env_allowlist=["EVALUATOR_API_KEY"],
        )
        with patch.dict(os.environ, {"EVALUATOR_API_KEY": "temporary-token"}, clear=True):
            child_env = agent._build_child_env()

        self.assertEqual(
            child_env,
            {
                "EVALUATOR_API_KEY": "temporary-token",
                "CLI_CONFIG_DIR": "isolated-state",
            },
        )

    def test_explicit_empty_allowlist_inherits_nothing(self) -> None:
        agent = CliAgent(agent_id="test", command=[sys.executable, "--version"], env_allowlist=[])
        with patch.dict(os.environ, {"PATH": "production-path"}, clear=True):
            self.assertEqual(agent._build_child_env(), {})

    def test_config_rejects_invalid_environment_controls(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "agents.json"
            config_path.write_text(
                json.dumps(
                    {
                        "agents": {
                            "bad": {
                                "backend": "cli",
                                "command": [sys.executable, "--version"],
                                "env_allowlist": "API_KEY",
                            }
                        }
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "env_allowlist"):
                CliAgent.from_config("bad", str(config_path))

    def test_cli_state_directory_is_copied_per_task(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "shared_state"
            source.mkdir()
            (source / "config.toml").write_text("provider = 'evaluation'\n", encoding="utf-8")
            env = {"CODEX_HOME": str(source), "HOME": str(source)}

            isolated = _isolate_env_directories(
                env,
                names=["CODEX_HOME", "HOME"],
                workspace_path=root / "task_workspace",
            )

            self.assertEqual(env["CODEX_HOME"], env["HOME"])
            self.assertNotEqual(Path(env["CODEX_HOME"]), source)
            self.assertEqual(Path(env["CODEX_HOME"], "config.toml").read_text(encoding="utf-8"), "provider = 'evaluation'\n")
            Path(env["CODEX_HOME"], "task-write.txt").write_text("private", encoding="utf-8")
            self.assertFalse((source / "task-write.txt").exists())
            self.assertEqual(set(isolated), {"CODEX_HOME", "HOME"})


if __name__ == "__main__":
    unittest.main()
