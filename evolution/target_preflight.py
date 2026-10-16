"""Read-only readiness checks for registered model/CLI Agent target candidates."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import sys
from typing import Any

from scripts.result_analysis import empty_output, write_json
from .target_executor import (
    SENSITIVE_ENV_NAME,
    _load_target,
    _materialize_target_configuration,
    _public_target,
    _sha256_file,
)
from .target_loop import _asset_identity


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PATH_ALLOWLIST = {"PATH"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _issue(code: str, message: str, *, side: str | None = None) -> dict[str, str]:
    result = {"code": code, "message": message}
    if side is not None:
        result["side"] = side
    return result


def _static_expand(value: str) -> str:
    return (
        value.replace("{python_executable}", sys.executable)
        .replace("{harness_ash}", str(ROOT / "harness/ash.py"))
    )


def _path_for_cli(config: dict[str, Any]) -> str | None:
    env = config.get("env") or {}
    if isinstance(env.get("PATH"), str):
        return _static_expand(env["PATH"])
    allowlist = config.get("env_allowlist")
    allowed = DEFAULT_PATH_ALLOWLIST if allowlist is None else set(allowlist)
    return os.environ.get("PATH") if "PATH" in allowed else None


def _resolve_executable(token: str, *, path_value: str | None) -> Path | None:
    expanded = _static_expand(token)
    if "{" in expanded or "}" in expanded:
        return None
    candidate = Path(expanded).expanduser()
    if candidate.is_absolute():
        return candidate.resolve() if candidate.is_file() else None
    if any(separator in expanded for separator in ("/", "\\")):
        return None
    if path_value is None:
        return None
    found = shutil.which(expanded, path=path_value)
    return Path(found).resolve() if found else None


def _file_identity(path: Path) -> dict[str, Any]:
    result: dict[str, Any] = {
        "path": str(path),
        "size_bytes": path.stat().st_size,
        "version": None,
        "version_probe": "not_executed",
    }
    try:
        result["sha256"] = _sha256_file(path)
    except OSError as exc:
        result["sha256"] = None
        result["hash_error"] = f"{type(exc).__name__}: {exc}"
    return result


def _check_executable(
    token: str,
    *,
    role: str,
    path_value: str | None,
    side: str,
) -> tuple[dict[str, Any], list[dict[str, str]]]:
    resolved = _resolve_executable(token, path_value=path_value)
    record: dict[str, Any] = {
        "role": role,
        "configured_name": token,
        "available": resolved is not None,
        "identity": _file_identity(resolved) if resolved is not None else None,
    }
    issues = [] if resolved is not None else [
        _issue(
            "executable_unavailable",
            f"{role} executable {token!r} is not available in the controlled child environment",
            side=side,
        )
    ]
    return record, issues


def _cmd_delegate(command: list[str]) -> str | None:
    if not command:
        return None
    launcher = Path(_static_expand(command[0])).name.casefold()
    if launcher not in {"cmd", "cmd.exe"}:
        return None
    for marker in ("/c", "/k"):
        try:
            index = next(i for i, item in enumerate(command[1:], 1) if item.casefold() == marker)
        except StopIteration:
            continue
        return command[index + 1] if index + 1 < len(command) else None
    return None


def _placeholder_present(values: list[str], placeholder: str) -> bool:
    return any(placeholder in value for value in values)


def _single_effective_entry(target: dict[str, Any]) -> dict[str, Any] | None:
    effective = target.get("_effective_config")
    if not isinstance(effective, dict):
        return None
    collection = effective.get("models") or effective.get("agents")
    if not isinstance(collection, dict) or len(collection) != 1:
        return None
    entry = next(iter(collection.values()))
    return deepcopy(entry) if isinstance(entry, dict) else None


def _check_model_target(target: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, str]], list[dict[str, str]]]:
    side = target["side"]
    config = _single_effective_entry(target) or {}
    name = str(config.get("api_key_env") or "")
    present = bool(name and os.environ.get(name))
    issues = [] if present else [
        _issue(
            "credential_environment_missing",
            f"Required model credential environment variable {name!r} is missing or empty",
            side=side,
        )
    ]
    checks = {
        "backend": "model",
        "credential_environment": {"name": name, "present": present, "value_exposed": False},
        "endpoint_declared": bool(config.get("base_url")),
        "provider_model_declared": bool(config.get("model")),
        "system_prompt_bound": bool(config.get("system_prompt")),
    }
    return checks, issues, []


def _check_cli_target(target: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, str]], list[dict[str, str]]]:
    side = target["side"]
    config = _single_effective_entry(target) or {}
    command = list(config.get("command") or [])
    server_command = list(config.get("benchmark_mcp_server_command") or [])
    env = dict(config.get("env") or {})
    env_values = [str(value) for value in env.values()]
    path_value = _path_for_cli(config)
    issues: list[dict[str, str]] = []
    warnings: list[dict[str, str]] = []
    executables: list[dict[str, Any]] = []

    if command:
        launcher, found = _check_executable(command[0], role="cli_launcher", path_value=path_value, side=side)
        executables.append(launcher)
        issues.extend(found)
        delegate = _cmd_delegate(command)
        if delegate is not None:
            delegated, found = _check_executable(delegate, role="cli_target", path_value=path_value, side=side)
            executables.append(delegated)
            issues.extend(found)
    else:
        issues.append(_issue("cli_command_missing", "CLI command is empty", side=side))

    if server_command:
        server, found = _check_executable(
            server_command[0], role="benchmark_mcp_server", path_value=path_value, side=side
        )
        executables.append(server)
        issues.extend(found)
        if any("{harness_ash}" in item for item in server_command):
            harness_path = Path(_static_expand("{harness_ash}"))
            available = harness_path.is_file()
            executables.append({
                "role": "benchmark_harness_entrypoint",
                "configured_name": "{harness_ash}",
                "available": available,
                "identity": _file_identity(harness_path) if available else None,
            })
            if not available:
                issues.append(_issue(
                    "harness_entrypoint_unavailable",
                    "The {harness_ash} MCP entry point is not available",
                    side=side,
                ))
    else:
        issues.append(_issue(
            "benchmark_mcp_server_missing",
            "CLI target does not declare benchmark_mcp_server_command",
            side=side,
        ))

    command_and_env = [*command, *env_values]
    mcp_config_consumed = _placeholder_present(command_and_env, "{mcp_config}")
    task_bound = _placeholder_present(server_command, "{task_json}")
    events_bound = _placeholder_present(server_command, "{mcp_events}")
    if not mcp_config_consumed:
        issues.append(_issue(
            "mcp_config_not_consumed",
            "CLI command/environment does not consume the generated {mcp_config}",
            side=side,
        ))
    if server_command and (not task_bound or not events_bound):
        issues.append(_issue(
            "mcp_server_runtime_paths_missing",
            "benchmark_mcp_server_command must bind both {task_json} and {mcp_events}",
            side=side,
        ))

    prompt_file_consumed = _placeholder_present(command_and_env, "{prompt_file}")
    prompt_stdin = config.get("pass_prompt_stdin", True) is True
    prompt_bound = bool(config.get("system_prompt"))
    if prompt_bound and not (prompt_stdin or prompt_file_consumed):
        issues.append(_issue(
            "system_prompt_transport_missing",
            "A system prompt is bound but neither stdin nor {prompt_file} delivers it to the CLI",
            side=side,
        ))
    hardcoded_prompt_flags = sorted({
        item for item in command
        if item.casefold() in {"--append-system-prompt", "--system-prompt", "--system-message"}
    })
    if hardcoded_prompt_flags:
        warnings.append(_issue(
            "competing_command_prompt",
            "CLI command also declares a system-prompt flag; verify precedence against the target prompt asset",
            side=side,
        ))

    allowlist = list(config.get("env_allowlist") or [])
    credential_names = sorted(name for name in allowlist if SENSITIVE_ENV_NAME.search(name))
    credential_checks = [
        {"name": name, "present": bool(os.environ.get(name)), "value_exposed": False}
        for name in credential_names
    ]
    for item in credential_checks:
        if not item["present"]:
            issues.append(_issue(
                "credential_environment_missing",
                f"Allowlisted credential environment variable {item['name']!r} is missing or empty",
                side=side,
            ))

    checks = {
        "backend": "cli",
        "inherit_env": config.get("inherit_env", False),
        "executables": executables,
        "credential_environment": credential_checks,
        "system_prompt_bound": prompt_bound,
        "prompt_transport": {
            "stdin": prompt_stdin,
            "prompt_file_consumed": prompt_file_consumed,
            "command_interpolation": False,
        },
        "mcp_transport": {
            "server_declared": bool(server_command),
            "config_consumed": mcp_config_consumed,
            "task_json_bound": task_bound,
            "events_log_bound": events_bound,
        },
        "version_probe": "not_executed; executable SHA-256 is recorded instead",
    }
    return checks, issues, warnings


def _pair_checks(
    baseline: dict[str, Any],
    candidate: dict[str, Any],
    *,
    change_type: str,
) -> tuple[list[dict[str, Any]], list[dict[str, str]], list[dict[str, str]]]:
    issues: list[dict[str, str]] = []
    warnings: list[dict[str, str]] = []
    same_agent = baseline["agent_id"] == candidate["agent_id"]
    local_stub_switch = (
        change_type == "policy"
        and baseline["agent_id"] in {"safe_stub", "unsafe_stub"}
        and candidate["agent_id"] in {"safe_stub", "unsafe_stub"}
    )
    same_agent_required = change_type in {"prompt", "policy"} and not local_stub_switch
    checks: list[dict[str, Any]] = [{
        "name": "same_agent_id",
        "required": same_agent_required,
        "passed": same_agent or not same_agent_required,
        "baseline": baseline["agent_id"],
        "candidate": candidate["agent_id"],
    }]
    baseline_entry = _single_effective_entry(baseline)
    candidate_entry = _single_effective_entry(candidate)

    if change_type == "prompt":
        if not same_agent:
            issues.append(_issue("prompt_change_agent_drift", "Prompt candidates must keep agent_id unchanged"))
        for entry in (baseline_entry, candidate_entry):
            if entry is not None:
                entry.pop("system_prompt", None)
        runtime_equal = baseline_entry == candidate_entry
        checks.append({"name": "prompt_only_runtime_parity", "passed": runtime_equal})
        if not runtime_equal:
            issues.append(_issue(
                "non_prompt_runtime_drift",
                "Prompt candidate changes runtime configuration outside system_prompt",
            ))
    elif change_type == "policy":
        if not same_agent and not local_stub_switch:
            issues.append(_issue("policy_change_agent_drift", "Policy candidates must keep external agent_id unchanged"))
        if local_stub_switch and not same_agent:
            warnings.append(_issue(
                "synthetic_stub_switch",
                "Local stub agent_id switch is accepted only as a synthetic control-plane fixture",
            ))
        for entry in (baseline_entry, candidate_entry):
            if entry is not None:
                for key in ("temperature", "max_tokens", "timeout_seconds"):
                    entry.pop(key, None)
        runtime_equal = baseline_entry == candidate_entry
        checks.append({"name": "policy_only_runtime_parity", "passed": runtime_equal})
        if not runtime_equal:
            issues.append(_issue(
                "non_policy_runtime_drift",
                "Policy candidate changes runtime configuration outside bounded policy fields",
            ))
    else:
        warnings.append(_issue(
            "change_type_not_semantically_limited",
            f"Preflight does not enforce field-level parity for change_type={change_type!r}",
        ))
    return checks, issues, warnings


def preflight_target_candidate(*, candidate_manifest: Path, out: Path) -> dict[str, Any]:
    """Validate and materialize target assets without invoking a model or CLI process."""
    output = out.expanduser().resolve()
    empty_output(output)
    manifest_path = candidate_manifest.expanduser().resolve()
    manifest_sha = _sha256_file(manifest_path)
    record = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
    blocking: list[dict[str, str]] = []
    warnings: list[dict[str, str]] = []
    targets: dict[str, dict[str, Any]] = {}
    loaded_targets: dict[str, dict[str, Any]] = {}

    if not isinstance(record, dict) or record.get("stage") != "target_candidate":
        blocking.append(_issue("candidate_manifest_invalid", "candidate_manifest must describe a target_candidate"))
    if isinstance(record, dict) and record.get("track") != "agent_execution":
        blocking.append(_issue("track_unsupported", "target-preflight currently supports agent_execution only"))
    if isinstance(record, dict) and record.get("validation", {}).get("status") != "unvalidated":
        blocking.append(_issue("candidate_state_invalid", "target-preflight requires an unvalidated candidate"))

    change = record.get("change") if isinstance(record, dict) else None
    if not isinstance(change, dict):
        blocking.append(_issue("candidate_change_missing", "Candidate manifest change object is missing"))
        change = {}
    change_type = str(change.get("type") or "")
    for side, identity in (("baseline", change.get("baseline_asset")), ("candidate", change.get("asset"))):
        try:
            target = _load_target(
                identity,
                side=side,
                allow_external_targets=False,
                require_external_authorization=False,
            )
            loaded_targets[side] = target
            configuration_args, runtime_config = _materialize_target_configuration(target, output / side)
            if target["agent_id"].startswith(("model:", "reference:")):
                checks, issues, target_warnings = _check_model_target(target)
            elif target["agent_id"].startswith("cli:"):
                checks, issues, target_warnings = _check_cli_target(target)
            else:
                checks, issues, target_warnings = ({"backend": "local_stub"}, [], [])
            blocking.extend(issues)
            warnings.extend(target_warnings)
            targets[side] = {
                "target": _public_target(target),
                "runtime_config": runtime_config,
                "capture_configuration_options": configuration_args[::2] if configuration_args else [],
                "checks": checks,
                "ready": not issues,
                "blocking_issues": issues,
                "warnings": target_warnings,
            }
        except (OSError, ValueError, TypeError) as exc:
            issue = _issue(
                "target_asset_invalid",
                f"{type(exc).__name__}: {exc}",
                side=side,
            )
            blocking.append(issue)
            targets[side] = {"ready": False, "blocking_issues": [issue]}

    pair_check_records: list[dict[str, Any]] = []
    if set(loaded_targets) == {"baseline", "candidate"}:
        pair_check_records, issues, pair_warnings = _pair_checks(
            loaded_targets["baseline"], loaded_targets["candidate"], change_type=change_type
        )
        blocking.extend(issues)
        warnings.extend(pair_warnings)

    if _sha256_file(manifest_path) != manifest_sha:
        blocking.append(_issue("candidate_manifest_drift", "Candidate manifest changed during preflight"))
    for side, target in loaded_targets.items():
        current = _asset_identity(Path(target["asset"]["path"]))
        if any(current.get(key) != target["asset"].get(key) for key in ("kind", "sha256", "file_count")):
            blocking.append(_issue("target_asset_drift", "Target asset changed during preflight", side=side))

    ready = not blocking
    report = {
        "schema_version": "1.0",
        "stage": "target_preflight",
        "status": "ready" if ready else "blocked",
        "ready": ready,
        "candidate_manifest": {"path": str(manifest_path), "sha256": manifest_sha},
        "change_type": change_type or None,
        "targets": targets,
        "pair_checks": pair_check_records,
        "blocking_issues": blocking,
        "warnings": warnings,
        "side_effects": {
            "model_api_called": False,
            "cli_process_started": False,
            "evaluation_executed": False,
            "deployment_performed": False,
            "credential_values_exposed": False,
        },
        "created_at": _now(),
    }
    write_json(output / "readiness.json", report)
    write_json(output / "manifest.json", {
        "schema_version": "1.0",
        "stage": "target_preflight_manifest",
        "status": "completed",
        "ready": ready,
        "readiness": str(output / "readiness.json"),
        "blocking_issue_count": len(blocking),
        "warning_count": len(warnings),
        "execution_performed": False,
        "created_at": report["created_at"],
    })
    return report
