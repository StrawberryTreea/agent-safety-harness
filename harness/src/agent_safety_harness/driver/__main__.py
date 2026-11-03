"""driver 子包演示入口：python -m agent_safety_harness.driver

展示“自然语言/关键词指令 → plan → 执行（dry_run）”整条链路，不依赖真实引擎。
真实接入后，把 dry_run=True 改成 False 即跑真引擎。
"""

from __future__ import annotations

from .planner import plan_from_instruction
from .executor import execute_plan


def main() -> None:
    instruction = "复现 AgentBench 并构建隔离环境跑一轮，然后分析"
    print(f"指令：{instruction}\n")
    plan = plan_from_instruction(instruction)
    print("规划步骤：")
    for i, step in enumerate(plan.steps):
        print(f"  {i}. {step.fn}({step.args})  # {step.note}")
    print("\n执行（dry_run）：")
    result = execute_plan(plan, dry_run=True)
    for r in result.results:
        print(f"  [{r.status}] {r.fn}: {r.output}")


if __name__ == "__main__":
    main()
