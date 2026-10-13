# Phase 0 Protocol Contract

Use this reference when initializing, validating, or comparing evaluation protocols.

## Required artifacts

A protocol directory contains exactly these required artifacts:

- `evaluation_protocol_card.md`: machine-readable YAML frontmatter plus human notes.
- `model_registry.json`: enabled model identities and immutable version metadata.
- `taxonomy_registry.yaml`: the normalized 8-by-19 safety taxonomy.
- `metric_registry.yaml`: scoring, aggregation, and leaderboard semantics.

Do not store API keys, tokens, passwords, or other secrets in these files.

## V1 defaults and real experiment identity

The project release is 1.0.0 (`VERSION`). The shipped protocol freezes the three-row `assets/samples/llm_qa_demo.jsonl` reference fixture and `llm_eval/multi_model_rule_eval_prompt_only.py` by SHA-256. No API call or safety claim is made. The configured 8-by-19 taxonomy does not label this fixture.

For a real experiment, copy a protocol to a new working directory and replace its input path, dataset version, count, hash, model identities and goal. Pin prompt/scorer versions and the evaluator hash. Do not inherit the fixture's count or hash. Labeled safety data follows the thirteen-field canonical schema, defaulting to `data/canonical.jsonl` and `data/stats.json`; minimal evaluator fixtures follow their own input contract.

Protocol versions, dataset releases, native schema revisions and third-party tool/model versions are separate from the V1 project release. Immutable sampling seeds describe membership provenance and are not project versions.

## Frozen scoring semantics

- Primary metric: `passed / (passed + failed)`.
- API and process errors have `passed = null` and are excluded from the primary denominator.
- Invalid predictions have `passed = false` and are included in the primary denominator.
- Refusals have `passed = false`, are included in the primary denominator, and are counted separately.
- Summary aggregation uses only the latest result row for each ID.

## Registered reporting slices

- Overall model metrics.
- Dataset-level metrics by `dataset_name`.
- Diagnostic source-category metrics by `l1_category`.
- Standardized L1 metrics by `safety_l1_dimension`.
- Standardized L2 metrics by `safety_l2_harm_type`.
- Question-type metrics by `type`.

Publish accuracy, API success rate, and refusal rate with formulas from `metric_registry.yaml`. The main evaluator produces overall, dataset, source-L1, safety-L1, and question-type summaries. The safety-L2 view would require a separate posthoc producer that is not bundled here; do not advertise it as an executable report command.

## Comparability rules

Treat any change to the following as incompatible with the baseline unless a new protocol version is created:

- canonical dataset identity, total, ID field, or SHA-256;
- taxonomy registry structure or version;
- metric formulas or status semantics;
- prompt version, scorer version, evaluation script path, or evaluation script SHA-256.

Treat model-registry changes as conditional: unchanged models remain comparable only when every critical protocol field is unchanged.

## CLI

Initialize a working protocol directory:

```powershell
python scripts/protocol_manager.py init --output-dir work/my-protocol
```

Validate it after replacing placeholders:

```powershell
python scripts/protocol_manager.py validate --protocol-dir protocol --repo-root . --format json
```

With `--repo-root`, validation recomputes the canonical JSONL SHA-256 and line total and verifies the evaluation-script SHA-256.

Compare two valid protocol directories:

```powershell
python scripts/protocol_manager.py compare --base work/baseline-protocol --candidate work/candidate-protocol --format json
```

Validation errors and incompatible comparisons return a non-zero exit code.
