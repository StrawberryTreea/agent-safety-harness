# MVP Task Allocation

Date: 2026-07-27

## Completed in v0.1

| Work package | Owner | Status | Output |
|---|---|---|---|
| MVP scope and dataset cut | Main agent | Done | `评测框架设计/agent_safety_dataset_screening_harness_mvp.md` |
| Python package and CLI | Main agent | Done | `src/agent_safety_harness/cli.py`, `ash.py` |
| Unified schema | Main agent | Done | `src/agent_safety_harness/schema.py` |
| Built-in sample benchmarks | Main agent | Done | `src/agent_safety_harness/data/*.jsonl` |
| Agent execution runner | Main agent | Done | `src/agent_safety_harness/runners.py` |
| Offline trace judge runner | Main agent | Done | `src/agent_safety_harness/runners.py` |
| Report aggregation | Main agent | Done | `src/agent_safety_harness/reporting.py` |
| Smoke-test checklist | Parallel review | Done | reflected in `tests/` |
| MVP backlog review | Parallel review | Done | reflected in this allocation file |
| Config files | Main agent | Done | `config/benchmarks.json`, `config/models.json`, `config/policies.json` |
| OpenAI-compatible adapter scaffold | Main agent | Done | `src/agent_safety_harness/agents.py` |
| Output overwrite protection | Main agent | Done | `--overwrite` flag in CLI |
| Per-task failure result | Main agent | Done | failed tasks produce `policy_verdict=unknown` |
| CLI edge tests | Main agent | Done | `tests/test_cli_edges.py` |

## v0.2 Backlog

1. Add verified raw-data imports for AgentHarm and record the observed fields.
2. Add verified raw-data mappings for AgentDojo and other external benchmark task sets; keep official scorer mapping as an optional compatibility layer.
3. Add retry and concurrency to the OpenAI-compatible adapter.
4. Add run IDs in addition to manual `--out` paths.
5. Add malformed JSONL fixture tests beyond parser line-number errors.
6. Add optional inspect-ai integration once the first adapters are stable.
7. Add real benchmark download/cache commands.
8. Add benchmark-level license and source metadata checks.
