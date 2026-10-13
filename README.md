# LLM and Agent Safety Evaluation Research Pipeline

[中文说明](README.zh-CN.md)

This is the primary repository for the project's LLM and Agent evaluation code and research design. Existing LLM data/protocol tooling, the internal Agent harness, full-product benchmark adapters, and offline trace judging share an entry point and result-analysis layer while retaining their native scoring contracts.

The intended loop is evaluation → failure analysis → candidate generation and acceptance → a new test-set version → evaluation. This integration implements executable local smoke and failure-analysis handoffs. Automatic generation, acceptance execution, dataset publication and Agent/model improvement remain future work.

## Run locally

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe pipeline.py inventory
.\.venv\Scripts\python.exe pipeline.py smoke
.\.venv\Scripts\python.exe pipeline.py smoke --agent unsafe_stub
```

Each smoke uses three synthetic local tasks, captures public execution, applies private scoring rules, writes a native report, and analyzes the results. A unique directory under `runs/` preserves logs and a pipeline manifest. Mock results are plumbing checks; they do not qualify official benchmarks or measure real products.

```powershell
python pipeline.py plan --track agent_execution --tasks public_tasks.jsonl --private-rules private_rules.jsonl --out runs/planned
python pipeline.py analyze --track agent_execution --results runs/example/score/results.jsonl --out runs/analysis
```

`plan` emits working directories, argv arrays and required paths without running services; `--agents-config` and `--models-config` forward native Agent settings. `analyze` emits observations, failures, invalids, grouped statistics and unaccepted candidate plans. Plans retain result-file/line/task lineage and eight pending evidence gates; `admitted=false` throughout this version.

## Native tracks

| Track | Executor | Metric boundary |
|---|---|---|
| `llm_qa` | `eval_mvp_V2/multi_model_rule_eval_prompt_only.py` | V2 rule_eval; refusals fail the primary metric; API errors are separate |
| `agent_execution` | `harness/ash.py capture/score/report` | Internal/source-derived tasks with deterministic private rules |
| `agent_product` | `federated_asr_benchmark/tools/run_product.py` | Full CLI products with native environments/scorers; success means attack success |
| `trace_judge` | `harness/ash.py judge` | Offline trace assessment, separate from execution |

Do not combine populations or denominator units. Unknown/invalid/excluded records never become safety passes. Attack-only tasks without a completion oracle contribute no task-success denominator. Product ASR preserves case and retrieval-event units.

## External assets

Source datasets, third-party benchmark runtimes, credentials, virtual environments and historical runs are external. Set `AGENT_EVAL_DATA_ROOT` or use native `--data-root`. `AGENTDOJO_PYTHON`, `AGENTDYN_PYTHON` and `AGENTPOISON_PYTHON` select dedicated interpreters. Existing versioned product/model/judge experiment profiles retain their constraints.

```powershell
python federated_asr_benchmark/tools/build_case_index.py --data-root D:/benchmark-data --output-dir runs/rebuilt-index
```

The LLM V2 truth source remains `eval_mvp_V2/data/master_normalized_dataset_v2_final.jsonl`, derived statistics remain `output/doc/v2_stats.json`, and authoritative publication remains `rule_eval_mvp/output_v2_final_authoritative/`. These rules apply to the LLM track; the large canonical data and complete production publish tooling are not bundled.

## Verification and design

Run `python -m unittest discover -s tests -v` at the root and in each of `harness/` and `federated_asr_benchmark/`. Missing named external assets produce explicit integration-test skips. These skips are not evidence of official qualification.

- [Architecture and original concept diagram](docs/integration/architecture.md)
- [Research and experiment plan](docs/integration/research_plan.md)
- [Validation record](docs/integration/validation.md)
- [Migration manifest](docs/integration/migration_manifest.json)
- [Agent working conventions](AGENTS.md)
- [Repository skill](SKILL.md) and [phase contracts](references/phase-contracts.md)
