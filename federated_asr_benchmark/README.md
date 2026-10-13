# 完整产品与原生 benchmark ASR

[项目入口](../README.zh-CN.md) · [配置](../docs/configuration.md) · [分阶段协议](docs/staged_execution.md)

本模块用完整产品 CLI 替换原 benchmark 的 Agent runner，保留原生任务、工具、环境与 scorer。测量产品默认系统行为、规划和工具编排；结果不能直接与原论文模型排行榜混排。

## 当前资产和限制

仓库有 adapter 源码、schema、registry、请求示例、固定索引和选择集。`case_index.jsonl` 的 v0.1 快照有 5,384 条：InjecAgent 1,054、MSB 2,000、AgentDojo 629、AgentLeak 496、SABER 715、AgentPoison-StrategyQA 490。索引不是原始数据或当前已完成的成绩。

其他 adapter/扩展候选也有源码与测试，不等同于 v0.1 正式集合或可计算全产品结果。大型数据、第三方源码/运行环境、CLI、Docker、凭证需要另外准备。外部测试 skip 不构成正式资格。

## 本地查看和计划

从仓库根、已安装根依赖的 Python 环境运行：

```powershell
.\.venv\Scripts\python.exe federated_asr_benchmark/tools/run_product.py --help
.\.venv\Scripts\python.exe pipeline.py plan --track agent_product --request federated_asr_benchmark/examples/requests/codex_saber.json --data-root D:/benchmark-data --case-id saber:A_code_007 --out runs/product-plan
```

plan 不执行产品；只输出 dry-run 的 argv。原生 `--dry-run` 也需要对应资产才能检查环境，不能把 dry-run 当评分结果。

## 准备真实运行

按[配置指南](../docs/configuration.md)设置 `AGENT_EVAL_DATA_ROOT` 和专用解释器。构建索引并运行 preflight：

```powershell
.\.venv\Scripts\python.exe federated_asr_benchmark/tools/build_case_index.py --data-root $env:AGENT_EVAL_DATA_ROOT --output-dir runs/rebuilt-index
.\.venv\Scripts\python.exe federated_asr_benchmark/tools/preflight.py --data-root $env:AGENT_EVAL_DATA_ROOT
```

检查 [product_registry.json](product_registry.json)、[benchmark_registry.json](benchmark_registry.json)、[tested_profiles.json](release/tested_profiles.json) 与 [run_request schema](schemas/run_request.schema.json)。当前受控 profile 固定 DeepSeek V4 Flash；Codex 0.122.0、Claude Code 2.1.195、Qoder 1.0.48 是锁定版本，Qoder BYOK runtime ID 与 CodeBuddy 的路由绑定也受 runner 检查。API judge 的模型/接口/温度由单独的 judge policy 固定。改变版本/模型要新建 profile 和资格证据。

preflight 包含环境 pins、API judge、路由与 release blockers；必须先解决失败。真实执行使用 `tools/run_product.py` 或 `tools/run_product_batch.py`，以其 `--help` 和[分阶段协议](docs/staged_execution.md)为准。Qoder batch 使用现存 BYOK wrapper。

## 公平性与结果

每条 case 用新产品状态，不加载个人记忆、技能或项目规则；权限和网络按 case 限定。固定产品/模型、adapter、benchmark revision、scorer、种子和预算。不同产品分别统计。

```text
ASR-valid = sum(asr_numerator) / sum(asr_denominator)
Macro-ASR = mean(required benchmark ASR-valid)
```

`status=success` 表示攻击成功，ASR 越低越好。普通 case 用 1/1 或 0/1；AgentPoison 保留官方 ASR-r retrieval-event 单位。invalid/excluded 不是安全通过；环境/API/judge 错误保留 0/0。required benchmark 未达覆盖率门槛时不发 Macro-ASR。

[case_result schema](schemas/case_result.schema.json)保留官方结果与证据。`tools/aggregate_results.py` 负责原生 ASR 汇总；`pipeline.py analyze --track agent_product` 负责失败计划，不能替代正式资格审查。

## 代码导航

- `product_eval/`：产品命令、原生 adapter、模型/judge policy、路由审计、结果规范化。
- `tools/`：preflight、索引、选择集、单条/批量/adaptive 运行、scorer、聚合。
- `examples/requests/`：版本化产品请求；`selections/`：固定分阶段集合。
- `requirements-*.lock`：专用外部 runtime 依赖，不是根控制器的安装入口。
- `tests/`：自包含与资产依赖测试；根 `scripts/check_repo.py` 统一调用。
- `docs/*audit.json`：迁移的历史审计快照，对应运行输出在外部，不代表当前版本合格。

现场验证见[验证记录](../docs/integration/validation.md)。
