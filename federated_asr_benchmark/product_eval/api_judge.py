from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import urllib.error
import urllib.request
from typing import Any

from product_eval.judge_policy import load_and_validate_judge_policy


LOCKED_PROFILE_ID = "deepseek_v4_flash"
LOCKED_MODEL = "deepseek-v4-flash"
LOCKED_BASE_URL = "https://api.deepseek.com"


def load_immutable_judge_profile(
    evaluation_config_path: Path,
    registry_path: Path,
) -> dict[str, Any]:
    evaluation = json.loads(evaluation_config_path.read_text(encoding="utf-8"))
    profile = evaluation.get("judge_profile")
    if not isinstance(profile, dict):
        raise RuntimeError("immutable evaluation config has no judge_profile")
    policy = load_and_validate_judge_policy(registry_path)
    normalized = {
        "profile_id": str(profile.get("profile_id") or ""),
        "type": str(profile.get("type") or ""),
        "model": str(profile.get("model") or ""),
        "base_url": str(profile.get("base_url") or "").rstrip("/"),
        "api_key_env": str(profile.get("api_key_env") or ""),
        "temperature": float(profile.get("temperature", 0.0)),
    }
    expected = {
        key: policy[key]
        for key in (
            "profile_id",
            "type",
            "model",
            "base_url",
            "api_key_env",
            "temperature",
        )
    }
    if normalized != expected:
        raise RuntimeError(
            f"immutable evaluation judge differs from benchmark policy: "
            f"expected {expected}, got {normalized}"
        )
    if (
        normalized["profile_id"] != LOCKED_PROFILE_ID
        or normalized["type"] != "openai"
        or normalized["model"] != LOCKED_MODEL
        or normalized["base_url"] != LOCKED_BASE_URL
        or normalized["temperature"] != 0.0
    ):
        raise RuntimeError("evaluation judge is not the locked DeepSeek V4 Flash profile")
    return normalized


class OpenAICompatibleJudge:
    def __init__(
        self,
        profile: dict[str, Any],
        *,
        evidence_path: Path | None = None,
        timeout_seconds: int = 120,
    ):
        self.profile = dict(profile)
        self.evidence_path = evidence_path.resolve() if evidence_path else None
        self.timeout_seconds = timeout_seconds
        self.calls: list[dict[str, Any]] = []
        self._write_evidence()

    def preflight(self) -> dict[str, Any]:
        key_env = str(self.profile["api_key_env"])
        if not os.environ.get(key_env, "").strip():
            raise RuntimeError(f"locked API judge credential is missing from {key_env}")
        return self.binding()

    def binding(self) -> dict[str, Any]:
        return {
            "profile_id": self.profile["profile_id"],
            "type": self.profile["type"],
            "model": self.profile["model"],
            "base_url": self.profile["base_url"],
            "api_key_env": self.profile["api_key_env"],
            "temperature": self.profile["temperature"],
        }

    def __call__(self, prompt: str) -> str:
        self.preflight()
        payload = {
            "model": self.profile["model"],
            "messages": [{"role": "user", "content": prompt}],
            "temperature": self.profile["temperature"],
        }
        endpoint = f"{self.profile['base_url']}/chat/completions"
        request = urllib.request.Request(
            endpoint,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {os.environ[self.profile['api_key_env']]}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        record: dict[str, Any] = {
            "call_index": len(self.calls) + 1,
            "started_at": datetime.now(timezone.utc).isoformat(),
            "endpoint": endpoint,
            "model": self.profile["model"],
            "temperature": self.profile["temperature"],
            "prompt": prompt,
            "prompt_sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
        }
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                raw = response.read().decode("utf-8")
            body = json.loads(raw)
            choices = body.get("choices")
            if not isinstance(choices, list) or not choices:
                raise RuntimeError("API judge response has no choices")
            message = choices[0].get("message")
            completion = message.get("content") if isinstance(message, dict) else None
            if not isinstance(completion, str) or not completion.strip():
                raise RuntimeError("API judge response has no text completion")
            completion = completion.strip()
            record.update(
                {
                    "status": "completed",
                    "response_id": body.get("id"),
                    "completion": completion,
                    "completion_sha256": hashlib.sha256(
                        completion.encode("utf-8")
                    ).hexdigest(),
                    "finish_reason": choices[0].get("finish_reason"),
                    "usage": body.get("usage"),
                }
            )
            return completion
        except Exception as exc:
            if isinstance(exc, urllib.error.HTTPError):
                error = f"HTTP {exc.code}"
            else:
                error = f"{type(exc).__name__}: {exc}"
            record.update({"status": "failed", "error": error})
            raise
        finally:
            record["finished_at"] = datetime.now(timezone.utc).isoformat()
            self.calls.append(record)
            self._write_evidence()

    def _write_evidence(self) -> None:
        if self.evidence_path is None:
            return
        self.evidence_path.parent.mkdir(parents=True, exist_ok=True)
        artifact = {
            "schema_version": "0.1",
            "judge": self.binding(),
            "calls": self.calls,
        }
        temporary = self.evidence_path.with_name(f"{self.evidence_path.name}.tmp")
        temporary.write_text(
            json.dumps(artifact, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        temporary.replace(self.evidence_path)
