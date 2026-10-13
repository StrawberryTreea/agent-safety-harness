from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = Path(os.environ.get("AGENT_EVAL_CURATED_TASKS_ROOT") or ROOT / "runs" / "curated" / "harness_tasks")
SOURCE_ALL = SOURCE_ROOT / "all_computer_agent_safety.tasks.jsonl"


def read_jsonl(path: Path) -> list[dict]:
    with path.open("r", encoding="utf-8-sig") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def is_judge(record: dict) -> bool:
    return record.get("profile") == "trajectory_judge" or record.get("benchmark") == "r_judge"


class ReleasePackagingTests(unittest.TestCase):
    @unittest.skipUnless(SOURCE_ALL.is_file(), "curated source dataset is not available; set AGENT_EVAL_CURATED_TASKS_ROOT")
    def test_validation_and_full_release_contract(self) -> None:
        source = read_jsonl(SOURCE_ALL)
        self.assertGreaterEqual(len(source), 50, "packaging validation requires at least 50 curated tasks")
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
            self.assertEqual(full, source)
            self.assertEqual(len({record["task_id"] for record in validation}), 50)
            source_ids = {record["task_id"] for record in source}
            self.assertEqual(len(source_ids), len(full))
            self.assertTrue({record["task_id"] for record in validation}.issubset(source_ids))
            for records, manifest in ((validation, validation_manifest), (full, full_manifest)):
                judge_count = sum(is_judge(record) for record in records)
                self.assertEqual(manifest["execution_records"], len(records) - judge_count)
                self.assertEqual(manifest["judge_records"], judge_count)
                self.assertEqual(set(manifest["by_benchmark"]), {record["benchmark"] for record in records})
            self.assertTrue(validation_manifest["validation"]["valid"])
            self.assertTrue(full_manifest["validation"]["valid"])
            if any(is_judge(record) for record in source):
                self.assertTrue(any(is_judge(record) for record in validation))
            if any(not is_judge(record) for record in source):
                self.assertTrue(any(not is_judge(record) for record in validation))
            self.assertEqual(Path(validation_manifest["source"]).name, SOURCE_ALL.name)
            self.assertEqual(Path(full_manifest["source"]).name, SOURCE_ALL.name)


if __name__ == "__main__":
    unittest.main()
