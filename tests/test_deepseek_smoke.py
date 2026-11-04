from __future__ import annotations

import json
import os
import unittest
from unittest.mock import patch

from scripts.run_deepseek_smoke import run, validate_usage
from tests.temp_workspace import WorkspaceTempDir


def response(answer="A", **changes):
    value = {"model": "deepseek-flash", "system_fingerprint": "fixture",
             "choices": [{"message": {"content": answer}, "finish_reason": "stop"}],
             "usage": {"prompt_tokens": 10, "prompt_cache_hit_tokens": 0,
                       "prompt_cache_miss_tokens": 10, "completion_tokens": 1, "total_tokens": 11}}
    value.update(changes)
    return value


class DeepSeekSmokeTests(unittest.TestCase):
    def test_default_is_offline_and_existing_output_is_rejected(self):
        with WorkspaceTempDir() as root:
            with patch("scripts.run_deepseek_smoke.transport", side_effect=AssertionError("network")):
                result = run(root / "run")
                self.assertEqual(result["status"], "plan_only")
                self.assertEqual(result["attempted_requests"], 0)
                with self.assertRaises(FileExistsError):
                    run(root / "run")

    def test_missing_key_blocks_before_output_creation(self):
        with WorkspaceTempDir() as root, patch.dict(os.environ, {"DEEPSEEK_API_KEY": ""}):
            with self.assertRaises(ValueError):
                run(root / "run", live=True)
            self.assertFalse((root / "run").exists())

    def test_native_score_usage_and_no_private_answer_in_payload(self):
        with WorkspaceTempDir() as root, patch.dict(os.environ, {"DEEPSEEK_API_KEY": "test-secret"}):
            answers = iter(["A", "B", "A"])
            def send(body, key):
                self.assertEqual(key, "test-secret")
                self.assertNotIn("reference_answer", json.dumps(body))
                self.assertEqual(body["thinking"], {"type": "disabled"})
                self.assertEqual(body["max_tokens"], 32)
                return response(next(answers))
            result = run(root / "run", live=True, send=send)
            self.assertEqual((result["passed"], result["failed"], result["api_errors"]), (3, 0, 0))
            self.assertEqual(result["attempted_requests"], 3)
            self.assertEqual(result["usage_priced_upper_microcny"], 84)
            self.assertEqual(result["budget"]["unsettled_jobs"], 0)
            for path in (root / "run").glob("*.json*"):
                self.assertNotIn("test-secret", path.read_text(encoding="utf-8"))

    def test_unknown_network_cost_keeps_reservation_and_stops_without_retry(self):
        with WorkspaceTempDir() as root, patch.dict(os.environ, {"DEEPSEEK_API_KEY": "test-secret"}):
            calls = []
            def send(body, key):
                calls.append(1)
                raise TimeoutError("test-secret must not appear in logs")
            result = run(root / "run", live=True, send=send)
            self.assertEqual(len(calls), 1)
            self.assertEqual(result["budget"]["cost_committed_or_reserved"], 500_000)
            self.assertEqual(result["budget"]["unsettled_jobs"], 1)
            self.assertIsNone(result["rule_eval_pass_rate"])
            self.assertNotIn("test-secret", (root / "run/results.jsonl").read_text(encoding="utf-8"))

    def test_missing_or_inconsistent_usage_is_not_free(self):
        for usage in (None, {}, {"prompt_tokens": True},
                      {"prompt_tokens": 10, "prompt_cache_hit_tokens": 0,
                       "prompt_cache_miss_tokens": 9, "completion_tokens": 1, "total_tokens": 11}):
            with self.subTest(usage=usage), WorkspaceTempDir() as root:
                with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "test-secret"}):
                    result = run(root / "run", live=True, send=lambda body, key: response(usage=usage))
                self.assertEqual(result["attempted_requests"], 1)
                self.assertEqual(result["api_errors"], 1)
                self.assertEqual(result["budget"]["unsettled_jobs"], 1)

    def test_incomplete_response_is_invalid_even_if_answer_matches(self):
        with WorkspaceTempDir() as root, patch.dict(os.environ, {"DEEPSEEK_API_KEY": "test-secret"}):
            result = run(root / "run", live=True, send=lambda body, key: response(
                choices=[{"message": {"content": "A"}, "finish_reason": "length"}]))
            self.assertEqual((result["attempted_requests"], result["invalid"], result["passed"]), (1, 1, 0))
            self.assertIsNone(result["rule_eval_pass_rate"])

    def test_usage_peak_rounding(self):
        usage = {"prompt_tokens": 1, "prompt_cache_hit_tokens": 1,
                 "prompt_cache_miss_tokens": 0, "completion_tokens": 0, "total_tokens": 1}
        self.assertEqual(validate_usage(usage)[1], 1)

    def test_unextractable_completed_answer_fails_native_denominator(self):
        with WorkspaceTempDir() as root, patch.dict(os.environ, {"DEEPSEEK_API_KEY": "test-secret"}):
            result = run(root / "run", live=True, send=lambda body, key: response("I do not know."))
            self.assertEqual((result["completed_valid"], result["failed"], result["unextractable_outputs"]), (3, 3, 3))
            self.assertEqual(result["rule_eval_pass_rate"], 0)

    def test_recorded_cost_overrun_stops_further_dispatch(self):
        with WorkspaceTempDir() as root, patch.dict(os.environ, {"DEEPSEEK_API_KEY": "test-secret"}):
            usage = {"prompt_tokens": 1_000_000, "prompt_cache_hit_tokens": 0,
                     "prompt_cache_miss_tokens": 1_000_000, "completion_tokens": 1, "total_tokens": 1_000_001}
            result = run(root / "run", live=True, send=lambda body, key: response(usage=usage))
            self.assertEqual(result["attempted_requests"], 1)
            self.assertEqual(result["budget"]["cost_committed_or_reserved"], 2_000_008)
            self.assertEqual(len(result["budget"]["incidents"]), 1)


if __name__ == "__main__":
    unittest.main()
