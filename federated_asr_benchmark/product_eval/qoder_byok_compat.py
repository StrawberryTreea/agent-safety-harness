from __future__ import annotations

from collections import Counter
import json
import os
from pathlib import Path
import re
from typing import Any

from product_eval.runner import ProductRunner


QODER_BYOK_CLI_VERSION = "1.0.48"
QODER_BYOK_CUSTOM_MODEL_ID = "model_1786147854337_zp7v4vd"
QODER_BYOK_PROVIDER = "deepseek"
QODER_BYOK_PROVIDER_MODEL = "deepseek-v4-flash-pg"
QODER_BYOK_TYPE = "pg"
QODER_CANONICAL_MODEL = "deepseek-v4-flash"


def qoder_cli_executable(package_root: Path) -> Path:
    suffix = ".cmd" if os.name == "nt" else ""
    candidates = (
        package_root / ".runtime" / "qodercli-1.0.48",
        package_root / ".codex_tmp" / "qodercli-runtime-1.0.48",
    )
    executables = [
        root / "node_modules" / ".bin" / f"qodercli{suffix}" for root in candidates
    ]
    return next((path.resolve() for path in executables if path.is_file()), executables[0].resolve())


def resolve_qoder_byok_model(product_id: str, requested_model: str | None) -> str | None:
    if product_id != "qoder":
        raise RuntimeError("The Qoder BYOK runner only accepts product_id='qoder'")
    return QODER_BYOK_CUSTOM_MODEL_ID


def validate_qoder_byok_runtime_model(
    product_id: str,
    requested_model: str | None,
    detected_model: str | None,
) -> str:
    if product_id != "qoder" or requested_model != QODER_BYOK_CUSTOM_MODEL_ID:
        raise RuntimeError("Qoder BYOK product/model binding drifted")
    if detected_model != QODER_BYOK_CUSTOM_MODEL_ID:
        raise RuntimeError(
            f"Qoder reported {detected_model!r}; expected custom model ID "
            f"{QODER_BYOK_CUSTOM_MODEL_ID!r}"
        )
    return QODER_CANONICAL_MODEL


class QoderByokProductRunner(ProductRunner):
    def _configure_qoder_byok_state(self, destination: Path) -> None:
        api_key = os.environ.get("DEEPSEEK_API_KEY")
        if not api_key:
            raise RuntimeError("DEEPSEEK_API_KEY is required for Qoder BYOK")
        settings_path = destination / "settings.json"
        settings: dict[str, Any]
        if settings_path.is_file():
            settings = json.loads(settings_path.read_text(encoding="utf-8"))
            if not isinstance(settings, dict):
                raise RuntimeError("Qoder settings.json must contain an object")
        else:
            settings = {}
        settings.pop("providers", None)
        settings.setdefault("modelConfigs", {})["customModels"] = [
            {
                "key": QODER_BYOK_CUSTOM_MODEL_ID,
                "displayName": "DeepSeek-V4-Flash",
                "provider": QODER_BYOK_PROVIDER,
                "apiKey": api_key,
                "model": QODER_BYOK_PROVIDER_MODEL,
                "baseURL": "https://api.deepseek.com",
                "format": "openai",
                "type": QODER_BYOK_TYPE,
                "isReasoning": True,
                "maxInputTokens": 1_000_000,
            }
        ]
        settings_path.write_text(
            json.dumps(settings, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def build_command(self, **kwargs: Any) -> list[str]:
        command = super().build_command(**kwargs)
        isolated_state = Path(kwargs["isolated_state_dir"])
        index = command.index("--config-dir") + 2
        command[index:index] = [
            "--settings",
            str(isolated_state / "settings.json"),
            "--setting-sources",
            "user",
        ]
        return command

    @staticmethod
    def _write_qoder_route_evidence(isolated_state: Path, output_path: Path) -> None:
        domain_counts: Counter[str] = Counter()
        custom_model_lines = 0
        managed_generation_lines = 0
        diagnostics: list[str] = []
        secret = os.environ.get("DEEPSEEK_API_KEY", "")
        for log_path in isolated_state.glob("logs/**/*.log"):
            text = log_path.read_text(encoding="utf-8", errors="replace")
            for line in text.splitlines():
                lowered = line.casefold()
                domain_counts.update(
                    domain.casefold()
                    for domain in re.findall(r"https?://([A-Za-z0-9.-]+)", line)
                )
                if (
                    "qoderinferrequest details" in lowered
                    and QODER_BYOK_PROVIDER_MODEL in lowered
                    and f'"type":"{QODER_BYOK_TYPE}"' in lowered
                    and '"api_key":"[redacted]"' in lowered
                ):
                    custom_model_lines += 1
                if "agent_chat_generation" in lowered and "-->" in lowered:
                    managed_generation_lines += 1
                if any(
                    marker in lowered
                    for marker in (
                        "qoderinferrequest details",
                        "byok_ready",
                        "custom pool",
                        "custom_model",
                    )
                ):
                    sanitized = line.replace(secret, "[REDACTED]") if secret else line
                    diagnostics.append(sanitized[:2_000])
        evidence = {
            "route_mode": "qoder_byok_custom_pool",
            "provider": QODER_BYOK_PROVIDER,
            "provider_model": QODER_BYOK_PROVIDER_MODEL,
            "canonical_model": QODER_CANONICAL_MODEL,
            "custom_model_id": QODER_BYOK_CUSTOM_MODEL_ID,
            "byok_type": QODER_BYOK_TYPE,
            "api_key_supplied": custom_model_lines > 0,
            "custom_model_request_lines": custom_model_lines,
            "qoder_managed_generation_lines": managed_generation_lines,
            "observed_domain_counts": dict(sorted(domain_counts.items())),
            "diagnostics": diagnostics[-100:],
        }
        output_path.write_text(
            json.dumps(evidence, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
