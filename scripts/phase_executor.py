#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
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


def find_repo_root(explicit_root: Optional[Path] = None) -> Path:
    if explicit_root is not None:
        return explicit_root.expanduser().resolve()
    env_root = os.environ.get("LLM_SAFETY_EVAL_REPO_ROOT")
    if env_root:
        return Path(env_root).expanduser().resolve()

    candidates: List[Path] = []
    for start in (Path.cwd().resolve(), Path(__file__).resolve().parent):
        for candidate in (start, *start.parents):
            if candidate not in candidates:
                candidates.append(candidate)
    for candidate in candidates:
        canonical = candidate / "eval_mvp_V2" / "data" / "master_normalized_dataset_v2_final.jsonl"
        if canonical.exists() or (candidate / "protocol/evaluation_tracks.json").exists() or ((candidate / "eval_mvp_V2").exists() and (candidate / "rule_eval_mvp").exists()):
            return candidate
    return Path.cwd().resolve()


def build_paths(repo_root: Path) -> Dict[str, Path]:
    return {
        "canonical_dataset": repo_root / "eval_mvp_V2" / "data" / "master_normalized_dataset_v2_final.jsonl",
        "canonical_stats": repo_root / "output" / "doc" / "v2_stats.json",
        "source_intake": repo_root / "scripts" / "source_intake.py",
        "canonicalizer": repo_root / "scripts" / "canonicalize_datasets.py",
        "canonical_validator": repo_root / "scripts" / "canonical_validate.py",
        "phase3_preparer": repo_root / "scripts" / "prepare_phase3.py",
        "source_extractor": repo_root / "extract_whitepaper_datasets.py",
        "dataset_report_builder": repo_root / "build_v2_final_dataset_report.py",
        "main_eval_script": repo_root / "eval_mvp_V2" / "multi_model_rule_eval_prompt_only.py",
        "smoke_subset": repo_root / "eval_mvp_V2" / "data" / "test_eval_100_dataset_type_coverage.jsonl",
        "phase3_run_config": repo_root / "work" / "phase3" / "run_config.yaml",
        "legacy_output_root": repo_root / "rule_eval_mvp" / "output",
        "delta_output_root": repo_root / "rule_eval_mvp" / "output_v2_delta",
        "merged_output_root": repo_root / "rule_eval_mvp" / "output_v2_merged",
        "authoritative_output_root": repo_root / "rule_eval_mvp" / "output_v2_final_authoritative",
        "incremental_rerun_script": repo_root / "rule_eval_mvp" / "incremental_tools" / "run_v2_incremental_eval.py",
        "append_only_script": repo_root / "rule_eval_mvp" / "incremental_tools" / "run_append_only_eval.py",
        "append_results_script": repo_root / "rule_eval_mvp" / "incremental_tools" / "append_new_dataset_results.py",
        "merge_script": repo_root / "rule_eval_mvp" / "incremental_tools" / "merge_v2_incremental_results.py",
        "rebuild_merged_script": repo_root / "rule_eval_mvp" / "incremental_tools" / "rebuild_merged_summaries.py",
        "publish_script": repo_root / "rule_eval_mvp" / "incremental_tools" / "publish_v2_final_authoritative_results.py",
        "deleted_ids": repo_root / "数据集" / "汇总" / "_processing_workspace" / "output" / "master_normalized_dataset_v2_deleted_ids.jsonl",
    }


REPO_ROOT = Path.cwd().resolve()
PATHS = build_paths(REPO_ROOT)
PROTOCOL_DIR = REPO_ROOT / "protocol"
PROTOCOL_MANAGER = SCRIPT_DIR / "protocol_manager.py"


def configure_repository(repo_root: Path, protocol_dir: Optional[Path] = None) -> None:
    global REPO_ROOT, PATHS, PROTOCOL_DIR
    REPO_ROOT = repo_root.resolve()
    PATHS = build_paths(REPO_ROOT)
    PROTOCOL_DIR = (protocol_dir or (REPO_ROOT / "protocol")).expanduser().resolve()


PHASE_METADATA: Dict[str, Dict[str, object]] = {
    "phase0": {
        "title": "Protocol Setup",
        "summary": "Lock taxonomy, metrics, models, and comparability before running work.",
        "artifacts": ["evaluation_protocol_card.md", "model_registry.json", "taxonomy_registry.yaml", "metric_registry.yaml"],
    },
    "phase1": {
        "title": "Source Intake",
        "summary": "Collect benchmark sources and whitepaper evidence.",
        "artifacts": [
            "dataset_catalog.json",
            "source_inventory.csv",
            "evidence_inventory.jsonl",
            "reports/field_mapping_report.json",
            "reports/needs_review.json",
            "reports/failed_files.json",
            "manifests/pending/*.pending.yaml",
        ],
    },
    "phase2": {
        "title": "Canonical Standardization",
        "summary": "Standardize membership and ensure canonical JSONL and stats stay consistent.",
        "artifacts": ["master_normalized_dataset_v2_final.jsonl", "dataset_sources_clean.csv", "v2_stats.json", "membership_diff.json"],
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
        "title": "Agent Evaluation",
        "summary": "Run source-derived execution, full-product evaluation, or offline trace judging with native contracts.",
        "artifacts": ["manifest.json", "traces.jsonl", "results.jsonl", "summary.json"],
    },
    "phase10": {
        "title": "Failure Analysis And Evolution Planning",
        "summary": "Analyze native results and propose tests with evidence gates before dataset admission.",
        "artifacts": ["observations.jsonl", "failures.jsonl", "invalids.jsonl", "candidate_plans.jsonl", "summary.json"],
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


def phase_checks(phase: str, models: Optional[str], intake_config: Optional[Path]) -> List[CheckResult]:
    checks: List[CheckResult] = []
    if phase == "phase0":
        checks.append(path_check("protocol_manager_exists", PROTOCOL_MANAGER))
        if not PROTOCOL_DIR.exists():
            checks.append(path_check("protocol_dir_exists", PROTOCOL_DIR, "warning"))
        else:
            for filename in (
                "evaluation_protocol_card.md",
                "model_registry.json",
                "taxonomy_registry.yaml",
                "metric_registry.yaml",
            ):
                checks.append(path_check(f"{filename}_exists", PROTOCOL_DIR / filename))
            try:
                from protocol_manager import validate_protocol_dir

                verify_root = (
                    REPO_ROOT
                    if PATHS["canonical_dataset"].exists() or PATHS["main_eval_script"].exists()
                    else None
                )
                validation = validate_protocol_dir(PROTOCOL_DIR, verify_root)
                detail = "valid" if validation["valid"] else "; ".join(
                    f"{item['path']}: {item['message']}" for item in validation.get("errors", [])[:5]
                )
                check_name = "protocol_bound_valid" if verify_root is not None else "protocol_structure_valid"
                checks.append(CheckResult(check_name, "pass" if validation["valid"] else "fail", "fatal", detail))
            except Exception as exc:
                checks.append(CheckResult("protocol_validation", "fail", "fatal", str(exc)))
    if phase in {"phase1", "phase2"}:
        checks.append(path_check("canonical_schema_exists", PROTOCOL_DIR / "canonical_schema.yaml"))
    if phase in {"phase1"}:
        checks.append(path_check("source_intake_exists", PATHS["source_intake"]))
        if intake_config is not None:
            checks.append(path_check("intake_config_exists", intake_config))
    if phase == "phase2":
        checks.append(path_check("canonical_dataset_exists", PATHS["canonical_dataset"], "warning"))
    if phase in {"phase3", "phase4", "phase5", "phase7", "phase8"}:
        checks.append(path_check("canonical_dataset_exists", PATHS["canonical_dataset"]))
    if phase == "phase3":
        checks.append(path_check("phase3_preparer_exists", PATHS["phase3_preparer"]))
        checks.append(path_check("main_eval_script_exists", PATHS["main_eval_script"]))
    if phase == "phase4":
        checks.append(path_check("phase3_run_config_exists", PATHS["phase3_run_config"]))
        checks.append(path_check("smoke_subset_exists", PATHS["smoke_subset"]))
    if phase in {"phase2", "phase7", "phase8"}:
        checks.append(path_check("canonical_stats_exists", PATHS["canonical_stats"], "warning"))
    if phase == "phase2":
        checks.append(path_check("canonicalizer_exists", PATHS["canonicalizer"]))
        checks.append(path_check("canonical_validator_exists", PATHS["canonical_validator"]))
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
        checks.append(path_check("agent_harness_exists", REPO_ROOT / "harness/ash.py"))
        checks.append(path_check("product_executor_exists", REPO_ROOT / "federated_asr_benchmark/tools/run_product.py"))
        checks.append(path_check("track_registry_exists", PROTOCOL_DIR / "evaluation_tracks.json"))
    if phase == "phase10":
        checks.append(path_check("analysis_entry_exists", REPO_ROOT / "pipeline.py"))
        checks.append(path_check("result_analyzer_exists", REPO_ROOT / "scripts/result_analysis.py"))
    if models:
        checks.append(CheckResult(name="requested_models", status="info", severity="audit", detail=models))
    return checks


def phase_commands(phase: str, models: Optional[str], manifest: Optional[Path]) -> List[str]:
    model_arg = models or "doubao_seed_2_0_pro_full"
    intake_config_arg = f' --intake-config "{manifest}"' if manifest is not None else ""
    commands: List[str] = []
    if phase == "phase1":
        commands.append(
            f'python "{PATHS["source_intake"]}" --benchmark-root "<benchmark_root>" '
            f'--evidence-root "<evidence_root>"{intake_config_arg} '
            f'--output-dir "{REPO_ROOT / "work" / "phase1"}" --protocol-dir "{PROTOCOL_DIR}"'
        )
    elif phase == "phase2":
        commands.append(
            f'python "{PATHS["canonicalizer"]}" '
            f'--catalog "{REPO_ROOT / "work" / "phase1" / "dataset_catalog.json"}" '
            f'--output "{PATHS["canonical_dataset"]}" --stats "{PATHS["canonical_stats"]}" '
            f'--protocol-dir "{PROTOCOL_DIR}"'
        )
        commands.append(
            f'python "{PATHS["canonical_validator"]}" '
            f'--canonical "{PATHS["canonical_dataset"]}" --stats "{PATHS["canonical_stats"]}" '
            f'--protocol-dir "{PROTOCOL_DIR}"'
        )
    elif phase == "phase3":
        commands.append(
            f'python "{PATHS["phase3_preparer"]}" '
            f'--canonical "{PATHS["canonical_dataset"]}" '
            f'--output-dir "{REPO_ROOT / "work" / "phase3"}" '
            f'--smoke-output "{PATHS["smoke_subset"]}" '
            f'--run-config "{PATHS["phase3_run_config"]}" '
            f'--protocol-dir "{PROTOCOL_DIR}" '
            f'--sample-size 100'
        )
    elif phase == "phase0":
        if not PROTOCOL_DIR.exists():
            commands.append(f'python "{PROTOCOL_MANAGER}" init --output-dir "{PROTOCOL_DIR}"')
        commands.append(
            f'python "{PROTOCOL_MANAGER}" validate --protocol-dir "{PROTOCOL_DIR}" '
            f'--repo-root "{REPO_ROOT}" --format json'
        )
    elif phase == "phase4":
        commands.append(f'python "{PATHS["main_eval_script"]}" --config "{PATHS["phase3_run_config"]}"')
    elif phase == "phase5":
        commands.append(f'python "{PATHS["main_eval_script"]}" --config "<full_run_config.yaml>"')
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
    elif phase == "phase9":
        commands.append(f'python "{REPO_ROOT / "pipeline.py"}" inventory')
        commands.append(f'python "{REPO_ROOT / "pipeline.py"}" plan --track agent_execution --tasks "<public_tasks.jsonl>" --private-rules "<private_rules.jsonl>"')
    elif phase == "phase10":
        commands.append(f'python "{REPO_ROOT / "pipeline.py"}" analyze --track "<track>" --results "<results.jsonl>" --out "<new_analysis_dir>"')
    return commands


def phase_notes(phase: str) -> List[str]:
    notes: Dict[str, List[str]] = {
        "phase0": [
            "Freeze dataset identity, safety_taxonomy_8x19, rule_eval semantics, model versions, and comparability before evaluation.",
            "For the LLM QA V2 rule_eval track, refusal is a failure and is counted separately. Agent outcomes follow their own contracts.",
            "Do not store API keys or other secrets in protocol artifacts.",
        ],
        "phase2": [
            "Recompute or verify total counts directly from the canonical JSONL.",
            "Do not use report prose as the membership source.",
        ],
        "phase3": [
            "Generate run_config.yaml and the deterministic 100-row smoke subset before invoking the evaluator.",
            "Prompt and scorer changes should be represented in the Phase 3 protocol config, not by editing the evaluator body.",
        ],
        "phase4": [
            "Run the evaluator with work/phase3/run_config.yaml in self_test mode before any API evaluation.",
        ],
        "phase5": [
            "Use an external full-run config so INPUT_JSONL, RUN_ROOT, prompt, scorer, and model choices are auditable.",
            "Review AGGREGATE_LATEST_BY_ID and RERUN_API_ERRORS before execution.",
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
        "phase9": ["Agent data and scorers are separate from LLM QA canonical membership.", "Mock smoke does not establish official benchmark qualification."],
        "phase10": ["Failure reasons are observed signals, not proven root causes.", "Candidate plans remain unaccepted until all evidence gates pass."],
    }
    return notes.get(phase, [])


def build_phase_record(
    phase: str,
    models: Optional[str],
    emit_commands: bool,
    intake_config: Optional[Path] = None,
) -> Dict[str, object]:
    metadata = PHASE_METADATA[phase]
    record: Dict[str, object] = {
        "phase": phase,
        "title": metadata["title"],
        "summary": metadata["summary"],
        "artifacts": metadata["artifacts"],
        "checks": [asdict(check) for check in phase_checks(phase, models, intake_config)],
        "available_models": {
            "legacy": list_model_dirs(PATHS["legacy_output_root"]),
            "merged": list_model_dirs(PATHS["merged_output_root"]),
            "authoritative": list_model_dirs(PATHS["authoritative_output_root"]),
        },
        "notes": phase_notes(phase),
    }
    if emit_commands:
        record["commands"] = phase_commands(phase, models, intake_config)
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
    parser.add_argument("--phase", choices=sorted(PHASE_METADATA), help="Single explicit phase such as phase7.")
    parser.add_argument("--models", help="Comma-separated model directories to bind into command hints.")
    parser.add_argument("--intake-config", type=Path, help="Optional Phase 1 intake overrides YAML/JSON.")
    parser.add_argument("--repo-root", type=Path, help="Explicit host evaluation repository root.")
    parser.add_argument("--protocol-dir", type=Path, help="Phase 0 protocol directory; defaults to <repo-root>/protocol.")
    parser.add_argument("--emit-commands", action="store_true", help="Include recommended command hints.")
    parser.add_argument("--check-only", action="store_true", help="Emit checks without command hints.")
    parser.add_argument("--format", choices=["text", "json"], default="text")
    args = parser.parse_args()

    configure_repository(find_repo_root(args.repo_root), args.protocol_dir)

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
    phase_records = [
        build_phase_record(phase, args.models, emit_commands, args.intake_config) for phase in phases
    ]
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
    fatal_failures = [
        check
        for record in phase_records
        for check in record["checks"]
        if check["status"] == "fail" and check["severity"] == "fatal"
    ]
    return 1 if fatal_failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
