# 协作指南

## 安装与提交前检查

从仓库根目录操作，Windows + Python 3.11/3.14 是当前验证环境：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe scripts/check_repo.py
```

CI 调用同一个验收入口并保留日志。只改文档时可先运行 `scripts/check_repo.py --docs-only`。完整验收中的外部集成测试可以因明确缺失资产跳过；新增 adapter 或宣称正式支持时，需要额外提交真实环境验证证据。

## 版本约定

整合后的项目从 V1（1.0.0）开始，以根 `VERSION` 为准。数据版本、协议版本、原生 schema 和第三方 runtime/model 版本独立记录。修改评测器时同步重算默认协议和模板中的脚本 SHA-256；真实实验另建冻结协议，不能沿用合成自测身份。

## 从哪一层扩展

| 修改目标 | 入口 |
|---|---|
| 新 track 或路由 | `protocol/evaluation_tracks.json`、`scripts/pipeline_cli.py` |
| 结果解释/失败分析 | `scripts/result_analysis.py`、`tests/test_pipeline_integration.py` |
| LLM / Agent 受控目标候选构建、预检、执行与比较 | `evolution/target_optimizer.py`、`target_preflight.py`、`target_executor.py`、`target_loop.py`、`tests/test_target_loop.py`、`references/target-evolution-contract.md` |
| LLM 配置或评分 | `llm_eval/multi_model_rule_eval_prompt_only.py`、`protocol/` |
| Agent/工具/私有评分 | `harness/src/agent_safety_harness/`、`harness/tests/` |
| 新产品/原生 benchmark | `federated_asr_benchmark/product_eval/`、registry、schema、模块测试 |
| 自动测试集演化 | 先按[研究计划](docs/integration/research_plan.md)落实候选生成与验收契约 |

复用已有执行器与 schema。公开任务不带私有评分答案；新增指标必须保留轨道、方向、分母和版本。失败 reason 只是观察，根因需要轨迹/环境证据。候选计划不等同于接纳任务。

## PR 应提供

- 改了什么、为什么，以及可复现的命令。
- 测试通过/失败/跳过数量，输出证据与未完成的环境资格。
- 接口、数据/模型/产品/scorer 版本或指标变化。
- 同步的文档、配置示例和必要回归测试。

原始数据、密钥、运行输出和虚拟环境不进 Git。模型/endpoint/凭证来源/并发/重试/预算使用配置；已有受控产品 profile 的版本约束必须经过新 profile 与证据更新后才能修改。拒绝覆盖已有非空输出目录，续跑使用原生显式选项。工作约定见 [AGENTS.md](AGENTS.md)。
