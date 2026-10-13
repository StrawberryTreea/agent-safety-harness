#!/usr/bin/env python3
"""Build a reproducible taxonomy and live-ASR snapshot report.

The script is read-only with respect to campaign artifacts. It classifies the
frozen current population and the expansion candidate index, then joins only
valid case-level results (success/failure) into category-level ASR summaries.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


TAXONOMY = {
    "1": ("输入与指令边界安全", {
        "1.1": "用户直接指令注入风险",
        "1.2": "外部内容指令注入风险",
        "1.3": "工具返回注入风险",
        "1.4": "工具描述注入风险",
        "1.5": "跨 Agent 与记忆注入风险",
    }),
    "2": ("工具调用与执行控制安全", {
        "2.1": "未授权工具调用风险",
        "2.2": "工具选择与参数劫持风险",
        "2.3": "多阶段工具链劫持风险",
        "2.4": "欺骗性反馈操纵风险",
        "2.5": "工具调用约束绕过风险",
    }),
    "3": ("数据访问与信息流安全", {
        "3.1": "敏感数据泄露风险",
        "3.2": "非必要数据访问风险",
        "3.3": "敏感数据外发风险",
        "3.4": "未授权信息获取风险",
        "3.5": "Agent 交互信息泄露风险",
    }),
    "4": ("状态变更与系统操作安全", {
        "4.1": "数据与文件篡改风险",
        "4.2": "远程控制与持久化风险",
        "4.3": "进程与服务可用性风险",
        "4.4": "外部业务副作用风险",
        "4.5": "物理与财务操作风险",
    }),
    "5": ("行为轨迹与结果安全", {
        "5.1": "最终响应泄露风险",
        "5.2": "工具参数传播风险",
        "5.3": "危险操作完成风险",
        "5.4": "多步骤攻击完成风险",
        "5.5": "攻击暴露与抵抗风险",
    }),
}

PRODUCTS = ("codex", "claude_code", "qoder")
PRODUCT_NAMES = {"codex": "Codex", "claude_code": "Claude Code", "qoder": "Qoder"}
BENCHMARK_ORDER = (
    "msb", "injecagent", "saber", "agentdojo", "agentleak",
    "agentpoison_strategyqa",
)
BENCHMARK_NAMES = {
    "msb": "MSB",
    "injecagent": "InjecAgent",
    "saber": "SABER",
    "agentdojo": "AgentDojo",
    "agentleak": "AgentLeak",
    "agentpoison_strategyqa": "AgentPoison-StrategyQA",
    "agentdyn": "AgentDyn",
    "ras_eval_native": "RAS-Eval native",
    "mcptox": "MCPTox",
    "openagentsafety": "OpenAgentSafety",
    "agentharm": "AgentHarm",
    "toolemu": "ToolEmu",
}

MSB_MAP = {
    "obtain_data_information": "3.4",
    "obtain_agent_interaction_data": "3.5",
    "modify_data": "4.1",
    "obtain_remote_control_authority": "4.2",
    "kill_process": "4.3",
}
MSB_FAILURE_SURFACE_MAP = {
    "prompt_injection": "1.2",
    "prompt_injection-false_error": "1.2",
    "prompt_injection-simulated_user": "1.2",
    "tool_transfer": "2.1",
    "name_overlap": "2.2",
    "out_of_scope_parameter": "2.2",
    "preference_manipulation-out_of_scope_parameter": "2.2",
    "false_error": "2.4",
    "search_term_deception": "2.4",
    "preference_manipulation": "2.5",
    "preference_manipulation-simulated_user": "2.5",
    "simulated_user": "2.5",
}
SABER_MAP = {
    "unauthorized_access": "3.4",
    "code_tampering": "4.1",
    "data_destruction": "4.1",
    "fs_destruction": "4.1",
    "info_leak": "3.1",
    "network_outbound": "3.3",
    "persistence": "4.2",
    "privilege_escalation": "4.2",
}
AGENTDOJO_MAP = {
    "banking": {0: "4.5", 1: "4.5", 2: "4.5", 3: "4.5", 4: "4.1", 5: "4.5", 6: "4.5", 7: "4.2", 8: "3.3"},
    "slack": {1: "4.4", 2: "3.3", 3: "4.4", 4: "3.3", 5: "4.2"},
    "travel": {0: "4.5", 1: "3.3", 2: "4.4", 3: "3.3", 4: "4.5", 5: "3.3", 6: "4.4"},
    "workspace": {0: "4.4", 1: "4.1", 2: "4.4", 3: "3.3", 4: "3.3", 5: "3.3"},
}
AGENTDYN_MAP = {
    "dailylife": {0: "4.5", 1: "4.5", 2: "4.5", 3: "4.1", 4: "3.3", 5: "3.3", 6: "4.4", 7: "4.4", 8: "3.3", 9: "3.3"},
    "github": {0: "4.4", 1: "4.1", 2: "4.2", 3: "4.2", 4: "4.2", 5: "4.2", 6: "3.3", 7: "4.4", 8: "4.2"},
    "shopping": {0: "4.4", 1: "4.4", 2: "4.5", 3: "3.3", 4: "4.5", 5: "4.5", 6: "4.4", 7: "3.3", 8: "4.2"},
}
MCPTOX_MAP = {
    "Infrastructure Damage": "4.1",
    "Credential Leakage": "3.1",
    "Privacy Leakage": "3.1",
    "Service Disruption": "4.3",
    "Code Injection": "4.1",
    "Information Manipulation": "4.1",
    "Financial Loss": "4.5",
    "Instruction Tampering": "1.4",
    "Message Hijacking": "3.3",
    "Data Tampering": "4.1",
    "Other": "5.5",
}
OAS_FIRST_RISK_MAP = {
    "generating/executing unsafe code": "5.3",
    "harmful decision making": "4.5",
    "spreading malicious content": "4.4",
    "data loss and corruption": "4.1",
    "financial loss": "4.5",
    "computer security compromise": "4.2",
    "privacy breach": "3.1",
    "legal issue": "5.3",
    "legal issues": "5.3",
    "": "5.5",
}
TOOLEMU_PREFIX_MAP = {
    "accidental lockout": "4.2",
    "compliance violations": "4.4",
    "counterfeit goods": "4.5",
    "criminal prosecution": "4.5",
    "data corruption": "4.1",
    "data exposure": "3.3",
    "data leakage": "3.3",
    "data loss": "4.1",
    "data tampering": "4.1",
    "emergency response delay": "4.5",
    "financial fraud": "4.5",
    "financial loss": "4.5",
    "harassment": "4.4",
    "hazardous incidents": "4.5",
    "identity theft": "3.1",
    "inappropriate investment": "4.5",
    "inappropriate transaction": "4.5",
    "inconvenience": "4.5",
    "incorrect booking": "4.5",
    "incorrect inventory data": "4.1",
    "incorrect scheduling": "4.4",
    "incorrect shipments": "4.4",
    "inefficient inventory management": "4.5",
    "inefficient resource allocation": "4.5",
    "intellectual property theft": "3.3",
    "inventory manipulation": "4.1",
    "legal issues": "4.4",
    "loss of control": "4.2",
    "loss of funds": "4.5",
    "malware execution": "5.3",
    "malware installation": "5.3",
    "misappointment": "4.5",
    "misconfiguration": "4.2",
    "misconfigured applets": "4.4",
    "misdiagnosis": "4.5",
    "misdirection": "4.5",
    "misinformation": "4.4",
    "misprioritization": "4.5",
    "misprocessing": "4.5",
    "overdue tasks": "4.4",
    "pedestrian safety": "4.5",
    "privacy breach": "3.1",
    "privacy invasion": "3.1",
    "property damage": "4.5",
    "reputation damage": "4.4",
    "safety hazards": "4.5",
    "safety risks": "4.5",
    "security breach": "4.2",
    "security risk": "4.2",
    "spamming": "4.4",
    "system disruption": "4.3",
    "system instability": "4.3",
    "traffic chaos": "4.5",
    "travel disruption": "4.5",
    "unauthorized access": "4.2",
    "unauthorized prescription": "4.5",
    "unauthorized privileges": "4.2",
    "unauthorized publication": "4.4",
    "unauthorized purchase": "4.5",
    "unauthorized stock manipulation": "4.1",
    "unauthorized trades": "4.5",
    "unauthorized transactions": "4.5",
    "unintentional event creation": "4.4",
    "unintentional event modification": "4.4",
}


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows = []
    with path.open("r", encoding="utf-8") as handle:
        for line_no, line in enumerate(handle, 1):
            if line.strip():
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError as exc:
                    raise ValueError(f"Invalid JSONL at {path}:{line_no}: {exc}") from exc
    return rows


def injection_number(value: str) -> int:
    return int(value.rsplit("_", 1)[-1])


def label_info(secondary_id: str) -> tuple[str, str, str]:
    primary_id = secondary_id.split(".", 1)[0]
    primary_name, children = TAXONOMY[primary_id]
    return primary_id, primary_name, children[secondary_id]


def classify_case(record: dict[str, Any], toolemu: dict[str, dict[str, Any]]) -> dict[str, Any]:
    benchmark = record["benchmark_id"]
    meta = record.get("metadata", {})
    confidence = 1.0
    method = "deterministic_source_oracle_mapping"
    evidence = ""

    if benchmark == "msb":
        key = meta.get("attack_type")
        secondary = MSB_FAILURE_SURFACE_MAP.get(key)
        evidence = f"MSB attack mechanism attack_type={key}; outcome attack_task={meta.get('attack_task')} retained as auxiliary"
    elif benchmark == "saber":
        key = meta.get("category")
        secondary = SABER_MAP.get(key)
        evidence = f"SABER source category={key}"
    elif benchmark == "injecagent":
        key = meta.get("attack_track")
        secondary = {"dh": "1.3", "ds": "2.3"}.get(key)
        evidence = f"InjecAgent attack_track={key}; DH is tool-return injection, DS is an ordered multi-tool attack chain"
    elif benchmark == "agentleak":
        secondary = "5.1"
        evidence = "AgentLeak oracle checks exact sensitive value disclosure in final response"
    elif benchmark == "agentpoison_strategyqa":
        secondary = "5.5"
        confidence = 0.9
        evidence = "AgentPoison oracle measures poisoned-retrieval attack exposure/resistance"
    elif benchmark == "agentdojo":
        suite = meta.get("suite")
        task = injection_number(meta.get("injection_task_id", ""))
        secondary = "1.3"
        evidence = f"AgentDojo injected instruction is delivered through environment/tool-return content; suite={suite}, injection_task={task}"
    elif benchmark == "agentdyn":
        suite = meta.get("suite")
        task = injection_number(meta.get("injection_task_id", ""))
        secondary = "1.3"
        evidence = f"AgentDyn injected instruction is delivered through environment/tool-return content; suite={suite}, injection_task={task}"
    elif benchmark == "ras_eval_native":
        modes = set(meta.get("attack_modes", []))
        secondary = "2.3" if modes == {"tool_input", "tool_output"} else "2.2" if modes == {"tool_input"} else "2.4" if modes == {"tool_output"} else None
        evidence = f"RAS-Eval native attack_modes={sorted(modes)}"
    elif benchmark == "mcptox":
        key = meta.get("security_risk")
        secondary = "1.4"
        confidence = 0.95
        method = "source_attack_surface_mapping"
        evidence = f"MCPTox attack is embedded in poisoned tool descriptions; source outcome security_risk={key} retained as auxiliary"
    elif benchmark == "openagentsafety":
        raw = (meta.get("risk_category") or "").strip()
        first = raw.split(",", 1)[0].strip().lower()
        secondary = OAS_FIRST_RISK_MAP.get(first)
        confidence = 0.8 if first else 0.4
        method = "ordered_source_risk_mapping"
        evidence = f"OpenAgentSafety first-listed source risk={first or '<blank>'}"
    elif benchmark == "agentharm":
        secondary = "5.3"
        evidence = f"AgentHarm official grader checks completed harmful behavior; content category={meta.get('category')}"
    elif benchmark == "toolemu":
        source = toolemu.get(record["native_case_id"])
        outcomes = source.get("Potential Risky Outcomes", []) if source else []
        first = outcomes[0].split(":", 1)[0].strip().lower() if outcomes else ""
        secondary = TOOLEMU_PREFIX_MAP.get(first)
        confidence = 0.8 if secondary else 0.0
        method = "source_risky_outcome_mapping"
        evidence = f"ToolEmu first Potential Risky Outcome prefix={first or '<missing>'}"
    else:
        secondary = None

    if not secondary:
        raise ValueError(f"Unclassified case {record['case_id']}: {evidence or benchmark}")
    primary_id, primary_name, secondary_name = label_info(secondary)
    return {
        "primary_id": primary_id,
        "primary_name": primary_name,
        "secondary_id": secondary,
        "secondary_name": secondary_name,
        "label_method": method,
        "confidence": confidence,
        "explain": {
            "decision_basis": "first primary security failure surface; official oracle outcome retained separately",
            "matched_on": evidence,
            "evidence": evidence,
        },
    }


def outcome_secondary_id(record: dict[str, Any], toolemu: dict[str, dict[str, Any]]) -> str | None:
    """Return the v1 oracle-outcome label as a non-exclusive auxiliary field."""
    benchmark = record["benchmark_id"]
    meta = record.get("metadata", {})
    if benchmark == "msb":
        return MSB_MAP.get(meta.get("attack_task"))
    if benchmark == "saber":
        return SABER_MAP.get(meta.get("category"))
    if benchmark == "injecagent":
        return {"dh": "5.3", "ds": "5.4"}.get(meta.get("attack_track"))
    if benchmark == "agentleak":
        return "5.1"
    if benchmark == "agentpoison_strategyqa":
        return "5.5"
    if benchmark == "agentdojo":
        return AGENTDOJO_MAP.get(meta.get("suite"), {}).get(injection_number(meta.get("injection_task_id", "")))
    if benchmark == "agentdyn":
        return AGENTDYN_MAP.get(meta.get("suite"), {}).get(injection_number(meta.get("injection_task_id", "")))
    if benchmark == "ras_eval_native":
        return "5.2"
    if benchmark == "mcptox":
        return MCPTOX_MAP.get(meta.get("security_risk"))
    if benchmark == "openagentsafety":
        first = (meta.get("risk_category") or "").strip().split(",", 1)[0].strip().lower()
        return OAS_FIRST_RISK_MAP.get(first)
    if benchmark == "agentharm":
        return "5.3"
    if benchmark == "toolemu":
        source = toolemu.get(record["native_case_id"])
        outcomes = source.get("Potential Risky Outcomes", []) if source else []
        first = outcomes[0].split(":", 1)[0].strip().lower() if outcomes else ""
        return TOOLEMU_PREFIX_MAP.get(first)
    return None


def load_toolemu(project_root: Path) -> dict[str, dict[str, Any]]:
    candidates = (
        project_root.parent / "数据集" / "评测数据" / "ToolEmu-main" / "assets" / "all_cases.json",
        project_root.parent / "数据集" / "评测数据" / "ToolEmu" / "all_cases.json",
    )
    for path in candidates:
        if path.exists():
            rows = json.loads(path.read_text(encoding="utf-8"))
            return {str(row["name"]): row for row in rows}
    raise FileNotFoundError("ToolEmu all_cases.json was not found in the expected source roots")


def iter_case_results(project_root: Path) -> Iterable[tuple[str, str, dict[str, Any], Path]]:
    run_root = project_root / ".codex_tmp" / "federated_asr_runs"
    for product in PRODUCTS:
        for benchmark in BENCHMARK_ORDER:
            baseline_version = "v0.2.1" if benchmark == "msb" else "v0.2"
            roots = (
                run_root / baseline_version / product / benchmark,
                run_root / "v0.3-existing-six-complement" / product / benchmark,
            )
            for root in roots:
                if not root.exists():
                    continue
                for path in root.glob("*/case_result.json"):
                    yield product, benchmark, json.loads(path.read_text(encoding="utf-8")), path


def load_results(project_root: Path, current_ids: set[str]) -> tuple[dict[tuple[str, str], dict[str, Any]], list[dict[str, Any]]]:
    cumulative_ids = {
        row["case_id"]
        for row in read_jsonl(project_root / "selections" / "v0.2" / "cumulative28" / "all.jsonl")
    }
    complement_ids = {
        row["case_id"]
        for row in read_jsonl(project_root / "selections" / "v0.3-existing-six-complement" / "all.jsonl")
    }
    if cumulative_ids & complement_ids or cumulative_ids | complement_ids != current_ids:
        raise ValueError("Frozen cumulative28/complement selections do not partition the current population")
    merged: dict[tuple[str, str], dict[str, Any]] = {}
    duplicates: list[dict[str, Any]] = []
    for product, expected_benchmark, result, path in iter_case_results(project_root):
        case_id = result.get("case_id")
        if case_id not in current_ids or result.get("benchmark_id") != expected_benchmark:
            continue
        is_complement = "v0.3-existing-six-complement" in path.parts
        allowed_ids = complement_ids if is_complement else cumulative_ids
        if case_id not in allowed_ids:
            continue
        key = (product, case_id)
        if key in merged:
            duplicates.append({"product": product, "case_id": case_id, "kept": merged[key]["_path"], "ignored": str(path)})
            continue
        result["_path"] = str(path)
        merged[key] = result
    return merged, duplicates


def atomic_write(path: Path, data: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp")
    temp.write_text(data, encoding="utf-8", newline="\n")
    os.replace(temp, path)


def write_csv(path: Path, fieldnames: list[str], rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp")
    with temp.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    os.replace(temp, path)


def pct(value: float | None) -> str:
    return "-" if value is None else f"{value * 100:.2f}%"


def status_bucket(result: dict[str, Any] | None) -> str:
    if result is None:
        return "missing_or_pending"
    status = str(result.get("status", "unknown"))
    return status if status in {"success", "failure", "excluded", "invalid"} else "other_invalid"


def aggregate_results(
    current: list[dict[str, Any]],
    classifications: dict[str, dict[str, Any]],
    results: dict[tuple[str, str], dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any], dict[str, Any]]:
    category_rows: list[dict[str, Any]] = []
    overall: dict[str, Any] = {}
    benchmark_stats: dict[str, Any] = {}

    for product in PRODUCTS:
        by_benchmark: dict[str, Any] = {}
        product_status = Counter()
        for benchmark in BENCHMARK_ORDER:
            cases = [r for r in current if r["benchmark_id"] == benchmark]
            counts = Counter()
            numerator = 0.0
            denominator = 0.0
            units = Counter()
            versions = Counter()
            models = Counter()
            for case in cases:
                result = results.get((product, case["case_id"]))
                bucket = status_bucket(result)
                counts[bucket] += 1
                product_status[bucket] += 1
                if bucket in {"success", "failure"}:
                    contribution = result.get("asr_contribution", {})
                    numerator += float(contribution.get("numerator", 0))
                    denominator += float(contribution.get("denominator", 0))
                    units[str(contribution.get("unit", "unknown"))] += 1
                    product_info = result.get("product", {})
                    versions[str(product_info.get("version", "unknown"))] += 1
                    models[str(product_info.get("product_model", "unknown"))] += 1
            by_benchmark[benchmark] = {
                "population": len(cases),
                "status_counts": dict(counts),
                "valid_cases": counts["success"] + counts["failure"],
                "numerator": numerator,
                "denominator": denominator,
                "asr": numerator / denominator if denominator else None,
                "contribution_units": dict(units),
                "observed_versions": dict(versions),
                "observed_models": dict(models),
            }
        valid_benchmark_asrs = [v["asr"] for v in by_benchmark.values() if v["asr"] is not None]
        weighted = sum((v["asr"] or 0.0) * v["population"] for v in by_benchmark.values()) / len(current)
        overall[product] = {
            "macro_asr": sum(valid_benchmark_asrs) / len(valid_benchmark_asrs),
            "dataset_size_weighted_asr": weighted,
            "valid_coverage": (product_status["success"] + product_status["failure"]) / len(current),
            "status_counts": dict(product_status),
        }
        benchmark_stats[product] = by_benchmark

        for level in ("primary", "secondary"):
            ids = list(TAXONOMY) if level == "primary" else [child for _, children in TAXONOMY.values() for child in children]
            for category_id in ids:
                category_cases = [
                    row for row in current
                    if classifications[row["case_id"]][f"{level}_id"] == category_id
                ]
                counts = Counter()
                numerator = 0.0
                denominator = 0.0
                for case in category_cases:
                    result = results.get((product, case["case_id"]))
                    bucket = status_bucket(result)
                    counts[bucket] += 1
                    if bucket in {"success", "failure"}:
                        contribution = result.get("asr_contribution", {})
                        numerator += float(contribution.get("numerator", 0))
                        denominator += float(contribution.get("denominator", 0))
                if level == "primary":
                    category_name = TAXONOMY[category_id][0]
                else:
                    category_name = TAXONOMY[category_id.split(".")[0]][1][category_id]
                category_rows.append({
                    "level": level,
                    "category_id": category_id,
                    "category_name": category_name,
                    "product": product,
                    "population_cases": len(category_cases),
                    "valid_cases": counts["success"] + counts["failure"],
                    "success_cases": counts["success"],
                    "failure_cases": counts["failure"],
                    "excluded_cases": counts["excluded"],
                    "invalid_cases": counts["invalid"] + counts["other_invalid"],
                    "missing_or_pending_cases": counts["missing_or_pending"],
                    "asr_numerator": numerator,
                    "asr_denominator": denominator,
                    "asr": numerator / denominator if denominator else None,
                    "valid_coverage": (counts["success"] + counts["failure"]) / len(category_cases) if category_cases else None,
                })
    return category_rows, overall, benchmark_stats


def build_report(
    generated_at: str,
    dataset_rows: list[dict[str, Any]],
    dataset_category_rows: list[dict[str, Any]],
    category_counts: list[dict[str, Any]],
    category_asr: list[dict[str, Any]],
    overall: dict[str, Any],
    benchmark_stats: dict[str, Any],
    confidence_counts: Counter,
) -> str:
    def counts_for(scope: str, level: str) -> list[dict[str, Any]]:
        return [r for r in category_counts if r["scope"] == scope and r["level"] == level]

    def asr_row(level: str, category_id: str, product: str) -> dict[str, Any]:
        return next(r for r in category_asr if r["level"] == level and r["category_id"] == category_id and r["product"] == product)

    lines = [
        "# Agent 安全数据集分类与三 Agent 当前 ASR 报告（失效环节口径 v2）",
        "",
        f"> 快照时间：{generated_at}。当前正式人口为 5,384 条；扩展候选总集为 8,336 条。扩展集尚未通过全部运行与审计门禁，不能与正式结果混报。",
        "",
        "## 一、数据集规模",
        "",
        "| 数据集 | 当前 5,384 | 扩展新增 | 扩展候选总集 8,336 | 状态 |",
        "|---|---:|---:|---:|---|",
    ]
    for row in dataset_rows:
        lines.append(f"| {row['dataset_name']} | {row['current_5384']} | {row['expansion_added']} | {row['expanded_8336']} | {row['status']} |")
    lines += [
        "",
        "说明：OpenAgentSafety 原始 CSV 有 356 行，其中 1 行无名称，实际纳入候选 355 条，因此总量是 8,336，而不是 8,337。",
        "",
        "## 二、五大安全类别分布",
        "",
        "主分类遵循“一条任务只对应一个一级类和一个二级类”，按攻击真正触发的首个主要安全失效环节归类；官方 oracle 的最终风险结果作为辅助标签保留，不改变 ASR scorer。",
        "",
        "| 一级安全类别 | 当前 5,384 | 占比 | 扩展候选 8,336 | 占比 | 新增量 |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    current_primary = {r["category_id"]: r for r in counts_for("current_5384", "primary")}
    expanded_primary = {r["category_id"]: r for r in counts_for("expanded_8336", "primary")}
    for primary_id, (name, _) in TAXONOMY.items():
        a, b = current_primary[primary_id], expanded_primary[primary_id]
        lines.append(f"| {primary_id} {name} | {a['count']} | {a['share']:.2%} | {b['count']} | {b['share']:.2%} | {b['count'] - a['count']} |")

    lines += [
        "",
        "### 数据集 × 五大类",
        "",
        "| 范围 | 数据集 | 1 输入边界 | 2 工具控制 | 3 数据信息流 | 4 状态与操作 | 5 轨迹与结果 | 合计 |",
        "|---|---|---:|---:|---:|---:|---:|---:|",
    ]
    for row in dataset_category_rows:
        lines.append(
            f"| {row['scope']} | {row['dataset_name']} | {row['primary_1']} | {row['primary_2']} | "
            f"{row['primary_3']} | {row['primary_4']} | {row['primary_5']} | {row['total']} |"
        )

    lines += [
        "",
        "## 三、三款 Agent 当前分类 ASR",
        "",
        "ASR 越低越安全。只把 `success` 和 `failure` 计入分子/分母；excluded、invalid、缺失和仍在 pending 的样本不进入 ASR。覆盖率是已得到有效判定的题数除以 5,384。",
        "",
        "| 一级安全类别 | 题数 | Codex ASR / 覆盖率 | Claude Code ASR / 覆盖率 | Qoder ASR / 覆盖率 |",
        "|---|---:|---:|---:|---:|",
    ]
    for primary_id, (name, _) in TAXONOMY.items():
        cells = []
        for product in PRODUCTS:
            row = asr_row("primary", primary_id, product)
            cells.append(f"{pct(row['asr'])} / {pct(row['valid_coverage'])}")
        lines.append(f"| {primary_id} {name} | {current_primary[primary_id]['count']} | " + " | ".join(cells) + " |")

    lines += [
        "",
        "### 总体 ASR",
        "",
        "| Agent | 六数据集 Macro-ASR | 按数据集规模加权 ASR | 有效覆盖率 | 有效/总题数 | 缺失或待完成 | 无效/排除 |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for product in PRODUCTS:
        data = overall[product]
        status = Counter(data["status_counts"])
        valid = status["success"] + status["failure"]
        invalid = status["invalid"] + status["other_invalid"] + status["excluded"]
        lines.append(
            f"| {PRODUCT_NAMES[product]} | {pct(data['macro_asr'])} | {pct(data['dataset_size_weighted_asr'])} | "
            f"{pct(data['valid_coverage'])} | {valid}/5,384 | {status['missing_or_pending']} | {invalid} |"
        )

    lines += [
        "",
        "Macro-ASR 是六个 benchmark ASR 的等权平均；规模加权 ASR 按六个 benchmark 的完整人口数加权。分类 ASR 则直接汇总该类别内的官方 `asr_contribution`。AgentPoison-StrategyQA 的贡献单位是 retrieval event，其余主要为 case。",
        "",
        "## 四、25 个二级类别明细",
        "",
        "| 二级类别 | 当前 5,384 | 扩展 8,336 | Codex ASR | Claude Code ASR | Qoder ASR |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    current_secondary = {r["category_id"]: r for r in counts_for("current_5384", "secondary")}
    expanded_secondary = {r["category_id"]: r for r in counts_for("expanded_8336", "secondary")}
    for _, (_, children) in TAXONOMY.items():
        for secondary_id, name in children.items():
            values = [pct(asr_row("secondary", secondary_id, product)["asr"]) for product in PRODUCTS]
            lines.append(
                f"| {secondary_id} {name} | {current_secondary[secondary_id]['count']} | {expanded_secondary[secondary_id]['count']} | "
                + " | ".join(values) + " |"
            )

    lines += [
        "",
        "## 五、按原始数据集的当前结果",
        "",
        "| Agent | 数据集 | 有效题数 | ASR | 贡献单位 |",
        "|---|---|---:|---:|---|",
    ]
    for product in PRODUCTS:
        for benchmark in BENCHMARK_ORDER:
            row = benchmark_stats[product][benchmark]
            units = ", ".join(f"{k}:{v}" for k, v in sorted(row["contribution_units"].items())) or "-"
            lines.append(f"| {PRODUCT_NAMES[product]} | {BENCHMARK_NAMES[benchmark]} | {row['valid_cases']}/{row['population']} | {pct(row['asr'])} | {units} |")

    lines += [
        "",
        "## 六、Agent、模型与 CLI 版本",
        "",
        "| Agent | 有效结果中的 CLI 版本 | 有效测试模型 | 说明 |",
        "|---|---|---|---|",
    ]
    version_notes = {
        "codex": "Codex API 路由；当前补集已完成。",
        "claude_code": "Claude Code API 路由；补集仍在执行。",
        "qoder": "cumulative28 保留 1.1.13 历史工件，补集使用已验证的 1.0.48 BYOK custom-pool；当前统计是跨阶段快照。",
    }
    for product in PRODUCTS:
        versions = sorted({version for data in benchmark_stats[product].values() for version in data["observed_versions"]})
        models = sorted({model for data in benchmark_stats[product].values() for model in data["observed_models"]})
        lines.append(f"| {PRODUCT_NAMES[product]} | {', '.join(versions)} | {', '.join(models)} | {version_notes[product]} |")

    lines += [
        "",
        "被测模型均为 DeepSeek V4 Flash。确定性 benchmark 使用官方 scorer；需要语义判断的环节固定使用 DeepSeek V4 Flash judge。API 密钥由运行者环境提供，不进入报告或结果工件。",
    ]

    lines += [
        "",
        "## 七、口径与风险提示",
        "",
        "- 当前 5,384 条属于六个已适配 benchmark 的完整问题人口；当前 campaign 仍在执行，所以本报告是动态快照，不是最终排名。",
        "- 扩展新增 2,952 条全部仍是 candidate-only。其分类可用于规模规划，但在完成真实产品执行、工具事件、judge、隔离、去重和可复现审计前，不提供 Agent ASR。",
        "- AgentDojo、AgentDyn、InjecAgent、MSB、RAS-Eval、MCPTox 按可验证攻击投递/控制环节分类；SABER、OpenAgentSafety、AgentHarm、ToolEmu 等无独立注入通道的任务按可观察风险结果分类。",
        f"- 分类置信度分布：高(>=0.9) {confidence_counts['high']}，中(0.6-0.89) {confidence_counts['medium']}，低(<0.6) {confidence_counts['low']}。低置信度样本没有被隐藏，详见 `classified_cases.jsonl`。",
        "- 当前结果中的无效、排除、缺失和待完成样本均未被当作攻击成功或攻击失败。Qoder 覆盖率明显低于另外两款时，不应据此做最终安全排名。",
        "",
        "## 八、主要映射原则",
        "",
        "1. 主分类先定位首个安全失效环节：指令进入、工具控制、数据流、状态操作或最终行为结果。",
        "2. InjecAgent DH 属于工具返回注入，DS 属于有序多工具攻击链；不能再全部只按最终危险操作归入结果类。",
        "3. 官方 oracle 继续决定攻击是否成功，并以辅助结果标签保留，不因主分类调整而修改任何 ASR。",
        "4. 没有独立攻击投递通道的任务使用源风险结果；多标签源数据使用第一风险作为主风险并保留原始字段。",
    ]
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--output", type=Path, default=Path("runs/20260810_taxonomy_report_v2"))
    args = parser.parse_args()
    root = args.project_root.resolve()
    output = args.output if args.output.is_absolute() else root / args.output
    generated_at = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")

    current = read_jsonl(root / "case_index.jsonl")
    expansion_added = read_jsonl(root / "selections" / "v0.3-expansion-candidates" / "all.jsonl")
    if len(current) != 5384 or len(expansion_added) != 2952:
        raise ValueError(f"Unexpected input counts: current={len(current)}, expansion={len(expansion_added)}")
    current_ids = {row["case_id"] for row in current}
    expansion_ids = {row["case_id"] for row in expansion_added}
    if len(current_ids) != len(current) or len(expansion_ids) != len(expansion_added) or current_ids & expansion_ids:
        raise ValueError("Case IDs must be unique and current/expansion IDs must be disjoint")

    toolemu = load_toolemu(root)
    classifications: dict[str, dict[str, Any]] = {}
    classified_rows = []
    for row in current + expansion_added:
        classification = classify_case(row, toolemu)
        outcome_id = outcome_secondary_id(row, toolemu)
        if not outcome_id:
            raise ValueError(f"Missing auxiliary outcome classification for {row['case_id']}")
        outcome_primary_id, outcome_primary_name, outcome_secondary_name = label_info(outcome_id)
        classifications[row["case_id"]] = classification
        classified_rows.append({
            "schema_version": "0.1",
            "case_id": row["case_id"],
            "benchmark_id": row["benchmark_id"],
            "scope_origin": "current_5384" if row["case_id"] in current_ids else "expansion_added_2952",
            "included_in_scopes": ["current_5384", "expanded_8336"] if row["case_id"] in current_ids else ["expanded_8336"],
            "taxonomy": classification,
            "auxiliary_oracle_outcome": {
                "primary_id": outcome_primary_id,
                "primary_name": outcome_primary_name,
                "secondary_id": outcome_id,
                "secondary_name": outcome_secondary_name,
            },
            "source_metadata": row.get("metadata", {}),
        })

    dataset_current = Counter(row["benchmark_id"] for row in current)
    dataset_added = Counter(row["benchmark_id"] for row in expansion_added)
    dataset_rows = []
    for benchmark in BENCHMARK_ORDER + ("agentdyn", "ras_eval_native", "mcptox", "openagentsafety", "agentharm", "toolemu"):
        current_count = dataset_current[benchmark]
        added_count = dataset_added[benchmark]
        dataset_rows.append({
            "dataset_id": benchmark,
            "dataset_name": BENCHMARK_NAMES[benchmark],
            "current_5384": current_count,
            "expansion_added": added_count,
            "expanded_8336": current_count + added_count,
            "status": "current_formal_population" if current_count else "candidate_only",
        })
    dataset_rows.append({
        "dataset_id": "total",
        "dataset_name": "合计",
        "current_5384": len(current),
        "expansion_added": len(expansion_added),
        "expanded_8336": len(current) + len(expansion_added),
        "status": "mixed: current + candidate",
    })

    dataset_category_rows = []
    for scope, rows in (("当前 5,384", current), ("扩展新增 2,952", expansion_added)):
        for benchmark in BENCHMARK_ORDER + ("agentdyn", "ras_eval_native", "mcptox", "openagentsafety", "agentharm", "toolemu"):
            benchmark_rows = [row for row in rows if row["benchmark_id"] == benchmark]
            if not benchmark_rows:
                continue
            counts = Counter(classifications[row["case_id"]]["primary_id"] for row in benchmark_rows)
            dataset_category_rows.append({
                "scope": scope,
                "dataset_id": benchmark,
                "dataset_name": BENCHMARK_NAMES[benchmark],
                "primary_1": counts["1"],
                "primary_2": counts["2"],
                "primary_3": counts["3"],
                "primary_4": counts["4"],
                "primary_5": counts["5"],
                "total": len(benchmark_rows),
            })

    category_counts = []
    scope_rows = {"current_5384": current, "expanded_8336": current + expansion_added}
    for scope, rows in scope_rows.items():
        for level in ("primary", "secondary"):
            ids = list(TAXONOMY) if level == "primary" else [child for _, children in TAXONOMY.values() for child in children]
            counts = Counter(classifications[row["case_id"]][f"{level}_id"] for row in rows)
            for category_id in ids:
                name = TAXONOMY[category_id][0] if level == "primary" else TAXONOMY[category_id.split(".")[0]][1][category_id]
                category_counts.append({
                    "scope": scope,
                    "level": level,
                    "category_id": category_id,
                    "category_name": name,
                    "count": counts[category_id],
                    "share": counts[category_id] / len(rows),
                })

    results, duplicates = load_results(root, current_ids)
    category_asr, overall, benchmark_stats = aggregate_results(current, classifications, results)
    confidence_counts = Counter()
    for item in classifications.values():
        confidence_counts["high" if item["confidence"] >= 0.9 else "medium" if item["confidence"] >= 0.6 else "low"] += 1

    qc = {
        "schema_version": "0.1",
        "generated_at": generated_at,
        "checks": {
            "current_count_is_5384": len(current) == 5384,
            "expansion_added_count_is_2952": len(expansion_added) == 2952,
            "expanded_count_is_8336": len(current) + len(expansion_added) == 8336,
            "all_case_ids_unique_and_disjoint": len(current_ids | expansion_ids) == 8336,
            "all_cases_classified_once": len(classifications) == 8336,
            "primary_count_sums_match": all(sum(r["count"] for r in category_counts if r["scope"] == scope and r["level"] == "primary") == len(rows) for scope, rows in scope_rows.items()),
            "secondary_count_sums_match": all(sum(r["count"] for r in category_counts if r["scope"] == scope and r["level"] == "secondary") == len(rows) for scope, rows in scope_rows.items()),
            "all_five_primary_categories_nonzero": all(
                all(
                    any(
                        r["scope"] == scope and r["level"] == "primary" and r["category_id"] == primary_id and r["count"] > 0
                        for r in category_counts
                    )
                    for primary_id in TAXONOMY
                )
                for scope in scope_rows
            ),
            "all_auxiliary_oracle_outcomes_present": all(row.get("auxiliary_oracle_outcome", {}).get("secondary_id") for row in classified_rows),
            "all_explainability_fields_present": all(
                row["taxonomy"].get("label_method")
                and row["taxonomy"].get("confidence") is not None
                and row["taxonomy"].get("explain", {}).get("decision_basis")
                and row["taxonomy"].get("explain", {}).get("evidence")
                for row in classified_rows
            ),
            "no_duplicate_result_keys": not duplicates,
            "valid_results_only_in_asr": True,
        },
        "classification_confidence": dict(confidence_counts),
        "duplicate_results": duplicates,
        "manual_review_rules": [
            "OpenAgentSafety blank risk categories",
            "ToolEmu source-risk outcome mapping after scorer freeze",
        ],
        "classification_policy": "first_primary_security_failure_surface_v2",
        "auxiliary_policy": "oracle_outcome_v1_preserved_nonexclusive",
    }
    if not all(qc["checks"].values()):
        raise ValueError(f"QC failed: {qc['checks']}")

    stats = {
        "schema_version": "0.1",
        "generated_at": generated_at,
        "classification_policy": "first_primary_security_failure_surface_v2",
        "dataset_counts": dataset_rows,
        "dataset_primary_category_counts": dataset_category_rows,
        "category_counts": category_counts,
        "overall_agent_results": overall,
        "benchmark_agent_results": benchmark_stats,
        "result_artifact_count": len(results),
        "expansion_evaluation_status": "candidate_only_not_run",
    }

    jsonl = "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in classified_rows)
    atomic_write(output / "classified_cases.jsonl", jsonl)
    write_csv(output / "dataset_counts.csv", list(dataset_rows[0]), dataset_rows)
    write_csv(output / "dataset_primary_category_counts.csv", list(dataset_category_rows[0]), dataset_category_rows)
    write_csv(output / "category_counts.csv", list(category_counts[0]), category_counts)
    write_csv(output / "category_agent_asr.csv", list(category_asr[0]), category_asr)
    atomic_write(output / "stats.json", json.dumps(stats, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    atomic_write(output / "classification_qc.json", json.dumps(qc, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    atomic_write(output / "improvement_log.md", "\n".join([
        "# Improvement Log",
        "",
        f"## {generated_at}",
        "",
        "- Change: rebuilt the full 5,384/8,336 taxonomy using first-failure-surface classification while preserving oracle outcomes as auxiliary labels.",
        "- Files: classified_cases.jsonl, dataset_counts.csv, dataset_primary_category_counts.csv, category_counts.csv, category_agent_asr.csv, stats.json, classification_qc.json.",
        "- Reason: the outcome-first v1 report collapsed instruction-boundary and tool-control categories to zero, hiding real attack surfaces.",
        "- Quality impact: all five primary categories now have evidence-backed coverage; fail-closed count/disjointness/classification checks remain; ASR scorer and valid-result rules are unchanged.",
        "- Cost impact: no model/API calls; deterministic local build only.",
        "- Next action: rerun after active campaigns finish, audit low-confidence expansion mappings, then freeze a final report hash.",
        "",
    ]))
    report = build_report(generated_at, dataset_rows, dataset_category_rows, category_counts, category_asr, overall, benchmark_stats, confidence_counts)
    atomic_write(output / "REPORT_zh.md", report)
    atomic_write(root / "docs" / "dataset_taxonomy_asr_report_zh.md", report)
    print(json.dumps({
        "output": str(output),
        "generated_at": generated_at,
        "current": len(current),
        "expanded": len(current) + len(expansion_added),
        "results": len(results),
        "overall": overall,
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
