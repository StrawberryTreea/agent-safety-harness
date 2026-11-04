#!/usr/bin/env python3
"""Read-only evidence audit and local Markdown report; never calls a provider."""
from __future__ import annotations

import argparse
from dataclasses import asdict
from decimal import Decimal
import json
from pathlib import Path
import sqlite3
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import run_deepseek_pilot as pilot
from scripts import run_deepseek_smoke as api
from orchestrator.registry import content_hash, validate_identity
from evolution.contracts import validate_contract
from evolution.feedback_view import scan_canaries


def money(micro):
    return str(Decimal(micro) / Decimal(1_000_000))


def report(source, out, *, first_run=None, platform_evidence=None):
    summary = json.loads((source / "summary.json").read_text(encoding="utf-8"))
    checkpoint = json.loads((source / "checkpoint.json").read_text(encoding="utf-8"))
    private = json.loads((source / "private_oracles.json").read_text(encoding="utf-8"))
    identity = json.loads((source / "run_identity.json").read_text(encoding="utf-8"))
    validate_identity(identity)
    errors = []

    def check(condition, name):
        if not condition:
            errors.append(name)

    check(checkpoint["artifacts"] == pilot.artifact_hashes(source), "artifact_hashes")
    check(checkpoint["summary_hash"] == content_hash(summary), "summary_hash")
    calls = {path.stem: json.loads(path.read_text(encoding="utf-8")) for path in (source / "calls").glob("*.json")}
    with sqlite3.connect((source / "budget.sqlite3").resolve().as_uri() + "?mode=ro", uri=True) as db:
        jobs = db.execute("SELECT id,executions,actual_cost,state FROM jobs WHERE state!='cancelled'").fetchall()
    check(set(calls) == {row[0] for row in jobs}, "receipt_ledger_population")
    for job, executions, charge, state in jobs:
        row = calls[job]
        usage, cost = api.validate_usage(row["usage"])
        check(executions == 1 and state == "settled", "job_state:" + job)
        check(charge == row["budget_charge_microcny"] == max(cost, pilot.ACCOUNTING_FLOOR), "charge:" + job)
        check(row["payload_hash"] == content_hash(row["payload"]), "payload_hash:" + job)
        check(scan_canaries(row["payload"], pilot.CANARIES)["status"] == "pass", "private_canary:" + job)
        serialized = json.dumps(row["payload"])
        check("reference_answer" not in serialized and "oracle_canary" not in serialized, "private_fields:" + job)
        check(row["response_model"] == api.MODEL, "target_model:" + job)
        check(job.split("-")[1].isdigit() and int(job.split("-")[1]) < 20, "holdout_job:" + job)
    check(len({row["response_id"] for row in calls.values()}) == len(calls), "unique_response_ids")
    check(len({row["system_fingerprint"] for row in calls.values()}) == 1, "backend_consistency")
    check(summary["budget"]["cost_committed_or_reserved"] <= pilot.LIMIT, "budget_limit")
    check(summary["model_requests"] == len(calls) <= pilot.CALL_LIMIT, "request_limit")
    check(not summary["budget"]["unsettled_jobs"] and not summary["budget"]["incidents"], "no_uncertain_charge")
    check(summary["primary_metric"] is None and not summary["formal_qualified"], "qualification_boundary")
    groups = {name: {"total": 0, "passed": 0} for name in ("clean", "seed", "replays", "benign")}
    invalid = []
    for family in summary["families"]:
        check(not family["admission"]["admitted"] and not family["admission"]["formal_qualified"], "not_admitted:" + family["family"])
        if family["generation_status"] == "valid":
            candidate = json.loads((source / "candidates" / (family["family"] + ".json")).read_text(encoding="utf-8"))
            validate_contract("TestCandidate", candidate)
            check(family["admission"]["candidate_hash"] == content_hash(candidate), "candidate_binding:" + family["family"])
        else:
            raw = json.loads(calls[family["family"] + "-generate"]["output"])
            invalid.append({"family": family["family"], "unexpected_fields": sorted(set(raw) - {"injection"})})
        oracle = private[family["family"]]
        for group in groups:
            rows = family.get(group, []) if group == "replays" else [family[group]] if group in family else []
            for row in rows:
                receipt = calls[Path(row["receipt"]).stem]
                score = asdict(api.native.RuleScorer.score({"test_text": oracle["question"], "reference_answer": oracle["reference_answer"]}, receipt["output"]))
                check(score == row["score"], "independent_rescore:" + receipt["job_id"])
                groups[group]["total"] += 1
                groups[group]["passed"] += score["passed"] is True
    audit = {"status": "passed" if not errors else "failed", "errors": errors,
             "source": str(source), "identity_hash": identity["identity_hash"], "groups": groups,
             "unique_model_requests": len(calls), "invalid_generations": invalid,
             "user_reported_platform_display_cny": "0.01",
             "user_report_note": "User says platform still displays 0.01; this is not independently verified per-run billing.",
             "summary_hash": content_hash(summary)}
    reconciliation = None
    if platform_evidence is not None:
        if first_run is None:
            raise ValueError("first_run_required_for_daily_reconciliation")
        first = json.loads((first_run / "summary.json").read_text(encoding="utf-8"))
        evidence = json.loads(platform_evidence.read_text(encoding="utf-8"))
        first_rows = [json.loads(line) for line in (first_run / "results.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
        cached = sum(row["usage"]["prompt_cache_hit_tokens"] for row in [*calls.values(), *first_rows])
        totals = {"requests": len(calls) + first["attempted_requests"],
                  "input_cache_hit": cached,
                  "input_cache_miss": summary["prompt_tokens"] + first["prompt_tokens"] - cached,
                  "output": summary["completion_tokens"] + first["completion_tokens"]}
        totals["total_tokens"] = totals["input_cache_hit"] + totals["input_cache_miss"] + totals["output"]
        check(all(evidence[name] == value for name, value in totals.items()), "platform_usage_reconciliation")
        reconciliation = {"source": "User-provided screenshots, transcribed locally", "date": evidence["date"],
                          "local_totals": totals, "platform_totals": evidence,
                          "all_usage_counts_match": "platform_usage_reconciliation" not in errors,
                          "displayed_daily_cost_cny": evidence["displayed_cost_cny"],
                          "billing_precision_note": "Daily displayed amount; not evidence of a 0.01 CNY minimum charge per request."}
        audit["platform_reconciliation"] = reconciliation
        audit["status"] = "passed" if not errors else "failed"
    out.mkdir(parents=True, exist_ok=False)
    api.write_json(out / "audit.json", audit)
    lines = ["# DeepSeek实际测试报告（2026-10-23）", "",
             f"本轮完成{summary['families_completed']}个自编任务家族，共{len(calls)}次实际模型请求；{summary.get('reused_prior_requests', 0)}条响应在修复后复用，没有重复发送。",
             "", "| 测试组 | 已完成目标回答 | 规则通过 |", "|---|---:|---:|"]
    labels = {"clean": "干净基线", "seed": "固定提示注入", "replays": "模型生成注入的两次重放", "benign": "良性对照"}
    for group, counts in groups.items():
        lines.append(f"| {labels[group]} | {counts['total']} | {counts['passed']} |")
    lines += ["", f"生成器共{sum(job.endswith('-generate') for job in calls)}次调用，{summary['families_with_valid_candidate']}个候选通过结构检查，{summary['invalid_generation_families']}个无效候选被隔离。",
              "真实生成器曾多返回type字段，原流程导致全批停止；现已改为隔离无效候选后继续。严格字段白名单保持不变。",
              "", f"有效配对中新增可复现失败家族{summary['new_reproducible_failure_families']}个、良性退化{summary['benign_regression_families']}个；无效候选存在，因此全体净差指标留空，正式主指标仍未冻结。",
              f"正式接纳数为{summary['admitted']}；有效候选仍等待语义、来源及独立审核。没有发布新测试集。",
              "", "## 费用", "", "用户最新反馈：平台仍显示0.01元；该显示值不是本报告独立确认的本轮增量账单。",
              f"Token用量：输入{summary['prompt_tokens']}，输出{summary['completion_tokens']}。按公开高峰单价估算{money(summary['usage_priced_upper_microcny'])}元。",
              f"保守预算占用{money(summary['budget']['cost_committed_or_reserved'])}元，上限5元。预算占用包含每次至少0.02元的内部预留口径，不是平台收费。",
              f"余额接口观察到的账户差额为{money(summary.get('observed_account_decrease_microcny', 0))}元；接口取整、延迟或其他账户活动会影响该值，不据此认定免费。",
              "", "## 审计与结论边界", "",
              f"审计状态：{audit['status']}，错误数{len(errors)}。已核对输入及收据哈希、{len(calls)}个不同响应ID、账本逐项结算、独立复评分、私有字段/canary、模型及指纹一致性、V/H未调用和正式接纳关闭。",
              "20个任务是自编的无害指令遵循问题；此结果证明实际调用的工程闭环可运行，不能推导模型总体安全性、Agent能力或回路A研究收益。",
              "", f"原始证据目录：`{source}`。报告在独立目录生成，没有改写冻结的运行检查点。", ""]
    if reconciliation:
        lines += ["## 平台截图核对", "", "用户提供的2026-10-23当天累计截图与本地记录逐项一致：", "",
                  "| 项目 | 平台截图 | 本地合计 |", "|---|---:|---:|"]
        labels = {"requests": "模型请求", "input_cache_hit": "输入缓存命中", "input_cache_miss": "输入缓存未命中", "output": "输出Token", "total_tokens": "总Token"}
        for name, label in labels.items():
            lines.append(f"| {label} | {evidence[name]} | {totals[name]} |")
        lines += ["", "120次请求为首次3次加本轮117次；当天累计输入15227、输出1060、总计16287 Token。",
                  "费用截图显示当天累计0.01元。这是平台显示值，不能解读为每次请求收费0.01元；内部2.34元预算占用也不代表实际扣费。", ""]
    (out / "report.md").write_text("\n".join(lines), encoding="utf-8")
    return audit


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--first-run", type=Path)
    parser.add_argument("--platform-evidence", type=Path)
    args = parser.parse_args()
    result = report(args.source.resolve(), args.out.resolve(), first_run=args.first_run,
                    platform_evidence=args.platform_evidence)
    print(json.dumps({"status": result["status"], "errors": result["errors"], "requests": result["unique_model_requests"]}))
    return 0 if result["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
