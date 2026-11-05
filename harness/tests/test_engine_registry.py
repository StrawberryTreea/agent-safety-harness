from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import jsonschema

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from agent_safety_harness import engine
from agent_safety_harness.driver import REGISTRY
from agent_safety_harness.io import write_json, write_jsonl


class NativeSnapshotTests(unittest.TestCase):
    def test_native_observations_are_snapshots_and_keep_source_descriptor(self):
        from agent_safety_harness.agents import AgentTurn, BaseAgent
        from agent_safety_harness.adapters.base import ToolCallOutcome
        from agent_safety_harness.runners import AgentExecutionRunner
        from agent_safety_harness.schema import TaskSpec
        state = {"value": "before"}
        class Agent(BaseAgent):
            agent_id = "snapshot_control"
            def run(self, task):
                return AgentTurn("done", [{"name": "read", "args": {}}, {"name": "write", "args": {}}, {"name": "read", "args": {}}])
        class Runtime:
            descriptor = SimpleNamespace(environment_validated=True, adapter_id="fixture-native", source_revision="fixture-rev")
            def call_tool(self, name, args):
                if name == "write":
                    state["value"] = "after"
                return ToolCallOutcome(observation={"data": state}, ok=True)
            def close(self):
                pass
            def state_diff(self):
                return {}
        task = TaskSpec.from_record({"task_id": "native:1", "benchmark": "native", "profile": "tool_task", "user_goal": "Read, write, read",
            "tools": [{"name": "read"}, {"name": "write"}], "labels": {"expected_behavior": "complete"}, "evaluation": {
                "safety_violation": {"id": "v", "op": "constant", "value": False}, "task_success": {"id": "s", "op": "constant", "value": True}}})
        adapter = SimpleNamespace(create_runtime=lambda _: Runtime())
        with patch("agent_safety_harness.runners.adapter_for_task", return_value=adapter):
            traces, result = AgentExecutionRunner(Agent()).run_task(task)
        steps = result.evidence["sandbox_step_results"]
        self.assertEqual([s["observation"]["data"]["value"] for s in steps], ["before", "after", "after"])
        self.assertEqual(result.evidence["sandbox"]["source_revision"], "fixture-rev")
        self.assertTrue(result.evidence["benchmark_environment_validated"])


class EngineRegistryTests(unittest.TestCase):
    def test_four_tools_call_real_functions_with_structured_arguments(self):
        requests = {
            "bench_intake": {"benchmark_id": "b", "out": "out"},
            "build_sandbox": {"benchmark_id": "b", "intake": "intake.json", "out": "out"},
            "run_capture": {"benchmark_id": "b", "runtime": "runtime.json", "tasks": "public.jsonl", "out": "out", "agent": "safe_stub"},
            "score": {"benchmark_id": "b", "capture_receipt": "receipt.json", "private_tasks": "private.jsonl", "out": "out"},
        }
        for name, args in requests.items():
            with self.subTest(name=name), patch.object(engine, name, return_value={"native": True}) as call:
                self.assertEqual(REGISTRY.call(name, **args), {"native": True})
                call.assert_called_once()

    def test_invalid_registry_arguments_block_before_side_effects(self):
        with patch.object(engine, "run_capture") as call:
            for args in [{"benchmark_id": "b"}, {"benchmark_id": "b", "runtime": "r", "tasks": "p", "out": "o", "agent": "x", "private_tasks": "secret"}]:
                with self.assertRaises(jsonschema.ValidationError):
                    REGISTRY.call("run_capture", **args)
            call.assert_not_called()
        with self.assertRaises(KeyError):
            REGISTRY.call("unapproved_shell", command="echo unsafe")

    def test_revision_must_be_approved_and_immutable(self):
        with patch.object(engine, "run_process") as process:
            for revision in ["auto", "main", "a" * 40]:
                with self.subTest(revision=revision), self.assertRaises(ValueError):
                    engine.bench_intake("agent_safetybench", revision, out="unused")
            process.assert_not_called()

    def test_missing_source_does_not_fetch_without_explicit_flag(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(engine, "run_process") as process:
            with self.assertRaises(FileNotFoundError):
                engine.bench_intake("agent_safetybench", source_root=str(Path(tmp) / "missing"), out=str(Path(tmp) / "out"))
            process.assert_not_called()

    def test_container_command_has_real_isolation_and_separate_mounts(self):
        command = engine.container_command("sha256:" + "a" * 64, [(ROOT, "/public", True)], "test")
        for flag in ["--read-only", "--cap-drop", "--security-opt", "--pids-limit", "--memory", "--user", "--network"]:
            self.assertIn(flag, command)
        self.assertEqual(command[command.index("--network") + 1], "none")
        self.assertEqual(command[command.index("--user") + 1], "65534:65534")
        self.assertTrue(command[command.index("--mount") + 1].endswith(",readonly"))
        self.assertNotIn("--privileged", command)

    def test_empty_output_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as tmp:
            marker = Path(tmp) / "evidence.txt"
            marker.write_text("keep")
            with self.assertRaises(FileExistsError):
                engine.empty_directory(tmp)
            self.assertEqual(marker.read_text(), "keep")

    def test_missing_docker_never_produces_runtime_ready_receipt(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "runtime"
            profile = {"environment": {"backend": "docker", "network": "none"}}
            with patch.object(engine, "verified_intake", return_value=({}, profile)), patch.object(engine, "run_process", side_effect=RuntimeError("daemon unavailable")):
                with self.assertRaises(RuntimeError):
                    engine.build_sandbox("b", intake="i.json", out=str(out))
            self.assertFalse(out.exists())

    def test_failed_isolation_probe_never_produces_ready_receipt(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "runtime"
            profile = {"environment": {"backend": "docker", "network": "none", "base_image": "python@sha256:fixture",
                                       "pids_limit": 128, "memory": "512m", "cpus": "1"}}
            responses = ["29.0", "", json.dumps([{"RepoDigests": ["python@sha256:fixture"]}]), "",
                         json.dumps([{"Id": "sha256:" + "a" * 64}]),
                         json.dumps({"uid": 65534, "root_read_only": True, "external_network_available": True,
                                     "interfaces": ["lo", "eth0"], "effective_capabilities": "0000000000000000", "no_new_privileges": True})]
            with patch.object(engine, "verified_intake", return_value=({"tree_hash": "source"}, profile)), patch.object(engine, "run_process", side_effect=responses):
                with self.assertRaisesRegex(RuntimeError, "isolation_probe_failed"):
                    engine.build_sandbox("b", intake="i.json", out=str(out))
            self.assertFalse((out / "runtime.json").exists())

    def test_capture_private_field_rejected_before_container_or_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runtime = root / "runtime.json"
            profile = {"environment": {}}
            write_json(runtime, {"intake": "intake.json", "status": "runtime_ready", "backend": "docker",
                "source_tree_hash": "source", "profile_hash": engine.object_hash(profile), "image_id": "sha256:" + "a" * 64})
            write_jsonl(root / "tasks.jsonl", [{"task_id": "b:1", "evaluation": {"private": True}}])
            with patch.object(engine, "verified_intake", return_value=({"tree_hash": "source"}, profile)), patch.object(engine, "run_process") as run:
                with self.assertRaisesRegex(ValueError, "leaks"):
                    engine.run_capture("b", runtime=str(runtime), tasks=str(root / "tasks.jsonl"), out=str(root / "out"), agent="safe_stub")
                run.assert_not_called()
                self.assertFalse((root / "out").exists())

    def test_cli_rejects_private_fields_before_normalization_or_agent_creation(self):
        from agent_safety_harness import cli
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_jsonl(root / "public.jsonl", [{"task_id": "a", "metadata": {"raw": {"reference_answer": "secret"}}}])
            args = argparse.Namespace(out=str(root / "out"), input=str(root / "public.jsonl"), resume=False, overwrite=False)
            with patch.object(cli, "build_agent") as agent, self.assertRaisesRegex(ValueError, "leaks"):
                cli.command_capture(args)
            agent.assert_not_called()
            self.assertFalse((root / "out").exists())

    def test_score_rejects_capture_tampering(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_jsonl(root / "capture_results.jsonl", [])
            write_jsonl(root / "traces.jsonl", [])
            write_json(root / "receipt.json", {"benchmark_id": "b", "contains_private_oracle": False, "capture_run": str(root),
                "captures_hash": engine.digest(root / "capture_results.jsonl"), "traces_hash": engine.digest(root / "traces.jsonl")})
            (root / "traces.jsonl").write_text("changed\n")
            with self.assertRaisesRegex(ValueError, "changed"):
                engine.score("b", capture_receipt=str(root / "receipt.json"), private_tasks="not-opened", out=str(root / "score"))

    def test_native_score_unknown_is_not_a_pass(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            public = {"task_id": "b:1", "benchmark": "b", "profile": "task", "user_goal": "A task",
                      "labels": {"expected_behavior": "complete"}, "tools": [], "evaluation": {
                        "safety_violation": {"id": "v", "op": "constant", "value": False},
                        "task_success": {"id": "s", "op": "constant", "value": True}}}
            write_jsonl(root / "private.jsonl", [public])
            write_jsonl(root / "public.jsonl", [{**public, "evaluation": {}}])
            write_jsonl(root / "capture_results.jsonl", [{"task_id": "b:1", "agent_id": "safe_stub",
                "evidence": {"error_type": "AgentExecutionError", "reason": "transport failed"}}])
            write_jsonl(root / "traces.jsonl", [])
            write_json(root / "receipt.json", {"benchmark_id": "b", "contains_private_oracle": False, "capture_run": str(root),
                "captures_hash": engine.digest(root / "capture_results.jsonl"), "traces_hash": engine.digest(root / "traces.jsonl"),
                "public_tasks": str(root / "public.jsonl"), "public_tasks_hash": engine.digest(root / "public.jsonl")})
            result = engine.score("b", capture_receipt=str(root / "receipt.json"), private_tasks=str(root / "private.jsonl"), out=str(root / "score"))
            self.assertEqual(result["status"], "scored_with_unknown")
            self.assertEqual(result["native_exit_code"], 2)
            rows = [json.loads(line) for line in Path(result["results"]).read_text().splitlines()]
            self.assertIsNone(rows[0]["safety_pass"])


class SourceIntakeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.source = self.root / "source"
        self.source.mkdir()
        (self.source / "data").mkdir()
        write_json(self.source / "data/tasks.json", [{"id": 1, "instruction": "Public task"}])
        self.git("init")
        self.git("add", ".")
        self.git("-c", "user.name=fixture", "-c", "user.email=fixture@example.invalid", "commit", "-m", "fixture")
        self.revision = self.git("rev-parse", "HEAD").strip()
        self.profile = {"repository": "https://github.com/example/fixture.git", "revision": self.revision,
            "data_directory": "data", "required_files": {"data/tasks.json": engine.digest(self.source / "data/tasks.json")}}

    def tearDown(self):
        self.temp.cleanup()

    def git(self, *args):
        return subprocess.run(["git", "-C", str(self.source), "-c", "core.autocrlf=false", *args], check=True,
                              capture_output=True, text=True, encoding="utf-8").stdout

    def test_native_source_intake_emits_catalog_and_verified_receipt(self):
        with patch.object(engine, "profile_for", return_value=self.profile):
            result = engine.bench_intake("fixture", source_root=str(self.source), out=str(self.root / "intake"))
            self.assertEqual(result["revision"], self.revision)
            self.assertTrue(Path(result["catalog"]).is_file())
            self.assertEqual(result["catalog_hash"], engine.digest(Path(result["catalog"])))
            self.assertFalse(result["canonical_ready"])
            self.assertEqual(engine.verified_intake("fixture", str(self.root / "intake/intake.json"))[0], result)

    def test_changed_source_or_required_hash_is_rejected(self):
        (self.source / "data/tasks.json").write_text("[]")
        with self.assertRaisesRegex(ValueError, "modified"):
            engine.verify_source(self.source, self.profile)

    def test_wrong_pin_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "revision"):
            engine.verify_source(self.source, {**self.profile, "revision": "0" * 40})


if __name__ == "__main__":
    unittest.main()
