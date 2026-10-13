from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
import unittest

from tools.build_expansion_candidates import (
    _proportional_quotas,
    build_candidates,
    mcptox_candidates,
    openagentsafety_candidates,
    ras_native_candidates,
    stratified_smoke_selection,
)


PACKAGE_ROOT = Path(__file__).resolve().parents[1]


def external_data_root() -> Path:
    configured = os.environ.get("AGENT_EVAL_DATA_ROOT")
    if not configured:
        raise unittest.SkipTest("Set AGENT_EVAL_DATA_ROOT to run source data integration tests")
    root = Path(configured).expanduser()
    if not root.is_dir():
        raise unittest.SkipTest(f"Source data directory is unavailable: {root}")
    return root


class ExpansionCandidateTest(unittest.TestCase):
    def test_agentdyn_manifests_pin_version_suites_and_tool_counts(self) -> None:
        manifest_root = PACKAGE_ROOT / "manifests" / "agentdyn_v1_2_2"
        expected = {"shopping": 39, "github": 34, "dailylife": 27}
        self.assertEqual(
            {path.stem for path in manifest_root.glob("*.json")}, set(expected)
        )
        for suite, tool_count in expected.items():
            manifest = json.loads(
                (manifest_root / f"{suite}.json").read_text(encoding="utf-8")
            )
            self.assertEqual(manifest["benchmark_version"], "v1.2.2")
            self.assertEqual(manifest["suite"], suite)
            self.assertEqual(len(manifest["tools"]), tool_count)

    def test_mcptox_flattens_server_groups_without_response_records(self) -> None:
        source = [
            {
                "Server_1": {
                    "server_name": "Server",
                    "tool_name": "poisoned",
                    "query": "do work",
                    "tool_content": "malicious instruction",
                    "security risk": "Data Tampering",
                    "paradigm": "Template-1",
                    "tool_address": "def_tool/1.py",
                }
            },
            {
                "Other_1": {
                    "server_name": "Other",
                    "tool_name": "poisoned_other",
                    "query": "do other work",
                    "tool_content": "other malicious instruction",
                    "security risk": "Privacy Leakage",
                    "paradigm": "Template-2",
                    "tool_address": "def_tool/2.py",
                }
            },
        ]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "pure_tool.json"
            path.write_text(json.dumps(source), encoding="utf-8")
            rows = mcptox_candidates(path)
        self.assertEqual([row["native_case_id"] for row in rows], ["Server_1", "Other_1"])
        self.assertTrue(all(row["qualification_status"] == "candidate_only" for row in rows))

    def test_ras_selection_is_deterministic_complete_and_disjoint(self) -> None:
        source = [
            {
                "index": index,
                "target_index": index % 5,
                "attack": [{"mode": "tool_input", "tool": f"tool_{index % 3}"}],
            }
            for index in range(50)
        ]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "attack_tasks.json"
            path.write_text(json.dumps(source), encoding="utf-8")
            first = ras_native_candidates(path, target_count=17, seed="fixed")
            second = ras_native_candidates(path, target_count=17, seed="fixed")
        first_ids = [row["case_id"] for row in first]
        self.assertEqual(first_ids, [row["case_id"] for row in second])
        self.assertEqual(len(first_ids), 17)
        self.assertEqual(len(set(first_ids)), 17)
        self.assertTrue(set(first_ids).isdisjoint({"ras_eval_native:missing"}))

    def test_openagentsafety_excludes_only_fully_unnamed_rows(self) -> None:
        csv_text = (
            "Task Name,Description,Tools,Risk Category,Intent Category (user's intent),"
            "Environment intent (NPC's behaviour),path\n"
            "valid,description,terminal,risk,Benign,,tasks/safety-valid\n"
            ",,terminal,risk,Malicious,,\n"
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "tasks.csv"
            path.write_text(csv_text, encoding="utf-8")
            rows = openagentsafety_candidates(path)
        self.assertEqual([row["native_case_id"] for row in rows], ["valid"])

    def test_openagentsafety_audits_required_execution_artifacts(self) -> None:
        csv_text = (
            "Task Name,Description,Tools,Risk Category,Intent Category (user's intent),"
            "Environment intent (NPC's behaviour),path\n"
            "valid,description,terminal,risk,Benign,,tasks/safety-valid\n"
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "tasks.csv"
            path.write_text(csv_text, encoding="utf-8")
            task_root = root / "tasks"
            task_dir = task_root / "safety-valid"
            (task_dir / "utils").mkdir(parents=True)
            (task_dir / "task.md").write_text("task", encoding="utf-8")
            (task_dir / "checkpoints.md").write_text("checks", encoding="utf-8")
            rows = openagentsafety_candidates(path, task_root=task_root)
            self.assertFalse(rows[0]["metadata"]["source_artifacts_complete"])
            self.assertEqual(
                rows[0]["metadata"]["missing_source_artifacts"],
                ["utils/evaluator.py", "utils/dependencies.yml"],
            )
            (task_dir / "utils" / "evaluator.py").write_text("pass", encoding="utf-8")
            (task_dir / "utils" / "dependencies.yml").write_text("", encoding="utf-8")
            rows = openagentsafety_candidates(path, task_root=task_root)
        self.assertTrue(rows[0]["metadata"]["source_artifacts_complete"])

    def test_hamilton_quotas_sum_to_target_and_respect_population(self) -> None:
        counts = {0: 3, 1: 7, 2: 11}
        quotas = _proportional_quotas(counts, 13)
        self.assertEqual(sum(quotas.values()), 13)
        self.assertTrue(all(0 <= quotas[key] <= counts[key] for key in counts))

    def test_smoke_selection_is_deterministic_stratified_and_disjoint(self) -> None:
        rows = [
            {
                "benchmark_id": "agentdyn",
                "case_id": f"agentdyn:{suite}:{index}",
                "metadata": {"suite": suite},
            }
            for suite, count in (("shopping", 18), ("github", 18), ("dailylife", 20))
            for index in range(count)
        ]
        first = stratified_smoke_selection(rows)
        second = stratified_smoke_selection(list(reversed(rows)))
        self.assertEqual(first, second)
        self.assertEqual(len(first), 5)
        self.assertEqual(len({row["case_id"] for row in first}), 5)
        self.assertEqual(
            set(row["metadata"]["suite"] for row in first),
            {"shopping", "github", "dailylife"},
        )

    def test_openagentsafety_smoke_excludes_incomplete_tasks(self) -> None:
        rows = [
            {
                "benchmark_id": "openagentsafety",
                "case_id": f"openagentsafety:{index}",
                "metadata": {
                    "intent_category": "Benign" if index % 2 else "Malicious",
                    "source_artifacts_complete": index >= 2,
                    "evaluator_contract_complete": index >= 3,
                },
            }
            for index in range(12)
        ]
        selected = stratified_smoke_selection(rows)
        self.assertEqual(len(selected), 5)
        self.assertTrue(
            all(row["metadata"]["source_artifacts_complete"] for row in selected)
        )
        self.assertTrue(
            all(row["metadata"]["evaluator_contract_complete"] for row in selected)
        )

    def test_toolemu_execution_contract_sources_are_listed(self) -> None:
        _, manifest = build_candidates(external_data_root())
        locks = manifest["sources"]
        self.assertIn("toolemu", locks)
        self.assertIn("toolemu_toolkits", locks)
        self.assertIn("toolemu_simulator_prompt", locks)
        self.assertIn("toolemu_safety_prompt", locks)
        self.assertEqual(
            manifest["qualification_reviews"]["toolemu"]["qualification_status"],
            "blocked_runtime_adapter",
        )

    def test_openagentsafety_contract_and_task_tree_are_listed(self) -> None:
        _, manifest = build_candidates(external_data_root())
        review = manifest["qualification_reviews"]["openagentsafety"]
        self.assertEqual(review["source_complete_count"], 348)
        self.assertEqual(review["evaluator_contract_complete_count"], 338)
        self.assertEqual(review["qualification_status"], "blocked_harness_runtime")
        task_lock = manifest["sources"]["openagentsafety_runtime_source"]
        self.assertEqual(task_lock["glob"], "**/*")


if __name__ == "__main__":
    unittest.main()
