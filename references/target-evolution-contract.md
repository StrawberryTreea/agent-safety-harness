# Target Evolution Contract

Use this contract for Loop B work that changes an evaluated LLM or Agent target. The implemented control layer can apply an explicit bounded recipe to an existing JSON/YAML prompt or policy asset, register external candidates, materialize those assets into model/CLI runtime configuration, execute paired internal-Agent jobs, and compare native results. It does not use an LLM to invent optimizations, patch executable code, train weights, schedule LLM QA/product/trace jobs, deploy a target, or perform rollback.

## Evidence flow

1. Run the native baseline evaluation and `pipeline.py analyze`.
2. Build a prompt/policy candidate with `target-build`, or create a candidate asset outside the evaluator. External registration also accepts `tools`, `code`, `model_route`, and `training_plugin` change types.
3. Register the immutable candidate asset against `failures.jsonl`. Registration records its SHA-256, observed-signal lineage, and an explicit `root_cause=unconfirmed` boundary.
4. For `agent_execution`, run `target-preflight`. Resolve structural, credential-name, executable, prompt-transport, MCP and unintended-drift blockers without invoking the target.
5. After explicit external-execution authorization, use `target-evaluate` or run manually. Evaluate baseline and candidate with the same frozen safety set, native scorer, environment, seeds, and budget, plus a separate benign/capability regression set. Other tracks remain manual.
6. Run a development comparison. A passing development comparison is not promotable.
7. Stop tuning, run a sealed release comparison, and review the generated decision. A `promote` record is evidence for an external release process; it performs no deployment.

## Bounded prompt/policy candidate build

Start from `assets/templates/target_optimizer_recipe.yaml`. `target-build` reads one JSON/YAML mapping, requires every changed dot path to exist and appear in `allowed_paths`, and supports only `set`, `append_text`, and `prepend_text`. Prompt recipes are restricted to prompt-like leaves; policy recipes are restricted to `agent_id`, `temperature`, `max_tokens`, and `timeout_seconds`. It rejects evaluator/oracle/answer/credential paths and execution-routing fields such as commands, endpoints, environment injection and referenced config paths. Repeated paths, no-op recipes, non-matching failure signals, array addressing, code patches, model calls, and deployment hooks are also unsupported.

```powershell
python pipeline.py target-build `
  --baseline-asset work/targets/agent-v1.yaml `
  --recipe work/evolution/agent-v2-recipe.yaml `
  --failures runs/baseline/analysis/failures.jsonl `
  --out runs/evolution/agent-v2-build
```

The output contains the generated `candidate.json` or `candidate.yaml`, a build `manifest.json`, and `registration/target_candidate.json`. The latter records baseline/candidate/recipe hashes, allowlisted changed paths, observed-signal applicability, and `root_cause=unconfirmed`. The recipe is deterministic user-authored configuration; this is not model-generated or automatically accepted optimization.

## External candidate registration

```powershell
python pipeline.py target-register `
  --track agent_execution `
  --baseline-id agent-v1 `
  --candidate-id agent-v2 `
  --change-type policy `
  --candidate-asset work/candidates/agent-v2.json `
  --baseline-asset work/candidates/agent-v1.json `
  --failures runs/baseline/analysis/failures.jsonl `
  --out runs/evolution/agent-v2-registration
```

The output contains `target_candidate.json` and `manifest.json`. Candidate directories are hashed over sorted relative paths and file digests. Registration never marks a candidate validated or promotable.

## Non-executing target preflight

Run preflight before authorizing any `model:` or `cli:` evaluation:

```powershell
python pipeline.py target-preflight `
  --candidate-manifest runs/evolution/agent-v2-build/registration/target_candidate.json `
  --out runs/evolution/agent-v2-preflight
```

Preflight validates the registered baseline and candidate hashes, materializes the same read-only effective configurations used by execution, and writes `readiness.json`. It checks model credential environment-variable presence without reading or recording the value. For CLI targets it checks the controlled child PATH, launcher/delegated executable identities, credential variable names, prompt transport and benchmark MCP wiring. It never runs `--version`: the executable path, size and SHA-256 are recorded instead so preflight does not start the target process.

CLI MCP readiness requires a server command bound to `{task_json}` and `{mcp_events}`, plus a command or environment value that consumes `{mcp_config}`. `{python_executable}` and `{harness_ash}` expand at runtime to the current interpreter and repository Harness entry point. A missing executable, credential, prompt transport or MCP binding produces `status=blocked`; the CLI returns 2 after preserving the report. Warnings, such as a competing hard-coded system-prompt flag, do not by themselves make the target ready or blocked.

`target-preflight` does not require `--allow-external-targets` because it makes no model API call and starts no CLI process. Passing preflight is necessary but not sufficient for evaluation qualification: provider reachability, actual CLI version output, budget, live MCP negotiation and benchmark-environment validity are established only by separately authorized execution evidence.

## Synthetic end-to-end check

```powershell
python pipeline.py target-smoke --out runs/target-smoke
```

This local fixture runs `unsafe_stub` as the baseline, derives failure observations, applies a bounded policy recipe that selects `safe_stub`, executes both targets through the native capture/score/report path, separates safety and benign regression populations, and performs a development comparison. It calls no model API and cannot promote or deploy anything. Its expected `development_pass` validates control-plane wiring only; it is not evidence that a real prompt, policy, model, Agent, or product became safer.

## Paired internal-Agent execution

Start from `assets/templates/target_agent_config.yaml` for local stubs, `target_model_config.yaml` for an OpenAI-compatible model, `target_cli_config.yaml` for a CLI Agent, and `target_evaluation_job.yaml` for the paired job. The registered candidate and baseline assets must be target configuration files, or directories containing exactly one `target.yaml`, `target.yml`, or `target.json`. Referenced `models.json` or `agents.json` files must remain inside the registered directory so its asset hash binds the executable configuration.

```powershell
python pipeline.py target-evaluate `
  --candidate-manifest runs/evolution/agent-v2-build/registration/target_candidate.json `
  --job work/evolution/development-job.yaml `
  --policy work/evolution/policy.yaml `
  --out runs/evolution/agent-v2-evaluation
```

The job requires disjoint safety and benign-regression populations, each with public tasks and private rules. Before execution it checks exact public/private task-ID parity, expected counts, distinct population IDs, that public tasks contain no `evaluation` oracle, and that private rows contain scoring rules. It runs baseline and candidate through capture, private score, report, and analysis for both populations; then it checks that the target assets, candidate manifest, job, policy, public tasks, and private rules did not drift before calling `target-compare`.

### Runtime target asset contract

An inline `model:<id>` target contains a `model` object with `provider: openai_compatible`, `base_url`, `api_key_env`, provider model ID and bounded generation/timeout settings. The optional `prompt.system_prompt` is copied into the model's system message. The optional `policy` may override only `temperature`, `max_tokens`, and `timeout_seconds`. Store only the credential environment-variable name in `api_key_env`; never store a key value in the asset.

An inline `cli:<id>` target contains a `cli` object using the Harness CLI adapter. `prompt.system_prompt` is prepended to the benchmark prompt and reaches the Agent through standard input and the generated prompt file; it is never interpolated into the command. CLI `policy` may override only `timeout_seconds`. Controlled target execution requires `inherit_env: false`; required inherited variables are named in `env_allowlist`, and literal credential-looking keys are rejected from `env`. The template uses `{python_executable}` and `{harness_ash}` to start the repository MCP server with per-task `{task_json}` and `{mcp_events}`. A real CLI run must still consume `{mcp_config}` and produce validated environment evidence; a process that merely prints text is not a qualified Agent execution.

Instead of inline `model`/`cli`, a registered directory may use `models_config: models.json` or `agents_config: agents.json`. Prompt and policy overlays are applied to the selected entry after loading. For every native run, `target-evaluate` writes a read-only-by-convention effective config under `runtime_config/`, records its SHA-256, and passes that exact file to `ash.py capture`. The registered source asset is not modified. Public manifests retain configuration and prompt hashes but do not embed the prompt or effective configuration.

Local `safe_stub` and `unsafe_stub` targets are allowed by default. `model:`, `reference:`, and `cli:` targets are rejected unless the caller adds `--allow-external-targets`; that flag is explicit authorization for possible credentials, API budget, and local CLI execution. It is not implied by candidate registration or by an ordinary development run. A `reference:` target may use model runtime policy but deliberately rejects a prompt overlay because its benchmark reference prompt remains fixed. The executor currently supports `agent_execution` only and never deploys the decision.

## Paired comparison

Start from `assets/templates/target_evolution_policy.yaml` and `assets/templates/target_evaluation_manifest.json`. Freeze thresholds before reading candidate outcomes. The manifest records safety and regression set IDs, SHA-256 digests, expected case counts, evaluation role, and isolation claims.

```powershell
python pipeline.py target-compare `
  --candidate-manifest runs/evolution/agent-v2-registration/target_candidate.json `
  --baseline-results runs/baseline/score/results.jsonl `
  --candidate-results runs/candidate/score/results.jsonl `
  --baseline-regression-results runs/baseline-benign/score/results.jsonl `
  --candidate-regression-results runs/candidate-benign/score/results.jsonl `
  --evaluation-manifest work/evolution/development-set.json `
  --policy work/evolution/policy.yaml `
  --out runs/evolution/agent-v2-development
```

For different native populations, set `--regression-track`. For example, a full-product safety comparison can use an `agent_execution` or `llm_qa` regression population. Do not combine their raw metrics; the regression gate only checks the separately declared population.

## Gates and decisions

The comparison preserves each track's direction: higher pass rate is better for LLM QA, internal Agent execution, and trace judging; lower ASR is better for product evaluation. It verifies:

- exact case identity parity when required;
- frozen expected counts and minimum paired cases;
- maximum unknown/invalid rate;
- minimum safety improvement and candidate safety score;
- maximum benign/capability regression;
- optional paired sign-test threshold;
- optional total-token and mean-latency growth limits;
- private-oracle isolation and sealed release-set conditions.

Outputs are `comparison.json`, `decision.json`, and `manifest.json`.

- `development_pass`: all configured gates passed, but an independent release comparison is still required.
- `promote`: all release gates passed; no deployment was performed and the baseline remains the rollback target.
- `reject`: comparable evidence shows a configured safety, regression, confidence, or cost gate failed.
- `insufficient_evidence`: coverage, identity parity, validity, or expected counts are incomplete.

Do not use a development set as a release holdout after its per-case results have entered optimization. A release manifest must state that the set was frozen before candidate creation, case details were hidden from the candidate process, and private oracles were not exposed.
