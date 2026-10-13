from __future__ import annotations

from collections import Counter
import argparse
import json
from pathlib import Path
import sys
from typing import Any


PACKAGE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PACKAGE_ROOT))

from product_eval.agentpoison import AgentPoisonAdapter
BENCHMARKS = (
    "injecagent",
    "msb",
    "agentdojo",
    "agentleak",
    "saber",
    "agentpoison_strategyqa",
)
PRODUCTS = ("codex", "claude_code", "qoder", "codebuddy")
CANONICAL_MODEL = "deepseek-v4-flash"
DEFAULT_AGENTPOISON_ROOT = (
    PACKAGE_ROOT.parent
    / "\u6570\u636e\u96c6"
    / "\u8bc4\u6d4b\u6570\u636e"
    / "AgentPoison-master"
    / "AgentPoison-master"
)
TRANSPORT_ERROR_MARKERS = (
    "transport closed",
    "invalid unicode",
    "serde error",
    "resources/list failed",
    "resources/templates/list failed",
)


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _selected(path: Path) -> list[str]:
    return [json.loads(line)["case_id"] for line in path.read_text(encoding="utf-8").splitlines() if line]


def _runtime_model(product: str, run: dict[str, Any], stdout_path: Path) -> tuple[str | None, str | None]:
    command = run.get("command") or []
    rendered = " ".join(str(part) for part in command)
    if product == "codex":
        required = (
            'model_provider="deepseek"',
            "https://api.deepseek.com/v1",
            'model_providers.deepseek.env_key="DEEPSEEK_API_KEY"',
            CANONICAL_MODEL,
        )
        missing = [value for value in required if value not in rendered]
        return (CANONICAL_MODEL, None) if not missing else (None, f"missing command locks: {missing}")
    detected = None
    if stdout_path.is_file():
        for line in stdout_path.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if event.get("type") == "system" and event.get("subtype") == "init":
                detected = event.get("model")
                break
    if not isinstance(detected, str):
        return None, "runtime init event did not report a model"
    expected_runtime = (
        "custom-local:deepseek-v4-flash"
        if product == "codebuddy"
        else CANONICAL_MODEL
    )
    if detected.casefold() != expected_runtime.casefold():
        return detected, f"unexpected runtime model: {detected}"
    if product == "qoder" and "api3.qoder.sh" in rendered.casefold():
        return detected, "Qoder used its managed api3.qoder.sh route"
    return detected, None


def _harness_errors(case_root: Path) -> list[str]:
    errors = []
    for events_path in case_root.glob("*_mcp_events.jsonl"):
        for line_number, line in enumerate(events_path.read_text(encoding="utf-8").splitlines(), start=1):
            if not line.strip():
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError as exc:
                errors.append(f"{events_path.name}:{line_number}: invalid JSON: {exc}")
                continue
            if event.get("event_type") in {"server_error", "protocol_error"}:
                errors.append(
                    f"{events_path.name}:{line_number}: {event.get('event_type')}: {event.get('error')}"
                )
    for log_name in ("product_stderr.txt", "product_stdout.jsonl"):
        path = case_root / log_name
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8", errors="replace").casefold()
        for marker in TRANSPORT_ERROR_MARKERS:
            if marker in text:
                errors.append(f"{log_name}: contains {marker!r}")
    stdout_path = case_root / "product_stdout.jsonl"
    if stdout_path.is_file():
        for line_number, line in enumerate(
            stdout_path.read_text(encoding="utf-8", errors="replace").splitlines(), start=1
        ):
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if (
                event.get("type") == "system"
                and event.get("subtype") == "hook_response"
                and event.get("outcome") == "error"
            ):
                errors.append(
                    f"product_stdout.jsonl:{line_number}: hook error: "
                    f"{event.get('hook_name')}: {event.get('stderr') or event.get('output')}"
                )
    return errors


def _qoder_route_error(case_root: Path) -> str | None:
    path = case_root / "product_network_route.json"
    if not path.is_file():
        return "missing product_network_route.json"
    try:
        evidence = _read_json(path)
    except (OSError, json.JSONDecodeError) as exc:
        return f"invalid product_network_route.json: {exc}"
    expected = {
        "provider": "deepseek",
        "provider_model": "deepseek-v4-flash",
        "configured_base_url": "https://api.deepseek.com",
    }
    mismatches = {
        key: evidence.get(key) for key, value in expected.items() if evidence.get(key) != value
    }
    deepseek_requests = int(evidence.get("deepseek_official_request_lines") or 0)
    managed_requests = int(evidence.get("qoder_inference_request_lines") or 0)
    observed_requests = evidence.get("observed_requests") or []
    managed_generation = any(
        str(request.get("host", "")).casefold() == "api3.qoder.sh"
        and "agent_chat_generation" in str(request.get("path", "")).casefold()
        for request in observed_requests
        if isinstance(request, dict)
    )
    if mismatches or deepseek_requests < 1 or managed_requests or managed_generation:
        return (
            "unexpected Qoder BYOK route evidence: "
            f"mismatches={mismatches}, deepseek_requests={deepseek_requests}, "
            f"managed_requests={managed_requests}, managed_generation={managed_generation}"
        )
    return None


def _codebuddy_route_error(case_root: Path) -> str | None:
    path = case_root / "product_network_route.json"
    if not path.is_file():
        return "missing product_network_route.json"
    try:
        evidence = _read_json(path)
    except (OSError, json.JSONDecodeError) as exc:
        return f"invalid product_network_route.json: {exc}"
    expected = {
        "route_mode": "audited_local_forwarder",
        "provider": "deepseek",
        "provider_model": CANONICAL_MODEL,
        "api_key_supplied": True,
        "effective_model": f"custom-local:{CANONICAL_MODEL}",
    }
    mismatches = {
        key: evidence.get(key)
        for key, value in expected.items()
        if evidence.get(key) != value
    }
    observed = evidence.get("observed_requests") or []
    invalid_requests = [
        request
        for request in observed
        if not isinstance(request, dict)
        or request.get("host") != "api.deepseek.com"
        or request.get("authorization_supplied") is not True
        or request.get("request_model") != CANONICAL_MODEL
        or request.get("response_status") != 200
        or request.get("tools_supplied") is not True
    ]
    custom_requests = int(evidence.get("observed_custom_model_requests") or 0)
    if mismatches or custom_requests < 1 or invalid_requests:
        return (
            "unexpected CodeBuddy BYOK route evidence: "
            f"mismatches={mismatches}, custom_requests={custom_requests}, "
            f"invalid_requests={len(invalid_requests)}"
        )
    return None


def audit_cell(root: Path, selection_root: Path, product: str, benchmark: str) -> dict[str, Any]:
    case_ids = _selected(selection_root / f"{benchmark}.jsonl")
    statuses: Counter[str] = Counter()
    product_statuses: Counter[str] = Counter()
    missing = []
    model_errors = []
    stderr_nonempty = []
    harness_errors = []
    numerator = denominator = 0
    for case_id in case_ids:
        case_root = root / product / benchmark / case_id.replace(":", "__")
        run_path = case_root / "product_run.json"
        result_path = case_root / "case_result.json"
        if not run_path.is_file() or not result_path.is_file():
            missing.append(case_id)
            continue
        run = _read_json(run_path)
        result = _read_json(result_path)
        product_statuses[str(run.get("status"))] += 1
        statuses[str(result.get("status"))] += 1
        contribution = result.get("asr_contribution") or {}
        numerator += int(contribution.get("numerator") or 0)
        denominator += int(contribution.get("denominator") or 0)
        reported_model = (run.get("product") or {}).get("product_model")
        if reported_model != CANONICAL_MODEL:
            model_errors.append({"case_id": case_id, "error": f"reported {reported_model!r}"})
        detected, error = _runtime_model(product, run, case_root / "product_stdout.jsonl")
        if error:
            model_errors.append({"case_id": case_id, "detected": detected, "error": error})
        if product == "qoder":
            route_error = _qoder_route_error(case_root)
            if route_error:
                model_errors.append({"case_id": case_id, "error": route_error})
        elif product == "codebuddy":
            route_error = _codebuddy_route_error(case_root)
            if route_error:
                model_errors.append({"case_id": case_id, "error": route_error})
        stderr_path = case_root / "product_stderr.txt"
        if stderr_path.is_file() and stderr_path.read_text(encoding="utf-8", errors="replace").strip():
            stderr_nonempty.append(case_id)
        for error in _harness_errors(case_root):
            harness_errors.append({"case_id": case_id, "error": error})
    complete = not missing and sum(statuses.values()) == len(case_ids)
    execution_ok = complete and product_statuses == {"completed": len(case_ids)}
    no_invalid = statuses.get("invalid", 0) == 0
    gate_pass = (
        execution_ok
        and no_invalid
        and not model_errors
        and not stderr_nonempty
        and not harness_errors
    )
    return {
        "selected": len(case_ids),
        "missing": missing,
        "product_statuses": dict(product_statuses),
        "case_statuses": dict(statuses),
        "asr": numerator / denominator if denominator else None,
        "asr_numerator": numerator,
        "asr_denominator": denominator,
        "model_route_errors": model_errors,
        "stderr_nonempty_cases": stderr_nonempty,
        "harness_errors": harness_errors,
        "artifacts_complete": complete,
        "execution_ok": execution_ok,
        "no_invalid": no_invalid,
        "gate_pass": gate_pass,
        "smoke_gate_pass": gate_pass,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit the fixed v0.2 five-case smoke runs.")
    parser.add_argument(
        "--run-root", type=Path,
        default=PACKAGE_ROOT / ".codex_tmp" / "federated_asr_runs" / "v0.2",
    )
    parser.add_argument(
        "--selection-root", type=Path,
        default=PACKAGE_ROOT / "selections" / "v0.2" / "smoke5",
    )
    parser.add_argument("--output", type=Path)
    parser.add_argument("--agentpoison-root", type=Path, default=DEFAULT_AGENTPOISON_ROOT)
    args = parser.parse_args()
    run_root = args.run_root.resolve()
    selection_root = args.selection_root.resolve()
    cells = {
        product: {
            benchmark: audit_cell(run_root, selection_root, product, benchmark)
            for benchmark in BENCHMARKS
        }
        for product in PRODUCTS
    }
    gate_failures = [
        f"{product} x {benchmark}"
        for product in PRODUCTS
        for benchmark in BENCHMARKS
        if not cells[product][benchmark]["smoke_gate_pass"]
    ]
    agentpoison_preflight = None
    agentpoison_preflight_error = None
    try:
        agentpoison_preflight = AgentPoisonAdapter(args.agentpoison_root).preflight()
    except Exception as exc:
        agentpoison_preflight_error = f"{type(exc).__name__}: {exc}"
    agentpoison_ready = bool(
        agentpoison_preflight and agentpoison_preflight.get("formal_release_ready") is True
    )
    ten_percent_blockers = list(gate_failures)
    if not agentpoison_ready:
        ten_percent_blockers.append(
            "agentpoison_strategyqa formal DPR runtime is not ready: "
            + str(agentpoison_preflight_error)
        )
    report = {
        "schema_version": "0.2",
        "primary_metric": "ASR",
        "run_root": str(run_root),
        "selection_root": str(selection_root),
        "cells": cells,
        "gate_failures": gate_failures,
        "agentpoison_formal_release_ready": agentpoison_ready,
        "agentpoison_preflight": agentpoison_preflight,
        "agentpoison_preflight_error": agentpoison_preflight_error,
        "ten_percent_blockers": ten_percent_blockers,
        "ready_for_ten_percent": not ten_percent_blockers,
    }
    output = args.output or run_root / "smoke_audit.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ready_for_ten_percent"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
