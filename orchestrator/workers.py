"""Budgeted native fixture worker. Real providers are deliberately not dispatched here."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

from .registry import bind_run, canonical_json, content_hash, file_hash, make_identity

ROOT = Path(__file__).resolve().parents[1]


def read_rows(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8-sig").splitlines() if line.strip()]


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        handle.write(canonical_json(data) + "\n")


def write_rows(path, rows):
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        handle.write("".join(canonical_json(row) + "\n" for row in rows))


def fixture_identity(public, private, agent):
    source = ROOT / "harness/src/agent_safety_harness"
    return make_identity(track="agent_execution", inputs_hash=content_hash(public),
                         effective_prompt={"transport": "public_capture_v1"},
                         target={"agent": agent, "source_hash": file_hash(source / "agents.py")},
                         environment={"kind": "mock", "python": list(sys.version_info[:3]),
                                      "entrypoint_hash": file_hash(ROOT/"harness/ash.py"),
                                      "worker_hash": file_hash(Path(__file__)),
                                      "source_tree": {str(p.relative_to(source)): file_hash(p) for p in sorted(source.rglob("*.py"))},
                                      "configs": {p.name: file_hash(p) for p in sorted((ROOT/"harness/config").glob("*.json"))}},
                         scorer={"private_rules_hash": content_hash(private),
                                 "sources": {p.name: file_hash(p) for p in sorted((source / "evaluation").glob("*.py"))}},
                         dataset_version="fixture-" + content_hash(public), seed=0)


def run_fixture(public: list[dict], private: list[dict], *, out: Path, agent: str, ledger, bucket: str, job_id: str):
    if agent not in {"safe_stub", "unsafe_stub"}:
        raise ValueError("This worker supports only the two local fixture agents")
    if any("evaluation" in t or "attack_goal" in t for t in public):
        raise ValueError("Public task contains private scoring information")
    public_by_id, private_by_id = ({t["task_id"]: t for t in rows} for rows in (public, private))
    if (not public or any(not isinstance(t, str) or not t.strip() for t in public_by_id)
            or len(public_by_id) != len(public) or len(private_by_id) != len(private)
            or set(public_by_id) != set(private_by_id)):
        raise ValueError("Public/private tasks require unique matching nonempty populations")
    for task_id, task in private_by_id.items():
        projection = {k: v for k, v in task.items() if k not in {"evaluation", "attack_goal"}}
        if projection != public_by_id[task_id]:
            raise ValueError("Public/private runtime fields differ; refusing mismatched scoring")
    identity = fixture_identity(public, private, agent)
    bind_run(out, identity, result_names=("worker_result.json",))
    receipt = out / "worker_result.json"
    if receipt.exists():
        result = json.loads(receipt.read_text(encoding="utf-8"))
        if result.get("identity_hash") != identity["identity_hash"]:
            raise ValueError("Worker receipt identity mismatch")
        for relative, digest in result["artifacts"].items():
            path = (out / relative).resolve()
            if not path.is_relative_to(out.resolve()) or file_hash(path) != digest:
                raise ValueError("Worker artifact changed after capture")
        return result
    # A prior uncertain job is not re-executed or refunded on resume.
    if not ledger.reserve(job_id, bucket, len(public), 0):
        raise ValueError("incomplete_job_requires_reconciliation: refusing uncertain repeat execution")
    public_path, private_path = out / "public.jsonl", out / "private.jsonl"
    write_rows(public_path, public)
    write_rows(private_path, private)
    ledger.start(job_id)
    commands = [
        [sys.executable, str(ROOT/"harness/ash.py"), "capture", "--agent", agent,
         "--input", str(public_path), "--out", str(out/"capture"), "--max-attempts", "1"],
        [sys.executable, str(ROOT/"harness/ash.py"), "score", "--private-input", str(private_path),
         "--capture-run", str(out/"capture"), "--out", str(out/"score")],
    ]
    try:
        for name, argv in zip(("capture", "score"), commands):
            with (out / (name + ".log")).open("x", encoding="utf-8") as log:
                process = subprocess.run(argv, cwd=ROOT, env={**os.environ, "PYTHONUTF8": "1"},
                                         stdout=log, stderr=subprocess.STDOUT, timeout=120, check=False)
            if process.returncode:
                raise RuntimeError(f"Native {name} failed; see {out / (name + '.log')}")
    finally:
        # These agents are local Python stubs; monetary cost is known to be zero.
        # Execution reservations are charged even if the capture or scoring failed.
        ledger.settle(job_id, 0)
    artifacts = {str(path.relative_to(out)): file_hash(path) for path in
                 [public_path, private_path, out/"capture/traces.jsonl", out/"capture/capture_results.jsonl",
                  out/"score/results.jsonl", out/"score/summary.json"]}
    result = dict(kind="fixture", identity_hash=identity["identity_hash"], agent=agent,
                  formal_qualified=False, artifacts=artifacts, results_ref="score/results.jsonl")
    write_json(receipt, result)
    return result
