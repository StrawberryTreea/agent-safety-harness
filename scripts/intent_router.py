#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass
from typing import List


@dataclass
class RouteResult:
    request: str
    phases: List[str]
    rationale: List[str]
    follow_up_actions: List[str]


ROUTES = [
    {
        "keywords": ["新 benchmark", "new benchmark", "补 benchmark", "新增 benchmark"],
        "phases": ["phase1", "phase2"],
        "rationale": "Request implies source intake plus canonical standardization.",
        "follow_up_actions": ["If prompt or scorer shape changes, append phase3."],
    },
    {
        "keywords": ["白皮书数据集", "整理白皮书", "白皮书提到"],
        "phases": ["phase1"],
        "rationale": "Request centers on source intake or whitepaper evidence extraction.",
        "follow_up_actions": ["Append phase2 only if the source should enter the canonical dataset."],
    },
    {
        "keywords": ["字段映射", "删样", "重算统计", "修字段", "membership", "stats"],
        "phases": ["phase2"],
        "rationale": "Request changes canonical membership, mapping, or statistics.",
        "follow_up_actions": ["Revalidate canonical JSONL against v2_stats.json after changes."],
    },
    {
        "keywords": ["换一套指标", "更换指标", "metric", "主指标"],
        "phases": ["phase0", "phase3", "phase5", "phase8"],
        "rationale": "Metric changes require protocol locking before execution and reporting.",
        "follow_up_actions": ["Update metric_registry.yaml and comparability statement first."],
    },
    {
        "keywords": ["换一套风险维度", "taxonomy", "二级维度", "一级维度"],
        "phases": ["phase0", "phase2", "phase3", "phase8"],
        "rationale": "Taxonomy changes affect mapping, protocol, and downstream reporting.",
        "follow_up_actions": ["Update taxonomy_registry.yaml before rerunning stats or evaluation."],
    },
    {
        "keywords": ["smoke", "试跑", "100 条", "小样本测试", "self test"],
        "phases": ["phase3", "phase4"],
        "rationale": "Request asks for protocol shaping plus smoke validation.",
        "follow_up_actions": ["Do not escalate to phase5 until smoke checks pass."],
    },
    {
        "keywords": ["正式评测", "full run", "跑正式", "全量评测"],
        "phases": ["phase5"],
        "rationale": "Request asks for full evaluation execution.",
        "follow_up_actions": ["Require a fresh or recently valid phase4 when protocol changed."],
    },
    {
        "keywords": ["api error", "重跑 api", "增量重跑", "rerun", "delta"],
        "phases": ["phase6"],
        "rationale": "Request is a rerun or delta-only execution request.",
        "follow_up_actions": ["Do not overwrite legacy output during delta execution."],
    },
    {
        "keywords": ["leaderboard", "权威发布", "authoritative", "merge", "发布结果"],
        "phases": ["phase7", "phase8"],
        "rationale": "Request affects merged or authoritative results and usually downstream reporting.",
        "follow_up_actions": ["Run phase7 checks before generating external-facing summaries."],
    },
    {
        "keywords": ["生成白皮书", "白皮书", "快报", "报告", "report"],
        "phases": ["phase7", "phase8"],
        "rationale": "Report generation requires publish consistency before prose generation.",
        "follow_up_actions": ["Use authoritative results and manifest as the only numeric sources."],
    },
    {
        "keywords": ["agent 安全", "agent safety", "trajectory", "轨迹评测", "智能体", "agent 评测"],
        "phases": ["phase0", "phase9"],
        "rationale": "Request involves Agent execution, full-product evaluation, or offline trace judging.",
        "follow_up_actions": ["Choose the native track using pipeline.py inventory and plan."],
    },
    {
        "keywords": ["进化", "演化", "闭环", "失败分析", "failure analysis", "evolution", "候选测试"],
        "phases": ["phase10"],
        "rationale": "Request involves failure feedback and candidate-test planning.",
        "follow_up_actions": ["Preserve lineage and unknown outcomes; require evidence gates before dataset admission."],
    },
]


def configure_stdio() -> None:
    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        if stream is None:
            continue
        try:
            stream.reconfigure(encoding="utf-8")
        except AttributeError:
            pass


def route_request(request: str) -> RouteResult:
    text = request.lower()
    matched_phases: List[str] = []
    rationale: List[str] = []
    follow_up: List[str] = []

    for route in ROUTES:
        if any(keyword.lower() in text for keyword in route["keywords"]):
            for phase in route["phases"]:
                if phase not in matched_phases:
                    matched_phases.append(phase)
            rationale.append(route["rationale"])
            follow_up.extend(route["follow_up_actions"])

    if not matched_phases:
        matched_phases = ["phase0"]
        rationale.append("No strong routing signal matched. Default to protocol scoping.")
        follow_up.append("Clarify whether the request changes data, execution, publish state, or reporting state.")

    return RouteResult(
        request=request,
        phases=matched_phases,
        rationale=rationale,
        follow_up_actions=follow_up,
    )


def render_text(result: RouteResult) -> str:
    lines = [
        f"request: {result.request}",
        f"phases: {', '.join(result.phases)}",
        "rationale:",
    ]
    lines.extend(f"- {item}" for item in result.rationale)
    lines.append("follow_up_actions:")
    lines.extend(f"- {item}" for item in result.follow_up_actions)
    return "\n".join(lines)


def main() -> int:
    configure_stdio()
    parser = argparse.ArgumentParser(description="Route a repository safety-eval request into protocol phases.")
    parser.add_argument("--request", required=True, help="Natural-language user request.")
    parser.add_argument("--format", choices=["text", "json"], default="text")
    args = parser.parse_args()

    result = route_request(args.request)
    if args.format == "json":
        print(json.dumps(asdict(result), ensure_ascii=False, indent=2))
    else:
        print(render_text(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
