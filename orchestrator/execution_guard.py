"""Product-entry preflight: declarations never establish runtime isolation."""
from __future__ import annotations

import json
import copy
from pathlib import Path
import sys

from .registry import bind_run, content_hash, file_hash, make_identity

ROOT = Path(__file__).resolve().parents[1]
MODES = {"container", "vm", "disposable_workspace"}
NETWORKS = {"deny_except_benchmark", "deny_all", "benchmark_defined"}


def product_preflight(request: dict, *, dry_run: bool) -> dict:
    isolation = request.get("isolation")
    required = {"mode", "fresh_product_state", "network_policy", "tool_permission_mode"}
    if not isinstance(isolation, dict) or set(isolation) != required:
        raise ValueError("isolation_declaration_invalid")
    if (isolation["mode"] not in MODES or isolation["network_policy"] not in NETWORKS
            or isolation["fresh_product_state"] is not True
            or isolation["tool_permission_mode"] != "autonomous_preapproved_in_sandbox"):
        raise ValueError("isolation_declaration_invalid")
    # The existing entry launches a host subprocess. Neither an environment flag
    # nor a request saying 'container' can turn that into a verified sandbox.
    return dict(schema_version="product_preflight_v1", status="plan_only" if dry_run else "blocked",
                execution_permitted=False, isolation_verified=False, formal_ready=False,
                requested_isolation=dict(isolation), paid_calls_dispatched=0,
                blockers=[] if dry_run else ["verified_isolation_launcher_not_registered"],
                note="Dry-run planning is available; live host execution cannot enforce the declared isolation.")


def require_product_execution(request: dict, *, dry_run: bool) -> dict:
    report = product_preflight(request, dry_run=dry_run)
    if not dry_run:
        raise ValueError("isolation_unverified: live product execution blocked before native/product/judge calls; "
                         "a verified isolation launcher must be integrated")
    return report


def product_identity(request: dict, selection_path: Path, args) -> dict:
    package = ROOT/"federated_asr_benchmark"
    semantic = copy.deepcopy({k: v for k, v in request.items() if k not in {"run_id", "output_dir", "selection"}})
    target = semantic["product"]
    if target["product_id"] == "qoder":
        # The BYOK wrapper replaces these fields before the single-case entry.
        # Bind the same effective plan in the batch controller.
        from product_eval.qoder_byok_compat import QODER_BYOK_CLI_VERSION, qoder_cli_executable
        target["version"] = QODER_BYOK_CLI_VERSION
        target["executable"] = str(qoder_cli_executable(package))
    selected = {"case_index_hash": file_hash(selection_path),
                "case_ids": request.get("selection", {}).get("case_ids")}
    runtime = {name: str(Path(value).expanduser().resolve()) if value is not None else None
               for name in ("data_root", "saber_root", "agentdojo_root", "agentdojo_python", "agentdyn_root",
                            "agentdyn_python", "agentharm_data_root", "agentharm_runtime_root", "injecagent_root",
                            "msb_root", "agentleak_root", "agentpoison_root", "state_source")
               for value in [getattr(args, name, None)]}
    return make_identity(track="agent_product", inputs_hash=content_hash(selected),
                         effective_prompt={"request": semantic}, target=target,
                         environment={"python": list(sys.version_info[:3]), "kind": "dry_run_plan",
                                      "runtime_paths": runtime, "guard_hash": file_hash(Path(__file__)),
                                      "entry_hashes": {p.name: file_hash(p) for p in
                                                       (package/"tools/run_product.py", package/"tools/run_product_batch.py", package/"tools/run_product_qoder_byok.py")}},
                         scorer={"sources": {p.name: file_hash(p) for p in sorted((package/"product_eval").glob("*.py"))}},
                         dataset_version="selected-"+file_hash(selection_path), seed=request.get("seed", 42))


def bind_product_run(out: Path, identity: dict, report: dict):
    if not (out/"run_identity.json").exists() and out.exists() and any(out.iterdir()):
        raise ValueError("legacy_identity_missing: use a new product output directory")
    bind_run(out, identity)
    path = out/"execution_preflight.json"
    if path.exists():
        if json.loads(path.read_text(encoding="utf-8")) != report:
            raise ValueError("product_preflight_changed: use a new output directory")
    else:
        with path.open("x", encoding="utf-8", newline="\n") as stream:
            json.dump(report, stream, ensure_ascii=False, sort_keys=True, indent=2)
            stream.write("\n")
