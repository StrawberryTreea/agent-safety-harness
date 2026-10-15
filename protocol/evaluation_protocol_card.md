---
protocol_version: v1_fixture_identity_choice_v2
task_goal: Verify evaluator plumbing with three synthetic reference-answer questions;
  no model safety claim.
dataset:
  path: assets/samples/llm_qa_demo.jsonl
  version: v1_fixture
  total: 3
  sha256: 6316390B628383D111AE45D3C384D7AD682DB25EECF97E849DCD547B2680A5BB
  id_field: id
taxonomy_version: safety_taxonomy_8x19
metric_version: rule_eval
models:
- self_test_reference
evaluation_script:
  path: llm_eval/multi_model_rule_eval_prompt_only.py
  sha256: 2539D503A29A04F217925BF6F0A202F69886E5AA90542770C866A4A364D9023E
  prompt_version: prompt_only_v1
  scorer_version: rule_eval_conservative_choice_v2
comparability:
  baseline_protocol: null
  status: new_baseline
  statement: Local fixture baseline only. Taxonomy reporting is configured but the
    fixture has no safety taxonomy labels.
publish_policy: internal
---

# V1 Evaluation Protocol Card

The shipped protocol binds a three-row synthetic fixture and the evaluator by SHA-256. It verifies reading, scoring and reporting only. It is not a safety dataset or a real model evaluation.

For a real experiment, replace the dataset identity, goal, models and version bindings together. Preserve credentials in environment variables. Publication requires a separate reviewed release; Phase 6–8 have no executor.
