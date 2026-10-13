---
name: llm-safety-eval-research-pipeline
description: Route, validate, and operationalize this repository's large-model safety evaluation workflow. Use when work involves this project's canonical dataset, taxonomy or metric changes, benchmark intake, smoke or full evaluation, API-error reruns, V2 merge and authoritative publish, leaderboard refresh, whitepaper generation, Agent execution, full-product native evaluation, offline trace judging, failure analysis, candidate-test evolution, or any request that must reuse local safety-eval contracts.
---

# LLM Safety Eval Research Pipeline

Treat this skill as the protocol and execution layer for this LLM and Agent evaluation repository. Select the track with `pipeline.py inventory`; read `docs/integration/architecture.md` and `protocol/evaluation_tracks.json` for its native executor and metric boundary.

Do not invent a parallel eval stack. Reuse the current repository's canonical dataset, incremental tools, publish roots, and report assets. The skill's job is to:

- route a user request into the right phase or phase set,
- enforce truth-model and publish invariants,
- bind phases to existing local scripts and directories,
- emit reusable registry, manifest, and checklist artifacts.

## Operating Rules

1. For `llm_qa`, treat `eval_mvp_V2/data/master_normalized_dataset_v2_final.jsonl` as the canonical dataset truth source.
2. For `llm_qa`, treat `output/doc/v2_stats.json` as a derived stats snapshot that must stay consistent with the canonical JSONL.
3. For `llm_qa`, treat `rule_eval_mvp/output_v2_final_authoritative/` as the only authoritative publish root for final leaderboard and report consumption.
4. Treat whitepapers, summaries, DOCX files, and prose as derived artifacts only.
5. Prefer the repository's existing scripts and output contracts before writing new orchestration code.
6. For LLM QA, freeze `safety_taxonomy_8x19` and `rule_eval`; for every track, freeze its own model/product versions, data, environment, scorer and metric units before comparable evaluation.
7. Keep execution and offline judging separate. Unknown/invalid and missing task-success rules must retain their native semantics.
8. Candidate plans are unaccepted; require replay, benign solvability, private oracle, provenance/license, deduplication, diversity, holdout isolation and review evidence before dataset admission.

## Workflow

### 1. Route The Request

Run `scripts/intent_router.py` when the user request is ambiguous, phase-coupled, or names only an outcome such as:

- "补一个新 benchmark"
- "只重跑 API error"
- "更新 leaderboard"
- "生成白皮书"
- "换一套指标"

If the request already names a phase-like operation precisely, you may skip the router and enter the phase directly.

### 2. Load The Right Reference Depth

- For repo asset locations and path semantics, read `references/repo-asset-map.md`.
- For phase input/output/check contracts, read `references/phase-contracts.md`.
- For Phase 0 artifact schemas, scoring semantics, and compatibility rules, read `references/protocol-contract.md`.
- For integration and research scope, read `docs/integration/architecture.md` and `docs/integration/research_plan.md`.
- For production artifacts, start from `assets/templates/` instead of writing registry or manifest files from scratch.

### 3. Run Prechecks Before Execution

Run `scripts/phase_executor.py` in `--check-only` or `--emit-commands` mode before making claims about what can be published.

Use prechecks to answer questions like:

- does the canonical dataset exist,
- does the stats snapshot exist,
- does the requested publish root exist,
- which model directories are actually available in `output/`, `output_v2_merged/`, or `output_v2_final_authoritative/`.

### 4. Bind To Existing Local Scripts

Use the existing repository scripts as the primary executors:

- `scripts/protocol_manager.py` for Phase 0 initialization, validation, and comparison
- `eval_mvp_V2/multi_model_rule_eval_prompt_only.py` as the implemented main evaluator

Treat the following names as planned interfaces until their implementations exist:

- `extract_whitepaper_datasets.py`
- `build_v2_final_dataset_report.py`
- `rule_eval_mvp/incremental_tools/run_v2_incremental_eval.py`
- `rule_eval_mvp/incremental_tools/run_append_only_eval.py`
- `rule_eval_mvp/incremental_tools/append_new_dataset_results.py`
- `rule_eval_mvp/incremental_tools/merge_v2_incremental_results.py`
- `rule_eval_mvp/incremental_tools/rebuild_merged_summaries.py`
- `rule_eval_mvp/incremental_tools/publish_v2_final_authoritative_results.py`

Do not replace them with a new pipeline unless the task is explicitly to refactor the runtime architecture.

### 5. Emit Standardized Artifacts

When a phase changes protocol, publish state, or reporting state, emit or update the smallest applicable artifacts from `assets/templates/`. For Phase 0, initialize and validate them with `scripts/protocol_manager.py`.

- `evaluation_protocol_card.md`
- `model_registry.json`
- `taxonomy_registry.yaml`
- `metric_registry.yaml`
- `run_config.yaml`
- `publish_config.yaml`
- `release_manifest.yaml`
- `release_checklist.md`

## Phase Routing Summary

- `Phase 0`: protocol setup, taxonomy or metric changes
- `Phase 1`: source intake, whitepaper extraction, benchmark inventory
- `Phase 2`: canonical standardization, membership or stats repair
- `Phase 3`: prompt or scorer shaping, smoke subset design
- `Phase 4`: smoke validation
- `Phase 5`: full evaluation
- `Phase 6`: incremental rerun or append-only evaluation
- `Phase 7`: merge, clean, authoritative publish, manifest
- `Phase 8`: leaderboard, charts, whitepaper, report consistency
- `Phase 9`: native Agent execution, full-product evaluation or offline trace judging
- `Phase 10`: result analysis and unaccepted candidate-test plans

## Quick Commands

Initialize and validate Phase 0 protocol artifacts:

```powershell
python scripts/protocol_manager.py init --output-dir protocol
python scripts/protocol_manager.py validate --protocol-dir protocol --repo-root D:\path\to\evaluation-repo --format json
```

Route a request:

```powershell
python scripts/intent_router.py --request "更新 leaderboard"
```

Generate phase checks and command hints:

```powershell
python scripts/phase_executor.py --request "更新 leaderboard" --emit-commands
```

Check a specific publish phase directly:

```powershell
python scripts/phase_executor.py --phase phase7 --models doubao_seed_2_0_pro_full --emit-commands
```

## Integrated Entry Point

Use `pipeline.py inventory`, `plan --track ...`, `smoke`, and `analyze --track ... --results ... --out ...`. Plans emit argv arrays; only smoke invokes local synthetic fixtures. Analysis keeps lineage and native score direction and does not generate or accept new tasks.

## Failure Policy

- Stop when Phase 0 protocol artifacts are missing, contain placeholders or secrets, disagree on versions, or violate frozen scoring semantics.
- Stop on canonical-total mismatches.
- Stop if publish tries to bypass merged clean results.
- Stop if report numbers cannot be traced back to canonical or authoritative artifacts.
- Warn, but do not silently proceed, when the request implies a protocol change without a refreshed smoke phase.
