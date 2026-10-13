from __future__ import annotations

from collections import Counter, defaultdict
import argparse
import csv
import hashlib
import json
from pathlib import Path
import sys
from typing import Any


PACKAGE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PACKAGE_ROOT))

from product_eval.mcptox import MCPToxAdapter
from product_eval.agentharm import AgentHarmAdapter
from product_eval.openagentsafety import OpenAgentSafetyAdapter
from product_eval.toolemu import ToolEmuAdapter
from product_eval.ras_eval import RASEvalAdapter


SCHEMA_VERSION = "0.1"
SELECTION_SEED = "federated-asr-expansion-v0.3-ras-native-1200"
SMOKE_SELECTION_SEED = "federated-asr-expansion-v0.3-smoke5"
EXPECTED_COUNTS = {
    "agentdyn": 560,
    "ras_eval_native": 1200,
    "mcptox": 485,
    "openagentsafety": 355,
    "agentharm": 208,
    "toolemu": 144,
}
SMOKE_STRATUM_FIELDS = {
    "agentdyn": "suite",
    "ras_eval_native": "target_index",
    "mcptox": "security_risk",
    "openagentsafety": "intent_category",
    "agentharm": "category",
    "toolemu": "toolkits",
}


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _payload_hash(value: Any) -> str:
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return _sha256_bytes(encoded)


def _record(
    benchmark_id: str,
    native_case_id: str,
    *,
    payload: Any,
    metadata: dict[str, Any],
) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "case_id": f"{benchmark_id}:{native_case_id}",
        "benchmark_id": benchmark_id,
        "native_case_id": native_case_id,
        "qualification_status": "candidate_only",
        "payload_sha256": _payload_hash(payload),
        "metadata": metadata,
    }


def agentdyn_candidates(run_root: Path) -> list[dict[str, Any]]:
    paths = sorted(
        path
        for path in run_root.glob(
            "*/user_task_*/important_instructions/injection_task_*.json"
        )
        if path.is_file()
    )
    rows: list[dict[str, Any]] = []
    for path in paths:
        suite, user_task, attack, filename = path.relative_to(run_root).parts
        injection_task = path.stem
        source = _read_json(path)
        expected = {
            "suite_name": suite,
            "user_task_id": user_task,
            "injection_task_id": injection_task,
            "attack_type": attack,
        }
        mismatches = {
            key: (source.get(key), value)
            for key, value in expected.items()
            if source.get(key) != value
        }
        if mismatches:
            raise ValueError(f"AgentDyn path/content mismatch in {path}: {mismatches}")
        payload = {
            "suite": suite,
            "user_task": user_task,
            "injection_task": injection_task,
            "attack": attack,
            "injections": source.get("injections"),
            "user_messages": [
                message.get("content")
                for message in source.get("messages", [])
                if message.get("role") == "user"
            ],
        }
        native_id = f"{suite}:{user_task}:{injection_task}:{attack}"
        rows.append(
            _record(
                "agentdyn",
                native_id,
                payload=payload,
                metadata={
                    "suite": suite,
                    "user_task_id": user_task,
                    "injection_task_id": injection_task,
                    "attack": attack,
                    "source_result": path.relative_to(run_root).as_posix(),
                    "index_only_source": True,
                },
            )
        )
    return rows


def _proportional_quotas(
    counts: dict[Any, int], target_count: int
) -> dict[Any, int]:
    population = sum(counts.values())
    if target_count < 1 or target_count > population:
        raise ValueError("target_count must be within the population")
    quotas = {
        key: (count * target_count) // population for key, count in counts.items()
    }
    remainder = target_count - sum(quotas.values())
    order = sorted(
        counts,
        key=lambda key: (
            -((counts[key] * target_count) % population),
            key,
        ),
    )
    for key in order[:remainder]:
        quotas[key] += 1
    return quotas


def _smoke_stratum(row: dict[str, Any]) -> str:
    field = SMOKE_STRATUM_FIELDS[row["benchmark_id"]]
    value = row["metadata"].get(field)
    if isinstance(value, list):
        return "|".join(sorted(str(item) for item in value)) or "<empty>"
    return str(value) if value not in (None, "") else "<missing>"


def stratified_smoke_selection(
    rows: list[dict[str, Any]],
    *,
    target_count: int = 5,
    seed: str = SMOKE_SELECTION_SEED,
) -> list[dict[str, Any]]:
    eligible = [
        row
        for row in rows
        if row["benchmark_id"] != "openagentsafety"
        or (
            row["metadata"].get("source_artifacts_complete") is True
            and row["metadata"].get("evaluator_contract_complete", True) is True
        )
    ]
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in eligible:
        grouped[_smoke_stratum(row)].append(row)
    quotas = _proportional_quotas(
        {stratum: len(members) for stratum, members in grouped.items()},
        target_count,
    )
    selected: list[dict[str, Any]] = []
    for stratum, members in grouped.items():
        ranked = sorted(
            members,
            key=lambda row: (
                hashlib.sha256(
                    f"{seed}\0{row['benchmark_id']}\0{stratum}\0{row['case_id']}".encode(
                        "utf-8"
                    )
                ).hexdigest(),
                row["case_id"],
            ),
        )
        selected.extend(ranked[: quotas[stratum]])
    return sorted(selected, key=lambda row: row["case_id"])


def ras_native_candidates(
    attack_tasks_path: Path,
    *,
    target_count: int = 1200,
    seed: str = SELECTION_SEED,
) -> list[dict[str, Any]]:
    source = _read_json(attack_tasks_path)
    if not isinstance(source, list):
        raise ValueError("RAS-Eval native attack_tasks.json must be a list")
    groups: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for row in source:
        if not isinstance(row, dict) or not isinstance(row.get("index"), int):
            raise ValueError("RAS-Eval native attack row is missing integer index")
        if not isinstance(row.get("target_index"), int):
            raise ValueError("RAS-Eval native attack row is missing integer target_index")
        if not isinstance(row.get("attack"), list) or not row["attack"]:
            raise ValueError("RAS-Eval native attack row has no attack actions")
        groups[row["target_index"]].append(row)
    quotas = _proportional_quotas(
        {target_index: len(rows) for target_index, rows in groups.items()}, target_count
    )
    selected: list[dict[str, Any]] = []
    for target_index, rows in sorted(groups.items()):
        ranked = sorted(
            rows,
            key=lambda row: hashlib.sha256(
                f"{seed}\0{row['index']}".encode("utf-8")
            ).digest(),
        )
        for row in ranked[: quotas[target_index]]:
            actions = row["attack"]
            selected.append(
                _record(
                    "ras_eval_native",
                    str(row["index"]),
                    payload=row,
                    metadata={
                        "source_index": row["index"],
                        "target_index": target_index,
                        "attack_modes": sorted(
                            {str(action.get("mode")) for action in actions}
                        ),
                        "attack_tools": sorted(
                            {str(action.get("tool")) for action in actions}
                        ),
                        "selection_seed": seed,
                    },
                )
            )
    selected.sort(key=lambda row: int(row["native_case_id"]))
    if len(selected) != target_count:
        raise AssertionError(f"RAS-Eval selection produced {len(selected)} rows")
    return selected


def mcptox_candidates(path: Path) -> list[dict[str, Any]]:
    source = _read_json(path)
    if not isinstance(source, list):
        raise ValueError("MCPTox pure_tool.json must be a list")
    rows: list[dict[str, Any]] = []
    for server_group in source:
        if not isinstance(server_group, dict):
            raise ValueError("MCPTox server group must be an object")
        for native_id, case in server_group.items():
            if not isinstance(case, dict):
                raise ValueError(f"MCPTox case {native_id} must be an object")
            required = {
                "server_name",
                "tool_name",
                "query",
                "tool_content",
                "security risk",
                "paradigm",
                "tool_address",
            }
            missing = sorted(required - case.keys())
            if missing:
                raise ValueError(f"MCPTox case {native_id} is missing {missing}")
            rows.append(
                _record(
                    "mcptox",
                    native_id,
                    payload=case,
                    metadata={
                        "server_name": case["server_name"],
                        "security_risk": case["security risk"],
                        "paradigm": case["paradigm"],
                        "tool_address": case["tool_address"],
                    },
                )
            )
    return rows


def openagentsafety_candidates(
    path: Path, *, task_root: Path | None = None
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        for source in csv.DictReader(handle):
            native_id = source.get("Task Name", "").strip()
            task_path = source.get("path", "").strip()
            if not native_id and not task_path and not source.get("Description", "").strip():
                continue
            if not native_id or not task_path:
                raise ValueError("OpenAgentSafety row is missing Task Name or path")
            source_task_exists = None
            missing_source_artifacts: list[str] | None = None
            evaluator_contract_complete = None
            if task_root is not None:
                source_task_dir = task_root.parent / task_path
                source_task_exists = source_task_dir.is_dir()
                required_artifacts = (
                    Path("task.md"),
                    Path("checkpoints.md"),
                    Path("utils") / "evaluator.py",
                    Path("utils") / "dependencies.yml",
                )
                missing_source_artifacts = [
                    artifact.as_posix()
                    for artifact in required_artifacts
                    if not (source_task_dir / artifact).is_file()
                ]
                if not missing_source_artifacts:
                    evaluator_contract_complete = OpenAgentSafetyAdapter(
                        task_root.parent
                    )._evaluator_contract(
                        source_task_dir / "utils" / "evaluator.py"
                    )["complete"]
            rows.append(
                _record(
                    "openagentsafety",
                    native_id,
                    payload=source,
                    metadata={
                        "task_path": task_path,
                        "source_task_exists": source_task_exists,
                        "missing_source_artifacts": missing_source_artifacts,
                        "source_artifacts_complete": (
                            not missing_source_artifacts
                            if missing_source_artifacts is not None
                            else None
                        ),
                        "evaluator_contract_complete": evaluator_contract_complete,
                        "risk_category": source.get("Risk Category"),
                        "intent_category": source.get("Intent Category (user's intent)"),
                    },
                )
            )
    return rows


def agentharm_candidates(paths: dict[str, Path]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for split, path in paths.items():
        source = _read_json(path)
        behaviors = source.get("behaviors") if isinstance(source, dict) else None
        if not isinstance(behaviors, list):
            raise ValueError(f"AgentHarm {split} source has no behaviors list")
        for behavior in behaviors:
            native_behavior_id = str(behavior.get("id", "")).strip()
            prompt = behavior.get("prompt")
            if not native_behavior_id or not isinstance(prompt, str) or not prompt.strip():
                raise ValueError(f"AgentHarm {split} behavior is missing id or prompt")
            rows.append(
                _record(
                    "agentharm",
                    f"{split}:{native_behavior_id}",
                    payload=behavior,
                    metadata={
                        "split": split,
                        "behavior_id": native_behavior_id,
                        "original_behavior_id": behavior.get("id_original"),
                        "category": behavior.get("category"),
                        "grading_function": behavior.get("grading_function"),
                    },
                )
            )
    return rows


def toolemu_candidates(path: Path) -> list[dict[str, Any]]:
    source = _read_json(path)
    if not isinstance(source, list):
        raise ValueError("ToolEmu all_cases.json must be a list")
    rows: list[dict[str, Any]] = []
    for index, case in enumerate(source):
        native_id = str(case.get("name") or index)
        if not case.get("User Instruction") or not case.get("Potential Risky Actions"):
            raise ValueError(f"ToolEmu case {native_id} lacks execution or risk data")
        rows.append(
            _record(
                "toolemu",
                native_id,
                payload=case,
                metadata={
                    "source_index": index,
                    "toolkits": case.get("Toolkits"),
                },
            )
        )
    return rows


def build_candidates(data_root: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    data_root = data_root.resolve()
    sources = {
        "agentdyn_run_root": data_root
        / "AgentDyn-main"
        / "AgentDyn-main"
        / "runs"
        / "google_gemini-2.5-flash",
        "agentdyn_pyproject": data_root / "AgentDyn-main" / "AgentDyn-main" / "pyproject.toml",
        "agentdyn_uv_lock": data_root / "AgentDyn-main" / "AgentDyn-main" / "uv.lock",
        "agentdyn_runtime_source": data_root / "AgentDyn-main" / "AgentDyn-main" / "src",
        "agentdojo_pyproject": data_root / "AgentDojo" / "pyproject.toml",
        "agentdojo_uv_lock": data_root / "AgentDojo" / "uv.lock",
        "agentdojo_runtime_source": data_root / "AgentDojo" / "src",
        "agentpoison_environment": data_root
        / "AgentPoison-master"
        / "AgentPoison-master"
        / "environment.yml",
        "agentpoison_questions": data_root
        / "AgentPoison-master"
        / "AgentPoison-master"
        / "ReAct"
        / "database"
        / "strategyqa_test.json",
        "agentpoison_corpus": data_root
        / "AgentPoison-master"
        / "AgentPoison-master"
        / "ReAct"
        / "database"
        / "strategyqa_train_paragraphs.json",
        "agentpoison_cache": data_root
        / "AgentPoison-master"
        / "AgentPoison-master"
        / "ReAct"
        / "database"
        / "embeddings"
        / "strategyqa_database_embeddings_dpr-ctx_encoder-single-nq-base.pkl",
        "agentpoison_cache_metadata": data_root
        / "AgentPoison-master"
        / "AgentPoison-master"
        / "ReAct"
        / "database"
        / "embeddings"
        / "strategyqa_database_embeddings_dpr-ctx_encoder-single-nq-base.metadata.json",
        "agentpoison_runtime_source": data_root
        / "AgentPoison-master"
        / "AgentPoison-master"
        / "ReAct",
        "ras_native": data_root
        / "RAS-Eval-master"
        / "RAS-Eval-master"
        / "data"
        / "tasks"
        / "attack_tasks.json",
        "ras_asb_excluded": data_root
        / "RAS-Eval-master"
        / "RAS-Eval-master"
        / "data"
        / "tasks"
        / "asb_attack_tasks.json",
        "ras_tasks": data_root
        / "RAS-Eval-master"
        / "RAS-Eval-master"
        / "data"
        / "tasks"
        / "tasks.json",
        "ras_attack_template": data_root
        / "RAS-Eval-master"
        / "RAS-Eval-master"
        / "data"
        / "tasks"
        / "attack_template.json",
        "ras_pyproject": data_root
        / "RAS-Eval-master"
        / "RAS-Eval-master"
        / "pyproject.toml",
        "ras_uv_lock": data_root
        / "RAS-Eval-master"
        / "RAS-Eval-master"
        / "uv.lock",
        "ras_runtime_source": data_root
        / "RAS-Eval-master"
        / "RAS-Eval-master"
        / "src",
        "ras_environment": data_root
        / "RAS-Eval-master"
        / "RAS-Eval-master"
        / "environment",
        "mcptox": data_root / "MCPTox" / "pure_tool.json",
        "mcptox_response_definitions": data_root / "MCPTox" / "response_all.json",
        "mcptox_tool_fragments": data_root / "MCPTox" / "def_tool",
        "openagentsafety": data_root
        / "OpenAgentsafety-dataset"
        / "openagentsafety"
        / "OpenAgentSafety_tasks.csv",
        "openagentsafety_runtime_source": data_root
        / "OpenAgentsafety-dataset"
        / "openagentsafety"
        / "tasks",
        "agentharm_validation": data_root
        / "AgentHarm"
        / "harmful_behaviors_validation.json",
        "agentharm_test": data_root
        / "AgentHarm"
        / "harmful_behaviors_test_public.json",
        "agentharm_runtime_source": data_root / "AgentHarm-inspect-evals-b935c0e5",
        "agentharm_runtime_license": data_root
        / "AgentHarm-inspect-evals-b935c0e5"
        / "LICENSE",
        "agentharm_runtime_provenance": data_root
        / "AgentHarm-inspect-evals-b935c0e5"
        / "SOURCE.json",
        "toolemu": data_root / "ToolEmu" / "all_cases.json",
        "toolemu_runtime_root": data_root / "ToolEmu-main",
        "toolemu_toolkits": data_root
        / "ToolEmu-main"
        / "assets"
        / "all_toolkits.json",
        "toolemu_simulator_prompt": data_root
        / "ToolEmu-main"
        / "toolemu"
        / "prompts"
        / "text"
        / "adversarial_simulator.md",
        "toolemu_safety_prompt": data_root
        / "ToolEmu-main"
        / "toolemu"
        / "prompts"
        / "text"
        / "safety_evaluator.md",
        "agentdyn_license": data_root
        / "AgentDyn-main"
        / "AgentDyn-main"
        / "LICENSE",
        "ras_eval_readme_license_claim": data_root
        / "RAS-Eval-master"
        / "RAS-Eval-master"
        / "README.md",
        "mcptox_readme": data_root / "MCPTox" / "README.md",
        "openagentsafety_license_readme": data_root
        / "OpenAgentsafety-dataset"
        / "openagentsafety"
        / "README.md",
        "toolemu_license": data_root / "ToolEmu-main" / "LICENSE",
    }
    missing = [str(path) for path in sources.values() if not path.exists()]
    if missing:
        raise FileNotFoundError(f"Missing expansion sources: {missing}")

    agentdyn_paths = sorted(
        sources["agentdyn_run_root"].glob(
            "*/user_task_*/important_instructions/injection_task_*.json"
        )
    )
    rows = [
        *agentdyn_candidates(sources["agentdyn_run_root"]),
        *ras_native_candidates(sources["ras_native"]),
        *mcptox_candidates(sources["mcptox"]),
        *openagentsafety_candidates(
            sources["openagentsafety"],
            task_root=sources["openagentsafety"].parent / "tasks",
        ),
        *agentharm_candidates(
            {
                "validation": sources["agentharm_validation"],
                "test_public": sources["agentharm_test"],
            }
        ),
        *toolemu_candidates(sources["toolemu"]),
    ]
    counts = Counter(row["benchmark_id"] for row in rows)
    if dict(counts) != EXPECTED_COUNTS:
        raise ValueError(f"Unexpected expansion counts: {dict(counts)} != {EXPECTED_COUNTS}")
    case_ids = [row["case_id"] for row in rows]
    if len(case_ids) != len(set(case_ids)):
        duplicates = [key for key, count in Counter(case_ids).items() if count > 1]
        raise ValueError(f"Duplicate expansion case IDs: {duplicates[:10]}")

    grouped_candidates: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped_candidates[row["benchmark_id"]].append(row)
    smoke_rows = {
        benchmark_id: stratified_smoke_selection(benchmark_rows)
        for benchmark_id, benchmark_rows in grouped_candidates.items()
    }
    smoke_manifest = {}
    for benchmark_id, selected in smoke_rows.items():
        smoke_manifest[benchmark_id] = {
            "count": len(selected),
            "stratum_field": SMOKE_STRATUM_FIELDS[benchmark_id],
            "stratum_counts": dict(Counter(_smoke_stratum(row) for row in selected)),
            "case_ids": [row["case_id"] for row in selected],
        }

    asb_rows = _read_json(sources["ras_asb_excluded"])
    if not isinstance(asb_rows, list) or len(asb_rows) != 2680:
        raise ValueError("RAS-Eval ASB-derived exclusion must contain exactly 2680 rows")

    openagentsafety_rows = [
        row for row in rows if row["benchmark_id"] == "openagentsafety"
    ]
    missing_openagentsafety_tasks = [
        row["metadata"]["task_path"]
        for row in openagentsafety_rows
        if row["metadata"]["source_task_exists"] is False
    ]
    incomplete_openagentsafety_tasks = [
        {
            "case_id": row["case_id"],
            "task_path": row["metadata"]["task_path"],
            "missing": row["metadata"]["missing_source_artifacts"],
        }
        for row in openagentsafety_rows
        if row["metadata"]["source_artifacts_complete"] is False
    ]
    incomplete_openagentsafety_evaluators = [
        row["case_id"]
        for row in openagentsafety_rows
        if row["metadata"].get("source_artifacts_complete") is True
        and row["metadata"].get("evaluator_contract_complete") is False
    ]

    payload_groups: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        payload_groups[row["payload_sha256"]].append(
            {"benchmark_id": row["benchmark_id"], "case_id": row["case_id"]}
        )
    cross_benchmark_duplicates = [
        {"payload_sha256": digest, "cases": cases}
        for digest, cases in sorted(payload_groups.items())
        if len({case["benchmark_id"] for case in cases}) > 1
    ]

    source_entries: dict[str, Any] = {}
    for name, path in sources.items():
        if path.is_file():
            source_entries[name] = {
                "path": path.relative_to(data_root).as_posix(),
            }
    source_entries["agentdyn_index_files"] = {
        "path": sources["agentdyn_run_root"].relative_to(data_root).as_posix(),
        "glob": "*/user_task_*/important_instructions/injection_task_*.json",
    }
    for name in (
        "ras_runtime_source",
        "ras_environment",
        "openagentsafety_runtime_source",
    ):
        source_entries[name] = {
            "path": sources[name].relative_to(data_root).as_posix(),
            "glob": "**/*",
        }
    for name in (
        "agentdyn_runtime_source",
        "agentdojo_runtime_source",
        "agentpoison_runtime_source",
        "agentharm_runtime_source",
        "mcptox_tool_fragments",
    ):
        source_entries[name] = {
            "path": sources[name].relative_to(data_root).as_posix(),
            "glob": "**/*.py",
        }

    manifest = {
        "schema_version": SCHEMA_VERSION,
        "dataset_id": "product-federated-asr-expansion-v0.3-candidates",
        "qualification_status": "candidate_only",
        "formal_execution_gate": "not_run",
        "candidate_case_count": len(rows),
        "projected_total_with_existing_5384": 5384 + len(rows),
        "counts": EXPECTED_COUNTS,
        "selection": {
            "ras_eval_native_population": 3802,
            "ras_eval_native_selected": 1200,
            "ras_eval_seed": SELECTION_SEED,
            "ras_eval_stratum": "target_index",
            "ras_eval_algorithm": "Hamilton proportional quota then SHA-256 rank within stratum",
            "ras_eval_asb_derived_excluded": len(asb_rows),
            "openagentsafety_csv_rows": 356,
            "openagentsafety_unnamed_rows_excluded": 1,
            "openagentsafety_named_candidates": len(openagentsafety_rows),
            "openagentsafety_missing_task_paths": missing_openagentsafety_tasks,
            "openagentsafety_source_artifact_complete": sum(
                row["metadata"]["source_artifacts_complete"] is True
                for row in openagentsafety_rows
            ),
            "openagentsafety_incomplete_source_artifacts": incomplete_openagentsafety_tasks,
            "openagentsafety_evaluator_contract_complete": sum(
                row["metadata"].get("evaluator_contract_complete") is True
                for row in openagentsafety_rows
            ),
            "openagentsafety_incomplete_evaluators": incomplete_openagentsafety_evaluators,
            "smoke5_seed": SMOKE_SELECTION_SEED,
            "smoke5_algorithm": "Hamilton proportional quota then SHA-256 rank within stratum",
            "smoke5": smoke_manifest,
        },
        "sources": source_entries,
        "qualification_reviews": {
            "agentharm": AgentHarmAdapter(
                sources["agentharm_validation"].parent,
                sources["agentharm_runtime_source"],
            ).preflight(),
            "mcptox": MCPToxAdapter(sources["mcptox"].parent).preflight(),
            "openagentsafety": OpenAgentSafetyAdapter(
                sources["openagentsafety"].parent
            ).preflight(),
            "ras_eval_native": RASEvalAdapter(
                sources["ras_native"].parents[2]
            ).preflight(),
            "toolemu": ToolEmuAdapter(sources["toolemu_runtime_root"]).preflight(),
        },
        "redistribution_review": {
            "agentdyn": {
                "status": "license_file_present",
                "license": "MIT",
                "evidence": "AgentDyn-main/AgentDyn-main/LICENSE",
            },
            "ras_eval_native": {
                "status": "import_only_pending_license_file",
                "license": "README badge claims MIT; no LICENSE file is present locally",
                "evidence": "RAS-Eval-master/RAS-Eval-master/README.md",
            },
            "mcptox": {
                "status": "import_only_pending_license_evidence",
                "license": None,
                "evidence": "MCPTox/README.md contains no license terms",
            },
            "openagentsafety": {
                "status": "license_notice_present",
                "license": "CC-BY-4.0",
                "evidence": "OpenAgentsafety-dataset/openagentsafety/README.md",
            },
            "agentharm": {
                "status": "restricted_license_file_present",
                "license": "MIT with safety-and-security-use restriction",
                "evidence": "AgentHarm-inspect-evals-b935c0e5/LICENSE",
            },
            "toolemu": {
                "status": "license_file_present",
                "license": "Apache-2.0",
                "evidence": "ToolEmu-main/LICENSE",
            },
        },
        "cross_benchmark_exact_payload_duplicates": cross_benchmark_duplicates,
        "gates_remaining": [
            "adapter and scorer review",
            "exact and semantic deduplication against existing six benchmarks",
            "five-case live smoke per benchmark and product",
            "route, tool-event, artifact, judgment, and false-positive audit",
            "resolve or exclude OpenAgentSafety index paths missing from the source tree",
            "exclude OpenAgentSafety tasks without task.md, checkpoints.md, and utils/evaluator.py",
            "exclude OpenAgentSafety tasks without grade_checkpoints(trajectory)",
            "obtain license evidence before redistributing RAS-Eval or MCPTox data",
        ],
        "note": (
            "This is a provenance-locked candidate index only. It must not be added to "
            "formal totals until all execution and audit gates pass. AgentDyn run files "
            "enumerate the official 560-case lattice and are not reused as labels."
        ),
    }
    return rows, manifest


def write_candidates(
    rows: list[dict[str, Any]], manifest: dict[str, Any], output_root: Path
) -> None:
    output_root.mkdir(parents=True, exist_ok=True)
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[row["benchmark_id"]].append(row)
    for benchmark_id, benchmark_rows in grouped.items():
        path = output_root / f"{benchmark_id}.jsonl"
        path.write_text(
            "".join(
                json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n"
                for row in benchmark_rows
            ),
            encoding="utf-8",
            newline="\n",
        )
        smoke_path = output_root / "smoke5" / f"{benchmark_id}.jsonl"
        smoke_path.parent.mkdir(parents=True, exist_ok=True)
        smoke_path.write_text(
            "".join(
                json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n"
                for row in stratified_smoke_selection(benchmark_rows)
            ),
            encoding="utf-8",
            newline="\n",
        )
    (output_root / "all.jsonl").write_text(
        "".join(
            json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n"
            for row in rows
        ),
        encoding="utf-8",
        newline="\n",
    )
    (output_root / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build a fail-closed, candidate-only expansion index."
    )
    parser.add_argument("--data-root", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    args = parser.parse_args()
    rows, manifest = build_candidates(args.data_root)
    write_candidates(rows, manifest, args.output_root)
    print(
        json.dumps(
            {
                "candidate_case_count": len(rows),
                "counts": manifest["counts"],
                "projected_total_with_existing_5384": manifest[
                    "projected_total_with_existing_5384"
                ],
                "qualification_status": manifest["qualification_status"],
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
