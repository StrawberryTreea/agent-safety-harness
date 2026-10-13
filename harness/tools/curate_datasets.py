from __future__ import annotations

import argparse
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


EXCLUDE_MARKERS = (
    "phone",
    "mobile",
    "robot",
    "robotic",
    "embodied",
    "smartlock",
    "smart_lock",
    "iot",
    "trafficdispatch",
    "household",
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Curate source benchmark files into a minimal harness-ready dataset pack.")
    parser.add_argument("--data-root", type=Path, default=Path("..") / "数据集")
    parser.add_argument("--out-name", default="规整数据")
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()

    data_root = args.data_root.resolve()
    source_root = data_root / "评测数据"
    output_root = data_root / args.out_name
    if not source_root.exists():
        raise SystemExit(f"Missing source directory: {source_root}")

    prepare_output_dir(data_root, output_root, args.overwrite)
    minimal_root = output_root / "source_minimal"
    tasks_root = output_root / "harness_tasks"
    minimal_root.mkdir(parents=True, exist_ok=True)
    tasks_root.mkdir(parents=True, exist_ok=True)

    manifest: dict[str, Any] = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "input_root": str(source_root),
        "output_root": str(output_root),
        "policy": {
            "scope": "computer_related_agent_safety",
            "raw_archives_kept_in_place": str(data_root / "原始数据"),
            "excluded_markers": list(EXCLUDE_MARKERS),
            "note": "Original expanded datasets are not modified. This directory is a minimal, reproducible working subset.",
        },
        "sources": {},
        "harness_tasks": {},
    }

    manifest["sources"]["AgentHarm"] = curate_agentharm(source_root, minimal_root)
    manifest["sources"]["InjecAgent"] = curate_injecagent(source_root, minimal_root)
    manifest["sources"]["R-Judge"] = curate_rjudge(source_root, minimal_root)
    manifest["sources"]["ToolEmu"] = curate_toolemu(source_root, minimal_root)
    manifest["sources"]["AgentLeak"] = curate_agentleak(source_root, minimal_root)
    manifest["sources"]["AgentDojo"] = curate_agentdojo(source_root, minimal_root)

    all_tasks: list[dict[str, Any]] = []
    all_tasks.extend(build_agentharm_tasks(source_root))
    all_tasks.extend(build_injecagent_tasks(source_root))
    all_tasks.extend(build_rjudge_tasks(source_root))
    all_tasks.extend(build_toolemu_tasks(source_root))
    all_tasks.extend(build_agentleak_tasks(source_root))

    grouped: dict[str, list[dict[str, Any]]] = {}
    for task in all_tasks:
        grouped.setdefault(task["benchmark"], []).append(task)

    for benchmark, tasks in sorted(grouped.items()):
        out_path = tasks_root / f"{benchmark}.tasks.jsonl"
        write_jsonl(out_path, tasks)
        manifest["harness_tasks"][benchmark] = {
            "path": str(out_path.relative_to(output_root)),
            "records": len(tasks),
        }

    execution_tasks = [task for task in all_tasks if task["benchmark"] != "r_judge"]
    trace_judge_tasks = [task for task in all_tasks if task["benchmark"] == "r_judge"]
    execution_path = tasks_root / "agent_execution_computer_safety.tasks.jsonl"
    trace_judge_path = tasks_root / "trace_judge_computer_safety.tasks.jsonl"
    write_jsonl(execution_path, execution_tasks)
    write_jsonl(trace_judge_path, trace_judge_tasks)
    manifest["harness_tasks"]["agent_execution_computer_safety"] = {
        "path": str(execution_path.relative_to(output_root)),
        "records": len(execution_tasks),
        "run_command": "ash.py run",
    }
    manifest["harness_tasks"]["trace_judge_computer_safety"] = {
        "path": str(trace_judge_path.relative_to(output_root)),
        "records": len(trace_judge_tasks),
        "run_command": "ash.py judge",
    }

    all_path = tasks_root / "all_computer_agent_safety.tasks.jsonl"
    write_jsonl(all_path, all_tasks)
    manifest["harness_tasks"]["all_computer_agent_safety"] = {
        "path": str(all_path.relative_to(output_root)),
        "records": len(all_tasks),
        "note": "inventory aggregate; use command-specific aggregate files for actual runs",
    }

    (output_root / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    (output_root / "README.md").write_text(render_readme(manifest), encoding="utf-8")

    print(json.dumps({"output_root": str(output_root), "tasks": len(all_tasks)}, ensure_ascii=False, indent=2))


def prepare_output_dir(data_root: Path, output_root: Path, overwrite: bool) -> None:
    resolved_data = data_root.resolve()
    resolved_output = output_root.resolve()
    if resolved_output == resolved_data or resolved_data not in resolved_output.parents:
        raise SystemExit(f"Refusing to write outside data root: {resolved_output}")
    if output_root.exists():
        if not overwrite:
            raise SystemExit(f"Output exists; pass --overwrite to rebuild: {output_root}")
        shutil.rmtree(output_root)
    output_root.mkdir(parents=True, exist_ok=True)


def copy_files(source_root: Path, minimal_root: Path, source_name: str, rel_paths: list[str]) -> dict[str, Any]:
    copied: list[str] = []
    missing: list[str] = []
    for rel in rel_paths:
        src = source_root / rel
        dst = minimal_root / rel
        if not src.exists():
            missing.append(rel)
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        copied.append(str((Path(source_name) / rel).as_posix()))
    return {"status": "ok" if copied else "missing", "copied_files": len(copied), "missing_files": missing}


def copy_tree_filtered(src: Path, dst: Path, exclude_dirs: set[str] | None = None) -> int:
    if not src.exists():
        return 0
    exclude_dirs = exclude_dirs or set()
    count = 0
    for path in src.rglob("*"):
        if not path.is_file():
            continue
        if any(part in exclude_dirs for part in path.relative_to(src).parts):
            continue
        target = dst / path.relative_to(src)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)
        count += 1
    return count


def curate_agentharm(source_root: Path, minimal_root: Path) -> dict[str, Any]:
    rels = [str(p.relative_to(source_root)) for p in sorted((source_root / "AgentHarm").glob("*.json"))]
    return copy_files(source_root, minimal_root, "AgentHarm", rels)


def curate_injecagent(source_root: Path, minimal_root: Path) -> dict[str, Any]:
    rels = [
        "InjecAgent-main/README.md",
        "InjecAgent-main/LICENCE",
        "InjecAgent-main/data/attacker_cases_dh.jsonl",
        "InjecAgent-main/data/attacker_cases_ds.jsonl",
        "InjecAgent-main/data/attacker_simulated_responses.json",
        "InjecAgent-main/data/test_cases_dh_base.json",
        "InjecAgent-main/data/test_cases_dh_enhanced.json",
        "InjecAgent-main/data/test_cases_ds_base.json",
        "InjecAgent-main/data/test_cases_ds_enhanced.json",
        "InjecAgent-main/data/tools.json",
        "InjecAgent-main/data/user_cases.jsonl",
    ]
    return copy_files(source_root, minimal_root, "InjecAgent", rels)


def curate_rjudge(source_root: Path, minimal_root: Path) -> dict[str, Any]:
    base = source_root / "R-Judge-main"
    rels = ["R-Judge-main/README.md", "R-Judge-main/config/data_schema.json"]
    for path in sorted((base / "data").glob("*/*.json")):
        rel = path.relative_to(source_root)
        if should_exclude_path(rel):
            continue
        rels.append(str(rel))
    result = copy_files(source_root, minimal_root, "R-Judge", rels)
    result["excluded_rule"] = "Dropped phone/mobile/IoT-oriented files from harness-ready subset."
    return result


def curate_toolemu(source_root: Path, minimal_root: Path) -> dict[str, Any]:
    rels = [
        "ToolEmu-main/README.md",
        "ToolEmu-main/LICENSE",
        "ToolEmu-main/assets/all_cases.json",
        "ToolEmu-main/assets/all_toolkits.json",
        "ToolEmu-main/assets/for_generation/tool_taxonomy.json",
        "ToolEmu-main/assets/for_generation/toolkit_risks.json",
        "ToolEmu-main/toolemu/prompts/text/safety_evaluator.md",
        "ToolEmu-main/toolemu/prompts/text/helpfulness_evaluator.md",
    ]
    return copy_files(source_root, minimal_root, "ToolEmu", rels)


def curate_agentleak(source_root: Path, minimal_root: Path) -> dict[str, Any]:
    rels = [
        "AgentLeak-main/README.md",
        "AgentLeak-main/LICENSE",
        "AgentLeak-main/pyproject.toml",
    ]
    dataset_dir = source_root / "AgentLeak-main" / "agentleak_data" / "datasets"
    example_dir = source_root / "AgentLeak-main" / "agentleak_data" / "examples"
    rels.extend(str(p.relative_to(source_root)) for p in sorted(dataset_dir.glob("*.json*")))
    rels.extend(str(p.relative_to(source_root)) for p in sorted(example_dir.glob("*.jsonl")))
    return copy_files(source_root, minimal_root, "AgentLeak", rels)


def curate_agentdojo(source_root: Path, minimal_root: Path) -> dict[str, Any]:
    src = source_root / "agentdojo-main"
    dst = minimal_root / "AgentDojo" / "agentdojo-main"
    copied = 0
    copied += copy_tree_filtered(src / "src" / "agentdojo" / "data" / "suites", dst / "src" / "agentdojo" / "data" / "suites")
    copied += copy_tree_filtered(src / "src" / "agentdojo" / "default_suites", dst / "src" / "agentdojo" / "default_suites")
    for rel in [
        "README.md",
        "LICENSE",
        "pyproject.toml",
        "src/agentdojo/base_tasks.py",
        "src/agentdojo/functions_runtime.py",
        "src/agentdojo/types.py",
        "src/agentdojo/models.py",
        "src/agentdojo/yaml_loader.py",
        "src/agentdojo/strenum.py",
        "src/agentdojo/task_suite/load_suites.py",
        "src/agentdojo/task_suite/task_suite.py",
        "src/agentdojo/task_suite/task_combinators.py",
    ]:
        path = src / rel
        if path.exists():
            target = dst / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)
            copied += 1
    return {
        "status": "ok" if copied else "missing",
        "copied_files": copied,
        "excluded_rule": "Dropped runs, docs, tests, notebooks, CI, and historical model outputs.",
        "harness_tasks": "not generated yet; AgentDojo is code-defined and should be imported from task suites.",
    }


def build_agentharm_tasks(source_root: Path) -> list[dict[str, Any]]:
    tasks: list[dict[str, Any]] = []
    for path in sorted((source_root / "AgentHarm").glob("*.json")):
        payload = read_json(path)
        records = payload.get("behaviors", []) if isinstance(payload, dict) else payload
        if not isinstance(records, list):
            continue
        kind = "benign" if "benign" in path.name else "chat" if "chat" in path.name else "harmful"
        for index, row in enumerate(records, start=1):
            if not isinstance(row, dict):
                continue
            prompt = row.get("prompt")
            if not prompt:
                continue
            tasks.append(
                {
                    "task_id": f"agentharm:{path.stem}:{row.get('id', index)}",
                    "benchmark": "agentharm",
                    "profile": "benign_agent_task" if kind == "benign" else "harmful_chat_task" if kind == "chat" else "harmful_agent_task",
                    "user_goal": prompt,
                    "attack_goal": None if kind == "benign" else prompt,
                    "tools": [{"name": str(t), "type": "function", "risk": "medium" if kind == "benign" else "high"} for t in as_list(row.get("target_functions"))],
                    "initial_state": {},
                    "labels": {
                        "expected_behavior": "complete" if kind == "benign" else "refuse",
                        "safety_category": "benign" if kind == "benign" else "harmful_action",
                        "risk_type": row.get("category"),
                        "source_split": path.stem,
                    },
                    "metadata": provenance("AgentHarm", path.name, index, row),
                }
            )
    return tasks


def build_injecagent_tasks(source_root: Path) -> list[dict[str, Any]]:
    tasks: list[dict[str, Any]] = []
    data_dir = source_root / "InjecAgent-main" / "data"
    for path in sorted(data_dir.glob("test_cases_*.json")):
        records = read_json(path)
        if not isinstance(records, list):
            continue
        for index, row in enumerate(records, start=1):
            if not isinstance(row, dict) or should_exclude_record(row):
                continue
            user_goal = row.get("User Instruction")
            if not user_goal:
                continue
            attacker_instruction = row.get("Attacker Instruction")
            tasks.append(
                {
                    "task_id": f"injecagent:{path.stem}:{index:05d}",
                    "benchmark": "injecagent",
                    "profile": "prompt_injection_tool_env",
                    "user_goal": user_goal,
                    "attack_goal": row.get("Expected Achievements") or attacker_instruction,
                    "tools": normalize_tool_names(as_list(row.get("User Tool")) + as_list(row.get("Attacker Tools"))),
                    "initial_state": {
                        "tool_parameters": row.get("Tool Parameters"),
                        "tool_response": row.get("Tool Response"),
                        "attacker_instruction": attacker_instruction,
                    },
                    "labels": {
                        "expected_behavior": "complete",
                        "safety_category": "indirect_prompt_injection",
                        "risk_type": row.get("Attack Type"),
                        "source_split": path.stem,
                    },
                    "metadata": provenance("InjecAgent", path.name, index, row),
                }
            )
    return tasks


def build_rjudge_tasks(source_root: Path) -> list[dict[str, Any]]:
    tasks: list[dict[str, Any]] = []
    base = source_root / "R-Judge-main" / "data"
    for path in sorted(base.glob("*/*.json")):
        if should_exclude_path(path.relative_to(source_root)):
            continue
        records = read_json(path)
        if not isinstance(records, list):
            continue
        for index, row in enumerate(records, start=1):
            if not isinstance(row, dict) or should_exclude_record(row):
                continue
            contents = row.get("contents")
            user_goal = first_user_message(contents) or row.get("profile") or row.get("goal")
            tasks.append(
                {
                    "task_id": f"r_judge:{path.parent.name.lower()}:{path.stem}:{row.get('id', index)}",
                    "benchmark": "r_judge",
                    "profile": "trajectory_judge",
                    "user_goal": str(user_goal),
                    "attack_goal": row.get("risk_description"),
                    "tools": [],
                    "initial_state": {},
                    "labels": {
                        "expected_behavior": "judge",
                        "safety_category": "trace_safety_judge",
                        "risk_type": row.get("attack_type") or row.get("scenario"),
                        "safety_violation": bool(row.get("label")),
                        "source_split": f"{path.parent.name}/{path.name}",
                    },
                    "metadata": {**provenance("R-Judge", str(path.relative_to(source_root)), index, row), "trace": normalize_rjudge_trace(contents)},
                }
            )
    return tasks


def build_toolemu_tasks(source_root: Path) -> list[dict[str, Any]]:
    cases_path = source_root / "ToolEmu-main" / "assets" / "all_cases.json"
    toolkits_path = source_root / "ToolEmu-main" / "assets" / "all_toolkits.json"
    cases = read_json(cases_path) if cases_path.exists() else []
    toolkits = {row.get("toolkit"): row for row in read_json(toolkits_path) if isinstance(row, dict)} if toolkits_path.exists() else {}
    tasks: list[dict[str, Any]] = []
    if not isinstance(cases, list):
        return tasks
    for index, row in enumerate(cases, start=1):
        if not isinstance(row, dict) or should_exclude_record(row):
            continue
        user_goal = row.get("User Instruction")
        if not user_goal:
            continue
        toolkit_names = as_list(row.get("Toolkits"))
        tools = []
        for name in toolkit_names:
            toolkit = toolkits.get(name)
            if toolkit and isinstance(toolkit.get("tools"), list):
                tools.extend({"name": f"{name}.{tool.get('name', 'unknown')}", "type": "function", "risk": "medium"} for tool in toolkit["tools"] if isinstance(tool, dict))
            else:
                tools.append({"name": str(name), "type": "toolkit", "risk": "medium"})
        tasks.append(
            {
                "task_id": f"toolemu:{row.get('name', index)}",
                "benchmark": "toolemu",
                "profile": "tool_use_emulated_sandbox",
                "user_goal": user_goal,
                "attack_goal": join_text(row.get("Potential Risky Outcomes")),
                "tools": tools,
                "initial_state": {"underspecifications": row.get("Underspecifications")},
                "labels": {
                    "expected_behavior": "complete",
                    "safety_category": "tool_risk_emulation",
                    "risk_type": infer_tool_risk(row),
                },
                "metadata": provenance("ToolEmu", cases_path.name, index, row),
            }
        )
    return tasks


def build_agentleak_tasks(source_root: Path) -> list[dict[str, Any]]:
    tasks: list[dict[str, Any]] = []
    data_dir = source_root / "AgentLeak-main" / "agentleak_data" / "datasets"
    for path in sorted(data_dir.glob("scenarios_*.jsonl")):
        for index, row in enumerate(read_jsonl(path), start=1):
            if not isinstance(row, dict) or should_exclude_record(row):
                continue
            objective = row.get("objective") if isinstance(row.get("objective"), dict) else {}
            user_goal = objective.get("user_request") or row.get("scenario_id")
            tasks.append(
                {
                    "task_id": f"agentleak:{row.get('scenario_id', f'{path.stem}_{index}')}",
                    "benchmark": "agentleak",
                    "profile": "multi_agent_privacy_trace",
                    "user_goal": user_goal,
                    "attack_goal": stringify(row.get("attack")),
                    "tools": normalize_tool_names(as_list(row.get("tools"))),
                    "initial_state": {
                        "agents": row.get("agents"),
                        "private_vault": row.get("private_vault"),
                        "allowed_set": row.get("allowed_set"),
                    },
                    "labels": {
                        "expected_behavior": "complete",
                        "safety_category": "privacy_data_flow",
                        "risk_type": row.get("vertical") or "privacy_leak",
                        "source_split": path.stem,
                    },
                    "metadata": provenance("AgentLeak", path.name, index, row),
                }
            )
    return tasks


def should_exclude_path(path: Path) -> bool:
    parts = [part.lower() for part in path.parts]
    joined = "/".join(parts)
    return any(marker in joined for marker in EXCLUDE_MARKERS)


def should_exclude_record(record: dict[str, Any]) -> bool:
    surface = json.dumps(record, ensure_ascii=False).lower()
    return any(marker in surface for marker in EXCLUDE_MARKERS)


def provenance(source_dataset: str, source_name: str, index: int, raw: dict[str, Any]) -> dict[str, Any]:
    return {
        "harness_dataset_role": "source_derived_task",
        "source_dataset": source_dataset,
        "source_name": source_name,
        "source_record_index": index,
        "raw": raw,
    }


def normalize_tool_names(values: list[Any]) -> list[dict[str, Any]]:
    tools: list[dict[str, Any]] = []
    for value in values:
        if isinstance(value, dict):
            name = value.get("name") or value.get("toolkit") or value.get("id") or value.get("tool")
        else:
            name = value
        if name:
            tools.append({"name": str(name), "type": "function", "risk": "medium"})
    return tools


def first_user_message(contents: Any) -> str | None:
    if isinstance(contents, list):
        for conversation in contents:
            if isinstance(conversation, list):
                for event in conversation:
                    if isinstance(event, dict) and event.get("role") == "user" and event.get("content"):
                        return str(event["content"])
    return None


def normalize_rjudge_trace(contents: Any) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    conversations = contents if isinstance(contents, list) else []
    for conversation in conversations:
        if not isinstance(conversation, list):
            continue
        for index, event in enumerate(conversation, start=1):
            if not isinstance(event, dict):
                continue
            events.append(
                {
                    "event_id": str(index),
                    "role": event.get("role", "unknown"),
                    "event_type": "message",
                    "content": {
                        "text": event.get("content"),
                        "thought": event.get("thought"),
                        "action": event.get("action"),
                    },
                }
            )
    return events


def infer_tool_risk(row: dict[str, Any]) -> str:
    text = " ".join(str(item) for item in as_list(row.get("Potential Risky Outcomes"))).lower()
    if "privacy" in text or "sensitive" in text or "credential" in text:
        return "data_leak"
    if "loss" in text or "delete" in text:
        return "data_loss"
    if "financial" in text or "money" in text:
        return "financial_loss"
    return "unsafe_tool_use"


def as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def join_text(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, list):
        return "\n".join(str(item) for item in value)
    return str(value)


def stringify(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def read_jsonl(path: Path) -> list[Any]:
    rows: list[Any] = []
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def safe_relpath(path: Path, root: Path) -> str:
    try:
        return str(path.relative_to(root))
    except ValueError:
        return path.name


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def render_readme(manifest: dict[str, Any]) -> str:
    source_lines = []
    for name, info in manifest["sources"].items():
        source_lines.append(f"- {name}: {info.get('copied_files', 0)} files; status={info.get('status')}")
    task_lines = []
    for name, info in manifest["harness_tasks"].items():
        task_lines.append(f"- `{info['path']}`: {info['records']} records")
    return "\n".join(
        [
            "# 规整数据",
            "",
            "这个目录由 `harness/tools/curate_datasets.py` 生成，用于保存精简后的 agent 安全评测工作集。",
            "",
            "原则：",
            "- `原始数据` 只保存下载归档，不改动。",
            "- `评测数据` 只保存展开后的上游项目，不改动。",
            "- `规整数据/source_minimal` 只保留构造我们内部 benchmark 需要的数据和少量必要源码。",
            "- `规整数据/harness_tasks` 是统一 JSONL 任务，可直接接入 harness。",
            "- 当前任务集过滤了明确的 phone / mobile / IoT / robotics / embodied 场景。",
            "",
            "## 精简源文件",
            *source_lines,
            "",
            "## 统一任务文件",
            *task_lines,
            "",
            "重建命令：",
            "",
            "```powershell",
            "# From the repository root, using the external source/curation data parent:",
            "python harness/tools/curate_datasets.py --data-root <data-parent> --overwrite",
            "```",
            "",
        ]
    )


if __name__ == "__main__":
    main()
