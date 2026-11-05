"""原始任务描述（原文保留，当前实现见下方修改说明）

executor · 顺序/条件执行 plan（伦锋 负责）

直接调 ToolRegistry.call（Python 函数调用，不是 subprocess）。
默认行为安全：出错不中断整条链路（记录 error 继续），便于自进化长链路稳健运行。
提供 dry_run 模式：只打印会调什么，不动真引擎——这是 driver 的默认安全姿势。

本次修改与新增功能：
    - 保留直接调用 ToolRegistry.call 的方式，不通过 shell 执行计划。
    - 新增整份计划预检、依赖检查、参数引用解析和产物存在性检查。
    - 原“出错后继续”的策略已改为默认遇错停止，依赖失败或跳过的步骤不执行。
    - dry-run 默认开启，返回 planned，不把未执行的步骤记为 ok。
    - 区分原生业务状态与编排状态，按步骤 ID 保存结果并持久化执行记录。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..registry import REGISTRY, ToolRegistry
from .artifacts import (
    checked_artifacts,
    prepare_record_dir,
    public_output,
    save_execution,
)
from .planner import Plan
from .validation import (
    resolve_arguments,
    resolve_reference,
    validate_arguments,
    validate_plan,
)

SUCCESS = {
    "bench_intake": {"source_registered"},
    "build_sandbox": {"runtime_ready"},
    "run_capture": {"captured"},
    "score": {"scored"},
    "evolve": {"pending_review", "no_failures"},
}
INCOMPLETE = {"run_capture": {"capture_invalid"}, "score": {"scored_with_unknown"}}


@dataclass
class StepResult:
    fn: str
    status: str  # "ok" | "skipped" | "error"
    # 本次新增 planned（尚未执行）和 incomplete（业务未完整完成）两种步骤状态。
    output: Any = None
    error: str | None = None
    id: str = ""
    artifacts: dict[str, str] = field(default_factory=dict)


@dataclass
class ExecutionResult:
    results: list[StepResult] = field(default_factory=list)
    context: dict[str, Any] = field(default_factory=dict)
    status: str = "running"


def execute_plan(
    plan: Plan,
    *,
    dry_run: bool = True,
    registry: ToolRegistry = REGISTRY,
    allowed=None,
    record_dir: str | Path | None = None,
) -> ExecutionResult:
    # 在调用函数或创建执行记录前，先校验整份计划，避免执行到中途才发现计划错误。
    plan = validate_plan(plan, registry=registry, allowed=allowed)
    result = ExecutionResult(status="planned" if dry_run else "running")
    if dry_run:
        for step in plan.steps:
            message = (
                "条件待执行时判断"
                if step.condition
                else "[dry-run] 计划校验通过，尚未执行"
            )
            result.results.append(
                StepResult(step.fn, "planned", {"detail": message}, id=step.id)
            )
        return result

    record_root = Path(record_dir).expanduser().resolve() if record_dir else None
    # 执行记录与步骤输出目录必须分开；引擎要求输出目录为空。
    if record_root:
        for step in plan.steps:
            out = step.args.get("out")
            if isinstance(out, str):
                destination = Path(out).expanduser().resolve()
                if (
                    destination == record_root
                    or destination in record_root.parents
                    or record_root in destination.parents
                ):
                    raise ValueError("执行记录目录不能与步骤输出目录重叠")
        record_root = prepare_record_dir(record_root)
        save_execution(record_root, result)
    states: dict[str, str] = {}
    outputs: list[Path] = []
    halted = False
    for step in plan.steps:
        item = StepResult(step.fn, "skipped", id=step.id)
        if halted:
            item.error = "前序步骤失败或未完整完成，已停止后续执行"
        elif any(states[dependency] != "ok" for dependency in step.depends_on):
            item.error = "依赖步骤未成功完成"
        else:
            try:
                # 原条件判断任务：极小条件 DSL：看上一步的输出里某个字段是否等于期望值。
                # 本次修改：使用统一的前序步骤引用，字段不存在时明确失败，不默认放行。
                if (
                    step.condition
                    and resolve_reference(step.condition, result.context)
                    != step.condition["equals"]
                ):
                    item.error = "条件未满足"
                else:
                    args = resolve_arguments(step.args, result.context)
                    validate_arguments(step.fn, args, registry)
                    destination = Path(args["out"]).expanduser().resolve()
                    if (
                        any(
                            destination == other
                            or destination in other.parents
                            or other in destination.parents
                            for other in outputs
                        )
                        or record_root
                        and (
                            destination == record_root
                            or destination in record_root.parents
                            or record_root in destination.parents
                        )
                    ):
                        raise ValueError("解析后的输出目录存在重叠")
                    if destination.exists() and (
                        not destination.is_dir() or any(destination.iterdir())
                    ):
                        raise FileExistsError("步骤输出目录必须是新目录或空目录")
                    outputs.append(destination)
                    native = registry.call(step.fn, **args)
                    if not isinstance(native, dict):
                        raise ValueError("注册函数必须返回包含业务状态的对象")
                    status = native.get("status")
                    item.output = public_output(native)
                    if status not in SUCCESS[step.fn] | INCOMPLETE.get(step.fn, set()):
                        raise ValueError("无法识别业务状态，或函数仍返回占位结果")
                    item.artifacts = checked_artifacts(step.fn, args, native)
                    item.status = "ok" if status in SUCCESS[step.fn] else "incomplete"
                    result.context[step.id] = {
                        "output": item.output,
                        "artifacts": item.artifacts,
                    }
                    if item.status == "incomplete":
                        result.status = "incomplete"
                        halted = True
            except Exception as exc:
                # 原执行策略说明（保留）：
                # 单步失败不拖垮整条链路
                # 本次修改：真实引擎步骤存在依赖，默认记录错误并停止后续执行。
                # 模型服务和解析器的异常文字可能包含私有输入。
                # 这里只记录异常类型，具体原因结合引擎原生证据排查。
                item.status = "error"
                item.error = type(exc).__name__
                result.status = "failed"
                halted = True
        result.results.append(item)
        states[step.id] = item.status
        if record_root:
            save_execution(record_root, result)
    if not halted:
        result.status = (
            "completed_with_skips"
            if any(item.status == "skipped" for item in result.results)
            else "completed"
        )
    if record_root:
        save_execution(record_root, result)
    return result
