from __future__ import annotations

import argparse
import importlib.metadata
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys


PACKAGE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PACKAGE_ROOT))

from product_eval.runner import ProductIdentity, ProductRunner, default_state_source
from product_eval.agentdyn import AgentDynAdapter
from product_eval.agentharm import AgentHarmAdapter
from product_eval.agentdojo import AgentDojoAdapter
from product_eval.agentleak import AgentLeakAdapter
from product_eval.agentpoison import AgentPoisonAdapter
from product_eval.injecagent import InjecAgentAdapter
from product_eval.judge_policy import load_and_validate_judge_policy, validate_api_judge_binding
from product_eval.api_judge import load_immutable_judge_profile
from product_eval.product_model_policy import (
    CANONICAL_PRODUCT_MODEL,
    PRODUCT_MODEL_BINDINGS,
    resolve_requested_model,
)
from product_eval.msb import MsbAdapter
from product_eval.qoder_byok_compat import (
    QODER_BYOK_CLI_VERSION,
    QoderByokProductRunner,
    qoder_cli_executable,
    resolve_qoder_byok_model,
)
from product_eval.saber import SaberAdapter
from tools.run_product import (
    DEFAULT_AGENTDYN_PYTHON,
    DEFAULT_AGENTDYN_ROOT,
    DEFAULT_AGENTDOJO_PYTHON,
    DEFAULT_AGENTDOJO_ROOT,
    DEFAULT_AGENTLEAK_ROOT,
    DEFAULT_AGENTPOISON_ROOT,
    DEFAULT_INJECAGENT_ROOT,
    DEFAULT_MSB_ROOT,
    DEFAULT_SABER_ROOT,
    DEFAULT_DATA_ROOT,
    benchmark_roots,
)


def command_version(command: list[str]) -> tuple[str | None, str | None]:
    executable = shutil.which(command[0])
    if executable is None:
        return None, f"executable not found: {command[0]}"
    try:
        completed = subprocess.run(
            [executable, *command[1:]],
            text=True,
            encoding="utf-8",
            errors="replace",
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=30,
            check=False,
        )
    except Exception as exc:
        return None, f"{type(exc).__name__}: {exc}"
    if completed.returncode != 0:
        return None, completed.stderr.strip() or f"exit {completed.returncode}"
    return completed.stdout.strip(), None


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify products, benchmark sources, and judge policy.")
    parser.add_argument("--data-root", type=Path, default=DEFAULT_DATA_ROOT)
    args = parser.parse_args()
    roots = benchmark_roots(args.data_root)
    failed = False
    tested_profiles = json.loads(
        (PACKAGE_ROOT / "release" / "tested_profiles.json").read_text(encoding="utf-8")
    )
    expected_pins = tested_profiles["runtime_pins"]
    node_version, node_error = command_version(["node", "--version"])
    npm_version, npm_error = command_version(["npm", "--version"])
    try:
        uv_version = importlib.metadata.version("uv")
    except importlib.metadata.PackageNotFoundError:
        uv_version = None
    observed_pins = {
        "python": ".".join(str(value) for value in sys.version_info[:3]),
        "uv": uv_version,
        "node": node_version.lstrip("v") if node_version else None,
        "npm": npm_version,
    }
    runtime_pin_errors = [
        f"{name}: observed {observed_pins.get(name)!r}, expected {expected!r}"
        for name, expected in expected_pins.items()
        if name in observed_pins and observed_pins.get(name) != expected
    ]
    for name, error in (("node", node_error), ("npm", npm_error)):
        if error:
            runtime_pin_errors.append(f"{name}: {error}")
    try:
        judge_policy = load_and_validate_judge_policy(PACKAGE_ROOT / "benchmark_registry.json")
        judge_policy_error = None
    except Exception as exc:
        judge_policy = None
        judge_policy_error = f"{type(exc).__name__}: {exc}"
    try:
        immutable_judge_profile = load_immutable_judge_profile(
            PACKAGE_ROOT / "release" / "evaluation_config.json",
            PACKAGE_ROOT / "benchmark_registry.json",
        )
        immutable_judge_profile_error = None
    except Exception as exc:
        immutable_judge_profile = None
        immutable_judge_profile_error = f"{type(exc).__name__}: {exc}"
        failed = True

    registry = json.loads((PACKAGE_ROOT / "product_registry.json").read_text(encoding="utf-8"))
    request_model_checks = []
    for request_path in sorted((PACKAGE_ROOT / "examples" / "requests").rglob("*.json")):
        request = json.loads(request_path.read_text(encoding="utf-8"))
        product = request["product"]
        try:
            resolver = (
                resolve_qoder_byok_model
                if product["product_id"] == "qoder"
                else resolve_requested_model
            )
            resolved_model = resolver(product["product_id"], product.get("product_model"))
            error = None
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
            failed = True
        request_model_checks.append(
            {
                "request": request_path.relative_to(PACKAGE_ROOT).as_posix(),
                "product_id": product["product_id"],
                "request_model": product.get("product_model"),
                "resolved_model": resolved_model if error is None else None,
                "error": error,
            }
        )
    results = []
    failed = failed or judge_policy_error is not None
    required_credentials = {
        "codex": "DEEPSEEK_API_KEY",
        "claude_code": "ANTHROPIC_API_KEY",
        "qoder": "DEEPSEEK_API_KEY",
        "codebuddy": "DEEPSEEK_API_KEY",
    }
    for product in registry["products"]:
        product_id = product["product_id"]
        source = default_state_source(product_id)
        credential_env = required_credentials[product_id]
        credential_supplied = bool(os.environ.get(credential_env))
        executable = product["executable"]
        version = product["detected_version"]
        runner_type = ProductRunner
        if product_id == "qoder":
            executable = str(qoder_cli_executable(PACKAGE_ROOT))
            version = QODER_BYOK_CLI_VERSION
            runner_type = QoderByokProductRunner
        runner = runner_type(
            ProductIdentity(
                product_id=product_id,
                executable=executable,
                version=version,
                state_source=str(source),
            )
        )
        try:
            version_output = runner.verify_version()
            error = None
        except Exception as exc:
            version_output = None
            error = f"{type(exc).__name__}: {exc}"
            failed = True
        state_exists = source.is_dir()
        state_requirement_met = product_id == "codebuddy" or state_exists
        if not state_requirement_met or not credential_supplied:
            failed = True
        results.append(
            {
                "product_id": product_id,
                "version_output": version_output,
                "state_source": str(source),
                "state_source_exists": state_exists,
                "state_source_required": product_id != "codebuddy",
                "state_requirement_met": state_requirement_met,
                "api_credential_env": credential_env,
                "api_credential_supplied": credential_supplied,
                "built_in_quota_allowed": False,
                "product_native_credentials_inherited": False,
                "error": error,
            }
        )

    try:
        docker = subprocess.run(
            ["docker", "version", "--format", "{{.Server.Version}}"],
            text=True,
            encoding="utf-8",
            errors="replace",
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=30,
            check=False,
        )
        docker_version = docker.stdout.strip() if docker.returncode == 0 else None
        docker_error = docker.stderr.strip() if docker.returncode != 0 else None
        failed = failed or docker.returncode != 0
    except Exception as exc:
        docker_version = None
        docker_error = f"{type(exc).__name__}: {exc}"
        failed = True

    image = subprocess.run(
        ["docker", "image", "inspect", "osbench-sandbox", "--format", "{{.Id}}"],
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=30,
        check=False,
    )
    saber_image_id = image.stdout.strip() if image.returncode == 0 else None
    saber_image_error = image.stderr.strip() if image.returncode != 0 else None
    failed = failed or image.returncode != 0 or not saber_image_id
    observed_pins["docker_server"] = docker_version
    observed_pins["saber_image"] = (
        f"osbench-sandbox@{saber_image_id}" if saber_image_id else None
    )
    for name in ("docker_server", "saber_image"):
        if observed_pins[name] != expected_pins[name]:
            runtime_pin_errors.append(
                f"{name}: observed {observed_pins[name]!r}, expected {expected_pins[name]!r}"
            )
    try:
        saber_judge = SaberAdapter(roots["saber"]).preflight_judge()
        if judge_policy is None:
            raise RuntimeError("global API judge policy is unavailable")
        validate_api_judge_binding(judge_policy, saber_judge)
        saber_judge_error = None
    except Exception as exc:
        saber_judge = None
        saber_judge_error = f"{type(exc).__name__}: {exc}"
        failed = True
    try:
        agentdojo = AgentDojoAdapter(roots["agentdojo"], DEFAULT_AGENTDOJO_PYTHON).preflight()
        agentdojo_error = None
    except Exception as exc:
        agentdojo = None
        agentdojo_error = f"{type(exc).__name__}: {exc}"
        failed = True
    try:
        agentdyn = AgentDynAdapter(roots["agentdyn"], DEFAULT_AGENTDYN_PYTHON).preflight()
        agentdyn_error = None
    except Exception as exc:
        agentdyn = None
        agentdyn_error = f"{type(exc).__name__}: {exc}"
        failed = True
    try:
        injecagent = InjecAgentAdapter(roots["injecagent"]).preflight()
        injecagent_error = None
    except Exception as exc:
        injecagent = None
        injecagent_error = f"{type(exc).__name__}: {exc}"
        failed = True
    benchmark_preflights = {
        "agentdojo": {"result": agentdojo, "error": agentdojo_error},
        "agentdyn": {"result": agentdyn, "error": agentdyn_error},
        "injecagent": {"result": injecagent, "error": injecagent_error},
    }
    for benchmark_id, adapter in (
        (
            "agentharm",
            AgentHarmAdapter(roots["agentharm_data"], roots["agentharm_runtime"]),
        ),
        ("msb", MsbAdapter(roots["msb"])),
        ("agentleak", AgentLeakAdapter(roots["agentleak"])),
        ("agentpoison_strategyqa", AgentPoisonAdapter(roots["agentpoison"])),
    ):
        try:
            result = adapter.preflight()
            error = None
        except Exception as exc:
            result = None
            error = f"{type(exc).__name__}: {exc}"
            failed = True
        benchmark_preflights[benchmark_id] = {"result": result, "error": error}
    for name, preflight in (("agentdojo", agentdojo), ("agentdyn", agentdyn)):
        version = None
        error = None
        if preflight is not None:
            version, error = command_version(
                [preflight["python"], "-c", "import sys; print('.'.join(map(str, sys.version_info[:3])))"]
            )
        if error or version != expected_pins["python"]:
            runtime_pin_errors.append(
                f"{name}_python: observed {version!r}, expected {expected_pins['python']!r}"
                + (f" ({error})" if error else "")
            )
    formal_products = {product["product_id"] for product in registry["products"]}
    release_blockers = [
        f"{product_id}: {profile['status']}"
        for product_id, profile in tested_profiles["products"].items()
        if product_id in formal_products and profile.get("status", "ready") != "ready"
    ]
    failed = failed or bool(runtime_pin_errors) or bool(release_blockers)
    output = {
        "status": "failed" if failed else "passed",
        "products": results,
        "docker_version": docker_version,
        "docker_error": docker_error,
        "saber_image_id": saber_image_id,
        "saber_image_error": saber_image_error,
        "saber_judge": saber_judge,
        "saber_judge_error": saber_judge_error,
        "judge_policy": judge_policy,
        "judge_policy_error": judge_policy_error,
        "immutable_judge_profile": immutable_judge_profile,
        "immutable_judge_profile_error": immutable_judge_profile_error,
        "runtime_pins": {
            "expected": expected_pins,
            "observed": observed_pins,
            "errors": runtime_pin_errors,
        },
        "release_blockers": release_blockers,
        "product_model_policy": {
            "canonical_model": CANONICAL_PRODUCT_MODEL,
            "bindings": PRODUCT_MODEL_BINDINGS,
            "request_checks": request_model_checks,
        },
        "agentdojo": agentdojo,
        "agentdojo_error": agentdojo_error,
        "agentdyn": agentdyn,
        "agentdyn_error": agentdyn_error,
        "injecagent": injecagent,
        "injecagent_error": injecagent_error,
        "benchmark_preflights": benchmark_preflights,
    }
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
