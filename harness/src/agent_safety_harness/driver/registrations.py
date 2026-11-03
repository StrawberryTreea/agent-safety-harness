"""占位工具注册（scaffold 用）· 成员1/4 用真实引擎替换

本文件把六个核心能力注册成 ToolRegistry 工具，但实现是占位（明确标 TODO）。
目的：在真实 adapter/evaluation/sandbox/evolution 落地前，整条链路（CLI / driver）
就能以 dry_run 跑通、做端到端冒烟。成员1 负责把 bench_intake / build_sandbox /
run_capture / score / analyze 换成真实调用；成员4 负责 evolve_*。

注册方式即未来真实函数的模板：用 REGISTRY.register_fn 装饰器 + 写清 input_schema /
arg_doc（driver 和 LLM 都靠这些决定怎么调）。
"""

from __future__ import annotations

from ..registry import REGISTRY


@REGISTRY.register_fn(
    "bench_intake",
    "从源（GitHub @ 固定 revision）拉取 benchmark 并登记为规范数据集。",
    input_schema={"properties": {"benchmark_id": {"type": "string"}, "revision": {"type": "string"}}},
    arg_doc={"benchmark_id": "要复现哪个 benchmark", "revision": "锁定的 git revision"},
    tags=["intake"],
)
def bench_intake(benchmark_id: str, revision: str = "auto") -> dict:
    # TODO(member1): 接 scripts/source_intake.py + GitHub 拉取器（按固定 revision + 哈希校验）
    return {"benchmark_id": benchmark_id, "revision": revision, "status": "intake_pending"}


@REGISTRY.register_fn(
    "build_sandbox",
    "读取 benchmark 的环境需求声明，自动构建隔离 runtime，并把官方 scorer 包成统一 ResultSpec。",
    input_schema={"properties": {"benchmark_id": {"type": "string"}}},
    arg_doc={"benchmark_id": "要为哪个 benchmark 构建隔离环境"},
    tags=["sandbox"],
)
def build_sandbox(benchmark_id: str) -> dict:
    # TODO(member1): 接 sandbox/build.py，替代现在手动设 AGENTDOJO_PYTHON / AGENTDYN_PYTHON
    return {"benchmark_id": benchmark_id, "runtime": "venv://placeholder", "needs_build": True}


@REGISTRY.register_fn(
    "run_capture",
    "公开执行阶段：在沙箱里跑被测对象，只产轨迹，不泄露私有评分规则。",
    input_schema={"properties": {"benchmark_id": {"type": "string"}}},
    arg_doc={"benchmark_id": "要执行哪个 benchmark"},
    tags=["eval"],
)
def run_capture(benchmark_id: str) -> dict:
    # TODO(member1/3): 接 harness/ash.py capture + llm_eval 执行
    return {"benchmark_id": benchmark_id, "traces": "trace://placeholder"}


@REGISTRY.register_fn(
    "score",
    "私有评分阶段：注入隔离的评分规则，产出统一 ResultSpec（safety_pass 等）。",
    input_schema={"properties": {"benchmark_id": {"type": "string"}}},
    arg_doc={"benchmark_id": "要评分哪个 benchmark"},
    tags=["eval"],
)
def score(benchmark_id: str) -> dict:
    # TODO(member1): 接 harness/ash.py score
    return {"benchmark_id": benchmark_id, "safety_pass": None}


@REGISTRY.register_fn(
    "analyze",
    "四轨聚合分析，产出失败观察（自进化的燃料）。",
    input_schema={"properties": {"benchmark_id": {"type": "string"}}},
    arg_doc={"benchmark_id": "要分析哪个 benchmark"},
    tags=["analysis"],
)
def analyze(benchmark_id: str) -> dict:
    # TODO(member3): 接 scripts/result_analysis.py
    return {"benchmark_id": benchmark_id, "failures": "failures://placeholder"}


@REGISTRY.register_fn(
    "evolve",
    "触发自进化：失败观察 → Loop-A 出新测试集 / Loop-B 优化方法。",
    input_schema={"properties": {"benchmark_id": {"type": "string"}, "loop": {"type": "string"}}},
    arg_doc={"benchmark_id": "针对哪个 benchmark", "loop": "loop-a 或 loop-b"},
    tags=["evolution"],
)
def evolve(benchmark_id: str, loop: str = "loop-a") -> dict:
    # TODO(member4): 接 evolution/（Loop-A 合入 + Loop-B 模型生成式优化）
    return {"benchmark_id": benchmark_id, "loop": loop, "status": "evolve_pending"}
