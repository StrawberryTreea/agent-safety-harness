"""Tests for the on-call roster decision logic (no network calls)."""
import json
import sys
import tempfile
import unittest
import urllib.error
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import oncall_roster  # noqa: E402


class RosterLoadingTests(unittest.TestCase):
    def test_shipped_roster_is_consistent(self):
        roster = oncall_roster.load_roster(ROOT / "docs" / "roster.json")
        self.assertEqual(roster["warn_days"], 14)
        self.assertEqual(roster["escalate_days"], 30)
        self.assertEqual(len(roster["members"]), 5)
        self.assertEqual(roster["arbiter"], "wanghao")

    def test_every_member_has_github_and_backup(self):
        roster = oncall_roster.load_roster(ROOT / "docs" / "roster.json")
        ids = {str(member["id"]) for member in roster["members"]}
        for member in roster["members"]:
            self.assertTrue(str(member.get("github", "")).strip(), f"{member['id']} missing github account")
            backup = member.get("backup")
            self.assertIn(backup, ids, f"{member['id']} backup must point to a known member")
            self.assertNotEqual(backup, member["id"], f"{member['id']} cannot be its own backup")

    def test_duplicate_ids_are_rejected(self):
        with self.assertRaises(ValueError):
            oncall_roster.load_roster_from_data({"members": [{"id": "a"}, {"id": "a"}]})


class ClassificationTests(unittest.TestCase):
    def test_thresholds_map_to_status(self):
        self.assertEqual(oncall_roster.classify(0, 14, 30), oncall_roster.STATUS_ACTIVE)
        self.assertEqual(oncall_roster.classify(13, 14, 30), oncall_roster.STATUS_ACTIVE)
        self.assertEqual(oncall_roster.classify(14, 14, 30), oncall_roster.STATUS_WARN)
        self.assertEqual(oncall_roster.classify(29, 14, 30), oncall_roster.STATUS_WARN)
        self.assertEqual(oncall_roster.classify(30, 14, 30), oncall_roster.STATUS_ESCALATE)
        self.assertEqual(oncall_roster.classify(400, 14, 30), oncall_roster.STATUS_ESCALATE)

    def test_no_activity_means_escalate(self):
        self.assertEqual(oncall_roster.classify(None, 14, 30), oncall_roster.STATUS_ESCALATE)

    def test_evaluate_reports_inactive_days(self):
        roster = {
            "warn_days": 14, "escalate_days": 30, "arbiter": "wanghao",
            "members": [{"id": "liuke", "name": "刘轲", "github": "deep-fry-xiaopengyou",
                         "role": "engine", "owns": [], "backup": "lunfeng"}],
        }
        now = datetime(2026, 11, 4, tzinfo=timezone.utc)
        rows = oncall_roster.evaluate(roster, {"liuke": now - timedelta(days=20)}, now)
        self.assertEqual(rows[0]["status"], oncall_roster.STATUS_WARN)
        self.assertEqual(rows[0]["inactive_days"], 20)

    def test_future_activity_is_clamped_to_zero_days(self):
        roster = {"warn_days": 14, "escalate_days": 30, "arbiter": "wanghao",
                  "members": [{"id": "liuke", "name": "刘轲", "github": "x", "role": "engine",
                               "owns": [], "backup": "lunfeng"}]}
        now = datetime(2026, 11, 4, tzinfo=timezone.utc)
        rows = oncall_roster.evaluate(roster, {"liuke": now + timedelta(days=5)}, now)
        self.assertEqual(rows[0]["inactive_days"], 0)
        self.assertEqual(rows[0]["status"], oncall_roster.STATUS_ACTIVE)


class HandoverTests(unittest.TestCase):
    def setUp(self):
        self.roster = {
            "warn_days": 14, "escalate_days": 30, "arbiter": "wanghao",
            "members": [
                {"id": "wanghao", "name": "王浩", "github": "StrawberryTreea", "role": "arbiter",
                 "owns": [], "backup": "yidi"},
                {"id": "liuke", "name": "刘轲", "github": "deep-fry-xiaopengyou", "role": "engine",
                 "owns": [], "backup": "lunfeng"},
                {"id": "lunfeng", "name": "伦锋", "github": "DylanWu6", "role": "evolution",
                 "owns": [], "backup": "yidi"},
                {"id": "yidi", "name": "一迪", "github": "321ddi", "role": "analysis",
                 "owns": [], "backup": "liuke"},
            ],
        }

    def test_escalated_member_produces_reassignment_action(self):
        rows = [
            {"id": "liuke", "name": "刘轲", "github": "deep-fry-xiaopengyou", "status": oncall_roster.STATUS_ESCALATE,
             "inactive_days": 45, "backup": "lunfeng", "owns": []},
        ]
        actions = oncall_roster.handover_actions(rows, self.roster)
        self.assertEqual(len(actions), 1)
        self.assertTrue(actions[0]["automatic"])
        self.assertIn("DylanWu6", actions[0]["detail"])

    def test_arbiter_escalation_is_manual_and_narrower(self):
        rows = [
            {"id": "wanghao", "name": "王浩", "github": "StrawberryTreea", "status": oncall_roster.STATUS_ESCALATE,
             "inactive_days": 45, "backup": "yidi", "owns": []},
        ]
        actions = oncall_roster.handover_actions(rows, self.roster)
        self.assertEqual(len(actions), 1)
        self.assertFalse(actions[0]["automatic"])
        self.assertIn("口径裁决", actions[0]["detail"])

    def test_active_member_produces_no_action(self):
        rows = [
            {"id": "liuke", "name": "刘轲", "github": "deep-fry-xiaopengyou", "status": oncall_roster.STATUS_ACTIVE,
             "inactive_days": 1, "backup": "lunfeng", "owns": []},
        ]
        self.assertEqual(oncall_roster.handover_actions(rows, self.roster), [])

    def test_missing_backup_is_reported_as_manual(self):
        roster = dict(self.roster)
        roster["members"] = [dict(roster["members"][1], backup=None)]
        rows = [
            {"id": "liuke", "name": "刘轲", "github": "deep-fry-xiaopengyou", "status": oncall_roster.STATUS_ESCALATE,
             "inactive_days": 45, "backup": None, "owns": []},
        ]
        actions = oncall_roster.handover_actions(rows, roster)
        self.assertEqual(len(actions), 1)
        self.assertFalse(actions[0]["automatic"])


class TimestampTests(unittest.TestCase):
    def test_parse_timestamp_handles_github_format(self):
        stamp = oncall_roster._parse_timestamp("2026-11-04T07:18:10Z")
        self.assertIsNotNone(stamp)
        self.assertEqual(stamp.year, 2026)

    def test_parse_timestamp_rejects_garbage(self):
        self.assertIsNone(oncall_roster._parse_timestamp("not-a-date"))
        self.assertIsNone(oncall_roster._parse_timestamp(None))

    def test_latest_timestamp_picks_the_newest(self):
        payloads = [[{"updated_at": "2026-11-01T00:00:00Z"}], [{"created_at": "2026-11-03T00:00:00Z"}]]
        stamp = oncall_roster.latest_timestamp(payloads)
        self.assertEqual(stamp.day, 3)


class CodeownersCoverageTests(unittest.TestCase):
    def test_shipped_codeowners_meets_minimum(self):
        roster = oncall_roster.load_roster(ROOT / "docs" / "roster.json")
        problems = oncall_roster.codeowners_coverage(ROOT / ".github" / "CODEOWNERS", roster, 3)
        self.assertEqual(problems, [], f"CODEOWNERS 覆盖度不达标: {problems}")

    def test_rule_with_too_few_owners_is_reported(self):
        roster = oncall_roster.load_roster(ROOT / "docs" / "roster.json")
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "CODEOWNERS"
            path.write_text("/docs/ @StrawberryTreea @321ddi\n", encoding="utf-8")
            problems = oncall_roster.codeowners_coverage(path, roster, 3)
        self.assertEqual(len(problems), 1)
        self.assertEqual(problems[0]["owner_count"], 2)
        self.assertEqual(problems[0]["required"], 3)

    def test_account_outside_roster_is_reported(self):
        roster = oncall_roster.load_roster(ROOT / "docs" / "roster.json")
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "CODEOWNERS"
            path.write_text("/docs/ @StrawberryTreea @321ddi @DylanWu6 @stranger\n", encoding="utf-8")
            problems = oncall_roster.codeowners_coverage(path, roster, 3)
        self.assertTrue(any("stranger" in item["reason"] for item in problems))

    def test_parse_ignores_comments_and_blank_lines(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "CODEOWNERS"
            path.write_text("# a comment\n\n/docs/ @a @b @c  # trailing comment\n", encoding="utf-8")
            rules = oncall_roster.parse_codeowners(path)
        self.assertEqual(rules, [("/docs/", ["a", "b", "c"])])

    def test_any_single_absence_still_leaves_two_approvers(self):
        """Three owners per path: the author cannot approve their own last push,
        and one absent member must not block the merge."""
        rules = oncall_roster.parse_codeowners(ROOT / ".github" / "CODEOWNERS")
        self.assertTrue(rules)
        for pattern, owners in rules:
            self.assertGreaterEqual(len(owners), 3, f"{pattern} owners={owners}")


class JoinDateBaselineTests(unittest.TestCase):
    def test_new_member_without_commits_is_not_flagged_as_absent(self):
        roster = {
            "warn_days": 14, "escalate_days": 30, "arbiter": "wanghao",
            "members": [{"id": "newbie", "name": "新人", "github": "x", "role": "r",
                         "owns": [], "backup": "liuke", "joined": "2026-11-01"}],
        }
        now = datetime(2026, 11, 4, tzinfo=timezone.utc)
        rows = oncall_roster.evaluate(roster, {"newbie": None}, now)
        self.assertEqual(rows[0]["status"], oncall_roster.STATUS_ACTIVE)
        self.assertEqual(rows[0]["inactive_days"], 3)
        self.assertEqual(rows[0]["inactive_basis"], "join-date")

    def test_activity_overrides_join_date(self):
        roster = {
            "warn_days": 14, "escalate_days": 30, "arbiter": "wanghao",
            "members": [{"id": "m", "name": "n", "github": "x", "role": "r",
                         "owns": [], "backup": "liuke", "joined": "2026-01-01"}],
        }
        now = datetime(2026, 11, 4, tzinfo=timezone.utc)
        rows = oncall_roster.evaluate(roster, {"m": now - timedelta(days=2)}, now)
        self.assertEqual(rows[0]["inactive_basis"], "activity")
        self.assertEqual(rows[0]["inactive_days"], 2)


class ApiLayerTests(unittest.TestCase):
    """Exercise the GitHub call layer against a local stub server."""

    def setUp(self):
        import http.server
        import os
        import threading

        calls = []

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def _respond(self):
                calls.append((self.command, self.path))
                body = json.dumps([
                    {"number": 3, "title": "P0 task", "assignee": {"login": "deep-fry-xiaopengyou"}},
                    {"number": 9, "title": "a PR in disguise", "pull_request": {"url": "x"}},
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
        self.previous_base = os.environ.get("ONCALL_API_BASE")
        os.environ["ONCALL_API_BASE"] = self.base

    def tearDown(self):
        import os
        if self.previous_base is None:
            os.environ.pop("ONCALL_API_BASE", None)
        else:
            os.environ["ONCALL_API_BASE"] = self.previous_base
        self.server.shutdown()
        self.server.server_close()

    def test_open_issues_excludes_pull_requests(self):
        issues = oncall_roster.open_issues_for("owner/repo", "someone", None)
        self.assertEqual([issue["number"] for issue in issues], [3])

    def test_reassign_issue_patches_then_comments(self):
        result = oncall_roster.reassign_issue("owner/repo", 3, "DylanWu6", None, "handover note")
        self.assertTrue(result)
        methods = [method for method, _ in self.calls]
        self.assertIn("PATCH", methods)
        self.assertIn("POST", methods)
        patch_path = next(path for method, path in self.calls if method == "PATCH")
        self.assertIn("/issues/3", patch_path)

    def test_reassign_issue_reports_failure(self):
        original = oncall_roster.api_request

        def boom(*args, **kwargs):
            raise urllib.error.HTTPError("u", 403, "Forbidden", None, None)

        oncall_roster.api_request = boom
        try:
            result = oncall_roster.reassign_issue("owner/repo", 3, "DylanWu6", None, "note")
        finally:
            oncall_roster.api_request = original
        self.assertFalse(result)


class BoardRenderingTests(unittest.TestCase):
    def test_markdown_mentions_every_member_and_thresholds(self):
        roster = oncall_roster.load_roster(ROOT / "docs" / "roster.json")
        now = datetime(2026, 11, 4, tzinfo=timezone.utc)
        rows = oncall_roster.evaluate(roster, {str(m["id"]): now for m in roster["members"]}, now)
        markdown = oncall_roster.render_markdown(rows, [], roster, "2026-11-04T00:00:00+00:00")
        for member in roster["members"]:
            self.assertIn(str(member["name"]), markdown)
        self.assertIn("14", markdown)
        self.assertIn("30", markdown)
        self.assertIn("无。全部成员在阈值内。", markdown)


if __name__ == "__main__":
    unittest.main()
