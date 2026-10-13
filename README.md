# LLM and Agent Safety Evaluation Platform · V1

[中文说明](README.zh-CN.md) · [Documentation](docs/README.md) · [Contributing](CONTRIBUTING.md)

This is the primary project repository. The integrated platform starts at **V1 (1.0.0)**, combining LLM evaluation, internal Agent execution, full-product native evaluation and offline trace judging. Failure evidence informs new tests and subsequent improvements.

**Implemented:** native entry points, public execution with separate private scoring, result analysis and unaccepted candidate plans. **Not implemented:** general experiment scheduling, replay diagnosis, automatic test-set evolution, LLM / Agent improvement, promotion and rollback.

## Figure 1: dual-loop evaluation and evolution Harness

Every node states its status: **Implemented** has a code entry point; **Partial** has protocols and configuration; **Not implemented** is a proposed module with a dashed border. Arrows show the intended data flow; the automatic controller and feedback loops are not connected yet. Each of the four tracks keeps its native metrics and denominator.

```mermaid
%%{init: {"flowchart": {"wrappingWidth": 340}}}%%
flowchart TB
    Inputs["Partial: freeze round inputs<br/>Protocols/configs exist; automatic snapshots do not"]
    Controller["Not implemented: experiment controller<br/>Scheduling, budgets, recovery, versions"]
    Inputs --> Controller

    Execute["Implemented: native executors<br/>LLM · internal Agent · full product"]
    Score["Implemented: native scoring<br/>Rules and separate private scoring"]
    Judge["Implemented: offline trace judging<br/>Separate tasks and metrics"]
    Results["Implemented: result and evidence analysis<br/>Metrics, failures, invalids, unaccepted plans"]
    Controller --> Execute --> Score --> Results
    Controller --> Judge --> Results
    Results --> Diagnosis["Not implemented: replay diagnosis and feedback filtering<br/>Verify failure conditions; isolate private information"]

    subgraph TestLoop["Loop A: test-set evolution"]
        Generate["Not implemented: generate test candidates<br/>Tasks, attacks and environment variants"]
        Admission["Not implemented: candidate admission<br/>Execute eight gates and isolation checks"]
        Dataset["Not implemented: dataset version release<br/>Update the evolution pool"]
        Generate --> Admission --> Dataset
    end

    subgraph TargetLoop["Loop B: LLM / Agent evolution"]
        Improve["Not implemented: generate target candidates<br/>Prompts, strategies, tools, code"]
        Validate["Not implemented: baseline/candidate comparison<br/>Safety, benign capability, validity, cost"]
        Promote["Not implemented: independent release check and promotion<br/>Promote on pass; retain baseline on failure; rollback"]
        Improve --> Validate --> Promote
    end

    Diagnosis --> TestLoop
    Diagnosis --> TargetLoop
    Dataset --> Next["Not implemented: automatic next round<br/>Freeze task and target versions again"]
    Promote --> Next
    classDef planned stroke-dasharray:5 5
    class Controller,Diagnosis,Generate,Admission,Dataset,Improve,Validate,Promote,Next planned
```

The diagram unfolds one round: the bottom node returns to the top for re-evaluation. Compare test-set changes and target changes separately so a change in test difficulty is not mistaken for target improvement. Independent release validation uses a sealed holdout after tuning stops.

### Implementation status and entry points

| Responsibility | Status | Current evidence and remaining work |
|---|---|---|
| Input protocols and freezing | Partial | [protocol/](protocol), [configuration](docs/configuration.md) and native manifests exist; automatic round snapshots and a shared version ledger do not |
| Three execution modules | Implemented | [LLM evaluator](llm_eval/multi_model_rule_eval_prompt_only.py), [internal Agent](harness/ash.py), [product executor](federated_asr_benchmark/tools/run_product.py); real runs require configured targets, data and native environments |
| Scoring and offline judging | Implemented | `ash.py score/judge`, LLM rules and product-native scorers; four separate populations; lower product ASR is better |
| Analysis and candidate plans | Implemented | [result_analysis.py](scripts/result_analysis.py) emits failures, invalids and `candidate_plans.jsonl`; `admitted=false`, `root_cause=unconfirmed` |
| Controller and replay diagnosis | Not implemented | In [pipeline_cli.py](scripts/pipeline_cli.py), `plan` only prints commands and `smoke` orchestrates three synthetic Agent fixtures; general scheduling, shared budgets, recovery and feedback filtering are missing |
| Loop A: test-set evolution | Not implemented | [Eight gate definitions](protocol/evaluation_tracks.json) exist and all candidate slots are `pending`; actual task generation, gate execution and version release are missing |
| Loop B: target evolution and next round | Not implemented | Candidate improvement, automated comparison, independent release checks, promotion/rollback and round cycling are missing; weight training is a future plugin |

See the [Harness design](docs/integration/harness_design.md) for the full module proposal, and [architecture](docs/integration/architecture.md) for the original concept and existing implementation mapping.

## Figure 2: module architecture and scoring boundary

`plan` prints commands. Native executors perform evaluation. Private rules are used in a separate scoring stage.

```mermaid
flowchart TB
    CLI["pipeline.py<br/>inventory / plan / smoke / analyze"] --> QA["llm_eval/<br/>LLM rule evaluation"]
    CLI --> Harness["harness/<br/>Public task execution and trace capture"]
    CLI --> Product["federated_asr_benchmark/<br/>Product and native environment adapters"]
    CLI --> Judge["harness/ judge<br/>Offline trace judging"]
    Harness --> Score["Separate scoring stage"]
    Private["Isolated private rules<br/>Never sent to the evaluated Agent"] --> Score
    QA --> Results["runs/<br/>Native results, traces and manifests"]
    Score --> Results
    Product --> Results
    Judge --> Results
    Results --> Analyze["scripts/result_analysis.py<br/>Grouped analysis and candidate plans"]
```

## Quick start

Run from the repository root. Validated platform: Windows PowerShell and Python 3.14. Local fixtures require no API key, product CLI, Docker or downloaded dataset.

```powershell
git clone https://github.com/StrawberryTreea/llm-safety-eval-research-pipeline.git
cd llm-safety-eval-research-pipeline
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe scripts/check_repo.py
```

Acceptance verifies the default protocol's bound count/hashes, Markdown links, three test suites, safe/unsafe Agent fixtures and a three-row LLM reference self-test. It writes a unique `runs/check-.../summary.json` and logs. Integration tests explicitly skip when named external assets are absent.

| Fixture | Expected result |
|---|---|
| `safe_stub` | 3/3 safety passes, no candidate plans |
| `unsafe_stub` | 1/3 safety passes, 2 unaccepted candidate plans |
| LLM reference self-test | 3/3 rule passes; copies the answer key without model inference |

These results verify local execution. Real safety claims require configured models/data and verified environments, coverage and scorers. See [getting started](docs/getting_started.md) and [validation](docs/integration/validation.md).

## Figure 3: collaborator run sequence

```mermaid
flowchart TB
    Install["Install root dependencies"] --> Check["check_repo.py<br/>Protocol, links, tests and fixtures"]
    Check --> Choose["Choose a track<br/>Prepare data and configuration"]
    Choose --> Plan["inventory / plan<br/>Inspect entry points and dependencies"]
    Plan --> Execute["Call the native executor<br/>Save a separate run"]
    Execute --> Analyze["analyze<br/>Review denominators, failures and evidence"]
    Analyze --> Review["Review candidates and qualification<br/>Record changes for the next round"]
```

## Choose a track

| Track | Native executor | Boundary |
|---|---|---|
| `llm_qa` | `llm_eval/multi_model_rule_eval_prompt_only.py` | Rule pass rate; refusals fail; API errors counted separately |
| `agent_execution` | `harness/ash.py capture/score/report` | Source-derived tasks; private rules score captured traces |
| `agent_product` | `federated_asr_benchmark/tools/run_product.py` | Native ASR; `success` means attack success; lower is better |
| `trace_judge` | `harness/ash.py judge` | Offline assessment, separate from execution results |

```powershell
.\.venv\Scripts\python.exe pipeline.py inventory
.\.venv\Scripts\python.exe pipeline.py plan --track llm_qa --config assets/samples/llm_self_test.yaml
```

`plan` does not execute. `analyze --track ... --results ... --out ...` produces observations, failures, invalids and candidate plans. Plans have `admitted=false` and eight pending acceptance gates. Failure reasons remain observations until traces establish their causes.

## Code map

| Path | Responsibility |
|---|---|
| `pipeline.py`, `scripts/pipeline_cli.py` | Shared inventory, plans and local smoke execution |
| `scripts/result_analysis.py` | Failure analysis with metric direction, denominators and lineage |
| `scripts/` | Protocols, source intake, canonicalization, statistics and preparation |
| `llm_eval/` | Streaming, resumable LLM rule evaluation |
| `harness/` | Internal Agents, tool environments, private rules and offline judging |
| `federated_asr_benchmark/` | Product adapters, native environments/scorers, indices/selections |
| `protocol/`, `assets/` | V1 contracts, templates and synthetic fixtures |
| `tests/`, module `tests/` | Regression tests and external-asset integration tests |
| `docs/`, `references/` | Collaborator guides, architecture, research plan and protocol reference |
| `runs/`, `work/`, `data/` | Outputs, intermediate artifacts and external data; ignored by Git |

## Real experiments and next steps

Read [configuration](docs/configuration.md), [Harness](harness/README.md) or [product evaluation](federated_asr_benchmark/README.md). The default protocol binds only three synthetic questions. Real experiments freeze their own data version/count/hash, models, prompts and scorers. Controlled product profiles retain actual third-party version constraints.

Unknown/invalid records and environment/API errors are never safety passes. Keep tracks and case/retrieval-event units separate. Source-derived tasks do not establish official benchmark reproduction. Phase 6–8 publication workflows have no executor.

[Research plan](docs/integration/research_plan.md) · [Protocol](references/protocol-contract.md) · [V1 cleanup](docs/integration/cleanup.md)
