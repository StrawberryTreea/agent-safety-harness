from __future__ import annotations

from typing import Any

from ..schema import TaskSpec
from .base import BenchmarkAdapter, BenchmarkRuntime
from .agent_safetybench import AgentSafetyBenchAdapter
from .executable import ExecutableTaskAdapter, is_executable
from .unified_scenario import UnifiedScenarioAdapter, is_unified_scenario


_UNIFIED_ADAPTER = UnifiedScenarioAdapter()
_EXECUTABLE_ADAPTER = ExecutableTaskAdapter()
_ADAPTERS: tuple[BenchmarkAdapter, ...] = (AgentSafetyBenchAdapter(),)

OFFICIAL_EXECUTION_BENCHMARKS = frozenset(
    {
        "agent_safetybench",
        "agentdojo",
        "agentharm",
        "agenthazard",
        "agentlab",
        "agentleak",
        "cuaharm",
        "injecagent",
        "mcptox",
        "msb",
        "openagentsafety",
        "ras_eval",
        "saber",
        "st_webagentbench",
        "toolemu",
        "wasp",
    }
)


def adapter_for_task(task: TaskSpec) -> BenchmarkAdapter | None:
    if is_executable(task):
        return _EXECUTABLE_ADAPTER
    if is_unified_scenario(task):
        return _UNIFIED_ADAPTER
    benchmark = task.benchmark.casefold()
    return next((adapter for adapter in _ADAPTERS if benchmark in adapter.benchmark_ids), None)


def create_runtime(task: TaskSpec) -> BenchmarkRuntime | None:
    adapter = adapter_for_task(task)
    return adapter.create_runtime(task) if adapter else None


def prompt_for_task(task: TaskSpec) -> str | None:
    adapter = adapter_for_task(task)
    return adapter.prompt_for_task(task) if adapter else None


def unsupported_official_benchmarks(tasks: list[TaskSpec]) -> list[str]:
    unsupported = {
        task.benchmark.casefold()
        for task in tasks
        if task.benchmark.casefold() in OFFICIAL_EXECUTION_BENCHMARKS and adapter_for_task(task) is None
    }
    return sorted(unsupported)
