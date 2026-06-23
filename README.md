# LLM Safety Eval Research Pipeline

[中文说明 / Chinese README](./README.zh-CN.md)

This repository is a project-local Codex skill for turning a large-model safety evaluation project into a reusable, auditable, and partially executable workflow.

It is not a new evaluation framework that replaces the original project runtime. Its role is to sit on top of an existing safety-eval codebase and provide:

- an intent router that maps user requests to protocol phases,
- a phase executor that performs prechecks and emits executable command hints,
- a reusable protocol layer for data, metrics, taxonomy, publishing, and reporting,
- a template layer for manifests, registries, and release checklists,
- a truth-model that prevents reports and whitepapers from becoming the source of record.

The current version is bound to one concrete repository layout and one concrete V2 publish pipeline. It is designed to reuse existing local scripts and artifact conventions rather than invent a parallel orchestration stack.

## What This Skill Solves

Large-model safety evaluation projects usually accumulate:

- canonical datasets,
- evolving taxonomy and metric definitions,
- one-off rerun scripts,
- ad hoc leaderboard refreshes,
- whitepaper and report generation,
- manual consistency checks between data, results, and prose.

Over time, the workflow becomes hard to route and harder to audit. The main problem is often not the absence of code, but the absence of an operational protocol layer:

- when a user says "update leaderboard", what phase should run,
- what must be checked before publishing,
- which files are authoritative,
- which scripts should be called,
- what artifacts must be emitted after the run,
- what conditions should stop the workflow.

This skill addresses that layer.

## Scope

This skill is intended for requests like:

- adding or normalizing a new benchmark,
- updating taxonomy or metric definitions,
- preparing smoke subsets and run configs,
- running or rerunning evaluations,
- rerunning API-error cases only,
- append-only evaluation on new datasets,
- merging old and delta results,
- publishing authoritative final outputs,
- refreshing leaderboards and whitepapers,
- generating auditable manifest and release artifacts.

This skill is not intended to:

- replace the original evaluation scripts,
- hide project-specific assumptions,
- automatically infer truth from report prose,
- generalize to arbitrary repositories without path binding,
- fully automate every runtime mutation in the current version.

## Truth Model

The skill is built around a strict source-of-truth hierarchy.

### Canonical Inputs

- `eval_mvp_V2/data/master_normalized_dataset_v2_final.jsonl`
  The canonical dataset truth source.
- `output/doc/v2_stats.json`
  A derived stats snapshot that must remain consistent with the canonical JSONL.

### Authoritative Outputs

- `rule_eval_mvp/output_v2_final_authoritative/`
  The only authoritative publish root for final leaderboard and report consumption.

### Derived Artifacts

- whitepapers,
- summary markdown,
- DOCX reports,
- distribution reports,
- leaderboard prose,
- charts and narrative writeups.

Derived artifacts may explain results, but they must never define them.

## Operating Principles

1. Canonical dataset first.
2. Reports are derived artifacts, never truth sources.
3. Authoritative publish must come from merged clean results only.
4. Canonical counts must be recomputed from the dataset, not copied from prose.
5. Metric and taxonomy changes must be explicitly locked before evaluation and publication.
6. Incremental reruns must not silently mutate legacy outputs.

## Repository Layout

```text
.
├── SKILL.md
├── README.md
├── .gitignore
├── agents/
│   └── openai.yaml
├── references/
│   ├── phase-contracts.md
│   └── repo-asset-map.md
├── scripts/
│   ├── intent_router.py
│   └── phase_executor.py
└── assets/
    └── templates/
        ├── evaluation_protocol_card.md
        ├── metric_registry.yaml
        ├── model_registry.json
        ├── publish_config.yaml
        ├── release_checklist.md
        ├── release_manifest.yaml
        ├── run_config.yaml
        └── taxonomy_registry.yaml
```

## Core Components

### `SKILL.md`

The actual Codex skill definition. It explains when the skill should be triggered, what protocol it enforces, which references to read, and which scripts to call.

### `references/`

Compact operational references loaded on demand.

- `phase-contracts.md`
  Short phase-by-phase contracts.
- `repo-asset-map.md`
  Repository asset reuse map and binding hints.

### `scripts/`

Minimal executable prototype layer.

- `intent_router.py`
  Converts natural-language user requests into protocol phases.
- `phase_executor.py`
  Runs phase prechecks, lists available model roots, and emits command hints bound to the existing project scripts.

### `assets/templates/`

Reusable governance and release artifacts. These are meant to be copied and filled, not reinvented.

## Phase System

The workflow is organized into ten phases.

| Phase | Name | Purpose |
| --- | --- | --- |
| `phase0` | Protocol Setup | Lock taxonomy, metrics, models, and comparability rules |
| `phase1` | Source Intake | Collect benchmark sources and whitepaper evidence |
| `phase2` | Canonical Standardization | Normalize membership and keep canonical JSONL and stats aligned |
| `phase3` | Protocol Shaping | Define prompts, scorers, and smoke subsets |
| `phase4` | Smoke Validation | Validate protocol on a small subset |
| `phase5` | Full Evaluation | Run the main evaluation over the canonical dataset |
| `phase6` | Incremental Rerun | Rerun API-error or append-only deltas |
| `phase7` | Merge And Publish | Merge old and delta runs, clean to canonical IDs, publish authoritative results |
| `phase8` | Reporting | Generate leaderboard, charts, DOCX, and whitepaper artifacts |
| `phase9` | Agent Safety Extension | Extend the protocol to trajectory-level agent safety |

## Intent Routing

The router is keyword-based by design. It is not trying to infer intent from deep semantics; it is trying to operationally map common research requests into the correct phase set.

Examples:

- `补一个新 benchmark` -> `phase1 + phase2`
- `只重跑 API error` -> `phase6`
- `更新 leaderboard` -> `phase7 + phase8`
- `生成白皮书` -> `phase7 + phase8`
- `换一套指标` -> `phase0 + phase3 + phase5 + phase8`
- `换一套风险维度` -> `phase0 + phase2 + phase3 + phase8`

If no strong signal is found, the router defaults to `phase0` so the request can be scoped at the protocol layer before execution.

## Phase Execution Model

The current executor is intentionally conservative.

For each phase, it can:

- check whether required paths and scripts exist,
- surface which model directories are actually available,
- emit notes about invariants and execution risks,
- emit command hints that match the current script interfaces.

The current executor does not yet:

- mutate upstream config files automatically,
- launch end-to-end multi-phase runs without supervision,
- write back results into external systems,
- infer missing user parameters such as append-only dataset paths.

## Existing Script Bindings

This skill is explicitly bound to the following project runtime scripts:

- `extract_whitepaper_datasets.py`
- `build_v2_final_dataset_report.py`
- `eval_mvp_V2/multi_model_rule_eval_prompt_only.py`
- `rule_eval_mvp/incremental_tools/run_v2_incremental_eval.py`
- `rule_eval_mvp/incremental_tools/run_append_only_eval.py`
- `rule_eval_mvp/incremental_tools/append_new_dataset_results.py`
- `rule_eval_mvp/incremental_tools/merge_v2_incremental_results.py`
- `rule_eval_mvp/incremental_tools/rebuild_merged_summaries.py`
- `rule_eval_mvp/incremental_tools/publish_v2_final_authoritative_results.py`

That binding is the point. The skill is meant to operationalize an existing codebase, not abstract it away.

## Templates And Registry Files

The `assets/templates/` directory provides the minimum artifact layer needed to make the workflow auditable.

### `evaluation_protocol_card.md`

Use this to freeze the run protocol, scope, assumptions, metric mode, model set, and comparability statements.

### `model_registry.json`

Use this to declare model identifiers, result paths, and model-level runtime metadata.

### `taxonomy_registry.yaml`

Use this to declare the active taxonomy version and label structure.

### `metric_registry.yaml`

Use this to declare the current metric version, scoring semantics, and primary metric choices.

### `run_config.yaml`

Use this to externalize the minimal run-layer configuration when comparing or documenting executions.

### `publish_config.yaml`

Use this to declare publish roots, selected models, and release-time rules.

### `release_manifest.yaml`

Use this as the machine-readable release record. It includes:

- release id,
- canonical dataset path and total,
- taxonomy and metric version,
- source result roots,
- published result path,
- validation fields like missing ids, extra ids, and deleted-id cleanup status.

### `release_checklist.md`

Use this for human release review before publishing external-facing results.

## Recommended Usage

### 1. Route the request

```powershell
python scripts/intent_router.py --request "更新 leaderboard" --format json
```

### 2. Run protocol checks or command emission

```powershell
python scripts/phase_executor.py --request "更新 leaderboard" --emit-commands --format json
```

### 3. Inspect a specific phase directly

```powershell
python scripts/phase_executor.py --phase phase7 --models doubao_seed_2_0_pro_full --emit-commands --format json
```

## Typical Workflow Examples

### Case 1: Refresh leaderboard from existing delta results

1. Route request to `phase7 + phase8`.
2. Run `phase_executor.py --phase phase7 --emit-commands`.
3. Check canonical dataset, stats, deleted ids, merge script, publish script.
4. Execute merge, rebuild summaries, and publish.
5. Use authoritative output only for leaderboard and report regeneration.

### Case 2: Rerun API-error cases only

1. Route request to `phase6`.
2. Validate legacy output and incremental rerun script existence.
3. Emit `run_v2_incremental_eval.py` command with canonical dataset and delta output root.
4. Merge and publish only after rerun completion if external-facing outputs need updating.

### Case 3: Change metric definition

1. Route request to `phase0 + phase3 + phase5 + phase8`.
2. Update `metric_registry.yaml`.
3. Freeze comparability statement in `evaluation_protocol_card.md`.
4. Revalidate smoke subset and scorer assumptions.
5. Re-run affected evaluations before regenerating reporting artifacts.

## Guards And Failure Policy

The current skill encodes several hard boundaries even before a full guard engine is implemented.

Fatal boundaries:

- stop on canonical-total mismatches,
- stop if publish tries to bypass merged clean results,
- stop if report numbers cannot be traced back to canonical or authoritative artifacts.

Warning boundaries:

- do not silently continue when protocol changed but smoke validation was not refreshed,
- do not assume append-only execution paths if the new dataset path is missing,
- do not treat stale stats snapshots as harmless.

## Current Limitations

This repository already contains a usable first prototype, but it is still a first prototype.

Current limitations:

- routing is keyword-driven rather than configuration-driven,
- guards are encoded as code checks and notes, not yet as a standalone machine-readable guard registry,
- executor emits commands but does not yet orchestrate full multi-phase `--run`,
- template filling is manual,
- repository binding is currently specific to one V2 safety-eval project structure.

## Roadmap

Natural next upgrades:

- introduce a machine-readable guard schema,
- add a real `--run` mode to `phase_executor.py`,
- support template materialization into a target working directory,
- externalize routing rules into a config file,
- add audit log emission,
- support V3 or domain-specific variants without breaking current invariants.

## Validation Status

This repository has already been locally validated as a standalone skill repository.

Validated items:

- skill structure passes `quick_validate.py`,
- `intent_router.py` routes Chinese and English requests,
- `phase_executor.py` emits working phase-7 checks and command hints,
- UTF-8 console output handling was patched for Windows use.

## Who This Is For

This repository is most useful for:

- researchers managing evolving safety benchmarks,
- teams with multiple local scripts but no protocol layer,
- projects that need auditable publish and reporting boundaries,
- Codex-based automation where intent routing and truth control matter as much as raw execution.

## Development Notes

- Keep `SKILL.md` as the canonical skill entrypoint for Codex.
- Keep `README.md` human-oriented and repository-oriented.
- Prefer updating templates and references rather than bloating `SKILL.md`.
- If new project scripts replace old executors, update both `SKILL.md` and `scripts/phase_executor.py`.
- Do not commit runtime caches or generated result directories into this repository.
