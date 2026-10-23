#!/usr/bin/env python3
"""Bounded engineering smoke; never a safety benchmark or a Loop A experiment."""
from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import datetime, timezone
from decimal import Decimal, ROUND_CEILING
import json
import os
from pathlib import Path
import sys
import time
import urllib.error
import urllib.request
import uuid

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from llm_eval import multi_model_rule_eval_prompt_only as native
from orchestrator.budget import BudgetExceeded, BudgetLedger
from orchestrator.registry import bind_run, file_hash, make_identity

ENDPOINT = "https://api.deepseek.com/chat/completions"
MODEL = "deepseek-flash"
INPUT = ROOT / "assets/samples/llm_qa_demo.jsonl"
PRICING_URL = "https://api-docs.deepseek.com/zh-cn/quick_start/pricing/"
# CNY per million tokens, checked 2026-10-23. Always use peak rates for budgeting.
RATES = {"hit": "0.04", "miss": "2", "output": "8"}
MAX_REQUESTS = 10
LIMIT_MICROCNY = 5_000_000
RESERVE_MICROCNY = 500_000
MAX_OUTPUT = 32


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def transport(payload: dict, key: str) -> dict:
    request = urllib.request.Request(
        ENDPOINT, data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"},
        method="POST")
    # No redirects, SDK retries, debug logs, or response-body exception messages.
    with urllib.request.build_opener(NoRedirect()).open(request, timeout=60) as response:
        body = response.read(1_000_001)
        if len(body) > 1_000_000:
            raise ValueError("response_too_large")
        return json.loads(body)


def validate_usage(usage: dict) -> tuple[dict, int]:
    if not isinstance(usage, dict):
        raise ValueError("missing_usage")
    names = ("prompt_tokens", "completion_tokens", "total_tokens",
             "prompt_cache_hit_tokens", "prompt_cache_miss_tokens")
    values = {name: usage.get(name) for name in names}
    if any(type(value) is not int or value < 0 for value in values.values()):
        raise ValueError("invalid_usage")
    if (values["prompt_tokens"] != values["prompt_cache_hit_tokens"] + values["prompt_cache_miss_tokens"]
            or values["total_tokens"] != values["prompt_tokens"] + values["completion_tokens"]):
        raise ValueError("inconsistent_usage")
    cost = (Decimal(RATES["hit"]) * values["prompt_cache_hit_tokens"]
            + Decimal(RATES["miss"]) * values["prompt_cache_miss_tokens"]
            + Decimal(RATES["output"]) * values["completion_tokens"])
    return values, int(cost.to_integral_value(rounding=ROUND_CEILING))


def write_json(path: Path, value):
    content = json.dumps(value, ensure_ascii=False, indent=2) + "\n"
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        with temporary.open("x", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def run(out: Path, *, live: bool = False, send=None) -> dict:
    key = os.environ.get("DEEPSEEK_API_KEY", "").strip() if live else ""
    if live and (not key or any(char.isspace() for char in key)):
        raise ValueError("missing_or_malformed_DEEPSEEK_API_KEY")
    # Fixed, versioned public fixture: no arbitrary private dataset is uploaded.
    records = [json.loads(line) for line in INPUT.read_text(encoding="utf-8").splitlines() if line.strip()]
    if len(records) != 3 or {row["id"] for row in records} != {"demo-1", "demo-2", "demo-3"}:
        raise ValueError("unexpected_fixture_population")
    payloads = [dict(model=MODEL, messages=native.build_chat_messages(native.build_prompt(row)),
                     max_tokens=MAX_OUTPUT, thinking={"type": "disabled"}, stream=False)
                for row in records]
    if any(len(json.dumps(body).encode("utf-8")) > 4096 for body in payloads):
        raise ValueError("prompt_too_large")
    identity = make_identity(
        track="llm_qa", inputs_hash=file_hash(INPUT), effective_prompt=payloads,
        target={"endpoint": ENDPOINT, "requested_model": MODEL, "thinking": "disabled"},
        environment={"python": sys.version, "runner_sha256": file_hash(Path(__file__)),
                     "live": live, "retries": 0, "request_limit": MAX_REQUESTS,
                     "cost_limit_microcny": LIMIT_MICROCNY, "rates": RATES},
        scorer={"version": native.SCORER_VERSION, "sha256": file_hash(Path(native.__file__))},
        dataset_version="synthetic_plumbing_demo_v1", seed=0)
    # Atomic directory ownership prevents accidental repeated paid dispatch/resume.
    out.mkdir(parents=True, exist_ok=False)
    bind_run(out, identity)
    write_json(out / "request_plan.json", payloads)
    ledger = BudgetLedger(out / "budget.sqlite3", MAX_REQUESTS, LIMIT_MICROCNY, "CNY")
    summary = dict(status="running" if live else "plan_only", track="llm_qa",
                   population="synthetic_plumbing_demo", formal_qualified=False,
                   safety_claim=False, requested_model=MODEL, input_sha256=file_hash(INPUT),
                   planned_requests=len(records), attempted_requests=0, api_success=0,
                   completed_valid=0, passed=0, failed=0, invalid=0, unextractable_outputs=0, api_errors=0,
                   prompt_tokens=0, completion_tokens=0, usage_priced_upper_microcny=0,
                   pricing_url=PRICING_URL, pricing_checked="2026-10-23", peak_rates_cny_per_million=RATES,
                   cost_note="Conservative usage estimate at peak prices, rounded up per request; not a provider invoice.",
                   run_identity=identity["identity_hash"])

    def save():
        summary["budget"] = ledger.summary()
        write_json(out / "summary.json", summary)

    save()
    if not live:
        return summary
    send = send or transport
    for record, payload in zip(records, payloads):
        job_id = record["id"]
        try:
            ledger.reserve(job_id, "baseline", 1, RESERVE_MICROCNY)
        except BudgetExceeded:
            summary.update(status="stopped_budget", stop_reason="budget_exhausted")
            break
        ledger.start(job_id)
        summary["attempted_requests"] += 1
        save()  # Persist liability BEFORE the request; uncertain calls are not retried.
        started = time.monotonic()
        row = {"id": job_id, "api_status": "error", "passed": None}
        try:
            response = send(payload, key)
            usage, cost = validate_usage(response.get("usage"))
            row["usage"] = usage
            row["usage_priced_upper_microcny"] = cost
            summary["prompt_tokens"] += usage["prompt_tokens"]
            summary["completion_tokens"] += usage["completion_tokens"]
            summary["usage_priced_upper_microcny"] += cost
            ledger.settle(job_id, cost)
            row["response_id"] = response.get("id")
            row["response_model"] = response.get("model")
            row["system_fingerprint"] = response.get("system_fingerprint")
            choices = response.get("choices")
            if not isinstance(choices, list) or len(choices) != 1:
                raise ValueError("invalid_choices")
            choice = choices[0]
            content = choice.get("message", {}).get("content")
            finish = choice.get("finish_reason")
            row.update(api_status="success", finish_reason=finish, output=content)
            summary["api_success"] += 1
            fingerprint = response.get("system_fingerprint")
            invalid_reason = None
            if response.get("model") != MODEL:
                invalid_reason = "target_model_mismatch"
            elif not isinstance(fingerprint, str) or not fingerprint.strip():
                invalid_reason = "missing_backend_fingerprint"
            elif summary.get("system_fingerprint", fingerprint) != fingerprint:
                invalid_reason = "backend_fingerprint_changed"
            elif (finish != "stop" or not 0 < usage["completion_tokens"] <= MAX_OUTPUT
                    or usage["prompt_tokens"] == 0
                    or not isinstance(content, str) or not content.strip()
                    or choice.get("message", {}).get("tool_calls")):
                invalid_reason = "incomplete_or_inconsistent_response"
            if invalid_reason:
                row.update(passed=None, invalid_reason=invalid_reason)
                summary["invalid"] += 1
                summary["status"] = "stopped_invalid"
            else:
                summary.update(observed_model=response["model"], system_fingerprint=fingerprint)
                score = native.RuleScorer.score(record, content)
                row["score"] = asdict(score)
                if score.passed is None:
                    row.update(passed=None, invalid_reason="unscorable_output")
                    summary["invalid"] += 1
                    summary["status"] = "stopped_invalid"
                else:
                    # Preserve native QA semantics: an unextractable completed answer fails.
                    if score.status == "invalid_prediction":
                        summary["unextractable_outputs"] += 1
                    row["passed"] = score.passed
                    summary["completed_valid"] += 1
                    summary["passed" if score.passed else "failed"] += 1
        except Exception as exc:
            # Exception text/HTTP bodies may contain secrets. Store only fixed codes.
            row.update(api_status="error", passed=None, error_type=type(exc).__name__)
            if isinstance(exc, urllib.error.HTTPError):
                row["http_status"] = exc.code
            summary["api_errors"] += 1
            summary["status"] = "stopped_error"
        row["latency_ms"] = round((time.monotonic() - started) * 1000, 2)
        with (out / "results.jsonl").open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
        save()
        print(json.dumps({"id": job_id, "api_status": row["api_status"], "passed": row["passed"]}), flush=True)
        if summary["status"] != "running":
            break
    if summary["status"] == "running":
        summary["status"] = "completed"
    summary["rule_eval_pass_rate"] = (summary["passed"] / summary["completed_valid"]
                                     if summary["completed_valid"] else None)
    save()
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--live", action="store_true", help="Paid calls; requires explicit human budget authorization")
    args = parser.parse_args()
    out = args.out or ROOT / "runs" / ("deepseek-smoke-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex[:8])
    try:
        summary = run(out.resolve(), live=args.live)
    except Exception as exc:
        print(json.dumps({"status": "blocked", "error_type": type(exc).__name__}), file=sys.stderr)
        return 2
    print(json.dumps({"summary": str(out.resolve() / "summary.json"), "status": summary["status"]}), flush=True)
    return 0 if summary["status"] in {"completed", "plan_only"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
