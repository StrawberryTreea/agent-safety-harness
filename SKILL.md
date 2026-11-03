---
name: agent-safety-harness
description: Route this repository's LLM and Agent safety evaluation, protocol, data governance, native execution and failure-analysis work using existing contracts.
---

# Repository workflow

Start with [README](README.md), [documentation navigation](docs/README.md), `protocol/evaluation_tracks.json` and `python pipeline.py inventory`.

1. Select llm_qa, agent_execution, agent_product or trace_judge. Reuse its executor, schema and scorer.
2. Read [configuration](docs/configuration.md) and the native module guide. Freeze data, target, environment, scorer and metric versions.
3. Use `pipeline.py plan` to inspect argv and requirements. It does not execute or establish runtime qualification.
4. Execute the native track, preserve traces/private scoring and output lineage, then use `pipeline.py analyze`.
5. For LLM/Agent target changes, read [target evolution](references/target-evolution-contract.md). Use `target-build` only for bounded JSON/YAML prompt or policy edits, or register external assets. For `agent_execution`, use the model/CLI target templates: system prompts and bounded runtime policy are materialized into a hashed effective config, while commands, endpoints and credentials stay outside recipe mutation. Run `target-preflight` before external execution; it must not call a model or start the CLI, and a ready report does not authorize execution. `target-evaluate` may run paired internal-Agent jobs only after explicit authorization for model/CLI targets. Compare paired safety/regression results before any release decision.
6. Run `python scripts/check_repo.py` before handing changes to collaborators. Report skips and qualification gaps.

## Contracts

- Tracks retain separate populations and metric directions/units; unknown and invalid are not passes.
- Project V1 starts at 1.0.0. The default protocol binds three synthetic reference questions. Freeze independent dataset, model and scorer identities for real runs; see [protocol](references/protocol-contract.md).
- Internal Agent tasks are source-derived; mapping or mock evidence does not prove official reproduction or product qualification.
- Public runtime tasks cannot expose private oracles.
- Failure reasons are observed signals, with root cause unconfirmed until verified.
- Candidate plans are unaccepted. Eight acceptance gates require provenance/license, replay, benign solvability, private rules, deduplication, diversity, holdout isolation and review evidence.
- Bounded recipe-based prompt/policy candidate building, non-executing model/CLI readiness checks, runtime prompt adaptation, candidate registration, paired internal-Agent execution, comparison and evidence-only promotion decisions are implemented. Model-generated optimization, non-Agent execution scheduling, deployment and rollback remain external work; `target-smoke` is synthetic control-plane validation only.
- Generation, acceptance execution and dataset publication remain future implementation work.
- Preserve existing controlled product profile constraints; use a versioned profile with new evidence for changes.

[Phase contracts](references/phase-contracts.md) describe the optional phase router. Phase6–8 are reserved and emit no runnable commands. Do not claim production publish capabilities based on those phase names.
