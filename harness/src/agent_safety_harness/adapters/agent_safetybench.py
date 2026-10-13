from __future__ import annotations

from copy import deepcopy
from functools import lru_cache
import importlib
import json
import os
from pathlib import Path
import re
import sys
from typing import Any

from ..evaluation import RULE_EVALUATOR_ID
from ..schema import TaskSpec
from .base import AdapterDescriptor, ToolCallOutcome


OFFICIAL_REVISION = "74feea8de601b3a1449a93fcf70017fe61556f73"
ADAPTER_ID = "agent_safetybench_official_env_v1"
_SAFE_ENV_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class AgentSafetyBenchRuntime:
    def __init__(self, task: TaskSpec):
        self.root = _find_official_root()
        self.record = _load_and_validate_record(self.root, task)
        self._envs: list[tuple[Any, set[str]]] = []
        self._tool_descs: list[dict[str, Any]] = []
        self._load_environments()
        self.descriptor = AdapterDescriptor(
            adapter_id=ADAPTER_ID,
            source_revision=OFFICIAL_REVISION,
            environment_validated=True,
            evaluator_id=RULE_EVALUATOR_ID,
            evaluator_available=True,
        )

    def _load_environments(self) -> None:
        env_dir_text = str(self.root / "environments")
        if env_dir_text not in sys.path:
            sys.path.insert(0, env_dir_text)
        for env_info in self.record.get("environments") or []:
            name = str(env_info.get("name") or "")
            if not name:
                continue
            if not _SAFE_ENV_NAME.fullmatch(name):
                raise ValueError(f"Invalid official environment name: {name!r}")
            env_class = getattr(importlib.import_module(name), name)
            env = env_class(parameters=deepcopy(env_info.get("parameters")))
            tool_names = [str(item) for item in env_info.get("tools") or []]
            self._envs.append((env, set(tool_names)))
            self._tool_descs.extend(deepcopy(env.get_tool_descs(tool_names)))

    def list_tools(self) -> list[dict[str, Any]]:
        return deepcopy(self._tool_descs)

    def call_tool(self, name: str, arguments: dict[str, Any]) -> ToolCallOutcome:
        for env, provided_tools in self._envs:
            if name in provided_tools and env.has_tool(name):
                observation = env.call_tool(name, deepcopy(arguments))
                ok = not (isinstance(observation, dict) and observation.get("success") is False)
                error = None if ok else str(observation.get("message") or "Official environment rejected the call")
                return ToolCallOutcome(observation=observation, ok=ok, error=error)
        observation = {"success": False, "message": f"Tool {name} doesn't exist."}
        return ToolCallOutcome(observation=observation, ok=False, error=observation["message"])

    def close(self) -> None:
        return None

    def state_diff(self) -> dict[str, Any]:
        return {"source": "official_agent_safetybench", "mutations": "owned_by_source_environment"}


class AgentSafetyBenchAdapter:
    benchmark_ids = frozenset({"agent_safetybench"})

    def create_runtime(self, task: TaskSpec) -> AgentSafetyBenchRuntime:
        return AgentSafetyBenchRuntime(task)

    def prompt_for_task(self, task: TaskSpec) -> str:
        record = _load_and_validate_record(_find_official_root(), task)
        return str(record["instruction"])


def _find_official_root() -> Path:
    candidates: list[Path] = []
    configured = os.environ.get("ASH_AGENT_SAFETYBENCH_ROOT")
    if configured:
        candidates.append(Path(configured))
    package_root = Path(__file__).resolve().parents[3]
    candidates.extend(
        [
            package_root / "official_benchmarks" / "agent_safetybench",
            package_root / "runs" / "official_repo_probe",
            Path.cwd() / "official_benchmarks" / "agent_safetybench",
        ]
    )
    for candidate in candidates:
        if (candidate / "data" / "released_data.json").is_file() and (candidate / "environments" / "BaseEnv.py").is_file():
            return candidate.resolve()
    raise FileNotFoundError("Pinned Agent-SafetyBench official source was not found. Set ASH_AGENT_SAFETYBENCH_ROOT.")


def _official_id(task: TaskSpec) -> int:
    raw = task.metadata.get("raw") if isinstance(task.metadata, dict) else None
    value = raw.get("id") if isinstance(raw, dict) else None
    if value is None:
        value = task.task_id.rsplit(":", 1)[-1]
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Cannot determine Agent-SafetyBench official id from {task.task_id!r}") from exc


def _load_and_validate_record(root: Path, task: TaskSpec) -> dict[str, Any]:
    official_id = _official_id(task)
    record = _records_by_id(str(root)).get(official_id)
    if record is None:
        raise KeyError(f"Agent-SafetyBench official id {official_id} was not found")
    if task.user_goal.strip() != str(record.get("instruction") or "").strip():
        raise ValueError(f"Task {task.task_id} instruction differs from the pinned official record")
    return deepcopy(record)


@lru_cache(maxsize=4)
def _records_by_id(root: str) -> dict[int, dict[str, Any]]:
    records = json.loads((Path(root) / "data" / "released_data.json").read_text(encoding="utf-8"))
    return {int(item["id"]): item for item in records}
