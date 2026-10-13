from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from tools.audit_smoke import (
    _codebuddy_route_error,
    _harness_errors,
    _qoder_route_error,
)


class SmokeAuditTest(unittest.TestCase):
    def test_codebuddy_route_requires_authenticated_tool_requests(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            route = root / "product_network_route.json"
            route.write_text(
                json.dumps(
                    {
                        "route_mode": "audited_local_forwarder",
                        "provider": "deepseek",
                        "provider_model": "deepseek-v4-flash",
                        "api_key_supplied": True,
                        "effective_model": "custom-local:deepseek-v4-flash",
                        "observed_custom_model_requests": 1,
                        "observed_requests": [
                            {
                                "host": "api.deepseek.com",
                                "authorization_supplied": True,
                                "request_model": "deepseek-v4-flash",
                                "response_status": 200,
                                "tools_supplied": True,
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            self.assertIsNone(_codebuddy_route_error(root))
            payload = json.loads(route.read_text(encoding="utf-8"))
            payload["observed_requests"][0]["authorization_supplied"] = False
            route.write_text(json.dumps(payload), encoding="utf-8")
            self.assertIn(
                "unexpected CodeBuddy BYOK route",
                _codebuddy_route_error(root) or "",
            )

    def test_harness_errors_detect_mcp_server_and_transport_failures(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "sample_mcp_events.jsonl").write_text(
                json.dumps({"event_type": "server_error", "error": "UnicodeEncodeError"})
                + "\n",
                encoding="utf-8",
            )
            (root / "product_stderr.txt").write_text(
                "resources/list failed: Transport closed\n", encoding="utf-8"
            )
            errors = _harness_errors(root)
            self.assertTrue(any("server_error" in error for error in errors))
            self.assertTrue(any("transport closed" in error for error in errors))
            self.assertTrue(any("resources/list failed" in error for error in errors))

    def test_harness_errors_accept_clean_logs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "sample_mcp_events.jsonl").write_text(
                json.dumps({"event_type": "tool_call", "is_error": False}) + "\n",
                encoding="utf-8",
            )
            (root / "product_stderr.txt").write_text("", encoding="utf-8")
            self.assertEqual(_harness_errors(root), [])

    def test_qoder_route_requires_official_deepseek_configuration(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            route = root / "product_network_route.json"
            route.write_text(
                json.dumps(
                    {
                        "provider": "deepseek",
                        "provider_model": "deepseek-v4-flash",
                        "configured_base_url": "https://api.deepseek.com",
                        "deepseek_official_request_lines": 1,
                        "qoder_inference_request_lines": 0,
                        "observed_requests": [
                            {"host": "api.deepseek.com", "path": "/chat/completions"}
                        ],
                    }
                ),
                encoding="utf-8",
            )
            self.assertIsNone(_qoder_route_error(root))
            route.write_text(
                json.dumps(
                    {
                        "provider": "deepseek",
                        "provider_model": "deepseek-v4-flash",
                        "configured_base_url": "https://example.invalid",
                    }
                ),
                encoding="utf-8",
            )
            self.assertIn("unexpected Qoder BYOK route", _qoder_route_error(root) or "")

    def test_qoder_route_rejects_config_only_and_managed_generation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            route = root / "product_network_route.json"
            base = {
                "provider": "deepseek",
                "provider_model": "deepseek-v4-flash",
                "configured_base_url": "https://api.deepseek.com",
                "deepseek_official_request_lines": 0,
                "qoder_inference_request_lines": 0,
                "observed_requests": [],
            }
            route.write_text(json.dumps(base), encoding="utf-8")
            self.assertIn("deepseek_requests=0", _qoder_route_error(root) or "")
            base["qoder_inference_request_lines"] = 1
            base["observed_requests"] = [
                {
                    "host": "api3.qoder.sh",
                    "path": "/algo/api/v2/service/pro/sse/agent_chat_generation",
                }
            ]
            route.write_text(json.dumps(base), encoding="utf-8")
            self.assertIn("managed_generation=True", _qoder_route_error(root) or "")


if __name__ == "__main__":
    unittest.main()
