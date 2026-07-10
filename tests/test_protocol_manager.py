from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import protocol_manager  # noqa: E402
from tests.temp_workspace import WorkspaceTempDir  # noqa: E402


class ProtocolManagerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = WorkspaceTempDir()
        self.root = self.temp_dir.__enter__()
        self.addCleanup(self.temp_dir.__exit__, None, None, None)

    def create_protocol(self, name: str = "protocol") -> Path:
        protocol_dir = self.root / name
        protocol_manager.initialize_protocol(protocol_dir)

        card = protocol_dir / "evaluation_protocol_card.md"
        text = card.read_text(encoding="utf-8")
        text = text.replace("<describe the concrete evaluation goal>", "Evaluate V2 safety benchmark models")
        text = text.replace('"<model_id>"', "test_model")
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
        return protocol_dir

    def test_init_creates_required_artifacts(self) -> None:
        protocol_dir = self.root / "initialized"
        result = protocol_manager.initialize_protocol(protocol_dir)
        self.assertTrue(result["initialized"])
        self.assertEqual(set(protocol_manager.PROTOCOL_FILES), {path.name for path in protocol_dir.iterdir()})

    def test_unfilled_templates_fail_validation(self) -> None:
        protocol_dir = self.root / "unfilled"
        protocol_manager.initialize_protocol(protocol_dir)
        result = protocol_manager.validate_protocol_dir(protocol_dir)
        self.assertFalse(result["valid"])
        self.assertTrue(any(item["code"] == "placeholder" for item in result["errors"]))

    def test_valid_protocol_passes(self) -> None:
        result = protocol_manager.validate_protocol_dir(self.create_protocol())
        self.assertTrue(result["valid"], result["errors"])
        self.assertEqual(result["summary"]["taxonomy_version"], "safety_taxonomy_8x19")
        self.assertEqual(result["summary"]["metric_version"], "rule_eval")

    def test_refusal_semantics_are_frozen(self) -> None:
        protocol_dir = self.create_protocol()
        metric = protocol_dir / "metric_registry.yaml"
        text = metric.read_text(encoding="utf-8").replace(
            "refusal_scoring_mode: failure_and_separate_counter", "refusal_scoring_mode: separate"
        )
        metric.write_text(text, encoding="utf-8")
        result = protocol_manager.validate_protocol_dir(protocol_dir)
        self.assertFalse(result["valid"])
        self.assertTrue(any(item["path"] == "metric.refusal_scoring_mode" for item in result["errors"]))

    def test_required_reporting_slices_are_frozen(self) -> None:
        protocol_dir = self.create_protocol()
        metric = protocol_dir / "metric_registry.yaml"
        text = metric.read_text(encoding="utf-8").replace("  - id: safety_l2_harm_type", "  - id: omitted_l2")
        metric.write_text(text, encoding="utf-8")
        result = protocol_manager.validate_protocol_dir(protocol_dir)
        self.assertFalse(result["valid"])
        self.assertTrue(any(item["path"] == "metric.reporting_slices" for item in result["errors"]))

    def test_repo_root_verification_rejects_missing_bound_files(self) -> None:
        protocol_dir = self.create_protocol()
        result = protocol_manager.validate_protocol_dir(protocol_dir, self.root / "empty-host")
        self.assertFalse(result["valid"])
        self.assertEqual(
            2,
            sum(item["code"] == "missing_bound_file" for item in result["errors"]),
        )

    def test_repo_root_verification_accepts_matching_files(self) -> None:
        protocol_dir = self.create_protocol()
        host = self.root / "host"
        data_path = host / "eval_mvp_V2" / "data" / "master_normalized_dataset_v2_final.jsonl"
        script_path = host / "eval_mvp_V2" / "multi_model_rule_eval_prompt_only.py"
        data_path.parent.mkdir(parents=True)
        data_bytes = b'{"id":"one"}\n{"id":"two"}\n'
        script_bytes = b"print('synthetic evaluator')\n"
        data_path.write_bytes(data_bytes)
        script_path.write_bytes(script_bytes)

        card = protocol_dir / "evaluation_protocol_card.md"
        text = card.read_text(encoding="utf-8")
        text = text.replace("total: 668820", "total: 2")
        text = text.replace(
            "B2B6E8DBCDFE0C19D50444B3B684DB63023724A6DFB03902F44F43EB177E5647",
            hashlib.sha256(data_bytes).hexdigest().upper(),
        )
        text = text.replace(
            "1B20C9E8653FE08326F1643F59B1DA76BB1C3CC518B6702325C8C3F16366D89C",
            hashlib.sha256(script_bytes).hexdigest().upper(),
        )
        card.write_text(text, encoding="utf-8")

        result = protocol_manager.validate_protocol_dir(protocol_dir, host)
        self.assertTrue(result["valid"], result["errors"])
        self.assertEqual(result["verified_files"]["dataset"]["total"], 2)

    def test_identical_protocols_are_comparable(self) -> None:
        base = self.create_protocol("base")
        candidate = self.create_protocol("candidate")
        result = protocol_manager.compare_protocol_dirs(base, candidate)
        self.assertEqual(result["status"], "comparable")
        self.assertTrue(result["comparable"])

    def test_dataset_hash_change_is_incompatible(self) -> None:
        base = self.create_protocol("base")
        candidate = self.create_protocol("candidate")
        card = candidate / "evaluation_protocol_card.md"
        text = card.read_text(encoding="utf-8").replace(
            "B2B6E8DBCDFE0C19D50444B3B684DB63023724A6DFB03902F44F43EB177E5647",
            "A" * 64,
        )
        card.write_text(text, encoding="utf-8")
        result = protocol_manager.compare_protocol_dirs(base, candidate)
        self.assertEqual(result["status"], "incompatible")
        self.assertFalse(result["comparable"])
        self.assertEqual(result["breaking_changes"][0]["path"], "protocol.dataset")

    def test_phase_executor_runs_phase0_without_host_repo_layout(self) -> None:
        protocol_dir = self.create_protocol()
        command = [
            sys.executable,
            str(SCRIPTS / "phase_executor.py"),
            "--phase",
            "phase0",
            "--repo-root",
            str(self.root),
            "--protocol-dir",
            str(protocol_dir),
            "--format",
            "json",
        ]
        completed = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", check=False)
        self.assertEqual(completed.returncode, 0, completed.stderr or completed.stdout)
        result = json.loads(completed.stdout)
        checks = {item["name"]: item for item in result["phase_records"][0]["checks"]}
        self.assertEqual(checks["protocol_structure_valid"]["status"], "pass")


if __name__ == "__main__":
    unittest.main()
