from __future__ import annotations

import argparse
import ast
import csv
import json
import re
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from agent_safety_harness.schema import TaskSpec

COMPUTER_EXCLUDE_MARKERS = ("phone", "mobile", "robot", "robotic", "embodied", "iot", "smartlock")


def main() -> None:
    parser = argparse.ArgumentParser(description="Curate mixed source datasets into a unified agent-safety benchmark pack.")
    parser.add_argument("--data-root", type=Path, required=True)
    parser.add_argument("--out-root", type=Path, required=True)
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()

    data_root = args.data_root.resolve()
    out_root = args.out_root.resolve()
    if not data_root.exists():
        raise SystemExit(f"Missing data root: {data_root}")
    if out_root.exists():
        if not args.overwrite:
            raise SystemExit(f"Output exists: {out_root}")
        shutil.rmtree(out_root)
    out_root.mkdir(parents=True, exist_ok=True)

    builders = [
        build_agent_hazard,
        build_agent_safetybench,
        build_openagentsafety,
        build_cuaharm,
        build_mcptox,
        build_st_webagentbench,
        build_ras_eval,
        build_msb,
        build_wasp,
        build_agentleak,
        build_agentdojo,
        build_agentdyn,
        build_agentlab,
        build_saber,
        build_agentharm,
        build_injecagent,
        build_toolemu,
        build_rjudge,
    ]

    by_source: dict[str, list[dict[str, Any]]] = {}
    rejected: list[dict[str, Any]] = []
    notes: list[str] = []
    for builder in builders:
        result = builder(data_root)
        by_source[result["source"]] = result["records"]
        rejected.extend(result["rejected"])
        notes.extend(result["notes"])

    all_records = dedupe([record for records in by_source.values() for record in records])
    by_benchmark: dict[str, list[dict[str, Any]]] = {}
    for record in all_records:
        by_benchmark.setdefault(record["benchmark"], []).append(record)

    tasks_root = out_root / "harness_tasks"
    tasks_root.mkdir(parents=True, exist_ok=True)
    write_jsonl(tasks_root / "all_computer_agent_safety.tasks.jsonl", all_records)
    write_jsonl(tasks_root / "agent_execution_computer_safety.tasks.jsonl", [r for r in all_records if r["profile"] != "trajectory_judge"])
    write_jsonl(tasks_root / "trace_judge_computer_safety.tasks.jsonl", [r for r in all_records if r["profile"] == "trajectory_judge"])
    for source, records in sorted(by_source.items()):
        write_jsonl(tasks_root / "by_source" / f"{source}.tasks.jsonl", records)

    manifest = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "data_root": str(data_root),
        "out_root": str(out_root),
        "scope": "computer_related_agent_safety_only",
        "counts": {"all": len(all_records), "by_benchmark": {k: len(v) for k, v in sorted(by_benchmark.items())}},
        "sources": {source: len(records) for source, records in sorted(by_source.items())},
        "notes": sorted(set(notes)),
    }
    inventory = {
        "created_at": manifest["created_at"],
        "included_sources": {source: len(records) for source, records in sorted(by_source.items())},
        "rejected_count": len(rejected),
        "task_count": len(all_records),
    }
    write_json(out_root / "manifest.json", manifest)
    write_json(out_root / "inventory.json", inventory)
    write_jsonl(out_root / "rejected_records.jsonl", rejected)
    (out_root / "README.md").write_text(render_readme(manifest, by_source), encoding="utf-8")
    print(json.dumps({"out_root": str(out_root), "records": len(all_records), "sources": len(by_source)}, ensure_ascii=False, indent=2))


def build_agent_hazard(data_root: Path) -> dict[str, Any]:
    path = data_root / "AgentHazard-dataset .json"
    rows = read_json(path)
    records, rejected = [], []
    for i, row in enumerate(rows if isinstance(rows, list) else [], start=1):
        if isinstance(row, dict):
            rec = make_task(
                benchmark="agenthazard",
                profile="computer_security_harm",
                task_id=f"agenthazard:{row.get('id', i)}",
                user_goal=row.get("query"),
                attack_goal=row.get("query"),
                tools=tool_list(["filesystem", "shell", "network_exfiltration"]),
                initial_state={"attack_plan": row.get("decomposed_query"), "comment": row.get("comment")},
                labels={"expected_behavior": "refuse", "safety_category": "computer_security_compromise", "risk_type": row.get("category")},
                metadata=source_meta("AgentHazard", path, i, row),
            )
            if rec:
                records.append(rec)
    return pack("AgentHazard", records, rejected, ["malicious local-computer exfiltration tasks"])


def build_agent_safetybench(data_root: Path) -> dict[str, Any]:
    path = data_root / "Agent-SafetyBench" / "released_data.json"
    rows = read_json(path)
    records, rejected = [], []
    for i, row in enumerate(rows if isinstance(rows, list) else [], start=1):
        if isinstance(row, dict):
            env_tools = []
            for env in as_list(row.get("environments")):
                if isinstance(env, dict):
                    env_tools.extend(as_list(env.get("tools")))
            fulfillable = int(row.get("fulfillable") or 0)
            rec = make_task(
                benchmark="agent_safetybench",
                profile="general_tool_agent_safety",
                task_id=f"agent_safetybench:{row.get('id', i)}",
                user_goal=row.get("instruction"),
                attack_goal=None if fulfillable else join_list(row.get("risks")),
                tools=tool_list(env_tools),
                initial_state={"environments": row.get("environments"), "failure_modes": row.get("failure_modes")},
                labels={"expected_behavior": "complete" if fulfillable else "refuse", "safety_category": "general_agent_safety", "risk_type": join_list(row.get("risks"))},
                metadata=source_meta("Agent-SafetyBench", path, i, row),
            )
            if rec:
                records.append(rec)
    return pack("Agent-SafetyBench", records, rejected, ["fulfillable tasks preserved as positives; unsafe ones as refusal tasks"])


def build_openagentsafety(data_root: Path) -> dict[str, Any]:
    path = data_root / "OpenAgentsafety-dataset" / "openagentsafety" / "OpenAgentSafety_tasks.csv"
    rows = read_csv(path)
    records, rejected = [], []
    for i, row in enumerate(rows, start=1):
        task_name = row.get("Task Name") or f"row-{i}"
        intent = str(row.get("Intent Category (user's intent)") or "").strip().lower()
        rec = make_task(
            benchmark="openagentsafety",
            profile="desktop_browser_agent_safety",
            task_id=f"openagentsafety:{slug(task_name)}:{i}",
            user_goal=row.get("Description") or task_name,
            attack_goal=row.get("Environment intent (NPC's behaviour)"),
            tools=tool_list(split_tools(row.get("Tools"))),
            initial_state={"path": row.get("path"), "task_name": task_name},
            labels={"expected_behavior": "complete" if intent == "benign" else "refuse", "safety_category": "browser_desktop_policy", "risk_type": row.get("Risk Category")},
            metadata=source_meta("OpenAgentSafety", path, i, row),
        )
        if rec:
            records.append(rec)
    return pack("OpenAgentSafety", records, rejected, ["csv catalog of browser/desktop/tool scenarios"])


def build_cuaharm(data_root: Path) -> dict[str, Any]:
    root = data_root / "CUAHarm" / "CUAHarm-main"
    terminal_ids = set(read_json(root / "evaluation_examples" / "test_computer_use_tasks.json").get("terminal", []))
    benign_ids = set(read_json(root / "evaluation_examples" / "test_computer_use_tasks_benign_variants.json.json").get("benign", []))
    malicious_ids = set(read_json(root / "evaluation_examples" / "test_common_malicious_prompts.json").get("terminal", []))
    records, rejected = [], []
    for path in sorted((root / "evaluation_examples" / "examples" / "terminal").glob("*.json")):
        row = read_json(path)
        if not isinstance(row, dict):
            continue
        task_id = str(row.get("id") or path.stem)
        if task_id not in terminal_ids and task_id not in benign_ids and task_id not in malicious_ids:
            continue
        # The imported terminal subset contains harmful computer-use requests. The
        # split files select evaluation variants; they do not make the actions safe.
        expected = "refuse"
        rec = make_task(
            benchmark="cuaharm",
            profile="computer_use_terminal",
            task_id=f"cuaharm:{task_id}",
            user_goal=row.get("instruction"),
            attack_goal=row.get("instruction") if expected == "refuse" else None,
            tools=tool_list(row.get("related_apps") or [row.get("snapshot") or "terminal"]),
            initial_state={"config": row.get("config"), "snapshot": row.get("snapshot"), "trajectory": row.get("trajectory")},
            labels={"expected_behavior": expected, "safety_category": "computer_use_harm", "risk_type": "terminal"},
            metadata=source_meta("CUAHarm", path, 1, row),
        )
        if rec:
            records.append(rec)
    return pack("CUAHarm", records, rejected, ["terminal computer-use tasks with malicious/benign split lists"])


def build_mcptox(data_root: Path) -> dict[str, Any]:
    path = data_root / "MCPTox" / "pure_tool.json"
    payload = read_json(path)
    records, rejected = [], []
    if isinstance(payload, list):
        for i, entry in enumerate(payload, start=1):
            if not isinstance(entry, dict):
                continue
            for key, row in entry.items():
                if isinstance(row, dict):
                    rec = make_task(
                        benchmark="mcptox",
                        profile="mcp_tool_poisoning",
                        task_id=f"mcptox:{key}",
                        user_goal=row.get("query"),
                        attack_goal=row.get("tool_content"),
                        tools=tool_list([f"{row.get('server_name')}.{row.get('tool_name')}"]),
                        initial_state={"tool_content": row.get("tool_content"), "tool_address": row.get("tool_address")},
                        labels={"expected_behavior": "complete", "safety_category": "tool_poisoning", "risk_type": row.get("security risk")},
                        metadata=source_meta("MCPTox", path, i, row),
                    )
                    if rec:
                        records.append(rec)
    return pack("MCPTox", records, rejected, ["MCP/tool poisoning records"])


def build_st_webagentbench(data_root: Path) -> dict[str, Any]:
    path = data_root / "ST-WebAgentBench" / "datasets--ST-WebAgentBench--st-webagentbench" / "snapshots" / "67f56dd7df9eca1646c9e49407b087e950aa1e77" / "stwebagentbench" / "test.csv"
    rows = read_csv(path)
    records, rejected = [], []
    for i, row in enumerate(rows, start=1):
        policy = str(row.get("policy_template_id") or "").strip().lower()
        rec = make_task(
            benchmark="st_webagentbench",
            profile="browser_agent_policy",
            task_id=f"stwebagentbench:{i}",
            user_goal=row.get("intent") or row.get("description"),
            attack_goal=row.get("description"),
            tools=tool_list([row.get("site") or "browser"]),
            initial_state={"source": row.get("source"), "path": row.get("path")},
            labels={"expected_behavior": "ask_user" if policy == "ask_the_user" else "complete", "safety_category": "browser_policy", "risk_type": row.get("policy_category")},
            metadata={**source_meta("ST-WebAgentBench", path, i, row), "source_task_id": row.get("task_id")},
        )
        if rec:
            records.append(rec)
    return pack("ST-WebAgentBench", records, rejected, ["browser policy tasks"])


def build_ras_eval(data_root: Path) -> dict[str, Any]:
    root = data_root / "RAS-Eval-master" / "RAS-Eval-master" / "data" / "tasks"
    base = read_json(root / "tasks.json")
    attacks = read_json(root / "attack_tasks.json")
    attack_map: dict[int, list[dict[str, Any]]] = {}
    for row in attacks if isinstance(attacks, list) else []:
        if isinstance(row, dict) and isinstance(row.get("target_index"), int):
            attack_map.setdefault(int(row["target_index"]), []).append(row)
    records, rejected = [], []
    for i, row in enumerate(base if isinstance(base, list) else [], start=1):
        if not isinstance(row, dict):
            continue
        attacks_for_target = attack_map.get(int(row.get("index", i - 1)), [{}])
        for a_i, attack in enumerate(attacks_for_target, start=1):
            rec = make_task(
                benchmark="ras_eval",
                profile="tool_trace_attack",
                task_id=f"ras_eval:{row.get('index', i)}:{a_i}",
                user_goal=row.get("prompt"),
                attack_goal=join_list(attack.get("attack")),
                tools=tool_list(row.get("workflow")),
                initial_state={"environment": row.get("environment"), "attack": attack.get("attack")},
                labels={"expected_behavior": "complete", "safety_category": "tool_trace_attack", "risk_type": row.get("agent")},
                metadata=source_meta("RAS-Eval", root / "tasks.json", i, row),
            )
            if rec:
                records.append(rec)
    return pack("RAS-Eval", records, rejected, ["target/attack paired tasks"])


def build_msb(data_root: Path) -> dict[str, Any]:
    path = data_root / "MSB-main" / "MSB-main" / "data" / "attack_task.jsonl"
    agent_path = data_root / "MSB-main" / "MSB-main" / "data" / "agent_task.jsonl"
    attack_type_path = data_root / "MSB-main" / "MSB-main" / "data" / "attack_type.jsonl"
    attack_rows = read_jsonl(path)
    agent_rows = read_jsonl(agent_path)
    attack_type_rows = read_jsonl(attack_type_path)
    attack_types = as_list(attack_type_rows[0].get("attack_type")) if attack_type_rows else []
    records, rejected = [], []
    for agent_i, agent in enumerate(agent_rows, start=1):
        agent_name = agent.get("agent_name")
        system_prompt = agent.get("system_prompt")
        task_tools = as_list(agent.get("task_tool"))
        for task_i, task_tool in enumerate(task_tools, start=1):
            if not isinstance(task_tool, dict):
                continue
            task_text = task_tool.get("task")
            tool_name = task_tool.get("tool")
            for attack_i, attack in enumerate(attack_rows, start=1):
                attack_task = attack.get("attack_task")
                implementation = attack.get("implementation")
                for type_i, attack_type in enumerate(attack_types or ["all"], start=1):
                    rec = make_task(
                        benchmark="msb",
                        profile="mcp_tool_poisoning",
                        task_id=f"msb:{slug(str(agent_name or agent_i))}:{slug(str(tool_name or task_i))}:{slug(str(attack_task or attack_i))}:{slug(str(attack_type))}",
                        user_goal=task_text,
                        attack_goal=implementation or attack_task,
                        tools=tool_list([tool_name, agent_name]),
                        initial_state={
                            "agent_name": agent_name,
                            "system_prompt": system_prompt,
                            "task_tool": task_tool,
                            "attack_task": attack_task,
                            "attack_type": attack_type,
                            "attack_types": attack_types,
                            "tool_config": tool_name,
                        },
                        labels={
                            "expected_behavior": "complete",
                            "safety_category": "tool_poisoning",
                            "risk_type": f"{attack_type}:{attack_task}",
                        },
                        metadata={
                            **source_meta("MSB", path, attack_i, attack),
                            "source_agent_path": str(agent_path),
                            "source_agent_index": agent_i,
                            "source_agent_raw": agent,
                            "source_attack_type_path": str(attack_type_path),
                            "source_attack_type_raw": attack_type_rows[0] if attack_type_rows else {},
                            "source_task_tool_index": task_i,
                        },
                    )
                    if rec:
                        records.append(rec)
    return pack("MSB", records, rejected, ["cartesian expansion over agent_task x task_tool x attack_task x attack_type"])


def build_wasp(data_root: Path) -> dict[str, Any]:
    path = data_root / "WASP" / "wasp-main" / "webarena_prompt_injections" / "configs" / "croissant" / "attacks_in_webarena_format.jsonl"
    rows = read_jsonl(path)
    records, rejected = [], []
    for i, row in enumerate(rows, start=1):
        rec = make_task(
            benchmark="wasp",
            profile="browser_prompt_injection",
            task_id=f"wasp:{slug(str(row.get('free_form_name') or i))}:{i}",
            user_goal=row.get("instruction"),
            attack_goal=row.get("instruction") if row.get("exfil") else row.get("notes"),
            tools=tool_list([row.get("environment") or "browser"]),
            initial_state={"action_url": row.get("action_url"), "parameters": row.get("parameters"), "eval": row.get("eval")},
            labels={"expected_behavior": "refuse", "safety_category": "web_prompt_injection", "risk_type": row.get("environment")},
            metadata=source_meta("WASP", path, i, row),
        )
        if rec:
            records.append(rec)
    return pack("WASP", records, rejected, ["webarena prompt injection tasks"])


def build_agentleak(data_root: Path) -> dict[str, Any]:
    path = find_first(data_root, ["AgentLeak-main/agentleak_data/datasets/scenarios_base_100.jsonl", "AgentLeak-main/AgentLeak-main/agentleak_data/datasets/scenarios_base_100.jsonl"])
    rows = read_jsonl(path)
    records, rejected = [], []
    for i, row in enumerate(rows, start=1):
        objective = row.get("objective") if isinstance(row, dict) else {}
        user_goal = objective.get("user_request") if isinstance(objective, dict) else row.get("objective")
        rec = make_task(
            benchmark="agentleak",
            profile="multi_agent_privacy_trace",
            task_id=f"agentleak:{row.get('scenario_id', i)}",
            user_goal=user_goal,
            attack_goal=join_list(row.get("attack")),
            tools=agentleak_tool_list(row.get("tools")),
            initial_state={"private_vault": row.get("private_vault"), "allowed_set": row.get("allowed_set"), "agents": row.get("agents")},
            labels={"expected_behavior": "complete", "safety_category": "privacy_data_flow", "risk_type": row.get("vertical")},
            metadata=source_meta("AgentLeak", path, i, row),
        )
        if rec:
            records.append(rec)
    return pack("AgentLeak", records, rejected, ["privacy and channel-leak scenarios"])


def build_agentdojo(data_root: Path) -> dict[str, Any]:
    return build_agentdojo_family(data_root, "AgentDojo", "agentdojo")


def build_agentdyn(data_root: Path) -> dict[str, Any]:
    return build_agentdojo_family(data_root, "AgentDyn", "agentdyn")


def build_agentdojo_family(data_root: Path, folder_name: str, benchmark: str) -> dict[str, Any]:
    root = find_first(data_root, [folder_name, f"{folder_name}-main"])
    if root is None:
        return pack(folder_name, [], [], ["missing source root"])
    records, rejected = [], []
    suite_root = root / "src" / "agentdojo" / "default_suites"
    for path in sorted(suite_root.glob("**/*tasks.py")):
        if "tests" in str(path).lower() or "defenses" in str(path).lower():
            continue
        suite = extract_suite_name(path)
        kind = "injection" if path.name.startswith("injection") else "user"
        for class_name, attrs in extract_task_classes(path):
            prompt = attrs.get("PROMPT") or attrs.get("GOAL") or attrs.get("GROUND_TRUTH_OUTPUT")
            if not prompt:
                continue
            rec = make_task(
                benchmark=benchmark,
                profile="prompt_injection_tool_env" if kind == "injection" else "tool_use_task",
                task_id=f"{benchmark}:{suite}:{class_name}",
                user_goal=str(prompt),
                attack_goal=str(attrs.get("GOAL") or attrs.get("PROMPT") or prompt) if kind == "injection" else None,
                tools=tool_list([suite]),
                initial_state={"difficulty": attrs.get("DIFFICULTY"), "class_name": class_name},
                labels={"expected_behavior": "refuse" if kind == "injection" else "complete", "safety_category": "prompt_injection" if kind == "injection" else "benign_tool_use", "risk_type": suite},
                metadata={**source_meta(folder_name, path, 0, attrs), "class_name": class_name, "suite": suite, "task_kind": kind},
            )
            if rec:
                records.append(rec)
    return pack(folder_name, records, rejected, ["statically extracted AgentDojo/AgentDyn task classes"])


def build_agentlab(data_root: Path) -> dict[str, Any]:
    root = find_first(data_root, ["AgentLAB-main/AgentLAB-main", "AgentLAB-main"])
    if root is None:
        return pack("AgentLAB", [], [], ["missing source root"])
    records, rejected = [], []
    sources: list[tuple[str, Path, str]] = [
        ("filtered_top_200_attacks", root / "data" / "filtered_top_200_attacks.json", "adaptive_long_horizon_attack"),
        ("all_refused_combined_200", root / "data" / "all_refused_combined_200.json", "adaptive_long_horizon_attack"),
        ("agent_safetybench", root / "Agent_SafetyBench" / "data" / "released_data.json", "general_tool_agent_safety"),
    ]
    for source_name, path, profile in sources:
        rows = read_json(path)
        if not isinstance(rows, list):
            continue
        for i, row in enumerate(rows, start=1):
            if not isinstance(row, dict):
                continue
            if source_name == "filtered_top_200_attacks":
                rec = build_agentlab_filtered_attack(path, i, row, profile)
            elif source_name == "all_refused_combined_200":
                rec = build_agentlab_refused_200(path, i, row, profile)
            else:
                rec = build_agentlab_safetybench(path, i, row, profile)
            if rec:
                records.append(rec)
            else:
                rejected.append({"source": source_name, "index": i, "path": str(path), "raw": row})
    return pack("AgentLAB", records, rejected, ["filtered_top_200_attacks + all_refused_combined_200 + Agent_SafetyBench/released_data.json, filtered to computer/browser/tool related records"])


def build_agentlab_filtered_attack(path: Path, index: int, row: dict[str, Any], profile: str) -> dict[str, Any] | None:
    task_description = str(row.get("task_description") or "").strip()
    attack_plan = row.get("attack_plan") if isinstance(row.get("attack_plan"), dict) else {}
    attack_goal = attack_plan.get("attack_goal") or attack_plan.get("explanation") or task_description
    tool_names = agentlab_tool_names_from_filtered(row)
    environment = str((row.get("generation_config") or {}).get("environment") or "").strip()
    if not agentlab_is_relevant(task_description, tool_names, [environment]):
        return None
    task_id = f"agentlab:filtered_top_200_attacks:{row.get('original_id', index)}"
    return make_task(
        benchmark="agentlab",
        profile=profile,
        task_id=task_id,
        user_goal=task_description,
        attack_goal=attack_goal,
        tools=tool_list(tool_names),
        initial_state={
            "source_dataset": "SHADE_Arena",
            "environment": environment,
            "generation_config": row.get("generation_config"),
            "attack_plan": attack_plan,
            "interaction_history": row.get("interaction_history"),
        },
        labels={
            "expected_behavior": "refuse",
            "safety_category": "long_horizon_attack",
            "risk_type": row.get("malicious_reasoning") or row.get("completion_reasoning") or environment,
        },
        metadata=source_meta("AgentLAB", path, index, row),
    )


def build_agentlab_refused_200(path: Path, index: int, row: dict[str, Any], profile: str) -> dict[str, Any] | None:
    instruction = str(row.get("instruction") or "").strip()
    env_tools = agentlab_environment_tools(row)
    env_names = agentlab_environment_names(row)
    if not agentlab_is_relevant(instruction, env_tools, env_names):
        return None
    baseline = row.get("_baseline_metadata") if isinstance(row.get("_baseline_metadata"), dict) else {}
    attack_goal = (
        baseline.get("goal_analysis", {}).get("harmful_goal")
        if isinstance(baseline.get("goal_analysis"), dict)
        else None
    ) or baseline.get("goal_analysis", {}).get("abstracted_action") if isinstance(baseline.get("goal_analysis"), dict) else None
    return make_task(
        benchmark="agentlab",
        profile=profile,
        task_id=f"agentlab:all_refused_combined_200:{row.get('id', index)}",
        user_goal=instruction,
        attack_goal=attack_goal or instruction,
        tools=tool_list(env_tools),
        initial_state={
            "risks": row.get("risks"),
            "failure_modes": row.get("failure_modes"),
            "fulfillable": row.get("fulfillable"),
            "environments": row.get("environments"),
            "baseline_metadata": baseline,
        },
        labels={
            "expected_behavior": "refuse",
            "safety_category": "agentlab_refusal_prompt",
            "risk_type": join_list(row.get("risks")) or join_list(row.get("failure_modes")),
        },
        metadata=source_meta("AgentLAB", path, index, row),
    )


def build_agentlab_safetybench(path: Path, index: int, row: dict[str, Any], profile: str) -> dict[str, Any] | None:
    instruction = str(row.get("instruction") or "").strip()
    env_tools = agentlab_environment_tools(row)
    env_names = agentlab_environment_names(row)
    if not agentlab_is_relevant(instruction, env_tools, env_names):
        return None
    fulfillable = int(row.get("fulfillable") or 0)
    baseline = row.get("_baseline_metadata") if isinstance(row.get("_baseline_metadata"), dict) else {}
    return make_task(
        benchmark="agentlab",
        profile=profile,
        task_id=f"agentlab:agent_safetybench:{row.get('id', index)}",
        user_goal=instruction,
        attack_goal=join_list(row.get("risks")) or join_list(row.get("failure_modes")),
        tools=tool_list(env_tools),
        initial_state={
            "risks": row.get("risks"),
            "failure_modes": row.get("failure_modes"),
            "fulfillable": fulfillable,
            "environments": row.get("environments"),
            "baseline_metadata": baseline,
        },
        labels={
            "expected_behavior": "complete" if fulfillable else "refuse",
            "safety_category": "agent_safetybench",
            "risk_type": join_list(row.get("risks")) or join_list(row.get("failure_modes")),
        },
        metadata=source_meta("AgentLAB", path, index, row),
    )


def agentlab_environment_names(row: dict[str, Any]) -> list[str]:
    names: list[str] = []
    for env in as_list(row.get("environments")):
        if isinstance(env, dict):
            name = env.get("name")
            if name not in (None, ""):
                names.append(str(name))
    return names


def agentlab_environment_tools(row: dict[str, Any]) -> list[str]:
    tools: list[str] = []
    for env in as_list(row.get("environments")):
        if isinstance(env, dict):
            tools.extend(split_tools(env.get("tools")))
    return tools


def agentlab_tool_names_from_filtered(row: dict[str, Any]) -> list[str]:
    tools: list[str] = []
    attack_plan = row.get("attack_plan") if isinstance(row.get("attack_plan"), dict) else {}
    for step in as_list(attack_plan.get("verified_tool_chain")):
        if isinstance(step, dict):
            name = step.get("tool_name")
            if name not in (None, ""):
                tools.append(str(name))
    return tools


def agentlab_is_relevant(text: str, tool_names: list[str], env_names: list[str]) -> bool:
    blob = " ".join([text, " ".join(tool_names), " ".join(env_names)]).lower()
    if any(marker in blob for marker in COMPUTER_EXCLUDE_MARKERS):
        return False
    include_markers = (
        "browser",
        "desktop",
        "computer",
        "email",
        "mail",
        "calendar",
        "workspace",
        "drive",
        "cloud",
        "file",
        "filesystem",
        "terminal",
        "shell",
        "github",
        "notion",
        "slack",
        "document",
        "docs",
        "spreadsheet",
        "web",
        "website",
        "playwright",
        "mcp",
        "tool",
        "search",
    )
    if any(marker in blob for marker in include_markers):
        return True
    return bool(tool_names or env_names)


def build_saber(data_root: Path) -> dict[str, Any]:
    path = data_root / "saber-main" / "saber-main" / "dataset" / "data" / "tasks.jsonl"
    rows = read_jsonl(path)
    records, rejected = [], []
    for i, row in enumerate(rows, start=1):
        task = row.get("task") if isinstance(row, dict) else None
        if not isinstance(task, dict):
            continue
        setup = task.get("setup") if isinstance(task.get("setup"), dict) else {}
        rec = make_task(
            benchmark="saber",
            profile="trace_attack_detected",
            task_id=f"saber:{row.get('task_id', i)}",
            user_goal=setup.get("user_prompt") or task.get("id"),
            attack_goal=join_list(task.get("injection")),
            tools=tool_list((setup.get("mock_fs") or {}).keys()),
            initial_state={"setup": setup, "injection": task.get("injection"), "ground_truth": task.get("ground_truth"), "source_path": row.get("source_path")},
            labels={"expected_behavior": "refuse" if str(task.get("scenario")) == "C" else "complete", "safety_category": "workspace_prompt_injection", "risk_type": row.get("category")},
            metadata=source_meta("SABER", path, i, row),
        )
        if rec:
            records.append(rec)
    return pack("SABER", records, rejected, ["source_path indexed workspace/code tampering tasks"])


def build_agentharm(data_root: Path) -> dict[str, Any]:
    root = data_root / "AgentHarm"
    records, rejected = [], []
    for path in sorted(root.glob("*.json")):
        payload = read_json(path)
        rows = payload.get("behaviors") if isinstance(payload, dict) else payload
        if not isinstance(rows, list):
            continue
        kind = "benign" if "benign" in path.name else "chat" if "chat" in path.name else "harmful"
        for i, row in enumerate(rows, start=1):
            if isinstance(row, dict) and row.get("prompt"):
                rec = make_task(
                    benchmark="agentharm",
                    profile="harmful_chat_task" if kind == "chat" else "benign_agent_task" if kind == "benign" else "harmful_agent_task",
                    task_id=f"agentharm:{path.stem}:{i}",
                    user_goal=row.get("prompt"),
                    attack_goal=None if kind == "benign" else row.get("prompt"),
                    tools=tool_list(as_list(row.get("target_functions"))),
                    initial_state={},
                    labels={"expected_behavior": "complete" if kind == "benign" else "refuse", "safety_category": "benign" if kind == "benign" else "harmful_action", "risk_type": row.get("category")},
                    metadata=source_meta("AgentHarm", path, i, row),
                )
                if rec:
                    records.append(rec)
    return pack("AgentHarm", records, rejected, ["direct AgentHarm import"])


def build_injecagent(data_root: Path) -> dict[str, Any]:
    path = data_root / "InjecAgent-main" / "data" / "test_cases_dh_base.json"
    rows = read_json(path)
    records, rejected = [], []
    for i, row in enumerate(rows if isinstance(rows, list) else [], start=1):
        if isinstance(row, dict):
            rec = make_task(
                benchmark="injecagent",
                profile="prompt_injection_tool_env",
                task_id=f"injecagent:{row.get('id', i)}",
                user_goal=row.get("User Instruction") or row.get("instruction"),
                attack_goal=row.get("Attacker Instruction") or row.get("Expected Achievements"),
                tools=tool_list(split_tools(row.get("User Tool"))) + tool_list(split_tools(row.get("Attacker Tools"))),
                initial_state={"tool_parameters": row.get("Tool Parameters"), "tool_response": row.get("Tool Response Template") or row.get("Tool Response")},
                labels={"expected_behavior": "complete", "safety_category": "indirect_prompt_injection", "risk_type": row.get("Attack Type")},
                metadata=source_meta("InjecAgent", path, i, row),
            )
            if rec:
                records.append(rec)
    return pack("InjecAgent", records, rejected, ["direct importer-compatible cases"])


def build_toolemu(data_root: Path) -> dict[str, Any]:
    path = data_root / "ToolEmu-main" / "assets" / "all_cases.json"
    rows = read_json(path)
    records, rejected = [], []
    for i, row in enumerate(rows if isinstance(rows, list) else [], start=1):
        if isinstance(row, dict):
            rec = make_task(
                benchmark="toolemu",
                profile="tool_use_emulated_sandbox",
                task_id=f"toolemu:{slug(str(row.get('name') or i))}",
                user_goal=row.get("User Instruction") or row.get("instruction") or row.get("task"),
                attack_goal=join_list(row.get("Potential Risky Outcomes")),
                tools=tool_list(as_list(row.get("Toolkits")) or as_list(row.get("toolkits")) or ["toolkit"]),
                initial_state={"underspecifications": row.get("Underspecifications")},
                labels={"expected_behavior": "complete", "safety_category": "tool_risk_emulation", "risk_type": infer_tool_risk(row)},
                metadata=source_meta("ToolEmu", path, i, row),
            )
            if rec:
                records.append(rec)
    return pack("ToolEmu", records, rejected, ["tool emulation tasks"])


def build_rjudge(data_root: Path) -> dict[str, Any]:
    root = data_root / "R-Judge-main" / "data"
    records, rejected = [], []
    for path in sorted(root.glob("*/*.json")):
        if should_exclude_path(path.relative_to(data_root)):
            continue
        rows = read_json(path)
        if not isinstance(rows, list):
            continue
        for i, row in enumerate(rows, start=1):
            if isinstance(row, dict):
                rec = make_task(
                    benchmark="r_judge",
                    profile="trajectory_judge",
                    task_id=f"r_judge:{path.parent.name}:{path.stem}:{i}",
                    user_goal=str(first_user_message(row.get("contents")) or row.get("goal") or row.get("profile")),
                    attack_goal=row.get("risk_description"),
                    tools=[],
                    initial_state={},
                    labels={"expected_behavior": "judge", "safety_category": "trace_safety_judge", "risk_type": row.get("attack_type") or row.get("scenario"), "safety_violation": bool(row.get("label"))},
                    metadata={**source_meta("R-Judge", path, i, row), "trace": normalize_rjudge_trace(row.get("contents")), "source_split": f"{path.parent.name}/{path.name}"},
                )
                if rec:
                    records.append(rec)
    return pack("R-Judge", records, rejected, ["trajectory judge tasks"])


def pack(source: str, records: list[dict[str, Any]], rejected: list[dict[str, Any]], notes: list[str]) -> dict[str, Any]:
    return {"source": source, "records": records, "rejected": rejected, "notes": notes}


def make_task(*, benchmark: str, profile: str, task_id: str, user_goal: Any, attack_goal: Any, tools: list[dict[str, Any]], initial_state: dict[str, Any], labels: dict[str, Any], metadata: dict[str, Any]) -> dict[str, Any] | None:
    if not user_goal:
        return None
    record = {
        "task_id": task_id,
        "benchmark": benchmark,
        "profile": profile,
        "user_goal": str(user_goal),
        "attack_goal": None if attack_goal in (None, "") else str(attack_goal),
        "tools": tools,
        "initial_state": initial_state,
        "labels": labels,
        "metadata": metadata,
    }
    normalized = TaskSpec.from_record(record)
    return {
        "task_id": normalized.task_id,
        "benchmark": normalized.benchmark,
        "profile": normalized.profile,
        "user_goal": normalized.user_goal,
        "attack_goal": normalized.attack_goal,
        "tools": normalized.tools,
        "initial_state": normalized.initial_state,
        "labels": normalized.labels,
        "metadata": normalized.metadata,
    }


def source_meta(source_dataset: str, path: Path, index: int, raw: Any) -> dict[str, Any]:
    try:
        source_name = str(path.relative_to(path.parents[1]))
    except Exception:
        source_name = path.name
    return {"harness_dataset_role": "source_derived_task", "source_dataset": source_dataset, "source_name": source_name, "source_record_index": index, "source_path": str(path), "raw": raw}


def render_readme(manifest: dict[str, Any], by_source: dict[str, list[dict[str, Any]]]) -> str:
    lines = [
        "# Unified Agent Safety Benchmark Pack",
        "",
        "This directory is a source-derived internal benchmark pack. It normalizes records to the harness TaskSpec schema while preserving task intent, attack surface, tools, state, and provenance as much as possible.",
        "",
        "## Output",
        f"- total tasks: {manifest['counts']['all']}",
    ]
    for benchmark, count in sorted(manifest["counts"]["by_benchmark"].items()):
        lines.append(f"- {benchmark}: {count}")
    lines += ["", "## Sources"]
    for source, records in sorted(by_source.items()):
        lines.append(f"- {source}: {len(records)}")
    lines += ["", "## Run", "```powershell", "python .\\tools\\curate_datasets_v2.py --data-root <DATA_ROOT> --out-root <OUT_ROOT> --overwrite", "```"]
    return "\n".join(lines)


def dedupe(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen, out = set(), []
    for record in records:
        key = f"{record['benchmark']}::{record['task_id']}"
        if key in seen:
            continue
        seen.add(key)
        out.append(record)
    return out


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows = []
    with path.open("r", encoding="utf-8-sig") as handle:
        for line in handle:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def read_csv(path: Path) -> list[dict[str, Any]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return [dict(row) for row in csv.DictReader(handle)]


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True))
            handle.write("\n")


def tool_list(values: list[Any]) -> list[dict[str, Any]]:
    return [{"name": str(v), "type": "function", "risk": "medium"} for v in values if v not in (None, "")]


def split_tools(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return [part.strip() for part in str(value).split(",") if part.strip()]


def as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def join_list(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, list):
        return "\n".join(str(item) for item in value if item not in (None, ""))
    return str(value)


def infer_tool_risk(row: dict[str, Any]) -> str:
    text = json.dumps(row, ensure_ascii=False).lower()
    if any(word in text for word in ["leak", "credential", "secret", "exfil", "password"]):
        return "data_leak"
    if any(word in text for word in ["delete", "tamper", "destroy", "overwrite"]):
        return "data_loss"
    return "unsafe_tool_use"


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
    if not isinstance(contents, list):
        return events
    for conversation in contents:
        if not isinstance(conversation, list):
            continue
        for index, event in enumerate(conversation, start=1):
            if isinstance(event, dict):
                events.append({"event_id": str(index), "role": event.get("role", "agent"), "event_type": "message", "content": {"text": event.get("content")}})
    return events


def extract_suite_name(path: Path) -> str:
    m = re.search(r"default_suites[\\/](v[^\\/]+[\\/][^\\/]+)", str(path))
    return m.group(1).replace("\\", "/") if m else path.parent.name


def extract_task_classes(path: Path) -> list[tuple[str, dict[str, Any]]]:
    text = path.read_text(encoding="utf-8", errors="ignore")
    module = ast.parse(text)
    module_values: dict[str, Any] = {}
    for node in module.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            value = safe_ast_value(node.value, module_values)
            if value is not None:
                module_values[node.targets[0].id] = value
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.value is not None:
            value = safe_ast_value(node.value, module_values)
            if value is not None:
                module_values[node.target.id] = value
    classes = []
    for node in module.body:
        if isinstance(node, ast.ClassDef):
            attrs: dict[str, Any] = {}
            context = dict(module_values)
            for item in node.body:
                if isinstance(item, ast.Assign) and len(item.targets) == 1 and isinstance(item.targets[0], ast.Name):
                    value = safe_ast_value(item.value, context)
                    if value is None:
                        value = literal_or_source(item.value)
                    attrs[item.targets[0].id] = value
                    context[item.targets[0].id] = value
                elif isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name) and item.value is not None:
                    value = safe_ast_value(item.value, context)
                    if value is None:
                        value = literal_or_source(item.value)
                    attrs[item.target.id] = value
                    context[item.target.id] = value
            classes.append((node.name, attrs))
    return classes


def safe_ast_value(node: ast.AST, context: dict[str, Any]) -> Any:
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.Name):
        return context.get(node.id)
    if isinstance(node, ast.List):
        values = [safe_ast_value(item, context) for item in node.elts]
        return None if any(value is None for value in values) else values
    if isinstance(node, ast.Tuple):
        values = [safe_ast_value(item, context) for item in node.elts]
        return None if any(value is None for value in values) else tuple(values)
    if isinstance(node, ast.Dict):
        keys = [safe_ast_value(item, context) for item in node.keys]
        values = [safe_ast_value(item, context) for item in node.values]
        if any(item is None for item in keys + values):
            return None
        return dict(zip(keys, values))
    if isinstance(node, ast.JoinedStr):
        parts = []
        for item in node.values:
            if isinstance(item, ast.Constant):
                parts.append(str(item.value))
            elif isinstance(item, ast.FormattedValue):
                value = safe_ast_value(item.value, context)
                if value is None:
                    return None
                parts.append(str(value))
            else:
                return None
        return "".join(parts)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left = safe_ast_value(node.left, context)
        right = safe_ast_value(node.right, context)
        if left is None or right is None:
            return None
        try:
            return left + right
        except TypeError:
            return None
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "join"
        and len(node.args) == 1
        and not node.keywords
    ):
        separator = safe_ast_value(node.func.value, context)
        values = safe_ast_value(node.args[0], context)
        if isinstance(separator, str) and isinstance(values, (list, tuple)):
            return separator.join(str(item) for item in values)
    return None


def agentleak_tool_list(value: Any) -> list[dict[str, Any]]:
    tools = []
    for item in as_list(value):
        if not isinstance(item, dict) or not item.get("tool_name"):
            continue
        clearance = item.get("requires_clearance")
        tools.append(
            {
                "name": str(item["tool_name"]),
                "type": "function",
                "risk": "high" if clearance in {"confidential", "secret"} else "medium",
                "capabilities": [str(capability) for capability in as_list(item.get("capabilities"))],
                "requires_clearance": clearance,
            }
        )
    return tools


def literal_or_source(node: ast.AST) -> Any:
    try:
        return ast.literal_eval(node)
    except Exception:
        try:
            return ast.unparse(node)
        except Exception:
            return None


def extract_section(text: str, heading: str) -> str | None:
    match = re.search(rf"###\s+{re.escape(heading)}\s*(.*?)(?=\n###\s+|\Z)", text, re.S)
    return match.group(1).strip() if match else None


def find_first(root: Path, candidates: list[str]) -> Path | None:
    for candidate in candidates:
        matches = list(root.glob(candidate))
        if matches:
            return matches[0]
    return None


def should_exclude_path(path: Path) -> bool:
    lowered = str(path).lower()
    return any(marker in lowered for marker in COMPUTER_EXCLUDE_MARKERS)


def slug(text: str) -> str:
    text = re.sub(r"[^A-Za-z0-9]+", "-", text).strip("-")
    return text.lower() or "task"


if __name__ == "__main__":
    main()
