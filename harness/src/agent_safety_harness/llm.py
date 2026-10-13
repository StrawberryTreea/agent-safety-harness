from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ChatCompletionResult:
    content: str
    usage: dict[str, Any] = field(default_factory=dict)
    raw_response: dict[str, Any] = field(default_factory=dict)


@dataclass
class OpenAICompatibleChatClient:
    base_url: str
    api_key_env: str
    model: str
    temperature: float = 0.0
    max_tokens: int = 512
    timeout_seconds: int = 60

    def complete(self, messages: list[dict[str, Any]]) -> str:
        return self.complete_with_usage(messages).content

    def complete_with_usage(self, messages: list[dict[str, Any]]) -> ChatCompletionResult:
        api_key = os.environ.get(self.api_key_env)
        if not api_key:
            raise RuntimeError(f"Missing API key environment variable: {self.api_key_env}")
        payload = {
            "model": self.model,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "messages": messages,
        }
        request = urllib.request.Request(
            f"{self.base_url.rstrip('/')}/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                body = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"Model provider HTTP {exc.code}: {detail}") from exc
        content = str(body["choices"][0]["message"].get("content") or "")
        return ChatCompletionResult(
            content=content,
            usage=normalize_token_usage(body.get("usage"), model=self.model, source="provider_usage"),
            raw_response=body,
        )


def normalize_token_usage(
    usage: Any,
    *,
    model: str | None = None,
    component: str | None = None,
    source: str = "provider_usage",
) -> dict[str, Any]:
    raw_usage = dict(usage or {}) if isinstance(usage, dict) else {}

    prompt_tokens = _int_or_none(
        raw_usage.get("prompt_tokens")
        or raw_usage.get("input_tokens")
        or raw_usage.get("promptTokens")
        or raw_usage.get("inputTokens")
    )
    completion_tokens = _int_or_none(
        raw_usage.get("completion_tokens")
        or raw_usage.get("output_tokens")
        or raw_usage.get("completionTokens")
        or raw_usage.get("outputTokens")
    )
    total_tokens = _int_or_none(raw_usage.get("total_tokens") or raw_usage.get("totalTokens"))
    if total_tokens is None and (prompt_tokens is not None or completion_tokens is not None):
        total_tokens = int(prompt_tokens or 0) + int(completion_tokens or 0)

    normalized: dict[str, Any] = {
        "usage_source": source,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": total_tokens,
    }
    if model:
        normalized["model"] = model
    if component:
        normalized["component"] = component
    if raw_usage:
        normalized["raw_usage"] = raw_usage
    return normalized


def estimate_token_count(text: str) -> int:
    ascii_chars = 0
    cjk_chars = 0
    other_chars = 0
    for char in text:
        codepoint = ord(char)
        if (
            0x4E00 <= codepoint <= 0x9FFF
            or 0x3400 <= codepoint <= 0x4DBF
            or 0x3040 <= codepoint <= 0x30FF
            or 0xAC00 <= codepoint <= 0xD7AF
        ):
            cjk_chars += 1
        elif codepoint < 128:
            ascii_chars += 1
        else:
            other_chars += 1
    return max(1, int(round(ascii_chars / 4 + cjk_chars * 1.2 + other_chars / 2)))


def estimate_messages_tokens(messages: list[dict[str, Any]]) -> int:
    total = 0
    for message in messages:
        total += 4
        total += estimate_token_count(str(message.get("role") or ""))
        total += estimate_token_count(str(message.get("content") or ""))
    return total + 2


def estimated_usage(
    *,
    prompt_text: str = "",
    completion_text: str = "",
    messages: list[dict[str, Any]] | None = None,
    model: str | None = None,
    component: str | None = None,
    source: str = "estimated_usage",
    note: str | None = None,
) -> dict[str, Any]:
    prompt_tokens = estimate_messages_tokens(messages) if messages is not None else estimate_token_count(prompt_text)
    completion_tokens = estimate_token_count(completion_text) if completion_text else 0
    usage = {
        "usage_source": source,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": prompt_tokens + completion_tokens,
    }
    if model:
        usage["model"] = model
    if component:
        usage["component"] = component
    if note:
        usage["note"] = note
    return usage


def _int_or_none(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
