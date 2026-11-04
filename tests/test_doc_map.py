import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts import check_doc_map  # noqa: E402


class DocumentMapTests(unittest.TestCase):
    def test_real_map_passes(self):
        result = check_doc_map.check(check_doc_map.MAP)
        self.assertEqual(result["status"], "pass", result)
        self.assertEqual(result["missing_files"], [])
        self.assertEqual(result["unlisted"], [])

    def test_every_tracked_markdown_is_listed(self):
        tracked = check_doc_map.tracked_markdown()
        listed = check_doc_map.listed_paths(check_doc_map.MAP.read_text(encoding="utf-8"))
        missing = sorted(tracked - listed - check_doc_map.EXEMPT)
        self.assertEqual(missing, [], f"tracked but not listed: {missing}")

    def test_listed_paths_all_exist(self):
        listed = check_doc_map.listed_paths(check_doc_map.MAP.read_text(encoding="utf-8"))
        broken = sorted(p for p in listed if not (ROOT / p).exists())
        self.assertEqual(broken, [], f"listed but missing on disk: {broken}")

    def test_map_covers_every_layer(self):
        """Each documented layer must actually appear in the map."""
        text = check_doc_map.MAP.read_text(encoding="utf-8")
        for layer in ("L0 入口层", "L1 契约层", "L2 导航层", "L3 操作层", "L4 记录层"):
            self.assertIn(layer, text, f"layer missing from map: {layer}")

    def test_map_documents_all_five_roles(self):
        text = check_doc_map.MAP.read_text(encoding="utf-8")
        for role in ("王浩", "刘轲", "辰浩", "一迪", "伦锋"):
            self.assertIn(role, text, f"role missing from map: {role}")

    def test_map_states_the_tmp_grep_trap(self):
        """The .tmp/ stale-copy trap is the single biggest hazard for coding tools."""
        text = check_doc_map.MAP.read_text(encoding="utf-8")
        self.assertIn(".tmp/", text)
        self.assertIn("git ls-files", text)

    def test_missing_file_is_detected(self):
        with tempfile.TemporaryDirectory() as tmp:
            fake_map = Path(tmp) / "MAP.md"
            fake_map.write_text(
                "# fake\n\n`docs/definitely_not_here.md`\n`README.md`\n", encoding="utf-8"
            )
            result = check_doc_map.check(fake_map)
            self.assertEqual(result["status"], "fail")
            self.assertIn("docs/definitely_not_here.md", result["missing_files"])

    def test_unlisted_document_is_detected(self):
        with tempfile.TemporaryDirectory() as tmp:
            fake_map = Path(tmp) / "MAP.md"
            fake_map.write_text("# fake\n\n`README.md`\n", encoding="utf-8")
            result = check_doc_map.check(fake_map)
            self.assertEqual(result["status"], "fail")
            self.assertTrue(result["unlisted"])

    def test_prose_paths_are_not_treated_as_files(self):
        """Wildcards and directories are prose, not file references."""
        text = "`protocol/*.json` `harness/src/` `docs/integration/*`\n"
        found = check_doc_map.listed_paths(text)
        self.assertNotIn("protocol/*.json", found)
        self.assertNotIn("harness/src/", found)

    def test_cli_exit_codes(self):
        ok = subprocess.run(
            [sys.executable, "scripts/check_doc_map.py"], cwd=ROOT, capture_output=True, text=True
        )
        self.assertEqual(ok.returncode, 0, ok.stdout + ok.stderr)
        self.assertIn("PASS", ok.stdout)

    def test_cli_reports_failure_exit_code(self):
        with tempfile.TemporaryDirectory() as tmp:
            fake_map = Path(tmp) / "MAP.md"
            fake_map.write_text("# fake\n", encoding="utf-8")
            bad = subprocess.run(
                [sys.executable, "scripts/check_doc_map.py", "--map", str(fake_map)],
                cwd=ROOT,
                capture_output=True,
                text=True,
            )
            self.assertEqual(bad.returncode, 1)

    def test_json_output_is_written(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "nested" / "result.json"
            subprocess.run(
                [sys.executable, "scripts/check_doc_map.py", "--json", str(out)],
                cwd=ROOT,
                capture_output=True,
                text=True,
            )
            self.assertTrue(out.exists())
            data = json.loads(out.read_text(encoding="utf-8"))
            self.assertEqual(data["status"], "pass")


if __name__ == "__main__":
    unittest.main()
