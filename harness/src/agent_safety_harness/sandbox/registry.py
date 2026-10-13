from __future__ import annotations

from ..schema import TaskSpec
from .base import SandboxBackend, SandboxUnsupported, required_capabilities
from .composite import CompositeBackend
from .local_workspace import LocalWorkspaceBackend
from .null_backend import NullBackend
from .tool_backend import ToolBackend


class SandboxRegistry:
    def __init__(self, backends: list[SandboxBackend] | None = None):
        self.backends = list(backends or [])

    def register(self, backend: SandboxBackend) -> None:
        self.backends.append(backend)

    def select(self, task: TaskSpec) -> SandboxBackend:
        required = required_capabilities(task)
        for backend in self.backends:
            if required.issubset(backend.capabilities):
                return backend
        available = {
            backend.backend_id: sorted(backend.capabilities)
            for backend in self.backends
        }
        raise SandboxUnsupported(
            f"No sandbox backend supports capabilities {sorted(required)}. Available: {available}"
        )


def default_registry() -> SandboxRegistry:
    local_workspace = LocalWorkspaceBackend()
    tool_mock = ToolBackend()
    return SandboxRegistry(
        [
            local_workspace,
            NullBackend(),
            tool_mock,
            CompositeBackend([local_workspace, tool_mock]),
        ]
    )
