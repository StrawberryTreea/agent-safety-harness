from __future__ import annotations

from typing import Any

from ..schema import TaskSpec
from .base import SandboxAction, SandboxBackend, SandboxSession, SandboxStepResult, required_capabilities


class CompositeBackend(SandboxBackend):
    backend_id = "composite"

    def __init__(self, backends: list[SandboxBackend]):
        self.backends = list(backends)
        self.capabilities = set().union(*(backend.capabilities for backend in self.backends))

    def create(self, task: TaskSpec) -> SandboxSession:
        required = required_capabilities(task)
        sessions = [
            backend.create(task)
            for backend in self.backends
            if required & backend.capabilities
        ]
        return CompositeSession(f"composite:{task.task_id}", sessions)


class CompositeSession(SandboxSession):
    def __init__(self, session_id: str, sessions: list[SandboxSession]):
        self.session_id = session_id
        self.sessions = sessions
        self.workspace_path = next((session.workspace_path for session in sessions if session.workspace_path), None)
        self.events: list[dict[str, Any]] = []

    def reset(self) -> None:
        self.events.clear()
        for session in self.sessions:
            session.reset()

    def inject_task(self, task: TaskSpec) -> None:
        for session in self.sessions:
            session.inject_task(task)

    def observe(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "backend": "composite",
            "sessions": [session.observe() for session in self.sessions],
        }

    def step(self, action: SandboxAction) -> SandboxStepResult:
        for session in self.sessions:
            result = session.step(action)
            if result.ok or result.error not in _UNSUPPORTED_ERRORS:
                self.events.append(
                    {
                        "event_type": "delegated_action",
                        "session_id": session.session_id,
                        "action_type": action.action_type,
                        "target": action.target,
                        "policy_verdict": result.policy_verdict,
                        "ok": result.ok,
                        "error": result.error,
                    }
                )
                return result
        return SandboxStepResult(
            ok=False,
            policy_verdict="fail",
            error=f"no composite sandbox session handled action_type: {action.action_type}",
        )

    def state_diff(self) -> dict[str, Any]:
        return {
            session.session_id: session.state_diff()
            for session in self.sessions
        }

    def artifacts(self) -> dict[str, Any]:
        return {
            "backend": "composite",
            "events": list(self.events),
            "sessions": {
                session.session_id: session.artifacts()
                for session in self.sessions
            },
            "state_diff": self.state_diff(),
        }

    def close(self) -> None:
        for session in self.sessions:
            session.close()
        self.events.append({"event_type": "session_closed", "backend": "composite"})


_UNSUPPORTED_ERRORS = {
    "unsupported action_type: tool_call",
    "unsupported action_type: file_write",
    "unsupported action_type: file_read",
    "unsupported action_type: list_files",
}
