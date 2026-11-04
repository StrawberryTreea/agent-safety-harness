"""Tests for the dispatch gate: readiness criteria and batch assignment."""
import json
import os
import sys
import tempfile
import unittest
import urllib.error
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import dispatch_tasks  # noqa: E402


class ContractCheckTests(unittest.TestCase):
    def test_shipped_repo_has_contract_files(self):
        passed, detail = dispatch_tasks.check_contract()
        self.assertTrue(passed, detail)

    def test_missing_contract_file_is_detected(self):
        with tempfile.TemporaryDirectory() as tmp:
            passed, detail = dispatch_tasks.check_contract(Path(tmp))
        self.assertFalse(passed)
        self.assertIn("缺少契约文件", detail)


class DocumentationCheckTests(unittest.TestCase):
    def test_document_map_is_consistent(self):
        passed, detail = dispatch_tasks.check_documentation()
        self.assertTrue(passed, detail)


class InterpreterTests(unittest.TestCase):
    """The gate must run the suite with the interpreter that has the deps.

    The acceptance suite imports jsonschema and friends, which live in the
    project's .venv. Using the ambient interpreter reports a false failure on
    any machine whose Python lacks them.
    """

    def test_venv_interpreter_is_preferred(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            venv = root / ".venv" / "Scripts"
            venv.mkdir(parents=True)
            (venv / "python.exe").write_text("", encoding="utf-8")
            self.assertEqual(dispatch_tasks.project_python(root),
                             str(venv / "python.exe"))

    def test_posix_venv_layout_is_found(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            venv = root / ".venv" / "bin"
            venv.mkdir(parents=True)
            (venv / "python").write_text("", encoding="utf-8")
            self.assertEqual(dispatch_tasks.project_python(root),
                             str(venv / "python"))

    def test_falls_back_to_current_interpreter_without_venv(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(dispatch_tasks.project_python(Path(tmp)),
                             sys.executable)


class RosterCheckTests(unittest.TestCase):
    def test_shipped_codeowners_covers_every_path(self):
        passed, detail = dispatch_tasks.check_roster()
        self.assertTrue(passed, detail)


class TaskContentTests(unittest.TestCase):
    def test_task_without_owner_is_rejected(self):
        tasks = [{"number": 1, "owner": "", "body": "x" * 300 + "完成标准"}]
        passed, detail = dispatch_tasks.check_tasks(tasks)
        self.assertFalse(passed)
        self.assertIn("没有 owner 标签", detail)

    def test_task_without_acceptance_criteria_is_rejected(self):
        tasks = [{"number": 2, "owner": "liuke", "body": "x" * 300}]
        passed, detail = dispatch_tasks.check_tasks(tasks)
        self.assertFalse(passed)
        self.assertIn("缺少完成标准或验收命令", detail)

    def test_task_with_too_short_body_is_rejected(self):
        tasks = [{"number": 3, "owner": "liuke", "body": "太短 完成标准"}]
        passed, detail = dispatch_tasks.check_tasks(tasks)
        self.assertFalse(passed)
        self.assertIn("正文过短", detail)

    def test_complete_task_passes(self):
        tasks = [{"number": 4, "owner": "liuke", "body": "x" * 300 + "验收命令"}]
        passed, detail = dispatch_tasks.check_tasks(tasks)
        self.assertTrue(passed, detail)

    def test_no_tasks_is_not_ready(self):
        passed, detail = dispatch_tasks.check_tasks([])
        self.assertFalse(passed)


class ReportTests(unittest.TestCase):
    def test_report_states_verdict(self):
        checks = [
            {"name": "契约文件", "passed": True, "detail": "齐备"},
            {"name": "流水线可运行", "passed": False, "detail": "缺 pipeline.py"},
        ]
        text = dispatch_tasks.render_report(checks)
        self.assertIn("[PASS]", text)
        self.assertIn("[FAIL]", text)
        self.assertIn("尚未就绪，先不要派工", text)

    def test_report_states_ready_verdict(self):
        checks = [{"name": "契约文件", "passed": True, "detail": "齐备"}]
        self.assertIn("仓库已就绪，可以统一派工", dispatch_tasks.render_report(checks))


class DispatchTests(unittest.TestCase):
    def setUp(self):
        import http.server
        import threading

        calls = []

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def _respond(self):
                payload = ""
                length = int(self.headers.get("Content-Length") or 0)
                if length:
                    payload = self.rfile.read(length).decode("utf-8")
                calls.append((self.command, self.path, payload))
                body = json.dumps([
                    {
                        "number": 3,
                        "title": "P0 task",
                        "body": "x" * 300 + "完成标准",
                        "labels": [{"name": "P0"}, {"name": "owner:liuke"}],
                        "assignees": [],
                    },
                    {
                        # The GitHub labels parameter is an OR, so a helper issue
                        # that matches none of the priority labels still comes back.
                        "number": 15,
                        "title": "在岗与代理看板（自动维护）",
                        "body": "x" * 300,
                        "labels": [{"name": "type:chore"}, {"name": "track:infra"}],
                        "assignees": [],
                    },
                ]).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            do_GET = _respond
            do_POST = _respond
            do_PATCH = _respond

        self.server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.calls = calls
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"
        self.previous_base = os.environ.get("DISPATCH_API_BASE")
        os.environ["DISPATCH_API_BASE"] = self.base

    def tearDown(self):
        if self.previous_base is None:
            os.environ.pop("DISPATCH_API_BASE", None)
        else:
            os.environ["DISPATCH_API_BASE"] = self.previous_base
        self.server.shutdown()
        self.server.server_close()

    def test_task_issues_extracts_owner_from_label(self):
        tasks = dispatch_tasks.task_issues("owner/repo", None)
        self.assertEqual(len(tasks), 1)
        self.assertEqual(tasks[0]["owner"], "liuke")
        self.assertEqual(tasks[0]["assignees"], [])

    def test_issue_without_priority_label_is_not_a_task(self):
        """The labels query parameter is an OR, so helper issues come back too."""
        tasks = dispatch_tasks.task_issues("owner/repo", None)
        self.assertTrue(all("P0" in task["labels"] for task in tasks))

    def test_dry_run_writes_nothing(self):
        roster = {"members": [{"id": "liuke", "github": "deep-fry-xiaopengyou"}]}
        tasks = [{"number": 3, "owner": "liuke", "title": "P0 task", "assignees": []}]
        results = dispatch_tasks.dispatch("owner/repo", tasks, roster, None, dry_run=True)
        self.assertEqual(results[0]["status"], "would-assign")
        self.assertEqual(results[0]["account"], "deep-fry-xiaopengyou")
        self.assertEqual(self.calls, [])

    def test_dispatch_assigns_once_per_issue(self):
        roster = {"members": [{"id": "liuke", "github": "deep-fry-xiaopengyou"}]}
        tasks = [{"number": 3, "owner": "liuke", "title": "P0 task", "assignees": []}]
        results = dispatch_tasks.dispatch("owner/repo", tasks, roster, None)
        self.assertEqual(results[0]["status"], "assigned")
        patches = [call for call in self.calls if call[0] == "PATCH"]
        self.assertEqual(len(patches), 1)
        self.assertIn("/issues/3", patches[0][1])
        self.assertIn("deep-fry-xiaopengyou", patches[0][2])

    def test_already_assigned_is_not_rewritten(self):
        roster = {"members": [{"id": "liuke", "github": "deep-fry-xiaopengyou"}]}
        tasks = [{"number": 3, "owner": "liuke", "title": "P0 task",
                  "assignees": ["deep-fry-xiaopengyou"]}]
        results = dispatch_tasks.dispatch("owner/repo", tasks, roster, None)
        self.assertEqual(results[0]["status"], "already")
        self.assertEqual(self.calls, [])

    def test_unknown_owner_is_skipped(self):
        roster = {"members": [{"id": "liuke", "github": "deep-fry-xiaopengyou"}]}
        tasks = [{"number": 3, "owner": "ghost", "title": "P0 task", "assignees": []}]
        results = dispatch_tasks.dispatch("owner/repo", tasks, roster, None)
        self.assertEqual(results[0]["status"], "skipped")
        self.assertEqual(self.calls, [])


if __name__ == "__main__":
    unittest.main()
