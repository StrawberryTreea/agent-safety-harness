from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from product_eval.judge_policy import load_and_validate_judge_policy, validate_api_judge_binding


PACKAGE_ROOT = Path(__file__).resolve().parents[1]


class JudgePolicyTest(unittest.TestCase):
    def test_repository_policy_locks_all_api_evaluators(self) -> None:
        policy = load_and_validate_judge_policy(PACKAGE_ROOT / "benchmark_registry.json")
        self.assertEqual(policy["profile_id"], "deepseek_v4_flash")
        self.assertEqual(policy["model"], "deepseek-v4-flash")
        self.assertEqual(policy["api_benchmarks"], ["saber"])
        self.assertEqual(
            set(policy["deterministic_benchmarks"]),
            {"injecagent", "msb", "agentdojo", "agentleak", "agentpoison_strategyqa"},
        )

    def test_rejects_api_benchmark_with_another_profile(self) -> None:
        registry = json.loads((PACKAGE_ROOT / "benchmark_registry.json").read_text(encoding="utf-8"))
        saber = next(row for row in registry["benchmarks"] if row["benchmark_id"] == "saber")
        saber["api_judge_profile"] = "another_judge"
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "registry.json"
            path.write_text(json.dumps(registry), encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "must use API judge profile"):
                load_and_validate_judge_policy(path)

    def test_rejects_runtime_judge_that_differs_from_policy(self) -> None:
        policy = load_and_validate_judge_policy(PACKAGE_ROOT / "benchmark_registry.json")
        with self.assertRaisesRegex(RuntimeError, "does not match locked policy"):
            validate_api_judge_binding(
                policy,
                {
                    "type": "openai",
                    "id": "other-model",
                    "base_url": "https://api.deepseek.com",
                    "key_env": "DEEPSEEK_API_KEY",
                    "temperature": 0.0,
                },
            )

    def test_rejects_unknown_evaluation_backend(self) -> None:
        registry = json.loads((PACKAGE_ROOT / "benchmark_registry.json").read_text(encoding="utf-8"))
        registry["benchmarks"][0]["evaluation_backend"] = "api_judeg"
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "registry.json"
            path.write_text(json.dumps(registry), encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "unsupported evaluation_backend"):
                load_and_validate_judge_policy(path)
