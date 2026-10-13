# Repo Asset Map

Use this file when you need exact local assets instead of abstract framework language.

## Core Directories

- `eval_mvp_V2/`
  Current canonical dataset and main V2 evaluation script layer.
- `rule_eval_mvp/`
  Formal evaluation worktree plus incremental rerun, merge, and publish scripts.
- `output/`
  Canonical stats snapshots and dataset-document outputs.
- `标准文件/`
  Whitepaper and standard-document evidence layer. Prefer existing processed HTML artifacts here.
- `白皮书/`
  Report-writing and external-expression workspace. Do not use as the fact source.
- `数据集/`
  Upstream raw data, normalization workspace, and membership edits.

## Truth-Source Assets

- Canonical dataset:
  `eval_mvp_V2/data/master_normalized_dataset_v2_final.jsonl`
- Canonical stats snapshot:
  `output/doc/v2_stats.json`
- Authoritative publish root:
  `rule_eval_mvp/output_v2_final_authoritative/`

## Main Executors

- Source extraction:
  `extract_whitepaper_datasets.py`
- Dataset-document generation:
  `build_v2_final_dataset_report.py`
- Main evaluation:
  `eval_mvp_V2/multi_model_rule_eval_prompt_only.py`
- Incremental rerun:
  `rule_eval_mvp/incremental_tools/run_v2_incremental_eval.py`
- Append-only rerun:
  `rule_eval_mvp/incremental_tools/run_append_only_eval.py`
- Append onto merged baseline:
  `rule_eval_mvp/incremental_tools/append_new_dataset_results.py`
- Merge:
  `rule_eval_mvp/incremental_tools/merge_v2_incremental_results.py`
- Rebuild merged summaries:
  `rule_eval_mvp/incremental_tools/rebuild_merged_summaries.py`
- Authoritative publish:
  `rule_eval_mvp/incremental_tools/publish_v2_final_authoritative_results.py`

## Stable Output Contracts

Per model run output:

- `results.jsonl`
- `progress.json`
- `summary.json`
- `summary.csv`
- `leaderboard.csv`

Merged output supplements:

- `deleted_audit.jsonl`
- `merge_report.json`

Authoritative publish supplements:

- `release_manifest.json`
- `distribution_report.md`

## Publish Roots

- Legacy root:
  `rule_eval_mvp/output/`
- Delta rerun root:
  `rule_eval_mvp/output_v2_delta/`
- Merged clean root:
  `rule_eval_mvp/output_v2_merged/`
- Authoritative publish root:
  `rule_eval_mvp/output_v2_final_authoritative/`

## Integrated Agent and evolution modules

See `protocol/evaluation_tracks.json` and `docs/integration/architecture.md` for the authoritative integration map. `pipeline.py` is the shared entry point; `harness/` and `federated_asr_benchmark/` preserve their native executors. External assets use `AGENT_EVAL_DATA_ROOT` and dedicated interpreter environment variables. Runtime results belong under ignored `runs/` paths.
