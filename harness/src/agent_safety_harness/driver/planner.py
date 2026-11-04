"""planner · 把指令拆成有序函数调用 plan（伦锋 负责）

两种来源：
    1) 若配置了 harness_driver LLM 角色（llm_roles.json），走 nl_bridge 用 LLM 生成 plan；
    2) 否则走关键词兜底规划，保证没有 LLM 也能跑通（便于测试 / 离线）。
无论哪种，最终 plan 里的 fn 必须都在 ToolRegistry 白名单内——driver 不能调未注册动作。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from ..registry import REGISTRY, ToolRegistry


@dataclass
class PlanStep:
    fn: str
    args: dict[str, Any] = field(default_factory=dict)
    note: Optional[str] = None
    # 简单条件 DSL：{"if_output_of": <上一步索引>, "key": <输出字段>, "equals": <值>}
    # 仅当该条件满足时才执行此步（用于“若某 benchmark 无本地源则先 intake”这类分支）。
    condition: Optional[dict[str, Any]] = None


@dataclass
class Plan:
    steps: list[PlanStep]
    rationale: str = ""


# 关键词 → 函数名（无 LLM 时的兜底）。命中顺序即执行顺序。
_KEYWORD_MAP: list[tuple[tuple[str, ...], str]] = [
    (("拉", "复现", "下载", "clone", "reproduce", "intake", "fetch", "获取"), "bench_intake"),
    (("沙箱", "环境", "构建", "build", "sandbox", "env", "隔离"), "build_sandbox"),
    (("跑", "执行", "评测", "capture", "run", "eval"), "run_capture"),
    (("评分", "打分", "score", "判分"), "score"),
    (("分析", "聚合", "analyze", "归因"), "analyze"),
    (("进化", "自进化", "evolve", "优化", "improve", "提升"), "evolve"),
]


def plan_from_instruction(
    text: str,
    *,
    client: Optional[NLBridge] = None,
    registry: ToolRegistry = REGISTRY,
) -> Plan:
    """自然语言/半结构化指令 → 有序执行 plan。"""
    if client is not None:
        try:
            return client.to_plan(text, registry=registry)
        except Exception as exc:  # 退化到关键词规划，不让单次 LLM 失败卡死整条链路
            print(f"[planner] NL bridge failed ({exc}); falling back to keyword planner")
    return _keyword_plan(text, registry=registry)


def _keyword_plan(text: str, *, registry: ToolRegistry) -> Plan:
    lower = text.lower()
    steps: list[PlanStep] = []
    seen: set[str] = set()
    for kws, fn in _KEYWORD_MAP:
        if any(k in lower for k in kws) and fn not in seen and fn in registry.names():
            seen.add(fn)
            steps.append(PlanStep(fn=fn, args={"instruction": text}, note=f"关键词命中 {fn}"))
    if not steps:
        steps.append(PlanStep(fn="help", args={}, note="未识别到任何意图"))
    return Plan(steps=steps, rationale="关键词兜底（未配置 harness_driver LLM）")
