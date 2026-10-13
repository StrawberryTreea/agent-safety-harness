from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from product_eval.adaptive import Lease, QueueStore
from tools.run_adaptive import (
    BENCHMARKS,
    PACKAGE_ROOT,
    aggregate_results,
    campaign_judge_preflight,
    controller_lock,
    initialize_queue,
    load_worker_states,
    controller_main,
    record_controller_event,
    persist_attempt_diagnostic,
    stop_requests_needed,
    validate_product_release_gates,
    validate_case_artifacts,
    worker_command,
)


def write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def make_valid_case(
    root: Path,
    product: str = "codex",
    model: str = "deepseek-v4-flash",
    benchmark_id: str = "injecagent",
) -> None:
    root.mkdir(parents=True, exist_ok=True)
    for name in ("prompt.txt", "mcp.json", "native_result.json", "product_stdout.jsonl", "final_response.txt"):
        (root / name).write_text("{}", encoding="utf-8")
    (root / "product_stderr.txt").write_text("", encoding="utf-8")
    write_json(
        root / "product_run.json",
        {
            "status": "completed",
            "timed_out": False,
            "duration_seconds": 2.5,
            "product": {"product_id": product, "product_model": model},
        },
    )
    write_json(
        root / "case_result.json",
        {
            "benchmark_id": benchmark_id,
            "case_id": f"{benchmark_id}:test:1",
            "status": "failure",
            "asr_contribution": {"numerator": 0, "denominator": 1, "unit": "case"},
        },
    )
    if benchmark_id == "agentharm":
        write_json(
            root / "judge_evidence.json",
            {
                "judge": {
                    "profile_id": "deepseek_v4_flash",
                    "type": "openai",
                    "model": "deepseek-v4-flash",
                    "base_url": "https://api.deepseek.com",
                    "api_key_env": "DEEPSEEK_API_KEY",
                    "temperature": 0.0,
                },
                "calls": [],
            },
        )
    if benchmark_id == "saber":
        container_id = "a" * 64
        write_json(
            root / "official_judgment.json",
            {"id": "test:1", "harmful": False, "judge_err": ""},
        )
        (root / "saber_mcp_events.jsonl").write_text(
            json.dumps({"event_type": "runtime_started", "container_id": container_id}) + "\n",
            encoding="utf-8",
        )
        write_json(
            root / "saber_runtime_cleanup.json",
            {
                "schema_version": "0.1",
                "observed_container_ids": [container_id],
                "removed_container_ids": [],
                "already_absent_container_ids": [container_id],
            },
        )


class AdaptiveRuntimeTest(unittest.TestCase):
    def test_qoder_worker_uses_qualified_byok_entrypoint(self) -> None:
        command = worker_command(Path("config.json"), "qoder", "qoder-001-test")
        self.assertEqual(Path(command[1]).name, "run_adaptive_qoder_byok.py")
        codex = worker_command(Path("config.json"), "codex", "codex-001-test")
        self.assertEqual(Path(codex[1]).name, "run_adaptive.py")

    def test_saber_requires_judgment_and_cleanup_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "case"
            make_valid_case(root, benchmark_id="saber")
            self.assertTrue(
                validate_case_artifacts(
                    root, expected_product="codex", expected_model="deepseek-v4-flash"
                )[0]
            )
            (root / "saber_runtime_cleanup.json").unlink()
            valid, kind, detail, _ = validate_case_artifacts(
                root, expected_product="codex", expected_model="deepseek-v4-flash"
            )
            self.assertFalse(valid)
            self.assertEqual(kind, "missing_artifact")
            self.assertIn("saber_runtime_cleanup.json", detail)

    def test_saber_accepts_clean_no_tool_execution_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "case"
            make_valid_case(root, benchmark_id="saber")
            (root / "saber_mcp_events.jsonl").write_text(
                json.dumps({"event_type": "server_started", "task_id": "test:1"}) + "\n",
                encoding="utf-8",
            )
            write_json(
                root / "saber_runtime_cleanup.json",
                {
                    "schema_version": "0.1",
                    "observed_container_ids": [],
                    "removed_container_ids": [],
                    "already_absent_container_ids": [],
                },
            )
            self.assertTrue(
                validate_case_artifacts(
                    root, expected_product="codex", expected_model="deepseek-v4-flash"
                )[0]
            )

    def test_agentharm_is_supported_and_requires_locked_judge_evidence(self) -> None:
        self.assertIn("agentharm", BENCHMARKS)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "case"
            make_valid_case(root, benchmark_id="agentharm")
            self.assertTrue(
                validate_case_artifacts(
                    root, expected_product="codex", expected_model="deepseek-v4-flash"
                )[0]
            )
            (root / "judge_evidence.json").unlink()
            valid, kind, _, _ = validate_case_artifacts(
                root, expected_product="codex", expected_model="deepseek-v4-flash"
            )
            self.assertFalse(valid)
            self.assertEqual(kind, "missing_artifact")

    def test_agentdyn_requires_clean_events_and_attack_delivery(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "case"
            make_valid_case(root, benchmark_id="agentdyn")
            delivery = {
                "schema_version": "0.1",
                "injection_key": "injection_emails_0",
                "payload_sha256": "a" * 64,
                "normalized_payload_sha256": "b" * 64,
                "tool_name": "get_received_emails",
                "call_index": 1,
                "normalized_output_sha256": "c" * 64,
            }
            write_json(
                root / "native_result.json",
                {
                    "error": None,
                    "pre_environment": {},
                    "post_environment": {},
                    "function_calls": [],
                    "attack_delivery": [delivery],
                },
            )
            (root / "agentdyn_mcp_events.jsonl").write_text(
                json.dumps({"event_type": "attack_delivery", **delivery}) + "\n",
                encoding="utf-8",
            )
            self.assertTrue(
                validate_case_artifacts(
                    root, expected_product="codex", expected_model="deepseek-v4-flash"
                )[0]
            )
            with (root / "agentdyn_mcp_events.jsonl").open("a", encoding="utf-8") as handle:
                handle.write(json.dumps({"event_type": "server_error", "error": "boom"}) + "\n")
            valid, kind, detail, _ = validate_case_artifacts(
                root, expected_product="codex", expected_model="deepseek-v4-flash"
            )
            self.assertFalse(valid)
            self.assertEqual(kind, "invalid_artifact")
            self.assertIn("server_error", detail)

    def test_agentdyn_rejects_missing_attack_delivery(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "case"
            make_valid_case(root, benchmark_id="agentdyn")
            write_json(
                root / "native_result.json",
                {
                    "error": None,
                    "pre_environment": {},
                    "post_environment": {},
                    "function_calls": [],
                    "attack_delivery": [],
                },
            )
            (root / "agentdyn_mcp_events.jsonl").write_text("", encoding="utf-8")
            valid, kind, detail, _ = validate_case_artifacts(
                root, expected_product="codex", expected_model="deepseek-v4-flash"
            )
            self.assertFalse(valid)
            self.assertEqual(kind, "invalid_artifact")
            self.assertIn("not observably delivered", detail)

    def test_artifact_validation_rejects_missing_invalid_and_route_drift(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "case"
            valid, kind, _, _ = validate_case_artifacts(
                root, expected_product="codex", expected_model="deepseek-v4-flash"
            )
            self.assertFalse(valid)
            self.assertEqual(kind, "missing_artifact")
            make_valid_case(root)
            self.assertTrue(
                validate_case_artifacts(
                    root, expected_product="codex", expected_model="deepseek-v4-flash"
                )[0]
            )
            result = json.loads((root / "case_result.json").read_text(encoding="utf-8"))
            result["asr_contribution"] = {"numerator": 2, "denominator": 7, "unit": "retrieval"}
            write_json(root / "case_result.json", result)
            self.assertTrue(
                validate_case_artifacts(
                    root, expected_product="codex", expected_model="deepseek-v4-flash"
                )[0]
            )
            run = json.loads((root / "product_run.json").read_text(encoding="utf-8"))
            run["product"]["product_model"] = "wrong-model"
            write_json(root / "product_run.json", run)
            self.assertEqual(
                validate_case_artifacts(
                    root, expected_product="codex", expected_model="deepseek-v4-flash"
                )[1],
                "model_route",
            )

    def test_resume_skips_only_complete_scored_clean_case(self) -> None:
        temp_parent = PACKAGE_ROOT / ".codex_tmp"
        temp_parent.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=temp_parent) as tmp:
            root = Path(tmp)
            selection = root / "selection.jsonl"
            selection.write_text(
                json.dumps({"benchmark_id": "injecagent", "case_id": "injecagent:test:1"}) + "\n",
                encoding="utf-8",
            )
            run_root = root / "runs"
            case = run_root / "codex" / "injecagent" / "injecagent__test__1"
            make_valid_case(case)
            config = {
                "products": ["codex"],
                "case_index": str(selection.relative_to(PACKAGE_ROOT)).replace("\\", "/"),
                "run_root": str(run_root.relative_to(PACKAGE_ROOT)).replace("\\", "/"),
                "logical_shards": 32,
                "tested_profiles": {"codex": {"model": "deepseek-v4-flash"}},
            }
            store = initialize_queue(config, root / "campaign")
            self.assertEqual(store.counts("codex")["complete"], 1)

    def test_resume_rejects_result_from_another_release(self) -> None:
        temp_parent = PACKAGE_ROOT / ".codex_tmp"
        temp_parent.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=temp_parent) as tmp:
            root = Path(tmp)
            selection = root / "selection.jsonl"
            selection.write_text(
                json.dumps({"benchmark_id": "injecagent", "case_id": "injecagent:test:1"})
                + "\n",
                encoding="utf-8",
            )
            run_root = root / "runs"
            case = run_root / "codex" / "injecagent" / "injecagent__test__1"
            make_valid_case(case)
            result = json.loads((case / "case_result.json").read_text(encoding="utf-8"))
            result["evaluation_metadata"] = {
                "release_id": "old-release",
            }
            write_json(case / "case_result.json", result)
            config = {
                "products": ["codex"],
                "case_index": str(selection.relative_to(PACKAGE_ROOT)).replace("\\", "/"),
                "run_root": str(run_root.relative_to(PACKAGE_ROOT)).replace("\\", "/"),
                "logical_shards": 32,
                "release_id": "current-release",
                "tested_profiles": {"codex": {"model": "deepseek-v4-flash"}},
            }
            store = initialize_queue(config, root / "campaign")
            self.assertEqual(store.counts("codex")["complete"], 0)
            self.assertEqual(store.counts("codex")["pending"], 1)

    def test_multi_execution_api_campaign_requires_judge_credential(self) -> None:
        temp_parent = PACKAGE_ROOT / ".codex_tmp"
        temp_parent.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=temp_parent) as tmp:
            selection = Path(tmp) / "selection.jsonl"
            selection.write_text(
                json.dumps(
                    {"benchmark_id": "agentharm", "case_id": "agentharm:test_public:17-1"}
                )
                + "\n",
                encoding="utf-8",
            )
            config = {
                "products": ["codex", "claude_code"],
                "case_index": str(selection.relative_to(PACKAGE_ROOT)).replace("\\", "/"),
                "judge_profile": {
                    "model": "deepseek-v4-flash",
                    "base_url": "https://api.deepseek.com",
                },
            }
            with patch.dict("os.environ", {}, clear=True):
                with self.assertRaisesRegex(RuntimeError, "DEEPSEEK_API_KEY"):
                    campaign_judge_preflight(config)

    def test_qoder_release_gate_blocks_adaptive_initialization(self) -> None:
        validate_product_release_gates(["codex", "claude_code", "qoder"])

    def test_codebuddy_release_gate_is_ready(self) -> None:
        validate_product_release_gates(["codebuddy"])

    def test_codebuddy_artifacts_require_authenticated_deepseek_route(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "case"
            make_valid_case(root, product="codebuddy")
            route = {
                "route_mode": "audited_local_forwarder",
                "provider": "deepseek",
                "provider_model": "deepseek-v4-flash",
                "api_key_supplied": True,
                "effective_model": "custom-local:deepseek-v4-flash",
                "observed_custom_model_requests": 1,
                "observed_requests": [
                    {
                        "host": "api.deepseek.com",
                        "authorization_supplied": True,
                        "request_model": "deepseek-v4-flash",
                        "response_status": 200,
                        "tools_supplied": True,
                    }
                ],
            }
            write_json(root / "product_network_route.json", route)
            self.assertTrue(
                validate_case_artifacts(
                    root,
                    expected_product="codebuddy",
                    expected_model="deepseek-v4-flash",
                )[0]
            )
            route["observed_requests"][0]["authorization_supplied"] = False
            write_json(root / "product_network_route.json", route)
            valid, kind, _, _ = validate_case_artifacts(
                root,
                expected_product="codebuddy",
                expected_model="deepseek-v4-flash",
            )
            self.assertFalse(valid)
            self.assertEqual(kind, "model_route")

    def test_qoder_artifact_validation_requires_observed_official_route(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "case"
            make_valid_case(root, product="qoder", model="deepseek-v4-flash")
            configured = {
                "route_mode": "qoder_byok_custom_pool",
                "provider": "deepseek",
                "provider_model": "deepseek-v4-flash-pg",
                "canonical_model": "deepseek-v4-flash",
                "custom_model_id": "model_1786147854337_zp7v4vd",
                "byok_type": "pg",
                "api_key_supplied": True,
                "custom_model_request_lines": 0,
                "qoder_managed_generation_lines": 1,
            }
            write_json(root / "product_network_route.json", configured)
            valid, kind, _, _ = validate_case_artifacts(
                root, expected_product="qoder", expected_model="deepseek-v4-flash"
            )
            self.assertFalse(valid)
            self.assertEqual(kind, "model_route")
            configured.update(
                {
                    "custom_model_request_lines": 1,
                    "qoder_managed_generation_lines": 1,
                }
            )
            write_json(root / "product_network_route.json", configured)
            self.assertTrue(
                validate_case_artifacts(
                    root, expected_product="qoder", expected_model="deepseek-v4-flash"
                )[0]
            )

    def test_deferred_aggregation_writes_complete_cases_only(self) -> None:
        temp_parent = PACKAGE_ROOT / ".codex_tmp"
        temp_parent.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=temp_parent) as tmp:
            root = Path(tmp)
            campaign = root / "campaign"
            run_root = root / "runs"
            config = {
                "campaign_id": "test-campaign",
                "products": ["codex"],
                "run_root": str(run_root.relative_to(PACKAGE_ROOT)).replace("\\", "/"),
                "campaign_dir_abs": str(campaign),
            }
            store = QueueStore(campaign / "queue.sqlite3")
            store.add_cases(
                "codex",
                [
                    {"benchmark_id": "injecagent", "case_id": "injecagent:test:1"},
                    {"benchmark_id": "injecagent", "case_id": "injecagent:test:2"},
                ],
            )
            case = run_root / "codex" / "injecagent" / "injecagent__test__1"
            make_valid_case(case)
            store.mark_existing_complete("codex", "injecagent", "injecagent:test:1", "failure")
            report = aggregate_results(config, store)
            destination = run_root / "codex" / "injecagent" / "case_result.jsonl"
            rows = destination.read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(rows), 1)
            self.assertEqual(report["products"]["codex"]["injecagent"]["selected"], 1)
            self.assertFalse(any(destination.parent.glob("*.tmp")))

    def test_single_controller_lock_rejects_a_live_duplicate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            campaign = Path(tmp)
            with controller_lock(campaign):
                with self.assertRaisesRegex(RuntimeError, "already running"):
                    with controller_lock(campaign):
                        pass

    def test_stale_worker_pid_reuse_is_not_treated_as_live(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            campaign = Path(tmp)
            write_json(
                campaign / "workers" / "qoder-old.json",
                {
                    "worker_id": "qoder-old",
                    "pid": __import__("os").getpid(),
                    "product": "qoder",
                    "status": "running",
                },
            )
            states = load_worker_states(campaign)
            self.assertEqual(len(states), 1)
            self.assertFalse(states[0]["alive"])

    def test_controller_event_export_is_atomic_and_readable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            campaign = Path(tmp)
            store = QueueStore(campaign / "queue.sqlite3")
            record_controller_event(
                store,
                campaign,
                "scale_window",
                {"codebuddy": {"from": 2, "to": 3, "reason": "forced_scale_up"}},
            )
            rows = [
                json.loads(line)
                for line in (campaign / "controller_events.jsonl")
                .read_text(encoding="utf-8")
                .splitlines()
            ]
            self.assertEqual(rows[0]["event_type"], "scale_window")
            self.assertEqual(rows[0]["payload"]["codebuddy"]["to"], 3)
            self.assertFalse(any(campaign.glob("controller_events.jsonl.*.tmp")))

    def test_controller_records_completed_final_metadata(self) -> None:
        temp_parent = PACKAGE_ROOT / ".codex_tmp"
        temp_parent.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=temp_parent) as tmp:
            root = Path(tmp)
            campaign = root / "campaign"
            campaign.mkdir()
            selection = root / "selection.jsonl"
            selection.write_text(
                json.dumps(
                    {"benchmark_id": "injecagent", "case_id": "injecagent:test:1"}
                )
                + "\n",
                encoding="utf-8",
            )
            run_root = root / "runs"
            case = run_root / "codex" / "injecagent" / "injecagent__test__1"
            make_valid_case(case)
            config = {
                "campaign_id": "controller-final-metadata-test",
                "products": ["codex"],
                "case_index": str(selection.relative_to(PACKAGE_ROOT)).replace("\\", "/"),
                "run_root": str(run_root.relative_to(PACKAGE_ROOT)).replace("\\", "/"),
                "logical_shards": 32,
                "lease_seconds": 30.0,
                "max_attempts": 1,
                "initial_workers": 2,
                "global_max_workers": 10,
                "window_seconds": 300.0,
                "cooldown_seconds": 300.0,
                "poll_seconds": 0.001,
                "tested_profiles": {"codex": {"model": "deepseek-v4-flash"}},
            }
            config_path = campaign / "config.json"
            write_json(config_path, config)
            self.assertEqual(
                controller_main(SimpleNamespace(config=config_path)),
                0,
            )
            metadata = json.loads(
                (campaign / "controller.json").read_text(encoding="utf-8")
            )
            self.assertEqual(metadata["status"], "completed")
            self.assertEqual(metadata["exit_code"], 0)
            self.assertEqual(metadata["counts"]["codex"]["complete"], 1)
            event_types = [
                json.loads(line)["event_type"]
                for line in (campaign / "controller_events.jsonl")
                .read_text(encoding="utf-8")
                .splitlines()
            ]
            self.assertEqual(
                event_types,
                ["controller_running", "controller_finished"],
            )

    def test_controller_records_preflight_failure_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            campaign = Path(tmp)
            config_path = campaign / "config.json"
            write_json(config_path, {"campaign_id": "failed-controller"})
            with patch(
                "tools.run_adaptive.campaign_judge_preflight",
                side_effect=RuntimeError("credential unavailable"),
            ):
                with self.assertRaises(RuntimeError):
                    controller_main(SimpleNamespace(config=config_path))
            metadata = json.loads(
                (campaign / "controller.json").read_text(encoding="utf-8")
            )
            self.assertEqual(metadata["status"], "failed")
            self.assertEqual(metadata["error_type"], "RuntimeError")
            self.assertNotIn("credential unavailable", json.dumps(metadata))

    def test_failed_attempt_diagnostic_is_redacted_and_atomic(self) -> None:
        with tempfile.TemporaryDirectory(dir=PACKAGE_ROOT / ".codex_tmp") as tmp:
            campaign = Path(tmp)
            lease = Lease(
                token="token",
                product="codebuddy",
                benchmark_id="saber",
                case_id="saber:A_info_042",
                logical_shard=5,
                attempt=2,
            )
            completed = subprocess.CompletedProcess(
                args=["runner"],
                returncode=1,
                stdout="judge failed",
                stderr="authorization test-secret-value",
            )
            with patch.dict(
                os.environ,
                {"DEEPSEEK_API_KEY": "test-secret-value"},
                clear=False,
            ):
                path = persist_attempt_diagnostic(campaign, lease, completed)
            payload = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(payload["returncode"], 1)
            self.assertIn("[redacted]", payload["stderr"])
            self.assertNotIn("test-secret-value", path.read_text(encoding="utf-8"))
            self.assertFalse(any(path.parent.glob("*.tmp")))

    def test_scale_down_does_not_duplicate_pending_stop_requests(self) -> None:
        self.assertEqual(
            stop_requests_needed(
                live_workers=3, desired_workers=2, already_requested=0
            ),
            1,
        )
        self.assertEqual(
            stop_requests_needed(
                live_workers=3, desired_workers=2, already_requested=1
            ),
            0,
        )
        self.assertEqual(
            stop_requests_needed(
                live_workers=2, desired_workers=2, already_requested=1
            ),
            0,
        )


if __name__ == "__main__":
    unittest.main()
