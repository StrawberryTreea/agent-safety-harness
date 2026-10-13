from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from tools.run_product import DEFAULT_AGENTHARM_DATA_ROOT, DEFAULT_AGENTHARM_RUNTIME_ROOT


PACKAGE_ROOT = Path(__file__).resolve().parents[1]
RUN_ONE = PACKAGE_ROOT / "tools" / "run_product.py"
RUN_BATCH = PACKAGE_ROOT / "tools" / "run_product_batch.py"


def request(case_index: Path, output_root: Path) -> dict:
    return {
        "run_id": "agentharm-runner-test",
        "benchmark_id": "agentharm",
        "product": {
            "product_id": "codex",
            "executable": "codex",
            "version": "0.122.0",
            "product_model": "deepseek-v4-flash",
            "state_source": None,
        },
        "adapter": {
            "adapter_id": "agentharm-product-adapter",
            "adapter_version": "0.1.0",
            "mcp_config": None,
        },
        "isolation": {
            "mode": "disposable_workspace",
            "fresh_product_state": True,
            "network_policy": "benchmark_defined",
            "tool_permission_mode": "autonomous_preapproved_in_sandbox",
        },
        "selection": {"case_index": str(case_index)},
        "output_dir": str(output_root),
        "timeout_seconds": 30,
    }


class AgentHarmRunnerTest(unittest.TestCase):
    def setUp(self) -> None:
        if not DEFAULT_AGENTHARM_DATA_ROOT.exists():
            self.skipTest("pinned AgentHarm source is unavailable")

    def _fixture(self, root: Path, case_ids: list[str]) -> tuple[Path, Path]:
        index = root / "index.jsonl"
        index.write_text(
            "".join(
                json.dumps(
                    {
                        "benchmark_id": "agentharm",
                        "case_id": case_id,
                        "native_case_id": case_id.removeprefix("agentharm:"),
                    }
                )
                + "\n"
                for case_id in case_ids
            ),
            encoding="utf-8",
        )
        request_path = root / "request.json"
        request_path.write_text(
            json.dumps(request(index, root / "output")), encoding="utf-8"
        )
        return request_path, index

    def test_single_case_dry_run_wires_agentharm_without_judge(self) -> None:
        with tempfile.TemporaryDirectory(dir=PACKAGE_ROOT / ".codex_tmp") as tmp:
            root = Path(tmp)
            case_id = "agentharm:test_public:17-1"
            request_path, index = self._fixture(root, [case_id])
            env = os.environ.copy()
            env.pop("DEEPSEEK_API_KEY", None)
            completed = subprocess.run(
                [
                    sys.executable,
                    str(RUN_ONE),
                    "--request",
                    str(request_path),
                    "--case-index",
                    str(index),
                    "--case-id",
                    case_id,
                    "--agentharm-data-root",
                    str(DEFAULT_AGENTHARM_DATA_ROOT),
                    "--agentharm-runtime-root",
                    str(DEFAULT_AGENTHARM_RUNTIME_ROOT),
                    "--dry-run",
                ],
                cwd=PACKAGE_ROOT,
                env=env,
                text=True,
                encoding="utf-8",
                errors="replace",
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=60,
                check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            artifact = json.loads(
                (
                    root
                    / "output"
                    / case_id.replace(":", "__")
                    / "product_run.json"
                ).read_text(encoding="utf-8")
            )
            self.assertEqual(artifact["benchmark_id"], "agentharm")
            self.assertEqual(artifact["status"], "dry_run")
            self.assertIn("mcp_servers.agentharm", " ".join(artifact["command"]))

    def test_multi_case_paid_batch_stops_before_execution_without_judge(self) -> None:
        with tempfile.TemporaryDirectory(dir=PACKAGE_ROOT / ".codex_tmp") as tmp:
            root = Path(tmp)
            cases = ["agentharm:test_public:17-1", "agentharm:test_public:22-2"]
            request_path, index = self._fixture(root, cases)
            env = os.environ.copy()
            env.pop("DEEPSEEK_API_KEY", None)
            completed = subprocess.run(
                [
                    sys.executable,
                    str(RUN_BATCH),
                    "--request",
                    str(request_path),
                    "--case-index",
                    str(index),
                    "--agentharm-data-root",
                    str(DEFAULT_AGENTHARM_DATA_ROOT),
                    "--agentharm-runtime-root",
                    str(DEFAULT_AGENTHARM_RUNTIME_ROOT),
                ],
                cwd=PACKAGE_ROOT,
                env=env,
                text=True,
                encoding="utf-8",
                errors="replace",
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=30,
                check=False,
            )
            self.assertEqual(completed.returncode, 2)
            self.assertIn("multi-case AgentHarm execution blocked", completed.stderr)
            self.assertFalse((root / "output").exists())


if __name__ == "__main__":
    unittest.main()
