from __future__ import annotations

import ast
from collections import Counter
import csv
import json
from pathlib import Path, PurePosixPath
from typing import Any


class OpenAgentSafetyAdapter:
    """Fail-closed source contract for the OpenAgentSafety task collection."""

    benchmark_id = "openagentsafety"
    benchmark_version = "dataset-source-review"
    required_task_artifacts = (
        PurePosixPath("task.md"),
        PurePosixPath("checkpoints.md"),
        PurePosixPath("utils/evaluator.py"),
        PurePosixPath("utils/dependencies.yml"),
    )

    def __init__(self, root: Path):
        self.root = root.resolve()
        self.index_path = self.root / "OpenAgentSafety_tasks.csv"
        self.tasks_root = self.root / "tasks"
        self.readme_path = self.root / "README.md"

    def index_rows(self) -> list[dict[str, str]]:
        with self.index_path.open("r", encoding="utf-8-sig", newline="") as handle:
            source_rows = list(csv.DictReader(handle))
        rows = [
            row
            for row in source_rows
            if str(row.get("Task Name") or "").strip()
        ]
        return rows

    def _task_dir(self, row: dict[str, str]) -> Path:
        native_id = str(row.get("Task Name") or "").strip()
        path_text = str(row.get("path") or "").strip()
        expected = PurePosixPath("tasks") / f"safety-{native_id}"
        if PurePosixPath(path_text) != expected:
            raise RuntimeError(
                f"OpenAgentSafety task path does not match its identity: {native_id}"
            )
        task_dir = self.root.joinpath(*expected.parts)
        if task_dir.resolve() != task_dir.absolute() or task_dir.is_symlink():
            raise RuntimeError(f"OpenAgentSafety task path is unsafe: {path_text}")
        return task_dir

    def load_case(self, native_case_id: str) -> dict[str, Any]:
        matches = [
            row
            for row in self.index_rows()
            if str(row.get("Task Name") or "").strip() == native_case_id
        ]
        if len(matches) != 1:
            raise FileNotFoundError(
                f"OpenAgentSafety case is missing or non-unique: {native_case_id}"
            )
        row = matches[0]
        task_dir = self._task_dir(row)
        missing = [
            artifact.as_posix()
            for artifact in self.required_task_artifacts
            if not task_dir.joinpath(*artifact.parts).is_file()
        ]
        if missing:
            raise FileNotFoundError(
                f"OpenAgentSafety case has incomplete source artifacts: {native_case_id}: {missing}"
            )
        return {
            "native_case_id": native_case_id,
            "index": row,
            "task_dir": task_dir,
            "prompt": (task_dir / "task.md").read_text(encoding="utf-8-sig"),
        }

    @staticmethod
    def build_product_prompt(case: dict[str, Any]) -> str:
        prompt = case.get("prompt")
        if not isinstance(prompt, str) or not prompt.strip():
            raise RuntimeError("OpenAgentSafety case has no task.md prompt")
        # The dataset contract explicitly permits only task.md to be shown to products.
        return prompt

    @staticmethod
    def _evaluator_contract(path: Path) -> dict[str, Any]:
        tree = ast.parse(path.read_text(encoding="utf-8-sig"))
        grade_functions = [
            node
            for node in tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == "grade_checkpoints"
        ]
        imports = set()
        for node in tree.body:
            if isinstance(node, ast.Import):
                imports.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                imports.add((node.module or "").split(".")[0])
        signature_ok = (
            len(grade_functions) == 1
            and not isinstance(grade_functions[0], ast.AsyncFunctionDef)
            and [argument.arg for argument in grade_functions[0].args.args]
            == ["trajectory"]
        )
        required_imports_ok = "scoring" in imports
        return {
            "complete": signature_ok and required_imports_ok,
            "grade_checkpoints_count": len(grade_functions),
            "signature_ok": signature_ok,
            "required_imports_ok": required_imports_ok,
            "imports": sorted(imports),
        }

    @staticmethod
    def _dependencies(path: Path) -> list[str]:
        dependencies = []
        for line in path.read_text(encoding="utf-8-sig").splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            if not stripped.startswith("- ") or not stripped[2:].strip():
                raise RuntimeError(
                    f"OpenAgentSafety dependencies.yml is not a string list: {path}"
                )
            dependencies.append(stripped[2:].strip())
        return dependencies

    def preflight(self) -> dict[str, Any]:
        required = [self.index_path, self.tasks_root, self.readme_path]
        missing = [str(path) for path in required if not path.exists()]
        if missing:
            raise RuntimeError(f"OpenAgentSafety source artifacts are missing: {missing}")

        with self.index_path.open("r", encoding="utf-8-sig", newline="") as handle:
            source_rows = list(csv.DictReader(handle))
        rows = self.index_rows()
        if len(source_rows) != 356 or len(rows) != 355:
            raise RuntimeError(
                "OpenAgentSafety expected 356 CSV rows and 355 named candidates"
            )
        native_ids = [str(row.get("Task Name") or "").strip() for row in rows]
        if len(set(native_ids)) != len(native_ids):
            raise RuntimeError("OpenAgentSafety task identities are not unique")

        indexed_dirs = set()
        incomplete: list[dict[str, Any]] = []
        evaluator_incomplete: list[dict[str, Any]] = []
        dependency_counts: Counter[str] = Counter()
        import_counts: Counter[str] = Counter()
        workspace_count = 0
        scenario_count = 0
        scenario_model_counts: Counter[str] = Counter()
        source_complete_count = 0
        for row in rows:
            native_id = str(row["Task Name"]).strip()
            task_dir = self._task_dir(row)
            indexed_dirs.add(task_dir.name)
            missing_artifacts = [
                artifact.as_posix()
                for artifact in self.required_task_artifacts
                if not task_dir.joinpath(*artifact.parts).is_file()
            ]
            if missing_artifacts:
                incomplete.append(
                    {
                        "native_case_id": native_id,
                        "task_path": str(row["path"]).strip(),
                        "missing": missing_artifacts,
                    }
                )
                continue
            source_complete_count += 1
            workspace_count += int((task_dir / "workspace").is_dir())
            scenario_path = task_dir / "scenarios.json"
            scenario_count += int(scenario_path.is_file())
            if scenario_path.is_file():
                scenario = json.loads(scenario_path.read_text(encoding="utf-8-sig"))
                models = scenario.get("agent_models") if isinstance(scenario, dict) else None
                if isinstance(models, dict):
                    scenario_model_counts.update(str(model) for model in models.values())
            dependencies = self._dependencies(task_dir / "utils" / "dependencies.yml")
            dependency_counts.update(dependencies)
            evaluator = self._evaluator_contract(task_dir / "utils" / "evaluator.py")
            import_counts.update(evaluator["imports"])
            if not evaluator["complete"]:
                evaluator_incomplete.append(
                    {
                        "native_case_id": native_id,
                        "grade_checkpoints_count": evaluator["grade_checkpoints_count"],
                        "signature_ok": evaluator["signature_ok"],
                        "required_imports_ok": evaluator["required_imports_ok"],
                    }
                )

        actual_dirs = {
            path.name for path in self.tasks_root.iterdir() if path.is_dir()
        }
        unindexed_dirs = sorted(actual_dirs - indexed_dirs)
        if source_complete_count != 348 or len(incomplete) != 7:
            raise RuntimeError("OpenAgentSafety source-complete case lattice drifted")
        if len(evaluator_incomplete) != 10:
            raise RuntimeError("OpenAgentSafety evaluator contract lattice drifted")
        if dict(dependency_counts) != {"owncloud": 83, "gitlab": 47, "plane": 7}:
            raise RuntimeError(
                f"OpenAgentSafety service dependency lattice drifted: {dict(dependency_counts)}"
            )
        if scenario_count != 189 or workspace_count != 173:
            raise RuntimeError("OpenAgentSafety isolation artifact lattice drifted")
        if dict(scenario_model_counts) != {"gpt-4o": 224}:
            raise RuntimeError("OpenAgentSafety scenario model routes drifted")

        readme = self.readme_path.read_text(encoding="utf-8-sig")
        normalized_readme = " ".join(
            readme.casefold().replace("*", "").replace("`", "").split()
        )
        if "cc-by-4.0" not in normalized_readme or "only file" not in normalized_readme:
            raise RuntimeError("OpenAgentSafety license or prompt-boundary evidence drifted")

        qualified_source_count = source_complete_count - len(evaluator_incomplete)
        return {
            "benchmark_version": self.benchmark_version,
            "qualification_status": "blocked_harness_runtime",
            "formal_release_ready": False,
            "csv_row_count": len(source_rows),
            "named_candidate_count": len(rows),
            "unnamed_rows_excluded": len(source_rows) - len(rows),
            "source_complete_count": source_complete_count,
            "evaluator_contract_complete_count": qualified_source_count,
            "incomplete_source_cases": incomplete,
            "incomplete_evaluator_cases": evaluator_incomplete,
            "unindexed_task_directories": unindexed_dirs,
            "workspace_case_count": workspace_count,
            "scenario_case_count": scenario_count,
            "service_dependency_counts": dict(dependency_counts),
            "scenario_model_counts": dict(scenario_model_counts),
            "evaluator_import_counts": dict(import_counts),
            "license": "CC-BY-4.0",
            "product_prompt_source": "task.md only",
            "prerecorded_labels_allowed_for_scoring": False,
            "blockers": [
                "seven indexed tasks lack required source artifacts and must be excluded",
                "ten source-complete tasks lack grade_checkpoints(trajectory) and must be excluded",
                "the dataset omits the common and scoring harness modules imported by every valid evaluator",
                "OwnCloud, GitLab, and Plane service fixtures are not provisioned per isolated case",
                "ChatNPC scenarios pin gpt-4o and are not adapted to the immutable tested-model profile",
                "fresh product trajectories and deterministic grader outputs are not yet persisted and smoke-audited",
            ],
        }
