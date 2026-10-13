from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from ..schema import TaskSpec


@dataclass(frozen=True)
class AdapterDescriptor:
    adapter_id: str
    source_revision: str
    environment_validated: bool
    evaluator_id: str
    evaluator_available: bool
    validation_reason: str | None = None

    def to_event(self) -> dict[str, Any]:
        record: dict[str, Any] = {
            "environment_adapter": self.adapter_id,
            "environment_source_revision": self.source_revision,
            "environment_validated": self.environment_validated,
            "evaluator_adapter": self.evaluator_id,
            "evaluator_available": self.evaluator_available,
        }
        if self.validation_reason:
            record["environment_validation_reason"] = self.validation_reason
        return record


@dataclass
class ToolCallOutcome:
    observation: Any
    ok: bool = True
    error: str | None = None
    state_diff: dict[str, Any] | None = None


class BenchmarkRuntime(Protocol):
    descriptor: AdapterDescriptor

    def list_tools(self) -> list[dict[str, Any]]: ...

    def call_tool(self, name: str, arguments: dict[str, Any]) -> ToolCallOutcome: ...

    def state_diff(self) -> dict[str, Any]: ...

    def close(self) -> None: ...


class BenchmarkAdapter(Protocol):
    benchmark_ids: frozenset[str]

    def create_runtime(self, task: TaskSpec) -> BenchmarkRuntime: ...

    def prompt_for_task(self, task: TaskSpec) -> str: ...
