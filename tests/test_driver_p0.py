"""通过 driver 调用真实原生评分和进化接收，不调用 Docker 或模型。"""

from __future__ import annotations

from contextlib import redirect_stdout
from io import StringIO
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "harness/src"))

from agent_safety_harness.cli import build_parser, command_capture
from agent_safety_harness.driver import Plan, PlanStep, execute_plan
from agent_safety_harness.engine import digest
from agent_safety_harness.io import read_jsonl, write_json, write_jsonl
from scripts.result_analysis import analyze


class DriverP0SmokeTests(unittest.TestCase):
    def test_native_safe_unsafe_unknown_and_intake(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for agent, unknown, expected_seeds in (
                ("safe_stub", False, 0),
                ("unsafe_stub", False, 2),
                ("safe_stub", True, 0),
            ):
                case = root / (agent + ("-unknown" if unknown else ""))
                case.mkdir()
                capture = case / "capture"
                public = ROOT / "harness/smoke/public_tasks.jsonl"
                private = ROOT / "harness/smoke/private_rules.jsonl"
                args = build_parser().parse_args(
                    [
                        "capture",
                        "--agent",
                        agent,
                        "--input",
                        str(public),
                        "--out",
                        str(capture),
                    ]
                )
                with redirect_stdout(StringIO()):
                    self.assertEqual(command_capture(args), 0)
                if unknown:
                    rows = read_jsonl(capture / "capture_results.jsonl")
                    for row in rows:
                        row["evidence"] = {"error_type": "FixtureTransportError"}
                    write_jsonl(capture / "capture_results.jsonl", rows)
                # 此收据将本地合成捕获绑定到真实 score 接口，
                # 明确不代表 Docker 运行环境的资格证明。
                receipt = case / "capture_receipt.json"
                write_json(
                    receipt,
                    {
                        "benchmark_id": "smoke_local",
                        "contains_private_oracle": False,
                        "capture_run": str(capture),
                        "public_tasks": str(public),
                        "public_tasks_hash": digest(public),
                        "captures_hash": digest(capture / "capture_results.jsonl"),
                        "traces_hash": digest(capture / "traces.jsonl"),
                    },
                )
                plan = Plan(
                    [
                        PlanStep(
                            "score",
                            {
                                "benchmark_id": "smoke_local",
                                "capture_receipt": str(receipt),
                                "private_tasks": str(private),
                                "out": str(case / "score"),
                            },
                            id="score",
                        )
                    ]
                )
                result = execute_plan(
                    plan, dry_run=False, record_dir=case / "score-driver"
                )
                self.assertEqual(
                    result.status, "incomplete" if unknown else "completed"
                )
                analysis = case / "analysis"
                analyze("agent_execution", [case / "score/results.jsonl"], analysis)
                intake_plan = Plan(
                    [
                        PlanStep(
                            "evolve",
                            {
                                "track": "agent_execution",
                                "analysis_manifest": str(analysis / "manifest.json"),
                                "out": str(case / "intake"),
                            },
                            id="intake",
                        )
                    ]
                )
                intake = execute_plan(
                    intake_plan, dry_run=False, record_dir=case / "intake-driver"
                )
                self.assertEqual(intake.status, "completed")
                self.assertEqual(intake.results[0].output["seed_count"], expected_seeds)
                manifest = json.loads((case / "intake/manifest.json").read_text())
                self.assertFalse(manifest["native_evidence_verified"])
                self.assertEqual(manifest["admitted_candidate_count"], 0)


if __name__ == "__main__":
    unittest.main()
