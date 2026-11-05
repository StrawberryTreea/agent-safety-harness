from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from agent_safety_harness.importers.agentharm import convert_agentharm_record
from agent_safety_harness.importers.mapped import convert_mapped_record
from agent_safety_harness.public_capture import assert_public_record
from agent_safety_harness.schema import TaskSpec, to_record


class ModalityTests(unittest.TestCase):
    def agent(self, **changes):
        return {"task_id": "a", "benchmark": "demo", "profile": "task", "user_goal": "Read the public file",
                "labels": {"expected_behavior": "complete"}, **changes}

    def llm(self, **changes):
        return {"task_id": "q", "benchmark": "demo", "profile": "qa", "modality": "llm",
                "test_text": "Select A or B", "type": "选择", "eval_method": "规则", **changes}

    def test_old_agent_records_gain_defaults_and_roundtrip(self):
        task = TaskSpec.from_record(self.agent())
        self.assertEqual(task.modality, "agent")
        self.assertEqual(task.taxonomy_ref, "default-agent")
        self.assertEqual(to_record(TaskSpec.from_record(to_record(task))), to_record(task))

    def test_pure_llm_needs_no_agent_fields_and_keeps_rule_payload(self):
        task = TaskSpec.from_record(self.llm(reference_answer="A", note={"rule": "exact"}, label_mapping={"A": True}))
        self.assertEqual(task.taxonomy_ref, "default-llm")
        self.assertEqual(task.reference_answer, "A")
        self.assertEqual(task.note, {"rule": "exact"})
        self.assertEqual(task.user_goal, "")
        self.assertEqual(task.tools, [])
        self.assertEqual(to_record(TaskSpec.from_record(to_record(task))), to_record(task))

    def test_llm_ignores_agent_environment(self):
        task = TaskSpec.from_record(self.llm(user_goal="ignored", tools=[{"name": "shell"}], initial_state={"x": 1}))
        self.assertFalse(task.tools)
        self.assertFalse(task.initial_state)
        self.assertFalse(task.user_goal)

    def test_llm_tasks_cannot_silently_enter_agent_execution(self):
        from agent_safety_harness.agents import SafeStubAgent
        from agent_safety_harness.runners import AgentExecutionRunner
        with self.assertRaisesRegex(ValueError, "llm_qa"):
            AgentExecutionRunner(SafeStubAgent()).run_task(TaskSpec.from_record(self.llm()))

    def test_llm_required_fields_are_enforced_by_modality(self):
        for key in ["test_text", "type", "eval_method"]:
            row = self.llm()
            del row[key]
            with self.subTest(key=key), self.assertRaises(ValueError):
                TaskSpec.from_record(row)

    def test_unknown_modality_and_incompatible_taxonomy_rejected(self):
        for row in [self.llm(modality="unknown"), self.llm(taxonomy_ref="default-agent"), self.agent(taxonomy_ref="unregistered")]:
            with self.subTest(row=row), self.assertRaises(ValueError):
                TaskSpec.from_record(row)

    def test_agent_may_be_environment_driven_but_cannot_be_empty(self):
        self.assertEqual(TaskSpec.from_record(self.agent(user_goal="", initial_state={"ready": True})).user_goal, "")
        with self.assertRaises(ValueError):
            TaskSpec.from_record(self.agent(user_goal=""))

    def test_registered_extension_keeps_custom_fields_without_schema_edit(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "taxonomy.yaml").write_text("task_taxonomies:\n  audio-native:\n    modalities: [audio]\n", encoding="utf-8")
            (root / "modalities.json").write_text(json.dumps({"taxonomy_registry": "taxonomy.yaml", "modalities": {
                "audio": {"default_taxonomy_ref": "audio-native", "schema": {"required": ["audio_ref"],
                    "properties": {"audio_ref": {"type": "string", "minLength": 1}}}}}}), encoding="utf-8")
            row = {"task_id": "s", "benchmark": "audio-bench", "profile": "speech", "modality": "audio", "audio_ref": "sample.wav"}
            row.update(user_goal="Listen to the recording", tools=[{"name": "listen"}], initial_state={"volume": 1})
            config = str(root / "modalities.json")
            task = TaskSpec.from_record(row, modalities_config=config)
            self.assertEqual(task.modality_data, {"audio_ref": "sample.wav"})
            self.assertEqual(task.tools[0]["name"], "listen")
            self.assertEqual(task.initial_state, {"volume": 1})
            self.assertEqual(TaskSpec.from_record(to_record(task), modalities_config=config), task)

    def test_native_labels_not_forced_into_default_dimensions(self):
        task = TaskSpec.from_record(self.agent(labels={"expected_behavior": "complete", "native_family": "navigation"}))
        self.assertEqual(task.labels["native_family"], "navigation")

    def test_importers_explicitly_emit_agent_modality(self):
        agentharm = convert_agentharm_record({"id": 1, "prompt": "A harmless task"}, index=1, source_name="sample", kind="benign")
        mapped = convert_mapped_record({"id": 1, "instruction": "A task"}, {"benchmark": "demo", "profile": "task",
            "fields": {"task_id": "id", "user_goal": "instruction"}}, "source", 1)
        self.assertEqual(agentharm["modality"], "agent")
        self.assertEqual(mapped["modality"], "agent")

    def test_public_capture_rejects_original_nested_private_fields(self):
        for fragment in [{"evaluation": {"safety_violation": True}}, {"reference_answer": "A"},
                         {"metadata": {"raw": {"reference_answer": "A"}}}, {"note": {"rule": "secret"}},
                         {"label_mapping": {"A": "safe"}}]:
            with self.subTest(fragment=fragment), self.assertRaises(ValueError):
                assert_public_record(self.llm(**fragment))

    def test_public_capture_allows_explicitly_empty_private_fields(self):
        assert_public_record(self.llm(evaluation={}, reference_answer=None, note={}, label_mapping={}))


if __name__ == "__main__":
    unittest.main()
