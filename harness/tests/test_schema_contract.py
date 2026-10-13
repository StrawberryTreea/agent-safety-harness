from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from agent_safety_harness.io import read_jsonl


class SchemaContractTests(unittest.TestCase):
    def run_agentdojo(self, out_dir: Path) -> None:
        env = os.environ.copy()
        env["PYTHONPATH"] = str(ROOT / "src")
        subprocess.run(
            [
                sys.executable,
                "-m",
                "agent_safety_harness",
                "run",
                "--benchmark",
                "agentdojo",
                "--agent",
                "unsafe_stub",
                "--sample",
                "3",
                "--out",
                str(out_dir),
            ],
            cwd=ROOT,
            env=env,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

    def test_trace_and_result_contract(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "agentdojo_unsafe"
            self.run_agentdojo(run_dir)
            traces = read_jsonl(run_dir / "traces.jsonl")
            results = read_jsonl(run_dir / "results.jsonl")
            event_ids = {event["event_id"] for event in traces}
            self.assertEqual(len(event_ids), len(traces))

            for event in traces:
                for key in ["event_id", "task_id", "step", "role", "event_type", "content", "timestamp"]:
                    self.assertIn(key, event)

            for result in results:
                for key in [
                    "task_id", "benchmark", "agent_id", "task_type", "safety_pass", "reason",
                    "safety_violation", "policy_verdict", "score", "evidence", "cost",
                ]:
                    self.assertIn(key, result)
                for event_id in result["evidence"]["trace_event_ids"]:
                    self.assertIn(event_id, event_ids)

            violation_count = sum(1 for result in results if result["safety_violation"])
            self.assertEqual(violation_count, 2)


if __name__ == "__main__":
    unittest.main()
