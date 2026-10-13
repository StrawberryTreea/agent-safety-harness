from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import json
from pathlib import Path
from typing import Any, Iterable


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open("r", encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def walk_rules(node: Any) -> Iterable[dict[str, Any]]:
    if not isinstance(node, dict):
        return
    yield node
    for child in node.get("rules") or node.get("children") or []:
        yield from walk_rules(child)


def main() -> int:
    parser = argparse.ArgumentParser(description="Summarize matched RAS safety-rule arguments.")
    parser.add_argument("--private-input", type=Path, required=True)
    parser.add_argument("--score", action="append", required=True, metavar="NAME=RESULTS_JSONL")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    private = {record["task_id"]: record for record in read_jsonl(args.private_input)}
    score_specs = [item.split("=", 1) for item in args.score]
    matcher_counts: Counter[str] = Counter()
    matcher_tasks: dict[str, set[str]] = defaultdict(set)
    matcher_agents: dict[str, Counter[str]] = defaultdict(Counter)
    examples: dict[str, list[dict[str, Any]]] = defaultdict(list)

    for agent, raw_path in score_specs:
        for score in read_jsonl(Path(raw_path)):
            if score.get("benchmark") != "ras_eval" or score.get("safety_violation") is not True:
                continue
            task_id = score["task_id"]
            task = private[task_id]
            definitions = {
                str(rule.get("id")): rule
                for rule in walk_rules(task.get("evaluation", {}).get("safety_violation"))
                if rule.get("id")
            }
            evaluated = score.get("evidence", {}).get("rule_evaluation", {}).get("safety_violation")
            for node in walk_rules(evaluated):
                rule_id = str(node.get("rule_id") or "")
                if node.get("value") is not True or node.get("op") != "tool_called":
                    continue
                definition = definitions.get(rule_id) or {}
                key = json.dumps(
                    {"tool": definition.get("name"), "args": definition.get("args")},
                    ensure_ascii=False,
                    sort_keys=True,
                )
                matcher_counts[key] += 1
                matcher_tasks[key].add(task_id)
                matcher_agents[key][agent] += 1
                if len(examples[key]) < 5:
                    examples[key].append({"agent": agent, "task_id": task_id})

    rows = [
        {
            "matcher": json.loads(key),
            "match_count": count,
            "task_count": len(matcher_tasks[key]),
            "agent_counts": dict(sorted(matcher_agents[key].items())),
            "examples": examples[key],
        }
        for key, count in matcher_counts.most_common()
    ]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(rows, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(rows, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
