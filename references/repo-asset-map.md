# 仓库资产地图

所有位置相对仓库根目录；当前 checkout 是主仓库，个人旧工作区仅作为迁移来源。

| 当前存在的资产 | 用途 |
|---|---|
| `pipeline.py`、`scripts/pipeline_cli.py` | 四轨入口与执行计划 |
| `scripts/result_analysis.py` | 结果观察、异常、候选计划 |
| `scripts/protocol_manager.py` | 协议初始化/校验/比较 |
| `scripts/source_intake.py` | 来源证据登记和 catalog |
| `scripts/canonicalize_datasets.py`、`canonical_validate.py` | LLM 规范化与统计校验 |
| `scripts/prepare_phase3.py` | LLM 子集与运行配置 |
| `eval_mvp_V2/multi_model_rule_eval_prompt_only.py` | LLM 评测器 |
| `harness/` | 内部执行、私有评分、离线 judge |
| `federated_asr_benchmark/` | 产品原生评测 |
| `protocol/` | 当前版本的 schema/taxonomy/metrics/tracks |
| `assets/templates/`、`assets/samples/` | 模板、合成样例 |
| `scripts/check_repo.py` | 文档/测试/示例统一验收 |

## 外部生产约定

以下大数据/结果不随 Git 提供，路径由生产协议保留：

- LLM canonical：`eval_mvp_V2/data/master_normalized_dataset_v2_final.jsonl`。
- LLM 派生统计：`output/doc/v2_stats.json`。
- LLM 权威发布根：`rule_eval_mvp/output_v2_final_authoritative/`。
- 产品原始数据：`AGENT_EVAL_DATA_ROOT` / `--data-root`。
- 原生第三方 runtime：专用解释器与源码/Docker/VM。
- 输出、密钥、环境：`runs/`、环境变量、虚拟环境，不进 Git。

原完整语料的 canonical 数量与版本不能套用到合成样例。Phase 6–8 增量/合并/发布执行器没有随仓库实现，不输出对应可执行命令。以[阶段契约](phase-contracts.md)、[配置](../docs/configuration.md)为准。
