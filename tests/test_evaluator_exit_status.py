"""Evaluator failures must remain visible to shell users and CI."""
from __future__ import annotations

import copy
import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("exit_status_evaluator", ROOT / "llm_eval/multi_model_rule_eval_prompt_only.py")
evaluator = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = evaluator
spec.loader.exec_module(evaluator)


class EvaluatorExitStatusTests(unittest.TestCase):
    def invoke(self, models, run_error=None):
        config = copy.deepcopy(evaluator.CONFIG)
        with tempfile.TemporaryDirectory() as temporary:
            config.update(INPUT_JSONL=str(ROOT / "assets/samples/llm_qa_demo.jsonl"),
                          RUN_ROOT=temporary, RUN_MODE="self_test", MODELS=models,
                          FAIL_FAST_ON_MODEL_ERROR=False)
            with patch.object(evaluator, "CONFIG", config), patch.object(evaluator, "STOP_REQUESTED", False), \
                 patch.object(evaluator, "setup_logging"), patch.object(evaluator, "write_leaderboard"), \
                 patch.object(evaluator, "run_one_model", side_effect=run_error) as runner:
                status = evaluator.main([])
                return status, runner.call_count

    def test_no_enabled_model_fails(self):
        status, calls = self.invoke([])
        self.assertEqual(status, 2)
        self.assertEqual(calls, 0)

    def test_model_exception_fails_even_when_other_models_continue(self):
        models = [{"name": "broken", "provider": "self_test", "enabled": True},
                  {"name": "working", "provider": "self_test", "enabled": True}]
        status, calls = self.invoke(models, [RuntimeError("fixture model error"), None])
        self.assertEqual(status, 1)
        self.assertEqual(calls, 2)


if __name__ == "__main__":
    unittest.main()
