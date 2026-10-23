from __future__ import annotations

import copy
import io
import json
import unittest
from unittest.mock import patch
from contextlib import redirect_stdout

from evolution.partitions import build_manifest, GenerationScope
from evolution.feedback_view import build_feedback
from evolution.strategies import select
from evolution.test_generator import generate
from evolution.admission import evaluate
from orchestrator.registry import content_hash
from tests.test_loop_a_evolution import candidate, evidence, policy, CANARIES, scope_for
from tests.temp_workspace import WorkspaceTempDir
from scripts.pipeline_cli import main


class PartitionTests(unittest.TestCase):
    def test_manifest_cli_exports_frozen_assignments_without_overwriting(self):
        with WorkspaceTempDir() as root:
            tasks = root/"tasks.jsonl"
            tasks.write_text('{"task_id":"x","user_goal":"public goal"}\n', encoding="utf-8")
            assignments = root/"assignments.json"
            assignments.write_text('{"x":{"family_id":"family-x","partition":"E"}}', encoding="utf-8")
            out = root/"partition-output"
            args = ["loop-a", "partitions", "--tasks", str(tasks), "--assignments", str(assignments), "--out", str(out)]
            with redirect_stdout(io.StringIO()):
                self.assertEqual(main(args), 0)
            scope = GenerationScope(json.loads((out/"partitions.json").read_text(encoding="utf-8")))
            self.assertEqual(scope.authorize({"task_id": "x", "user_goal": "public goal"})["partition"], "E")
            with patch('sys.stderr', new=io.StringIO()), self.assertRaises(SystemExit):
                main(args)

    def test_malformed_ids_and_assignments_have_explicit_errors(self):
        _, _, scope = self.fixture()
        for task in ({"task_id": []}, {"task_id": ""}, None):
            with self.subTest(task=task), self.assertRaises(ValueError):
                scope.authorize(task)
        with self.assertRaises(ValueError):
            build_manifest([], [])
        with self.assertRaises(ValueError):
            build_manifest([{"task_id": "x"}], {"x": None})

    def fixture(self):
        tasks = [{"task_id": p, "user_goal": "public goal "+p} for p in ("E", "V", "H")]
        manifest = build_manifest(tasks, {t["task_id"]: {"family_id": "family-"+t["task_id"], "partition": t["task_id"]} for t in tasks})
        return tasks, manifest, GenerationScope(manifest)

    def test_manifest_order_is_deterministic(self):
        tasks, manifest, scope = self.fixture()
        assignments = {t["task_id"]: {"family_id": "family-"+t["task_id"], "partition": t["task_id"]} for t in tasks}
        self.assertEqual(manifest, build_manifest(list(reversed(tasks)), assignments))
        self.assertEqual(scope.authorize(tasks[0])["partition"], "E")

    def test_validation_and_holdout_block_before_feedback_is_read(self):
        tasks, _, scope = self.fixture()
        for parent in tasks[1:]:
            with self.subTest(partition=parent["task_id"]), patch('evolution.strategies.validate_feedback') as check:
                with self.assertRaisesRegex(ValueError, "generation_partition_excluded"):
                    select([parent], {}, strategy="feedback", seed=0, limit=1, scope=scope)
                check.assert_not_called()
            with self.assertRaisesRegex(ValueError, "generation_partition_excluded"):
                generate(parent, family="family-"+parent["task_id"], feedback={}, pointer="/user_goal", replacement="new",
                         space="wording", policy=policy()["transform"], canaries=CANARIES, scope=scope)

    def test_mixed_batch_rejects_before_any_feedback(self):
        tasks, _, scope = self.fixture()
        with patch('evolution.strategies.validate_feedback') as check:
            with self.assertRaises(ValueError):
                select(tasks, {}, strategy="random", seed=0, limit=1, scope=scope)
            check.assert_not_called()

    def test_cross_partition_family_and_duplicate_ids_are_invalid(self):
        tasks, _, _ = self.fixture()
        with self.assertRaisesRegex(ValueError, "family_overlap"):
            build_manifest(tasks, {t["task_id"]: {"family_id": "same-family", "partition": t["task_id"]} for t in tasks})
        with self.assertRaisesRegex(ValueError, "duplicate"):
            build_manifest([tasks[0], tasks[0]], {"E": {"family_id": "family-E", "partition": "E"}})

    def test_parent_relabel_or_content_swap_is_blocked(self):
        tasks, _, scope = self.fixture()
        with self.assertRaisesRegex(ValueError, "family_mismatch"):
            scope.authorize(tasks[0], family="different")
        swapped = {**tasks[0], "user_goal": tasks[2]["user_goal"]}
        with self.assertRaisesRegex(ValueError, "parent_hash_mismatch"):
            scope.authorize(swapped)
        with self.assertRaisesRegex(ValueError, "not_registered"):
            scope.authorize({"task_id": "unknown"})

    def test_corrupt_manifest_and_mutation_of_input_are_handled(self):
        tasks, manifest, scope = self.fixture()
        corrupt = copy.deepcopy(manifest)
        corrupt["entries"][0]["partition"] = "H"
        with self.assertRaisesRegex(ValueError, "hash_mismatch"):
            GenerationScope(corrupt)
        manifest["entries"][0]["partition"] = "H"
        self.assertEqual(scope.authorize(tasks[0])["partition"], "E")

    def test_feedback_with_same_id_but_different_text_is_blocked(self):
        task = {"task_id": "parent", "user_goal": "Look up the current status.", "benchmark": "fixture"}
        view = build_feedback(task, {"failures": []}, CANARIES)
        view["public_task"]["user_goal"] = "holdout content"
        with self.assertRaisesRegex(ValueError, "feedback_parent_mismatch"):
            generate(task, family="family", feedback=view, pointer="/user_goal", replacement="new", space="wording",
                     policy=policy()["transform"], canaries=CANARIES, scope=scope_for())

    def test_admission_rejects_different_frozen_registry(self):
        c = candidate()
        parent = c["parent_task"]
        scope = GenerationScope(build_manifest([parent], {parent["task_id"]: {"family_id": "family", "partition": "H"}}))
        result = evaluate(c, evidence(c), policy(), canaries=CANARIES, existing_texts=[], holdout_families=set(),
                          admitted_counts={}, scope=scope, mode="fixture")
        self.assertEqual(result["status"], "rejected")
        self.assertEqual(result["gates"]["holdout_isolation"]["reason_code"], "generation_partition_excluded")

    def test_empty_private_fields_are_not_public(self):
        for field in ("evaluation", "attack_goal"):
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, "private_fields"):
                build_manifest([{"task_id": "x", "user_goal": "goal", field: {}}], {"x": {"family_id": "family", "partition": "E"}})
