from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = ROOT / "runs" / "curated_benchmark_v3" / "harness_tasks"


def read_jsonl(path: Path) -> list[dict]:
    with path.open("r", encoding="utf-8-sig") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def is_judge(record: dict) -> bool:
    return record.get("profile") == "trajectory_judge" or record.get("benchmark") == "r_judge"


class ReleasePackagingTests(unittest.TestCase):
    @unittest.skipUnless(SOURCE_ROOT.exists(), "curated source dataset is not available")
    def test_validation_and_full_release_contract(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out_root = Path(tmp) / "datasets"
            subprocess.run(
                [
                    sys.executable,
                    "tools/package_release_datasets.py",
                    "--source-root",
                    str(SOURCE_ROOT),
                    "--out-root",
                    str(out_root),
                    "--validation-size",
                    "50",
                    "--seed",
                    "42",
                ],
                cwd=ROOT,
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )

            validation = read_jsonl(out_root / "validation_50" / "all_computer_agent_safety.tasks.jsonl")
            full = read_jsonl(out_root / "full" / "all_computer_agent_safety.tasks.jsonl")
            validation_manifest = json.loads((out_root / "validation_50" / "manifest.json").read_text(encoding="utf-8"))
            full_manifest = json.loads((out_root / "full" / "manifest.json").read_text(encoding="utf-8"))

            self.assertEqual(len(validation), 50)
            self.assertEqual(len(full), 18657)
            self.assertEqual(len({record["task_id"] for record in validation}), 50)
            self.assertEqual(len({record["task_id"] for record in full}), 18657)
            self.assertEqual(validation_manifest["execution_records"], 47)
            self.assertEqual(validation_manifest["judge_records"], 3)
            self.assertEqual(full_manifest["execution_records"], 18136)
            self.assertEqual(full_manifest["judge_records"], 521)
            self.assertEqual(len(validation_manifest["by_benchmark"]), 17)
            self.assertEqual(len(full_manifest["by_benchmark"]), 17)
            self.assertTrue(validation_manifest["validation"]["valid"])
            self.assertTrue(full_manifest["validation"]["valid"])
            self.assertTrue(any(is_judge(record) for record in validation))
            self.assertTrue(any(not is_judge(record) for record in validation))
            self.assertFalse(Path(validation_manifest["source"]).is_absolute())
            self.assertFalse(Path(full_manifest["source"]).is_absolute())


if __name__ == "__main__":
    unittest.main()
