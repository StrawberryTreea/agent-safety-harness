from __future__ import annotations

import json
from http.client import HTTPConnection
import os
from pathlib import Path
import platform
import sys
import tempfile
import unittest
from unittest.mock import patch

from product_eval.runner import (
    ProductIdentity,
    ProductRunner,
    _codebuddy_result_error,
    _validate_codebuddy_route,
)
from product_eval.product_model_policy import validate_runtime_model
from product_eval.route_audit_proxy import DeepSeekRouteAuditProxy


class ProductRunnerTest(unittest.TestCase):
    def command_for(self, product_id: str) -> list[str]:
        executable = {
            "codex": "codex",
            "claude_code": "claude",
            "qoder": "qodercli",
            "codebuddy": "codebuddy",
        }[product_id]
        runner = ProductRunner(ProductIdentity(product_id, executable, "test"))
        # Command construction must not depend on a locally installed Codex CLI.
        with tempfile.TemporaryDirectory() as tmp, patch(
            "product_eval.runner._windows_native_codex",
            return_value=Path(tmp) / "codex.exe",
        ):
            root = Path(tmp)
            return runner.build_command(
                workspace=root / "workspace",
                mcp_config=root / "mcp.json",
                mcp_server_command=["python", "server.py"],
                allowed_tools=["mcp__saber__bash"],
                final_response_file=root / "response.txt",
                isolated_state_dir=root / "state",
            )

    def test_all_product_commands_are_noninteractive_and_do_not_override_system_prompt(self) -> None:
        for product_id in ("codex", "claude_code", "qoder", "codebuddy"):
            command = self.command_for(product_id)
            rendered = " ".join(command)
            self.assertNotIn("system-prompt", rendered)
            self.assertNotIn("append-system-prompt", rendered)
            if product_id == "codex":
                self.assertIn("ignore-user-config", rendered)
            else:
                self.assertNotIn("ignore-user-config", rendered)
            self.assertNotIn("--safe-mode", rendered)
            self.assertIn("saber", rendered)
        self.assertIn("--ephemeral", self.command_for("codex"))
        codex_command = self.command_for("codex")
        self.assertIn("--dangerously-bypass-approvals-and-sandbox", codex_command)
        if platform.system() == "Windows":
            self.assertTrue(codex_command[0].casefold().endswith("codex.exe"))
            self.assertNotEqual(codex_command[:3], ["cmd", "/d", "/c"])
        self.assertNotIn("--sandbox", codex_command)
        self.assertEqual(codex_command.count("--disable"), 3)
        self.assertIn("shell_tool", codex_command)
        self.assertIn("unified_exec", codex_command)
        self.assertIn("plugins", codex_command)
        self.assertIn("--no-session-persistence", self.command_for("claude_code"))
        self.assertIn("--no-session-persistence", self.command_for("qoder"))
        self.assertIn("--verbose", self.command_for("claude_code"))
        self.assertNotIn("--verbose", self.command_for("qoder"))
        self.assertIn("--disable-slash-commands", self.command_for("claude_code"))
        self.assertIn("--dangerously-skip-permissions", self.command_for("claude_code"))
        self.assertIn('{"enableAllProjectMcpServers":true}', self.command_for("claude_code"))
        self.assertNotIn("--mcp-config", self.command_for("claude_code"))
        self.assertIn("--mcp-config", self.command_for("qoder"))
        codebuddy_command = self.command_for("codebuddy")
        self.assertIn("--mcp-config", codebuddy_command)
        self.assertIn("--strict-mcp-config", codebuddy_command)
        self.assertIn("--allowedTools", codebuddy_command)
        self.assertIn("--disallowedTools", codebuddy_command)
        disabled_start = codebuddy_command.index("--disallowedTools") + 1
        disabled_end = (
            codebuddy_command.index("--model", disabled_start)
            if "--model" in codebuddy_command[disabled_start:]
            else len(codebuddy_command)
        )
        disabled = codebuddy_command[disabled_start:disabled_end]
        self.assertIn("Bash", disabled)
        self.assertIn("WebSearch", disabled)
        self.assertFalse(any("mcp__" in name for name in disabled))
        self.assertTrue(all("," not in name for name in disabled))
        self.assertIn("--tools=NoDefer(mcp__saber__bash)", codebuddy_command)
        self.assertIn("-y", codebuddy_command)
        self.assertNotIn("--config-dir", codebuddy_command)

    def test_codebuddy_forces_all_selected_mcp_tools_out_of_deferred_loading(self) -> None:
        runner = ProductRunner(
            ProductIdentity("codebuddy", "codebuddy", "test", "deepseek-v4-flash")
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            command = runner.build_command(
                workspace=root / "workspace",
                mcp_config=root / "mcp.json",
                mcp_server_command=["python", "server.py"],
                allowed_tools=[
                    "mcp__agentdojo__get_balance",
                    "mcp__agentdojo__send_money",
                ],
                final_response_file=root / "response.txt",
                isolated_state_dir=root / "state",
            )
        tools_argument = next(arg for arg in command if arg.startswith("--tools="))
        self.assertEqual(
            tools_argument,
            "--tools=NoDefer(mcp__agentdojo__get_balance),"
            "NoDefer(mcp__agentdojo__send_money)",
        )
        self.assertIn(
            "mcp__agentdojo__get_balance,mcp__agentdojo__send_money", command
        )

    def test_controlled_model_arguments_are_pinned_for_each_product(self) -> None:
        configured = {
            "codex": "deepseek-v4-flash",
            "claude_code": "deepseek-v4-flash",
            "qoder": "dfmodel",
            "codebuddy": "deepseek-v4-flash",
        }
        for product_id, model in configured.items():
            executable = {
                "codex": "codex",
                "claude_code": "claude",
                "qoder": "qodercli",
                "codebuddy": "codebuddy",
            }[product_id]
            runner = ProductRunner(ProductIdentity(product_id, executable, "test", model))
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                command = runner.build_command(
                    workspace=root / "workspace",
                    mcp_config=root / "mcp.json",
                    mcp_server_command=["python", "server.py"],
                    allowed_tools=["mcp__saber__bash"],
                    final_response_file=root / "response.txt",
                    isolated_state_dir=root / "state",
                )
            self.assertIn(model, command)
            if product_id == "codex":
                rendered = " ".join(command)
                self.assertIn("api.deepseek.com/v1", rendered)
                self.assertIn("DEEPSEEK_API_KEY", rendered)
                self.assertIn('wire_api="responses"', rendered)
                self.assertLess(command.index("mcp_servers.saber.command=\"python\""), command.index("exec"))

    def test_codebuddy_stream_errors_fail_closed_even_with_zero_process_exit(self) -> None:
        stdout = "\n".join(
            (
                json.dumps({"type": "system", "subtype": "init"}),
                json.dumps(
                    {
                        "type": "result",
                        "subtype": "error_during_execution",
                        "is_error": True,
                    }
                ),
            )
        )
        self.assertEqual(
            _codebuddy_result_error(stdout),
            "error_during_execution",
        )
        self.assertIsNone(
            _codebuddy_result_error(
                json.dumps(
                    {"type": "result", "subtype": "success", "is_error": False}
                )
            )
        )

    def test_codebuddy_route_policy_requires_direct_authenticated_tool_requests(self) -> None:
        request = {
            "host": "api.deepseek.com",
            "path": "/chat/completions",
            "authorization_supplied": True,
            "request_model": "deepseek-v4-flash",
            "tools_supplied": True,
            "tool_count": 4,
            "response_status": 200,
        }
        route = {"api_key_supplied": True, "observed_requests": [request]}
        self.assertIsNone(_validate_codebuddy_route(route, expected_tool_count=4))
        self.assertIn(
            "omitted benchmark tool schemas",
            _validate_codebuddy_route(
                {
                    **route,
                    "observed_requests": [{**request, "tools_supplied": False}],
                },
                expected_tool_count=4,
            ),
        )
        self.assertIn(
            "HTTP 429",
            _validate_codebuddy_route(
                {
                    **route,
                    "observed_requests": [{**request, "response_status": 429}],
                },
                expected_tool_count=4,
            ),
        )

    def test_runtime_model_policy_requires_qoder_byok_model_and_rejects_mismatch(self) -> None:
        self.assertEqual(
            validate_runtime_model(
                "qoder", "deepseek/deepseek-v4-flash", "DeepSeek-V4-Flash"
            ),
            "deepseek-v4-flash",
        )
        with self.assertRaises(RuntimeError):
            validate_runtime_model("qoder", "dfmodel", "DeepSeek-V4-Flash")
            with self.assertRaises(RuntimeError):
                validate_runtime_model("qoder", "custom-deepseek-id", "Auto")
        with self.assertRaises(RuntimeError):
            validate_runtime_model("claude_code", "deepseek-v4-flash", None)
        self.assertEqual(
            validate_runtime_model(
                "codebuddy", "deepseek-v4-flash", "custom-local:deepseek-v4-flash"
            ),
            "deepseek-v4-flash",
        )
        with self.assertRaises(RuntimeError):
            validate_runtime_model("codebuddy", "deepseek-v4-flash", None)

    def test_multiple_mcp_servers_are_rendered_for_all_products(self) -> None:
        servers = {"normal": ["python", "normal.py"], "attack": ["python", "attack.py"]}
        for product_id in ("codex", "claude_code", "qoder", "codebuddy"):
            executable = {
                "codex": "codex",
                "claude_code": "claude",
                "qoder": "qodercli",
                "codebuddy": "codebuddy",
            }[product_id]
            runner = ProductRunner(ProductIdentity(product_id, executable, "test"))
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                command = runner.build_command(
                    workspace=root / "workspace",
                    mcp_config=root / "mcp.json",
                    mcp_server_command=servers,
                    allowed_tools=["mcp__normal__read", "mcp__attack__read"],
                    final_response_file=root / "response.txt",
                    isolated_state_dir=root / "state",
                )
                rendered = " ".join(command)
                self.assertIn("normal", rendered)
                self.assertIn("attack", rendered)

    def test_dry_run_does_not_copy_authentication_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source-state"
            source.mkdir()
            (source / "auth.json").write_text("secret", encoding="utf-8")
            runner = ProductRunner(
                ProductIdentity("codex", "codex", "test", state_source=str(source))
            )
            output = root / "output"
            result = runner.run(
                run_id="dry",
                benchmark_id="saber",
                case_id="saber:A_fs_001",
                prompt="test",
                output_dir=output,
                mcp_server_command=["python", "server.py"],
                allowed_tool_names=["bash"],
                dry_run=True,
            )
            self.assertEqual(result.status, "dry_run")
            self.assertFalse((output / "dry_run_state_placeholder" / "auth.json").exists())
            self.assertTrue(result.isolated_state_removed)
            artifact = json.loads((output / "product_run.json").read_text(encoding="utf-8"))
            self.assertEqual(artifact["product"]["product_id"], "codex")
            self.assertTrue((output / "prompt.txt").is_file())

    def test_failed_execution_removes_temporary_authentication_copy(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source-state"
            source.mkdir()
            (source / "auth.json").write_text("secret", encoding="utf-8")
            runner = ProductRunner(
                ProductIdentity(
                    "codex",
                    sys.executable,
                    platform.python_version(),
                    state_source=str(source),
                ),
                timeout_seconds=10,
            )
            output = root / "output"
            result = runner.run(
                run_id="failed",
                benchmark_id="saber",
                case_id="saber:A_fs_001",
                prompt="test",
                output_dir=output,
                mcp_server_command=[sys.executable, "server.py"],
                allowed_tool_names=["bash"],
            )
            self.assertEqual(result.status, "failed")
            self.assertTrue(result.isolated_state_removed)
            self.assertFalse(Path(result.isolated_state_dir).exists())
            self.assertEqual(list(output.rglob("auth.json")), [])

    def test_codex_state_keeps_only_provider_routing_and_records_model(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source"
            source.mkdir()
            (source / "agents").mkdir()
            (source / "agents" / "custom.toml").write_text("invalid=true", encoding="utf-8")
            (source / "desktop-state.json").write_text("irrelevant", encoding="utf-8")
            (source / "config.toml").write_text(
                '\n'.join(
                    (
                        'model = "configured-model"',
                        'model_provider = "custom"',
                        '[model_providers.custom]',
                        'name = "Custom"',
                        'base_url = "https://provider.example/v1"',
                        'experimental_bearer_token = "temporary-secret"',
                        '[mcp_servers.user_server]',
                        'command = "unsafe"',
                    )
                )
                + "\n",
                encoding="utf-8",
            )
            runner = ProductRunner(
                ProductIdentity("codex", "codex", "test", state_source=str(source))
            )
            destination = root / "isolated"
            runner._prepare_isolated_state(destination)
            sanitized = (destination / "config.toml").read_text(encoding="utf-8")
            self.assertIn("temporary-secret", sanitized)
            self.assertNotIn("mcp_servers", sanitized)
            self.assertFalse((destination / "agents").exists())
            self.assertFalse((destination / "desktop-state.json").exists())
            self.assertEqual(runner._effective_product_model(), "configured-model")

    def test_isolated_state_ignores_sqlite_logs_and_journals(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source"
            source.mkdir()
            (source / "auth.json").write_text("secret", encoding="utf-8")
            for name in ("logs_2.sqlite", "logs_2.sqlite-shm", "logs_2.sqlite-wal"):
                (source / name).write_text("transient", encoding="utf-8")
            (source / "session.sqlite-shm").write_text("transient", encoding="utf-8")
            (source / "session.sqlite-wal").write_text("transient", encoding="utf-8")

            runner = ProductRunner(
                ProductIdentity("codex", "codex", "test", state_source=str(source))
            )
            destination = root / "isolated"
            runner._prepare_isolated_state(destination)

            self.assertTrue((destination / "auth.json").is_file())
            self.assertEqual(list(destination.glob("*.sqlite*")), [])

    def test_qoder_byok_model_is_materialized_only_in_isolated_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source"
            source.mkdir()
            (source / ".auth").mkdir()
            (source / ".auth" / "user").write_text("auth", encoding="utf-8")
            (source / ".models").mkdir()
            (source / ".models" / "catalog-v6").write_text("stale", encoding="utf-8")
            (source / "settings.json").write_text(
                json.dumps({"permissions": {"defaultMode": "default"}}),
                encoding="utf-8",
            )
            runner = ProductRunner(
                ProductIdentity(
                    "qoder",
                    "qodercli",
                    "test",
                    product_model="deepseek/deepseek-v4-flash",
                    state_source=str(source),
                )
            )
            destination = root / "isolated"
            with patch.dict("os.environ", {"DEEPSEEK_API_KEY": "test-secret"}):
                runner._prepare_isolated_state(destination)
            settings = json.loads((destination / "settings.json").read_text(encoding="utf-8"))
            self.assertEqual(settings["modelConfigs"]["customModels"], [])
            provider = settings["providers"]["deepseek"]
            self.assertEqual(provider["type"], "openai-compatible")
            self.assertEqual(provider["model"], "deepseek-v4-flash")
            self.assertEqual(provider["baseUrl"], "https://api.deepseek.com")
            self.assertEqual(provider["apiKey"], "test-secret")
            self.assertEqual(provider["models"][0]["model"], "deepseek-v4-flash")
            self.assertTrue((destination / ".auth" / "user").is_file())
            self.assertFalse((destination / ".models").exists())
            self.assertNotIn("modelConfigs", json.loads((source / "settings.json").read_text()))

    def test_codebuddy_byok_state_contains_no_persisted_credentials(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            destination = Path(tmp) / "isolated"
            runner = ProductRunner(
                ProductIdentity(
                    "codebuddy",
                    "codebuddy",
                    "test",
                    product_model="deepseek-v4-flash",
                )
            )
            with patch.dict("os.environ", {"DEEPSEEK_API_KEY": "test-secret"}):
                runner._prepare_isolated_state(
                    destination,
                    codebuddy_base_url="http://127.0.0.1:12345",
                )
            model_path = destination / "models.json"
            self.assertTrue(model_path.is_file())
            model_text = model_path.read_text(encoding="utf-8")
            self.assertNotIn("test-secret", model_text)
            models = json.loads(model_text)
            self.assertEqual(models["availableModels"], ["deepseek-v4-flash"])
            model = models["models"][0]
            self.assertEqual(model["id"], "deepseek-v4-flash")
            self.assertEqual(
                model["url"], "http://127.0.0.1:12345/chat/completions"
            )
            self.assertEqual(model["apiKey"], "${DEEPSEEK_API_KEY}")
            self.assertTrue(model["supportsToolCall"])

    def test_codebuddy_fails_closed_without_runner_api_key(self) -> None:
        runner = ProductRunner(
            ProductIdentity(
                "codebuddy",
                "codebuddy",
                "test",
                product_model="deepseek-v4-flash",
            )
        )
        with tempfile.TemporaryDirectory() as tmp, patch.dict("os.environ", {}, clear=True):
            with self.assertRaisesRegex(RuntimeError, "DEEPSEEK_API_KEY"):
                runner._prepare_isolated_state(
                    Path(tmp),
                    codebuddy_base_url="http://127.0.0.1:12345",
                )

    def test_codebuddy_fails_closed_without_audited_base_url(self) -> None:
        runner = ProductRunner(
            ProductIdentity(
                "codebuddy",
                "codebuddy",
                "test",
                product_model="deepseek-v4-flash",
            )
        )
        with tempfile.TemporaryDirectory() as tmp, patch.dict(
            "os.environ", {"DEEPSEEK_API_KEY": "test-secret"}
        ):
            with self.assertRaisesRegex(RuntimeError, "audited DeepSeek base URL"):
                runner._prepare_isolated_state(Path(tmp))

    def test_codebuddy_child_env_excludes_product_native_credentials(self) -> None:
        runner = ProductRunner(
            ProductIdentity(
                "codebuddy",
                "codebuddy",
                "test",
                product_model="deepseek-v4-flash",
            )
        )
        with tempfile.TemporaryDirectory() as tmp, patch.dict(
            "os.environ",
            {
                "DEEPSEEK_API_KEY": "runner-key",
                "CODEBUDDY_API_KEY": "managed-key",
                "CODEBUDDY_AUTH_TOKEN": "managed-token",
            },
            clear=False,
        ):
            env = runner._child_env(
                Path(tmp), codebuddy_base_url="http://127.0.0.1:12345"
            )
        self.assertEqual(env["DEEPSEEK_API_KEY"], "runner-key")
        self.assertEqual(env["CODEBUDDY_API_KEY"], "runner-key")
        self.assertNotIn("CODEBUDDY_AUTH_TOKEN", env)
        self.assertEqual(env["CODEBUDDY_BASE_URL"], "http://127.0.0.1:12345")
        self.assertEqual(env["CODEBUDDY_CONFIG_DIR"], tmp)
        self.assertEqual(env["CODEBUDDY_DEFER_TOOL_LOADING"], "false")
        self.assertEqual(env["CODEBUDDY_DEFERRED_TOOLS_MCP_READY_WAIT_MS"], "60000")
        self.assertEqual(env["MCP_TIMEOUT"], "60000")
        self.assertEqual(env["MCP_TOOL_TIMEOUT"], str(runner.timeout_seconds * 1000))
        self.assertEqual(env["DISABLE_TELEMETRY"], "1")

    def test_child_env_gives_mcp_runtime_an_isolated_home(self) -> None:
        for product_id in ("codex", "claude_code", "qoder", "codebuddy"):
            runner = ProductRunner(
                ProductIdentity(
                    product_id,
                    product_id,
                    "test",
                    product_model="deepseek-v4-flash",
                )
            )
            with tempfile.TemporaryDirectory() as tmp, patch.dict(
                "os.environ", {"DEEPSEEK_API_KEY": "runner-key"}, clear=False
            ):
                state = Path(tmp)
                env = runner._child_env(
                    state,
                    codebuddy_base_url=(
                        "http://127.0.0.1:12345" if product_id == "codebuddy" else None
                    ),
                )
            self.assertEqual(env["HOME"], str(state))
            self.assertEqual(env["USERPROFILE"], str(state))

    def test_route_audit_proxy_records_only_sanitized_metadata(self) -> None:
        captured: dict[str, object] = {}

        class FakeResponse:
            status = 200
            reason = "OK"

            def __init__(self) -> None:
                self.parts = [
                    b'data: {"choices":[{"delta":{"tool_calls":[]}}]}\n\n',
                    b"",
                ]

            def getheaders(self) -> list[tuple[str, str]]:
                return [("Content-Type", "text/event-stream")]

            def read(self, _size: int) -> bytes:
                return self.parts.pop(0)

        class FakeConnection:
            def __init__(self, host: str, timeout: int) -> None:
                captured["host"] = host
                captured["timeout"] = timeout

            def request(
                self,
                method: str,
                path: str,
                *,
                body: bytes,
                headers: dict[str, str],
            ) -> None:
                captured.update(
                    {"method": method, "path": path, "body": body, "headers": headers}
                )

            def getresponse(self) -> FakeResponse:
                return FakeResponse()

            def close(self) -> None:
                return

        with patch(
            "product_eval.route_audit_proxy.HTTPSConnection", FakeConnection
        ), DeepSeekRouteAuditProxy(timeout_seconds=30) as proxy:
            connection = HTTPConnection(
                proxy._server.server_address[0], proxy._server.server_address[1]
            )
            connection.request(
                "POST",
                "/v1/chat/completions",
                body=(
                    b'{"model":"deepseek-v4-flash","tools":['
                    b'{"type":"function","function":{"name":"safe_tool"}}],'
                    b'"tool_choice":"auto","messages":[{"role":"user",'
                    b'"content":"private prompt"}]}'
                ),
                headers={
                    "Authorization": "Bearer secret-value",
                    "Content-Type": "application/json",
                },
            )
            response = connection.getresponse()
            self.assertEqual(response.status, 200)
            self.assertIn(b'"tool_calls"', response.read())
            connection.close()
        evidence = proxy.evidence(model="deepseek-v4-flash", api_key_supplied=True)
        self.assertEqual(captured["host"], "api.deepseek.com")
        self.assertEqual(captured["path"], "/v1/chat/completions")
        self.assertEqual(evidence["observed_custom_model_requests"], 1)
        request = evidence["observed_requests"][0]
        self.assertTrue(request["authorization_supplied"])
        self.assertEqual(request["request_model"], "deepseek-v4-flash")
        self.assertIn("tools", request["request_fields"])
        self.assertTrue(request["tools_supplied"])
        self.assertEqual(request["tool_count"], 1)
        self.assertTrue(request["tool_choice_supplied"])
        self.assertFalse(request["functions_supplied"])
        self.assertEqual(request["function_count"], 0)
        self.assertIsNone(request["function_names_sha256"])
        self.assertFalse(request["function_call_supplied"])
        self.assertEqual(request["response_tool_call_markers"], 1)
        self.assertNotIn("secret-value", json.dumps(evidence))
        self.assertNotIn("private prompt", json.dumps(evidence))

    def test_qoder_route_evidence_contains_only_sanitized_domain_counts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            log_dir = root / "logs" / "runs" / "probe"
            log_dir.mkdir(parents=True)
            (log_dir / "qodercli.log").write_text(
                "--> operation=generate method=POST "
                "url=https://api.deepseek.com/chat/completions secret=hidden\n"
                "Elected inference endpoint: https://api3.qoder.sh\n",
                encoding="utf-8",
            )
            output = root / "route.json"
            ProductRunner._write_qoder_route_evidence(root, output)
            evidence = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(evidence["deepseek_official_request_lines"], 1)
            self.assertEqual(evidence["qoder_inference_request_lines"], 0)
            self.assertEqual(evidence["observed_requests"][0]["host"], "api.deepseek.com")
            self.assertEqual(
                evidence["observed_requests"][0]["path"], "/chat/completions"
            )
            self.assertNotIn("secret", output.read_text(encoding="utf-8"))

    def test_qoder_route_evidence_redacts_external_provider_diagnostics(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            log_dir = root / "logs"
            log_dir.mkdir()
            (log_dir / "qodercli.log").write_text(
                "[external-providers] configured api-key-is-secret "
                "uid=019fc7c6-514d-7ddd-bf2a-d318c9e70b00\n",
                encoding="utf-8",
            )
            output = root / "route.json"
            with patch.dict("os.environ", {"DEEPSEEK_API_KEY": "api-key-is-secret"}):
                ProductRunner._write_qoder_route_evidence(root, output)
            evidence = json.loads(output.read_text(encoding="utf-8"))
            self.assertIn("[REDACTED]", evidence["diagnostics"][0])
            self.assertNotIn("api-key-is-secret", output.read_text(encoding="utf-8"))
            self.assertNotIn("019fc7c6", output.read_text(encoding="utf-8"))

    @unittest.skipUnless(os.name == "nt", "Windows command resolution")
    def test_relative_product_executable_is_resolved_before_workspace_launch(self) -> None:
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as tmp:
            executable = Path(tmp) / "probe.cmd"
            executable.write_text("@echo off\n", encoding="ascii")
            relative = str(executable.relative_to(Path.cwd()))
            runner = ProductRunner(ProductIdentity("qoder", relative, "test"))
            command = runner._shell_command([relative, "--version"])
            self.assertEqual(Path(command[3]), executable.resolve())

    @unittest.skipUnless(os.name == "nt", "Windows command resolution")
    def test_pinned_codebuddy_node_script_runs_without_user_launcher(self) -> None:
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as tmp:
            executable = Path(tmp) / "codebuddy"
            executable.write_text("#!/usr/bin/env node\n", encoding="ascii")
            relative = str(executable.relative_to(Path.cwd()))
            runner = ProductRunner(ProductIdentity("codebuddy", relative, "test"))
            command = runner._shell_command([relative, "--version"])
            self.assertTrue(Path(command[0]).name.casefold().startswith("node"))
            self.assertEqual(Path(command[1]), executable.resolve())

    def test_qoder_route_evidence_detects_managed_generation_but_not_telemetry(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            log_dir = root / "logs"
            log_dir.mkdir()
            (log_dir / "qodercli.log").write_text(
                "--> operation=telemetry method=POST url=https://api3.qoder.sh/metrics\n"
                "--> operation=generate method=POST "
                "url=https://api3.qoder.sh/algo/api/v2/service/pro/sse/agent_chat_generation\n",
                encoding="utf-8",
            )
            output = root / "route.json"
            ProductRunner._write_qoder_route_evidence(root, output)
            evidence = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(evidence["qoder_inference_request_lines"], 1)


if __name__ == "__main__":
    unittest.main()
