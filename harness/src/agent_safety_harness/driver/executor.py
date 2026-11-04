"""executor · 顺序/条件执行 plan（伦锋 负责）

直接调 ToolRegistry.call（Python 函数调用，不是 subprocess）。
默认行为安全：出错不中断整条链路（记录 error 继续），便于自进化长链路稳健运行。
提供 dry_run 模式：只打印会调什么，不动真引擎——这是 driver 的默认安全姿势。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from ..registry import REGISTRY, ToolRegistry
from .planner import Plan, PlanStep


@dataclass
class StepResult:
    fn: str
    status: str  # "ok" | "skipped" | "error"
    output: Any = None
    error: Optional[str] = None


@dataclass
class ExecutionResult:
    results: list[StepResult] = field(default_factory=list)
    context: dict[str, Any] = field(default_factory=dict)


def execute_plan(plan: Plan, *, dry_run: bool = False, registry: ToolRegistry = REGISTRY) -> ExecutionResult:
    out = ExecutionResult()
    for idx, step in enumerate(plan.steps):
        if step.condition and not _eval_condition(step.condition, out.results):
            out.results.append(
                StepResult(fn=step.fn, status="skipped", error="条件未满足")
            )
            continue
        if dry_run:
            out.results.append(
                StepResult(
                    fn=step.fn,
                    status="ok",
                    output=f"[dry-run] 将调用 {step.fn}({step.args})",
                )
            )
            continue
        try:
            result = registry.call(step.fn, **step.args)
            out.results.append(StepResult(fn=step.fn, status="ok", output=result))
            out.context[step.fn] = result
        except Exception as exc:  # 单步失败不拖垮整条链路
            out.results.append(StepResult(fn=step.fn, status="error", error=str(exc)))
    return out


def _eval_condition(cond: dict[str, Any], results: list[StepResult]) -> bool:
    """极小条件 DSL：看上一步的输出里某个字段是否等于期望值。"""
    idx = cond.get("if_output_of")
    if isinstance(idx, int) and 0 <= idx < len(results):
        r = results[idx]
        if r.status != "ok" or not isinstance(r.output, dict):
            return False
        return r.output.get(cond.get("key")) == cond.get("equals")
    return True
