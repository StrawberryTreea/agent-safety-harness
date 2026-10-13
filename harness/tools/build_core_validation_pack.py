from __future__ import annotations

import argparse
import csv
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import re
import shutil
import sys
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from agent_safety_harness.schema import TaskSpec
from tools.curate_datasets_v2 import agentleak_tool_list, extract_task_classes


DATASET_VERSION = "ash_core_candidate_v2"
CORE_COUNTS = {
    "agent_safetybench": 1586,
    "agentdojo": 184,
    "agentleak": 100,
    "injecagent": 510,
    "mcptox": 485,
    "msb": 1560,
    "ras_eval": 3802,
    "saber": 716,
}

READINESS = {
    "agent_safetybench": {
        "runtime": "pinned_official_environment",
        "rules": "per_task_rule_mapping_pending",
        "formal_ready": False,
        "reason": "官方工具环境已接入，但原始 1,586 条尚未全部写入两项隐藏确定性规则。",
    },
    "agentdojo": {
        "runtime": "official_runtime_available_not_integrated",
        "rules": "official_utility_security_checks_not_integrated",
        "formal_ready": False,
        "reason": "当前记录是任务类静态提取结果，必须重建 user task、injection task 和 attack 的官方执行单元。",
    },
    "agentleak": {
        "runtime": "deterministic_data_flow_runtime_pending",
        "rules": "allowed_set_and_canary_rules_pending",
        "formal_ready": False,
        "reason": "allowed_set、forbidden_fields 和 canary 可确定性判定，但工具 schema 和数据流运行时尚未接入。",
    },
    "injecagent": {
        "runtime": "deterministic_tool_runtime_pending",
        "rules": "target_and_attacker_tool_rules_pending",
        "formal_ready": False,
        "reason": "目标工具、攻击工具和注入返回已存在，仍需恢复参数 schema 并生成隐藏轨迹规则。",
    },
    "mcptox": {
        "runtime": "deterministic_mcp_runtime_pending",
        "rules": "poisoned_side_effect_rules_pending",
        "formal_ready": False,
        "reason": "投毒工具描述和风险类型完整，仍需提供正常工具行为并标注禁止副作用事件。",
    },
    "msb": {
        "runtime": "official_mcp_scenarios_not_integrated",
        "rules": "attack_action_rules_pending",
        "formal_ready": False,
        "reason": "1,560 条为官方维度笛卡尔组合，需固定工具版本并把 attack_task 编译为事件规则。",
    },
    "ras_eval": {
        "runtime": "deterministic_trace_runtime_pending",
        "rules": "workflow_and_attack_trace_rules_pending",
        "formal_ready": False,
        "reason": "正常 workflow 与注入 attack trace 均已提供，适合直接编译成成功和违规轨迹规则。",
    },
    "saber": {
        "runtime": "deterministic_workspace_runtime_pending",
        "rules": "ground_truth_command_and_delta_rules_pending",
        "formal_ready": False,
        "reason": "文件初态、注入位置、安全命令和危险命令模式完整，需接入隔离 workspace 状态判定。",
    },
}


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the 8,943-record core candidate set and a 10% coverage sample.")
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--out-root", type=Path, required=True)
    parser.add_argument("--sample-ratio", type=float, default=0.10)
    parser.add_argument("--seed", type=int, default=20260805)
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()

    if not 0 < args.sample_ratio < 1:
        raise SystemExit("--sample-ratio must be between 0 and 1")
    input_path = args.input.resolve()
    out_root = args.out_root.resolve()
    if not input_path.is_file():
        raise SystemExit(f"Missing input: {input_path}")
    if out_root.exists():
        if not args.overwrite:
            raise SystemExit(f"Output exists: {out_root}")
        if ROOT not in out_root.parents:
            raise SystemExit(f"Refusing to replace output outside workspace: {out_root}")
        shutil.rmtree(out_root)
    out_root.mkdir(parents=True)

    source_records = read_jsonl(input_path)
    candidates = select_core_candidates(source_records)
    validate_candidates(candidates)
    quotas = allocate_quotas(CORE_COUNTS, args.sample_ratio)

    by_benchmark = group_by_benchmark(candidates)
    sampled: list[dict[str, Any]] = []
    coverage: dict[str, dict[str, Any]] = {}
    for benchmark in CORE_COUNTS:
        selected, report = sample_benchmark(by_benchmark[benchmark], quotas[benchmark], args.seed)
        sampled.extend(selected)
        coverage[benchmark] = report

    source_order = {record["task_id"]: index for index, record in enumerate(candidates)}
    sampled.sort(key=lambda record: source_order[record["task_id"]])
    expected_sample_total = math.ceil(len(candidates) * args.sample_ratio)
    if len(sampled) != expected_sample_total:
        raise RuntimeError(f"Expected {expected_sample_total} samples, got {len(sampled)}")

    candidate_path = out_root / "core_8943.candidates.jsonl"
    sample_root = out_root / "validation_10pct"
    sample_path = sample_root / f"coverage_sample_{len(sampled)}.tasks.jsonl"
    write_jsonl(candidate_path, candidates)
    write_jsonl(sample_path, sampled)
    write_coverage_matrix(sample_root / "coverage_matrix.csv", sampled)

    manifest = build_manifest(args, input_path, source_records, candidates, sampled, quotas, coverage)
    write_json(out_root / "manifest.json", manifest)
    write_json(sample_root / "coverage_manifest.json", manifest["validation_sample"])
    (out_root / "README.md").write_text(render_readme(manifest), encoding="utf-8")
    (sample_root / "coverage_report.md").write_text(render_coverage_report(manifest), encoding="utf-8")
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


def select_core_candidates(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    selected = []
    for record in records:
        benchmark = str(record.get("benchmark") or "")
        if benchmark not in CORE_COUNTS:
            continue
        if benchmark == "agent_safetybench" and not record.get("tools"):
            continue
        candidate = repair_source_record(record)
        metadata = dict(candidate.get("metadata") or {})
        metadata.update(
            {
                "core_candidate_version": DATASET_VERSION,
                "formal_ready": False,
                "formal_readiness_reason": READINESS[benchmark]["reason"],
            }
        )
        candidate["metadata"] = metadata
        selected.append(candidate)
    return selected


def repair_source_record(record: dict[str, Any]) -> dict[str, Any]:
    candidate = dict(record)
    benchmark = candidate["benchmark"]
    metadata = dict(candidate.get("metadata") or {})
    raw = metadata.get("raw") if isinstance(metadata.get("raw"), dict) else {}
    if benchmark == "agentleak":
        candidate["tools"] = agentleak_tool_list(raw.get("tools"))
        metadata["core_repair"] = "structured_agentleak_tools_v1"
    elif benchmark == "agentdojo":
        source_path = Path(str(metadata.get("source_path") or ""))
        class_name = str(metadata.get("class_name") or "")
        if not source_path.is_file():
            raise FileNotFoundError(f"AgentDojo source file is unavailable: {source_path}")
        classes = dict(extract_task_classes(source_path))
        attrs = classes.get(class_name)
        if attrs is None:
            raise KeyError(f"AgentDojo class {class_name!r} not found in {source_path}")
        prompt = attrs.get("PROMPT") or attrs.get("GOAL") or attrs.get("GROUND_TRUTH_OUTPUT")
        if not prompt:
            raise ValueError(f"AgentDojo class {class_name!r} has no resolved prompt")
        candidate["user_goal"] = str(prompt)
        if metadata.get("task_kind") == "injection":
            candidate["attack_goal"] = str(attrs.get("GOAL") or attrs.get("PROMPT") or prompt)
        metadata["raw"] = attrs
        metadata["core_repair"] = "safe_static_agentdojo_constants_v1"
    candidate["metadata"] = metadata
    return candidate


def validate_candidates(records: list[dict[str, Any]]) -> None:
    counts = {benchmark: 0 for benchmark in CORE_COUNTS}
    seen: set[str] = set()
    for index, record in enumerate(records, start=1):
        task = TaskSpec.from_record(record)
        if task.task_id in seen:
            raise ValueError(f"Duplicate task_id: {task.task_id}")
        seen.add(task.task_id)
        counts[task.benchmark] += 1
        if not task.tools:
            raise ValueError(f"Core task has no tools: {task.task_id}")
        if has_unresolved_template(record):
            raise ValueError(f"Core task has an unresolved template: {task.task_id}")
        if has_malformed_tool_name(record):
            raise ValueError(f"Core task has a malformed tool name: {task.task_id}")
        if missing_required_source_fields(record):
            raise ValueError(f"Core task is missing required source fields: {task.task_id}")
        if record.get("evaluation"):
            raise ValueError(f"Source candidate unexpectedly contains evaluation rules: {task.task_id}")
        if not record.get("metadata", {}).get("formal_readiness_reason"):
            raise ValueError(f"Missing readiness reason at record {index}")
    if counts != CORE_COUNTS:
        raise ValueError(f"Core counts differ from contract: expected={CORE_COUNTS}, actual={counts}")


def allocate_quotas(counts: dict[str, int], ratio: float) -> dict[str, int]:
    target = math.ceil(sum(counts.values()) * ratio)
    quotas = {name: math.floor(count * ratio) for name, count in counts.items()}
    remaining = target - sum(quotas.values())
    order = sorted(counts, key=lambda name: (-(counts[name] * ratio - quotas[name]), name))
    for name in order[:remaining]:
        quotas[name] += 1
    return quotas


def sample_benchmark(
    records: list[dict[str, Any]], quota: int, seed: int
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    mandatory_by_id = {record["task_id"]: mandatory_features(record) for record in records}
    optional_by_id = {record["task_id"]: optional_features(record) for record in records}
    mandatory_universe = set().union(*mandatory_by_id.values()) if records else set()
    optional_universe = set().union(*optional_by_id.values()) if records else set()
    selected: list[dict[str, Any]] = []
    selected_ids: set[str] = set()

    add_greedy_cover(
        records,
        selected,
        selected_ids,
        mandatory_by_id,
        mandatory_universe,
        quota,
        seed,
    )
    covered_mandatory = union_for_selected(selected_ids, mandatory_by_id)
    missing_mandatory = mandatory_universe - covered_mandatory
    if missing_mandatory:
        preview = sorted(missing_mandatory)[:20]
        raise RuntimeError(f"Mandatory coverage exceeds quota {quota}: {preview}")

    add_greedy_cover(
        records,
        selected,
        selected_ids,
        optional_by_id,
        optional_universe,
        quota,
        seed + 1,
    )
    if len(selected) < quota:
        remaining = [record for record in records if record["task_id"] not in selected_ids]
        remaining.sort(key=lambda record: stable_order(record["task_id"], seed + 2))
        selected.extend(remaining[: quota - len(selected)])

    covered_optional = union_for_selected({record["task_id"] for record in selected}, optional_by_id)
    selected.sort(key=lambda record: stable_order(record["task_id"], seed + 3))
    return selected, {
        "source_records": len(records),
        "quota": quota,
        "sample_records": len(selected),
        "mandatory_feature_count": len(mandatory_universe),
        "mandatory_covered_count": len(covered_mandatory),
        "mandatory_features": sorted(mandatory_universe),
        "mandatory_missing": sorted(missing_mandatory),
        "optional_feature_count": len(optional_universe),
        "optional_covered_count": len(covered_optional),
        "optional_coverage_rate": round(len(covered_optional) / len(optional_universe), 6) if optional_universe else 1.0,
    }


def add_greedy_cover(
    records: list[dict[str, Any]],
    selected: list[dict[str, Any]],
    selected_ids: set[str],
    features_by_id: dict[str, set[str]],
    universe: set[str],
    quota: int,
    seed: int,
) -> None:
    uncovered = universe - union_for_selected(selected_ids, features_by_id)
    while uncovered and len(selected) < quota:
        available = [record for record in records if record["task_id"] not in selected_ids]
        best = max(
            available,
            key=lambda record: (
                len(features_by_id[record["task_id"]] & uncovered),
                -stable_order(record["task_id"], seed),
            ),
        )
        gain = features_by_id[best["task_id"]] & uncovered
        if not gain:
            break
        selected.append(best)
        selected_ids.add(best["task_id"])
        uncovered -= gain


def mandatory_features(record: dict[str, Any]) -> set[str]:
    benchmark = record["benchmark"]
    labels = record.get("labels") or {}
    metadata = record.get("metadata") or {}
    raw = metadata.get("raw") if isinstance(metadata.get("raw"), dict) else {}
    state = record.get("initial_state") or {}
    features = {
        f"profile={record.get('profile')}",
        f"expected_behavior={labels.get('expected_behavior')}",
        f"safety_category={labels.get('safety_category')}",
        f"attack_present={bool(record.get('attack_goal'))}",
        f"tool_count={tool_count_bucket(len(record.get('tools') or []))}",
    }

    if benchmark == "agent_safetybench":
        features.add(f"fulfillable={int(raw.get('fulfillable') or 0)}")
        add_values(features, "risk", raw.get("risks"))
        add_values(features, "failure_mode", split_tokens(raw.get("failure_modes")))
    elif benchmark == "agentdojo":
        suite = str(metadata.get("suite") or "<missing>")
        parts = suite.split("/", 1)
        features.update({f"suite={suite}", f"task_kind={metadata.get('task_kind')}", f"suite_version={parts[0]}"})
        if len(parts) == 2:
            features.add(f"suite_domain={parts[1]}")
    elif benchmark == "agentleak":
        features.add(f"vertical={raw.get('vertical')}")
        features.add(f"difficulty={raw.get('difficulty')}")
        attack = raw.get("attack") if isinstance(raw.get("attack"), dict) else {}
        for key in ("attack_class", "attack_family", "adversary_level", "injection_surface"):
            features.add(f"{key}={attack.get(key)}")
        add_values(features, "target_channel", attack.get("target_channels"))
    elif benchmark == "injecagent":
        features.add(f"attack_type={raw.get('Attack Type')}")
    elif benchmark == "mcptox":
        features.update(
            {
                f"security_risk={raw.get('security risk')}",
                f"server={raw.get('server_name')}",
            }
        )
    elif benchmark == "msb":
        for key in ("agent_name", "attack_type", "attack_task", "tool_config"):
            features.add(f"{key}={state.get(key)}")
    elif benchmark == "ras_eval":
        features.add(f"agent={raw.get('agent')}")
        add_values(features, "workflow_tool", raw.get("workflow"))
        for attack in state.get("attack") or []:
            if isinstance(attack, dict):
                features.add(f"attack_mode={attack.get('mode')}")
    elif benchmark == "saber":
        task = raw.get("task") if isinstance(raw.get("task"), dict) else {}
        injection = task.get("injection") if isinstance(task.get("injection"), dict) else {}
        features.update(
            {
                f"category={raw.get('category')}",
                f"scenario={task.get('scenario')}",
                f"difficulty={task.get('difficulty')}",
                f"injection_method={injection.get('method')}",
            }
        )
    return {feature for feature in features if not feature.endswith("=None") and not feature.endswith("=<missing>")}


def optional_features(record: dict[str, Any]) -> set[str]:
    features: set[str] = set()
    benchmark = record["benchmark"]
    metadata = record.get("metadata") or {}
    raw = metadata.get("raw") if isinstance(metadata.get("raw"), dict) else {}
    for tool in record.get("tools") or []:
        if isinstance(tool, dict) and tool.get("name"):
            features.add(f"tool={tool['name']}")
    if benchmark == "agent_safetybench":
        add_values(
            features,
            "environment",
            [item.get("name") for item in raw.get("environments") or [] if isinstance(item, dict)],
        )
    elif benchmark == "injecagent":
        add_values(features, "user_tool", split_tokens(raw.get("User Tool")))
        add_values(features, "attacker_tool", split_tokens(raw.get("Attacker Tools")))
    source_name = metadata.get("source_name")
    if source_name:
        features.add(f"source_name={source_name}")
    return features


def add_values(features: set[str], name: str, values: Any) -> None:
    if values is None:
        return
    if not isinstance(values, (list, tuple, set)):
        values = [values]
    for value in values:
        text = str(value).strip()
        if text:
            features.add(f"{name}={text}")


def split_tokens(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    text = str(value).strip()
    if not text:
        return []
    for separator in (",", ";", "|"):
        if separator in text:
            return [item.strip() for item in text.split(separator) if item.strip()]
    return [text]


def tool_count_bucket(count: int) -> str:
    if count == 1:
        return "1"
    if count == 2:
        return "2"
    return "3+" if count > 2 else "0"


def union_for_selected(selected_ids: set[str], features_by_id: dict[str, set[str]]) -> set[str]:
    covered: set[str] = set()
    for task_id in selected_ids:
        covered.update(features_by_id.get(task_id, set()))
    return covered


def stable_order(task_id: str, seed: int) -> int:
    return int(hashlib.sha256(f"{seed}:{task_id}".encode("utf-8")).hexdigest()[:16], 16)


def group_by_benchmark(records: Iterable[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    grouped = {benchmark: [] for benchmark in CORE_COUNTS}
    for record in records:
        grouped[record["benchmark"]].append(record)
    return grouped


def build_manifest(
    args: argparse.Namespace,
    input_path: Path,
    source_records: list[dict[str, Any]],
    candidates: list[dict[str, Any]],
    sampled: list[dict[str, Any]],
    quotas: dict[str, int],
    coverage: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "dataset_version": DATASET_VERSION,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "input": str(input_path),
        "input_records": len(source_records),
        "candidate_records": len(candidates),
        "candidate_counts": CORE_COUNTS,
        "quality_audit": quality_audit(candidates),
        "formal_ready_records": 0,
        "readiness": READINESS,
        "validation_sample": {
            "ratio": args.sample_ratio,
            "seed": args.seed,
            "target_records": math.ceil(len(candidates) * args.sample_ratio),
            "sample_records": len(sampled),
            "quality_audit": quality_audit(sampled),
            "quotas": quotas,
            "coverage": coverage,
        },
    }


def render_readme(manifest: dict[str, Any]) -> str:
    return f"""# ASH 核心候选集 v2

本目录保留 8 个来源的 `{manifest['candidate_records']}` 条有工具任务，并提供固定种子的 10% 分层抽检集。

重要：这些记录是正式集候选，不是已经可排名的正式集。当前 `formal_ready_records=0`；只有接入来源运行时、隐藏成功规则和隐藏违规规则后，才能进入模型评测。

## 文件

- `core_8943.candidates.jsonl`：完整核心候选集。
- `validation_10pct/coverage_sample_895.tasks.jsonl`：10% 分层质量抽检集。
- `validation_10pct/coverage_report.md`：来源、配额和覆盖情况。
- `validation_10pct/coverage_matrix.csv`：抽检任务逐条索引。
- `manifest.json`：数量和适配状态。

抽检种子：`{manifest['validation_sample']['seed']}`。
"""


def render_coverage_report(manifest: dict[str, Any]) -> str:
    sample = manifest["validation_sample"]
    lines = [
        "# 10% 核心数据覆盖抽检",
        "",
        f"- 核心候选：{manifest['candidate_records']}",
        f"- 抽检任务：{sample['sample_records']}",
        f"- 抽样比例：{sample['ratio']:.1%}",
        f"- 随机种子：{sample['seed']}",
        f"- 当前正式就绪：{manifest['formal_ready_records']}",
        "",
        "| 数据集 | 全量 | 抽检 | 强制分类覆盖 | 工具/来源可选覆盖 | 当前状态 |",
        "|---|---:|---:|---:|---:|---|",
    ]
    for benchmark in CORE_COUNTS:
        item = sample["coverage"][benchmark]
        optional = f"{item['optional_covered_count']}/{item['optional_feature_count']} ({item['optional_coverage_rate']:.1%})"
        lines.append(
            f"| {benchmark} | {item['source_records']} | {item['sample_records']} | "
            f"{item['mandatory_covered_count']}/{item['mandatory_feature_count']} | {optional} | 适配中 |"
        )
    lines.extend(
        [
            "",
            "## 数据质量",
            "",
            "| 数据集 | Schema有效 | 无工具 | 模板残留 | 畸形工具名 | 关键源字段缺失 | 缺隐藏规则 | 重复目标文本 |",
            "|---|---:|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for benchmark in CORE_COUNTS:
        item = manifest["quality_audit"][benchmark]
        lines.append(
            f"| {benchmark} | {item['schema_valid']}/{item['records']} | {item['no_tools']} | "
            f"{item['unresolved_templates']} | {item['malformed_tool_names']} | "
            f"{item['missing_required_source_fields']} | {item['missing_hidden_evaluation']} | "
            f"{item['duplicate_goal_texts']} |"
        )
    lines.extend(
        [
            "",
            "重复目标文本主要来自同一正常任务与不同攻击变体的组合；task_id 均唯一，不能按 user_goal 去重。",
            "",
            "## 覆盖口径",
            "",
            "强制覆盖包含每个来源适用的预期行为、风险类别、攻击类型、工具族、来源 suite、场景、难度、攻击通道和工作流类型。高基数的单个任务 ID 不作为分类。",
            "",
            "## 正式评测门禁",
            "",
            "- Agent 运行视图不得包含 expected_behavior、attack_goal、规则或允许/禁止答案标签。",
            "- 每条任务必须提供真实可调用工具或隔离 workspace。",
            "- task_success 和 safety_violation 必须分别由隐藏工具事件或最终状态判定。",
            "- 任一运行时、协议或规则失败都必须输出 unknown。",
        ]
    )
    return "\n".join(lines) + "\n"


def quality_audit(records: list[dict[str, Any]]) -> dict[str, dict[str, int]]:
    reports: dict[str, dict[str, int]] = {}
    for benchmark, group in group_by_benchmark(records).items():
        schema_valid = 0
        goals = []
        for record in group:
            TaskSpec.from_record(record)
            schema_valid += 1
            goals.append(str(record.get("user_goal") or ""))
        reports[benchmark] = {
            "records": len(group),
            "schema_valid": schema_valid,
            "no_tools": sum(not record.get("tools") for record in group),
            "unresolved_templates": sum(has_unresolved_template(record) for record in group),
            "malformed_tool_names": sum(has_malformed_tool_name(record) for record in group),
            "missing_required_source_fields": sum(missing_required_source_fields(record) for record in group),
            "missing_hidden_evaluation": sum(not record.get("evaluation") for record in group),
            "duplicate_goal_texts": len(goals) - len(set(goals)),
        }
    return reports


def has_unresolved_template(record: dict[str, Any]) -> bool:
    goal = str(record.get("user_goal") or "")
    return goal.startswith(("f'", 'f"')) or re.search(r"\{_[A-Za-z0-9_]+", goal) is not None


def has_malformed_tool_name(record: dict[str, Any]) -> bool:
    for tool in record.get("tools") or []:
        name = str(tool.get("name") or "") if isinstance(tool, dict) else ""
        if not name or name.startswith("{"):
            return True
    return False


def missing_required_source_fields(record: dict[str, Any]) -> bool:
    benchmark = record["benchmark"]
    metadata = record.get("metadata") or {}
    raw = metadata.get("raw") if isinstance(metadata.get("raw"), dict) else {}
    state = record.get("initial_state") or {}
    if benchmark == "agent_safetybench":
        return not raw.get("environments")
    if benchmark == "agentdojo":
        return not metadata.get("source_path") or not metadata.get("class_name") or not metadata.get("suite")
    if benchmark == "agentleak":
        return not state.get("allowed_set") or not state.get("private_vault") or not raw.get("evaluation")
    if benchmark == "injecagent":
        return not state.get("tool_response") or not raw.get("User Tool") or not raw.get("Attacker Tools")
    if benchmark == "mcptox":
        return not state.get("tool_content") or not raw.get("server_name") or not raw.get("tool_name")
    if benchmark == "msb":
        return not all(state.get(key) for key in ("agent_name", "task_tool", "attack_task", "attack_type"))
    if benchmark == "ras_eval":
        return not raw.get("workflow") or not state.get("attack")
    if benchmark == "saber":
        ground_truth = state.get("ground_truth") if isinstance(state.get("ground_truth"), dict) else {}
        has_ground_truth_rule = any(
            ground_truth.get(key)
            for key in (
                "expected_safe_commands",
                "harmful_command_patterns",
                "harmful_tool_patterns",
                "deltas",
            )
        )
        return not state.get("setup") or not ground_truth or not has_ground_truth_rule
    return True


def write_coverage_matrix(path: Path, records: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = []
    for record in records:
        labels = record.get("labels") or {}
        rows.append(
            {
                "benchmark": record.get("benchmark"),
                "task_id": record.get("task_id"),
                "profile": record.get("profile"),
                "expected_behavior": labels.get("expected_behavior"),
                "safety_category": labels.get("safety_category"),
                "risk_type": labels.get("risk_type"),
                "tool_count": len(record.get("tools") or []),
                "attack_present": bool(record.get("attack_goal")),
                "formal_ready": False,
                "block_reason": READINESS[record["benchmark"]]["reason"],
            }
        )
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    records = []
    with path.open("r", encoding="utf-8-sig") as handle:
        for line_number, line in enumerate(handle, start=1):
            line = line.strip()
            if line:
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError as exc:
                    raise ValueError(f"Invalid JSONL at line {line_number}: {exc}") from exc
    return records


def write_jsonl(path: Path, records: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
