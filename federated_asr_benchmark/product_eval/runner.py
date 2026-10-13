from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from collections import Counter
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from typing import Any
import re
import stat
import tomllib

from product_eval.product_model_policy import QODER_EXTERNAL_MODEL_KEY, validate_runtime_model
from product_eval.route_audit_proxy import DeepSeekRouteAuditProxy


BASE_ENV_ALLOWLIST = (
    "COMSPEC",
    "PATH",
    "PATHEXT",
    "SYSTEMDRIVE",
    "SYSTEMROOT",
    "TEMP",
    "TMP",
    "WINDIR",
)

PRODUCT_ENV_ALLOWLIST = {
    "codex": ("OPENAI_API_KEY", "DEEPSEEK_API_KEY"),
    "claude_code": ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"),
    "qoder": ("DEEPSEEK_API_KEY",),
    "codebuddy": ("DEEPSEEK_API_KEY",),
}

QODER_DEEPSEEK_PROVIDER_MODEL = "deepseek-v4-flash"
QODER_DEEPSEEK_BASE_URL = "https://api.deepseek.com"
CODEBUDDY_DEEPSEEK_MODEL = "deepseek-v4-flash"
CODEBUDDY_DEEPSEEK_API_KEY_REFERENCE = "${DEEPSEEK_API_KEY}"
CODEBUDDY_DISABLED_BUILTIN_TOOLS = (
    "Agent",
    "Read",
    "Write",
    "Edit",
    "Bash",
    "PowerShell",
    "Glob",
    "Grep",
    "EnterPlanMode",
    "ExitPlanMode",
    "TaskCreate",
    "TaskGet",
    "TaskUpdate",
    "TaskList",
    "WebFetch",
    "WebSearch",
    "TaskStop",
    "TaskOutput",
    "Skill",
    "SkillManage",
    "AskUserQuestion",
    "StructuredOutput",
    "ToolSearch",
    "DeferExecuteTool",
    "SendMessage",
    "TeamCreate",
    "TeamDelete",
    "NotebookEdit",
    "LSP",
    "ImageGen",
    "ImageEdit",
    "VideoGen",
    "EnterWorktree",
    "LeaveWorktree",
    "CronCreate",
    "CronDelete",
    "CronList",
    "WeChatReply",
    "WeComReply",
    "PushNotification",
    "ReportFindings",
    "ComputerUse",
    "ListMcpResources",
    "ReadMcpResource",
    "WaitForMcpServers",
    "Workflow",
    "Monitor",
)

STATE_IGNORES = (
    "AGENTS.md",
    "CLAUDE.md",
    "agents",
    "cache",
    "canvas",
    "commands",
    "history*",
    "hooks",
    "logs",
    "logs*.sqlite*",
    "*.sqlite-shm",
    "*.sqlite-wal",
    "memory",
    "memories",
    "extensions",
    "external-commands",
    "plugins",
    "projects",
    "rules",
    "sessions",
    "security",
    "session-env",
    "shell-snapshots",
    "skills",
    "tasks",
    "telemetry",
    "tmp",
)


@dataclass(frozen=True)
class ProductIdentity:
    product_id: str
    executable: str
    version: str
    product_model: str | None = None
    state_source: str | None = None


@dataclass
class ProductRunResult:
    schema_version: str
    run_id: str
    benchmark_id: str
    case_id: str
    product: dict[str, Any]
    status: str
    command: list[str]
    started_at: str
    duration_seconds: float
    returncode: int | None
    timed_out: bool
    final_response: str
    stdout_file: str
    stderr_file: str
    final_response_file: str
    state_source: str | None
    isolated_state_dir: str
    isolated_state_removed: bool
    error: str | None


class ProductRunner:
    def __init__(self, identity: ProductIdentity, timeout_seconds: int = 300):
        if identity.product_id not in {"codex", "claude_code", "qoder", "codebuddy"}:
            raise ValueError(f"Unsupported product: {identity.product_id}")
        if timeout_seconds < 1:
            raise ValueError("timeout_seconds must be positive")
        self.identity = identity
        self.timeout_seconds = timeout_seconds

    def verify_version(self, env: dict[str, str] | None = None) -> str:
        command = self._shell_command([self.identity.executable, "--version"])
        completed = subprocess.run(
            command,
            env=env or self._child_env(),
            text=True,
            encoding="utf-8",
            errors="replace",
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=30,
            check=False,
        )
        output = completed.stdout.strip()
        if completed.returncode != 0:
            raise RuntimeError(f"Product version check failed ({completed.returncode}): {output}")
        if self.identity.version not in output:
            message = (
                f"Configured {self.identity.product_id} version {self.identity.version!r} "
                f"does not match CLI output {output!r}"
            )
            if os.environ.get("FEDERATED_ASR_STRICT_CHECKS") == "1":
                raise RuntimeError(message)
            print(f"WARNING: {message}; continuing with local CLI adaptation.", file=sys.stderr)
        return output

    def build_command(
        self,
        *,
        workspace: Path,
        mcp_config: Path,
        mcp_server_command: list[str] | dict[str, list[str]],
        allowed_tools: list[str],
        final_response_file: Path,
        isolated_state_dir: Path,
    ) -> list[str]:
        product_id = self.identity.product_id
        mcp_servers = _normalize_mcp_servers(mcp_server_command, "saber")
        if product_id == "codex":
            # Codex's non-interactive `never` policy cancels MCP approval requests. The benchmark
            # process is externally scoped to MCP-only tools, so bypass UI approval and remove the
            # native execution tools that would otherwise make that bypass too broad.
            args = [
                self.identity.executable,
            ]
            if self.identity.product_model == "deepseek-v4-flash":
                args.extend(
                    [
                        "-c",
                        'model_provider="deepseek"',
                        "-c",
                        'model_providers.deepseek.name="DeepSeek"',
                        "-c",
                        'model_providers.deepseek.base_url="https://api.deepseek.com/v1"',
                        "-c",
                        'model_providers.deepseek.env_key="DEEPSEEK_API_KEY"',
                        "-c",
                        'model_providers.deepseek.wire_api="responses"',
                    ]
                )
            if self.identity.product_model:
                args.extend(["-m", self.identity.product_model])
            for server_name, command in mcp_servers.items():
                args.extend(
                    [
                        "-c",
                        f"mcp_servers.{server_name}.command="
                        f"{json.dumps(command[0], ensure_ascii=False)}",
                        "-c",
                        f"mcp_servers.{server_name}.args="
                        f"{json.dumps(command[1:], ensure_ascii=False)}",
                        "-c",
                        f"mcp_servers.{server_name}.startup_timeout_sec=60",
                    ]
                )
            args.extend(
                [
                    "--disable",
                    "shell_tool",
                    "--disable",
                    "unified_exec",
                    "--disable",
                    "plugins",
                    "--dangerously-bypass-approvals-and-sandbox",
                    "exec",
                    "--skip-git-repo-check",
                    "--ephemeral",
                    "--ignore-user-config",
                    "--ignore-rules",
                    "--json",
                    "--color",
                    "never",
                    "-C",
                    str(workspace),
                ]
            )
            args.extend(["-o", str(final_response_file)])
            args.append("-")
            return self._shell_command(args)

        if product_id == "codebuddy":
            args = [
                self.identity.executable,
                "--print",
                "--output-format",
                "stream-json",
                "--no-session-persistence",
                "--mcp-config",
                str(mcp_config),
                "--strict-mcp-config",
                "--permission-mode",
                "bypassPermissions",
                "--max-turns",
                "100",
                "-y",
            ]
            # CodeBuddy automatically defers larger MCP tool sets. The deferred
            # tools are then omitted from the provider request unless ToolSearch
            # is enabled, which is not an allowed benchmark capability. Force the
            # explicitly selected benchmark tools into the initial request.
            rendered_tools = ",".join(f"NoDefer({name})" for name in allowed_tools)
            permission_tools = ",".join(allowed_tools)
            # CodeBuddy uses --tools to decide which schemas reach the model, while
            # --allowedTools controls execution permission for those schemas.
            args.append(f"--tools={rendered_tools}")
            if allowed_tools:
                args.extend(["--allowedTools", permission_tools])
            args.extend(["--disallowedTools", *CODEBUDDY_DISABLED_BUILTIN_TOOLS])
            if self.identity.product_model:
                args.extend(["--model", self.identity.product_model])
            return self._shell_command(args)

        common = [
            "--print",
            "--output-format",
            "stream-json",
            "--no-session-persistence",
        ]
        if allowed_tools:
            common.extend(["--tools", ",".join(allowed_tools)])
            common.extend(["--allowed-tools", ",".join(allowed_tools)])

        if product_id == "claude_code":
            args = [
                self.identity.executable,
                *common,
                "--verbose",
                "--disable-slash-commands",
                "--dangerously-skip-permissions",
                "--settings",
                '{"enableAllProjectMcpServers":true}',
            ]
        else:
            args = [
                self.identity.executable,
                "--config-dir",
                str(isolated_state_dir),
                *common,
                "--mcp-config",
                str(mcp_config),
                "--strict-mcp-config",
                "--allowed-mcp-server-names",
                ",".join(mcp_servers),
                "--permission-mode",
                "bypass_permissions",
                "-w",
                str(workspace),
            ]
        if self.identity.product_model:
            args.extend(["--model", self.identity.product_model])
        return self._shell_command(args)

    def run(
        self,
        *,
        run_id: str,
        benchmark_id: str,
        case_id: str,
        prompt: str,
        output_dir: Path,
        mcp_server_command: list[str] | dict[str, list[str]],
        allowed_tool_names: list[str],
        dry_run: bool = False,
    ) -> ProductRunResult:
        output_dir.mkdir(parents=True, exist_ok=True)
        workspace = output_dir / "workspace"
        workspace.mkdir(parents=True, exist_ok=True)
        isolated_state = (
            output_dir / "dry_run_state_placeholder"
            if dry_run
            else Path(tempfile.mkdtemp(prefix=f"federated_asr_{self.identity.product_id}_"))
        )
        isolated_state.mkdir(parents=True, exist_ok=True)
        (output_dir / "prompt.txt").write_text(prompt, encoding="utf-8")
        mcp_config = output_dir / "mcp.json"
        mcp_servers = _normalize_mcp_servers(mcp_server_command, benchmark_id)
        mcp_config.write_text(
            json.dumps(
                {
                    "mcpServers": {
                        name: {"command": command[0], "args": command[1:]}
                        for name, command in mcp_servers.items()
                    }
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        final_response_file = output_dir / "final_response.txt"
        if len(mcp_servers) == 1:
            only_server = next(iter(mcp_servers))
            allowed_tools = [
                name if name.startswith("mcp__") else f"mcp__{only_server}__{name}"
                for name in allowed_tool_names
            ]
        else:
            allowed_tools = list(allowed_tool_names)
            invalid = [name for name in allowed_tools if not name.startswith("mcp__")]
            if invalid:
                raise ValueError(
                    "Multi-server runs require fully qualified mcp__<server>__<tool> names: "
                    f"{invalid}"
                )
        command = self.build_command(
            workspace=workspace,
            mcp_config=mcp_config,
            mcp_server_command=mcp_servers,
            allowed_tools=allowed_tools,
            final_response_file=final_response_file,
            isolated_state_dir=isolated_state,
        )
        stdout_file = output_dir / "product_stdout.jsonl"
        stderr_file = output_dir / "product_stderr.txt"
        started = datetime.now(timezone.utc)
        started_monotonic = time.monotonic()
        effective_product_model = self._effective_product_model()
        returncode: int | None = None
        timed_out = False
        stdout = ""
        stderr = ""
        execution_error = None
        isolated_state_removed = False

        route_proxy: DeepSeekRouteAuditProxy | None = None
        detected_model: str | None = None
        if dry_run:
            status = "dry_run"
            isolated_state_removed = _remove_directory(isolated_state)
        else:
            try:
                if self.identity.product_id == "codebuddy":
                    route_proxy = DeepSeekRouteAuditProxy(self.timeout_seconds)
                    route_proxy.__enter__()
                self._prepare_isolated_state(
                    isolated_state,
                    codebuddy_base_url=(route_proxy.base_url if route_proxy else None),
                )
                if self.identity.product_id == "claude_code":
                    _configure_claude_mcp_state(isolated_state, mcp_servers)
                env = self._child_env(
                    isolated_state,
                    codebuddy_base_url=(route_proxy.base_url if route_proxy else None),
                )
                self.verify_version(env)
                returncode, stdout, stderr, timed_out = _run_process(
                    command,
                    cwd=workspace,
                    env=env,
                    stdin_text=prompt,
                    timeout_seconds=self.timeout_seconds,
                )
                detected_model = _model_from_stream(stdout)
                status = "completed" if returncode == 0 and not timed_out else "failed"
                if self.identity.product_id == "codebuddy":
                    codebuddy_error = _codebuddy_result_error(stdout)
                    if codebuddy_error:
                        status = "failed"
                        execution_error = (
                            f"CodeBuddy stream reported {codebuddy_error}"
                        )
                        stderr = f"{stderr}\n{execution_error}".strip()
                if self.identity.product_model:
                    try:
                        effective_product_model = validate_runtime_model(
                            self.identity.product_id,
                            self.identity.product_model,
                            detected_model,
                        )
                    except RuntimeError as exc:
                        status = "failed"
                        execution_error = f"product model policy violation: {exc}"
                        stderr = f"{stderr}\n{execution_error}".strip()
                elif detected_model:
                    effective_product_model = detected_model
            except Exception as exc:
                status = "failed"
                execution_error = f"{type(exc).__name__}: {exc}"
                stderr = execution_error
            finally:
                if route_proxy is not None:
                    route_proxy.__exit__(None, None, None)
                    route = route_proxy.evidence(
                        model=CODEBUDDY_DEEPSEEK_MODEL,
                        api_key_supplied=bool(os.environ.get("DEEPSEEK_API_KEY")),
                    )
                    route["effective_model"] = detected_model
                    route["env_byok_configured"] = True
                    route_error = _validate_codebuddy_route(
                        route,
                        expected_tool_count=len(allowed_tools),
                    )
                    if route_error:
                        status = "failed"
                        execution_error = f"CodeBuddy route policy violation: {route_error}"
                        stderr = f"{stderr}\n{execution_error}".strip()
                    (output_dir / "product_network_route.json").write_text(
                        json.dumps(route, ensure_ascii=False, indent=2) + "\n",
                        encoding="utf-8",
                    )
                if self.identity.product_id == "qoder" and isolated_state.is_dir():
                    self._write_qoder_route_evidence(
                        isolated_state, output_dir / "product_network_route.json"
                    )
                isolated_state_removed = _remove_directory(isolated_state)

        if not isolated_state_removed:
            cleanup_message = f"isolated product state was not removed: {isolated_state}"
            status = "failed"
            execution_error = (
                f"{execution_error}; {cleanup_message}" if execution_error else cleanup_message
            )
            stderr = f"{stderr}\n{cleanup_message}".strip()

        stdout_file.write_text(stdout, encoding="utf-8")
        stderr_file.write_text(stderr, encoding="utf-8")
        final_response = _read_final_response(final_response_file, stdout)
        if not final_response_file.exists():
            final_response_file.write_text(final_response, encoding="utf-8")
        result = ProductRunResult(
            schema_version="0.1",
            run_id=run_id,
            benchmark_id=benchmark_id,
            case_id=case_id,
            product={
                "product_id": self.identity.product_id,
                "version": self.identity.version,
                "product_model": effective_product_model,
            },
            status=status,
            command=command,
            started_at=started.isoformat(),
            duration_seconds=time.monotonic() - started_monotonic,
            returncode=returncode,
            timed_out=timed_out,
            final_response=final_response,
            stdout_file=str(stdout_file),
            stderr_file=str(stderr_file),
            final_response_file=str(final_response_file),
            state_source=self.identity.state_source,
            isolated_state_dir=str(isolated_state),
            isolated_state_removed=isolated_state_removed,
            error=execution_error,
        )
        (output_dir / "product_run.json").write_text(
            json.dumps(asdict(result), indent=2) + "\n",
            encoding="utf-8",
        )
        return result

    def _prepare_isolated_state(
        self,
        destination: Path,
        *,
        codebuddy_base_url: str | None = None,
    ) -> None:
        source = Path(self.identity.state_source).expanduser() if self.identity.state_source else None
        if self.identity.product_id == "codex":
            destination.mkdir(parents=True, exist_ok=True)
            if source and source.is_dir():
                for name in ("auth.json", "config.toml"):
                    path = source / name
                    if path.is_file():
                        shutil.copy2(path, destination / name)
            _sanitize_codex_config(destination / "config.toml")
            return
        if self.identity.product_id == "qoder":
            destination.mkdir(parents=True, exist_ok=True)
            if source and source.is_dir():
                auth_source = source / ".auth"
                if auth_source.is_dir():
                    shutil.copytree(auth_source, destination / ".auth", dirs_exist_ok=True)
                for name in ("installation_id", "settings.json"):
                    path = source / name
                    if path.is_file():
                        shutil.copy2(path, destination / name)
            self._configure_qoder_byok_state(destination)
            return
        if self.identity.product_id == "codebuddy":
            self._configure_codebuddy_byok_state(
                destination,
                codebuddy_base_url=codebuddy_base_url,
            )
            return
        if source and source.is_dir():
            shutil.copytree(
                source,
                destination,
                dirs_exist_ok=True,
                symlinks=True,
                ignore=shutil.ignore_patterns(*STATE_IGNORES),
            )
        else:
            destination.mkdir(parents=True, exist_ok=True)

    def _configure_codebuddy_byok_state(
        self,
        destination: Path,
        *,
        codebuddy_base_url: str | None,
    ) -> None:
        if self.identity.product_model != CODEBUDDY_DEEPSEEK_MODEL:
            raise RuntimeError(
                f"CodeBuddy must use the pinned model {CODEBUDDY_DEEPSEEK_MODEL!r}"
            )
        if not os.environ.get("DEEPSEEK_API_KEY"):
            raise RuntimeError("DEEPSEEK_API_KEY is required for CodeBuddy BYOK")
        if not codebuddy_base_url:
            raise RuntimeError("CodeBuddy requires the audited DeepSeek base URL")
        destination.mkdir(parents=True, exist_ok=True)
        model_url = f"{codebuddy_base_url.rstrip('/')}/chat/completions"
        models = {
            "models": [
                {
                    "id": CODEBUDDY_DEEPSEEK_MODEL,
                    "name": "DeepSeek V4 Flash",
                    "vendor": "DeepSeek",
                    "url": model_url,
                    "apiKey": CODEBUDDY_DEEPSEEK_API_KEY_REFERENCE,
                    "maxInputTokens": 128_000,
                    "maxOutputTokens": 8_192,
                    "supportsToolCall": True,
                    "supportsImages": False,
                }
            ],
            "availableModels": [CODEBUDDY_DEEPSEEK_MODEL],
        }
        (destination / "models.json").write_text(
            json.dumps(models, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def _configure_qoder_byok_state(self, destination: Path) -> None:
        model_key = self.identity.product_model
        api_key = os.environ.get("DEEPSEEK_API_KEY")
        if model_key != QODER_EXTERNAL_MODEL_KEY:
            raise RuntimeError(
                f"Qoder must use the pinned external model {QODER_EXTERNAL_MODEL_KEY!r}"
            )
        if not api_key:
            raise RuntimeError("DEEPSEEK_API_KEY is required for Qoder BYOK")
        settings_path = destination / "settings.json"
        if settings_path.is_file():
            settings = json.loads(settings_path.read_text(encoding="utf-8"))
            if not isinstance(settings, dict):
                raise RuntimeError("Qoder settings.json must contain an object")
        else:
            settings = {}
        model_configs = settings.setdefault("modelConfigs", {})
        if not isinstance(model_configs, dict):
            raise RuntimeError("Qoder modelConfigs must contain an object")
        model_configs["customModels"] = []
        settings["providers"] = {
            "deepseek": {
                "type": "openai-compatible",
                "displayName": "DeepSeek",
                "baseUrl": QODER_DEEPSEEK_BASE_URL,
                "apiKey": api_key,
                "model": QODER_DEEPSEEK_PROVIDER_MODEL,
                "models": [
                    {
                        "model": QODER_DEEPSEEK_PROVIDER_MODEL,
                        "displayName": "DeepSeek-V4-Flash",
                        "contextWindow": 1_000_000,
                        "maxOutputTokens": 32_000,
                        "capabilities": {
                            "tools": True,
                            "vision": False,
                            "thinking": True,
                        },
                    }
                ],
            }
        }
        settings_path.write_text(
            json.dumps(settings, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    @staticmethod
    def _write_qoder_route_evidence(isolated_state: Path, output_path: Path) -> None:
        domain_counts: Counter[str] = Counter()
        deepseek_request_lines = 0
        qoder_inference_request_lines = 0
        observed_requests: list[dict[str, str]] = []
        diagnostics: list[str] = []
        secret = os.environ.get("DEEPSEEK_API_KEY", "")
        for log_path in isolated_state.glob("logs/**/*.log"):
            text = log_path.read_text(encoding="utf-8", errors="replace")
            for line in text.splitlines():
                lowered = line.casefold()
                if any(
                    marker in lowered
                    for marker in (
                        "[external-providers]",
                        "[externalprovider",
                        "[qoder-runtime-ready]",
                        "settings-byok",
                        "provider",
                        "settings",
                    )
                ):
                    sanitized = line.replace(secret, "[REDACTED]") if secret else line
                    sanitized = re.sub(
                        r"\buid=[A-Fa-f0-9-]+", "uid=[REDACTED]", sanitized
                    )
                    diagnostics.append(sanitized[:2_000])
                domains = re.findall(r"https?://([A-Za-z0-9.-]+)", line)
                domain_counts.update(domain.casefold() for domain in domains)
                if "api.deepseek.com" in line.casefold() and "-->" in line:
                    deepseek_request_lines += 1
                if (
                    "api3.qoder.sh" in line.casefold()
                    and "agent_chat_generation" in line.casefold()
                    and "-->" in line
                ):
                    qoder_inference_request_lines += 1
                request = re.search(
                    r"operation=(\S+).*?method=(\S+).*?url=https?://([^/\s]+)([^\s]*)",
                    line,
                )
                if request:
                    observed_requests.append(
                        {
                            "operation": request.group(1),
                            "method": request.group(2),
                            "host": request.group(3).casefold(),
                            "path": request.group(4).split("?", 1)[0],
                        }
                    )
        evidence = {
            "provider": "deepseek",
            "provider_model": QODER_DEEPSEEK_PROVIDER_MODEL,
            "configured_base_url": QODER_DEEPSEEK_BASE_URL,
            "observed_domain_counts": dict(sorted(domain_counts.items())),
            "deepseek_official_request_lines": deepseek_request_lines,
            "qoder_inference_request_lines": qoder_inference_request_lines,
            "observed_requests": observed_requests,
            "diagnostics": diagnostics[-100:],
        }
        output_path.write_text(
            json.dumps(evidence, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def _effective_product_model(self) -> str | None:
        if self.identity.product_model:
            return self.identity.product_model
        if self.identity.product_id != "codex" or not self.identity.state_source:
            return None
        config_path = Path(self.identity.state_source).expanduser() / "config.toml"
        if not config_path.is_file():
            return None
        try:
            model = tomllib.loads(config_path.read_text(encoding="utf-8")).get("model")
        except (OSError, tomllib.TOMLDecodeError):
            return None
        return model if isinstance(model, str) and model else None

    def _child_env(
        self,
        isolated_state: Path | None = None,
        *,
        codebuddy_base_url: str | None = None,
    ) -> dict[str, str]:
        names = (*BASE_ENV_ALLOWLIST, *PRODUCT_ENV_ALLOWLIST[self.identity.product_id])
        env = {name: os.environ[name] for name in names if name in os.environ}
        env["PYTHONIOENCODING"] = "utf-8"
        env["PYTHONUTF8"] = "1"
        env["USERNAME"] = "benchmark"
        env["USER"] = "benchmark"
        env["LOGNAME"] = "benchmark"
        env["HF_HOME"] = str(Path.home() / ".cache" / "huggingface")
        env["HF_HUB_OFFLINE"] = "1"
        env["TRANSFORMERS_OFFLINE"] = "1"
        if isolated_state is not None:
            # MCP runtimes inherit this environment. Give dependencies a valid
            # home without exposing or mutating the runner user's real home.
            env["HOME"] = str(isolated_state)
            env["USERPROFILE"] = str(isolated_state)
            if self.identity.product_id == "codex":
                env["CODEX_HOME"] = str(isolated_state)
            elif self.identity.product_id == "claude_code":
                env["CLAUDE_CONFIG_DIR"] = str(isolated_state)
                env["MCP_TIMEOUT"] = "60000"
                env["MCP_TOOL_TIMEOUT"] = str(self.timeout_seconds * 1000)
            elif self.identity.product_id == "qoder":
                env["QODER_CONFIG_DIR"] = str(isolated_state)
                env["QODERSEC_HOME"] = str((Path.home() / ".qodersec").resolve())
            else:
                if not codebuddy_base_url:
                    raise RuntimeError("CodeBuddy requires the audited DeepSeek base URL")
                env["CODEBUDDY_API_KEY"] = env["DEEPSEEK_API_KEY"]
                env.update(
                    {
                        "CODEBUDDY_CONFIG_DIR": str(isolated_state),
                        "CODEBUDDY_BASE_URL": codebuddy_base_url,
                        "CODEBUDDY_MODEL": CODEBUDDY_DEEPSEEK_MODEL,
                        "CODEBUDDY_SMALL_FAST_MODEL": CODEBUDDY_DEEPSEEK_MODEL,
                        "CODEBUDDY_BIG_SLOW_MODEL": CODEBUDDY_DEEPSEEK_MODEL,
                        "CODEBUDDY_CODE_SUBAGENT_MODEL": CODEBUDDY_DEEPSEEK_MODEL,
                        "CODEBUDDY_SKIP_BUILTIN_MARKETPLACE": "1",
                        "CODEBUDDY_SHARE_LINK_ENABLED": "false",
                        "CODEBUDDY_DISABLE_CRON": "1",
                        "CODEBUDDY_DISABLE_BACKGROUND_TASKS": "1",
                        "CODEBUDDY_CODE_DISABLE_BACKGROUND_TASKS": "1",
                        "CODEBUDDY_DISABLE_AUTO_MEMORY": "1",
                        "CODEBUDDY_DEFER_TOOL_LOADING": "false",
                        "CODEBUDDY_DEFERRED_TOOLS_MCP_READY_WAIT_MS": "60000",
                        "CODEBUDDY_DISABLE_MCP_LARGE_OUTPUT_FILES": "1",
                        "CODEBUDDY_DISABLE_SHELL_SNAPSHOT": "1",
                        "DISABLE_TELEMETRY": "1",
                        "DISABLE_ERROR_REPORTING": "1",
                        "DISABLE_AUTOUPDATER": "1",
                        "MCP_TIMEOUT": "60000",
                        "MCP_TOOL_TIMEOUT": str(self.timeout_seconds * 1000),
                    }
                )
        return env

    def _shell_command(self, args: list[str]) -> list[str]:
        if os.name == "nt":
            executable = Path(args[0])
            if not executable.is_absolute() and ("/" in args[0] or "\\" in args[0]):
                resolved = executable.resolve()
                if resolved.is_file():
                    args = [str(resolved), *args[1:]]
            if self.identity.product_id == "codex":
                native_codex = _windows_native_codex(self.identity.executable)
                if native_codex is not None:
                    return [str(native_codex), *args[1:]]
            if self.identity.product_id == "codebuddy":
                script = Path(args[0])
                if script.is_file() and not script.suffix:
                    node = shutil.which("node")
                    if node is None:
                        raise RuntimeError("Node.js is required to run pinned CodeBuddy CLI")
                    return [node, str(script), *args[1:]]
            return ["cmd", "/d", "/c", *args]
        return args


def default_state_source(product_id: str) -> Path:
    home = Path.home()
    env_name = {
        "codex": "CODEX_HOME",
        "claude_code": "CLAUDE_CONFIG_DIR",
        "qoder": "QODER_CONFIG_DIR",
        "codebuddy": "CODEBUDDY_CONFIG_DIR",
    }[product_id]
    configured = os.environ.get(env_name)
    if configured:
        return Path(configured)
    return home / {
        "codex": ".codex",
        "claude_code": ".claude",
        "qoder": ".qoder",
        "codebuddy": ".codebuddy",
    }[product_id]


def _windows_native_codex(executable: str) -> Path | None:
    launcher = shutil.which(executable)
    if launcher is None:
        return None
    launcher_path = Path(launcher)
    if launcher_path.suffix.casefold() == ".exe":
        return launcher_path

    package_root = launcher_path.parent / "node_modules" / "@openai" / "codex"
    candidates = [
        *package_root.glob("node_modules/@openai/codex-win32-*/vendor/*/codex/codex.exe"),
        *package_root.glob("vendor/*/codex/codex.exe"),
    ]
    return next((candidate for candidate in candidates if candidate.is_file()), None)


def _normalize_mcp_servers(
    value: list[str] | dict[str, list[str]], default_name: str
) -> dict[str, list[str]]:
    servers = {default_name: value} if isinstance(value, list) else value
    if not servers:
        raise ValueError("At least one MCP server is required")
    normalized: dict[str, list[str]] = {}
    for name, command in servers.items():
        if not re.fullmatch(r"[A-Za-z0-9_-]+", name):
            raise ValueError(f"Invalid MCP server name: {name!r}")
        if not isinstance(command, list) or not command or not all(
            isinstance(part, str) and part for part in command
        ):
            raise ValueError(f"MCP server {name!r} command must be a non-empty string list")
        normalized[name] = command
    return normalized


def _toml_scalar(value: Any) -> str:
    if isinstance(value, bool):
        return str(value).lower()
    if isinstance(value, (str, int, float)) and not isinstance(value, bool):
        return json.dumps(value, ensure_ascii=False)
    raise TypeError(f"Unsupported TOML scalar: {type(value).__name__}")


def _sanitize_codex_config(path: Path) -> None:
    if not path.is_file():
        return
    config = tomllib.loads(path.read_text(encoding="utf-8"))
    lines: list[str] = []
    for key in ("model", "model_provider", "model_reasoning_effort", "disable_response_storage"):
        value = config.get(key)
        if isinstance(value, (str, int, float, bool)):
            lines.append(f"{key} = {_toml_scalar(value)}")

    provider_name = config.get("model_provider")
    providers = config.get("model_providers", {})
    provider = providers.get(provider_name, {}) if isinstance(providers, dict) else {}
    if isinstance(provider_name, str) and isinstance(provider, dict):
        lines.extend(["", f"[model_providers.{provider_name}]"])
        for key in (
            "name",
            "base_url",
            "wire_api",
            "requires_openai_auth",
            "experimental_bearer_token",
        ):
            value = provider.get(key)
            if isinstance(value, (str, int, float, bool)):
                lines.append(f"{key} = {_toml_scalar(value)}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _configure_claude_mcp_state(
    isolated_state: Path, mcp_servers: dict[str, list[str]]
) -> None:
    path = isolated_state / ".claude.json"
    if path.is_file():
        try:
            config = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            config = {}
    else:
        config = {}
    if not isinstance(config, dict):
        config = {}
    config["mcpServers"] = {
        name: {"command": command[0], "args": command[1:]}
        for name, command in mcp_servers.items()
    }
    path.write_text(
        json.dumps(config, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _run_process(
    command: list[str],
    *,
    cwd: Path,
    env: dict[str, str],
    stdin_text: str,
    timeout_seconds: int,
) -> tuple[int | None, str, str, bool]:
    creationflags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
    process = subprocess.Popen(
        command,
        cwd=cwd,
        env=env,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=creationflags,
        start_new_session=os.name != "nt",
    )
    try:
        stdout, stderr = process.communicate(stdin_text, timeout=timeout_seconds)
        return process.returncode, stdout, stderr, False
    except subprocess.TimeoutExpired:
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
        else:
            os.killpg(process.pid, signal.SIGKILL)
        stdout, stderr = process.communicate()
        return process.returncode, stdout, stderr, True


def _read_final_response(path: Path, stdout: str) -> str:
    if path.is_file():
        return path.read_text(encoding="utf-8", errors="replace").strip()
    candidates: list[str] = []
    for line in stdout.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get("type") == "result" and isinstance(event.get("result"), str):
            candidates.append(event["result"])
        item = event.get("item")
        if isinstance(item, dict) and item.get("type") in {"agent_message", "assistant_message"}:
            text = item.get("text") or item.get("content")
            if isinstance(text, str):
                candidates.append(text)
    return candidates[-1].strip() if candidates else stdout.strip()


def _model_from_stream(stdout: str) -> str | None:
    for line in stdout.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get("type") == "system" and event.get("subtype") == "init":
            model = event.get("model")
            if isinstance(model, str) and model:
                return model
    return None


def _codebuddy_result_error(stdout: str) -> str | None:
    for line in stdout.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get("type") != "result":
            continue
        if event.get("is_error") is True or event.get("subtype") in {
            "error_during_execution",
            "error_max_turns",
        }:
            subtype = event.get("subtype")
            return subtype if isinstance(subtype, str) else "an error"
    return None


def _validate_codebuddy_route(
    route: dict[str, Any], *, expected_tool_count: int
) -> str | None:
    requests = route.get("observed_requests")
    if not route.get("api_key_supplied"):
        return "runner API credential was not supplied"
    if not isinstance(requests, list) or not requests:
        return "no audited custom-model request was observed"
    for index, request in enumerate(requests):
        if not isinstance(request, dict):
            return f"request {index} evidence is malformed"
        if request.get("host") != "api.deepseek.com":
            return f"request {index} used an unexpected provider host"
        if request.get("path") not in {"/chat/completions", "/v1/chat/completions"}:
            return f"request {index} used an unexpected provider path"
        if request.get("authorization_supplied") is not True:
            return f"request {index} did not use the runner credential"
        if request.get("request_model") != CODEBUDDY_DEEPSEEK_MODEL:
            return f"request {index} used an unexpected model"
        if request.get("tools_supplied") is not True:
            return f"request {index} omitted benchmark tool schemas"
        if request.get("tool_count") != expected_tool_count:
            return f"request {index} tool schema count did not match the benchmark"
        if request.get("response_status") != 200:
            return f"request {index} returned HTTP {request.get('response_status')}"
    return None


def _remove_directory(path: Path, attempts: int = 60) -> bool:
    consecutive_absent = 0
    for attempt in range(attempts):
        if path.exists():
            try:
                shutil.rmtree(path, onexc=_remove_readonly)
            except OSError:
                pass
        if path.exists():
            consecutive_absent = 0
        else:
            consecutive_absent += 1
            if consecutive_absent >= 3:
                return True
        if attempt + 1 < attempts:
            time.sleep(0.25)
    return not path.exists()


def _remove_readonly(function: Any, path: str, _exception: BaseException) -> None:
    os.chmod(path, stat.S_IWRITE)
    function(path)
