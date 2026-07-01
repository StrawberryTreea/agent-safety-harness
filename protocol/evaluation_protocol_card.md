---
protocol_version: v2_rule_eval_baseline
task_goal: "Evaluate eight production large-language models on the 668,820-item canonical V2 safety dataset with reproducible rule-based scoring, publishable leaderboards, and safety whitepaper reporting across the 8x19 taxonomy and question types."
dataset:
  path: eval_mvp_V2/data/master_normalized_dataset_v2_final.jsonl
  version: v2_final
  total: 668820
  sha256: B2B6E8DBCDFE0C19D50444B3B684DB63023724A6DFB03902F44F43EB177E5647
  id_field: id
taxonomy_version: safety_taxonomy_8x19
metric_version: rule_eval
models:
  - deepseek_v4_pro
  - kimi_k2_6_full
  - doubao_seed_2_0_pro_full
  - qwen3_6_max_preview
  - mimo_v2_5_pro
  - ling_2_6_1t_antling_official
  - tencent_hy3_preview
  - ernie_5_1
evaluation_script:
  path: eval_mvp_V2/multi_model_rule_eval_prompt_only.py
  sha256: 1B20C9E8653FE08326F1643F59B1DA76BB1C3CC518B6702325C8C3F16366D89C
  prompt_version: prompt_only_v2
  scorer_version: rule_eval
comparability:
  baseline_protocol: null
  status: new_baseline
  statement: "Initial multi-model publication baseline for safety_taxonomy_8x19, rule_eval, and the pinned prompt-only evaluator."
publish_policy: candidate
intended_outputs:
  - public_leaderboard
  - public_safety_whitepaper
---

# Evaluation Protocol Card

Complete the frontmatter before evaluation. Phase 0 validation treats the frontmatter as the machine-readable protocol record.

## Notes

- Do not store API keys, access tokens, passwords, or other secrets in protocol artifacts.
- Change `comparability.status` and explain the effect whenever dataset, taxonomy, metric, prompt, or scorer semantics change.
- Phase 7 validation is still required before an authoritative release.
