from __future__ import annotations

from collections import Counter
import argparse
import json
from pathlib import Path
from typing import Any, Iterable
import sys


PACKAGE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PACKAGE_ROOT))
from product_eval.data_paths import default_data_root
WORKSPACE_ROOT = PACKAGE_ROOT.parent
DATA_ROOT = default_data_root()

MIXED_MSB_ATTACKS = {
    "preference_manipulation-out_of_scope_parameter",
    "preference_manipulation-simulated_user",
    "prompt_injection-simulated_user",
    "prompt_injection-false_error",
}
RESTRICTED_MSB_PARTS = (
    "tool_transfer",
    "name_overlap",
    "out_of_scope_parameter",
    "preference_manipulation",
)


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open("r", encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def record(benchmark_id: str, native_case_id: str, **metadata: Any) -> dict[str, Any]:
    return {
        "schema_version": "0.1",
        "case_id": f"{benchmark_id}:{native_case_id}",
        "benchmark_id": benchmark_id,
        "native_case_id": native_case_id,
        "metadata": metadata,
    }


def injecagent_cases(source_files: list[Path]) -> Iterable[dict[str, Any]]:
    for attack_track, path in zip(("dh", "ds"), source_files, strict=True):
        rows = json.loads(path.read_text(encoding="utf-8"))
        for index, row in enumerate(rows):
            yield record(
                "injecagent",
                f"{attack_track}:{index}",
                attack_track=attack_track,
                attack_type=row.get("Attack Type"),
            )


def msb_tool_exists(msb_root: Path, agent_name: str, tool_name: str) -> bool:
    if agent_name == "image_generation":
        return False
    return (msb_root / "data" / "tools" / "attack_tools" / agent_name / tool_name).is_dir()


def msb_is_runnable(msb_root: Path, agent_name: str, tool_name: str, attack_type: str) -> bool:
    if (agent_name == "information_retrieval") != (attack_type == "search_term_deception"):
        return False
    restricted = any(part in attack_type for part in RESTRICTED_MSB_PARTS)
    return not restricted or msb_tool_exists(msb_root, agent_name, tool_name)


def msb_cases(msb_root: Path, source_files: list[Path]) -> Iterable[dict[str, Any]]:
    agents, attack_tasks, attack_type_rows = map(read_jsonl, source_files)
    attack_types = attack_type_rows[0]["attack_type"]
    for agent_index, agent in enumerate(agents, start=1):
        agent_name = agent["agent_name"]
        for task_index, task in enumerate(agent["task_tool"], start=1):
            for attack_task_index, attack_task in enumerate(attack_tasks, start=1):
                for attack_type_index, attack_type in enumerate(attack_types, start=1):
                    if not msb_is_runnable(msb_root, agent_name, task["tool"], attack_type):
                        continue
                    native_id = f"{agent_index}:{task_index}:{attack_task_index}:{attack_type_index}"
                    yield record(
                        "msb",
                        native_id,
                        agent_name=agent_name,
                        attack_task=attack_task["attack_task"],
                        attack_type=attack_type,
                        native_success_field=(
                            "mixed_attack_success" if attack_type in MIXED_MSB_ATTACKS else "attack_success"
                        ),
                    )


def agentdojo_cases(run_root: Path) -> Iterable[dict[str, Any]]:
    for path in sorted(run_root.glob("*/user_task_*/important_instructions/injection_task_*.json")):
        relative = path.relative_to(run_root)
        suite, user_task, attack, filename = relative.parts
        injection_task = Path(filename).stem
        native_id = f"{suite}:{user_task}:{injection_task}:{attack}"
        yield record(
            "agentdojo",
            native_id,
            suite=suite,
            user_task_id=user_task,
            injection_task_id=injection_task,
            attack=attack,
        )


def agentleak_cases(path: Path) -> Iterable[dict[str, Any]]:
    for row in read_jsonl(path):
        attack = row.get("attack") or {}
        if not attack.get("enabled"):
            continue
        yield record(
            "agentleak",
            row["scenario_id"],
            vertical=row.get("vertical"),
            attack_class=attack.get("attack_class"),
            attack_family=attack.get("attack_family"),
        )


def saber_cases(path: Path, tasks_root: Path) -> Iterable[dict[str, Any]]:
    manifest = json.loads(path.read_text(encoding="utf-8"))
    existing_tasks = {
        task_path.stem: task_path.relative_to(tasks_root)
        for task_path in tasks_root.glob("*/*/*.json")
    }
    for task_id in manifest["task_ids"]:
        relative = existing_tasks.get(task_id)
        if relative is None:
            continue
        scenario, category, _ = relative.parts
        yield record("saber", task_id, scenario=scenario, category=category)


def agentpoison_cases(path: Path) -> Iterable[dict[str, Any]]:
    rows = json.loads(path.read_text(encoding="utf-8"))
    for index, row in enumerate(rows):
        native_id = str(row.get("qid") or row.get("id") or index)
        yield record("agentpoison_strategyqa", native_id, source_index=index)


def main() -> None:
    global DATA_ROOT
    parser = argparse.ArgumentParser(description="Build the pinned product case index from external source data")
    parser.add_argument("--data-root", type=Path, default=DATA_ROOT)
    parser.add_argument("--output-dir", type=Path, default=PACKAGE_ROOT)
    args = parser.parse_args()
    DATA_ROOT = args.data_root.expanduser().resolve()
    sources = {
        "injecagent_dh": DATA_ROOT / "InjecAgent-main" / "data" / "test_cases_dh_base.json",
        "injecagent_ds": DATA_ROOT / "InjecAgent-main" / "data" / "test_cases_ds_base.json",
        "msb_agents": DATA_ROOT / "MSB-main" / "MSB-main" / "data" / "agent_task.jsonl",
        "msb_attack_tasks": DATA_ROOT / "MSB-main" / "MSB-main" / "data" / "attack_task.jsonl",
        "msb_attack_types": DATA_ROOT / "MSB-main" / "MSB-main" / "data" / "attack_type.jsonl",
        "agentleak": (
            DATA_ROOT
            / "AgentLeak-main"
            / "AgentLeak-main"
            / "agentleak_data"
            / "datasets"
            / "scenarios_full_1000.jsonl"
        ),
        "saber": DATA_ROOT / "saber-main" / "saber-main" / "dataset" / "manifest.json",
        "agentpoison": (
            DATA_ROOT
            / "AgentPoison-master"
            / "AgentPoison-master"
            / "ReAct"
            / "database"
            / "strategyqa_test.json"
        ),
    }
    missing = [str(path) for path in sources.values() if not path.is_file()]
    if missing:
        raise FileNotFoundError(f"Missing source files: {missing}")

    msb_root = DATA_ROOT / "MSB-main" / "MSB-main"
    dojo_run_root = DATA_ROOT / "AgentDojo" / "runs" / "claude-3-5-sonnet-20240620"
    rows = [
        *injecagent_cases([sources["injecagent_dh"], sources["injecagent_ds"]]),
        *msb_cases(
            msb_root,
            [sources["msb_agents"], sources["msb_attack_tasks"], sources["msb_attack_types"]],
        ),
        *agentdojo_cases(dojo_run_root),
        *agentleak_cases(sources["agentleak"]),
        *saber_cases(sources["saber"], DATA_ROOT / "saber-main" / "saber-main" / "tasks"),
        *agentpoison_cases(sources["agentpoison"]),
    ]
    ids = [row["case_id"] for row in rows]
    if len(ids) != len(set(ids)):
        raise ValueError("Duplicate case IDs detected")

    counts = Counter(row["benchmark_id"] for row in rows)
    expected = {
        "injecagent": 1054,
        "msb": 2000,
        "agentdojo": 629,
        "agentleak": 496,
        "saber": 715,
        "agentpoison_strategyqa": 490,
    }
    if dict(counts) != expected:
        raise ValueError(f"Unexpected benchmark counts: {dict(counts)} != {expected}")

    args.output_dir.mkdir(parents=True, exist_ok=True)
    output = args.output_dir / "case_index.jsonl"
    with output.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")

    manifest = {
        "schema_version": "0.1",
        "dataset_id": "product-federated-asr-v0.1",
        "case_count": len(rows),
        "counts": expected,
        "sources": {
            name: {"path": str(path.relative_to(DATA_ROOT)), "base": "AGENT_EVAL_DATA_ROOT"}
            for name, path in sources.items()
        },
        "agentdojo_index_source": str(dojo_run_root.relative_to(DATA_ROOT)),
        "saber_selection_audit": {
            "declared_manifest_count": 716,
            "selected_existing_task_count": 715,
            "excluded_missing_task_ids": ["A_persist_035"],
        },
        "note": "AgentDojo run files are used only to enumerate the complete pinned case lattice, not as labels.",
    }
    (args.output_dir / "case_index_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({"case_count": len(rows), "counts": expected}, ensure_ascii=False))


if __name__ == "__main__":
    main()
