---
protocol_version: v2_default
task_goal: "Evaluate test safety benchmark"
dataset:
  path: eval_mvp_V2/data/master_normalized_dataset_v2_final.jsonl
  version: v2_final
  total: 668820
  sha256: B2B6E8DBCDFE0C19D50444B3B684DB63023724A6DFB03902F44F43EB177E5647
  id_field: id
taxonomy_version: safety_taxonomy_8x19
metric_version: rule_eval
models:
  - test_model
evaluation_script:
  path: eval_mvp_V2/multi_model_rule_eval_prompt_only.py
  sha256: 1B20C9E8653FE08326F1643F59B1DA76BB1C3CC518B6702325C8C3F16366D89C
  prompt_version: prompt_only_v2
  scorer_version: rule_eval
comparability:
  baseline_protocol: null
  status: new_baseline
  statement: "Initial baseline for the safety_taxonomy_8x19 and rule_eval protocol."
publish_policy: internal
---

# Evaluation Protocol Card

Complete the frontmatter before evaluation. Phase 0 validation treats the frontmatter as the machine-readable protocol record.

## Notes

- Do not store API keys, access tokens, passwords, or other secrets in protocol artifacts.
- Change `comparability.status` and explain the effect whenever dataset, taxonomy, metric, prompt, or scorer semantics change.
- Phase 7 validation is still required before an authoritative release.
