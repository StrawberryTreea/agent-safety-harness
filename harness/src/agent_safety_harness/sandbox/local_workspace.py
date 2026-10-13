from __future__ import annotations

import hashlib
import json
import shutil
import tempfile
from pathlib import Path
from typing import Any

from ..schema import TaskSpec
from .base import SandboxAction, SandboxBackend, SandboxPolicyError, SandboxSession, SandboxStepResult


class LocalWorkspaceBackend(SandboxBackend):
    backend_id = "local_workspace"
    capabilities = {
        "workspace",
        "filesystem_ephemeral",
        "file_read",
        "file_write",
        "list_files",
        "state_diff",
    }

    def create(self, task: TaskSpec) -> SandboxSession:
        root = Path(tempfile.mkdtemp(prefix=f"ash_sandbox_{_safe_id(task.task_id)}_"))
        session = LocalWorkspaceSession(f"local_workspace:{task.task_id}", root)
        session.inject_task(task)
        return session


class LocalWorkspaceSession(SandboxSession):
    def __init__(self, session_id: str, workspace_path: Path):
        self.session_id = session_id
        self.workspace_path = workspace_path
        self.task: TaskSpec | None = None
        self.events: list[dict[str, Any]] = []
        self._before: dict[str, dict[str, Any]] = {}

    def reset(self) -> None:
        if self.workspace_path and self.workspace_path.exists():
            shutil.rmtree(self.workspace_path)
        self.workspace_path.mkdir(parents=True, exist_ok=True)
        self.events.clear()
        self._before = {}

    def inject_task(self, task: TaskSpec) -> None:
        self.task = task
        self.workspace_path.mkdir(parents=True, exist_ok=True)
        (self.workspace_path / "task.json").write_text(
            json.dumps(task.__dict__, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        (self.workspace_path / "prompt.txt").write_text(task.user_goal, encoding="utf-8")
        for file_spec in _initial_files(task.initial_state):
            path = self._resolve_inside(file_spec["path"])
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(str(file_spec.get("content", "")), encoding="utf-8")
        self._before = self._snapshot()
        self.events.append(
            {
                "event_type": "task_injected",
                "task_id": task.task_id,
                "workspace_path": str(self.workspace_path),
            }
        )

    def observe(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "backend": "local_workspace",
            "workspace_path": str(self.workspace_path),
            "files": sorted(self._snapshot()),
            "user_goal": self.task.user_goal if self.task else None,
        }

    def step(self, action: SandboxAction) -> SandboxStepResult:
        try:
            if action.action_type == "file_write":
                return self._write_file(action)
            if action.action_type == "file_read":
                return self._read_file(action)
            if action.action_type == "list_files":
                observation = {"files": sorted(self._snapshot())}
                self._record_action(action, observation)
                return SandboxStepResult(ok=True, observation=observation)
            if action.action_type == "terminal_command":
                return self._deny(action, "terminal_command is disabled until command allowlists are implemented")
            return self._deny(action, f"unsupported action_type: {action.action_type}")
        except SandboxPolicyError as exc:
            return self._deny(action, str(exc))

    def state_diff(self) -> dict[str, Any]:
        after = self._snapshot()
        before_paths = set(self._before)
        after_paths = set(after)
        added = sorted(after_paths - before_paths)
        deleted = sorted(before_paths - after_paths)
        modified = sorted(path for path in before_paths & after_paths if self._before[path]["sha256"] != after[path]["sha256"])
        return {
            "added": added,
            "modified": modified,
            "deleted": deleted,
            "before_count": len(before_paths),
            "after_count": len(after_paths),
        }

    def artifacts(self) -> dict[str, Any]:
        return {
            "workspace_path": str(self.workspace_path),
            "events": list(self.events),
            "state_diff": self.state_diff(),
        }

    def close(self) -> None:
        self.events.append({"event_type": "session_closed"})
        cleanup = False
        sandbox = self.task.metadata.get("sandbox") if self.task else None
        if isinstance(sandbox, dict):
            cleanup = bool(sandbox.get("cleanup_workspace", False))
        if cleanup and self.workspace_path.exists():
            shutil.rmtree(self.workspace_path)

    def _write_file(self, action: SandboxAction) -> SandboxStepResult:
        relative_path = str(action.args.get("path") or action.target or "")
        content = str(action.args.get("content", ""))
        path = self._resolve_inside(relative_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        observation = {"path": relative_path, "bytes": len(content.encode("utf-8"))}
        self._record_action(action, observation)
        return SandboxStepResult(ok=True, observation=observation)

    def _read_file(self, action: SandboxAction) -> SandboxStepResult:
        relative_path = str(action.args.get("path") or action.target or "")
        path = self._resolve_inside(relative_path)
        content = path.read_text(encoding="utf-8")
        observation = {"path": relative_path, "content": content}
        self._record_action(action, {"path": relative_path, "bytes": len(content.encode("utf-8"))})
        return SandboxStepResult(ok=True, observation=observation)

    def _deny(self, action: SandboxAction, reason: str) -> SandboxStepResult:
        event = {
            "event_type": "policy_denied",
            "action_type": action.action_type,
            "target": action.target,
            "reason": reason,
            "risk": action.risk,
        }
        self.events.append(event)
        return SandboxStepResult(ok=False, observation=event, policy_verdict="fail", error=reason)

    def _record_action(self, action: SandboxAction, observation: dict[str, Any]) -> None:
        self.events.append(
            {
                "event_type": "action",
                "action_type": action.action_type,
                "target": action.target,
                "args": action.args,
                "risk": action.risk,
                "observation": observation,
            }
        )

    def _resolve_inside(self, relative_path: str) -> Path:
        if not relative_path:
            raise SandboxPolicyError("path is required")
        root = self.workspace_path.resolve()
        target = (root / relative_path).resolve()
        if target != root and root not in target.parents:
            raise SandboxPolicyError(f"path escapes sandbox workspace: {relative_path}")
        return target

    def _snapshot(self) -> dict[str, dict[str, Any]]:
        snapshot: dict[str, dict[str, Any]] = {}
        for path in sorted(self.workspace_path.rglob("*")):
            if not path.is_file():
                continue
            relative = path.relative_to(self.workspace_path).as_posix()
            data = path.read_bytes()
            snapshot[relative] = {
                "size": len(data),
                "sha256": hashlib.sha256(data).hexdigest(),
            }
        return snapshot


def _initial_files(initial_state: dict[str, Any]) -> list[dict[str, Any]]:
    files = initial_state.get("files")
    if isinstance(files, dict):
        return [{"path": path, "content": content} for path, content in files.items()]
    if isinstance(files, list):
        return [dict(item) for item in files if isinstance(item, dict) and item.get("path")]
    return []


def _safe_id(task_id: str) -> str:
    return "".join(char if char.isalnum() else "_" for char in task_id)[:60]
