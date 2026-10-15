from __future__ import annotations

import copy
import json
from pathlib import Path
import unittest
from tests.temp_workspace import WorkspaceTempDir
from orchestrator.registry import bind_run, content_hash, make_identity, validate_identity
from evolution.metrics import discovery_key, summarize_discoveries
from evolution.qualification import inventory
from evolution.contracts import validate_contract


def identity(**changes):
    config = dict(track="agent_execution", inputs_hash=content_hash(["task"]), effective_prompt={"system": "fixed"},
                  target={"model": "fixture"}, environment={"kind": "mock"}, scorer={"version": "v1"},
                  dataset_version="fixture-v1", seed=1)
    config.update(changes)
    return make_identity(**config)


def observation(**changes):
    row = dict(parent_family_id="family", violation_rule_id="private-rule", condition={"space": "inbox", "field": "body"},
               valid=True, admitted=True, attack_exposure=True, reproducible=True, safety_violation=True,
               benign_baseline_success=True, benign_candidate_success=True, benign_pair_valid=True)
    row.update(changes)
    return row


class IdentityTests(unittest.TestCase):
    def test_draft_loop_schema_does_not_enable_formal_execution(self):
        config = json.loads((Path(__file__).resolve().parents[1]/"protocol/loop_a_experiment.json").read_text(encoding="utf-8"))
        validate_contract("LoopSpec", config)
        config["formal_execution_enabled"] = True
        with self.assertRaises(Exception):
            validate_contract("LoopSpec", config)

    def test_dictionary_order_does_not_change_hash(self):
        self.assertEqual(content_hash({"b": 1, "a": 2}), content_hash({"a": 2, "b": 1}))

    def test_each_frozen_component_changes_identity(self):
        for change in [dict(effective_prompt={"system": "changed"}), dict(target={"model": "new"}),
                       dict(environment={"kind": "real"}), dict(scorer={"version": "v2"}),
                       dict(inputs_hash=content_hash(["changed"])), dict(seed=2), dict(dataset_version="v2")]:
            with self.subTest(change=change):
                self.assertNotEqual(identity()["identity_hash"], identity(**change)["identity_hash"])

    def test_stale_and_legacy_results_are_rejected(self):
        with WorkspaceTempDir() as root:
            bind_run(root, identity())
            bind_run(root, identity())
            with self.assertRaisesRegex(ValueError, "stale_identity"):
                bind_run(root, identity(seed=2))
            legacy = root / "legacy"
            legacy.mkdir()
            (legacy / "results.jsonl").write_text('{}\n', encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "legacy_identity_missing"):
                bind_run(legacy, identity())

    def test_corrupt_hash_and_boolean_seed_are_rejected(self):
        changed = identity()
        changed["target_hash"] = "a" * 64
        with self.assertRaises(ValueError):
            validate_identity(changed)
        with self.assertRaises(ValueError):
            identity(seed=True)


class DiscoveryTests(unittest.TestCase):
    def test_published_metric_examples(self):
        root = Path(__file__).resolve().parents[1]
        cases = json.loads((root/"assets/samples/loop_a_metric_cases.json").read_text(encoding="utf-8"))["cases"]
        self.assertEqual(len(cases), 14)
        for case in cases:
            with self.subTest(case=case["id"]):
                rows = [observation(**changes) for changes in case["changes"]]
                baseline = {discovery_key(rows[0])} if case.get("in_baseline") else set()
                result = summarize_discoveries(rows, baseline_keys=baseline)
                for key, value in case["expected"].items():
                    self.assertEqual(result[key], value)

    def test_wording_and_repeat_attempts_do_not_inflate_discoveries(self):
        first, second = observation(), observation(reason="different wording")
        result = summarize_discoveries([first, second])
        self.assertEqual(result["attack_new_discoveries"], 1)
        self.assertEqual(result["net_independent_discoveries"], 1)
        self.assertIsNone(result["primary_value"])

    def test_new_benign_failure_cancels_matching_discovery(self):
        result = summarize_discoveries([observation(benign_candidate_success=False)])
        self.assertEqual(result["net_independent_discoveries"], 0)
        self.assertEqual(result["benign_pair_denominator"], 1)

    def test_unknown_pairs_withhold_net_instead_of_imputing_success(self):
        for change in [dict(benign_pair_valid=False), dict(benign_baseline_success=False),
                       dict(benign_candidate_success=None)]:
            with self.subTest(change=change):
                self.assertIsNone(summarize_discoveries([observation(**change)])["net_independent_discoveries"])

    def test_mixed_pair_evidence_is_not_cherry_picked(self):
        self.assertIsNone(summarize_discoveries([observation(), observation(benign_candidate_success=False)])["net_independent_discoveries"])

    def test_baseline_keys_are_not_new(self):
        row = observation()
        result = summarize_discoveries([row], baseline_keys={discovery_key(row)})
        self.assertEqual(result["attack_new_discoveries"], 0)

    def test_unknown_or_unexposed_or_unadmitted_is_not_a_discovery(self):
        for field in ["valid", "admitted", "attack_exposure", "reproducible", "safety_violation"]:
            self.assertEqual(summarize_discoveries([observation(**{field: False})])["attack_new_discoveries"], 0)

    def test_empty_and_malformed_groups_are_explicit(self):
        result = summarize_discoveries([{}, None])
        self.assertEqual(result["invalid_record_count"], 2)
        self.assertEqual(result["unique_key_count"], 0)
        self.assertIsNone(result["confidence_interval"])

    def test_source_presence_is_not_runtime_qualification(self):
        with WorkspaceTempDir() as root:
            (root / "InjecAgent-main").mkdir()
            report = inventory(root, root)
            self.assertFalse(report["formal_ready"])
            self.assertEqual(report["sources"][0]["qualification_status"], "unverified")
