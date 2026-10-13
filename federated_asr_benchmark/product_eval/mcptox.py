from __future__ import annotations

import ast
from collections import Counter
import json
from pathlib import Path
import re
from typing import Any


_TOOL_BLOCK = re.compile(
    r"(?:^|\n{3})Tool: ([^\n]+)\nDescription: (.*?)\nArguments:\n"
    r"(.*?)(?=\n{3}(?:Tool:|Choose the appropriate tool)|\Z)",
    re.DOTALL,
)
_ARGUMENT = re.compile(r"^- ([^:]+): (.*?)(?: \(required\))?$")


class MCPToxAdapter:
    benchmark_id = "mcptox"
    benchmark_version = "source-f85189f-candidate-only"

    def __init__(self, root: Path):
        self.root = root.resolve()
        self.cases_path = self.root / "pure_tool.json"
        self.response_path = self.root / "response_all.json"
        self.fragments_root = self.root / "def_tool"

    def _rows(self) -> list[tuple[str, dict[str, Any]]]:
        groups = json.loads(self.cases_path.read_text(encoding="utf-8"))
        if not isinstance(groups, list):
            raise RuntimeError("MCPTox pure_tool.json must be a list")
        rows: list[tuple[str, dict[str, Any]]] = []
        for group in groups:
            if not isinstance(group, dict):
                raise RuntimeError("MCPTox case group must be an object")
            rows.extend((str(case_id), row) for case_id, row in group.items())
        return rows

    def load_case(self, native_case_id: str) -> dict[str, Any]:
        matches = [row for case_id, row in self._rows() if case_id == native_case_id]
        if len(matches) != 1:
            raise FileNotFoundError(f"MCPTox case is missing or non-unique: {native_case_id}")
        return matches[0]

    def _servers(self) -> dict[str, dict[str, Any]]:
        source = json.loads(self.response_path.read_text(encoding="utf-8"))
        servers = source.get("servers") if isinstance(source, dict) else None
        if not isinstance(servers, dict):
            raise RuntimeError("MCPTox response_all.json has no servers object")
        return servers

    def clean_tool_definitions(self, server_name: str) -> list[dict[str, Any]]:
        server = self._servers().get(server_name)
        if not isinstance(server, dict):
            raise FileNotFoundError(f"MCPTox server fixture is missing: {server_name}")
        prompt = server.get("clean_system_promot")
        if not isinstance(prompt, str):
            raise RuntimeError(f"MCPTox clean tool prompt is missing: {server_name}")
        tools = []
        for name, description, argument_block in _TOOL_BLOCK.findall(prompt):
            arguments = []
            for line in argument_block.splitlines():
                match = _ARGUMENT.match(line.strip())
                if match:
                    arguments.append(
                        {
                            "name": match.group(1),
                            "description": match.group(2),
                            "required": line.rstrip().endswith("(required)"),
                        }
                    )
            tools.append(
                {
                    "name": name.strip(),
                    "description": description.strip(),
                    "arguments": arguments,
                }
            )
        if not tools:
            raise RuntimeError(f"MCPTox clean tool definitions could not be parsed: {server_name}")
        return tools

    def source_fixture(self, native_case_id: str) -> dict[str, Any]:
        case = self.load_case(native_case_id)
        return {
            "native_case_id": native_case_id,
            "query": case["query"],
            "server_name": case["server_name"],
            "clean_tools": self.clean_tool_definitions(case["server_name"]),
            "poisoned_tool": {
                "name": case["tool_name"],
                "description": case["tool_content"],
                "arguments": [],
            },
            "security_risk": case["security risk"],
            "paradigm": case["paradigm"],
            "execution_enabled": False,
        }

    def preflight(self) -> dict[str, Any]:
        required = [self.cases_path, self.response_path, self.fragments_root]
        missing = [str(path) for path in required if not path.exists()]
        if missing:
            raise RuntimeError(f"MCPTox source artifacts are missing: {missing}")
        rows = self._rows()
        case_ids = [case_id for case_id, _ in rows]
        if len(rows) != 485 or len(set(case_ids)) != 485:
            raise RuntimeError("MCPTox expected exactly 485 unique pure_tool cases")
        servers = self._servers()
        if len(servers) != 45:
            raise RuntimeError(f"MCPTox expected 45 server fixtures, got {len(servers)}")

        missing_servers = []
        missing_fragments = []
        fragment_name_mismatches = []
        runnable_fragment_count = 0
        clean_tool_counts: dict[str, int] = {}
        declared_tool_count_mismatches = []
        for server_name, server in servers.items():
            parsed = self.clean_tool_definitions(server_name)
            clean_tool_counts[server_name] = len(parsed)
            declared = server.get("tool_names")
            if isinstance(declared, list) and set(declared) != {tool["name"] for tool in parsed}:
                declared_tool_count_mismatches.append(
                    {
                        "server_name": server_name,
                        "declared": len(declared),
                        "parsed": len(parsed),
                    }
                )
        for case_id, row in rows:
            if row.get("server_name") not in servers:
                missing_servers.append(case_id)
            fragment = self.root / str(row.get("tool_address") or "")
            if not fragment.is_file():
                missing_fragments.append(case_id)
                continue
            tree = ast.parse(fragment.read_text(encoding="utf-8"))
            functions = [
                node
                for node in tree.body
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            ]
            if len(functions) != 1 or functions[0].name != row.get("tool_name"):
                fragment_name_mismatches.append(
                    {
                        "case_id": case_id,
                        "declared": row.get("tool_name"),
                        "python_name": functions[0].name if len(functions) == 1 else None,
                    }
                )
            has_runtime_binding = any(
                isinstance(node, (ast.Import, ast.ImportFrom, ast.Assign, ast.AnnAssign))
                for node in tree.body
            )
            has_entrypoint = any(isinstance(node, ast.If) for node in tree.body)
            runnable_fragment_count += int(has_runtime_binding and has_entrypoint)

        license_files = [
            path.name
            for path in self.root.iterdir()
            if path.is_file() and path.name.casefold().startswith(("license", "copying"))
        ]
        blockers = [
            "485 def_tool files are decorator fragments with no MCP runtime binding or entrypoint",
            "no fresh-run native evaluator or case-level success mapping is provided",
            "response_all.json labels are prerecorded model outputs and are excluded from scoring",
            "clean tool prompts omit argument types needed for strict executable MCP schemas",
            "no local redistribution license evidence is present",
        ]
        if missing_servers or missing_fragments:
            blockers.append("source case/server/fragment mappings are incomplete")
        if declared_tool_count_mismatches:
            blockers.append("parsed clean tools disagree with declared tool_names for one or more servers")
        return {
            "benchmark_version": self.benchmark_version,
            "qualification_status": "blocked_source_only",
            "formal_release_ready": False,
            "case_count": len(rows),
            "server_count": len(servers),
            "risk_counts": dict(Counter(row["security risk"] for _, row in rows)),
            "paradigm_counts": dict(Counter(row["paradigm"] for _, row in rows)),
            "clean_tool_count": sum(clean_tool_counts.values()),
            "runnable_fragment_count": runnable_fragment_count,
            "missing_server_count": len(missing_servers),
            "missing_fragment_count": len(missing_fragments),
            "fragment_name_mismatches": fragment_name_mismatches,
            "declared_tool_count_mismatches": declared_tool_count_mismatches,
            "license_files": license_files,
            "prerecorded_labels_allowed_for_scoring": False,
            "blockers": blockers,
        }
