from __future__ import annotations

from collections import Counter
import json

from .agentdojo import AgentDojoAdapter


class AgentDynAdapter(AgentDojoAdapter):
    benchmark_id = "agentdyn"
    benchmark_version = "v1.2.2"
    run_name = "google_gemini-2.5-flash"
    manifest_name = "agentdyn_v1_2_2"
    suites = ("shopping", "github", "dailylife")
    python_args = ("-X", "utf8")
    suite_case_counts = {"shopping": 180, "github": 180, "dailylife": 200}

    def preflight(self) -> dict[str, object]:
        result: dict[str, object] = super().preflight()
        paths = sorted(
            self.run_root.glob(
                "*/user_task_*/important_instructions/injection_task_*.json"
            )
        )
        counts: Counter[str] = Counter()
        for path in paths:
            relative = path.relative_to(self.run_root)
            suite, user_task, attack, filename = relative.parts
            source = json.loads(path.read_text(encoding="utf-8"))
            expected = {
                "benchmark_version": self.benchmark_version,
                "suite_name": suite,
                "user_task_id": user_task,
                "injection_task_id": path.stem,
                "attack_type": attack,
            }
            mismatch = {
                key: source.get(key)
                for key, expected_value in expected.items()
                if source.get(key) != expected_value
            }
            if mismatch:
                raise RuntimeError(f"AgentDyn source identity drift in {path}: {mismatch}")
            counts[suite] += 1
        if dict(counts) != self.suite_case_counts:
            raise RuntimeError(
                f"AgentDyn case lattice drift: {dict(counts)} != {self.suite_case_counts}"
            )
        result.update(
            {
                "run_root": str(self.run_root),
                "case_count": len(paths),
                "suite_case_counts": dict(counts),
            }
        )
        return result
