"""原始任务描述（原文保留，当前实现见下方修改说明）

planner · 把指令拆成有序函数调用 plan（伦锋 负责）

两种来源：
    1) 若配置了 harness_driver LLM 角色（llm_roles.json），走 nl_bridge 用 LLM 生成 plan；
    2) 否则走关键词兜底规划，保证没有 LLM 也能跑通（便于测试 / 离线）。
无论哪种，最终 plan 里的 fn 必须都在 ToolRegistry 白名单内——driver 不能调未注册动作。

本次修改与新增功能：
    - 保留关键词和 LLM 两种规划来源；默认关键词模式，LLM 需显式选择。
    - 原“LLM 失败后兜底”策略已取消，失败时明确报错，不切换为另一份计划。
    - 新增稳定步骤 ID、显式依赖和本地资源绑定，自动连接前序步骤产物。
    - 移除 instruction 占位传参和未注册 help 步骤，所有计划经过统一校验。
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, TYPE_CHECKING

from ..registry import REGISTRY, ToolRegistry

if TYPE_CHECKING:
    from .nl_bridge import NLBridge


@dataclass
class PlanStep:
    # 保留原有位置参数构造方式（fn、args、note、condition）。
    fn: str
    args: dict[str, Any] = field(default_factory=dict)
    note: str | None = None
    # 简单条件 DSL：{"if_output_of": <上一步索引>, "key": <输出字段>, "equals": <值>}
    # 仅当该条件满足时才执行此步（用于“若某 benchmark 无本地源则先 intake”这类分支）。
    # 本次修改：旧索引条件必须指向有效前序步骤，并转换为统一的步骤 ID / 字段引用。
    condition: dict[str, Any] | None = None
    id: str | None = None
    depends_on: list[str] = field(default_factory=list)


@dataclass
class Plan:
    steps: list[PlanStep]
    rationale: str = ""
    schema_version: str = "driver_plan_v1"


# 关键词 → 函数名（无 LLM 时的兜底）。命中顺序即执行顺序。
# 本次修改：关键词是默认规划方式，不再作为 LLM 调用失败后的自动降级路径。
KEYWORDS = (
    (("拉取", "复现", "下载", "intake", "fetch"), "bench_intake"),
    (("沙箱", "环境", "构建", "build", "sandbox"), "build_sandbox"),
    (("捕获", "执行", "评测", "capture", "run"), "run_capture"),
    (("评分", "打分", "score", "判分"), "score"),
    (("分析", "聚合", "analyze"), "analyze"),
    (("进化", "存档", "evolve", "优化"), "evolve"),
)
UPSTREAM_INPUTS = {
    "build_sandbox": ("intake", "bench_intake", "intake"),
    "run_capture": ("runtime", "build_sandbox", "runtime"),
    "score": ("capture_receipt", "run_capture", "capture_receipt"),
}


def bind_resources(plan: Plan, config: dict[str, Any]) -> Plan:
    """指令只决定步骤，不能自行指定路径、凭证或下载权限。"""
    from .validation import validate_config

    validate_config(config)
    if not config.get("run_root"):
        raise ValueError("指令模式必须提供 config.run_root")
    bound = deepcopy(plan)
    previous: dict[str, str] = {}
    root = Path(config["run_root"]).expanduser().resolve()
    for index, step in enumerate(bound.steps):
        if step.args:
            raise ValueError("意图计划不能自行提供资源参数")
        step.id = step.id or f"step-{index + 1}"
        step.args = deepcopy(config.get("arguments", {}).get(step.fn, {}))
        # 按步骤分开输出目录，避免同一函数多次调用时相互覆盖。
        if "out" in step.args:
            raise ValueError("指令模式的 out 由 run_root 和步骤 ID 生成")
        step.args["out"] = str(root / step.id)
        upstream = UPSTREAM_INPUTS.get(step.fn)
        if upstream:
            argument, function, artifact = upstream
            if argument not in step.args and function in previous:
                source = previous[function]
                step.args[argument] = {
                    "$from": source,
                    "field": f"artifacts.{artifact}",
                }
                if source not in step.depends_on:
                    step.depends_on.append(source)
        previous[step.fn] = step.id
    return bound


def plan_from_instruction(
    text: str,
    *,
    config: dict[str, Any] | None = None,
    client: NLBridge | None = None,
    registry: ToolRegistry = REGISTRY,
) -> Plan:
    """自然语言/半结构化指令 → 有序执行 plan。

    本次修改：两种来源均需绑定本地资源并通过统一校验；模型失败不自动降级。
    """
    from .validation import allowed_functions, validate_config, validate_plan

    config = {} if config is None else config
    validate_config(config)
    allowed = allowed_functions(config)
    if not isinstance(text, str) or not text.strip():
        raise ValueError("指令必须是非空文本")
    if client is not None:
        # 原任务策略说明（保留）：
        # 退化到关键词规划，不让单次 LLM 失败卡死整条链路
        # 本次修改：取消隐式降级，保持用户明确选择的规划方式。
        # 模型计划被拒绝后必须明确失败，不能静默换成另一份可执行计划。
        intent = client.to_plan(text, registry=registry, allowed=allowed)
    else:
        steps = [
            PlanStep(fn=fn)
            for words, fn in KEYWORDS
            if any(word in text.lower() for word in words)
        ]
        if not steps:
            raise ValueError("未识别到任何支持的意图，请提供结构化计划")
        intent = Plan(steps=steps, rationale="关键词规划（资源参数由本地配置绑定）")
    return validate_plan(
        bind_resources(intent, config), registry=registry, allowed=allowed
    )
