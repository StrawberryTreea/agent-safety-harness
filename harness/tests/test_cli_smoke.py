from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class CliSmokeTests(unittest.TestCase):
    def run_cli(self, *args: str) -> subprocess.CompletedProcess[str]:
        env = os.environ.copy()
        env["PYTHONPATH"] = str(ROOT / "src")
        return subprocess.run(
            [sys.executable, "-m", "agent_safety_harness", *args],
            cwd=ROOT,
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=True,
        )

    def assert_run_files(self, run_dir: Path) -> None:
        for name in ["manifest.json", "traces.jsonl", "results.jsonl", "summary.csv", "report.md", "summary.json"]:
            self.assertTrue((run_dir / name).exists(), name)

    def test_run_judge_and_report(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            agentharm_dir = tmp_path / "agentharm_safe"
            rjudge_dir = tmp_path / "rjudge_rule"

            self.run_cli(
                "run",
                "--benchmark",
                "agentharm",
                "--agent",
                "safe_stub",
                "--sample",
                "3",
                "--workers",
                "2",
                "--out",
                str(agentharm_dir),
            )
            self.run_cli("judge", "--benchmark", "r_judge", "--sample", "3", "--workers", "2", "--out", str(rjudge_dir))
            self.run_cli("report", "--run", str(agentharm_dir))

            self.assert_run_files(agentharm_dir)
            self.assert_run_files(rjudge_dir)
            manifest = json.loads((agentharm_dir / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["workers"], 2)
            summary = json.loads((agentharm_dir / "summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["total"], 3)
            self.assertEqual(summary["safety_pass_rate"], 1.0)
            self.assertEqual(summary["unknown_rate"], 0.0)


if __name__ == "__main__":
    unittest.main()
