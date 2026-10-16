"""Execute paired internal-Agent target evaluations with frozen local inputs."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
from typing import Any

import yaml

from scripts.result_analysis import analyze, empty_output, write_json
from .target_loop import _asset_identity, _load_policy, compare_target


ROOT = Path(__file__).resolve().parents[1]
LOCAL_STUBS = {"safe_stub", "unsafe_stub"}
ENV_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
SENSITIVE_ENV_NAME = re.compile(r"(?:API_?KEY|AUTH_?TOKEN|SECRET|PASSWORD|CREDENTIAL)", re.IGNORECASE)
MODEL_CONFIG_FIELDS = {
    "provider", "base_url", "api_key_env", "model", "temperature", "max_tokens",
    "timeout_seconds", "system_prompt",
}
CLI_CONFIG_FIELDS = {
    "backend", "command", "timeout_seconds", "pass_prompt_stdin", "response_file",
    "inherit_env", "env_allowlist", "env", "benchmark_mcp_server_command",
    "isolate_env_dirs", "system_prompt",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _combined_sha256(*digests: str) -> str:
    value = hashlib.sha256()
    for digest in digests:
        value.update(bytes.fromhex(digest))
    return value.hexdigest()


def _sha256_value(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _load_mapping(path: Path, *, label: str) -> dict[str, Any]:
    resolved = path.expanduser().resolve()
    if resolved.suffix.lower() not in {".json", ".yaml", ".yml"}:
        raise ValueError(f"{label} must be JSON or YAML: {resolved}")
    value = yaml.safe_load(resolved.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise ValueError(f"{label} must contain an object: {resolved}")
    return value


def _resolve_path(base: Path, value: Any, *, label: str) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-empty path string")
    path = Path(value).expanduser()
    return (path if path.is_absolute() else base / path).resolve()


def _jsonl_index(path: Path, *, label: str) -> dict[str, dict[str, Any]]:
    rows: dict[str, dict[str, Any]] = {}
    with path.open(encoding="utf-8-sig") as handle:
        for line_no, line in enumerate(handle, 1):
            if not line.strip():
                continue
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ValueError(f"{label} row must be an object at {path}:{line_no}")
            task_id = value.get("task_id")
            if not isinstance(task_id, str) or not task_id.strip():
                raise ValueError(f"{label} row lacks task_id at {path}:{line_no}")
            if task_id in rows:
                raise ValueError(f"Duplicate {label} task_id {task_id!r} at {path}:{line_no}")
            rows[task_id] = value
    if not rows:
        raise ValueError(f"{label} is empty: {path}")
    return rows


def _load_population(job_dir: Path, name: str, value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"Job {name} must be an object")
    unknown = sorted(set(value) - {"id", "public_tasks", "private_rules", "expected_case_count"})
    if unknown:
        raise ValueError(f"Unknown job {name} fields: {', '.join(unknown)}")
    population_id = value.get("id")
    expected = value.get("expected_case_count")
    if not isinstance(population_id, str) or not population_id.strip():
        raise ValueError(f"Job {name}.id must be non-empty")
    if type(expected) is not int or expected <= 0:
        raise ValueError(f"Job {name}.expected_case_count must be positive")
    public = _resolve_path(job_dir, value.get("public_tasks"), label=f"{name}.public_tasks")
    private = _resolve_path(job_dir, value.get("private_rules"), label=f"{name}.private_rules")
    public_rows = _jsonl_index(public, label=f"{name} public task")
    private_rows = _jsonl_index(private, label=f"{name} private rule")
    if set(public_rows) != set(private_rows):
        missing_private = sorted(set(public_rows) - set(private_rows))
        missing_public = sorted(set(private_rows) - set(public_rows))
        raise ValueError(
            f"Job {name} public/private task IDs differ; missing_private={missing_private}, missing_public={missing_public}"
        )
    if len(public_rows) != expected:
        raise ValueError(f"Job {name} expected {expected} cases but found {len(public_rows)}")
    if any("evaluation" in row for row in public_rows.values()):
        raise ValueError(f"Job {name} public tasks must not contain private evaluation rules")
    if any(not isinstance(row.get("evaluation"), dict) or not row["evaluation"] for row in private_rows.values()):
        raise ValueError(f"Job {name} private rules must contain non-empty evaluation objects")
    public_sha = _sha256_file(public)
    private_sha = _sha256_file(private)
    return {
        "id": population_id,
        "public_tasks": public,
        "private_rules": private,
        "expected_case_count": expected,
        "public_sha256": public_sha,
        "private_sha256": private_sha,
        "combined_sha256": _combined_sha256(public_sha, private_sha),
        "task_ids": sorted(public_rows),
    }


def _load_job(path: Path) -> dict[str, Any]:
    resolved = path.expanduser().resolve()
    value = _load_mapping(resolved, label="Target evaluation job")
    known = {
        "schema_version", "track", "role", "safety", "regression", "execution",
        "frozen_before_candidate", "candidate_access_to_case_details", "private_oracle_exposed",
    }
    unknown = sorted(set(value) - known)
    if unknown:
        raise ValueError(f"Unknown target evaluation job fields: {', '.join(unknown)}")
    if value.get("schema_version") != "1.0":
        raise ValueError("Target evaluation job schema_version must be 1.0")
    if value.get("track") != "agent_execution":
        raise ValueError("target-evaluate currently supports agent_execution only")
    role = value.get("role")
    if role not in {"development", "release"}:
        raise ValueError("Target evaluation job role must be development or release")
    for field in ("frozen_before_candidate", "candidate_access_to_case_details", "private_oracle_exposed"):
        if type(value.get(field)) is not bool:
            raise ValueError(f"Target evaluation job {field} must be boolean")
    if value["private_oracle_exposed"] is not False:
        raise ValueError("Target evaluation jobs must keep private oracles hidden")
    if role == "release" and (
        value["frozen_before_candidate"] is not True or value["candidate_access_to_case_details"] is not False
    ):
        raise ValueError("Release jobs require a pre-frozen set hidden from the candidate process")
    execution = value.get("execution", {})
    if not isinstance(execution, dict):
        raise ValueError("Target evaluation job execution must be an object")
    unknown_execution = sorted(set(execution) - {"workers", "max_attempts", "timeout_seconds"})
    if unknown_execution:
        raise ValueError(f"Unknown target evaluation execution fields: {', '.join(unknown_execution)}")
    workers = execution.get("workers", 1)
    attempts = execution.get("max_attempts", 1)
    timeout = execution.get("timeout_seconds", 120)
    if type(workers) is not int or not 1 <= workers <= 64:
        raise ValueError("Target evaluation workers must be an integer from 1 to 64")
    if type(attempts) is not int or not 1 <= attempts <= 3:
        raise ValueError("Target evaluation max_attempts must be an integer from 1 to 3")
    if type(timeout) is not int or not 1 <= timeout <= 3600:
        raise ValueError("Target evaluation timeout_seconds must be an integer from 1 to 3600")
    safety = _load_population(resolved.parent, "safety", value.get("safety"))
    regression = _load_population(resolved.parent, "regression", value.get("regression"))
    overlap = sorted(set(safety["task_ids"]) & set(regression["task_ids"]))
    if overlap:
        raise ValueError(f"Safety and regression populations must be disjoint; overlapping task_ids={overlap}")
    if safety["id"] == regression["id"]:
        raise ValueError("Safety and regression populations must use different IDs")
    return {
        "path": resolved,
        "sha256": _sha256_file(resolved),
        "track": "agent_execution",
        "role": role,
        "frozen_before_candidate": value["frozen_before_candidate"],
        "candidate_access_to_case_details": value["candidate_access_to_case_details"],
        "private_oracle_exposed": False,
        "execution": {"workers": workers, "max_attempts": attempts, "timeout_seconds": timeout},
        "safety": safety,
        "regression": regression,
    }


def _within(root: Path, path: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _validate_system_prompt(value: Any, *, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-empty string")
    if len(value) > 100_000:
        raise ValueError(f"{label} exceeds the 100000-character limit")
    return value


def _validate_model_config(value: Any, *, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an object")
    unknown = sorted(set(value) - MODEL_CONFIG_FIELDS)
    if unknown:
        raise ValueError(f"Unknown {label} fields: {', '.join(unknown)}")
    if value.get("provider") != "openai_compatible":
        raise ValueError(f"{label}.provider must be openai_compatible")
    result = dict(value)
    for field in ("base_url", "api_key_env", "model"):
        if not isinstance(result.get(field), str) or not result[field].strip():
            raise ValueError(f"{label}.{field} must be a non-empty string")
    if not ENV_NAME.fullmatch(result["api_key_env"]):
        raise ValueError(f"{label}.api_key_env must name an environment variable")
    temperature = result.get("temperature", 0.0)
    if isinstance(temperature, bool) or not isinstance(temperature, (int, float)) or not 0 <= temperature <= 2:
        raise ValueError(f"{label}.temperature must be a number from 0 to 2")
    max_tokens = result.get("max_tokens", 512)
    if type(max_tokens) is not int or not 1 <= max_tokens <= 1_000_000:
        raise ValueError(f"{label}.max_tokens must be an integer from 1 to 1000000")
    timeout = result.get("timeout_seconds", 60)
    if type(timeout) is not int or not 1 <= timeout <= 3600:
        raise ValueError(f"{label}.timeout_seconds must be an integer from 1 to 3600")
    if "system_prompt" in result:
        result["system_prompt"] = _validate_system_prompt(
            result["system_prompt"], label=f"{label}.system_prompt"
        )
    return result


def _validate_cli_config(value: Any, *, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an object")
    unknown = sorted(set(value) - CLI_CONFIG_FIELDS)
    if unknown:
        raise ValueError(f"Unknown {label} fields: {', '.join(unknown)}")
    if value.get("backend") != "cli":
        raise ValueError(f"{label}.backend must be cli")
    command = value.get("command")
    if not isinstance(command, list) or not command or not all(isinstance(item, str) and item for item in command):
        raise ValueError(f"{label}.command must be a non-empty string array")
    result = dict(value)
    timeout = result.get("timeout_seconds", 120)
    if type(timeout) is not int or not 1 <= timeout <= 3600:
        raise ValueError(f"{label}.timeout_seconds must be an integer from 1 to 3600")
    for field, default in (("pass_prompt_stdin", True), ("inherit_env", False)):
        item = result.get(field, default)
        if type(item) is not bool:
            raise ValueError(f"{label}.{field} must be a boolean")
    if result.get("inherit_env", False):
        raise ValueError(
            f"{label}.inherit_env must be false for controlled target execution; "
            "use env_allowlist for required inherited variables"
        )
    for field in ("env_allowlist", "benchmark_mcp_server_command", "isolate_env_dirs"):
        item = result.get(field, [])
        if not isinstance(item, list) or not all(isinstance(entry, str) and entry for entry in item):
            raise ValueError(f"{label}.{field} must be a string array")
    for field in ("env_allowlist", "isolate_env_dirs"):
        invalid_names = sorted(name for name in result.get(field, []) if not ENV_NAME.fullmatch(name))
        if invalid_names:
            raise ValueError(f"{label}.{field} contains invalid environment variable names: {', '.join(invalid_names)}")
    response_file = result.get("response_file")
    if response_file is not None and (not isinstance(response_file, str) or not response_file.strip()):
        raise ValueError(f"{label}.response_file must be a non-empty string when provided")
    env = result.get("env", {})
    if not isinstance(env, dict) or not all(isinstance(key, str) and isinstance(item, str) for key, item in env.items()):
        raise ValueError(f"{label}.env must be a string-to-string object")
    invalid_env_names = sorted(key for key in env if not ENV_NAME.fullmatch(key))
    if invalid_env_names:
        raise ValueError(f"{label}.env contains invalid environment variable names: {', '.join(invalid_env_names)}")
    sensitive = sorted(key for key in env if SENSITIVE_ENV_NAME.search(key))
    if sensitive:
        raise ValueError(
            f"{label}.env must not contain literal credential variables ({', '.join(sensitive)}); "
            "inherit their names through env_allowlist"
        )
    if "system_prompt" in result:
        result["system_prompt"] = _validate_system_prompt(
            result["system_prompt"], label=f"{label}.system_prompt"
        )
    return result


def _selected_config(path: str, *, collection: str, target_id: str, label: str) -> dict[str, Any]:
    source = _load_mapping(Path(path), label=label)
    entries = source.get(collection)
    if not isinstance(entries, dict) or target_id not in entries:
        known = ", ".join(sorted(entries)) if isinstance(entries, dict) else ""
        raise ValueError(f"{label} does not define {target_id!r}. Known: {known}")
    selected = entries[target_id]
    if not isinstance(selected, dict):
        raise ValueError(f"{label} entry {target_id!r} must be an object")
    return dict(selected)


def _load_prompt_overlay(config: dict[str, Any], *, side: str) -> dict[str, Any]:
    prompt = config.get("prompt")
    if prompt is None:
        return {}
    if not isinstance(prompt, dict):
        raise ValueError(f"{side.capitalize()} target prompt must be an object")
    unknown = sorted(set(prompt) - {"system_prompt"})
    if unknown:
        raise ValueError(f"Unknown {side} target prompt fields: {', '.join(unknown)}")
    if "system_prompt" not in prompt:
        raise ValueError(f"{side.capitalize()} target prompt requires system_prompt")
    return {"system_prompt": _validate_system_prompt(prompt["system_prompt"], label=f"{side}.prompt.system_prompt")}


def _load_policy_overlay(config: dict[str, Any], *, side: str, backend: str) -> dict[str, Any]:
    policy = config.get("policy")
    if policy is None:
        return {}
    if not isinstance(policy, dict):
        raise ValueError(f"{side.capitalize()} target policy must be an object")
    allowed = {"temperature", "max_tokens", "timeout_seconds"} if backend == "model" else {"timeout_seconds"}
    unknown = sorted(set(policy) - allowed)
    if unknown:
        raise ValueError(f"Unknown {side} target {backend} policy fields: {', '.join(unknown)}")
    probe = {
        "provider": "openai_compatible",
        "base_url": "https://validation.invalid/v1",
        "api_key_env": "VALIDATION_API_KEY",
        "model": "validation",
        **policy,
    } if backend == "model" else {
        "backend": "cli",
        "command": ["validation"],
        **policy,
    }
    validated = _validate_model_config(probe, label=f"{side}.policy") if backend == "model" else _validate_cli_config(
        probe, label=f"{side}.policy"
    )
    return {key: validated[key] for key in policy}


def _public_target(target: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in target.items() if not key.startswith("_")}


def _materialize_target_configuration(target: dict[str, Any], output: Path) -> tuple[list[str], dict[str, Any] | None]:
    effective = target.get("_effective_config")
    if effective is None:
        return [], None
    config_dir = output / "runtime_config"
    config_dir.mkdir(parents=True, exist_ok=True)
    if target["agent_id"].startswith(("model:", "reference:")):
        filename, option = "models.json", "--models-config"
    else:
        filename, option = "agents.json", "--agents-config"
    path = config_dir / filename
    write_json(path, effective)
    sha256 = _sha256_file(path)
    path.chmod(stat.S_IREAD)
    return [option, str(path)], {"path": str(path), "sha256": sha256, "read_only": True}


def _load_target(
    identity: Any,
    *,
    side: str,
    allow_external_targets: bool,
    require_external_authorization: bool = True,
) -> dict[str, Any]:
    if not isinstance(identity, dict) or not isinstance(identity.get("path"), str):
        raise ValueError(f"Candidate manifest is missing the {side} target asset")
    current = _asset_identity(Path(identity["path"]))
    if any(current.get(key) != identity.get(key) for key in ("kind", "sha256", "file_count")):
        raise ValueError(f"{side.capitalize()} target asset changed after registration")
    asset_path = Path(identity["path"]).expanduser().resolve()
    if current["kind"] == "directory":
        roots = [asset_path / name for name in ("target.yaml", "target.yml", "target.json") if (asset_path / name).is_file()]
        if len(roots) != 1:
            raise ValueError(f"{side.capitalize()} target directory must contain exactly one target.yaml/yml/json")
        config_path = roots[0]
        asset_root = asset_path
    else:
        config_path = asset_path
        asset_root = None
    config = _load_mapping(config_path, label=f"{side.capitalize()} target configuration")
    unknown = sorted(set(config) - {
        "schema_version", "track", "agent_id", "models_config", "agents_config",
        "model", "cli", "prompt", "policy",
    })
    if unknown:
        raise ValueError(f"Unknown {side} target configuration fields: {', '.join(unknown)}")
    if config.get("schema_version") != "1.0" or config.get("track") != "agent_execution":
        raise ValueError(f"{side.capitalize()} target configuration must be schema 1.0 for agent_execution")
    agent_id = config.get("agent_id")
    if not isinstance(agent_id, str) or not agent_id.strip():
        raise ValueError(f"{side.capitalize()} target configuration requires agent_id")
    is_external = agent_id not in LOCAL_STUBS
    if is_external and require_external_authorization and not allow_external_targets:
        raise ValueError(
            f"{side.capitalize()} target {agent_id!r} may call an external model or CLI; rerun with --allow-external-targets"
        )
    paths: dict[str, str] = {}
    for field in ("models_config", "agents_config"):
        declared = config.get(field)
        if declared is None:
            continue
        if asset_root is None:
            raise ValueError(f"{side.capitalize()} {field} is allowed only inside a registered target directory")
        resolved = _resolve_path(asset_root, declared, label=f"{side}.{field}")
        if not _within(asset_root, resolved) or not resolved.is_file():
            raise ValueError(f"{side.capitalize()} {field} must be a file inside the registered target directory")
        paths[field] = str(resolved)
    if agent_id not in LOCAL_STUBS and not agent_id.startswith(("model:", "reference:", "cli:")):
        raise ValueError(f"Unsupported {side} agent_id: {agent_id}")
    if agent_id in LOCAL_STUBS and paths:
        raise ValueError(f"Local stub target {side} must not declare external configuration")
    prompt_overlay = _load_prompt_overlay(config, side=side)
    effective_config: dict[str, Any] | None = None
    configuration_mode = "stub"
    if agent_id in LOCAL_STUBS:
        forbidden = sorted(set(config) & {"model", "cli", "prompt", "policy"})
        if forbidden:
            raise ValueError(f"Local stub target {side} cannot declare: {', '.join(forbidden)}")
    elif agent_id.startswith(("model:", "reference:")):
        model_id = agent_id.split(":", 1)[1]
        if not model_id:
            raise ValueError(f"{side.capitalize()} model target requires an ID after the prefix")
        if "cli" in config or "agents_config" in paths:
            raise ValueError(f"{side.capitalize()} model target cannot declare CLI configuration")
        if "model" in config and "models_config" in paths:
            raise ValueError(f"{side.capitalize()} model target must choose inline model or models_config, not both")
        if "model" in config:
            model_config = _validate_model_config(config["model"], label=f"{side}.model")
            configuration_mode = "inline"
        elif "models_config" in paths:
            model_config = _validate_model_config(
                _selected_config(
                    paths["models_config"], collection="models", target_id=model_id,
                    label=f"{side}.models_config",
                ),
                label=f"{side}.models_config.models.{model_id}",
            )
            configuration_mode = "registered_config"
        else:
            raise ValueError(f"{side.capitalize()} model target requires inline model or models_config")
        if agent_id.startswith("reference:") and (prompt_overlay or "system_prompt" in model_config):
            raise ValueError(f"{side.capitalize()} reference target does not accept system prompt overrides")
        model_config.update(prompt_overlay)
        model_config.update(_load_policy_overlay(config, side=side, backend="model"))
        model_config = _validate_model_config(model_config, label=f"{side}.effective_model")
        effective_config = {"models": {model_id: model_config}}
    elif agent_id.startswith("cli:"):
        cli_id = agent_id.split(":", 1)[1]
        if not cli_id:
            raise ValueError(f"{side.capitalize()} CLI target requires an ID after the prefix")
        if "model" in config or "models_config" in paths:
            raise ValueError(f"{side.capitalize()} CLI target cannot declare model configuration")
        if "cli" in config and "agents_config" in paths:
            raise ValueError(f"{side.capitalize()} CLI target must choose inline cli or agents_config, not both")
        if "cli" in config:
            cli_config = _validate_cli_config(config["cli"], label=f"{side}.cli")
            configuration_mode = "inline"
        elif "agents_config" in paths:
            cli_config = _validate_cli_config(
                _selected_config(
                    paths["agents_config"], collection="agents", target_id=cli_id,
                    label=f"{side}.agents_config",
                ),
                label=f"{side}.agents_config.agents.{cli_id}",
            )
            configuration_mode = "registered_config"
        else:
            raise ValueError(f"{side.capitalize()} CLI target requires inline cli or agents_config")
        cli_config.update(prompt_overlay)
        cli_config.update(_load_policy_overlay(config, side=side, backend="cli"))
        cli_config = _validate_cli_config(cli_config, label=f"{side}.effective_cli")
        effective_config = {"agents": {cli_id: cli_config}}
    elif agent_id not in LOCAL_STUBS:
        raise ValueError(f"Unsupported {side} agent_id: {agent_id}")
    return {
        "side": side,
        "agent_id": agent_id,
        "asset": current,
        "config_path": str(config_path),
        "models_config": paths.get("models_config"),
        "agents_config": paths.get("agents_config"),
        "configuration_mode": configuration_mode,
        "prompt_applied": bool(prompt_overlay),
        "prompt_sha256": _sha256_value(prompt_overlay) if prompt_overlay else None,
        "policy_sha256": _sha256_value(config["policy"]) if "policy" in config else None,
        "effective_config_sha256": _sha256_value(effective_config) if effective_config is not None else None,
        "external_execution_authorized": bool(is_external and allow_external_targets),
        "_effective_config": effective_config,
    }


def _run_population(
    *,
    target: dict[str, Any],
    population: dict[str, Any],
    output: Path,
    execution: dict[str, int],
) -> dict[str, Any]:
    empty_output(output)
    manifest_path = output / "manifest.json"
    ash = str(ROOT / "harness/ash.py")
    configuration, runtime_config = _materialize_target_configuration(target, output)
    commands = [
        ("capture", [
            sys.executable, ash, "capture", "--agent", target["agent_id"],
            "--input", str(population["public_tasks"]), "--workers", str(execution["workers"]),
            "--max-attempts", str(execution["max_attempts"]), "--out", str(output / "capture"),
            *configuration,
        ]),
        ("score", [
            sys.executable, ash, "score", "--private-input", str(population["private_rules"]),
            "--capture-run", str(output / "capture"), "--out", str(output / "score"),
        ]),
        ("report", [sys.executable, ash, "report", "--run", str(output / "score")]),
    ]
    manifest: dict[str, Any] = {
        "schema_version": "1.0",
        "stage": "target_native_evaluation",
        "status": "started",
        "target": _public_target(target),
        "runtime_config": runtime_config,
        "population": {
            key: str(value) if isinstance(value, Path) else value
            for key, value in population.items() if key != "task_ids"
        },
        "steps": [],
        "created_at": _now(),
    }
    write_json(manifest_path, manifest)
    env = {**os.environ, "PYTHONUTF8": "1"}
    try:
        for name, argv in commands:
            started = _now()
            with (output / f"{name}.log").open("w", encoding="utf-8") as log:
                process = subprocess.run(
                    argv,
                    cwd=ROOT / "harness",
                    env=env,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    timeout=execution["timeout_seconds"],
                    check=False,
                )
            manifest["steps"].append({
                "name": name,
                "argv": argv,
                "cwd": str(ROOT / "harness"),
                "started_at": started,
                "returncode": process.returncode,
            })
            write_json(manifest_path, manifest)
            if process.returncode:
                raise RuntimeError(f"Target evaluation {name} failed; see {output / (name + '.log')}")
        summary = json.loads((output / "score/summary.json").read_text(encoding="utf-8-sig"))
        if summary.get("run_valid") is not True or summary.get("total") != population["expected_case_count"]:
            raise RuntimeError("Target evaluation did not produce the expected valid native result count")
        analysis = analyze("agent_execution", [output / "score/results.jsonl"], output / "analysis")
        manifest.update(
            status="completed",
            native_summary=summary,
            analysis=str(output / "analysis/summary.json"),
            result_file=str(output / "score/results.jsonl"),
            failure_observation_count=analysis["failure_observation_count"],
            completed_at=_now(),
        )
    except Exception as exc:
        manifest.update(status="failed", error=f"{type(exc).__name__}: {exc}")
        write_json(manifest_path, manifest)
        raise
    write_json(manifest_path, manifest)
    return manifest


def execute_target_evaluation(
    *,
    candidate_manifest: Path,
    job_path: Path,
    policy_path: Path,
    out: Path,
    allow_external_targets: bool = False,
) -> dict[str, Any]:
    """Run paired safety/regression jobs and feed their native outputs to comparison."""
    candidate_path = candidate_manifest.expanduser().resolve()
    policy_resolved = policy_path.expanduser().resolve()
    candidate_manifest_sha = _sha256_file(candidate_path)
    record = json.loads(candidate_path.read_text(encoding="utf-8-sig"))
    if not isinstance(record, dict) or record.get("stage") != "target_candidate":
        raise ValueError("candidate_manifest must describe a target_candidate")
    if record.get("track") != "agent_execution":
        raise ValueError("target-evaluate currently supports agent_execution candidates only")
    if record.get("validation", {}).get("status") != "unvalidated":
        raise ValueError("target-evaluate requires an unvalidated candidate")
    change = record.get("change") or {}
    baseline = _load_target(change.get("baseline_asset"), side="baseline", allow_external_targets=allow_external_targets)
    candidate = _load_target(change.get("asset"), side="candidate", allow_external_targets=allow_external_targets)
    job = _load_job(job_path)
    policy = _load_policy(policy_resolved)
    if job["safety"]["expected_case_count"] < policy["min_paired_cases"]:
        raise ValueError("Target evaluation safety population is smaller than policy min_paired_cases")
    policy_sha = _sha256_file(policy_resolved)
    output = out.expanduser().resolve()
    empty_output(output)
    manifest_path = output / "manifest.json"
    manifest: dict[str, Any] = {
        "schema_version": "1.0",
        "stage": "target_evaluation_job",
        "status": "started",
        "candidate_manifest": {"path": str(candidate_path), "sha256": candidate_manifest_sha},
        "job": {"path": str(job["path"]), "sha256": job["sha256"]},
        "policy": {"path": str(policy_resolved), "sha256": policy_sha},
        "targets": {"baseline": _public_target(baseline), "candidate": _public_target(candidate)},
        "external_execution_authorized": allow_external_targets,
        "deployment_performed": False,
        "created_at": _now(),
    }
    write_json(manifest_path, manifest)
    runs: dict[str, dict[str, Any]] = {}
    try:
        for side, target in (("baseline", baseline), ("candidate", candidate)):
            for population_name in ("safety", "regression"):
                key = f"{side}_{population_name}"
                runs[key] = _run_population(
                    target=target,
                    population=job[population_name],
                    output=output / side / population_name,
                    execution=job["execution"],
                )
        if _sha256_file(candidate_path) != candidate_manifest_sha:
            raise ValueError("Candidate manifest changed during execution")
        if _sha256_file(job["path"]) != job["sha256"]:
            raise ValueError("Target evaluation job changed during execution")
        if _sha256_file(policy_resolved) != policy_sha:
            raise ValueError("Target evolution policy changed during execution")
        for side, target in (("baseline", baseline), ("candidate", candidate)):
            current_target = _asset_identity(Path(target["asset"]["path"]))
            if any(
                current_target.get(key) != target["asset"].get(key)
                for key in ("kind", "sha256", "file_count")
            ):
                raise ValueError(f"{side.capitalize()} target asset changed during execution")
        for population_name in ("safety", "regression"):
            population = job[population_name]
            if _sha256_file(population["public_tasks"]) != population["public_sha256"]:
                raise ValueError(f"{population_name} public tasks changed during execution")
            if _sha256_file(population["private_rules"]) != population["private_sha256"]:
                raise ValueError(f"{population_name} private rules changed during execution")
        evaluation_manifest = output / "evaluation_manifest.json"
        write_json(evaluation_manifest, {
            "schema_version": "1.0",
            "role": job["role"],
            "sets": {
                name: {
                    "id": job[name]["id"],
                    "sha256": job[name]["combined_sha256"],
                    "expected_case_count": job[name]["expected_case_count"],
                }
                for name in ("safety", "regression")
            },
            "frozen_before_candidate": job["frozen_before_candidate"],
            "candidate_access_to_case_details": job["candidate_access_to_case_details"],
            "private_oracle_exposed": False,
        })
        decision = compare_target(
            candidate_manifest=candidate_path,
            baseline_results=[Path(runs["baseline_safety"]["result_file"])],
            candidate_results=[Path(runs["candidate_safety"]["result_file"])],
            baseline_regression_results=[Path(runs["baseline_regression"]["result_file"])],
            candidate_regression_results=[Path(runs["candidate_regression"]["result_file"])],
            evaluation_manifest=evaluation_manifest,
            policy_path=policy_resolved,
            out=output / "comparison",
        )
        manifest.update(
            status="completed",
            runs={key: value["result_file"] for key, value in runs.items()},
            evaluation_manifest=str(evaluation_manifest),
            comparison=str(output / "comparison/decision.json"),
            decision=decision["decision"],
            promotable=decision["promotable"],
            promotion_action_performed=False,
            completed_at=_now(),
        )
    except Exception as exc:
        manifest.update(status="failed", error=f"{type(exc).__name__}: {exc}")
        write_json(manifest_path, manifest)
        raise
    write_json(manifest_path, manifest)
    return manifest
