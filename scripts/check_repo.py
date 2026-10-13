#!/usr/bin/env python3
"""Run the collaborator/CI acceptance checks with no model API calls."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import uuid
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]
IGNORED = {".git", ".venv", ".venvs", ".tmp", ".runtime", ".codex_tmp", "__pycache__",
           "runs", "work", "output", "outputs", "results", "reports", "data", "datasets"}


def check_links() -> dict:
    errors, files, links = [], 0, 0
    for directory, children, names in os.walk(ROOT):
        children[:] = sorted(child for child in children if child not in IGNORED)
        for name in sorted(names):
            if not name.endswith(".md"):
                continue
            path = Path(directory) / name
            files += 1
            content = path.read_text(encoding="utf-8-sig")
            # Fenced examples are not document links.
            content = re.sub(r"(?ms)^\s*```.*?^\s*```[^\n]*", "", content)
            targets = re.findall(r"!?\[[^\]\n]*\]\((<[^>]+>|[^)\s]+)(?:\s+[^)]*)?\)", content)
            targets += re.findall(r"(?m)^\s*\[[^\]]+\]:\s*(<[^>]+>|\S+)", content)
            for target in targets:
                target = target.strip("<>")
                parsed = urlsplit(target)
                if parsed.scheme or not parsed.path:
                    continue
                links += 1
                destination = (path.parent / unquote(parsed.path)).resolve()
                if not destination.exists():
                    errors.append(f"{path.relative_to(ROOT).as_posix()}: {target}")
    return {"markdown_files": files, "local_links": links, "errors": errors}


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--docs-only", action="store_true")
    parser.add_argument("--out", type=Path, help="New or empty log directory (default: unique runs/check-...)")
    args = parser.parse_args()
    out = args.out.resolve() if args.out else ROOT / "runs" / (
        "check-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex[:8])
    if out.exists() and (not out.is_dir() or any(out.iterdir())):
        parser.error(f"Use a new or empty output directory: {out}")
    out.mkdir(parents=True, exist_ok=True)
    summary = {"status": "started", "python": sys.version, "output": str(out), "steps": []}
    env = {key: value for key, value in os.environ.items() if not key.startswith("EVAL_")}
    env["PYTHONUTF8"] = "1"

    def save():
        (out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def run(name: str, argv: list[str], cwd: Path = ROOT):
        print(f"Running {name} ...", flush=True)
        log_path = out / f"{name}.log"
        with log_path.open("w", encoding="utf-8") as log:
            process = subprocess.run([sys.executable, *argv], cwd=cwd, env=env, stdout=log,
                                     stderr=subprocess.STDOUT, timeout=600, check=False)
        step = {"name": name, "returncode": process.returncode, "log": str(log_path)}
        text = log_path.read_text(encoding="utf-8")
        match = re.search(r"Ran (\d+) tests? in", text)
        if match:
            count = int(match.group(1))
            skipped = re.search(r"skipped=(\d+)", text)
            step.update(discovered=count, skipped=int(skipped.group(1)) if skipped else 0)
            if not process.returncode:
                step["passed"] = count - step["skipped"]
        summary["steps"].append(step)
        save()
        if process.returncode:
            raise RuntimeError(f"{name} failed ({process.returncode}); see {log_path}")

    try:
        summary["documentation"] = check_links()
        if summary["documentation"]["errors"]:
            raise RuntimeError("Broken document links: " + "; ".join(summary["documentation"]["errors"]))
        if not args.docs_only:
            run("bound-protocol", ["scripts/protocol_manager.py", "validate", "--protocol-dir", "protocol", "--repo-root", "."])
            for name, directory in (("root-tests", ROOT), ("harness-tests", ROOT / "harness"),
                                    ("product-tests", ROOT / "federated_asr_benchmark")):
                run(name, ["-m", "unittest", "discover", "-s", "tests", "-v"], directory)
            for agent, expected_pass, expected_candidates in (("safe_stub", 3, 0), ("unsafe_stub", 1, 2)):
                smoke_out = out / agent
                run(agent, ["pipeline.py", "smoke", "--agent", agent, "--out", str(smoke_out)])
                manifest = json.loads((smoke_out / "pipeline_manifest.json").read_text(encoding="utf-8"))
                native = manifest["native_summary"]
                if native["total"] != 3 or not native["run_valid"] or manifest["candidate_plan_count"] != expected_candidates:
                    raise RuntimeError(f"Unexpected {agent} smoke summary")
                rows = [json.loads(line) for line in (smoke_out / "score/results.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
                if sum(row.get("safety_pass") is True for row in rows) != expected_pass:
                    raise RuntimeError(f"Unexpected {agent} pass count")
            # Use the shipped config with an isolated output override; inherited EVAL_* values are cleared.
            env.update(EVAL_RUN_MODE="self_test", EVAL_INPUT_JSONL=str(ROOT / "assets/samples/llm_qa_demo.jsonl"),
                       EVAL_RUN_ROOT=str(out / "llm"))
            run("llm-self-test", ["llm_eval/multi_model_rule_eval_prompt_only.py", "--config", "assets/samples/llm_self_test.yaml"])
            results = out / "llm/self_test_reference/results.jsonl"
            rows = [json.loads(line) for line in results.read_text(encoding="utf-8").splitlines() if line.strip()]
            if len(rows) != 3 or {row["id"] for row in rows} != {"demo-1", "demo-2", "demo-3"} or any(
                row.get("api_status") != "success" or row.get("passed") is not True for row in rows):
                raise RuntimeError("LLM self-test did not produce the three successful fixture results")
            run("llm-analysis", ["pipeline.py", "analyze", "--track", "llm_qa", "--results", str(results), "--out", str(out / "llm-analysis")])
            analysis = json.loads((out / "llm-analysis/summary.json").read_text(encoding="utf-8"))
            if analysis["candidate_plan_count"] or analysis["unknown_count"] or analysis["admitted_candidate_count"]:
                raise RuntimeError("Unexpected LLM fixture analysis")
            summary["fixtures"] = {"safe_stub": {"pass": 3, "total": 3}, "unsafe_stub": {"pass": 1, "total": 3, "candidate_plans": 2},
                                   "llm_reference": {"pass": 3, "total": 3}, "admitted_candidates": 0}
        summary["status"] = "passed"
    except (OSError, ValueError, KeyError, RuntimeError, subprocess.TimeoutExpired) as exc:
        summary.update(status="failed", error=f"{type(exc).__name__}: {exc}")
        print(summary["error"], file=sys.stderr)
    save()
    print(f"{summary['status']}: {out / 'summary.json'}", flush=True)
    return 0 if summary["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
