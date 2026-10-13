from __future__ import annotations

import argparse
from collections import Counter
from contextlib import contextmanager
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import shutil
import statistics
import subprocess
import sys
import threading
import time
from typing import Any
import uuid

import psutil


PACKAGE_ROOT = Path(__file__).resolve().parents[1]
RUN_ONE = PACKAGE_ROOT / "tools" / "run_product.py"
PRODUCTS = ("codex", "claude_code", "qoder")
SUPPORTED_PRODUCTS = (*PRODUCTS, "codebuddy")
BENCHMARKS = (
    "injecagent",
    "msb",
    "agentdojo",
    "agentdyn",
    "agentharm",
    "agentleak",
    "agentpoison_strategyqa",
    "saber",
)
VALID_RESULTS = {"success", "failure", "excluded"}
sys.path.insert(0, str(PACKAGE_ROOT))

from product_eval.adaptive import AdaptivePolicy, Lease, QueueStore, WindowMetrics
from product_eval.api_judge import OpenAICompatibleJudge, load_immutable_judge_profile
from product_eval.qoder_byok_compat import (
    QODER_BYOK_CUSTOM_MODEL_ID,
    QODER_BYOK_PROVIDER,
    QODER_BYOK_PROVIDER_MODEL,
    QODER_BYOK_TYPE,
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json_atomic(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def write_jsonl_atomic(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        "".join(
            json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n"
            for row in rows
        ),
        encoding="utf-8",
    )
    temporary.replace(path)


def write_controller_metadata(
    campaign_dir: Path,
    lock: dict[str, Any],
    status: str,
    **fields: Any,
) -> None:
    write_json_atomic(
        campaign_dir / "controller.json",
        {
            **lock,
            "status": status,
            "updated_at": utc_now(),
            **fields,
        },
    )


def record_controller_event(
    store: QueueStore,
    campaign_dir: Path,
    event_type: str,
    payload: dict[str, Any],
) -> int:
    event_id = store.record_event(event_type, payload)
    write_jsonl_atomic(campaign_dir / "controller_events.jsonl", store.events())
    return event_id


def selection_rows(path: Path) -> list[dict[str, Any]]:
    rows = [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    seen: set[tuple[str, str]] = set()
    for row in rows:
        key = (row["benchmark_id"], row["case_id"])
        if key in seen:
            raise ValueError(f"duplicate selected case: {key}")
        if row["benchmark_id"] not in BENCHMARKS:
            raise ValueError(f"unsupported benchmark: {row['benchmark_id']}")
        seen.add(key)
    return rows


def relative_to_root(path: Path) -> str:
    return str(path.resolve().relative_to(PACKAGE_ROOT)).replace("\\", "/")


def resolve_config_path(config: dict[str, Any], key: str) -> Path:
    return (PACKAGE_ROOT / config[key]).resolve()


def validate_product_release_gates(
    products: list[str], *, allow_candidate_products: bool = False
) -> None:
    profiles = read_json(PACKAGE_ROOT / "release" / "tested_profiles.json")["products"]
    blocked = {}
    for product in products:
        profile = profiles.get(product)
        status = profile.get("status", "ready") if profile else "missing_profile"
        if status == "ready" or (allow_candidate_products and status == "candidate_smoke"):
            continue
        blocked[product] = status
    if blocked:
        raise RuntimeError(f"tested-product release gates are not ready: {blocked}")


def campaign_judge_preflight(config: dict[str, Any]) -> dict[str, Any] | None:
    selected = selection_rows(resolve_config_path(config, "case_index"))
    judged_executions = (
        sum(row["benchmark_id"] in {"saber", "agentharm"} for row in selected)
        * len(config["products"])
    )
    if judged_executions <= 1:
        return None
    locked = load_immutable_judge_profile(
        PACKAGE_ROOT / "release" / "evaluation_config.json",
        PACKAGE_ROOT / "benchmark_registry.json",
    )
    configured = config.get("judge_profile") or {}
    drift = {
        key: value
        for key, value in configured.items()
        if key in locked and locked[key] != value
    }
    if drift:
        raise RuntimeError(f"campaign judge profile drifted: {drift}")
    return OpenAICompatibleJudge(locked).preflight()


def case_root(config: dict[str, Any], product: str, benchmark: str, case_id: str) -> Path:
    return (
        resolve_config_path(config, "run_root")
        / product
        / benchmark
        / case_id.replace(":", "__")
    )


def classify_artifact_error(text: str) -> str:
    lowered = text.lower()
    if "429" in lowered or "rate limit" in lowered:
        return "rate_limit"
    if any(code in lowered for code in (" 500", " 502", " 503", " 504", "5xx")):
        return "server_5xx"
    if "timed out" in lowered or "timeout" in lowered:
        return "timeout"
    if "model policy violation" in lowered or "model-route" in lowered:
        return "model_route"
    if "docker" in lowered or "mcp" in lowered or "transport closed" in lowered:
        return "docker_mcp"
    return "invalid_artifact"


def redact_runner_diagnostic(text: str) -> str:
    redacted = text
    for name in (
        "DEEPSEEK_API_KEY",
        "ANTHROPIC_AUTH_TOKEN",
        "OPENAI_API_KEY",
        "CODEBUDDY_API_KEY",
    ):
        value = os.environ.get(name)
        if value:
            redacted = redacted.replace(value, "[redacted]")
    return redacted[-10_000:]


def persist_attempt_diagnostic(
    campaign_dir: Path,
    lease: Lease,
    completed: subprocess.CompletedProcess[str],
) -> Path:
    root = (
        campaign_dir
        / "attempt_diagnostics"
        / lease.product
        / lease.benchmark_id
        / lease.case_id.replace(":", "__")
    )
    path = root / f"attempt_{lease.attempt:03d}.json"
    write_json_atomic(
        path,
        {
            "schema_version": "0.1",
            "product": lease.product,
            "benchmark_id": lease.benchmark_id,
            "case_id": lease.case_id,
            "attempt": lease.attempt,
            "returncode": completed.returncode,
            "stdout": redact_runner_diagnostic(completed.stdout or ""),
            "stderr": redact_runner_diagnostic(completed.stderr or ""),
            "recorded_at": utc_now(),
        },
    )
    return path


def validate_saber_case_artifacts(
    root: Path, result: dict[str, Any]
) -> tuple[bool, str | None, str | None]:
    required = ("official_judgment.json", "saber_mcp_events.jsonl", "saber_runtime_cleanup.json")
    missing = [name for name in required if not (root / name).is_file()]
    if missing:
        return False, "missing_artifact", f"missing SABER artifacts: {', '.join(missing)}"

    try:
        judgment = read_json(root / "official_judgment.json")
        cleanup = read_json(root / "saber_runtime_cleanup.json")
        events = [
            json.loads(line)
            for line in (root / "saber_mcp_events.jsonl").read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        return False, "invalid_artifact", f"invalid SABER evidence: {exc}"

    if judgment.get("judge_err") or not isinstance(judgment.get("harmful"), bool):
        return False, "invalid_artifact", "invalid SABER official judgment"
    expected_native_id = str(result.get("case_id", "")).split(":", 1)[-1]
    if judgment.get("id") != expected_native_id:
        return False, "invalid_artifact", "SABER judgment case ID mismatch"

    started = [
        str(event.get("container_id"))
        for event in events
        if event.get("event_type") == "runtime_started" and event.get("container_id")
    ]
    observed = [str(value) for value in cleanup.get("observed_container_ids") or []]
    removed = [str(value) for value in cleanup.get("removed_container_ids") or []]
    absent = [str(value) for value in cleanup.get("already_absent_container_ids") or []]
    if cleanup.get("schema_version") != "0.1":
        return False, "invalid_artifact", "unsupported SABER cleanup evidence schema"
    if observed != started or len(set(observed)) != len(observed):
        return False, "invalid_artifact", "SABER cleanup container evidence mismatch"
    if any(value not in observed for value in absent) or any(
        not any(value.startswith(observed_id) for observed_id in observed) for value in removed
    ):
        return False, "invalid_artifact", "SABER cleanup contains an unobserved runtime"
    accounted = set(absent)
    for observed_id in observed:
        if observed_id in accounted or any(value.startswith(observed_id) for value in removed):
            continue
        return False, "invalid_artifact", "SABER cleanup did not account for every runtime"
    return True, None, None


def validate_case_artifacts(
    root: Path,
    *,
    expected_product: str,
    expected_model: str,
    expected_release_id: str | None = None,
    require_evaluation_metadata: bool = False,
) -> tuple[bool, str | None, str | None, dict[str, Any] | None]:
    required = (
        "prompt.txt",
        "mcp.json",
        "product_run.json",
        "native_result.json",
        "case_result.json",
        "product_stdout.jsonl",
        "product_stderr.txt",
        "final_response.txt",
    )
    missing = [name for name in required if not (root / name).is_file()]
    if missing:
        detail = f"missing artifacts: {', '.join(missing)}"
        return False, "missing_artifact", detail, None
    try:
        run = read_json(root / "product_run.json")
        result = read_json(root / "case_result.json")
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        return False, "invalid_artifact", f"unreadable JSON: {exc}", None
    if run.get("status") != "completed" or run.get("timed_out"):
        detail = str(run.get("error") or f"product status={run.get('status')}")
        return False, classify_artifact_error(detail), detail, result
    product = run.get("product") or {}
    if product.get("product_id") != expected_product or product.get("product_model") != expected_model:
        detail = f"effective route {product.get('product_id')}/{product.get('product_model')}"
        return False, "model_route", detail, result
    if expected_product == "qoder":
        route_path = root / "product_network_route.json"
        if not route_path.is_file():
            return False, "model_route", "missing product_network_route.json", result
        try:
            route = read_json(route_path)
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            return False, "model_route", f"invalid product_network_route.json: {exc}", result
        expected_route = {
            "route_mode": "qoder_byok_custom_pool",
            "provider": "deepseek",
            "provider_model": QODER_BYOK_PROVIDER_MODEL,
            "canonical_model": expected_model,
            "custom_model_id": QODER_BYOK_CUSTOM_MODEL_ID,
            "byok_type": QODER_BYOK_TYPE,
            "api_key_supplied": True,
        }
        mismatches = {
            key: route.get(key)
            for key, value in expected_route.items()
            if route.get(key) != value
        }
        custom_requests = int(route.get("custom_model_request_lines") or 0)
        control_plane_generation = int(route.get("qoder_managed_generation_lines") or 0)
        if mismatches or custom_requests < 1 or control_plane_generation != custom_requests:
            detail = (
                "Qoder BYOK route invalid: "
                f"mismatches={mismatches}, custom_model_requests={custom_requests}, "
                f"control_plane_generation={control_plane_generation}"
            )
            return False, "model_route", detail, result
    if expected_product == "codebuddy":
        route_path = root / "product_network_route.json"
        if not route_path.is_file():
            return False, "model_route", "missing product_network_route.json", result
        try:
            route = read_json(route_path)
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            return False, "model_route", f"invalid product_network_route.json: {exc}", result
        expected_route = {
            "route_mode": "audited_local_forwarder",
            "provider": "deepseek",
            "provider_model": "deepseek-v4-flash",
            "api_key_supplied": True,
            "effective_model": "custom-local:deepseek-v4-flash",
        }
        mismatches = {
            key: route.get(key)
            for key, value in expected_route.items()
            if route.get(key) != value
        }
        requests = route.get("observed_requests") or []
        invalid_requests = [
            request
            for request in requests
            if not isinstance(request, dict)
            or request.get("host") != "api.deepseek.com"
            or request.get("authorization_supplied") is not True
            or request.get("request_model") != "deepseek-v4-flash"
            or request.get("response_status") != 200
            or request.get("tools_supplied") is not True
        ]
        custom_requests = int(route.get("observed_custom_model_requests") or 0)
        if mismatches or custom_requests < 1 or len(requests) != custom_requests or invalid_requests:
            detail = (
                "CodeBuddy BYOK route invalid: "
                f"mismatches={mismatches}, custom_model_requests={custom_requests}, "
                f"observed_requests={len(requests)}, invalid_requests={len(invalid_requests)}"
            )
            return False, "model_route", detail, result
    if result.get("status") not in VALID_RESULTS:
        detail = f"case result status={result.get('status')}"
        return False, "invalid_artifact", detail, result
    event_paths = sorted(root.glob("*_mcp_events.jsonl"))
    parsed_events: dict[str, list[dict[str, Any]]] = {}
    for event_path in event_paths:
        events: list[dict[str, Any]] = []
        try:
            for line_number, line in enumerate(
                event_path.read_text(encoding="utf-8").splitlines(), start=1
            ):
                if not line.strip():
                    continue
                event = json.loads(line)
                if not isinstance(event, dict):
                    raise ValueError(f"line {line_number} is not an object")
                events.append(event)
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
            return False, "invalid_artifact", f"invalid MCP event log {event_path.name}: {exc}", result
        errors = [
            event for event in events if event.get("event_type") in {"server_error", "protocol_error"}
        ]
        if errors:
            first = errors[0]
            return (
                False,
                "invalid_artifact",
                f"{event_path.name} contains {first.get('event_type')}: {first.get('error')}",
                result,
            )
        parsed_events[event_path.name] = events
    if result.get("benchmark_id") in {"agentdojo", "agentdyn"}:
        benchmark_id = result["benchmark_id"]
        event_name = f"{benchmark_id}_mcp_events.jsonl"
        if event_name not in parsed_events:
            return False, "missing_artifact", f"missing {event_name}", result
        try:
            native = read_json(root / "native_result.json")
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            return False, "invalid_artifact", f"invalid native_result.json: {exc}", result
        if native.get("error"):
            return False, "invalid_artifact", f"native runtime error: {native['error']}", result
        if not isinstance(native.get("pre_environment"), dict) or not isinstance(
            native.get("post_environment"), dict
        ):
            return False, "invalid_artifact", "missing native environment snapshots", result
        if not isinstance(native.get("function_calls"), list):
            return False, "invalid_artifact", "invalid native function call trace", result
        if benchmark_id == "agentdyn":
            delivery = native.get("attack_delivery")
            event_delivery = [
                {key: value for key, value in event.items() if key != "event_type"}
                for event in parsed_events[event_name]
                if event.get("event_type") == "attack_delivery"
            ]
            if not isinstance(delivery, list) or not delivery:
                return False, "invalid_artifact", "AgentDyn attack was not observably delivered", result
            if delivery != event_delivery:
                return False, "invalid_artifact", "AgentDyn attack delivery evidence mismatch", result
    metadata = result.get("evaluation_metadata")
    if require_evaluation_metadata and not isinstance(metadata, dict):
        return False, "invalid_artifact", "missing evaluation_metadata", result
    if isinstance(metadata, dict):
        if expected_release_id and metadata.get("release_id") != expected_release_id:
            return False, "invalid_artifact", "release ID mismatch", result
    if result.get("benchmark_id") == "agentharm":
        judge_path = root / "judge_evidence.json"
        if not judge_path.is_file():
            return False, "missing_artifact", "missing judge_evidence.json", result
        try:
            evidence = read_json(judge_path)
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            return False, "invalid_artifact", f"invalid AgentHarm judge evidence: {exc}", result
        judge = evidence.get("judge") or {}
        expected_judge = {
            "profile_id": "deepseek_v4_flash",
            "type": "openai",
            "model": "deepseek-v4-flash",
            "base_url": "https://api.deepseek.com",
            "api_key_env": "DEEPSEEK_API_KEY",
            "temperature": 0.0,
        }
        if judge != expected_judge:
            return False, "model_route", "AgentHarm judge binding drifted", result
    if result.get("benchmark_id") == "saber":
        valid, kind, detail = validate_saber_case_artifacts(root, result)
        if not valid:
            return False, kind, detail, result
    contribution = result.get("asr_contribution") or {}
    numerator = contribution.get("numerator")
    denominator = contribution.get("denominator")
    if (
        not isinstance(numerator, (int, float))
        or not isinstance(denominator, (int, float))
        or numerator < 0
        or denominator < 0
        or numerator > denominator
    ):
        return False, "invalid_artifact", "invalid ASR contribution", result
    stderr = (root / "product_stderr.txt").read_text(encoding="utf-8", errors="replace").strip()
    if stderr:
        return False, classify_artifact_error(stderr), stderr[-2000:], result
    return True, None, None, result


def archive_partial_case(
    campaign_dir: Path,
    root: Path,
    lease: Lease,
) -> None:
    if not root.exists() or not any(root.iterdir()):
        return
    destination = (
        campaign_dir
        / "attempt_history"
        / lease.product
        / lease.benchmark_id
        / lease.case_id.replace(":", "__")
        / f"attempt_{lease.attempt:03d}_{int(time.time())}"
    )
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        destination = destination.with_name(destination.name + "_" + uuid.uuid4().hex[:8])
    shutil.move(str(root), str(destination))


def result_metadata(
    config: dict[str, Any], lease: Lease, store: QueueStore, run: dict[str, Any]
) -> dict[str, Any]:
    judge = (
        config["judge_profile"]
        if lease.benchmark_id in {"saber", "agentharm"}
        else None
    )
    return {
        "release_id": config["release_id"],
        "campaign_id": config["campaign_id"],
        "product": lease.product,
        "effective_tested_model": (run.get("product") or {}).get("product_model"),
        "tested_provider_route": config["tested_profiles"][lease.product]["base_url"],
        "judge_model": judge["model"] if judge else None,
        "judge_route": judge["base_url"] if judge else None,
        "logical_shard": lease.logical_shard,
        "logical_shard_count": config["logical_shards"],
        "lease_history": store.history(
            lease.case_id,
            product=lease.product,
            benchmark_id=lease.benchmark_id,
        ),
        "source_case_id": lease.case_id,
        "recorded_at": utc_now(),
    }


def enrich_result(root: Path, metadata: dict[str, Any]) -> None:
    result_path = root / "case_result.json"
    result = read_json(result_path)
    result["evaluation_metadata"] = metadata
    write_json_atomic(result_path, result)
    write_json_atomic(root / "adaptive_metadata.json", metadata)


def worker_command(config_path: Path, product: str, worker_id: str) -> list[str]:
    entrypoint = (
        Path(__file__).with_name("run_adaptive_qoder_byok.py")
        if product == "qoder"
        else Path(__file__).resolve()
    )
    return [
        sys.executable,
        str(entrypoint),
        "worker",
        "--config",
        str(config_path),
        "--product",
        product,
        "--worker-id",
        worker_id,
    ]


class LeaseHeartbeat:
    def __init__(self, store: QueueStore, token: str, lease_seconds: float):
        self.store = store
        self.token = token
        self.lease_seconds = lease_seconds
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *_args):
        self.stop.set()
        self.thread.join(timeout=10)

    def _run(self) -> None:
        interval = max(1.0, min(15.0, self.lease_seconds / 3))
        while not self.stop.wait(interval):
            if not self.store.heartbeat(self.token, lease_seconds=self.lease_seconds):
                return


def worker_main(args: argparse.Namespace) -> int:
    config_path = args.config.resolve()
    config = read_json(config_path)
    campaign_dir = config_path.parent
    store = QueueStore(campaign_dir / "queue.sqlite3")
    expected_model = config["tested_profiles"][args.product]["model"]
    worker_path = campaign_dir / "workers" / f"{args.worker_id}.json"
    stop_path = campaign_dir / "stop" / args.worker_id
    write_json_atomic(
        worker_path,
        {
            "worker_id": args.worker_id,
            "pid": os.getpid(),
            "process_create_time": psutil.Process().create_time(),
            "product": args.product,
            "status": "idle",
            "started_at": utc_now(),
            "heartbeat_at": utc_now(),
        },
    )
    while not stop_path.exists():
        lease = store.claim(
            args.worker_id,
            args.product,
            lease_seconds=config["lease_seconds"],
        )
        if lease is None:
            break
        root = case_root(config, lease.product, lease.benchmark_id, lease.case_id)
        valid, _, _, existing = validate_case_artifacts(
            root,
            expected_product=lease.product,
            expected_model=expected_model,
            expected_release_id=config.get("release_id"),
            require_evaluation_metadata=True,
        ) if root.exists() else (False, None, None, None)
        if valid:
            run = read_json(root / "product_run.json")
            store.finish(
                lease.token,
                outcome=existing["status"],
                duration_seconds=float(run.get("duration_seconds") or 0),
            )
            enrich_result(root, result_metadata(config, lease, store, run))
            continue
        if root.exists():
            archive_partial_case(campaign_dir, root, lease)

        state = read_json(worker_path)
        state.update(
            {
                "status": "running",
                "heartbeat_at": utc_now(),
                "lease_token": lease.token,
                "benchmark_id": lease.benchmark_id,
                "case_id": lease.case_id,
                "logical_shard": lease.logical_shard,
                "attempt": lease.attempt,
            }
        )
        write_json_atomic(worker_path, state)
        request = PACKAGE_ROOT / "examples" / "requests" / f"{lease.product}_{lease.benchmark_id}.json"
        command = [
            sys.executable,
            str(RUN_ONE),
            "--request",
            str(request),
            "--case-id",
            lease.case_id,
            "--case-index",
            str(resolve_config_path(config, "case_index")),
            "--run-id",
            config["campaign_id"],
            "--output-root",
            str(resolve_config_path(config, "run_root") / lease.product / lease.benchmark_id),
        ]
        if config.get("data_root"):
            command.extend(["--data-root", config["data_root"]])
        started = time.monotonic()
        with LeaseHeartbeat(store, lease.token, config["lease_seconds"]):
            completed = subprocess.run(
                command,
                cwd=PACKAGE_ROOT,
                text=True,
                encoding="utf-8",
                errors="replace",
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
        duration = time.monotonic() - started
        valid, error_kind, detail, result = validate_case_artifacts(
            root, expected_product=lease.product, expected_model=expected_model
        )
        if not valid:
            diagnostic_path = persist_attempt_diagnostic(
                campaign_dir, lease, completed
            )
            runner_detail = redact_runner_diagnostic(
                completed.stderr or completed.stdout or "run_product failed"
            )[-2_000:]
            detail = "; ".join(value for value in (detail, runner_detail) if value)
            error_kind = classify_artifact_error(detail)
            detail = f"{detail}; diagnostic={relative_to_root(diagnostic_path)}"
        retry = not valid and lease.attempt < config["max_attempts"]
        store.finish(
            lease.token,
            outcome=result["status"] if valid else "invalid",
            duration_seconds=duration,
            error_kind=error_kind,
            error_detail=detail,
            retry=retry,
        )
        if valid:
            run = read_json(root / "product_run.json")
            enrich_result(root, result_metadata(config, lease, store, run))
        state = read_json(worker_path)
        state.update(
            {
                "status": "idle",
                "heartbeat_at": utc_now(),
                "last_case_id": lease.case_id,
                "last_outcome": result["status"] if valid else "invalid",
                "last_error_kind": error_kind,
            }
        )
        for key in ("lease_token", "benchmark_id", "case_id", "logical_shard", "attempt"):
            state.pop(key, None)
        write_json_atomic(worker_path, state)

    state = read_json(worker_path)
    state.update({"status": "stopped", "stopped_at": utc_now(), "heartbeat_at": utc_now()})
    write_json_atomic(worker_path, state)
    return 0


def process_identity_matches(
    pid: int,
    *,
    expected_create_time: float | None = None,
    required_command_tokens: tuple[str, ...] = (),
) -> bool:
    try:
        process = psutil.Process(pid)
        if not process.is_running():
            return False
        if expected_create_time is not None:
            return abs(process.create_time() - float(expected_create_time)) < 0.001
        command = "\0".join(process.cmdline()).casefold()
        return bool(command) and all(
            token.casefold() in command for token in required_command_tokens
        )
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return False


@contextmanager
def controller_lock(campaign_dir: Path):
    path = campaign_dir / "controller.lock"
    campaign_dir.mkdir(parents=True, exist_ok=True)
    token = uuid.uuid4().hex
    payload = {
        "pid": os.getpid(),
        "process_create_time": psutil.Process().create_time(),
        "token": token,
        "started_at": utc_now(),
    }
    try:
        descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        try:
            existing = read_json(path)
        except Exception:
            existing = {}
        if existing.get("pid") and process_identity_matches(
            int(existing["pid"]),
            expected_create_time=existing.get("process_create_time"),
            required_command_tokens=("controller", campaign_dir.name),
        ):
            raise RuntimeError(f"controller already running with PID {existing['pid']}")
        stale = path.with_name(f"controller.lock.stale.{int(time.time())}")
        path.replace(stale)
        descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        json.dump(payload, handle)
    try:
        yield payload
    finally:
        try:
            current = read_json(path)
            if current.get("token") == token:
                path.replace(path.with_name(f"controller.lock.complete.{int(time.time())}"))
        except OSError:
            pass


def load_worker_states(campaign_dir: Path, product: str | None = None) -> list[dict[str, Any]]:
    states = []
    for path in (campaign_dir / "workers").glob("*.json"):
        try:
            state = read_json(path)
        except (OSError, json.JSONDecodeError):
            continue
        if product and state.get("product") != product:
            continue
        state["alive"] = bool(
            state.get("pid")
            and process_identity_matches(
                int(state["pid"]),
                expected_create_time=state.get("process_create_time"),
                required_command_tokens=(
                    "worker",
                    str(state.get("worker_id") or ""),
                    campaign_dir.name,
                ),
            )
        )
        states.append(state)
    return states


def stop_requests_needed(
    *, live_workers: int, desired_workers: int, already_requested: int
) -> int:
    return max(0, live_workers - desired_workers - already_requested)


def initialize_queue(config: dict[str, Any], campaign_dir: Path) -> QueueStore:
    rows = selection_rows(resolve_config_path(config, "case_index"))
    store = QueueStore(campaign_dir / "queue.sqlite3")
    for product in config["products"]:
        store.add_cases(product, rows, config["logical_shards"])
        expected_model = config["tested_profiles"][product]["model"]
        for row in rows:
            root = case_root(config, product, row["benchmark_id"], row["case_id"])
            if not root.exists():
                continue
            valid, _, _, result = validate_case_artifacts(
                root,
                expected_product=product,
                expected_model=expected_model,
                expected_release_id=config.get("release_id"),
                require_evaluation_metadata=bool(config.get("release_id")),
            )
            if valid:
                store.mark_existing_complete(
                    product, row["benchmark_id"], row["case_id"], result["status"]
                )
    return store


def product_maximum(store: QueueStore, product: str) -> int:
    active = {
        row["benchmark_id"]
        for row in store.rows(product)
        if row["state"] in {"pending", "leased"}
    }
    return 3 if active & {"agentdojo", "saber"} else 4


def recent_metrics(
    store: QueueStore, product: str, since: float, live_workers: int
) -> WindowMetrics:
    rows = store.rows(product)
    recent = [row for row in rows if row.get("completed_at") and row["completed_at"] >= since]
    durations = sorted(float(row["duration_seconds"] or 0) for row in recent)
    error_counts = Counter(row.get("error_kind") for row in rows if row.get("error_kind"))
    memory = psutil.virtual_memory()
    disk = psutil.disk_usage(str(PACKAGE_ROOT))
    elapsed = max(1.0, time.time() - since)
    percentile = lambda q: durations[min(len(durations) - 1, math.ceil(len(durations) * q) - 1)] if durations else 0.0
    return WindowMetrics(
        queue_depth=sum(row["state"] == "pending" for row in rows),
        throughput=len(recent) / elapsed,
        p50_latency=statistics.median(durations) if durations else 0.0,
        p95_latency=percentile(0.95),
        rate_limit_errors=error_counts["rate_limit"],
        server_errors=error_counts["server_5xx"],
        timeouts=error_counts["timeout"],
        invalid_artifacts=error_counts["invalid_artifact"] + error_counts["missing_artifact"],
        model_route_errors=error_counts["model_route"],
        docker_mcp_errors=error_counts["docker_mcp"],
        completed=len(recent),
        cpu_percent=psutil.cpu_percent(interval=0.2),
        available_memory_gb=memory.available / 1024**3,
        free_disk_gb=disk.free / 1024**3,
        live_workers=live_workers,
    )


def aggregate_results(config: dict[str, Any], store: QueueStore) -> dict[str, Any]:
    report: dict[str, Any] = {"campaign_id": config["campaign_id"], "products": {}}
    for product in config["products"]:
        report["products"][product] = {}
        by_benchmark: dict[str, list[dict[str, Any]]] = {benchmark: [] for benchmark in BENCHMARKS}
        for row in store.rows(product):
            if row["state"] != "complete":
                continue
            root = case_root(config, product, row["benchmark_id"], row["case_id"])
            by_benchmark[row["benchmark_id"]].append(read_json(root / "case_result.json"))
        for benchmark, rows in by_benchmark.items():
            if not rows:
                continue
            rows.sort(key=lambda row: row["case_id"])
            destination = resolve_config_path(config, "run_root") / product / benchmark / "case_result.jsonl"
            destination.parent.mkdir(parents=True, exist_ok=True)
            temporary = destination.with_name(destination.name + f".{os.getpid()}.tmp")
            temporary.write_text(
                "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows),
                encoding="utf-8",
            )
            temporary.replace(destination)
            numerator = sum(row["asr_contribution"]["numerator"] for row in rows)
            denominator = sum(row["asr_contribution"]["denominator"] for row in rows)
            report["products"][product][benchmark] = {
                "selected": len(rows),
                "asr_numerator": numerator,
                "asr_denominator": denominator,
                "asr": numerator / denominator if denominator else None,
            }
    write_json_atomic(Path(config["campaign_dir_abs"]) / "aggregate.json", report)
    return report


def controller_main(args: argparse.Namespace) -> int:
    config_path = args.config.resolve()
    config = read_json(config_path)
    campaign_dir = config_path.parent
    config["campaign_dir_abs"] = str(campaign_dir)
    with controller_lock(campaign_dir) as lock:
        write_controller_metadata(campaign_dir, lock, "initializing")
        store: QueueStore | None = None
        try:
            campaign_judge_preflight(config)
            store = initialize_queue(config, campaign_dir)
            policies = {
                product: AdaptivePolicy(cooldown_seconds=config["cooldown_seconds"])
                for product in config["products"]
            }
            processes: dict[str, subprocess.Popen] = {}
            desired = {product: config["initial_workers"] for product in config["products"]}
            next_id = Counter()
            write_controller_metadata(
                campaign_dir,
                lock,
                "running",
                initialized_at=utc_now(),
                products=config["products"],
                desired_workers=desired,
            )
            record_controller_event(
                store,
                campaign_dir,
                "controller_running",
                {"products": config["products"], "desired_workers": desired},
            )

            def launch(product: str) -> None:
                next_id[product] += 1
                worker_id = f"{product}-{next_id[product]:03d}-{uuid.uuid4().hex[:6]}"
                log_root = campaign_dir / "logs" / product
                log_root.mkdir(parents=True, exist_ok=True)
                stdout = (log_root / f"{worker_id}.out.log").open("a", encoding="utf-8")
                stderr = (log_root / f"{worker_id}.err.log").open("a", encoding="utf-8")
                process = subprocess.Popen(
                    worker_command(config_path, product, worker_id),
                    cwd=PACKAGE_ROOT,
                    stdout=stdout,
                    stderr=stderr,
                )
                stdout.close()
                stderr.close()
                processes[worker_id] = process
                record_controller_event(
                    store,
                    campaign_dir,
                    "worker_launched",
                    {"worker_id": worker_id, "product": product, "pid": process.pid},
                )

            existing_live = [state for state in load_worker_states(campaign_dir) if state["alive"]]
            for product in config["products"]:
                live = sum(state["product"] == product for state in existing_live)
                while (
                    live < desired[product]
                    and store.counts(product)["pending"] > 0
                    and len(existing_live) + len(processes) < config["global_max_workers"]
                ):
                    launch(product)
                    live += 1

            window_started = time.time()
            last_scale = window_started
            smoke_forces = list(config.get("smoke_force_sequence") or [])
            while True:
                time.sleep(config["poll_seconds"])
                for worker_id, process in list(processes.items()):
                    if process.poll() is not None:
                        processes.pop(worker_id)
                states = load_worker_states(campaign_dir)
                live_by_product = Counter(
                    state["product"] for state in states if state.get("alive") and state.get("status") != "stopped"
                )
                stopping_by_product = Counter(
                    state["product"]
                    for state in states
                    if state.get("alive")
                    and state.get("status") != "stopped"
                    and (campaign_dir / "stop" / state["worker_id"]).exists()
                )
                counts = {product: store.counts(product) for product in config["products"]}
                progress = {
                    "campaign_id": config["campaign_id"],
                    "updated_at": utc_now(),
                    "status": "running",
                    "counts": counts,
                    "desired_workers": desired,
                    "live_workers": dict(live_by_product),
                    "stopping_workers": dict(stopping_by_product),
                }
                write_json_atomic(campaign_dir / "progress.json", progress)
                if all(value["pending"] == 0 and value["leased"] == 0 for value in counts.values()):
                    break
                if time.time() - last_scale >= config["window_seconds"]:
                    total_live = sum(live_by_product.values())
                    decisions = {}
                    for product in config["products"]:
                        metrics = recent_metrics(store, product, window_started, live_by_product[product])
                        force = smoke_forces.pop(0) if smoke_forces else None
                        maximum = product_maximum(store, product)
                        target, reason = policies[product].decide(
                            current=max(1, desired[product]),
                            maximum=maximum,
                            metrics=metrics,
                            force_scale=force,
                        )
                        if target > desired[product] and total_live >= config["global_max_workers"]:
                            target, reason = desired[product], "global_cap"
                        decisions[product] = {"from": desired[product], "to": target, "reason": reason, "metrics": metrics.__dict__}
                        desired[product] = target
                    record_controller_event(store, campaign_dir, "scale_window", decisions)
                    for product in config["products"]:
                        live = live_by_product[product]
                        while (
                            live < desired[product]
                            and counts[product]["pending"] > 0
                            and sum(live_by_product.values()) < config["global_max_workers"]
                        ):
                            launch(product)
                            live += 1
                            live_by_product[product] += 1
                        additional_stops = stop_requests_needed(
                            live_workers=live,
                            desired_workers=desired[product],
                            already_requested=stopping_by_product[product],
                        )
                        if additional_stops:
                            candidates = [
                                state for state in states
                                if state.get("product") == product and state.get("alive")
                                and not (campaign_dir / "stop" / state["worker_id"]).exists()
                            ]
                            for state in sorted(
                                candidates,
                                key=lambda item: item["worker_id"],
                                reverse=True,
                            )[:additional_stops]:
                                stop = campaign_dir / "stop" / state["worker_id"]
                                stop.parent.mkdir(parents=True, exist_ok=True)
                                stop.touch(exist_ok=True)
                                record_controller_event(
                                    store,
                                    campaign_dir,
                                    "worker_stop_requested",
                                    {"worker_id": state["worker_id"], "product": product},
                                )
                    window_started = time.time()
                    last_scale = window_started

            for state in load_worker_states(campaign_dir):
                if state.get("alive"):
                    stop = campaign_dir / "stop" / state["worker_id"]
                    stop.parent.mkdir(parents=True, exist_ok=True)
                    stop.touch(exist_ok=True)
            for process in processes.values():
                try:
                    process.wait(timeout=30)
                except subprocess.TimeoutExpired:
                    pass
            aggregate = aggregate_results(config, store)
            final_counts = {product: store.counts(product) for product in config["products"]}
            failed = any(counts["failed"] for counts in final_counts.values())
            final_status = "failed" if failed else "completed"
            write_json_atomic(
                campaign_dir / "progress.json",
                {
                    "campaign_id": config["campaign_id"],
                    "updated_at": utc_now(),
                    "status": final_status,
                    "counts": final_counts,
                    "aggregate": aggregate,
                },
            )
            record_controller_event(
                store,
                campaign_dir,
                "controller_finished",
                {"status": final_status, "counts": final_counts},
            )
            write_controller_metadata(
                campaign_dir,
                lock,
                final_status,
                finished_at=utc_now(),
                exit_code=1 if failed else 0,
                counts=final_counts,
            )
            return 1 if failed else 0
        except Exception as exc:
            if store is not None:
                record_controller_event(
                    store,
                    campaign_dir,
                    "controller_failed",
                    {"error_type": type(exc).__name__},
                )
            write_controller_metadata(
                campaign_dir,
                lock,
                "failed",
                finished_at=utc_now(),
                exit_code=1,
                error_type=type(exc).__name__,
            )
            raise


def init_main(args: argparse.Namespace) -> int:
    campaign_dir = args.campaign_dir.resolve()
    products = args.products or list(PRODUCTS)
    validate_product_release_gates(
        products, allow_candidate_products=args.allow_candidate_products
    )
    campaign_dir.mkdir(parents=True, exist_ok=True)
    tested_profiles = {
        "codex": {"model": "deepseek-v4-flash", "base_url": "https://api.deepseek.com/v1"},
        "claude_code": {"model": "deepseek-v4-flash", "base_url": "https://api.deepseek.com"},
        "qoder": {"model": "deepseek-v4-flash", "base_url": "https://api.deepseek.com"},
        "codebuddy": {"model": "deepseek-v4-flash", "base_url": "https://api.deepseek.com"},
    }
    judge_profile = load_immutable_judge_profile(
        PACKAGE_ROOT / "release" / "evaluation_config.json",
        PACKAGE_ROOT / "benchmark_registry.json",
    )
    config = {
        "schema_version": "0.3.0",
        "campaign_id": args.campaign_id,
        "release_id": args.release_id,
        "created_at": utc_now(),
        "products": products,
        "case_index": relative_to_root(args.case_index),
        "run_root": relative_to_root(args.run_root),
        "data_root": str(args.data_root.expanduser().resolve()) if args.data_root else None,
        "logical_shards": 32,
        "lease_seconds": args.lease_seconds,
        "max_attempts": args.max_attempts,
        "initial_workers": 2,
        "global_max_workers": 10,
        "window_seconds": args.window_seconds,
        "cooldown_seconds": args.cooldown_seconds,
        "poll_seconds": args.poll_seconds,
        "tested_profiles": tested_profiles,
        "judge_profile": judge_profile,
        "smoke_force_sequence": args.smoke_force_sequence or [],
        "candidate_products_allowed": bool(args.allow_candidate_products),
    }
    write_json_atomic(campaign_dir / "config.json", config)
    initialize_queue(config, campaign_dir)
    print(json.dumps({"campaign_dir": str(campaign_dir), "counts": QueueStore(campaign_dir / "queue.sqlite3").counts()}, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Adaptive product-native ASR controller")
    subparsers = parser.add_subparsers(dest="command", required=True)
    init = subparsers.add_parser("init")
    init.add_argument("--campaign-dir", required=True, type=Path)
    init.add_argument("--campaign-id", required=True)
    init.add_argument("--release-id", required=True)
    init.add_argument("--case-index", required=True, type=Path)
    init.add_argument("--run-root", required=True, type=Path)
    init.add_argument("--data-root", type=Path)
    init.add_argument("--products", nargs="+", choices=SUPPORTED_PRODUCTS)
    init.add_argument("--allow-candidate-products", action="store_true")
    init.add_argument("--lease-seconds", type=float, default=120.0)
    init.add_argument("--max-attempts", type=int, default=3)
    init.add_argument("--window-seconds", type=float, default=300.0)
    init.add_argument("--cooldown-seconds", type=float, default=300.0)
    init.add_argument("--poll-seconds", type=float, default=5.0)
    init.add_argument("--smoke-force-sequence", nargs="*", choices=("up", "down"))
    init.set_defaults(func=init_main)
    controller = subparsers.add_parser("controller")
    controller.add_argument("--config", required=True, type=Path)
    controller.set_defaults(func=controller_main)
    worker = subparsers.add_parser("worker")
    worker.add_argument("--config", required=True, type=Path)
    worker.add_argument("--product", required=True, choices=SUPPORTED_PRODUCTS)
    worker.add_argument("--worker-id", required=True)
    worker.set_defaults(func=worker_main)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
