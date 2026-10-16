"""Register target candidates and compare them without mutating live targets."""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import re
from typing import Any, Iterable

import yaml

from scripts.result_analysis import TRACKS, empty_output, has_group_identity, normalize, write_json


CHANGE_TYPES = ("prompt", "policy", "tools", "code", "model_route", "training_plugin")
HEX_SHA256 = re.compile(r"^[0-9a-fA-F]{64}$")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.expanduser().resolve().read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise ValueError(f"Expected a JSON object: {path}")
    return value


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.expanduser().resolve().open(encoding="utf-8-sig") as handle:
        for line_no, line in enumerate(handle, 1):
            if not line.strip():
                continue
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ValueError(f"Expected an object at {path}:{line_no}")
            rows.append(value)
    return rows


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _asset_identity(path: Path) -> dict[str, Any]:
    path = path.expanduser().resolve()
    if path.is_file():
        return {"path": str(path), "kind": "file", "sha256": _sha256_file(path), "file_count": 1}
    if not path.is_dir():
        raise FileNotFoundError(f"Candidate asset does not exist: {path}")
    files = sorted(item for item in path.rglob("*") if item.is_file())
    if not files:
        raise ValueError(f"Candidate asset directory is empty: {path}")
    digest = hashlib.sha256()
    for item in files:
        relative = item.relative_to(path).as_posix().encode("utf-8")
        digest.update(len(relative).to_bytes(8, "big"))
        digest.update(relative)
        digest.update(bytes.fromhex(_sha256_file(item)))
    return {"path": str(path), "kind": "directory", "sha256": digest.hexdigest(), "file_count": len(files)}


def register_target_candidate(
    *,
    track: str,
    baseline_id: str,
    candidate_id: str,
    change_type: str,
    candidate_asset: Path,
    failures: Path,
    out: Path,
    baseline_asset: Path | None = None,
    notes: str | None = None,
) -> dict[str, Any]:
    if track not in TRACKS:
        raise ValueError(f"Unsupported track: {track}")
    if change_type not in CHANGE_TYPES:
        raise ValueError(f"Unsupported change type: {change_type}")
    if not baseline_id.strip() or not candidate_id.strip() or baseline_id == candidate_id:
        raise ValueError("Baseline and candidate IDs must be non-empty and different")
    failure_path = failures / "failures.jsonl" if failures.is_dir() else failures
    failure_rows = _read_jsonl(failure_path)
    if not failure_rows:
        raise ValueError("Target candidates require at least one observed failure")
    wrong_tracks = sorted({str(row.get("track")) for row in failure_rows if row.get("track") != track})
    if wrong_tracks:
        raise ValueError(f"Failure evidence contains other tracks: {', '.join(wrong_tracks)}")
    references = []
    for row in failure_rows:
        if not row.get("failures") or row.get("outcome") != "fail":
            raise ValueError("Failure evidence must contain failed observations and observed signals")
        references.append({
            "track": track,
            "benchmark": row.get("benchmark"),
            "case_id": row.get("case_id"),
            "subject": row.get("subject"),
            "run_id": row.get("run_id"),
            "observed_signals": row.get("failures"),
            "lineage": row.get("lineage"),
            "root_cause": "unconfirmed",
        })
    candidate_identity = _asset_identity(candidate_asset)
    baseline_identity = _asset_identity(baseline_asset) if baseline_asset else None
    output = out.expanduser().resolve()
    empty_output(output)
    created_at = _now()
    record = {
        "schema_version": "1.0",
        "stage": "target_candidate",
        "candidate_id": candidate_id,
        "baseline_id": baseline_id,
        "track": track,
        "change": {
            "type": change_type,
            "asset": candidate_identity,
            "baseline_asset": baseline_identity,
            "notes": notes,
        },
        "failure_evidence": {
            "source": str(failure_path.expanduser().resolve()),
            "count": len(references),
            "references": references,
            "private_oracle_included": False,
        },
        "generation": {
            "mode": "registered_external_candidate",
            "automatic_optimization": False,
            "root_cause_confirmed": False,
        },
        "validation": {"status": "unvalidated", "promotable": False},
        "created_at": created_at,
    }
    write_json(output / "target_candidate.json", record)
    write_json(output / "manifest.json", {
        "schema_version": "1.0",
        "stage": "target_candidate_registration",
        "status": "completed",
        "candidate_id": candidate_id,
        "output": str(output / "target_candidate.json"),
        "created_at": created_at,
    })
    return record


def _load_policy(path: Path) -> dict[str, Any]:
    policy = yaml.safe_load(path.expanduser().resolve().read_text(encoding="utf-8-sig"))
    if not isinstance(policy, dict):
        raise ValueError("Target evolution policy must be a YAML object")
    if policy.get("schema_version") != "1.0":
        raise ValueError("Target evolution policy schema_version must be 1.0")
    defaults: dict[str, Any] = {
        "require_exact_case_parity": True,
        "require_regression_set": True,
        "min_paired_cases": 1,
        "min_safety_improvement": 0.0,
        "min_candidate_safety_score": 0.0,
        "max_regression_drop": 0.0,
        "max_unknown_rate": 0.0,
        "max_one_sided_p_value": None,
        "max_total_token_increase_ratio": None,
        "max_mean_latency_increase_ratio": None,
    }
    unknown = sorted(set(policy) - {"schema_version", *defaults})
    if unknown:
        raise ValueError(f"Unknown target evolution policy fields: {', '.join(unknown)}")
    merged = {**defaults, **{key: value for key, value in policy.items() if key != "schema_version"}}
    for key in ("require_exact_case_parity", "require_regression_set"):
        if type(merged[key]) is not bool:
            raise ValueError(f"Policy {key} must be boolean")
    if type(merged["min_paired_cases"]) is not int or merged["min_paired_cases"] <= 0:
        raise ValueError("Policy min_paired_cases must be a positive integer")
    for key in (
        "min_safety_improvement", "min_candidate_safety_score", "max_regression_drop",
        "max_unknown_rate", "max_one_sided_p_value", "max_total_token_increase_ratio",
        "max_mean_latency_increase_ratio",
    ):
        value = merged[key]
        if value is not None and (type(value) not in {int, float} or value < 0):
            raise ValueError(f"Policy {key} must be null or a non-negative number")
        if value is not None:
            merged[key] = float(value)
    if merged["min_candidate_safety_score"] > 1 or merged["max_unknown_rate"] > 1:
        raise ValueError("Safety score and unknown-rate thresholds must not exceed 1")
    return merged


def _validate_evaluation_manifest(value: dict[str, Any], *, require_regression: bool) -> list[str]:
    errors: list[str] = []
    if value.get("schema_version") != "1.0":
        errors.append("schema_version must be 1.0")
    if value.get("role") not in {"development", "release"}:
        errors.append("role must be development or release")
    for set_name in ("safety", "regression"):
        item = (value.get("sets") or {}).get(set_name)
        if item is None and set_name == "regression" and not require_regression:
            continue
        if not isinstance(item, dict):
            errors.append(f"sets.{set_name} is required")
            continue
        if not isinstance(item.get("id"), str) or not item["id"].strip():
            errors.append(f"sets.{set_name}.id must be non-empty")
        if not isinstance(item.get("sha256"), str) or not HEX_SHA256.fullmatch(item["sha256"]):
            errors.append(f"sets.{set_name}.sha256 must be a SHA-256 digest")
        if type(item.get("expected_case_count")) is not int or item["expected_case_count"] <= 0:
            errors.append(f"sets.{set_name}.expected_case_count must be positive")
    if value.get("private_oracle_exposed") is not False:
        errors.append("private_oracle_exposed must be false")
    if value.get("role") == "release":
        if value.get("frozen_before_candidate") is not True:
            errors.append("release set must be frozen before candidate creation")
        if value.get("candidate_access_to_case_details") is not False:
            errors.append("release case details must be hidden from the candidate process")
    return errors


def _pair_key(item: dict[str, Any]) -> tuple[str, str, str]:
    return str(item["benchmark"]), str(item["case_id"]), str(item["unit"])


def _load_result_set(track: str, paths: Iterable[Path]) -> dict[tuple[str, str, str], dict[str, Any]]:
    items: dict[tuple[str, str, str], dict[str, Any]] = {}
    for input_path in paths:
        path = input_path.expanduser().resolve()
        with path.open(encoding="utf-8-sig") as handle:
            for line_no, line in enumerate(handle, 1):
                if not line.strip():
                    continue
                row = json.loads(line)
                if not isinstance(row, dict):
                    raise ValueError(f"Result row must be an object at {path}:{line_no}")
                item = normalize(track, row, path, line_no)
                if not has_group_identity(item) or type(item.get("case_id")) not in {str, int}:
                    raise ValueError(f"Result lacks comparable identity at {path}:{line_no}")
                key = _pair_key(item)
                if key in items:
                    raise ValueError(f"Duplicate comparison case {key} at {path}:{line_no}")
                items[key] = item
    if not items:
        raise ValueError("Comparison result set is empty")
    return items


def _total_tokens(item: dict[str, Any]) -> int | None:
    cost = item.get("cost")
    if not isinstance(cost, dict):
        return None
    for key in ("total_tokens", "totalTokenCount", "_local_token_estimate"):
        value = cost.get(key)
        if type(value) is int and value >= 0:
            return value
    prompt = cost.get("prompt_tokens", cost.get("promptTokenCount", cost.get("prompt_tokens_est")))
    completion = cost.get("completion_tokens", cost.get("candidatesTokenCount", cost.get("completion_tokens_est")))
    if type(prompt) is int and prompt >= 0 and type(completion) is int and completion >= 0:
        return prompt + completion
    return None


def _stats(track: str, items: dict[tuple[str, str, str], dict[str, Any]]) -> dict[str, Any]:
    valid = [item for item in items.values() if item["outcome"] != "unknown"]
    numerator = sum(int(item["metric_numerator"]) for item in valid)
    denominator = sum(int(item["metric_denominator"]) for item in valid)
    raw = numerator / denominator if denominator else None
    safety_score = (1.0 - raw) if raw is not None and track == "agent_product" else raw
    tokens = [_total_tokens(item) for item in items.values()]
    latencies = [item.get("latency_ms") for item in items.values()]
    known_tokens = [value for value in tokens if type(value) is int]
    known_latencies = [float(value) for value in latencies if type(value) in {int, float} and value >= 0]
    subjects = sorted({str(item["subject"]) for item in items.values()})
    return {
        "case_count": len(items),
        "valid_count": len(valid),
        "unknown_count": len(items) - len(valid),
        "unknown_rate": (len(items) - len(valid)) / len(items),
        "metric": "official_asr" if track == "agent_product" else "native_pass_rate",
        "metric_direction": "lower_is_better" if track == "agent_product" else "higher_is_better",
        "numerator": numerator,
        "denominator": denominator,
        "raw_metric": raw,
        "safety_score": safety_score,
        "subjects": subjects,
        "total_tokens": sum(known_tokens) if len(known_tokens) == len(items) else None,
        "token_coverage": len(known_tokens) / len(items),
        "mean_latency_ms": sum(known_latencies) / len(known_latencies) if len(known_latencies) == len(items) else None,
        "latency_coverage": len(known_latencies) / len(items),
    }


def _ratio_increase(baseline: float | int | None, candidate: float | int | None) -> float | None:
    if baseline is None or candidate is None:
        return None
    if baseline == 0:
        return 0.0 if candidate == 0 else None
    return (float(candidate) - float(baseline)) / float(baseline)


def _one_sided_sign_p_value(improved: int, regressed: int) -> float:
    discordant = improved + regressed
    if discordant == 0 or improved <= regressed:
        return 1.0
    return sum(math.comb(discordant, count) for count in range(improved, discordant + 1)) / (2 ** discordant)


def _paired_transitions(
    baseline: dict[tuple[str, str, str], dict[str, Any]],
    candidate: dict[tuple[str, str, str], dict[str, Any]],
) -> dict[str, Any]:
    counts: Counter[str] = Counter()
    rows = []
    for key in sorted(set(baseline) & set(candidate)):
        before, after = baseline[key]["outcome"], candidate[key]["outcome"]
        transition = f"{before}_to_{after}"
        counts[transition] += 1
        rows.append({"benchmark": key[0], "case_id": key[1], "unit": key[2], "baseline": before, "candidate": after})
    improved = counts["fail_to_pass"]
    regressed = counts["pass_to_fail"]
    return {
        "counts": dict(sorted(counts.items())),
        "improved": improved,
        "regressed": regressed,
        "net_improved": improved - regressed,
        "one_sided_sign_p_value": _one_sided_sign_p_value(improved, regressed),
        "pairs": rows,
    }


def _gate(passed: bool, *, requirement: Any, actual: Any, evidence: str) -> dict[str, Any]:
    return {"passed": bool(passed), "requirement": requirement, "actual": actual, "evidence": evidence}


def compare_target(
    *,
    candidate_manifest: Path,
    baseline_results: list[Path],
    candidate_results: list[Path],
    evaluation_manifest: Path,
    policy_path: Path,
    out: Path,
    baseline_regression_results: list[Path] | None = None,
    candidate_regression_results: list[Path] | None = None,
    regression_track: str | None = None,
) -> dict[str, Any]:
    candidate_record = _read_json(candidate_manifest)
    if candidate_record.get("schema_version") != "1.0":
        raise ValueError("Candidate manifest schema_version must be 1.0")
    if candidate_record.get("stage") != "target_candidate" or candidate_record.get("validation", {}).get("status") != "unvalidated":
        raise ValueError("Candidate manifest must describe an unvalidated target_candidate")
    track = candidate_record.get("track")
    if track not in TRACKS:
        raise ValueError(f"Unsupported candidate track: {track}")
    if not isinstance(candidate_record.get("candidate_id"), str) or not candidate_record["candidate_id"].strip():
        raise ValueError("Candidate manifest is missing candidate_id")
    if not isinstance(candidate_record.get("baseline_id"), str) or not candidate_record["baseline_id"].strip():
        raise ValueError("Candidate manifest is missing baseline_id")
    registered_asset = (candidate_record.get("change") or {}).get("asset")
    if not isinstance(registered_asset, dict) or not isinstance(registered_asset.get("path"), str):
        raise ValueError("Candidate manifest is missing its registered asset identity")
    current_asset = _asset_identity(Path(registered_asset["path"]))
    if any(current_asset.get(key) != registered_asset.get(key) for key in ("kind", "sha256", "file_count")):
        raise ValueError("Candidate asset changed after registration")
    registered_baseline_asset = (candidate_record.get("change") or {}).get("baseline_asset")
    if registered_baseline_asset is not None:
        if not isinstance(registered_baseline_asset, dict) or not isinstance(registered_baseline_asset.get("path"), str):
            raise ValueError("Candidate manifest contains an invalid baseline asset identity")
        current_baseline_asset = _asset_identity(Path(registered_baseline_asset["path"]))
        if any(current_baseline_asset.get(key) != registered_baseline_asset.get(key) for key in ("kind", "sha256", "file_count")):
            raise ValueError("Baseline asset changed after candidate registration")
    policy = _load_policy(policy_path)
    eval_record = _read_json(evaluation_manifest)
    eval_errors = _validate_evaluation_manifest(eval_record, require_regression=policy["require_regression_set"])
    if eval_errors:
        raise ValueError("Invalid evaluation manifest: " + "; ".join(eval_errors))

    baseline = _load_result_set(track, baseline_results)
    candidate = _load_result_set(track, candidate_results)
    baseline_keys, candidate_keys = set(baseline), set(candidate)
    parity = baseline_keys == candidate_keys
    safety_baseline, safety_candidate = _stats(track, baseline), _stats(track, candidate)
    transitions = _paired_transitions(baseline, candidate)
    safety_expected = eval_record["sets"]["safety"]["expected_case_count"]
    safety_improvement = (
        safety_candidate["safety_score"] - safety_baseline["safety_score"]
        if safety_candidate["safety_score"] is not None and safety_baseline["safety_score"] is not None else None
    )

    regression_baseline = regression_candidate = regression_transitions = None
    regression_drop = None
    regression_inputs_present = bool(baseline_regression_results and candidate_regression_results)
    if bool(baseline_regression_results) != bool(candidate_regression_results):
        raise ValueError("Baseline and candidate regression result sets must be provided together")
    if regression_inputs_present:
        regression_track = regression_track or track
        if regression_track not in TRACKS:
            raise ValueError(f"Unsupported regression track: {regression_track}")
        base_reg = _load_result_set(regression_track, baseline_regression_results or [])
        cand_reg = _load_result_set(regression_track, candidate_regression_results or [])
        regression_baseline, regression_candidate = _stats(regression_track, base_reg), _stats(regression_track, cand_reg)
        regression_transitions = _paired_transitions(base_reg, cand_reg)
        regression_parity = set(base_reg) == set(cand_reg)
        regression_expected = eval_record["sets"]["regression"]["expected_case_count"]
        regression_size_matches = len(base_reg) == regression_expected and len(cand_reg) == regression_expected
        if regression_baseline["safety_score"] is not None and regression_candidate["safety_score"] is not None:
            regression_drop = regression_baseline["safety_score"] - regression_candidate["safety_score"]
    else:
        regression_parity = False
        regression_expected = None
        regression_size_matches = not policy["require_regression_set"]

    unknown_rates = [safety_baseline["unknown_rate"], safety_candidate["unknown_rate"]]
    if regression_baseline and regression_candidate:
        unknown_rates.extend([regression_baseline["unknown_rate"], regression_candidate["unknown_rate"]])
    token_increase = _ratio_increase(safety_baseline["total_tokens"], safety_candidate["total_tokens"])
    latency_increase = _ratio_increase(safety_baseline["mean_latency_ms"], safety_candidate["mean_latency_ms"])

    gates: dict[str, dict[str, Any]] = {}
    target_identities_valid = len(safety_baseline["subjects"]) == 1 and len(safety_candidate["subjects"]) == 1
    if regression_baseline and regression_candidate:
        target_identities_valid = target_identities_valid and len(regression_baseline["subjects"]) == 1 and len(regression_candidate["subjects"]) == 1
    gates["target_identity"] = _gate(
        target_identities_valid,
        requirement="one native subject per side and population",
        actual={
            "safety_baseline": safety_baseline["subjects"],
            "safety_candidate": safety_candidate["subjects"],
            "regression_baseline": regression_baseline["subjects"] if regression_baseline else None,
            "regression_candidate": regression_candidate["subjects"] if regression_candidate else None,
        },
        evidence="native result subjects",
    )
    gates["case_parity"] = _gate(
        parity and (not regression_inputs_present or regression_parity) if policy["require_exact_case_parity"] else True,
        requirement="exact baseline/candidate identities" if policy["require_exact_case_parity"] else "not required",
        actual={"safety": parity, "regression": regression_parity if regression_inputs_present else None},
        evidence="comparison inputs",
    )
    gates["sample_size"] = _gate(
        len(baseline_keys & candidate_keys) >= policy["min_paired_cases"]
        and len(baseline) == safety_expected
        and len(candidate) == safety_expected
        and regression_size_matches,
        requirement={
            "minimum_paired": policy["min_paired_cases"],
            "expected_safety_cases": safety_expected,
            "expected_regression_cases": regression_expected,
        },
        actual={
            "paired": len(baseline_keys & candidate_keys),
            "safety_baseline": len(baseline),
            "safety_candidate": len(candidate),
            "regression_baseline": regression_baseline["case_count"] if regression_baseline else None,
            "regression_candidate": regression_candidate["case_count"] if regression_candidate else None,
        },
        evidence=str(evaluation_manifest.expanduser().resolve()),
    )
    gates["validity"] = _gate(
        max(unknown_rates) <= policy["max_unknown_rate"],
        requirement={"max_unknown_rate": policy["max_unknown_rate"]},
        actual={"max_unknown_rate": max(unknown_rates)},
        evidence="native result validity",
    )
    gates["safety"] = _gate(
        safety_improvement is not None
        and safety_improvement >= policy["min_safety_improvement"]
        and safety_candidate["safety_score"] is not None
        and safety_candidate["safety_score"] >= policy["min_candidate_safety_score"],
        requirement={"min_improvement": policy["min_safety_improvement"], "min_candidate_score": policy["min_candidate_safety_score"]},
        actual={"improvement": safety_improvement, "candidate_score": safety_candidate["safety_score"]},
        evidence="paired safety evaluation",
    )
    gates["regression"] = _gate(
        (not policy["require_regression_set"] and not regression_inputs_present)
        or (regression_inputs_present and regression_drop is not None and regression_drop <= policy["max_regression_drop"]),
        requirement={"required": policy["require_regression_set"], "max_drop": policy["max_regression_drop"]},
        actual={"present": regression_inputs_present, "drop": regression_drop},
        evidence="paired benign/capability regression evaluation",
    )
    p_limit = policy["max_one_sided_p_value"]
    gates["paired_confidence"] = _gate(
        p_limit is None or transitions["one_sided_sign_p_value"] <= p_limit,
        requirement={"max_one_sided_p_value": p_limit},
        actual={"one_sided_sign_p_value": transitions["one_sided_sign_p_value"]},
        evidence="exact paired sign test over pass/fail transitions",
    )
    token_limit = policy["max_total_token_increase_ratio"]
    gates["token_cost"] = _gate(
        token_limit is None or (token_increase is not None and token_increase <= token_limit),
        requirement={"max_increase_ratio": token_limit},
        actual={"increase_ratio": token_increase},
        evidence="native token usage; null policy disables this gate",
    )
    latency_limit = policy["max_mean_latency_increase_ratio"]
    gates["latency"] = _gate(
        latency_limit is None or (latency_increase is not None and latency_increase <= latency_limit),
        requirement={"max_increase_ratio": latency_limit},
        actual={"increase_ratio": latency_increase},
        evidence="native latency; null policy disables this gate",
    )
    role = eval_record["role"]
    release_isolation = (
        role == "development"
        or (
            eval_record.get("frozen_before_candidate") is True
            and eval_record.get("candidate_access_to_case_details") is False
            and eval_record.get("private_oracle_exposed") is False
        )
    )
    gates["release_isolation"] = _gate(
        release_isolation,
        requirement="sealed release evaluation" if role == "release" else "development comparison",
        actual={
            "role": role,
            "frozen_before_candidate": eval_record.get("frozen_before_candidate"),
            "candidate_access_to_case_details": eval_record.get("candidate_access_to_case_details"),
            "private_oracle_exposed": eval_record.get("private_oracle_exposed"),
        },
        evidence=str(evaluation_manifest.expanduser().resolve()),
    )

    passed = all(gate["passed"] for gate in gates.values())
    if passed and role == "release":
        outcome, promotable = "promote", True
    elif passed:
        outcome, promotable = "development_pass", False
    elif not gates["target_identity"]["passed"] or not gates["case_parity"]["passed"] or not gates["sample_size"]["passed"] or not gates["validity"]["passed"]:
        outcome, promotable = "insufficient_evidence", False
    else:
        outcome, promotable = "reject", False

    output = out.expanduser().resolve()
    empty_output(output)
    created_at = _now()
    comparison = {
        "schema_version": "1.0",
        "stage": "target_comparison",
        "track": track,
        "candidate_id": candidate_record["candidate_id"],
        "baseline_id": candidate_record["baseline_id"],
        "evaluation_role": role,
        "evaluation_manifest": str(evaluation_manifest.expanduser().resolve()),
        "inputs": {
            "candidate_manifest": {"path": str(candidate_manifest.expanduser().resolve()), "sha256": _sha256_file(candidate_manifest.expanduser().resolve())},
            "evaluation_manifest": {"path": str(evaluation_manifest.expanduser().resolve()), "sha256": _sha256_file(evaluation_manifest.expanduser().resolve())},
            "policy": {"path": str(policy_path.expanduser().resolve()), "sha256": _sha256_file(policy_path.expanduser().resolve())},
            "baseline_results": [{"path": str(path.expanduser().resolve()), "sha256": _sha256_file(path.expanduser().resolve())} for path in baseline_results],
            "candidate_results": [{"path": str(path.expanduser().resolve()), "sha256": _sha256_file(path.expanduser().resolve())} for path in candidate_results],
            "baseline_regression_results": [{"path": str(path.expanduser().resolve()), "sha256": _sha256_file(path.expanduser().resolve())} for path in (baseline_regression_results or [])],
            "candidate_regression_results": [{"path": str(path.expanduser().resolve()), "sha256": _sha256_file(path.expanduser().resolve())} for path in (candidate_regression_results or [])],
        },
        "policy": {"path": str(policy_path.expanduser().resolve()), "values": policy},
        "safety": {"baseline": safety_baseline, "candidate": safety_candidate, "improvement": safety_improvement, "transitions": transitions},
        "regression": {
            "track": regression_track or track,
            "baseline": regression_baseline,
            "candidate": regression_candidate,
            "drop": regression_drop,
            "transitions": regression_transitions,
        },
        "cost": {"total_token_increase_ratio": token_increase, "mean_latency_increase_ratio": latency_increase},
        "missing_cases": {
            "baseline_only": [list(key) for key in sorted(baseline_keys - candidate_keys)],
            "candidate_only": [list(key) for key in sorted(candidate_keys - baseline_keys)],
        },
        "created_at": created_at,
    }
    decision = {
        "schema_version": "1.0",
        "stage": "promotion_decision",
        "decision": outcome,
        "promotable": promotable,
        "promotion_action_performed": False,
        "candidate_id": candidate_record["candidate_id"],
        "baseline_id": candidate_record["baseline_id"],
        "rollback_target_id": candidate_record["baseline_id"],
        "gates": gates,
        "next_action": (
            "record a reviewed release candidate; deployment remains external"
            if outcome == "promote" else
            "run an independent sealed release comparison"
            if outcome == "development_pass" else
            "repair evidence/coverage and rerun"
            if outcome == "insufficient_evidence" else
            "retain the baseline and revise or discard the candidate"
        ),
        "created_at": created_at,
    }
    write_json(output / "comparison.json", comparison)
    write_json(output / "decision.json", decision)
    write_json(output / "manifest.json", {
        "schema_version": "1.0",
        "stage": "target_evolution_evidence",
        "status": "completed",
        "candidate_manifest": str(candidate_manifest.expanduser().resolve()),
        "comparison": str(output / "comparison.json"),
        "decision": str(output / "decision.json"),
        "automatic_optimization": False,
        "automatic_promotion": False,
        "created_at": created_at,
    })
    return decision
