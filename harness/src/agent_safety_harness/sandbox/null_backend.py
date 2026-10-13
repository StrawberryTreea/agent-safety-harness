from __future__ import annotations

from typing import Any

from ..schema import TaskSpec
from .base import SandboxAction, SandboxBackend, SandboxSession, SandboxStepResult


class NullBackend(SandboxBackend):
    backend_id = "null"
    capabilities = {"text", "prompt"}

    def create(self, task: TaskSpec) -> SandboxSession:
        session = NullSession(f"null:{task.task_id}")
        session.inject_task(task)
        return session


class NullSession(SandboxSession):
    def __init__(self, session_id: str):
        self.session_id = session_id
        self.task: TaskSpec | None = None
        self.events: list[dict[str, Any]] = []

    def reset(self) -> None:
        self.events.clear()

    def inject_task(self, task: TaskSpec) -> None:
        self.task = task
        self.events.append({"event_type": "task_injected", "task_id": task.task_id})

    def observe(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "backend": "null",
            "user_goal": self.task.user_goal if self.task else None,
        }

    def step(self, action: SandboxAction) -> SandboxStepResult:
        event = {
            "event_type": "action_recorded",
            "action_type": action.action_type,
            "target": action.target,
            "args": action.args,
            "risk": action.risk,
        }
        self.events.append(event)
        return SandboxStepResult(ok=True, observation=event)

    def state_diff(self) -> dict[str, Any]:
        return {"added": [], "modified": [], "deleted": []}

    def artifacts(self) -> dict[str, Any]:
        return {"events": list(self.events)}

    def close(self) -> None:
        self.events.append({"event_type": "session_closed"})
