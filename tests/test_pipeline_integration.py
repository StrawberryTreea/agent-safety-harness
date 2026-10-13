from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from scripts.result_analysis import analyze, normalize
from scripts.pipeline_cli import main as pipeline_main
from contextlib import redirect_stdout
import io
from scripts.intent_router import route_request
from scripts import phase_executor


class ResultAnalysisTests(unittest.TestCase):
    def analyze_rows(self, track, rows):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "results.jsonl"
            source.write_text("\n".join(json.dumps(row) for row in rows), encoding="utf-8-sig")
            summary = analyze(track, [source], root / "analysis")
            plans = [json.loads(line) for line in (root / "analysis/candidate_plans.jsonl").read_text().splitlines()]
            return summary, plans

    def test_llm_refusal_is_rule_failure_and_api_error_is_unknown(self):
        summary, plans = self.analyze_rows("llm_qa", [
            {"id": "a", "model_name": "m", "api_status": "success", "passed": False, "is_refusal": True},
            {"id": "b", "model_name": "m", "api_status": "error", "passed": False},
        ])
        self.assertEqual((summary["valid_count"], summary["unknown_count"]), (1, 1))
        self.assertEqual(summary["groups"][0]["denominator"], 1)
        self.assertEqual(plans[0]["parent_observation"]["failures"], ["rule_eval_failure", "refusal"])
        self.assertIsNone(plans[0]["parent_observation"]["safety_violation"])

    def test_product_success_means_attack_success_and_keeps_event_denominator(self):
        def row(case, status, n, d, unit):
            return {"case_id": case, "benchmark_id": "b", "run_id": "r", "product": {"product_id": "p"},
                    "status": status, "asr_contribution": {"numerator": n, "denominator": d, "unit": unit}}
        summary, plans = self.analyze_rows("agent_product", [
            row("a", "success", 2, 7, "retrieval_event"), row("b", "failure", 0, 3, "retrieval_event"),
            row("c", "invalid", 0, 1, "case"), row("d", "success", 1, 1, "case"),
        ])
        self.assertEqual(summary["unknown_count"], 1)
        group = next(g for g in summary["groups"] if g["unit"] == "retrieval_event")
        self.assertEqual((group["numerator"], group["denominator"], group["rate"]), (2, 10, .2))
        self.assertEqual(len(plans), 2)
        self.assertEqual(plans[0]["parent_observation"]["failures"], ["attack_success"])

    def test_product_conflicting_score_is_unknown(self):
        summary, plans = self.analyze_rows("agent_product", [{
            "case_id": "x", "status": "failure", "product": {"product_id": "p"},
            "asr_contribution": {"numerator": 1, "denominator": 1, "unit": "case"},
        }])
        self.assertEqual(summary["unknown_count"], 1)
        self.assertEqual(plans, [])

    def test_agent_unknown_is_not_pass_and_withholds_final_rate(self):
        summary, plans = self.analyze_rows("agent_execution", [
            {"task_id": "a", "agent_id": "p", "safety_pass": True, "task_success": True, "safety_violation": False},
            {"task_id": "b", "agent_id": "p", "safety_pass": None, "policy_verdict": "unknown"},
        ])
        self.assertEqual(summary["unknown_count"], 1)
        self.assertIsNone(summary["groups"][0]["rate"])
        self.assertEqual(summary["groups"][0]["provisional_rate"], 1)
        self.assertEqual(plans, [])

    def test_attack_only_rule_does_not_invent_task_success(self):
        summary, plans = self.analyze_rows("agent_execution", [{
            "task_id": "x", "agent_id": "p", "task_type": "attack_test", "safety_violation": True,
        }])
        self.assertEqual(summary["groups"][0]["task_success_denominator"], 0)
        self.assertIsNone(plans[0]["parent_observation"]["task_success"])

    def test_task_failure_and_safety_violation_remain_distinct(self):
        summary, plans = self.analyze_rows("agent_execution", [{
            "task_id": "x", "agent_id": "p", "safety_pass": False, "task_success": False, "safety_violation": False,
        }])
        self.assertEqual(plans[0]["parent_observation"]["failures"], ["task_failure"])
        self.assertEqual(summary["groups"][0]["rate"], 0)

    def test_duplicate_identity_rejected_before_writing_output(self):
        row = {"id": "x", "model_name": "m", "api_status": "success", "passed": True}
        with self.assertRaisesRegex(ValueError, "Duplicate result identity"):
            self.analyze_rows("llm_qa", [row, row])

    def test_same_case_for_different_models_is_kept(self):
        summary, _ = self.analyze_rows("llm_qa", [
            {"id": "x", "model_name": model, "api_status": "success", "passed": True} for model in ["a", "b"]
        ])
        self.assertEqual(len(summary["groups"]), 2)

    def test_product_models_and_versions_get_distinct_candidate_ids(self):
        rows = [{"case_id": "x", "benchmark_id": "b", "run_id": "r", "status": "success",
                 "product": {"product_id": "p", "version": version, "product_model": model},
                 "asr_contribution": {"numerator": 1, "denominator": 1, "unit": "case"}}
                for model, version in [("a", "1"), ("b", "1"), ("a", "2")]]
        summary, plans = self.analyze_rows("agent_product", rows)
        self.assertEqual(len(summary["groups"]), 3)
        self.assertEqual(len({plan["candidate_id"] for plan in plans}), 3)
        _, repeated = self.analyze_rows("agent_product", rows)
        self.assertEqual([p["candidate_id"] for p in plans], [p["candidate_id"] for p in repeated])

    def test_malformed_identity_fields_are_unknown(self):
        summary, plans = self.analyze_rows("agent_product", [{
            "case_id": "x", "benchmark_id": "b", "status": "success",
            "product": {"product_id": "p", "product_model": ["a", "b"]},
            "asr_contribution": {"numerator": 1, "denominator": 1, "unit": "case"},
        }, {"case_id": "y", "product": {"product_id": ["p"]}}])
        self.assertEqual(summary["unknown_count"], 2)
        self.assertEqual(plans, [])

    def test_candidate_plan_has_lineage_and_cannot_be_called_accepted(self):
        summary, plans = self.analyze_rows("trace_judge", [{"task_id": "x", "agent_id": "judge", "safety_pass": False}])
        self.assertEqual(summary["admitted_candidate_count"], 0)
        self.assertEqual(plans[0]["stage"], "candidate_plan")
        self.assertFalse(plans[0]["admitted"])
        self.assertTrue(all(gate["status"] == "pending" for gate in plans[0]["gates"].values()))
        self.assertEqual(plans[0]["parent_observation"]["lineage"]["line"], 1)
        self.assertEqual(plans[0]["generation_brief"]["root_cause"], "unconfirmed")

    def test_output_cannot_overwrite_previous_analysis(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "results.jsonl"
            source.write_text("{}\n", encoding="utf-8")
            analyze("llm_qa", [source], root / "out")
            with self.assertRaises(FileExistsError):
                analyze("llm_qa", [source], root / "out")

    def test_malformed_results_preserve_unknown_counts(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "results.jsonl"
            source.write_text('bad json\n[]\n{"id":"a","product":[]}\n', encoding="utf-8")
            summary = analyze("agent_product", [source], root / "out")
            self.assertEqual(summary["unknown_count"], 3)


class RoutingTests(unittest.TestCase):
    def test_reserved_phases_emit_no_nonexistent_commands(self):
        phase_executor.configure_repository(Path(__file__).resolve().parents[1])
        for phase in ("phase6", "phase7", "phase8"):
            with self.subTest(phase=phase):
                record = phase_executor.build_phase_record(phase, None, True)
                self.assertEqual(record["implementation_status"], "reserved")
                self.assertEqual(record["commands"], [])
                checks = {check["name"]: check for check in record["checks"]}
                self.assertEqual(checks["phase_implementation"]["status"], "fail")

    def test_product_plan_respects_output_root_and_remains_dry_run(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            request = root / "request.json"
            request.touch()
            output = io.StringIO()
            with redirect_stdout(output):
                pipeline_main(["plan", "--track", "agent_product", "--request", str(request),
                               "--data-root", str(root), "--case-id", "b:x", "--out", str(root / "run")])
            plan = json.loads(output.getvalue())
            argv = plan["steps"][0]["argv"]
            self.assertEqual(argv[argv.index("--output-root") + 1], str((root / "run").resolve()))
            self.assertIn("--dry-run", argv)
            self.assertFalse((root / "run").exists())

    def test_execution_plan_forwards_native_configuration_without_running(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            tasks, rules, agents, models = [root / name for name in ("tasks.jsonl", "rules.jsonl", "agents.json", "models.json")]
            for path in (tasks, rules, agents, models):
                path.touch()
            output = io.StringIO()
            with redirect_stdout(output):
                pipeline_main(["plan", "--track", "agent_execution", "--tasks", str(tasks),
                               "--private-rules", str(rules), "--agents-config", str(agents),
                               "--models-config", str(models), "--out", str(root / "run")])
            plan = json.loads(output.getvalue())
            self.assertTrue(plan["prerequisites_present"])
            self.assertFalse(plan["executed"])
            argv = plan["steps"][0]["argv"]
            self.assertEqual(argv[argv.index("--agents-config") + 1], str(agents.resolve()))
            self.assertEqual(argv[argv.index("--models-config") + 1], str(models.resolve()))
            self.assertFalse((root / "run").exists())

    def test_agent_evolution_routes_both_phases(self):
        self.assertEqual(route_request("智能体失败分析与进化").phases, ["phase0", "phase9", "phase10"])

    def test_agent_phase_does_not_require_static_llm_dataset(self):
        root = Path(__file__).resolve().parents[1]
        phase_executor.configure_repository(root)
        checks = phase_executor.phase_checks("phase9", None, None)
        self.assertNotIn("canonical_dataset_exists", [check.name for check in checks])
        self.assertTrue(all(check.status == "pass" for check in checks))


if __name__ == "__main__":
    unittest.main()
