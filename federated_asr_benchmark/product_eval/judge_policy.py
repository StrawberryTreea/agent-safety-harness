from __future__ import annotations

import json
from pathlib import Path
from typing import Any


API_EVALUATION_BACKENDS = {"api_judge", "hybrid_rule_and_api_judge"}
EVALUATION_BACKENDS = {"deterministic", *API_EVALUATION_BACKENDS}


def load_and_validate_judge_policy(registry_path: Path) -> dict[str, Any]:
    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    policy = registry.get("api_judge_policy")
    if not isinstance(policy, dict):
        raise RuntimeError("benchmark registry is missing api_judge_policy")

    required = {
        "required_profile_id",
        "type",
        "model",
        "base_url",
        "api_key_env",
        "temperature",
    }
    missing = sorted(required - set(policy))
    if missing:
        raise RuntimeError(f"api_judge_policy is missing fields: {missing}")

    profile_id = str(policy["required_profile_id"])
    api_benchmarks = []
    deterministic_benchmarks = []
    for benchmark in registry.get("benchmarks", []):
        benchmark_id = str(benchmark.get("benchmark_id") or "")
        backend = str(benchmark.get("evaluation_backend") or "")
        if not benchmark_id or not backend:
            raise RuntimeError("every benchmark must declare benchmark_id and evaluation_backend")
        if backend not in EVALUATION_BACKENDS:
            raise RuntimeError(f"{benchmark_id} has unsupported evaluation_backend {backend!r}")
        if backend in API_EVALUATION_BACKENDS:
            if benchmark.get("api_judge_profile") != profile_id:
                raise RuntimeError(
                    f"{benchmark_id} must use API judge profile {profile_id}, "
                    f"got {benchmark.get('api_judge_profile')!r}"
                )
            api_benchmarks.append(benchmark_id)
        else:
            if benchmark.get("api_judge_profile") is not None:
                raise RuntimeError(f"deterministic benchmark {benchmark_id} cannot declare an API judge")
            deterministic_benchmarks.append(benchmark_id)

    return {
        "profile_id": profile_id,
        "type": str(policy["type"]),
        "model": str(policy["model"]),
        "base_url": str(policy["base_url"]).rstrip("/"),
        "api_key_env": str(policy["api_key_env"]),
        "temperature": float(policy["temperature"]),
        "api_benchmarks": api_benchmarks,
        "deterministic_benchmarks": deterministic_benchmarks,
    }


def validate_api_judge_binding(policy: dict[str, Any], actual: dict[str, Any]) -> None:
    expected = {
        "type": policy["type"],
        "id": policy["model"],
        "base_url": policy["base_url"],
        "key_env": policy["api_key_env"],
        "temperature": policy["temperature"],
    }
    normalized = {
        "type": actual.get("type"),
        "id": actual.get("id"),
        "base_url": str(actual.get("base_url") or "").rstrip("/"),
        "key_env": actual.get("key_env"),
        "temperature": float(actual.get("temperature", 0.0)),
    }
    if normalized != expected:
        raise RuntimeError(f"API judge does not match locked policy: expected {expected}, got {normalized}")
