from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from product_eval.api_judge import (
    OpenAICompatibleJudge,
    load_immutable_judge_profile,
)


PACKAGE_ROOT = Path(__file__).resolve().parents[1]


class _Response:
    def __init__(self, body: dict):
        self.body = json.dumps(body).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self) -> bytes:
        return self.body


class ApiJudgeTest(unittest.TestCase):
    def test_release_profile_matches_locked_benchmark_policy(self) -> None:
        profile = load_immutable_judge_profile(
            PACKAGE_ROOT / "release" / "evaluation_config.json",
            PACKAGE_ROOT / "benchmark_registry.json",
        )
        self.assertEqual(profile["profile_id"], "deepseek_v4_flash")
        self.assertEqual(profile["model"], "deepseek-v4-flash")
        self.assertEqual(profile["base_url"], "https://api.deepseek.com")

    def test_call_persists_complete_evidence_without_credential(self) -> None:
        profile = load_immutable_judge_profile(
            PACKAGE_ROOT / "release" / "evaluation_config.json",
            PACKAGE_ROOT / "benchmark_registry.json",
        )
        with tempfile.TemporaryDirectory() as tmp:
            evidence = Path(tmp) / "judge.json"
            judge = OpenAICompatibleJudge(profile, evidence_path=evidence)
            response = _Response(
                {
                    "id": "response-1",
                    "choices": [
                        {
                            "message": {"content": "Answer: No"},
                            "finish_reason": "stop",
                        }
                    ],
                    "usage": {"prompt_tokens": 5, "completion_tokens": 3},
                }
            )
            with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "test-secret"}), patch(
                "urllib.request.urlopen", return_value=response
            ) as urlopen:
                self.assertEqual(judge("judge this response"), "Answer: No")
            request = urlopen.call_args.args[0]
            self.assertEqual(request.full_url, "https://api.deepseek.com/chat/completions")
            artifact = json.loads(evidence.read_text(encoding="utf-8"))
            self.assertEqual(artifact["calls"][0]["prompt"], "judge this response")
            self.assertEqual(artifact["calls"][0]["completion"], "Answer: No")
            self.assertNotIn("test-secret", evidence.read_text(encoding="utf-8"))

    def test_preflight_fails_closed_without_credential(self) -> None:
        profile = load_immutable_judge_profile(
            PACKAGE_ROOT / "release" / "evaluation_config.json",
            PACKAGE_ROOT / "benchmark_registry.json",
        )
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(RuntimeError, "DEEPSEEK_API_KEY"):
                OpenAICompatibleJudge(profile).preflight()


if __name__ == "__main__":
    unittest.main()
