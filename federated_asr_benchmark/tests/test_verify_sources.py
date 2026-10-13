from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from tools.verify_sources import verify_sources


class VerifySourcesTest(unittest.TestCase):
    def make_sources(self, root: Path) -> Path:
        source_file = root / "dataset" / "data.json"
        source_file.parent.mkdir(parents=True)
        source_file.write_text("source\n", encoding="utf-8")
        tree_root = root / "runtime"
        (tree_root / "suite").mkdir(parents=True)
        tree_file = tree_root / "suite" / "case.json"
        tree_file.write_text("case\n", encoding="utf-8")
        manifest = {
            "sources": {
                "file": {
                    "path": "dataset/data.json",
                },
                "tree": {
                    "path": "runtime",
                    "glob": "*/*.json",
                },
            }
        }
        manifest_path = root / "manifest.json"
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        return manifest_path

    def test_verifies_file_and_tree_locks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = verify_sources(root, self.make_sources(root))
            self.assertEqual(result["status"], "passed")
            self.assertEqual(result["source_count"], 2)

    def test_allows_source_content_edits(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifest = self.make_sources(root)
            (root / "dataset" / "data.json").write_text("changed\n", encoding="utf-8")
            self.assertEqual(verify_sources(root, manifest)["status"], "passed")

    def test_rejects_path_escape(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifest = self.make_sources(root)
            value = json.loads(manifest.read_text(encoding="utf-8"))
            value["sources"]["file"]["path"] = "../outside"
            manifest.write_text(json.dumps(value), encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "unsafe source path"):
                verify_sources(root, manifest)


if __name__ == "__main__":
    unittest.main()
