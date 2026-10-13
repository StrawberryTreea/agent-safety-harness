from __future__ import annotations

import argparse
import json
from pathlib import Path

from agentdojo.task_suite.load_suites import get_suites


def main() -> int:
    parser = argparse.ArgumentParser(description="Build pinned AgentDojo-compatible MCP tool manifests.")
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--benchmark-version", default="v1")
    parser.add_argument(
        "--suite",
        action="append",
        dest="suites",
        help="Include only this suite; repeat to include multiple suites.",
    )
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    suites = get_suites(args.benchmark_version)
    if args.suites:
        unknown = sorted(set(args.suites) - suites.keys())
        if unknown:
            raise ValueError(f"Unknown suites for {args.benchmark_version}: {unknown}")
        suites = {name: suites[name] for name in args.suites}
    for suite_name, suite in suites.items():
        manifest = {
            "schema_version": "0.1",
            "benchmark_version": args.benchmark_version,
            "suite": suite_name,
            "tools": [
                {
                    "name": function.name,
                    "description": function.description,
                    "inputSchema": function.parameters.model_json_schema(),
                }
                for function in suite.tools
            ],
        }
        (args.output / f"{suite_name}.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        print(f"{suite_name}: {len(manifest['tools'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
