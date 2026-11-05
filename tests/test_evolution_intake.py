"""待审进化接收的跨文件一致性与隐私边界检查。"""

from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from evolution.intake import receive_analysis
from scripts.result_analysis import analyze, write_json, write_jsonl


class EvolutionIntakeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "native.jsonl"
        self.analysis = self.root / "analysis"
        rows = [
            {
                "id": "bad",
                "model_name": "fixture",
                "dataset_name": "fixture",
                "run_id": "run-1",
                "api_status": "success",
                "passed": False,
                "evidence": {"oracle": "PRIVATE-CANARY"},
                "reason": "PRIVATE-CANARY",
            },
            {
                "id": "good",
                "model_name": "fixture",
                "dataset_name": "fixture",
                "run_id": "run-1",
                "api_status": "success",
                "passed": True,
            },
            {
                "id": "error",
                "model_name": "fixture",
                "dataset_name": "fixture",
                "run_id": "run-1",
                "api_status": "error",
                "passed": None,
            },
        ]
        write_jsonl(self.source, rows)
        analyze("llm_qa", [self.source], self.analysis)

    def receive(self, **changes):
        return receive_analysis(
            **{
                "track": "llm_qa",
                "analysis_manifest": str(self.analysis / "manifest.json"),
                "out": str(self.root / "intake"),
                **changes,
            }
        )

    def rows(self, name):
        return [
            json.loads(line)
            for line in (self.analysis / name).read_text().splitlines()
            if line.strip()
        ]

    def test_real_analysis_archived_without_private_content_or_admission(self):
        result = self.receive()
        self.assertEqual(result["status"], "pending_review")
        self.assertEqual(result["seed_count"], 1)
        self.assertEqual(result["admitted_candidate_count"], 0)
        archive = self.root / "intake"
        self.assertNotIn(
            "PRIVATE-CANARY", "".join(path.read_text() for path in archive.iterdir())
        )
        manifest = json.loads((archive / "manifest.json").read_text())
        self.assertFalse(manifest["native_evidence_verified"])
        expected = hashlib.sha256(
            (self.analysis / "failures.jsonl").read_bytes()
        ).hexdigest()
        self.assertEqual(manifest["inputs"]["failures.jsonl"]["sha256"], expected)
        request = json.loads((archive / "pending_requests.jsonl").read_text())
        self.assertFalse(request["admitted"])
        self.assertEqual(len(request["gates"]), 8)
        self.assertTrue(
            all(gate["status"] == "pending" for gate in request["gates"].values())
        )

    def test_loop_b_only_records_request(self):
        self.receive(loop="loop-b")
        request = json.loads((self.root / "intake/pending_requests.jsonl").read_text())
        self.assertFalse(request["target_candidate_registered"])
        self.assertFalse(request["automatic_promotion"])
        self.assertEqual(request["gates"], {})

    def test_no_failures_is_valid_including_invalid_only_population(self):
        for index, rows in enumerate(
            (
                [],
                [{"id": "bad", "model_name": "m", "api_status": "error"}],
                [
                    {
                        "id": "ok",
                        "model_name": "m",
                        "api_status": "success",
                        "passed": True,
                    }
                ],
            )
        ):
            source = self.root / f"source-{index}.jsonl"
            write_jsonl(source, rows)
            analysis = self.root / f"analysis-{index}"
            analyze("llm_qa", [source], analysis)
            result = self.receive(
                analysis_manifest=str(analysis / "manifest.json"),
                out=str(self.root / f"out-{index}"),
            )
            self.assertEqual(result["status"], "no_failures")
            self.assertEqual(result["seed_count"], 0)

    def test_duplicate_failure_identity_rejected_before_output(self):
        failures = self.rows("failures.jsonl")
        write_jsonl(self.analysis / "failures.jsonl", failures * 2)
        with self.assertRaisesRegex(ValueError, "重复"):
            self.receive()
        self.assertFalse((self.root / "intake").exists())

    def test_track_mismatch_rejected(self):
        with self.assertRaises(ValueError):
            self.receive(track="agent_execution")
        failures = self.rows("failures.jsonl")
        failures[0]["track"] = "agent_execution"
        write_jsonl(self.analysis / "failures.jsonl", failures)
        with self.assertRaises(ValueError):
            self.receive()

    def test_missing_file_and_malformed_json_rejected(self):
        path = self.analysis / "failures.jsonl"
        path.unlink()
        with self.assertRaises(FileNotFoundError):
            self.receive()
        path.write_text("{malformed")
        with self.assertRaises(ValueError):
            self.receive()
        self.assertFalse((self.root / "intake").exists())

    def test_count_or_signal_summary_mismatch_rejected(self):
        path = self.analysis / "summary.json"
        original = json.loads(path.read_text())
        for changes in (
            {"failure_observation_count": 0},
            {"valid_count": True},
            {"failure_signals": {}},
        ):
            write_json(path, {**original, **changes})
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.receive()

    def test_unknown_cannot_be_promoted_to_failure_seed(self):
        rows = self.rows("failures.jsonl")
        rows[0]["outcome"] = "unknown"
        write_jsonl(self.analysis / "failures.jsonl", rows)
        with self.assertRaises(ValueError):
            self.receive()

    def test_failure_must_match_observation_exactly(self):
        rows = self.rows("failures.jsonl")
        rows[0]["reason"] = "different"
        write_jsonl(self.analysis / "failures.jsonl", rows)
        with self.assertRaises(ValueError):
            self.receive()

    def test_plan_parent_gate_and_admission_tampering_rejected(self):
        path = self.analysis / "candidate_plans.jsonl"
        original = self.rows("candidate_plans.jsonl")
        variants = []
        altered = deepcopy(original)
        altered[0]["admitted"] = True
        variants.append(altered)
        altered = deepcopy(original)
        altered[0]["parent_observation"]["case_id"] = "other"
        variants.append(altered)
        altered = deepcopy(original)
        altered[0]["gates"]["review"]["status"] = "pass"
        variants.append(altered)
        variants.extend(([], original * 2))
        for rows in variants:
            write_jsonl(path, rows)
            with self.subTest(rows=rows), self.assertRaises(ValueError):
                self.receive()

    def test_nonempty_output_is_preserved(self):
        output = self.root / "intake"
        output.mkdir()
        (output / "keep").write_text("keep")
        with self.assertRaises(FileExistsError):
            self.receive()
        self.assertEqual((output / "keep").read_text(), "keep")

    def test_bad_manifest_status_or_admission_rejected(self):
        path = self.analysis / "manifest.json"
        original = json.loads(path.read_text())
        for changes in (
            {"status": "running"},
            {"automatic_admission": True},
            {"stage": "candidate_plan"},
        ):
            write_json(path, {**original, **changes})
            with self.assertRaises(ValueError):
                self.receive()

    def test_refusal_signal_preserved_without_generator_mapping(self):
        source = self.root / "refusal.jsonl"
        write_jsonl(
            source,
            [
                {
                    "id": "x",
                    "model_name": "m",
                    "api_status": "success",
                    "passed": False,
                    "is_refusal": True,
                }
            ],
        )
        analysis = self.root / "refusal-analysis"
        analyze("llm_qa", [source], analysis)
        self.receive(analysis_manifest=str(analysis / "manifest.json"))
        seed = json.loads((self.root / "intake/seed_refs.jsonl").read_text())
        self.assertIn("refusal", seed["observed_signals"])

    def test_distinct_subjects_and_runs_remain_distinct(self):
        source = self.root / "multi.jsonl"
        rows = [
            {
                "id": "same",
                "model_name": subject,
                "run_id": run,
                "api_status": "success",
                "passed": False,
            }
            for subject, run in (("a", "one"), ("b", "one"), ("a", "two"))
        ]
        write_jsonl(source, rows)
        analysis = self.root / "multi-analysis"
        analyze("llm_qa", [source], analysis)
        result = self.receive(analysis_manifest=str(analysis / "manifest.json"))
        self.assertEqual(result["seed_count"], 3)

    def test_all_tracks_keep_native_metric_directions(self):
        cases = {
            "agent_execution": {
                "task_id": "x",
                "agent_id": "fixture",
                "safety_pass": False,
                "safety_violation": True,
            },
            "trace_judge": {
                "task_id": "x",
                "agent_id": "fixture",
                "safety_pass": False,
                "safety_violation": True,
            },
            "agent_product": {
                "case_id": "x",
                "product": {"product_id": "fixture"},
                "status": "success",
                "asr_contribution": {
                    "numerator": 2,
                    "denominator": 3,
                    "unit": "retrieval_event",
                },
            },
        }
        for track, row in cases.items():
            source = self.root / f"{track}.jsonl"
            write_jsonl(source, [row])
            analysis = self.root / f"{track}-analysis"
            analyze(track, [source], analysis)
            result = self.receive(
                track=track,
                analysis_manifest=str(analysis / "manifest.json"),
                out=str(self.root / track),
            )
            self.assertEqual(result["seed_count"], 1)


if __name__ == "__main__":
    unittest.main()
