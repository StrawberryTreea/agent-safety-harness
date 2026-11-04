from __future__ import annotations

import copy
import importlib.util
import io
import json
from pathlib import Path
import sys
from contextlib import redirect_stdout
from unittest.mock import patch
import unittest

from tests.temp_workspace import WorkspaceTempDir
from orchestrator.registry import content_hash
from orchestrator.controller import readiness
from scripts.pipeline_cli import main

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("loop_a_native_llm", ROOT/"llm_eval/multi_model_rule_eval_prompt_only.py")
llm = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = llm
spec.loader.exec_module(llm)


class LLMRegressionTests(unittest.TestCase):
    def test_choice_ambiguity_cannot_become_false_positive(self):
        labels = ["A", "B"]
        for response in ["A is wrong; B is correct.", "Answer: A. Answer: B.", "A or B", "Not A"]:
            with self.subTest(response=response):
                self.assertIsNone(llm.RuleScorer.extract_choice(response, labels, {}))
        for response in ["A", "答案：A", "I choose A", "(A)"]:
            self.assertEqual(llm.RuleScorer.extract_choice(response, labels, {}), "A")

    def test_conflict_after_500_chars_is_not_hidden(self):
        self.assertIsNone(llm.RuleScorer.extract_choice("Answer: A. " + "text "*150 + "Answer: B.", ["A", "B"], {}))

    def test_prompt_hash_covers_system_and_suffix(self):
        cfg = copy.deepcopy(llm.CONFIG)
        record = {"id": "x", "test_text": "Answer A", "reference_answer": "A"}
        with patch.object(llm, "CONFIG", cfg):
            first = content_hash(llm.build_chat_messages(llm.build_prompt(record)))
            cfg["SYSTEM_PROMPT"] = "changed"
            self.assertNotEqual(first, content_hash(llm.build_chat_messages(llm.build_prompt(record))))

    def test_resume_checks_the_real_model_run_identity(self):
        cfg = copy.deepcopy(llm.CONFIG)
        cfg.update(RUN_MODE="self_test", COUNT_TOTAL_AT_START=False, SHOW_PROGRESS_BAR=False,
                   SYSTEM_PROMPT="", STOP_REQUESTED=False)
        model = {"name": "self_test_reference", "provider": "self_test", "enabled": True}
        with WorkspaceTempDir() as root, patch.object(llm, "CONFIG", cfg), patch.object(llm, "STOP_REQUESTED", False):
            llm.run_one_model(model, ROOT/"assets/samples/llm_qa_demo.jsonl", root)
            result_path = root/"self_test_reference/results.jsonl"
            before = result_path.read_bytes()
            llm.run_one_model(model, ROOT/"assets/samples/llm_qa_demo.jsonl", root)
            self.assertEqual(before, result_path.read_bytes())
            cfg["SYSTEM_PROMPT"] = "changed"
            with self.assertRaisesRegex(ValueError, "stale_identity"):
                llm.run_one_model(model, ROOT/"assets/samples/llm_qa_demo.jsonl", root)


class ControllerTests(unittest.TestCase):
    def test_formal_run_is_blocked_and_no_dispatch_occurs(self):
        config = json.loads((ROOT/"protocol/loop_a_experiment.json").read_text(encoding="utf-8"))
        self.assertFalse(readiness(config)["formal_ready"])
        with redirect_stdout(io.StringIO()):
            self.assertEqual(main(["loop-a", "run"]), 2)

    def test_fixture_smoke_and_completed_resume(self):
        from orchestrator.controller import smoke
        with WorkspaceTempDir() as root:
            out = root/"smoke"
            result = smoke(out)
            self.assertEqual(result["baseline_safety_failures"], 2)
            self.assertEqual(result["admitted_candidate_count"], 0)
            self.assertFalse(result["readiness"]["formal_ready"])
            self.assertEqual(result, smoke(out, resume=True))
            self.assertEqual(result["budget"]["executions_committed_or_reserved"], 3)
            with self.assertRaises(FileExistsError):
                smoke(out)
            path = out/"baseline/score/results.jsonl"
            path.write_text("tampered", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "artifact changed"):
                smoke(out, resume=True)
