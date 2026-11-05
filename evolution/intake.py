"""将分析证据接收为待审进化请求，不视为已接纳数据。

将分析文件视为同一份快照，创建输出前先核对跨文件身份和统计数量。
存档仅保留必要的来源信息及已知失败信号，不复制原始观察、私有规则或自由文本原因，
也不将这些内容发送给生成器。
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import Any

from scripts.result_analysis import (
    GATES,
    IDENTITY_FIELDS,
    TRACKS,
    empty_output,
    result_identity,
    write_json,
    write_jsonl,
)

SIGNALS = frozenset(
    {
        "attack_success",
        "rule_eval_failure",
        "refusal",
        "safety_violation",
        "task_failure",
        "native_score_failure",
    }
)
FILES = (
    "observations.jsonl",
    "failures.jsonl",
    "invalids.jsonl",
    "candidate_plans.jsonl",
    "summary.json",
)


def _decode(payload: bytes) -> Any:
    def reject_constant(_value):
        raise ValueError("JSON 包含非有限数值")

    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("JSON 存在重复字段")
            result[key] = value
        return result

    try:
        return json.loads(
            payload.decode("utf-8-sig"),
            parse_constant=reject_constant,
            object_pairs_hook=unique,
        )
    except (ValueError, UnicodeError):
        raise ValueError("分析产物中的 JSON 无效") from None


def _object(value: Any, label: str) -> dict:
    if not isinstance(value, dict):
        raise ValueError(f"{label} 必须是对象")
    return value


def _rows(payload: bytes, label: str) -> list[tuple[int, dict]]:
    result = []
    for line, raw in enumerate(payload.splitlines(), 1):
        if raw.strip():
            result.append((line, _object(_decode(raw), label)))
    return result


def _identity(row: dict, track: str) -> tuple[str, ...]:
    if row.get("track") != track:
        raise ValueError("分析产物包含不匹配的评测轨道")
    for key in ("benchmark", "run_id", "subject"):
        if not isinstance(row.get(key), str) or not row[key].strip():
            raise ValueError(f"分析身份缺少字段：{key}")
    if type(row.get("case_id")) not in (str, int) or not str(row["case_id"]).strip():
        raise ValueError("分析产物缺少有效的 case 身份")
    for key in ("model", "product_version"):
        if row.get(key) is not None and not isinstance(row[key], str):
            raise ValueError(f"分析身份字段无效：{key}")
    return result_identity(row)


def _indexed(rows: list[tuple[int, dict]], track: str) -> dict:
    indexed = {}
    for line, row in rows:
        identity = _identity(row, track)
        if identity in indexed:
            raise ValueError("分析身份重复")
        indexed[identity] = (line, row)
    return indexed


def receive_analysis(
    *, track: str, analysis_manifest: str, out: str, loop: str = "loop-a"
) -> dict:
    if track not in TRACKS or loop not in {"loop-a", "loop-b"}:
        raise ValueError("不支持该评测轨道或进化回路")
    manifest_path = Path(analysis_manifest).expanduser().resolve()
    paths = {
        "manifest.json": manifest_path,
        **{name: manifest_path.parent / name for name in FILES},
    }
    # 对实际解析的字节计算哈希；缺少配套文件时必须失败，
    # 不能将其视为空数据集，也不能静默丢弃失败观察。
    payloads = {name: path.read_bytes() for name, path in paths.items()}
    hashes = {
        name: hashlib.sha256(payload).hexdigest() for name, payload in payloads.items()
    }
    manifest = _object(_decode(payloads["manifest.json"]), "manifest")
    if (
        manifest.get("schema_version") != "1.0"
        or manifest.get("stage") != "result_analysis"
        or manifest.get("status") != "completed"
        or manifest.get("track") != track
        or manifest.get("automatic_admission") is not False
    ):
        raise ValueError("分析 manifest 必须属于当前轨道、已完成，且明确禁止自动接纳")
    summary = _object(_decode(payloads["summary.json"]), "summary")
    if (
        summary.get("schema_version") != "1.0"
        or summary.get("track") != track
        or summary.get("inputs") != manifest.get("inputs")
    ):
        raise ValueError("分析 summary 与 manifest 不一致")
    if not isinstance(manifest.get("inputs"), list) or not all(
        isinstance(p, str) for p in manifest["inputs"]
    ):
        raise ValueError("分析来源引用必须是路径列表")

    observations = _indexed(_rows(payloads["observations.jsonl"], "观察记录"), track)
    failures = _indexed(_rows(payloads["failures.jsonl"], "失败记录"), track)
    invalids = _rows(payloads["invalids.jsonl"], "无效记录")
    plans = _rows(payloads["candidate_plans.jsonl"], "候选计划")
    expected_failures = {}
    for identity, (_, row) in observations.items():
        signals = row.get("failures")
        if (
            row.get("outcome") not in {"pass", "fail"}
            or not isinstance(signals, list)
            or any(
                not isinstance(signal, str) or signal not in SIGNALS
                for signal in signals
            )
            or len(signals) != len(set(signals))
            or row.get("invalid_reason") is not None
        ):
            raise ValueError("有效观察记录的格式不符合要求")
        denominator, numerator = row.get("metric_denominator"), row.get(
            "metric_numerator"
        )
        if (
            type(denominator) is not int
            or denominator <= 0
            or type(numerator) is not int
            or not 0 <= numerator <= denominator
            or row.get("unit") not in {"case", "retrieval_event"}
        ):
            raise ValueError("观察记录的指标分子、分母或计量单位无效")
        if row["outcome"] == "fail":
            if not signals:
                raise ValueError("失败观察缺少已观测到的失败信号")
            expected_failures[identity] = row
        elif signals:
            raise ValueError("通过的观察不能提供失败种子")
    if set(failures) != set(expected_failures):
        raise ValueError("失败记录集合与观察记录不一致")
    for identity, (_, row) in failures.items():
        if row != expected_failures[identity]:
            raise ValueError("失败记录内容与对应观察不一致")

    invalid_ids = set()
    for _, row in invalids:
        if (
            row.get("track") != track
            or row.get("outcome") != "unknown"
            or not isinstance(row.get("invalid_reason"), str)
            or not row["invalid_reason"]
        ):
            raise ValueError("无效记录必须明确保留为 unknown")
        # 格式损坏的原生记录可能缺少身份；若提供了完整身份，
        # 该身份不得同时出现在有效观察集合中。
        if all(
            row.get(key) is not None
            for key in ("benchmark", "run_id", "subject", "case_id")
        ):
            identity = _identity(row, track)
            if identity in observations or identity in invalid_ids:
                raise ValueError("无效记录的身份存在冲突或重复")
            invalid_ids.add(identity)

    plan_ids = set()
    planned = {}
    for line, plan in plans:
        candidate_id = plan.get("candidate_id")
        if (
            not isinstance(candidate_id, str)
            or not candidate_id
            or candidate_id in plan_ids
        ):
            raise ValueError("候选计划 ID 必须非空且唯一")
        plan_ids.add(candidate_id)
        if (
            plan.get("schema_version") != "1.0"
            or plan.get("stage") != "candidate_plan"
            or plan.get("admitted") is not False
        ):
            raise ValueError("只允许尚未接纳的候选计划进入接收流程")
        parent = _object(plan.get("parent_observation"), "计划的父观察")
        identity = _identity(parent, track)
        if (
            identity not in failures
            or identity in planned
            or parent != failures[identity][1]
        ):
            raise ValueError("候选计划必须与失败观察一一对应")
        brief = _object(plan.get("generation_brief"), "生成说明")
        gates = _object(plan.get("gates"), "计划门控")
        if (
            brief.get("root_cause") != "unconfirmed"
            or brief.get("observed_signals") != parent["failures"]
            or set(gates) != set(GATES)
            or any(
                not isinstance(gate, dict)
                or gate.get("status") != "pending"
                or gate.get("evidence") is not None
                for gate in gates.values()
            )
        ):
            raise ValueError("分析计划必须保留未确认的根因和待审门控")
        planned[identity] = line
    if set(planned) != set(failures):
        raise ValueError("失败观察缺少对应的候选计划")

    counts = {
        "record_count": len(observations) + len(invalids),
        "valid_count": len(observations),
        "unknown_count": len(invalids),
        "failure_observation_count": len(failures),
        "candidate_plan_count": len(plans),
        "admitted_candidate_count": 0,
    }
    if any(
        type(summary.get(key)) is not int or summary[key] != value
        for key, value in counts.items()
    ):
        raise ValueError("分析汇总数量与配套文件不一致")
    signals = dict(
        Counter(signal for _, row in failures.values() for signal in row["failures"])
    )
    if summary.get("failure_signals") != signals or any(
        type(value) is not int for value in summary["failure_signals"].values()
    ):
        raise ValueError("失败信号统计与失败记录不一致")

    seeds, requests = [], []
    for identity, (line, row) in failures.items():
        # 使用确定性的哈希 ID，避免复制自由文本候选 ID 或生成说明。
        seed_id = hashlib.sha256(
            json.dumps(
                [hashes["failures.jsonl"], identity], ensure_ascii=False
            ).encode()
        ).hexdigest()
        seed = {
            "seed_id": seed_id,
            "identity": {key: row.get(key) for key in IDENTITY_FIELDS},
            "observed_signals": row["failures"],
            "root_cause": "unconfirmed",
            "failure_source": {
                "file": str(paths["failures.jsonl"]),
                "line": line,
                "sha256": hashes["failures.jsonl"],
            },
            "plan_source": {
                "file": str(paths["candidate_plans.jsonl"]),
                "line": planned[identity],
                "sha256": hashes["candidate_plans.jsonl"],
            },
            "native_evidence_verified": False,
        }
        seeds.append(seed)
        requests.append(
            {
                "request_id": f"{loop}-{seed_id}",
                "seed_id": seed_id,
                "track": track,
                "loop": loop,
                "stage": "evolution_request",
                "status": "pending_review",
                "admitted": False,
                "root_cause": "unconfirmed",
                "automatic_admission": False,
                "automatic_promotion": False,
                "gates": (
                    {gate: {"status": "pending", "evidence": None} for gate in GATES}
                    if loop == "loop-a"
                    else {}
                ),
                "target_candidate_registered": False,
            }
        )

    # 写入前再次检查输入是否变化；这些哈希仅确定本次接收快照，
    # 不证明原始执行证据的真实性或 benchmark 资格。
    if any(path.read_bytes() != payloads[name] for name, path in paths.items()):
        raise ValueError("接收过程中分析输入发生变化")
    destination = Path(out).expanduser().resolve()
    empty_output(destination)
    status = "pending_review" if seeds else "no_failures"
    result = {
        "schema_version": "evolution_intake_v1",
        "stage": "evolution_intake",
        "status": status,
        "track": track,
        "loop": loop,
        "seed_count": len(seeds),
        "admitted_candidate_count": 0,
        "automatic_admission": False,
        "automatic_promotion": False,
        "native_evidence_verified": False,
        "analysis_counts": counts,
    }
    write_jsonl(destination / "seed_refs.jsonl", seeds)
    write_jsonl(destination / "pending_requests.jsonl", requests)
    write_json(destination / "summary.json", result)
    write_json(
        destination / "manifest.json",
        {
            **result,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "inputs": {
                name: {"path": str(paths[name]), "sha256": hashes[name]}
                for name in paths
            },
            "native_result_references": manifest["inputs"],
            "limitation": "已核对分析快照；原始执行证据及生成资格仍未验证。",
        },
    )
    return {**result, "manifest": str(destination / "manifest.json")}
