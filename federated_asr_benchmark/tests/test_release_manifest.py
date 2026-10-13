from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from tools.release_manifest import (
    DEFAULT_INCLUDED_FILES,
    DEFAULT_INCLUDED_ROOTS,
    build_manifest,
    export_release,
    verify_manifest,
    write_manifest_atomic,
)


class ReleaseManifestTest(unittest.TestCase):
    def test_default_scope_includes_configs_and_excludes_runtime_state(self) -> None:
        self.assertIn("release", DEFAULT_INCLUDED_ROOTS)
        self.assertIn("release.ps1", DEFAULT_INCLUDED_FILES)
        self.assertIn("requirements-agentpoison.in", DEFAULT_INCLUDED_FILES)
        self.assertIn("requirements-agentpoison.lock", DEFAULT_INCLUDED_FILES)
        self.assertIn("requirements-controller.lock", DEFAULT_INCLUDED_FILES)
        for excluded in (".codex_tmp", ".venvs", "runs", "data", "datasets"):
            self.assertNotIn(excluded, DEFAULT_INCLUDED_ROOTS)

    def test_release_configs_have_no_machine_paths_or_secret_values(self) -> None:
        package_root = Path(__file__).resolve().parents[1]

        def assert_no_secret_fields(value: object) -> None:
            if isinstance(value, dict):
                for key, child in value.items():
                    self.assertNotIn(
                        key.casefold(), {"api_key", "secret_key", "access_token", "token"}
                    )
                    assert_no_secret_fields(child)
            elif isinstance(value, list):
                for child in value:
                    assert_no_secret_fields(child)

        for path in (package_root / "release").glob("*.json"):
            text = path.read_text(encoding="utf-8")
            self.assertNotIn("C:\\Users\\", text)
            self.assertNotIn("sk-", text)
            assert_no_secret_fields(json.loads(text))

    def test_agentpoison_lock_keeps_cuda_runtime_versions(self) -> None:
        package_root = Path(__file__).resolve().parents[1]
        lock = (package_root / "requirements-agentpoison.lock").read_text(encoding="utf-8")
        self.assertIn("torch==2.11.0+cu128", lock)
        self.assertIn("transformers==5.14.1", lock)
        self.assertNotIn("--hash=sha256:", lock)

    def make_release(self, root: Path) -> Path:
        (root / "tools").mkdir(parents=True)
        (root / "tools" / "runner.py").write_text("print('ok')\n", encoding="utf-8")
        (root / "README.md").write_text("release\n", encoding="utf-8")
        manifest = build_manifest(
            root,
            release_id="test-v1",
            included_roots=("tools",),
            included_files=("README.md",),
        )
        path = root / "release-manifest.json"
        write_manifest_atomic(path, manifest)
        return path

    def test_manifest_verifies_after_copy_to_space_and_unicode_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "source"
            manifest_path = self.make_release(source)
            copied = Path(tmp) / "release copy 测试"
            result = export_release(source, manifest_path, copied)
            self.assertEqual(result["status"], "passed")
            self.assertEqual(result["file_count"], 2)
            self.assertEqual(result["release_id"], "test-v1")

    def test_export_refuses_existing_destination(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "source"
            manifest_path = self.make_release(source)
            destination = Path(tmp) / "existing"
            destination.mkdir()
            with self.assertRaisesRegex(RuntimeError, "already exists"):
                export_release(source, manifest_path, destination)

    def test_verification_allows_content_edits(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifest_path = self.make_release(root)
            (root / "tools" / "runner.py").write_text("changed\n", encoding="utf-8")
            self.assertEqual(verify_manifest(root, manifest_path)["status"], "passed")

    def test_verification_fails_on_unexpected_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifest_path = self.make_release(root)
            (root / "tools" / "unexpected.py").write_text("pass\n", encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "unexpected"):
                verify_manifest(root, manifest_path)

    def test_verification_rejects_manifest_path_escape(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifest_path = self.make_release(root)
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["entries"][0]["path"] = "../escape"
            write_manifest_atomic(manifest_path, manifest)
            with self.assertRaisesRegex(RuntimeError, "unsafe"):
                verify_manifest(root, manifest_path)


if __name__ == "__main__":
    unittest.main()
