"""Real registry operations, reusing source intake, native capture and private score.

The public worker runs in an unprivileged, read-only, network-disabled container.
Only its public input, projected source environment and output directory are mounted.
Private tasks are opened exclusively by score(), outside that container.
"""
from __future__ import annotations

from contextlib import redirect_stdout
from io import StringIO
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import uuid

from .config import default_config_dir, load_json_config
from .io import read_jsonl, write_json, write_jsonl
from .public_capture import assert_public_record
from .schema import TaskSpec

ROOT = Path(__file__).resolve().parents[3]
HARNESS = ROOT / "harness"


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def object_hash(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()


def empty_directory(path: str | Path) -> Path:
    path = Path(path).expanduser().resolve()
    if path.exists() and (not path.is_dir() or any(path.iterdir())):
        raise FileExistsError(f"Use a new or empty output directory: {path}")
    path.mkdir(parents=True, exist_ok=True)
    return path


def profile_for(benchmark_id: str) -> dict:
    profiles = load_json_config(default_config_dir() / "engine_benchmarks.json")["benchmarks"]
    if benchmark_id not in profiles:
        raise ValueError(f"No approved engine profile: {benchmark_id!r}")
    return profiles[benchmark_id]


def run_process(argv: list[str], *, cwd: Path | None = None, input: str | None = None, timeout: int = 300) -> str:
    # No shell strings. Never place credentials into argv or return provider logs.
    completed = subprocess.run(argv, cwd=cwd, input=input, capture_output=True, text=True,
                               encoding="utf-8", errors="replace", timeout=timeout, check=False)
    if completed.returncode:
        raise RuntimeError(f"{Path(argv[0]).name} operation failed (exit {completed.returncode}); no success receipt written")
    return completed.stdout


def verify_source(root: Path, profile: dict) -> dict:
    revision = run_process(["git", "-C", str(root), "rev-parse", "HEAD"]).strip()
    if revision != profile["revision"]:
        raise ValueError("source_revision_mismatch")
    if run_process(["git", "-C", str(root), "diff", "HEAD", "--name-only"]).strip():
        raise ValueError("source_worktree_modified")
    paths = run_process(["git", "-C", str(root), "ls-files", "-z"]).split("\0")
    hashes = {}
    for name in filter(None, paths):
        path = root / name
        if path.is_symlink() or not path.is_file():
            raise ValueError("source_non_regular_file")
        hashes[name] = digest(path)
    for name, expected in profile["required_files"].items():
        # Compare the Git blob, independent of a collaborator's checkout EOL policy.
        blob = subprocess.run(["git", "-C", str(root), "show", f"HEAD:{name}"], capture_output=True, check=True).stdout
        if hashlib.sha256(blob).hexdigest() != expected:
            raise ValueError(f"source_hash_mismatch: {name}")
    return {"revision": revision, "file_hashes": hashes, "tree_hash": object_hash(hashes)}


def bench_intake(benchmark_id: str, revision: str | None = None, *, source_root: str | None = None,
                 out: str, allow_fetch: bool = False, intake_config: str | None = None) -> dict:
    profile = profile_for(benchmark_id)
    revision = revision or profile["revision"]
    if not re.fullmatch(r"[0-9a-f]{40}", revision) or revision != profile["revision"]:
        raise ValueError("An approved immutable 40-character source revision is required")
    source = Path(source_root).expanduser().resolve() if source_root else ROOT / ".runtime/sources" / benchmark_id
    if not source.exists():
        if not allow_fetch:
            raise FileNotFoundError("Pinned source missing; explicitly allow_fetch or provide source_root")
        source.parent.mkdir(parents=True, exist_ok=True)
        run_process(["git", "-c", "core.autocrlf=false", "clone", "--no-checkout", "--filter=blob:none", profile["repository"], str(source)])
        run_process(["git", "-C", str(source), "checkout", "--detach", revision])
    identity = verify_source(source, profile)
    destination = empty_directory(out)
    # source_intake is the single implementation; direct Python call, not a CLI subprocess.
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    from scripts.source_intake import main as source_intake
    argv = ["--benchmark-root", str(source / profile["data_directory"]), "--protocol-dir", str(ROOT / "protocol"),
            "--output-dir", str(destination / "phase1"), "--format", "json"]
    if intake_config:
        argv += ["--intake-config", str(Path(intake_config).resolve())]
    with redirect_stdout(StringIO()):
        code = source_intake(argv)
    if code:
        raise RuntimeError("native_source_intake_failed")
    catalog = destination / "phase1/dataset_catalog.json"
    report = destination / "phase1/reports/field_mapping_report.json"
    receipt = {"schema_version": "engine_intake_v1", "benchmark_id": benchmark_id,
               "source_root": str(source), "repository": profile["repository"], **identity,
               "profile_hash": object_hash(profile), "catalog": str(catalog), "catalog_hash": digest(catalog),
               "mapping_report": str(report), "mapping_report_hash": digest(report),
               "status": "source_registered", "canonical_ready": bool(load_json_config(catalog)["benchmarks"])}
    write_json(destination / "intake.json", receipt)
    return receipt


def verified_intake(benchmark_id: str, intake: str) -> tuple[dict, dict]:
    receipt = load_json_config(intake)
    profile = profile_for(benchmark_id)
    if receipt.get("benchmark_id") != benchmark_id or receipt.get("profile_hash") != object_hash(profile):
        raise ValueError("intake_profile_mismatch")
    identity = verify_source(Path(receipt["source_root"]), profile)
    if identity["tree_hash"] != receipt["tree_hash"] or digest(Path(receipt["catalog"])) != receipt["catalog_hash"]:
        raise ValueError("intake_artifact_changed")
    return receipt, profile


def build_sandbox(benchmark_id: str, *, intake: str, out: str) -> dict:
    receipt, profile = verified_intake(benchmark_id, intake)
    environment = profile["environment"]
    if environment["backend"] != "docker" or environment["network"] != "none":
        raise ValueError("Unsupported isolation profile")
    run_process(["docker", "info", "--format", "{{.ServerVersion}}"], timeout=30)
    destination = empty_directory(out)
    base = environment["base_image"]
    run_process(["docker", "pull", base], timeout=600)
    base_info = json.loads(run_process(["docker", "image", "inspect", base]))[0]
    base_digest = base_info.get("RepoDigests", [])
    if not base_digest:
        raise ValueError("Base image has no immutable registry digest")
    context = destination / "context"
    context.mkdir()
    shutil.copytree(HARNESS / "src", context / "src", ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    shutil.copytree(HARNESS / "config", context / "config")
    shutil.copytree(ROOT / "protocol", context / "protocol")
    shutil.copy2(HARNESS / "runtime/Dockerfile", context / "Dockerfile")
    context_hash = object_hash({p.relative_to(context).as_posix(): digest(p) for p in sorted(context.rglob("*")) if p.is_file()})
    image_tag = "ash-engine:" + context_hash[:20]
    run_process(["docker", "build", "--build-arg", "BASE_IMAGE=" + base_digest[0], "-t", image_tag, str(context)], timeout=600)
    image = json.loads(run_process(["docker", "image", "inspect", image_tag]))[0]["Id"]
    # Exercise the actual restrictions; a profile declaration alone is insufficient.
    probe = json.loads(run_process(container_command(image, [], "probe-" + uuid.uuid4().hex, environment), input=json.dumps({"action": "probe"}), timeout=45))
    if (probe.get("uid") != 65534 or probe.get("root_read_only") is not True or probe.get("external_network_available") is not False
            or probe.get("interfaces") != ["lo"] or probe.get("effective_capabilities") != "0000000000000000"
            or probe.get("no_new_privileges") is not True):
        raise RuntimeError("isolation_probe_failed")
    manifest = {"schema_version": "engine_runtime_v1", "benchmark_id": benchmark_id, "backend": "docker",
                "image_id": image, "base_image_digest": base_digest[0], "context_hash": context_hash,
                "source_tree_hash": receipt["tree_hash"], "intake": str(Path(intake).resolve()),
                "profile_hash": object_hash(profile), "probe": probe, "status": "runtime_ready",
                "formal_benchmark_qualified": False, "network": "none"}
    write_json(destination / "runtime.json", manifest)
    return manifest


def container_command(image: str, mounts: list[tuple[Path, str, bool]], name: str, environment: dict | None = None) -> list[str]:
    limits = environment or {"pids_limit": 128, "memory": "512m", "cpus": "1"}
    command = ["docker", "run", "--rm", "-i", "--name", name, "--network", "none", "--read-only",
               "--cap-drop", "ALL", "--security-opt", "no-new-privileges", "--pids-limit", str(limits["pids_limit"]),
               "--memory", str(limits["memory"]), "--cpus", str(limits["cpus"]), "--user", "65534:65534",
               "--tmpfs", "/tmp:rw,nosuid,noexec,size=67108864"]
    for source, target, readonly in mounts:
        source = source.resolve()
        if "," in str(source):
            raise ValueError("Mount paths containing commas are unsupported")
        command += ["--mount", f"type=bind,source={source},target={target}" + (",readonly" if readonly else "")]
    return command + [image]


def run_capture(benchmark_id: str, *, runtime: str, tasks: str, out: str, agent: str,
                agents_config: str | None = None) -> dict:
    manifest = load_json_config(runtime)
    receipt, profile = verified_intake(benchmark_id, manifest["intake"])
    if (manifest.get("status") != "runtime_ready" or manifest.get("backend") != "docker"
            or manifest.get("source_tree_hash") != receipt["tree_hash"] or manifest.get("profile_hash") != object_hash(profile)
            or not re.fullmatch(r"sha256:[0-9a-f]{64}", manifest.get("image_id", ""))):
        raise ValueError("runtime_identity_mismatch")
    records = read_jsonl(Path(tasks))
    if not records:
        raise ValueError("No public tasks")
    ids = []
    for record in records:
        assert_public_record(record)
        task = TaskSpec.from_record(record)
        if task.modality != "agent" or task.benchmark != benchmark_id:
            raise ValueError("Capture runtime/benchmark/modality mismatch")
        ids.append(task.task_id)
    if len(set(ids)) != len(ids):
        raise ValueError("Duplicate task IDs")
    destination = empty_directory(out)
    # Build a public-only projection, never mount the directory holding private rules.
    public = destination / "public"
    public.mkdir()
    write_jsonl(public / "tasks.jsonl", records)
    source = Path(receipt["source_root"])
    projected = public / "source"
    for name in receipt["file_hashes"]:
        if name.startswith("environments/"):
            target = projected / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source / name, target)
    selected = {int(i.rsplit(":", 1)[-1]) for i in ids}
    source_rows = json.loads((source / "data/released_data.json").read_text(encoding="utf-8"))
    (projected / "data").mkdir()
    rows = [{k: r[k] for k in ("id", "instruction", "environments")} for r in source_rows if r["id"] in selected]
    if len(rows) != len(ids):
        raise ValueError("Public task ID missing in pinned source")
    write_json(projected / "data/released_data.json", rows)
    if agents_config:
        # This file contains public target configuration only; no private scorer input.
        config = load_json_config(agents_config)
        assert_public_record(config)
        write_json(public / "agents.json", config)
    capture = destination / "capture"
    capture.mkdir()
    name = "ash-capture-" + uuid.uuid4().hex
    command = container_command(manifest["image_id"], [(public, "/public", True), (capture, "/output", False)], name, profile["environment"])
    request = {"action": "capture", "agent": agent, "agents_config": "/public/agents.json" if agents_config else None}
    try:
        run_process(command, input=json.dumps(request), timeout=300)
    except subprocess.TimeoutExpired:
        subprocess.run(["docker", "rm", "-f", name], capture_output=True, timeout=30)
        raise
    captures = read_jsonl(capture / "capture_results.jsonl")
    if {r["task_id"] for r in captures} != set(ids) or len(captures) != len(ids):
        raise ValueError("Capture result IDs do not match public tasks")
    result = {"schema_version": "engine_capture_v1", "benchmark_id": benchmark_id, "phase": "capture",
              "capture_run": str(capture), "task_count": len(ids), "source_tree_hash": receipt["tree_hash"],
              "runtime_hash": digest(Path(runtime)), "public_tasks_hash": digest(public / "tasks.jsonl"),
              "public_tasks": str(public / "tasks.jsonl"),
              "captures_hash": digest(capture / "capture_results.jsonl"), "traces_hash": digest(capture / "traces.jsonl"),
              "contains_private_oracle": False, "agent": agent,
              "status": "captured" if all(not r.get("evidence", {}).get("error_type") or r["evidence"]["error_type"] == "EvaluationRuleUnavailable" for r in captures) else "capture_invalid"}
    write_json(destination / "capture_receipt.json", result)
    return result


def score(benchmark_id: str, *, capture_receipt: str, private_tasks: str, out: str) -> dict:
    receipt = load_json_config(capture_receipt)
    capture = Path(receipt["capture_run"])
    if receipt.get("benchmark_id") != benchmark_id or receipt.get("contains_private_oracle") is not False:
        raise ValueError("capture_receipt_mismatch")
    for filename, key in [("capture_results.jsonl", "captures_hash"), ("traces.jsonl", "traces_hash")]:
        if digest(capture / filename) != receipt[key]:
            raise ValueError("capture_artifact_changed")
    if receipt.get("public_tasks"):
        public_path = Path(receipt["public_tasks"])
        if digest(public_path) != receipt["public_tasks_hash"]:
            raise ValueError("public_capture_input_changed")
        public_rows = {r["task_id"]: TaskSpec.from_record(r) for r in read_jsonl(public_path)}
    else:
        raise ValueError("capture_public_input_binding_missing")
    for record in read_jsonl(Path(private_tasks)):
        task = TaskSpec.from_record(record)
        if task.benchmark != benchmark_id or task.modality != "agent":
            raise ValueError("Private task benchmark/modality mismatch")
        public_task = public_rows.get(task.task_id)
        if public_task is None or any(getattr(public_task, key) != getattr(task, key)
                                      for key in ("benchmark", "modality", "profile", "taxonomy_ref", "user_goal", "tools")):
            raise ValueError("private_public_task_identity_mismatch")
    from .cli import build_parser, command_score
    destination = Path(out).expanduser().resolve()
    args = build_parser().parse_args(["score", "--capture-run", str(capture), "--private-input", private_tasks, "--out", str(destination)])
    with redirect_stdout(StringIO()):
        code = command_score(args)
    result = {"schema_version": "engine_score_v1", "benchmark_id": benchmark_id,
              "status": "scored" if code == 0 else "scored_with_unknown", "native_exit_code": code,
              "results": str(destination / "results.jsonl"), "summary": str(destination / "summary.json"),
              "capture_receipt_hash": digest(Path(capture_receipt)), "private_tasks_hash": digest(Path(private_tasks))}
    write_json(destination / "score_receipt.json", result)
    return result
