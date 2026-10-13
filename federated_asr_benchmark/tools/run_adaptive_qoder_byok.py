from __future__ import annotations

import json
from pathlib import Path
import sys
from typing import Any


PACKAGE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PACKAGE_ROOT))

from tools import run_adaptive as target
from product_eval.qoder_byok_compat import (
    QODER_BYOK_CUSTOM_MODEL_ID,
    QODER_BYOK_PROVIDER,
    QODER_BYOK_PROVIDER_MODEL,
    QODER_BYOK_TYPE,
)


THIS_FILE = Path(__file__).resolve()
original_validate_case_artifacts = target.validate_case_artifacts


def validate_qoder_byok_case_artifacts(
    root: Path,
    *,
    expected_product: str,
    expected_model: str,
    expected_release_id: str | None = None,
    require_evaluation_metadata: bool = False,
) -> tuple[bool, str | None, str | None, dict[str, Any] | None]:
    if expected_product != "qoder":
        return original_validate_case_artifacts(
            root,
            expected_product=expected_product,
            expected_model=expected_model,
            expected_release_id=expected_release_id,
            require_evaluation_metadata=require_evaluation_metadata,
        )

    required = (
        "prompt.txt",
        "mcp.json",
        "product_run.json",
        "native_result.json",
        "case_result.json",
        "product_stdout.jsonl",
        "product_stderr.txt",
        "final_response.txt",
        "product_network_route.json",
    )
    missing = [name for name in required if not (root / name).is_file()]
    if missing:
        detail = f"missing artifacts: {', '.join(missing)}"
        return False, "missing_artifact", detail, None
    try:
        run = target.read_json(root / "product_run.json")
        result = target.read_json(root / "case_result.json")
        route = target.read_json(root / "product_network_route.json")
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        return False, "invalid_artifact", f"unreadable JSON: {exc}", None

    if run.get("status") != "completed" or run.get("timed_out"):
        detail = str(run.get("error") or f"product status={run.get('status')}")
        return False, target.classify_artifact_error(detail), detail, result
    product = run.get("product") or {}
    if product.get("product_id") != "qoder" or product.get("product_model") != expected_model:
        detail = f"effective route {product.get('product_id')}/{product.get('product_model')}"
        return False, "model_route", detail, result

    expected_route = {
        "route_mode": "qoder_byok_custom_pool",
        "provider": QODER_BYOK_PROVIDER,
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

    if result.get("status") not in target.VALID_RESULTS:
        detail = f"case result status={result.get('status')}"
        return False, "invalid_artifact", detail, result
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
            evidence = target.read_json(judge_path)
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            return False, "invalid_artifact", f"invalid AgentHarm judge evidence: {exc}", result
        expected_judge = {
            "profile_id": "deepseek_v4_flash",
            "type": "openai",
            "model": "deepseek-v4-flash",
            "base_url": "https://api.deepseek.com",
            "api_key_env": "DEEPSEEK_API_KEY",
            "temperature": 0.0,
        }
        if (evidence.get("judge") or {}) != expected_judge:
            return False, "model_route", "AgentHarm judge binding drifted", result
    if result.get("benchmark_id") == "saber":
        valid, kind, detail = target.validate_saber_case_artifacts(root, result)
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
    stderr = (root / "product_stderr.txt").read_text(
        encoding="utf-8", errors="replace"
    ).strip()
    if stderr:
        return False, target.classify_artifact_error(stderr), stderr[-2000:], result
    return True, None, None, result


def worker_command(config_path: Path, product: str, worker_id: str) -> list[str]:
    return [
        sys.executable,
        str(THIS_FILE),
        "worker",
        "--config",
        str(config_path),
        "--product",
        product,
        "--worker-id",
        worker_id,
    ]


def configure_target() -> None:
    target.RUN_ONE = THIS_FILE.with_name("run_product_qoder_byok.py")
    target.validate_product_release_gates = lambda products: None
    target.validate_case_artifacts = validate_qoder_byok_case_artifacts
    target.worker_command = worker_command


if __name__ == "__main__":
    configure_target()
    raise SystemExit(target.main())
