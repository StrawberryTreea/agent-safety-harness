"""harness_driver 子包 · 自然语言驱动（harness 大脑）

定位（总体框架定死）：
    harness_driver 不重新实现任何评测引擎能力。它只做四件事：
        1) 理解意图（自然语言 → 结构化 plan）
        2) 选函数（从共享 ToolRegistry 的白名单里挑）
        3) 按序调用（executor 直接调 Python 函数）
        4) 看返回决定下一步（条件分支）
    “搭沙箱 / 跑评测 / 触发自进化”等所有“行动”都是 ToolRegistry 里的一个注册函数，
    driver 按 plan 自主串联即可。

分工：
    - 刘轲（引擎组）：registry.py + 各引擎函数（bench_intake / build_sandbox / run_capture / score / analyze / evolve_*）
    - 伦锋（自进化组）：本子包的 planner / executor / nl_bridge

导入本包注册四个真实引擎工具及 analyze/evolve 两个待接入工具。
真实调用按各工具的 input_schema 提供参数；关键词 dry_run 仍是编排演示。
"""

from __future__ import annotations

from ..registry import REGISTRY, FunctionSpec, ToolRegistry
from .planner import Plan, PlanStep, plan_from_instruction
from .executor import ExecutionResult, StepResult, execute_plan
from .nl_bridge import NLBridge

# 注册共享工具（四个真实入口；analyze/evolve 仍由对应负责人接入）。
from . import registrations  # noqa: F401,E402

__all__ = [
    "REGISTRY",
    "FunctionSpec",
    "ToolRegistry",
    "Plan",
    "PlanStep",
    "plan_from_instruction",
    "ExecutionResult",
    "StepResult",
    "execute_plan",
    "NLBridge",
]
