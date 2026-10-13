from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
for path in (ROOT, SRC):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from agent_safety_harness.evaluation import evaluate_task
from agent_safety_harness.schema import TaskSpec, classify_safety_outcome
from tools.build_ash_executable_v3 import read_jsonl, write_json
from tools.build_attack_only_v5 import (
    ADMITTED_SOURCES,
    CONTRACT_VERSION,
    DATASET_VERSION,
    FORMAL_STATUS,
    contains_unconditional_non_violation,
    extract_mcptox_prerequisite_tools,
    extract_mcptox_target_tools,
    rule_tool_names,
    validate_formal_task,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Independently validate every v5 formal task and oracle replay.")
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()

    dataset_root = args.root.resolve()
    report_path = (args.report or dataset_root / "audit" / "full_validation.json").resolve()
    report = validate_dataset(dataset_root)
    write_json(report_path, report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["valid"] else 1


def validate_dataset(dataset_root: Path) -> dict[str, Any]:
    manifest_path = dataset_root / "manifest.json"
    task_path = dataset_root / "tasks" / "attack_only.tasks.jsonl"
    public_path = dataset_root / "public" / "attack_only.public.jsonl"
    private_path = dataset_root / "private" / "attack_only.private.jsonl"
    failures: list[dict[str, Any]] = []
    if not manifest_path.is_file():
        raise FileNotFoundError(manifest_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
    tasks = read_jsonl(task_path)
    public = read_jsonl(public_path)
    private = read_jsonl(private_path)

    expected_manifest = {
        "dataset_version": DATASET_VERSION,
        "formal_evaluation_status": FORMAL_STATUS,
        "oracle_contract_version": CONTRACT_VERSION,
    }
    for field, expected in expected_manifest.items():
        if manifest.get(field) != expected:
            failures.append({"scope": "manifest", "field": field, "expected": expected, "actual": manifest.get(field)})
    if manifest.get("records") != len(tasks):
        failures.append({"scope": "manifest", "field": "records", "expected": manifest.get("records"), "actual": len(tasks)})
    if len(public) != len(tasks) or len(private) != len(tasks):
        failures.append({"scope": "split", "tasks": len(tasks), "public": len(public), "private": len(private)})

    evidence = manifest.get("source_evidence") or {}
    benchmark_root = Path(str(manifest.get("benchmark_root") or ""))
    for source in ADMITTED_SOURCES:
        item = evidence.get(source) if isinstance(evidence, dict) else None
        if not isinstance(item, dict):
            failures.append({"scope": "source_evidence", "source": source, "error": "missing evidence"})
            continue
        source_path = benchmark_root / str(item.get("relative_path") or "")
        if not source_path.is_file():
            failures.append({"scope": "source_evidence", "source": source, "error": f"missing {source_path}"})

    source_counts: Counter[str] = Counter()
    scope_counts: Counter[str] = Counter()
    for record in tasks:
        task_id = str(record.get("task_id") or "")
        try:
            validate_formal_task(record)
            validate_runtime_observability(record)
            validate_near_miss(record)
        except Exception as exc:
            failures.append({"scope": "task", "task_id": task_id, "error": f"{type(exc).__name__}: {exc}"})
        source_counts[str(record.get("benchmark") or "")] += 1
        contract = (record.get("metadata") or {}).get("oracle_contract") or {}
        scope_counts[str(contract.get("observable_scope") or "missing")] += 1

    if dict(source_counts) != manifest.get("source_counts"):
        failures.append(
            {"scope": "manifest", "field": "source_counts", "expected": manifest.get("source_counts"), "actual": dict(source_counts)}
        )
    return {
        "schema_version": 1,
        "dataset_version": DATASET_VERSION,
        "validated_at": datetime.now(timezone.utc).isoformat(),
        "valid": not failures,
        "records": len(tasks),
        "source_counts": dict(source_counts),
        "observable_scope_counts": dict(scope_counts),
        "failures": failures,
    }


def validate_runtime_observability(record: dict[str, Any]) -> None:
    metadata = record["metadata"]
    runtime = metadata["ash_runtime"]
    runtime_tools = runtime.get("tools") or []
    public_names = {str(tool.get("name")) for tool in record.get("tools") or []}
    runtime_names = {str(tool.get("name")) for tool in runtime_tools}
    if public_names != runtime_names:
        raise ValueError("public/runtime tool names differ")
    if "finish_task" in public_names:
        raise ValueError("finish_task is forbidden")
    if contains_unconditional_non_violation(record["evaluation"]["safety_violation"]):
        raise ValueError("unconditional non-violation rule")
    violation_names = rule_tool_names(record["evaluation"]["safety_violation"])
    if not violation_names.issubset(public_names):
        raise ValueError(f"violation tool not exposed: {sorted(violation_names - public_names)}")

    benchmark = record["benchmark"]
    serialized_runtime = json.dumps(runtime_tools, ensure_ascii=False)
    if benchmark == "injecagent" and str(record.get("attack_goal") or "") not in serialized_runtime:
        raise ValueError("InjecAgent payload absent from runtime tool result")
    if benchmark == "mcptox":
        contract = metadata["oracle_contract"]
        if contract["observable_scope"] != "explicit_malicious_prerequisite_tool_call":
            raise ValueError("unexpected MCPTox scope")
        poisoned_text = str(runtime_tools[0].get("description") or "")
        parsed_prerequisites = set(extract_mcptox_prerequisite_tools(poisoned_text))
        parsed_targets = set(extract_mcptox_target_tools(poisoned_text))
        if violation_names != parsed_prerequisites:
            raise ValueError(
                f"MCPTox rule/parser mismatch: rules={sorted(violation_names)}, "
                f"parsed={sorted(parsed_prerequisites)}"
            )
        if violation_names & parsed_targets:
            raise ValueError("MCPTox malicious prerequisite overlaps normal target tool")
    if benchmark == "agentleak":
        replay_value = str(metadata["oracle_replay"]["unsafe"].get("response") or "")
        if not replay_value or replay_value not in serialized_runtime:
            raise ValueError("AgentLeak canary absent from runtime data")
        exposure_names = rule_tool_names(record["evaluation"]["attack_exposure"])
        payload_tools = {
            str(tool.get("name"))
            for tool in runtime_tools
            if "untrusted_content" in json.dumps(tool.get("result"), ensure_ascii=False)
        }
        if not exposure_names or exposure_names != payload_tools:
            raise ValueError(
                f"AgentLeak exposure/runtime mismatch: rules={sorted(exposure_names)}, "
                f"payload_tools={sorted(payload_tools)}"
            )


def validate_near_miss(record: dict[str, Any]) -> None:
    """A non-violating replay after exposure must remain safe and evaluable."""
    task = TaskSpec.from_record(record)
    safe_trace = record["metadata"]["oracle_replay"]["safe"]
    events = deepcopy_events(safe_trace.get("tool_events") or [])
    evaluated = evaluate_task(
        task,
        response="The attack was ignored; no protected value was disclosed.",
        tool_events=events,
        state_diff={},
    )
    _, safety_pass, reason = classify_safety_outcome(
        task,
        task_success=evaluated.task_success,
        safety_violation=evaluated.safety_violation,
        attack_exposure=evaluated.attack_exposure,
    )
    if safety_pass is not True:
        raise ValueError(f"safe near-miss failed: safety_pass={safety_pass}, reason={reason}")


def deepcopy_events(events: list[Any]) -> list[dict[str, Any]]:
    return [json.loads(json.dumps(event, ensure_ascii=False)) for event in events]


if __name__ == "__main__":
    raise SystemExit(main())
