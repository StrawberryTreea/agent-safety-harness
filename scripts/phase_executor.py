#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Dict, List, Optional


SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from intent_router import route_request  # noqa: E402


@dataclass
class CheckResult:
    name: str
    status: str
    severity: str
    detail: str


def find_repo_root() -> Path:
    current = Path(__file__).resolve()
    for parent in current.parents:
        if (parent / "eval_mvp_V2").exists() and (parent / "rule_eval_mvp").exists():
            return parent
    raise RuntimeError("Could not locate repository root from skill script path.")


REPO_ROOT = find_repo_root()

PATHS = {
    "canonical_dataset": REPO_ROOT / "eval_mvp_V2" / "data" / "master_normalized_dataset_v2_final.jsonl",
    "canonical_stats": REPO_ROOT / "output" / "doc" / "v2_stats.json",
    "source_extractor": REPO_ROOT / "extract_whitepaper_datasets.py",
    "dataset_report_builder": REPO_ROOT / "build_v2_final_dataset_report.py",
    "main_eval_script": REPO_ROOT / "eval_mvp_V2" / "multi_model_rule_eval_prompt_only.py",
    "legacy_output_root": REPO_ROOT / "rule_eval_mvp" / "output",
    "delta_output_root": REPO_ROOT / "rule_eval_mvp" / "output_v2_delta",
    "merged_output_root": REPO_ROOT / "rule_eval_mvp" / "output_v2_merged",
    "authoritative_output_root": REPO_ROOT / "rule_eval_mvp" / "output_v2_final_authoritative",
    "incremental_rerun_script": REPO_ROOT / "rule_eval_mvp" / "incremental_tools" / "run_v2_incremental_eval.py",
    "append_only_script": REPO_ROOT / "rule_eval_mvp" / "incremental_tools" / "run_append_only_eval.py",
    "append_results_script": REPO_ROOT / "rule_eval_mvp" / "incremental_tools" / "append_new_dataset_results.py",
    "merge_script": REPO_ROOT / "rule_eval_mvp" / "incremental_tools" / "merge_v2_incremental_results.py",
    "rebuild_merged_script": REPO_ROOT / "rule_eval_mvp" / "incremental_tools" / "rebuild_merged_summaries.py",
    "publish_script": REPO_ROOT / "rule_eval_mvp" / "incremental_tools" / "publish_v2_final_authoritative_results.py",
    "deleted_ids": REPO_ROOT / "数据集" / "汇总" / "_processing_workspace" / "output" / "master_normalized_dataset_v2_deleted_ids.jsonl",
}


PHASE_METADATA: Dict[str, Dict[str, object]] = {
    "phase0": {
        "title": "Protocol Setup",
        "summary": "Lock taxonomy, metrics, models, and comparability before running work.",
        "artifacts": ["evaluation_protocol_card.md", "model_registry.json", "taxonomy_registry.yaml", "metric_registry.yaml"],
    },
    "phase1": {
        "title": "Source Intake",
        "summary": "Collect benchmark sources and whitepaper evidence.",
        "artifacts": ["dataset_catalog.json", "source_inventory.csv"],
    },
    "phase2": {
        "title": "Canonical Standardization",
        "summary": "Standardize membership and ensure canonical JSONL and stats stay consistent.",
        "artifacts": ["master_normalized_dataset_v2_final.jsonl", "dataset_sources_clean.csv", "v2_stats.json"],
    },
    "phase3": {
        "title": "Protocol Shaping",
        "summary": "Prepare prompt, scorer, and smoke subset.",
        "artifacts": ["test_eval_100_dataset_type_coverage.jsonl", "run_config.yaml"],
    },
    "phase4": {
        "title": "Smoke Validation",
        "summary": "Run self-test or a small evaluation subset before full execution.",
        "artifacts": ["progress.json", "summary.json", "summary.csv", "smoke_failures.md"],
    },
    "phase5": {
        "title": "Full Evaluation",
        "summary": "Run the main evaluation over the canonical dataset.",
        "artifacts": ["results.jsonl", "progress.json", "summary.json", "summary.csv", "leaderboard.csv"],
    },
    "phase6": {
        "title": "Incremental Rerun",
        "summary": "Rerun API-error or append-only deltas without mutating legacy output.",
        "artifacts": ["output_v2_delta/<model>/results.jsonl", "delta_run_summary.json"],
    },
    "phase7": {
        "title": "Merge And Publish",
        "summary": "Merge old and delta runs, clean to canonical IDs, and publish authoritative results.",
        "artifacts": ["merge_report.json", "deleted_audit.jsonl", "release_manifest.json", "distribution_report.md"],
    },
    "phase8": {
        "title": "Reporting",
        "summary": "Generate leaderboard, charts, DOCX, and whitepaper artifacts from authoritative outputs.",
        "artifacts": ["leaderboard.csv", "V2_final_dataset_report.docx", "whitepaper drafts"],
    },
    "phase9": {
        "title": "Agent Safety Extension",
        "summary": "Extend evaluation to trajectory-level agent safety while preserving taxonomy anchors.",
        "artifacts": ["agent_subset_catalog.json", "trajectory_eval_schema.yaml"],
    },
}


def configure_stdio() -> None:
    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        if stream is None:
            continue
        try:
            stream.reconfigure(encoding="utf-8")
        except AttributeError:
            pass


def path_check(name: str, path: Path, severity: str = "fatal") -> CheckResult:
    exists = path.exists()
    return CheckResult(
        name=name,
        status="pass" if exists else "fail",
        severity=severity,
        detail=str(path),
    )


def list_model_dirs(root: Path) -> List[str]:
    if not root.exists():
        return []
    return sorted(p.name for p in root.iterdir() if p.is_dir())


def phase_checks(phase: str, models: Optional[str]) -> List[CheckResult]:
    checks: List[CheckResult] = []
    if phase in {"phase1"}:
        checks.append(path_check("source_extractor_exists", PATHS["source_extractor"]))
        checks.append(path_check("standards_dir_exists", REPO_ROOT / "标准文件", "warning"))
    if phase in {"phase2", "phase3", "phase4", "phase5", "phase7", "phase8"}:
        checks.append(path_check("canonical_dataset_exists", PATHS["canonical_dataset"]))
    if phase in {"phase2", "phase7", "phase8"}:
        checks.append(path_check("canonical_stats_exists", PATHS["canonical_stats"], "warning"))
    if phase in {"phase4", "phase5"}:
        checks.append(path_check("main_eval_script_exists", PATHS["main_eval_script"]))
    if phase == "phase6":
        checks.append(path_check("legacy_output_root_exists", PATHS["legacy_output_root"], "warning"))
        checks.append(path_check("incremental_rerun_script_exists", PATHS["incremental_rerun_script"]))
        checks.append(path_check("append_only_script_exists", PATHS["append_only_script"], "warning"))
    if phase == "phase7":
        checks.append(path_check("merge_script_exists", PATHS["merge_script"]))
        checks.append(path_check("rebuild_merged_script_exists", PATHS["rebuild_merged_script"], "warning"))
        checks.append(path_check("publish_script_exists", PATHS["publish_script"]))
        checks.append(path_check("legacy_output_root_exists", PATHS["legacy_output_root"], "warning"))
        checks.append(path_check("delta_output_root_exists", PATHS["delta_output_root"], "warning"))
        checks.append(path_check("merged_output_root_exists", PATHS["merged_output_root"], "warning"))
        checks.append(path_check("deleted_ids_exists", PATHS["deleted_ids"], "warning"))
    if phase == "phase8":
        checks.append(path_check("authoritative_output_root_exists", PATHS["authoritative_output_root"], "warning"))
        checks.append(path_check("dataset_report_builder_exists", PATHS["dataset_report_builder"], "warning"))
    if phase == "phase9":
        checks.append(path_check("canonical_dataset_exists", PATHS["canonical_dataset"]))
    if models:
        checks.append(CheckResult(name="requested_models", status="info", severity="audit", detail=models))
    return checks


def phase_commands(phase: str, models: Optional[str]) -> List[str]:
    model_arg = models or "doubao_seed_2_0_pro_full"
    commands: List[str] = []
    if phase == "phase1":
        commands.append("python extract_whitepaper_datasets.py")
    elif phase == "phase0":
        commands.append("Copy templates from .codex/skills/llm-safety-eval-research-pipeline/assets/templates/ and fill protocol fields before execution")
    elif phase == "phase4":
        commands.append("python eval_mvp_V2/multi_model_rule_eval_prompt_only.py")
    elif phase == "phase5":
        commands.append("python eval_mvp_V2/multi_model_rule_eval_prompt_only.py")
    elif phase == "phase6":
        commands.append(
            "python rule_eval_mvp/incremental_tools/run_v2_incremental_eval.py "
            f"--base-script eval_mvp_V2/multi_model_rule_eval_prompt_only.py "
            f"--input-jsonl {PATHS['canonical_dataset']} "
            f"--run-root {PATHS['delta_output_root']} "
            f"--models {model_arg} --rerun-api-errors"
        )
        commands.append(
            "python rule_eval_mvp/incremental_tools/run_append_only_eval.py "
            f"--base-script eval_mvp_V2/multi_model_rule_eval_prompt_only.py "
            "--input-jsonl <new_dataset_jsonl> "
            f"--run-root {REPO_ROOT / 'rule_eval_mvp' / 'output_append_delta'} "
            f"--models {model_arg}"
        )
    elif phase == "phase7":
        commands.append(
            "python rule_eval_mvp/incremental_tools/merge_v2_incremental_results.py "
            f"--base-script eval_mvp_V2/multi_model_rule_eval_prompt_only.py "
            f"--base-run-root {PATHS['legacy_output_root']} "
            f"--delta-run-root {PATHS['delta_output_root']} "
            f"--merged-run-root {PATHS['merged_output_root']} "
            f"--deleted-ids {PATHS['deleted_ids']} "
            f"--v2-final-jsonl {PATHS['canonical_dataset']} "
            f"--models {model_arg}"
        )
        commands.append(
            "python rule_eval_mvp/incremental_tools/rebuild_merged_summaries.py "
            f"--base-script eval_mvp_V2/multi_model_rule_eval_prompt_only.py "
            f"--merged-run-root {PATHS['merged_output_root']} "
            f"--models {model_arg}"
        )
        commands.append(
            "python rule_eval_mvp/incremental_tools/publish_v2_final_authoritative_results.py "
            f"--base-script eval_mvp_V2/multi_model_rule_eval_prompt_only.py "
            f"--source-root {PATHS['merged_output_root']} "
            f"--publish-root {PATHS['authoritative_output_root']} "
            f"--canonical-dataset {PATHS['canonical_dataset']} "
            f"--models {model_arg}"
        )
    elif phase == "phase8":
        commands.append("python build_v2_final_dataset_report.py")
    return commands


def phase_notes(phase: str) -> List[str]:
    notes: Dict[str, List[str]] = {
        "phase2": [
            "Recompute or verify total counts directly from the canonical JSONL.",
            "Do not use report prose as the membership source.",
        ],
        "phase5": [
            "The main evaluation script uses in-file CONFIG, not a full CLI surface.",
            "Review INPUT_JSONL, RUN_ROOT, AGGREGATE_LATEST_BY_ID, and RERUN_API_ERRORS before execution.",
        ],
        "phase6": [
            "Delta execution must not overwrite the legacy output root.",
            "Append-only execution requires a new dataset JSONL path and should target output_append_delta.",
        ],
        "phase7": [
            "Publish must source from merged clean results only.",
            "Authoritative total must match the canonical dataset exactly.",
            "If deleted-id inventory changes, rerun merge before publishing.",
        ],
        "phase8": [
            "Every report number must trace back to canonical or authoritative artifacts.",
        ],
    }
    return notes.get(phase, [])


def build_phase_record(phase: str, models: Optional[str], emit_commands: bool) -> Dict[str, object]:
    metadata = PHASE_METADATA[phase]
    record: Dict[str, object] = {
        "phase": phase,
        "title": metadata["title"],
        "summary": metadata["summary"],
        "artifacts": metadata["artifacts"],
        "checks": [asdict(check) for check in phase_checks(phase, models)],
        "available_models": {
            "legacy": list_model_dirs(PATHS["legacy_output_root"]),
            "merged": list_model_dirs(PATHS["merged_output_root"]),
            "authoritative": list_model_dirs(PATHS["authoritative_output_root"]),
        },
        "notes": phase_notes(phase),
    }
    if emit_commands:
        record["commands"] = phase_commands(phase, models)
    return record


def render_text(plan: Dict[str, object]) -> str:
    lines: List[str] = []
    if plan.get("request"):
        lines.append(f"request: {plan['request']}")
    lines.append(f"repo_root: {plan['repo_root']}")
    lines.append(f"phases: {', '.join(plan['phases'])}")
    for phase_record in plan["phase_records"]:
        lines.append("")
        lines.append(f"[{phase_record['phase']}] {phase_record['title']}")
        lines.append(f"summary: {phase_record['summary']}")
        lines.append("checks:")
        for check in phase_record["checks"]:
            lines.append(f"- {check['name']} [{check['status']}/{check['severity']}]: {check['detail']}")
        if phase_record.get("commands"):
            lines.append("commands:")
            for command in phase_record["commands"]:
                lines.append(f"- {command}")
        if phase_record.get("notes"):
            lines.append("notes:")
            for note in phase_record["notes"]:
                lines.append(f"- {note}")
    return "\n".join(lines)


def main() -> int:
    configure_stdio()
    parser = argparse.ArgumentParser(description="Run minimal phase checks and command binding for this repository.")
    parser.add_argument("--request", help="Natural-language request to route into phases.")
    parser.add_argument("--phase", help="Single explicit phase such as phase7.")
    parser.add_argument("--models", help="Comma-separated model directories to bind into command hints.")
    parser.add_argument("--emit-commands", action="store_true", help="Include recommended command hints.")
    parser.add_argument("--check-only", action="store_true", help="Emit checks without command hints.")
    parser.add_argument("--format", choices=["text", "json"], default="text")
    args = parser.parse_args()

    if not args.request and not args.phase:
        parser.error("Provide either --request or --phase.")

    if args.phase:
        phases = [args.phase]
        request = None
    else:
        routed = route_request(args.request)
        phases = routed.phases
        request = args.request

    emit_commands = False if args.check_only else args.emit_commands
    phase_records = [build_phase_record(phase, args.models, emit_commands) for phase in phases]
    plan = {
        "repo_root": str(REPO_ROOT),
        "request": request,
        "phases": phases,
        "phase_records": phase_records,
    }

    if args.format == "json":
        print(json.dumps(plan, ensure_ascii=False, indent=2))
    else:
        print(render_text(plan))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
