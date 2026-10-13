from __future__ import annotations

import hashlib
import importlib.util
import json
import re
import subprocess
import sys
import unittest
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from scripts import protocol_manager  # noqa: E402
from tests.temp_workspace import WorkspaceTempDir  # noqa: E402


class Phase12ToolTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = WorkspaceTempDir()
        self.root = self.temp_dir.__enter__()
        self.addCleanup(self.temp_dir.__exit__, None, None, None)

    def create_protocol(self) -> Path:
        protocol_dir = self.root / "protocol"
        protocol_manager.initialize_protocol(protocol_dir)
        card = protocol_dir / "evaluation_protocol_card.md"
        text = card.read_text(encoding="utf-8")
        text = text.replace("<describe the concrete evaluation goal>", "Evaluate test safety benchmark")
        text = text.replace("<model_id>", "test_model")
        text = text.replace("assets/samples/llm_qa_demo.jsonl", "data/canonical.jsonl")
        card.write_text(text, encoding="utf-8")
        registry = {
            "models": [
                {
                    "model_id": "test_model",
                    "provider": "test_provider",
                    "endpoint": "test_endpoint",
                    "model_version": "2026-05-17",
                    "run_enabled": True,
                    "notes": "synthetic test model",
                }
            ]
        }
        (protocol_dir / "model_registry.json").write_text(
            json.dumps(registry, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        self.write_canonical_schema(protocol_dir)
        return protocol_dir

    def write_canonical_schema(self, protocol_dir: Path) -> Path:
        schema_path = protocol_dir / "canonical_schema.yaml"
        schema = {
            "schema_name": "test_canonical",
            "version": "test",
            "core_schema": {
                "required": True,
                "fields": [
                    {"name": "id"},
                    {"name": "dataset_name"},
                    {"name": "source_id"},
                    {"name": "test_text"},
                    {"name": "reference_answer"},
                    {"name": "safety_l1_dimension"},
                    {"name": "safety_l2_harm_type"},
                ],
            },
            "extended_schema": {
                "fields": [
                    {"name": "raw_choices", "type": "list"},
                    {"name": "metadata", "type": "dict"},
                ]
            },
        }
        schema_path.write_text(yaml.safe_dump(schema, sort_keys=False, allow_unicode=True), encoding="utf-8")
        return schema_path

    def taxonomy_pair(self, protocol_dir: Path) -> tuple[str, str]:
        taxonomy = yaml.safe_load((protocol_dir / "taxonomy_registry.yaml").read_text(encoding="utf-8"))
        return taxonomy["l1_dimensions"][0]["name"], taxonomy["l2_dimensions"][0]["name"]

    def run_script(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, *args],
            cwd=self.root,
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
        )

    def test_source_intake_writes_reports_pending_manifests_and_failed_files(self) -> None:
        protocol_dir = self.create_protocol()
        schema_path = protocol_dir / "canonical_schema.yaml"
        l1, l2 = self.taxonomy_pair(protocol_dir)
        raw_dir = self.root / "raw"
        raw_dir.mkdir()
        (raw_dir / "bench.jsonl").write_text(
            json.dumps({"prompt": "unsafe prompt", "label": "REFUSE", "l1": l1, "l2": l2}, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        (raw_dir / "needs_review.jsonl").write_text(
            json.dumps({"prompt": "unsafe prompt", "label": "REFUSE", "risk_type": "privacy"}, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        (raw_dir / "broken.jsonl").write_text('{"prompt": "unterminated"\n', encoding="utf-8")
        evidence_dir = self.root / "evidence"
        evidence_dir.mkdir()
        (evidence_dir / "whitepaper.html").write_text("<html><body>evidence</body></html>", encoding="utf-8")

        output_dir = self.root / "work" / "phase1"
        completed = self.run_script(
            str(SCRIPTS / "source_intake.py"),
            "--benchmark-root",
            str(raw_dir),
            "--evidence-root",
            str(evidence_dir),
            "--output-dir",
            str(output_dir),
            "--protocol-dir",
            str(protocol_dir),
            "--canonical-schema",
            str(schema_path),
            "--format",
            "json",
        )
        self.assertEqual(completed.returncode, 0, completed.stderr or completed.stdout)
        result = json.loads(completed.stdout)
        self.assertEqual(result["ready"], 1)
        self.assertEqual(result["needs_review"], 1)
        self.assertEqual(result["failed"], 1)
        self.assertTrue((output_dir / "source_inventory.csv").is_file())
        catalog = json.loads((output_dir / "dataset_catalog.json").read_text(encoding="utf-8"))
        self.assertEqual(len(catalog["benchmarks"]), 1)
        self.assertEqual(catalog["benchmarks"][0]["status"], "ready")
        field_mapping_report = json.loads((output_dir / "reports" / "field_mapping_report.json").read_text(encoding="utf-8"))
        self.assertEqual(field_mapping_report["summary"]["ready"], 1)
        self.assertEqual(field_mapping_report["summary"]["needs_review"], 1)
        self.assertEqual(field_mapping_report["summary"]["failed"], 1)
        needs_review = json.loads((output_dir / "reports" / "needs_review.json").read_text(encoding="utf-8"))
        self.assertEqual(needs_review[0]["status"], "needs_review")
        failed_files = json.loads((output_dir / "reports" / "failed_files.json").read_text(encoding="utf-8"))
        self.assertEqual(failed_files[0]["status"], "failed")
        pending = yaml.safe_load((output_dir / "manifests" / "pending" / "needs_review.pending.yaml").read_text(encoding="utf-8"))
        self.assertEqual(pending["status"], "needs_review")
        self.assertIn("safety_l1_dimension", pending["missing_required_fields"])
        self.assertTrue((output_dir / "evidence_inventory.jsonl").read_text(encoding="utf-8").strip())

    def test_source_intake_requires_review_for_ambiguous_required_mapping(self) -> None:
        protocol_dir = self.create_protocol()
        schema_path = protocol_dir / "canonical_schema.yaml"
        l1, l2 = self.taxonomy_pair(protocol_dir)
        raw_dir = self.root / "raw"
        raw_dir.mkdir()
        (raw_dir / "ambiguous.jsonl").write_text(
            json.dumps(
                {
                    "prompt": "preferred by alias order",
                    "question": "second candidate",
                    "query": "third candidate",
                    "label": "REFUSE",
                    "l1": l1,
                    "l2": l2,
                },
                ensure_ascii=False,
            )
            + "\n",
            encoding="utf-8",
        )
        output_dir = self.root / "work" / "phase1"
        completed = self.run_script(
            str(SCRIPTS / "source_intake.py"),
            "--benchmark-root",
            str(raw_dir),
            "--output-dir",
            str(output_dir),
            "--protocol-dir",
            str(protocol_dir),
            "--canonical-schema",
            str(schema_path),
            "--format",
            "json",
        )
        self.assertEqual(completed.returncode, 0, completed.stderr or completed.stdout)
        result = json.loads(completed.stdout)
        self.assertEqual(result["ready"], 0)
        self.assertEqual(result["needs_review"], 1)
        pending = yaml.safe_load(
            (output_dir / "manifests" / "pending" / "ambiguous.pending.yaml").read_text(encoding="utf-8")
        )
        ambiguity = pending["ambiguous_mappings"]["test_text"]
        self.assertEqual(ambiguity["selected"], "prompt")
        self.assertEqual(ambiguity["candidates"], ["prompt", "question", "query"])
        self.assertIsNone(pending["field_mapping"]["test_text"])

        config_path = self.root / "intake.yaml"
        config_path.write_text(
            yaml.safe_dump(
                {"benchmark_overrides": {"ambiguous": {"field_mapping": {"test_text": "question"}}}},
                sort_keys=False,
            ),
            encoding="utf-8",
        )
        overridden_dir = self.root / "work" / "phase1-overridden"
        overridden = self.run_script(
            str(SCRIPTS / "source_intake.py"),
            "--benchmark-root",
            str(raw_dir),
            "--output-dir",
            str(overridden_dir),
            "--protocol-dir",
            str(protocol_dir),
            "--canonical-schema",
            str(schema_path),
            "--intake-config",
            str(config_path),
            "--format",
            "json",
        )
        self.assertEqual(overridden.returncode, 0, overridden.stderr or overridden.stdout)
        overridden_result = json.loads(overridden.stdout)
        self.assertEqual(overridden_result["ready"], 1)
        catalog = json.loads((overridden_dir / "dataset_catalog.json").read_text(encoding="utf-8"))
        self.assertEqual(catalog["benchmarks"][0]["field_mapping"]["test_text"], "question")

    def test_canonicalize_and_validate_recompute_stats_from_jsonl(self) -> None:
        protocol_dir = self.create_protocol()
        schema_path = protocol_dir / "canonical_schema.yaml"
        l1, l2 = self.taxonomy_pair(protocol_dir)
        raw_dir = self.root / "raw"
        raw_dir.mkdir()
        raw_path = raw_dir / "bench.jsonl"
        raw_path.write_text(
            "\n".join(
                [
                    json.dumps(
                        {
                            "prompt": "p1",
                            "label": "REFUSE",
                            "l1": l1,
                            "l2": l2,
                            "source_id": "original-row-id",
                            "raw_choices": ["A", "B"],
                            "metadata": {"split": "test"},
                        },
                        ensure_ascii=False,
                    ),
                    json.dumps(
                        {
                            "prompt": "p2",
                            "label": "ALLOW",
                            "l1": l1,
                            "l2": l2,
                            "raw_choices": ["C", "D"],
                            "metadata": {"split": "dev"},
                        },
                        ensure_ascii=False,
                    ),
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        phase1_dir = self.root / "work" / "phase1"
        intake = self.run_script(
            str(SCRIPTS / "source_intake.py"),
            "--benchmark-root",
            str(raw_dir),
            "--output-dir",
            str(phase1_dir),
            "--protocol-dir",
            str(protocol_dir),
            "--canonical-schema",
            str(schema_path),
        )
        self.assertEqual(intake.returncode, 0, intake.stderr or intake.stdout)

        canonical = self.root / "data" / "canonical.jsonl"
        stats = self.root / "output" / "doc" / "stats.json"
        canonicalize = self.run_script(
            str(SCRIPTS / "canonicalize_datasets.py"),
            "--catalog",
            str(phase1_dir / "dataset_catalog.json"),
            "--canonical-schema",
            str(schema_path),
            "--output",
            str(canonical),
            "--stats",
            str(stats),
            "--protocol-dir",
            str(protocol_dir),
            "--format",
            "json",
        )
        self.assertEqual(canonicalize.returncode, 0, canonicalize.stderr or canonicalize.stdout)
        result = json.loads(canonicalize.stdout)
        self.assertEqual(result["canonical_rows"], 2)
        self.assertEqual(result["quarantined_rows"], 0)
        self.assertTrue(result["protocol_drift"]["total_changed"])
        canonical_records = [
            json.loads(line) for line in canonical.read_text(encoding="utf-8").splitlines() if line.strip()
        ]
        records_by_prompt = {record["test_text"]: record for record in canonical_records}
        self.assertEqual(records_by_prompt["p1"]["source_id"], "original-row-id")
        self.assertEqual(records_by_prompt["p2"]["source_id"], "bench:00000002")
        self.assertEqual(records_by_prompt["p1"]["raw_choices"], ["A", "B"])
        self.assertEqual(records_by_prompt["p1"]["metadata"], {"split": "test"})
        membership_diff = json.loads((self.root / "work" / "phase2" / "membership_diff.json").read_text(encoding="utf-8"))
        self.assertEqual(membership_diff["added_total"], 2)

        digest = hashlib.sha256(canonical.read_bytes()).hexdigest().upper()
        card = protocol_dir / "evaluation_protocol_card.md"
        text = card.read_text(encoding="utf-8")
        text = re.sub(r"total: \d+", "total: 2", text)
        text = re.sub(r"sha256: [0-9A-F]{64}", f"sha256: {digest}", text)
        card.write_text(text, encoding="utf-8")

        validate = self.run_script(
            str(SCRIPTS / "canonical_validate.py"),
            "--canonical-schema",
            str(schema_path),
            "--canonical",
            str(canonical),
            "--stats",
            str(stats),
            "--protocol-dir",
            str(protocol_dir),
            "--format",
            "json",
        )
        self.assertEqual(validate.returncode, 0, validate.stderr or validate.stdout)
        validation = json.loads(validate.stdout)
        self.assertTrue(validation["valid"], validation["errors"])
        self.assertEqual(validation["computed_stats"]["total"], 2)
        self.assertTrue(validation["stats_matches"])

    def test_canonicalize_fails_when_source_hash_drifted_after_intake(self) -> None:
        protocol_dir = self.create_protocol()
        schema_path = protocol_dir / "canonical_schema.yaml"
        l1, l2 = self.taxonomy_pair(protocol_dir)
        raw_dir = self.root / "raw"
        raw_dir.mkdir()
        raw_path = raw_dir / "bench.jsonl"
        raw_path.write_text(
            json.dumps({"prompt": "original", "label": "REFUSE", "l1": l1, "l2": l2}, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        phase1_dir = self.root / "work" / "phase1"
        intake = self.run_script(
            str(SCRIPTS / "source_intake.py"),
            "--benchmark-root",
            str(raw_dir),
            "--output-dir",
            str(phase1_dir),
            "--protocol-dir",
            str(protocol_dir),
            "--canonical-schema",
            str(schema_path),
        )
        self.assertEqual(intake.returncode, 0, intake.stderr or intake.stdout)

        raw_path.write_text(
            json.dumps({"prompt": "mutated", "label": "REFUSE", "l1": l1, "l2": l2}, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        canonical = self.root / "data" / "canonical.jsonl"
        stats = self.root / "output" / "doc" / "stats.json"
        canonicalize = self.run_script(
            str(SCRIPTS / "canonicalize_datasets.py"),
            "--catalog",
            str(phase1_dir / "dataset_catalog.json"),
            "--canonical-schema",
            str(schema_path),
            "--output",
            str(canonical),
            "--stats",
            str(stats),
            "--protocol-dir",
            str(protocol_dir),
            "--format",
            "json",
        )
        self.assertEqual(canonicalize.returncode, 2)
        self.assertIn("source hash mismatch", canonicalize.stdout)

    def test_prepare_phase3_writes_smoke_subset_and_run_config(self) -> None:
        protocol_dir = self.create_protocol()
        l1, l2 = self.taxonomy_pair(protocol_dir)
        canonical = self.root / "data" / "canonical.jsonl"
        canonical.parent.mkdir(parents=True)
        records = []
        for index in range(1, 9):
            records.append(
                {
                    "id": f"row-{index}",
                    "dataset_name": f"dataset-{index % 3}",
                    "source_id": f"source-{index}",
                    "test_text": f"prompt {index}",
                    "reference_answer": "ALLOW" if index % 2 else "REFUSE",
                    "safety_l1_dimension": l1,
                    "safety_l2_harm_type": l2,
                    "type": "choice" if index % 2 else "generation",
                }
            )
        canonical.write_text(
            "\n".join(json.dumps(record, ensure_ascii=False) for record in records) + "\n",
            encoding="utf-8",
        )
        digest = hashlib.sha256(canonical.read_bytes()).hexdigest().upper()
        eval_script = self.root / "llm_eval" / "multi_model_rule_eval_prompt_only.py"
        eval_script.parent.mkdir(parents=True, exist_ok=True)
        eval_script.write_text((ROOT / "llm_eval" / "multi_model_rule_eval_prompt_only.py").read_text(encoding="utf-8"), encoding="utf-8")
        eval_digest = hashlib.sha256(eval_script.read_bytes()).hexdigest().upper()
        card = protocol_dir / "evaluation_protocol_card.md"
        text = card.read_text(encoding="utf-8")
        text = re.sub(r"total: \d+", "total: 8", text)
        text = re.sub(r"sha256: [0-9A-F]{64}", f"sha256: {digest}", text)
        text = re.sub(
            r"(evaluation_script:\n\s+path: .+\n\s+sha256: )[0-9A-F]{64}",
            rf"\g<1>{eval_digest}",
            text,
        )
        card.write_text(text, encoding="utf-8")

        output_dir = self.root / "work" / "phase3"
        smoke_output = self.root / "work" / "phase3" / "smoke.jsonl"
        run_config = output_dir / "run_config.yaml"
        completed = self.run_script(
            str(SCRIPTS / "prepare_phase3.py"),
            "--canonical",
            str(canonical),
            "--output-dir",
            str(output_dir),
            "--smoke-output",
            str(smoke_output),
            "--run-config",
            str(run_config),
            "--protocol-dir",
            str(protocol_dir),
            "--sample-size",
            "5",
            "--format",
            "json",
        )
        self.assertEqual(completed.returncode, 0, completed.stderr or completed.stdout)
        result = json.loads(completed.stdout)
        self.assertEqual(result["smoke_rows_written"], 5)
        subset = [json.loads(line) for line in smoke_output.read_text(encoding="utf-8").splitlines()]
        self.assertEqual(len(subset), 5)
        self.assertEqual(len({record["id"] for record in subset}), 5)
        config = yaml.safe_load(run_config.read_text(encoding="utf-8"))
        self.assertEqual(config["phase"], "phase3")
        self.assertEqual(config["evaluator_config"]["input_jsonl"], "work/phase3/smoke.jsonl")
        self.assertEqual(config["evaluator_config"]["run_mode"], "self_test")
        self.assertEqual(config["fields"]["prompt"], "test_text")
        report = json.loads((output_dir / "phase3_report.json").read_text(encoding="utf-8"))
        self.assertEqual(report["smoke_rows_written"], 5)
        self.assertIn("dataset_name", report["smoke_coverage"])

    def test_evaluator_accepts_external_run_config(self) -> None:
        evaluator_path = ROOT / "llm_eval" / "multi_model_rule_eval_prompt_only.py"
        spec = importlib.util.spec_from_file_location("phase3_eval_config_test", evaluator_path)
        self.assertIsNotNone(spec)
        self.assertIsNotNone(spec.loader)
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        original = dict(module.CONFIG)
        try:
            module.apply_external_config(
                {
                    "evaluator_config": {
                        "input_jsonl": "work/phase3/smoke.jsonl",
                        "run_root": "runs/llm-smoke",
                        "run_mode": "self_test",
                    },
                    "fields": {
                        "id": "canonical_id",
                        "prompt": "prompt_text",
                        "reference": "gold_answer",
                    },
                }
            )
            self.assertEqual(module.CONFIG["INPUT_JSONL"], "work/phase3/smoke.jsonl")
            self.assertEqual(module.CONFIG["RUN_ROOT"], "runs/llm-smoke")
            self.assertEqual(module.CONFIG["RUN_MODE"], "self_test")
            self.assertEqual(module.CONFIG["ID_FIELD"], "canonical_id")
            self.assertEqual(module.CONFIG["PROMPT_FIELD"], "prompt_text")
            self.assertEqual(module.CONFIG["REFERENCE_FIELD"], "gold_answer")
        finally:
            module.CONFIG.clear()
            module.CONFIG.update(original)


if __name__ == "__main__":
    unittest.main()
