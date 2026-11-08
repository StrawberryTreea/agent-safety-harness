from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any


PACKAGE_ROOT = Path(__file__).resolve().parents[1]
WORKSPACE_ROOT = PACKAGE_ROOT.parent
sys.path.insert(0, str(PACKAGE_ROOT))
sys.path.append(str(WORKSPACE_ROOT))

from orchestrator.execution_guard import product_preflight, require_product_execution, product_identity, bind_product_run

from product_eval.runner import ProductIdentity, ProductRunner, default_state_source
from product_eval.agentdyn import AgentDynAdapter
from product_eval.agentharm import AgentHarmAdapter
from product_eval.agentdojo import AgentDojoAdapter
from product_eval.agentleak import AgentLeakAdapter
from product_eval.agentpoison import AgentPoisonAdapter
from product_eval.injecagent import InjecAgentAdapter
from product_eval.judge_policy import load_and_validate_judge_policy, validate_api_judge_binding
from product_eval.msb import MsbAdapter
from product_eval.saber import SaberAdapter
from product_eval.product_model_policy import resolve_requested_model
from product_eval.api_judge import OpenAICompatibleJudge, load_immutable_judge_profile
from product_eval.data_paths import default_data_root, runtime_python
from product_eval.selection import case_index_path, selected_cases


DEFAULT_DATA_ROOT = default_data_root()
DEFAULT_SABER_ROOT = DEFAULT_DATA_ROOT / "saber-main" / "saber-main"
DEFAULT_AGENTDOJO_ROOT = DEFAULT_DATA_ROOT / "AgentDojo"
DEFAULT_AGENTDOJO_PYTHON = runtime_python("agentdojo")
DEFAULT_AGENTDYN_ROOT = DEFAULT_DATA_ROOT / "AgentDyn-main" / "AgentDyn-main"
DEFAULT_AGENTDYN_PYTHON = runtime_python("agentdyn")
DEFAULT_AGENTHARM_DATA_ROOT = DEFAULT_DATA_ROOT / "AgentHarm"
DEFAULT_AGENTHARM_RUNTIME_ROOT = DEFAULT_DATA_ROOT / "AgentHarm-inspect-evals-b935c0e5"
DEFAULT_INJECAGENT_ROOT = DEFAULT_DATA_ROOT / "InjecAgent-main"
DEFAULT_MSB_ROOT = DEFAULT_DATA_ROOT / "MSB-main" / "MSB-main"
DEFAULT_AGENTLEAK_ROOT = DEFAULT_DATA_ROOT / "AgentLeak-main" / "AgentLeak-main"
DEFAULT_AGENTPOISON_ROOT = DEFAULT_DATA_ROOT / "AgentPoison-master" / "AgentPoison-master"


def benchmark_roots(data_root: Path) -> dict[str, Path]:
    root = data_root.expanduser().resolve()
    return {
        "saber": root / "saber-main" / "saber-main",
        "agentdojo": root / "AgentDojo",
        "agentdyn": root / "AgentDyn-main" / "AgentDyn-main",
        "agentharm_data": root / "AgentHarm",
        "agentharm_runtime": root / "AgentHarm-inspect-evals-b935c0e5",
        "injecagent": root / "InjecAgent-main",
        "msb": root / "MSB-main" / "MSB-main",
        "agentleak": root / "AgentLeak-main" / "AgentLeak-main",
        "agentpoison": root / "AgentPoison-master" / "AgentPoison-master",
    }


def apply_data_root(args: argparse.Namespace) -> None:
    if args.data_root is None:
        return
    roots = benchmark_roots(args.data_root)
    defaults = {
        "saber_root": (DEFAULT_SABER_ROOT, roots["saber"]),
        "agentdojo_root": (DEFAULT_AGENTDOJO_ROOT, roots["agentdojo"]),
        "agentdyn_root": (DEFAULT_AGENTDYN_ROOT, roots["agentdyn"]),
        "agentharm_data_root": (DEFAULT_AGENTHARM_DATA_ROOT, roots["agentharm_data"]),
        "agentharm_runtime_root": (
            DEFAULT_AGENTHARM_RUNTIME_ROOT,
            roots["agentharm_runtime"],
        ),
        "injecagent_root": (DEFAULT_INJECAGENT_ROOT, roots["injecagent"]),
        "msb_root": (DEFAULT_MSB_ROOT, roots["msb"]),
        "agentleak_root": (DEFAULT_AGENTLEAK_ROOT, roots["agentleak"]),
        "agentpoison_root": (DEFAULT_AGENTPOISON_ROOT, roots["agentpoison"]),
    }
    for attribute, (default, replacement) in defaults.items():
        if getattr(args, attribute) == default:
            setattr(args, attribute, replacement)


def load_request(path: Path) -> dict[str, Any]:
    request = json.loads(path.read_text(encoding="utf-8"))
    schema = json.loads((PACKAGE_ROOT / "schemas" / "run_request.schema.json").read_text(encoding="utf-8"))
    try:
        from jsonschema import validate
    except ImportError:
        required = {"run_id", "benchmark_id", "product", "adapter", "isolation", "selection", "output_dir"}
        missing = required - set(request)
        if missing:
            raise ValueError(f"Run request is missing fields: {sorted(missing)}")
    else:
        validate(request, schema)
    return request


def selected_case(case_id: str, case_index: Path) -> dict[str, Any]:
    found = None
    with case_index.open("r", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            record = json.loads(line)
            if record["case_id"] == case_id:
                found = record
                break
    if found is None:
        raise ValueError(f"Case is not selected by the federation index: {case_id}")
    return found


def resolve_relative(path_text: str, request_path: Path) -> Path:
    path = Path(path_text).expanduser()
    return path if path.is_absolute() else (request_path.parent / path).resolve()


def main() -> int:
    parser = argparse.ArgumentParser(description="Run one selected case against a complete Agent product.")
    parser.add_argument("--request", required=True, type=Path)
    parser.add_argument("--case-id", required=True)
    parser.add_argument("--case-index", type=Path, help="Explicitly override the request's staged index")
    parser.add_argument("--data-root", type=Path)
    parser.add_argument("--saber-root", type=Path, default=DEFAULT_SABER_ROOT)
    parser.add_argument("--agentdojo-root", type=Path, default=DEFAULT_AGENTDOJO_ROOT)
    parser.add_argument("--agentdojo-python", type=Path, default=DEFAULT_AGENTDOJO_PYTHON)
    parser.add_argument("--agentdyn-root", type=Path, default=DEFAULT_AGENTDYN_ROOT)
    parser.add_argument("--agentdyn-python", type=Path, default=DEFAULT_AGENTDYN_PYTHON)
    parser.add_argument("--agentharm-data-root", type=Path, default=DEFAULT_AGENTHARM_DATA_ROOT)
    parser.add_argument(
        "--agentharm-runtime-root", type=Path, default=DEFAULT_AGENTHARM_RUNTIME_ROOT
    )
    parser.add_argument("--injecagent-root", type=Path, default=DEFAULT_INJECAGENT_ROOT)
    parser.add_argument("--msb-root", type=Path, default=DEFAULT_MSB_ROOT)
    parser.add_argument("--agentleak-root", type=Path, default=DEFAULT_AGENTLEAK_ROOT)
    parser.add_argument("--agentpoison-root", type=Path, default=DEFAULT_AGENTPOISON_ROOT)
    parser.add_argument("--state-source", type=Path)
    parser.add_argument("--run-id", help="Override request.run_id for a staged execution.")
    parser.add_argument("--output-root", type=Path, help="Override request.output_dir.")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--preflight-only", action="store_true", help="Inspect isolation without product, benchmark or judge execution")
    parser.add_argument("--isolated-offline", action="store_true", help="Verify the registered single-case container route with upstream disabled")
    parser.add_argument("--publish-native-result", action="store_true")
    args = parser.parse_args()
    apply_data_root(args)

    request_path = args.request.resolve()
    request = load_request(request_path)
    from tools.isolated_agentdojo_dispatch import requested as isolated_requested, preflight as isolated_preflight, dispatch
    isolated = isolated_requested(request)
    if args.preflight_only:
        report = isolated_preflight(request, dry_run=args.dry_run) if isolated else product_preflight(request, dry_run=args.dry_run)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if args.dry_run or report.get('execution_permitted') else 2
    if isolated:
        preflight = isolated_preflight(request, dry_run=args.dry_run)
        if not args.dry_run and preflight.get('execution_permitted') is not True:
            raise ValueError('isolated_preflight_failed:' + ','.join(preflight['blockers']))
    else:
        preflight = require_product_execution(request, dry_run=args.dry_run)
    selection_path = case_index_path(request_path, request, args.case_index)
    allowed_ids = selected_cases(selection_path, request["benchmark_id"], request["selection"].get("case_ids"))
    if args.case_id not in allowed_ids:
        raise ValueError("Case is outside the request's selected index")
    if isolated:
        if args.state_source or args.publish_native_result:
            raise ValueError('Isolated profile forbids state-source and publish overrides')
        if args.run_id:
            request['run_id'] = args.run_id
        if args.dry_run:
            print(json.dumps({'kind': 'isolated_single_case_plan', 'preflight': preflight,
                'case_id': args.case_id, 'product_executed': False}, indent=2))
            return 0
        output = args.output_root or resolve_relative(request['output_dir'], request_path)
        report = dispatch(request, data_root=args.data_root or DEFAULT_DATA_ROOT,
            selection_path=selection_path, output=output.resolve(), offline=args.isolated_offline, case_id=args.case_id)
        print(json.dumps(report, indent=2))
        return 0 if report['passed'] and all(report['cleanup'].values()) else 2
    if args.isolated_offline:
        raise ValueError('--isolated-offline requires the registered isolated profile')
    indexed = selected_case(args.case_id, selection_path)
    if request["benchmark_id"] != indexed["benchmark_id"]:
        raise ValueError("Run request benchmark_id does not match selected case")
    requested_case_ids = request["selection"].get("case_ids")
    if requested_case_ids is not None and args.case_id not in requested_case_ids:
        raise ValueError("Case is outside run_request.selection.case_ids")
    supported = {
        "saber", "agentdojo", "agentdyn", "agentharm", "injecagent", "msb", "agentleak", "agentpoison_strategyqa"
    }
    if request["benchmark_id"] not in supported:
        raise NotImplementedError(f"Implemented benchmark adapters: {', '.join(sorted(supported))}")

    product = request["product"]
    product_id = product["product_id"]
    requested_model = resolve_requested_model(product_id, product.get("product_model"))
    configured_state = product.get("state_source")
    if product_id == "codebuddy" and not args.state_source and not configured_state:
        state_source = None
    else:
        state_source = args.state_source or (
            Path(configured_state) if configured_state else default_state_source(product_id)
        )
    identity = ProductIdentity(
        product_id=product_id,
        executable=product["executable"],
        version=product["version"],
        product_model=requested_model,
        state_source=str(state_source.resolve()) if state_source else None,
    )
    runner = ProductRunner(identity, timeout_seconds=int(request.get("timeout_seconds", 300)))
    native_case_id = indexed["native_case_id"]
    run_id = args.run_id or request["run_id"]
    output_root = (
        args.output_root.expanduser().resolve()
        if args.output_root
        else resolve_relative(request["output_dir"], request_path)
    )
    case_output = output_root / args.case_id.replace(":", "__")
    bind_product_run(output_root, product_identity(request, selection_path, args), preflight)
    native_result = case_output / "native_result.json"
    published = None
    case_result = None
    if request["benchmark_id"] == "saber":
        adapter = SaberAdapter(args.saber_root)
        if not args.dry_run:
            adapter.preflight_runtime()
        task_path = adapter.task_path(native_case_id)
        task = adapter.load_task(native_case_id)
        server_events = case_output / "saber_mcp_events.jsonl"
        server_command = adapter.mcp_server_command(
            server_script=PACKAGE_ROOT / "tools" / "saber_mcp_server.py",
            task_path=task_path,
            native_result_path=native_result,
            server_events_path=server_events,
        )
        product_run = runner.run(
            run_id=run_id,
            benchmark_id="saber",
            case_id=args.case_id,
            prompt=adapter.build_product_prompt(task),
            output_dir=case_output,
            mcp_server_command=server_command,
            allowed_tool_names=adapter.allowed_tool_names(task),
            dry_run=args.dry_run,
        )
        if not args.dry_run:
            adapter.cleanup_runtime_from_events(
                server_events,
                case_output / "saber_runtime_cleanup.json",
            )
            adapter.merge_product_result(task, product_run, native_result)
            slug = f"product_{product_id}_{product['version'].replace('.', '_')}"
            published = adapter.publish_native_result(native_result, slug)
            judge_policy = load_and_validate_judge_policy(PACKAGE_ROOT / "benchmark_registry.json")
            validate_api_judge_binding(judge_policy, adapter.preflight_judge())
            judged_path = adapter.judge_native_result(product_slug=slug, task=task)
            judgment_snapshot = case_output / "official_judgment.json"
            judgment_snapshot.write_text(
                json.dumps(
                    adapter.load_judged_result(judged_path),
                    ensure_ascii=False,
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
            case_result = adapter.case_result_from_judged(
                run_id=run_id,
                product=product_run.product,
                case_id=args.case_id,
                judged_path=judgment_snapshot,
            )
            (case_output / "case_result.json").write_text(
                json.dumps(case_result, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
    elif request["benchmark_id"] == "agentharm":
        if args.publish_native_result:
            raise ValueError("--publish-native-result is only used by the SABER official judge layout")
        adapter = AgentHarmAdapter(args.agentharm_data_root, args.agentharm_runtime_root)
        adapter.preflight()
        _, source = adapter.load_case(native_case_id)
        judge = None
        if not args.dry_run:
            profile = load_immutable_judge_profile(
                PACKAGE_ROOT / "release" / "evaluation_config.json",
                PACKAGE_ROOT / "benchmark_registry.json",
            )
            judge = OpenAICompatibleJudge(
                profile,
                evidence_path=case_output / "judge_evidence.json",
            )
            judge.preflight()
        server_command = adapter.mcp_server_command(
            server_script=PACKAGE_ROOT / "tools" / "agentharm_mcp_server.py",
            native_case_id=native_case_id,
            native_result_path=native_result,
            events_path=case_output / "agentharm_mcp_events.jsonl",
        )
        product_run = runner.run(
            run_id=run_id,
            benchmark_id="agentharm",
            case_id=args.case_id,
            prompt=adapter.build_product_prompt(source),
            output_dir=case_output,
            mcp_server_command=server_command,
            allowed_tool_names=adapter.allowed_tool_names(source),
            dry_run=args.dry_run,
        )
        if not args.dry_run:
            case_result = adapter.score_native_result(
                run_id=run_id,
                product=product_run.product,
                case_id=args.case_id,
                native_result_path=native_result,
                final_response=product_run.final_response,
                judge=judge,
                product_run_status=product_run.status,
                product_run_error=product_run.error,
            )
            case_result_path = case_output / "case_result.json"
            temporary = case_result_path.with_name(f"{case_result_path.name}.tmp")
            temporary.write_text(
                json.dumps(case_result, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            temporary.replace(case_result_path)
    elif request["benchmark_id"] in {"agentdojo", "agentdyn"}:
        if args.publish_native_result:
            raise ValueError("--publish-native-result is only used by the SABER official judge layout")
        if request["benchmark_id"] == "agentdyn":
            adapter = AgentDynAdapter(args.agentdyn_root, args.agentdyn_python)
        else:
            adapter = AgentDojoAdapter(args.agentdojo_root, args.agentdojo_python)
        adapter.preflight()
        source_path = adapter.source_result_path(native_case_id)
        source = adapter.load_source_result(native_case_id)
        server_events = case_output / f"{adapter.benchmark_id}_mcp_events.jsonl"
        server_command = adapter.mcp_server_command(
            server_script=PACKAGE_ROOT / "tools" / "agentdojo_mcp_server.py",
            source_result_path=source_path,
            native_result_path=native_result,
            events_path=server_events,
        )
        product_run = runner.run(
            run_id=run_id,
            benchmark_id=adapter.benchmark_id,
            case_id=args.case_id,
            prompt=adapter.build_product_prompt(source),
            output_dir=case_output,
            mcp_server_command=server_command,
            allowed_tool_names=adapter.allowed_tool_names(source),
            dry_run=args.dry_run,
        )
        if not args.dry_run:
            adapter.ensure_native_result(source, native_result)
            case_result_path = case_output / "case_result.json"
            case_result = adapter.score(
                scorer_script=PACKAGE_ROOT / "tools" / "score_agentdojo_result.py",
                native_result_path=native_result,
                product_run=product_run,
                case_result_path=case_result_path,
            )
    elif request["benchmark_id"] == "injecagent":
        if args.publish_native_result:
            raise ValueError("--publish-native-result is only used by the SABER official judge layout")
        adapter = InjecAgentAdapter(args.injecagent_root)
        adapter.preflight()
        _, _, source = adapter.load_case(native_case_id)
        server_events = case_output / "injecagent_mcp_events.jsonl"
        server_command = adapter.mcp_server_command(
            server_script=PACKAGE_ROOT / "tools" / "injecagent_mcp_server.py",
            native_case_id=native_case_id,
            native_result_path=native_result,
            events_path=server_events,
        )
        product_run = runner.run(
            run_id=run_id,
            benchmark_id="injecagent",
            case_id=args.case_id,
            prompt=adapter.build_product_prompt(source),
            output_dir=case_output,
            mcp_server_command=server_command,
            allowed_tool_names=adapter.allowed_tool_names(source),
            dry_run=args.dry_run,
        )
        if not args.dry_run:
            if product_id == "codebuddy":
                adapter.recover_native_result_from_events(
                    native_case_id=native_case_id,
                    source=source,
                    events_path=case_output / "injecagent_mcp_events.jsonl",
                    native_result_path=native_result,
                )
            adapter.ensure_native_result(
                native_case_id=native_case_id,
                source=source,
                native_result_path=native_result,
            )
            case_result_path = case_output / "case_result.json"
            case_result = adapter.score(
                scorer_script=PACKAGE_ROOT / "tools" / "score_injecagent_result.py",
                native_result_path=native_result,
                product_run=product_run,
                case_result_path=case_result_path,
            )
    elif request["benchmark_id"] == "msb":
        adapter = MsbAdapter(args.msb_root)
        adapter.preflight()
        source = adapter.load_case(native_case_id)
        server_command = adapter.mcp_server_command(
            server_script=PACKAGE_ROOT / "tools" / "msb_mcp_server.py",
            native_case_id=native_case_id,
            native_result_path=native_result,
            events_path=case_output / "msb_mcp_events.jsonl",
        )
        product_run = runner.run(
            run_id=run_id,
            benchmark_id="msb",
            case_id=args.case_id,
            prompt=adapter.build_product_prompt(source),
            output_dir=case_output,
            mcp_server_command=server_command,
            allowed_tool_names=adapter.allowed_tool_names(source),
            dry_run=args.dry_run,
        )
        if not args.dry_run:
            if product_id == "codebuddy":
                adapter.recover_native_result_from_events(
                    source,
                    case_output / "msb_mcp_events.jsonl",
                    native_result,
                )
            adapter.ensure_native_result(source, native_result)
            case_result = adapter.score(
                scorer_script=PACKAGE_ROOT / "tools" / "score_msb_result.py",
                native_result_path=native_result,
                product_run=product_run,
                case_result_path=case_output / "case_result.json",
            )
    elif request["benchmark_id"] == "agentleak":
        adapter = AgentLeakAdapter(args.agentleak_root)
        adapter.preflight()
        source = adapter.load_case(native_case_id)
        server_command = adapter.mcp_server_command(
            server_script=PACKAGE_ROOT / "tools" / "agentleak_mcp_server.py",
            native_case_id=native_case_id,
            native_result_path=native_result,
            events_path=case_output / "agentleak_mcp_events.jsonl",
        )
        product_run = runner.run(
            run_id=run_id,
            benchmark_id="agentleak",
            case_id=args.case_id,
            prompt=adapter.build_product_prompt(source),
            output_dir=case_output,
            mcp_server_command=server_command,
            allowed_tool_names=adapter.allowed_tool_names(source),
            dry_run=args.dry_run,
        )
        if not args.dry_run:
            adapter.ensure_native_result(source, native_result)
            case_result = adapter.score(
                scorer_script=PACKAGE_ROOT / "tools" / "score_agentleak_result.py",
                agentleak_root=args.agentleak_root,
                native_result_path=native_result,
                product_run=product_run,
                case_result_path=case_output / "case_result.json",
            )
    else:
        adapter = AgentPoisonAdapter(args.agentpoison_root)
        adapter.preflight()
        source_index, question, _replay = adapter.load_case(native_case_id)
        server_command = adapter.mcp_server_command(
            server_script=PACKAGE_ROOT / "tools" / "agentpoison_mcp_server.py",
            native_case_id=native_case_id,
            native_result_path=native_result,
            events_path=case_output / "agentpoison_mcp_events.jsonl",
        )
        product_run = runner.run(
            run_id=run_id,
            benchmark_id="agentpoison_strategyqa",
            case_id=args.case_id,
            prompt=adapter.build_product_prompt(question),
            output_dir=case_output,
            mcp_server_command=server_command,
            allowed_tool_names=adapter.allowed_tool_names(question),
            dry_run=args.dry_run,
        )
        if not args.dry_run:
            adapter.ensure_native_result(native_case_id, source_index, native_result)
            case_result = adapter.score(
                scorer_script=PACKAGE_ROOT / "tools" / "score_agentpoison_result.py",
                native_result_path=native_result,
                product_run=product_run,
                case_result_path=case_output / "case_result.json",
            )

    print(
        json.dumps(
            {
                "status": product_run.status,
                "run_id": product_run.run_id,
                "case_id": args.case_id,
                "product": product_run.product,
                "output_dir": str(case_output),
                "native_result": str(native_result) if native_result.exists() else None,
                "published_native_result": str(published) if published else None,
                "case_result": case_result,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if product_run.status in {"completed", "dry_run"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
