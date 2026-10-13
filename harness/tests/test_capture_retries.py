from __future__ import annotations

import subprocess
import sys
import unittest
from unittest.mock import patch
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from agent_safety_harness.cli import _capture_execution_succeeded
from agent_safety_harness.runners import AgentExecutionRunner, iter_agent_task_outputs
from agent_safety_harness.schema import ResultSpec, TaskSpec


class CaptureRetryTests(unittest.TestCase):
    def test_cli_capture_success_requires_transport_and_environment(self) -> None:
        valid = {
            "agent_id": "cli:codex",
            "evidence": {
                "artifacts": {"returncode": 0},
                "benchmark_tools_available": True,
                "benchmark_environment_validated": True,
                "error_type": "EvaluationRuleUnavailable",
            },
        }
        self.assertTrue(_capture_execution_succeeded(valid))
        timed_out = {
            "agent_id": "cli:codex",
            "evidence": {"error_type": "TimeoutExpired", "reason": "task execution failed"},
        }
        self.assertFalse(_capture_execution_succeeded(timed_out))

    def test_cli_timeout_is_retried_and_attempts_are_recorded(self) -> None:
        task = TaskSpec(
            task_id="retry:1",
            benchmark="retry",
            profile="retry",
            user_goal="Use the benchmark tool.",
            tools=[{"name": "status.lookup", "risk": "low"}],
            labels={"expected_behavior": "resist_attack", "task_type": "attack_test"},
        )
        successful = ResultSpec(
            task_id=task.task_id,
            benchmark=task.benchmark,
            agent_id="cli:codex",
            task_type="attack_test",
            safety_pass=None,
            reason="unknown",
            task_success=None,
            safety_violation=None,
            attack_success=None,
            unsafe_tool_call=None,
            data_leak=None,
            refusal=None,
            policy_verdict="unknown",
            score=0.0,
            evidence={
                "artifacts": {"returncode": 0},
                "benchmark_tools_available": True,
                "benchmark_environment_validated": True,
                "error_type": "EvaluationRuleUnavailable",
            },
        )

        class Agent:
            agent_id = "cli:codex"

        timeout = subprocess.TimeoutExpired(["codex"], 300)
        with patch.object(AgentExecutionRunner, "run_task", side_effect=[timeout, ([], successful)]) as run_task:
            outputs = list(iter_agent_task_outputs([task], Agent(), max_attempts=2))

        self.assertEqual(run_task.call_count, 2)
        result = outputs[0][1]
        self.assertEqual(result["evidence"]["execution_attempt_count"], 2)
        self.assertEqual(result["evidence"]["execution_attempts"][0]["error_type"], "TimeoutExpired")
        self.assertTrue(result["evidence"]["execution_attempts"][1]["completed"])


if __name__ == "__main__":
    unittest.main()
