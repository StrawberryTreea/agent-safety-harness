"""driver 契约测试：先校验再执行，遇到未完整完成的步骤即停止。"""

from __future__ import annotations

from contextlib import redirect_stdout, redirect_stderr
from copy import deepcopy
from io import StringIO
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agent_safety_harness.driver import (
    REGISTRY,
    FunctionSpec,
    ToolRegistry,
    Plan,
    PlanStep,
    execute_plan,
)
from agent_safety_harness.driver.__main__ import main
from agent_safety_harness.driver.nl_bridge import CONFIG_PATH, NLBridge
from agent_safety_harness.driver.planner import plan_from_instruction
from agent_safety_harness.driver.validation import (
    P0_FUNCTIONS,
    plan_from_record,
    validate_config,
    validate_plan,
)


class DriverTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.calls = []
        self.overrides = {}
        self.registry = ToolRegistry()
        for name in P0_FUNCTIONS:

            def invoke(_name=name, **kwargs):
                self.calls.append((_name, kwargs))
                if isinstance(self.overrides.get(_name), Exception):
                    raise self.overrides[_name]
                out = Path(kwargs["out"])
                out.mkdir(parents=True)
                files = {
                    "bench_intake": ["intake.json"],
                    "build_sandbox": ["runtime.json"],
                    "run_capture": ["capture_receipt.json"],
                    "score": ["score_receipt.json", "results.jsonl", "summary.json"],
                    "evolve": [
                        "manifest.json",
                        "seed_refs.jsonl",
                        "pending_requests.jsonl",
                        "summary.json",
                    ],
                }
                for filename in files[_name]:
                    (out / filename).write_text("{}\n", encoding="utf-8")
                status = {
                    "bench_intake": "source_registered",
                    "build_sandbox": "runtime_ready",
                    "run_capture": "captured",
                    "score": "scored",
                    "evolve": "pending_review",
                }[_name]
                return {
                    "status": self.overrides.get(_name, status),
                    "canonical_ready": False,
                    "results": str(out / "results.jsonl"),
                    "summary": str(out / "summary.json"),
                    "private_reason": "PRIVATE-CANARY",
                }

            native = REGISTRY.get(name)
            self.registry.register(
                FunctionSpec(
                    name, native.description, invoke, deepcopy(native.input_schema)
                )
            )

    def step(self, fn="bench_intake", id="first", **changes):
        args = {
            "bench_intake": {"benchmark_id": "b"},
            "build_sandbox": {"benchmark_id": "b", "intake": "input.json"},
            "run_capture": {
                "benchmark_id": "b",
                "runtime": "runtime.json",
                "tasks": "tasks.jsonl",
                "agent": "safe_stub",
            },
            "score": {
                "benchmark_id": "b",
                "capture_receipt": "receipt.json",
                "private_tasks": "private.jsonl",
            },
            "evolve": {
                "track": "agent_execution",
                "analysis_manifest": "manifest.json",
            },
        }[fn]
        args["out"] = str(self.root / id)
        return PlanStep(fn, args, id=id, **changes)

    def execute(self, steps, **kwargs):
        return execute_plan(Plan(steps), registry=self.registry, **kwargs)

    def test_all_four_engine_functions_chain_artifact_paths(self):
        steps = [self.step("bench_intake", "intake")]
        for fn, id, param, previous, artifact in (
            ("build_sandbox", "runtime", "intake", "intake", "intake"),
            ("run_capture", "capture", "runtime", "runtime", "runtime"),
            ("score", "score", "capture_receipt", "capture", "capture_receipt"),
        ):
            step = self.step(fn, id, depends_on=[previous])
            step.args[param] = {"$from": previous, "field": "artifacts." + artifact}
            steps.append(step)
        result = self.execute(steps, dry_run=False, record_dir=self.root / "audit")
        self.assertEqual(result.status, "completed")
        self.assertEqual([name for name, _ in self.calls], [step.fn for step in steps])
        # Windows 临时目录可能使用短路径别名；预期值与 driver 的产物路径保持相同的规范形式。
        self.assertEqual(
            self.calls[1][1]["intake"],
            str((self.root / "intake/intake.json").resolve()),
        )
        self.assertNotIn(
            "PRIVATE-CANARY", (self.root / "audit/execution.json").read_text()
        )
        self.assertFalse(result.context["intake"]["output"]["canonical_ready"])

    def test_default_dry_run_has_no_calls_or_directories(self):
        result = self.execute([self.step()], record_dir=self.root / "audit")
        self.assertEqual(result.status, "planned")
        self.assertEqual(result.results[0].status, "planned")
        self.assertEqual(self.calls, [])
        self.assertEqual(list(self.root.iterdir()), [])

    def test_invalid_later_step_prevents_any_execution(self):
        invalid = self.step("score", "bad")
        del invalid.args["private_tasks"]
        with self.assertRaisesRegex(ValueError, "private_tasks"):
            self.execute(
                [self.step(), invalid], dry_run=False, record_dir=self.root / "audit"
            )
        self.assertFalse(self.calls)
        self.assertFalse((self.root / "audit").exists())

    def test_bad_functions_and_arguments_are_rejected(self):
        for fn in ("analyze", "shell", "help"):
            with self.subTest(fn=fn), self.assertRaises(ValueError):
                self.execute([PlanStep(fn)])
        for changes in (
            {"unexpected": "x"},
            {"allow_fetch": "yes"},
            {"benchmark_id": 1},
        ):
            step = self.step()
            step.args.update(changes)
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.execute([step])
        with self.assertRaises(ValueError):
            self.execute([self.step()], allowed=[])

    def test_duplicate_ids_and_output_overlap_rejected(self):
        with self.assertRaises(ValueError):
            self.execute([self.step(), self.step()])
        second = self.step(id="second")
        second.args["out"] = str(self.root / "first/nested")
        with self.assertRaises(ValueError):
            self.execute([self.step(), second])

    def test_forward_cycle_and_undeclared_references_rejected(self):
        first = self.step(depends_on=["second"])
        second = self.step(id="second", depends_on=["first"])
        with self.assertRaises(ValueError):
            self.execute([first, second])
        second.depends_on = []
        second.args["benchmark_id"] = {"$from": "first", "field": "output.status"}
        with self.assertRaises(ValueError):
            self.execute([self.step(), second])

    def test_missing_output_field_fails_before_dependent_call(self):
        second = self.step(id="second", depends_on=["first"])
        second.args["benchmark_id"] = {"$from": "first", "field": "output.absent"}
        result = self.execute([self.step(), second], dry_run=False)
        self.assertEqual(result.status, "failed")
        self.assertEqual(len(self.calls), 1)

    def test_resolved_argument_type_is_checked(self):
        second = self.step(id="second", depends_on=["first"])
        second.args["benchmark_id"] = {
            "$from": "first",
            "field": "output.canonical_ready",
        }
        result = self.execute([self.step(), second], dry_run=False)
        self.assertEqual(result.status, "failed")
        self.assertEqual(len(self.calls), 1)

    def test_condition_false_skips_descendants(self):
        second = self.step(
            id="second",
            depends_on=["first"],
            condition={
                "$from": "first",
                "field": "output.canonical_ready",
                "equals": True,
            },
        )
        result = self.execute(
            [self.step(), second, self.step(id="third", depends_on=["second"])],
            dry_run=False,
        )
        self.assertEqual(
            [item.status for item in result.results], ["ok", "skipped", "skipped"]
        )
        self.assertEqual(result.status, "completed_with_skips")

    def test_true_legacy_condition_and_same_function_twice(self):
        second = self.step(
            id="second",
            condition={
                "if_output_of": 0,
                "key": "status",
                "equals": "source_registered",
            },
        )
        result = self.execute([self.step(), second], dry_run=False)
        self.assertEqual(result.status, "completed")
        self.assertEqual(set(result.context), {"first", "second"})

    def test_invalid_conditions_fail_closed(self):
        for condition in (
            {},
            {"if_output_of": 9, "key": "status", "equals": True},
            {"if_output_of": False, "key": "status", "equals": True},
        ):
            with self.subTest(condition=condition), self.assertRaises(ValueError):
                self.execute([self.step(), self.step(id="second", condition=condition)])

    def test_incomplete_business_outcomes_halt_and_keep_artifacts(self):
        for fn, status in (
            ("run_capture", "capture_invalid"),
            ("score", "scored_with_unknown"),
        ):
            with self.subTest(status=status):
                self.overrides[fn] = status
                result = self.execute(
                    [self.step(fn, status), self.step(id="after-" + status)],
                    dry_run=False,
                )
                self.assertEqual(result.status, "incomplete")
                self.assertTrue(result.results[0].artifacts)
                self.assertEqual(result.results[1].status, "skipped")

    def test_unknown_status_and_exception_halt(self):
        for index, outcome in enumerate(
            ("placeholder", RuntimeError("PRIVATE-CANARY"))
        ):
            self.overrides["bench_intake"] = outcome
            result = self.execute(
                [self.step(id=f"case-{index}"), self.step(id=f"after-{index}")],
                dry_run=False,
            )
            self.assertEqual(result.status, "failed")
            self.assertEqual(result.results[1].status, "skipped")
            self.assertNotIn("PRIVATE-CANARY", str(result))

    def test_missing_artifact_does_not_publish_success(self):
        with patch(
            "agent_safety_harness.driver.executor.checked_artifacts",
            side_effect=ValueError("missing"),
        ):
            result = self.execute([self.step()], dry_run=False)
        self.assertEqual(result.status, "failed")
        self.assertFalse(result.context)

    def test_nonempty_output_and_audit_are_not_overwritten(self):
        target = self.root / "first"
        target.mkdir()
        (target / "keep").write_text("keep")
        result = self.execute([self.step()], dry_run=False)
        self.assertEqual(result.status, "failed")
        self.assertFalse(self.calls)
        with self.assertRaises(FileExistsError):
            self.execute([self.step(id="new")], dry_run=False, record_dir=target)
        self.assertEqual((target / "keep").read_text(), "keep")

    def test_keyword_binding_uses_local_resources(self):
        config = {
            "run_root": str(self.root),
            "arguments": {
                "bench_intake": {"benchmark_id": "b"},
                "build_sandbox": {"benchmark_id": "b"},
            },
        }
        plan = plan_from_instruction(
            "拉取并构建环境", config=config, registry=self.registry
        )
        self.assertNotIn("instruction", plan.steps[0].args)
        self.assertEqual(plan.steps[1].args["intake"]["$from"], plan.steps[0].id)
        with self.assertRaisesRegex(ValueError, "尚未接入"):
            plan_from_instruction("分析", config=config, registry=self.registry)

    def test_instruction_requires_resources_and_config_does_not_expand_allowlist(self):
        with self.assertRaises(ValueError):
            plan_from_instruction("执行")
        with self.assertRaises(ValueError):
            validate_config({"allowed_functions": ["shell"]})
        with self.assertRaises(ValueError):
            plan_from_record(
                {"schema_version": "driver_plan_v1", "steps": [], "shell": "bad"}
            )

    def test_model_uses_schema_but_not_local_private_config(self):
        client = Mock()
        client.complete.return_value = json.dumps(
            {"schema_version": "driver_plan_v1", "steps": [{"fn": "score"}]}
        )
        bridge = NLBridge(client, registry=self.registry)
        config = {
            "run_root": str(self.root),
            "allowed_functions": ["score"],
            "arguments": {
                "score": {
                    "benchmark_id": "b",
                    "capture_receipt": "capture.json",
                    "private_tasks": "PRIVATE-CANARY",
                },
            },
        }
        plan = plan_from_instruction(
            "评分", client=bridge, config=config, registry=self.registry
        )
        prompt = str(client.complete.call_args)
        self.assertIn("input_schema", prompt)
        self.assertNotIn("PRIVATE-CANARY", prompt)
        self.assertEqual(plan.steps[0].args["private_tasks"], "PRIVATE-CANARY")
        self.assertTrue(CONFIG_PATH.is_file())

    def test_model_bad_output_or_error_never_falls_back(self):
        config = {
            "run_root": str(self.root),
            "arguments": {"bench_intake": {"benchmark_id": "b"}},
        }
        for response in (
            "invalid json",
            "[]",
            '{"schema_version":"driver_plan_v1","steps":[{"fn":"shell"}]}',
            '{"schema_version":"driver_plan_v1","steps":[{"fn":"bench_intake","args":{"allow_fetch":true}}]}',
        ):
            client = Mock()
            client.complete.return_value = response
            with self.subTest(response=response), self.assertRaises(ValueError):
                plan_from_instruction(
                    "拉取",
                    config=config,
                    client=NLBridge(client),
                    registry=self.registry,
                )
        client = Mock()
        client.complete.side_effect = RuntimeError("provider unavailable")
        with self.assertRaises(RuntimeError):
            plan_from_instruction(
                "拉取", config=config, client=NLBridge(client), registry=self.registry
            )

    def test_cli_help_and_plan_default_are_read_only(self):
        output = StringIO()
        with redirect_stdout(output):
            self.assertEqual(main([]), 0)
        self.assertIn("--execute", output.getvalue())
        plan_path = self.root / "plan.json"
        plan_path.write_text(
            json.dumps(
                {
                    "schema_version": "driver_plan_v1",
                    "steps": [
                        {
                            "fn": "evolve",
                            "args": {
                                "track": "agent_execution",
                                "analysis_manifest": "missing",
                                "out": str(self.root / "output"),
                            },
                        },
                    ],
                }
            )
        )
        with redirect_stdout(StringIO()), redirect_stderr(StringIO()):
            self.assertEqual(main(["--plan", str(plan_path)]), 0)
        self.assertFalse((self.root / "output").exists())


if __name__ == "__main__":
    unittest.main()
