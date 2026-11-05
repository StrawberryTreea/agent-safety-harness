"""Shared registered functions. Intake/build/capture/score call the native engine.

analyze and evolve remain owned by their respective integration tracks.
"""

from __future__ import annotations

from ..registry import REGISTRY
from .. import engine


@REGISTRY.register_fn(
    "bench_intake",
    "从源（GitHub @ 固定 revision）拉取 benchmark 并登记为规范数据集。",
    input_schema={"type": "object", "required": ["benchmark_id", "out"], "additionalProperties": False, "properties": {
        "benchmark_id": {"type": "string"}, "revision": {"type": ["string", "null"]},
        "source_root": {"type": ["string", "null"]}, "out": {"type": "string"},
        "allow_fetch": {"type": "boolean"}, "intake_config": {"type": ["string", "null"]}}},
    arg_doc={"benchmark_id": "已登记的 benchmark", "revision": "配置中批准的完整40位revision，省略时使用该固定值",
             "out": "全新来源收据目录", "source_root": "已有固定版本源码目录，可选",
             "allow_fetch": "显式允许下载缺失来源，默认false", "intake_config": "原生source_intake映射配置，可选"},
    tags=["intake"],
)
def bench_intake(benchmark_id: str, revision: str | None = None, *, source_root: str | None = None,
                 out: str, allow_fetch: bool = False, intake_config: str | None = None) -> dict:
    return engine.bench_intake(benchmark_id, revision, source_root=source_root, out=out,
                               allow_fetch=allow_fetch, intake_config=intake_config)


@REGISTRY.register_fn(
    "build_sandbox",
    "读取已批准的环境声明，构建并实测隔离 runtime，供原生捕获和私有 ResultSpec 评分复用。",
    input_schema={"type": "object", "required": ["benchmark_id", "intake", "out"], "additionalProperties": False,
                  "properties": {"benchmark_id": {"type": "string"}, "intake": {"type": "string"}, "out": {"type": "string"}}},
    arg_doc={"benchmark_id": "已登记的benchmark", "intake": "intake.json收据文件", "out": "全新隔离环境收据目录"},
    tags=["sandbox"],
)
def build_sandbox(benchmark_id: str, *, intake: str, out: str) -> dict:
    return engine.build_sandbox(benchmark_id, intake=intake, out=out)


@REGISTRY.register_fn(
    "run_capture",
    "公开执行阶段：在沙箱里跑被测对象，只产轨迹，不泄露私有评分规则。",
    input_schema={"type": "object", "required": ["benchmark_id", "runtime", "tasks", "out", "agent"], "additionalProperties": False,
                  "properties": {"benchmark_id": {"type": "string"}, "runtime": {"type": "string"}, "tasks": {"type": "string"},
                                 "out": {"type": "string"}, "agent": {"type": "string"}, "agents_config": {"type": ["string", "null"]}}},
    arg_doc={"benchmark_id": "已登记的benchmark", "runtime": "runtime.json文件", "tasks": "仅公开任务JSONL",
             "out": "全新捕获目录", "agent": "目标Agent标识", "agents_config": "公开Agent配置文件，可选"},
    tags=["eval"],
)
def run_capture(benchmark_id: str, *, runtime: str, tasks: str, out: str, agent: str, agents_config: str | None = None) -> dict:
    return engine.run_capture(benchmark_id, runtime=runtime, tasks=tasks, out=out, agent=agent, agents_config=agents_config)


@REGISTRY.register_fn(
    "score",
    "私有评分阶段：注入隔离的评分规则，产出统一 ResultSpec（safety_pass 等）。",
    input_schema={"type": "object", "required": ["benchmark_id", "capture_receipt", "private_tasks", "out"], "additionalProperties": False,
                  "properties": {"benchmark_id": {"type": "string"}, "capture_receipt": {"type": "string"},
                                 "private_tasks": {"type": "string"}, "out": {"type": "string"}}},
    arg_doc={"benchmark_id": "已登记的benchmark", "capture_receipt": "capture_receipt.json文件",
             "private_tasks": "私有评分任务JSONL，仅score消费", "out": "全新评分目录"},
    tags=["eval"],
)
def score(benchmark_id: str, *, capture_receipt: str, private_tasks: str, out: str) -> dict:
    return engine.score(benchmark_id, capture_receipt=capture_receipt, private_tasks=private_tasks, out=out)


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
