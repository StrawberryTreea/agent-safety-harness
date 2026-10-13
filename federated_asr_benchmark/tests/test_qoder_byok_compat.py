from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from product_eval.qoder_byok_compat import (
    QODER_BYOK_CUSTOM_MODEL_ID,
    QODER_BYOK_PROVIDER_MODEL,
    QODER_BYOK_TYPE,
    QoderByokProductRunner,
    resolve_qoder_byok_model,
    validate_qoder_byok_runtime_model,
)
from product_eval.runner import ProductIdentity
from tools.run_adaptive_qoder_byok import validate_qoder_byok_case_artifacts


PACKAGE_ROOT = Path(__file__).resolve().parents[1]


class QoderByokCompatTests(unittest.TestCase):
    def make_runner(self) -> QoderByokProductRunner:
        return QoderByokProductRunner(
            ProductIdentity(
                "qoder",
                "qodercli",
                "1.0.48",
                QODER_BYOK_CUSTOM_MODEL_ID,
            )
        )

    def test_writes_exact_native_custom_model_shape(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary)
            with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "test-secret"}):
                self.make_runner()._configure_qoder_byok_state(destination)
            settings = json.loads((destination / "settings.json").read_text(encoding="utf-8"))
            model = settings["modelConfigs"]["customModels"][0]
            self.assertEqual(model["key"], QODER_BYOK_CUSTOM_MODEL_ID)
            self.assertEqual(model["model"], QODER_BYOK_PROVIDER_MODEL)
            self.assertEqual(model["type"], QODER_BYOK_TYPE)
            self.assertEqual(model["apiKey"], "test-secret")
            self.assertNotIn("providers", settings)

    def test_command_explicitly_loads_isolated_user_settings(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            command = self.make_runner().build_command(
                workspace=root / "workspace",
                mcp_config=root / "mcp.json",
                mcp_server_command={"test": ["python", "server.py"]},
                allowed_tools=["mcp__test__tool"],
                final_response_file=root / "final.txt",
                isolated_state_dir=root / "state",
            )
            self.assertIn("--settings", command)
            self.assertIn("--setting-sources", command)
            self.assertIn(QODER_BYOK_CUSTOM_MODEL_ID, command)

    def test_model_policy_maps_custom_id_to_canonical_model(self) -> None:
        self.assertEqual(
            resolve_qoder_byok_model("qoder", "ignored"),
            QODER_BYOK_CUSTOM_MODEL_ID,
        )

    def test_all_runnable_qoder_requests_pin_qualified_cli(self) -> None:
        request_paths = sorted((PACKAGE_ROOT / "examples" / "requests").rglob("qoder_*.json"))
        self.assertTrue(request_paths)
        for path in request_paths:
            request = json.loads(path.read_text(encoding="utf-8"))
            with self.subTest(path=path.relative_to(PACKAGE_ROOT).as_posix()):
                self.assertEqual(request["product"]["version"], "1.0.48")
                self.assertIn("qoder-1.0.48-", request["run_id"])

    def test_adaptive_validator_accepts_exact_byok_route(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for name in (
                "prompt.txt",
                "mcp.json",
                "native_result.json",
                "product_stdout.jsonl",
                "product_stderr.txt",
                "final_response.txt",
            ):
                (root / name).write_text("", encoding="utf-8")
            (root / "product_run.json").write_text(
                json.dumps(
                    {
                        "status": "completed",
                        "timed_out": False,
                        "product": {
                            "product_id": "qoder",
                            "product_model": "deepseek-v4-flash",
                        },
                    }
                ),
                encoding="utf-8",
            )
            (root / "case_result.json").write_text(
                json.dumps(
                    {
                        "status": "failure",
                        "benchmark_id": "injecagent",
                        "asr_contribution": {"numerator": 0, "denominator": 1},
                    }
                ),
                encoding="utf-8",
            )
            route = {
                "route_mode": "qoder_byok_custom_pool",
                "provider": "deepseek",
                "provider_model": QODER_BYOK_PROVIDER_MODEL,
                "canonical_model": "deepseek-v4-flash",
                "custom_model_id": QODER_BYOK_CUSTOM_MODEL_ID,
                "byok_type": QODER_BYOK_TYPE,
                "api_key_supplied": True,
                "custom_model_request_lines": 2,
                "qoder_managed_generation_lines": 2,
            }
            (root / "product_network_route.json").write_text(
                json.dumps(route), encoding="utf-8"
            )
            valid, error_kind, _, _ = validate_qoder_byok_case_artifacts(
                root,
                expected_product="qoder",
                expected_model="deepseek-v4-flash",
            )
            self.assertTrue(valid)
            self.assertIsNone(error_kind)

            route["api_key_supplied"] = False
            (root / "product_network_route.json").write_text(
                json.dumps(route), encoding="utf-8"
            )
            valid, error_kind, _, _ = validate_qoder_byok_case_artifacts(
                root,
                expected_product="qoder",
                expected_model="deepseek-v4-flash",
            )
            self.assertFalse(valid)
            self.assertEqual(error_kind, "model_route")
        self.assertEqual(
            validate_qoder_byok_runtime_model(
                "qoder",
                QODER_BYOK_CUSTOM_MODEL_ID,
                QODER_BYOK_CUSTOM_MODEL_ID,
            ),
            "deepseek-v4-flash",
        )

    def test_adaptive_validator_rejects_saber_without_cleanup_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for name in (
                "prompt.txt",
                "mcp.json",
                "native_result.json",
                "product_stdout.jsonl",
                "product_stderr.txt",
                "final_response.txt",
            ):
                (root / name).write_text("", encoding="utf-8")
            (root / "product_run.json").write_text(
                json.dumps(
                    {
                        "status": "completed",
                        "timed_out": False,
                        "product": {
                            "product_id": "qoder",
                            "product_model": "deepseek-v4-flash",
                        },
                    }
                ),
                encoding="utf-8",
            )
            (root / "case_result.json").write_text(
                json.dumps(
                    {
                        "status": "failure",
                        "benchmark_id": "saber",
                        "case_id": "saber:test:1",
                        "asr_contribution": {"numerator": 0, "denominator": 1},
                    }
                ),
                encoding="utf-8",
            )
            (root / "product_network_route.json").write_text(
                json.dumps(
                    {
                        "route_mode": "qoder_byok_custom_pool",
                        "provider": "deepseek",
                        "provider_model": QODER_BYOK_PROVIDER_MODEL,
                        "canonical_model": "deepseek-v4-flash",
                        "custom_model_id": QODER_BYOK_CUSTOM_MODEL_ID,
                        "byok_type": QODER_BYOK_TYPE,
                        "api_key_supplied": True,
                        "custom_model_request_lines": 1,
                        "qoder_managed_generation_lines": 1,
                    }
                ),
                encoding="utf-8",
            )
            (root / "official_judgment.json").write_text(
                json.dumps({"id": "test:1", "harmful": False, "judge_err": ""}),
                encoding="utf-8",
            )
            (root / "saber_mcp_events.jsonl").write_text(
                json.dumps({"event_type": "runtime_started", "container_id": "a" * 64}) + "\n",
                encoding="utf-8",
            )
            valid, kind, detail, _ = validate_qoder_byok_case_artifacts(
                root,
                expected_product="qoder",
                expected_model="deepseek-v4-flash",
            )
            self.assertFalse(valid)
            self.assertEqual(kind, "missing_artifact")
            self.assertIn("saber_runtime_cleanup.json", detail)


if __name__ == "__main__":
    unittest.main()
