# Phase Contracts

Use this file when you need compact operational contracts instead of the full framework document.

## Phase 0

- Goal: lock protocol, taxonomy, metric, model registry, and comparability statement.
- Primary artifacts:
  `evaluation_protocol_card.md`, `model_registry.json`, `taxonomy_registry.yaml`, `metric_registry.yaml`
- Primary executor:
  `scripts/protocol_manager.py`
- Required behavior:
  initialize protocol artifacts, reject placeholders or secrets, validate cross-file versions, and compare protocol compatibility.
- Detailed contract:
  `references/protocol-contract.md`

## Phase 1

- Goal: collect source evidence and benchmark inventory.
- Primary executor:
  `extract_whitepaper_datasets.py`
- Primary outputs:
  `dataset_catalog.json`, `source_inventory.csv`

## Phase 2

- Goal: standardize data and rebuild canonical truth.
- Required truth:
  canonical JSONL and stats must match exactly.
- Primary outputs:
  canonical JSONL, `dataset_sources_clean.csv`, `v2_stats.json`

## Phase 3

- Goal: shape prompt, scorer, and smoke subset.
- Primary outputs:
  smoke subset JSONL and run config.

## Phase 4

- Goal: validate protocol on a small subset before full execution.
- Primary outputs:
  `progress.json`, `summary.json`, `summary.csv`, `smoke_failures.md`

## Phase 5

- Goal: run full evaluation with resumable outputs.
- Primary executor:
  `eval_mvp_V2/multi_model_rule_eval_prompt_only.py`

## Phase 6

- Goal: rerun API-error or append-only deltas without mutating legacy outputs.
- Primary executors:
  `run_v2_incremental_eval.py`, `run_append_only_eval.py`, `append_new_dataset_results.py`

## Phase 7

- Goal: merge, clean, and publish authoritative results.
- Required checks:
  canonical dataset exists, merged inputs cover canonical IDs, deleted IDs removed, totals match canonical.
- Primary executors:
  `merge_v2_incremental_results.py`, `rebuild_merged_summaries.py`, `publish_v2_final_authoritative_results.py`

## Phase 8

- Goal: generate leaderboard, charts, DOCX, and whitepaper artifacts from authoritative outputs.
- Rule:
  report artifacts must not define new facts.

## Phase 9

- Goal: extend to agent-safety trajectory evaluation without replacing the current taxonomy.
- Anchor:
  keep the current safety taxonomy and extend from L2-15 to L2-19 where appropriate.
