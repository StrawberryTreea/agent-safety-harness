# 协作者清理记录

日期：2026-10-13。目标：协作者从清楚的文档入口安装并运行，避免旧实验材料被误认为当前能力。

## 本轮改动

- 重写中英文 README、Harness/产品 README、资产地图、阶段与 adapter 状态说明。新增文档导航、上手、配置、协作与分发指南。
- runtime 依赖统一在 requirements.txt；requirements-dev.txt 引用它，删除不一致的 environment.yml。
- Phase6–8 标为 reserved，不再输出不存在的执行命令。LLM 没有启用模型或模型级异常时返回非零。
- 增加 scripts/check_repo.py，统一检查本地文档链接、三套测试、safe/unsafe 示例和 LLM 自测/分析，CI 调用同一入口。
- 删除旧 v1 整理器、一次性修补/个人固定队列/重复报告代码，以及旧成绩和状态文档；通用 runner、BYOK、v2 整理器和被调用的构建器保留。
- 移除误提交的 work/phase12_demo 生成产物，避免拿旧 protocol/输出作为当前输入。活跃测试自己生成独立 fixture。

删除文件可从 Git 历史恢复。迁移清单固定为 bff2ef 提交的历史快照，并记录删除 disposition；不再宣称所有迁移源文件仍在当前 checkout。

## 删除清单

| 文件 | 原因 |
|---|---|
| `docs/phase1_phase2_phase3_handoff.md` | 过时状态、个人成绩或重复架构文档；用新正式指南替代 |
| `environment.yml` | 旧环境版本与当前依赖/CI 不一致 |
| `federated_asr_benchmark/docs/agent_security_evaluation_briefing_zh.md` | 过时状态、个人成绩或重复架构文档；用新正式指南替代 |
| `federated_asr_benchmark/docs/codebuddy_candidate_qualification.md` | 过时状态、个人成绩或重复架构文档；用新正式指南替代 |
| `federated_asr_benchmark/docs/current_status.md` | 过时状态、个人成绩或重复架构文档；用新正式指南替代 |
| `federated_asr_benchmark/docs/dataset_taxonomy_asr_report_zh.md` | 过时状态、个人成绩或重复架构文档；用新正式指南替代 |
| `federated_asr_benchmark/docs/design_assessment.md` | 过时状态、个人成绩或重复架构文档；用新正式指南替代 |
| `federated_asr_benchmark/docs/qoder_route_audit.md` | 过时状态、个人成绩或重复架构文档；用新正式指南替代 |
| `federated_asr_benchmark/docs/sample_logs_zh.md` | 过时状态、个人成绩或重复架构文档；用新正式指南替代 |
| `federated_asr_benchmark/docs/successful_attack_case_bilingual.md` | 过时状态、个人成绩或重复架构文档；用新正式指南替代 |
| `federated_asr_benchmark/docs/v02_smoke_status.md` | 过时状态、个人成绩或重复架构文档；用新正式指南替代 |
| `federated_asr_benchmark/tools/build_taxonomy_asr_report.py` | 一次性或重复的旧实验工具；保留活跃替代入口 |
| `federated_asr_benchmark/tools/run_remaining20.py` | 一次性或重复的旧实验工具；保留活跃替代入口 |
| `harness/ENTERPRISE_API_AND_DISTRIBUTION.md` | 过时状态、个人成绩或重复架构文档；用新正式指南替代 |
| `harness/PARALLEL_CLI_EVAL.md` | 过时状态、个人成绩或重复架构文档；用新正式指南替代 |
| `harness/docs/mvp_task_allocation.md` | 过时状态、个人成绩或重复架构文档；用新正式指南替代 |
| `harness/docs/project_positioning.md` | 过时状态、个人成绩或重复架构文档；用新正式指南替代 |
| `harness/docs/unified_sandbox_runner.md` | 过时状态、个人成绩或重复架构文档；用新正式指南替代 |
| `harness/tools/classify_three_agent_results.py` | 一次性或重复的旧实验工具；保留活跃替代入口 |
| `harness/tools/correct_ras_ambiguous_matchers.py` | 一次性或重复的旧实验工具；保留活跃替代入口 |
| `harness/tools/curate_datasets.py` | 一次性或重复的旧实验工具；保留活跃替代入口 |
| `work/phase12_demo/intake.yaml` | 旧阶段 demo 的生成文件/实验配置 |
| `work/phase12_demo/phase1_ready/dataset_catalog.json` | 旧阶段 demo 的生成文件/实验配置 |
| `work/phase12_demo/phase1_ready/evidence_inventory.jsonl` | 旧阶段 demo 的生成文件/实验配置 |
| `work/phase12_demo/phase1_ready/source_inventory.csv` | 旧阶段 demo 的生成文件/实验配置 |
| `work/phase12_demo/phase1_review/dataset_catalog.json` | 旧阶段 demo 的生成文件/实验配置 |
| `work/phase12_demo/phase1_review/evidence_inventory.jsonl` | 旧阶段 demo 的生成文件/实验配置 |
| `work/phase12_demo/phase1_review/manifests/pending/ambiguous_typed.pending.yaml` | 旧阶段 demo 的生成文件/实验配置 |
| `work/phase12_demo/phase1_review/source_inventory.csv` | 旧阶段 demo 的生成文件/实验配置 |
| `work/phase12_demo/phase2/canonical.jsonl` | 旧阶段 demo 的生成文件/实验配置 |
| `work/phase12_demo/phase2/canonicalization_report.json` | 旧阶段 demo 的生成文件/实验配置 |
| `work/phase12_demo/phase2/dataset_sources_clean.csv` | 旧阶段 demo 的生成文件/实验配置 |
| `work/phase12_demo/phase2/membership_diff.json` | 旧阶段 demo 的生成文件/实验配置 |
| `work/phase12_demo/phase2/quarantine.jsonl` | 旧阶段 demo 的生成文件/实验配置 |
| `work/phase12_demo/phase2/stats.json` | 旧阶段 demo 的生成文件/实验配置 |
| `work/phase12_demo/protocol/canonical_schema.yaml` | 旧阶段 demo 的生成文件/实验配置 |
| `work/phase12_demo/protocol/evaluation_protocol_card.md` | 旧阶段 demo 的生成文件/实验配置 |
| `work/phase12_demo/protocol/metric_registry.yaml` | 旧阶段 demo 的生成文件/实验配置 |
| `work/phase12_demo/protocol/model_registry.json` | 旧阶段 demo 的生成文件/实验配置 |
| `work/phase12_demo/protocol/taxonomy_registry.yaml` | 旧阶段 demo 的生成文件/实验配置 |
| `work/phase12_demo/raw/ambiguous_typed.jsonl` | 旧阶段 demo 的生成文件/实验配置 |

当前运行证据见[验证记录](validation.md)。历史审计 JSON 仍保留为来源证据，不能当成当前产品资格。
