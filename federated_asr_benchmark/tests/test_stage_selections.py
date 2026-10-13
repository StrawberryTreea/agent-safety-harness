from __future__ import annotations

import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from tools.build_stage_selections import STRATIFICATION_FIELDS, build_selections, read_jsonl


PACKAGE_ROOT = Path(__file__).resolve().parents[1]


def tree_hash(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        digest.update(str(path.relative_to(root)).encode("utf-8"))
        digest.update(path.read_bytes())
    return digest.hexdigest()


class StageSelectionsTest(unittest.TestCase):
    def test_generated_selections_are_complete_disjoint_and_known(self) -> None:
        index_rows = read_jsonl(PACKAGE_ROOT / "case_index.jsonl")
        known = {row["case_id"] for row in index_rows}
        output_root = PACKAGE_ROOT / "selections"
        manifest = json.loads((output_root / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(
            manifest["source_index_sha256"],
            hashlib.sha256((PACKAGE_ROOT / "case_index.jsonl").read_bytes()).hexdigest(),
        )

        self.assertEqual(set(manifest["benchmarks"]), set(STRATIFICATION_FIELDS))
        for benchmark_id in STRATIFICATION_FIELDS:
            smoke = read_jsonl(output_root / "smoke20" / f"{benchmark_id}.jsonl")
            pilot = read_jsonl(output_root / "pilot100" / f"{benchmark_id}.jsonl")
            smoke_ids = {row["case_id"] for row in smoke}
            pilot_ids = {row["case_id"] for row in pilot}
            self.assertEqual(len(smoke_ids), 20)
            self.assertEqual(len(pilot_ids), 100)
            self.assertFalse(smoke_ids & pilot_ids)
            self.assertTrue((smoke_ids | pilot_ids) <= known)
            self.assertTrue(all(row["benchmark_id"] == benchmark_id for row in smoke + pilot))
        self.assertEqual(len(read_jsonl(output_root / "smoke20" / "all.jsonl")), 120)
        self.assertEqual(len(read_jsonl(output_root / "pilot100" / "all.jsonl")), 600)

    def test_generation_is_byte_deterministic(self) -> None:
        with tempfile.TemporaryDirectory() as first, tempfile.TemporaryDirectory() as second:
            build_selections(PACKAGE_ROOT / "case_index.jsonl", Path(first), "test-seed")
            build_selections(PACKAGE_ROOT / "case_index.jsonl", Path(second), "test-seed")
            self.assertEqual(tree_hash(Path(first)), tree_hash(Path(second)))

    def test_source_locator_survives_different_windows_drives(self) -> None:
        index = PACKAGE_ROOT / "case_index.jsonl"
        with tempfile.TemporaryDirectory() as output:
            with patch("tools.build_stage_selections.os.path.relpath", side_effect=ValueError("different drives")):
                manifest = build_selections(index, Path(output), "test-seed")
            self.assertEqual(Path(manifest["source_index"]), index.resolve())
            self.assertEqual(manifest["source_index_sha256"], hashlib.sha256(index.read_bytes()).hexdigest())
            self.assertEqual(len(read_jsonl(Path(output) / "smoke20" / "all.jsonl")), 120)
            self.assertEqual(len(read_jsonl(Path(output) / "pilot100" / "all.jsonl")), 600)


if __name__ == "__main__":
    unittest.main()
