from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..schema import TaskSpec


class SandboxUnsupported(RuntimeError):
    pass


class SandboxPolicyError(RuntimeError):
    pass


@dataclass
class SandboxAction:
    action_type: str
    target: str | None = None
    args: dict[str, Any] = field(default_factory=dict)
    risk: str = "low"


@dataclass
class SandboxStepResult:
    ok: bool
    observation: dict[str, Any] = field(default_factory=dict)
    policy_verdict: str = "pass"
    error: str | None = None


class SandboxSession(ABC):
    session_id: str
    workspace_path: Path | None = None

    @abstractmethod
    def reset(self) -> None:
        raise NotImplementedError

    @abstractmethod
    def inject_task(self, task: TaskSpec) -> None:
        raise NotImplementedError

    @abstractmethod
    def observe(self) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    def step(self, action: SandboxAction) -> SandboxStepResult:
        raise NotImplementedError

    @abstractmethod
    def state_diff(self) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    def artifacts(self) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    def close(self) -> None:
        raise NotImplementedError


class SandboxBackend(ABC):
    backend_id: str
    capabilities: set[str]

    def can_run(self, task: TaskSpec) -> bool:
        required = required_capabilities(task)
        return required.issubset(self.capabilities)

    @abstractmethod
    def create(self, task: TaskSpec) -> SandboxSession:
        raise NotImplementedError


def required_capabilities(task: TaskSpec) -> set[str]:
    sandbox = task.metadata.get("sandbox") if isinstance(task.metadata, dict) else None
    if isinstance(sandbox, dict):
        capabilities = sandbox.get("capabilities")
        if isinstance(capabilities, list):
            return {str(item) for item in capabilities}
    required: set[str]
    if task.profile in {"coding_workspace", "local_file_workspace"}:
        required = {"workspace", "filesystem_ephemeral", "state_diff"}
    elif task.tools or task.profile in {
        "prompt_injection_tool_env",
        "tool_use_emulated_sandbox",
        "general_tool_agent_safety",
        "multi_agent_privacy_trace",
        "harmful_agent_task",
    }:
        required = {"tools", "tool_call", "state_diff"}
    else:
        required = {"text", "prompt"}
    if task.tools and "tools" not in required and required != {"text", "prompt"}:
        required |= {"tools", "tool_call"}
    return required
