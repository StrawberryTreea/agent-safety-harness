# 阶段契约与实现状态

新协作者优先使用 `pipeline.py` 和[上手指南](../docs/getting_started.md)。阶段路由用于 LLM 数据/协议治理与未来工作规划，不代表所有阶段已实现。

| 阶段 | 实现入口 | 输入 → 产物 |
|---|---|---|
| 0 协议 | `scripts/protocol_manager.py` | 模板/版本 → 协议 artifacts 与校验 |
| 1 来源登记 | `scripts/source_intake.py` | 外部数据/证据 → catalog 与 manifest |
| 2 规范化 | `scripts/canonicalize_datasets.py`、`canonical_validate.py` | catalog → JSONL/stats/校验 |
| 3 评测准备 | `scripts/prepare_phase3.py` | canonical/协议 → 子集与 run_config |
| 4 自测 | LLM evaluator + Phase3 配置 | 子集/reference → 规则自测 |
| 5 真实 LLM | LLM evaluator + 外部配置 | 数据/API → 逐条结果与 summary |
| 6 增量重跑 | **reserved，无随仓库执行器** | 规划契约：旧结果/错误/追加数据 → delta |
| 7 合并/发布 | **reserved，无随仓库执行器** | 规划契约：canonical/clean merged → 权威发布 |
| 8 正式报告 | **reserved，无随仓库执行器** | 规划契约：权威结果 → leaderboard/报告 |
| 9 Agent | `pipeline.py plan` 与原生模块 | 执行/产品/离线 judge，各用自己的契约 |
| 10 分析与目标进化交接 | `pipeline.py analyze/target-build/target-register/target-preflight/target-evaluate/target-compare` | 原生结果 → 失败/异常/受控候选构建或登记/非执行式目标预检/内部 Agent 配对执行/比较/非执行式晋升决策 |

`phase_executor.py` 是检查和命令建议工具，不自动执行阶段。Phase6–8 返回 `implementation_status=reserved`、失败检查和空命令列表。检查到数据路径存在不等于正式发布已经完成。

```powershell
python scripts/intent_router.py --request "智能体失败分析与进化"
python scripts/phase_executor.py --phase phase9 --emit-commands
python scripts/phase_executor.py --phase phase7 --check-only --format json
```

最后一条在当前仓库应报告未实现并返回非零。不能将生产流程名称当成已存在代码。LLM canonical/stats/publish 约定仅用于 llm_qa，不挡住 Agent smoke。详细评分语义见[协议](protocol-contract.md)、[架构](../docs/integration/architecture.md)。

回路 B 的受控 JSON/YAML 提示/策略构建、候选登记、非执行式模型/CLI 预检、内部 Agent 配对执行与比较属于 Phase 10 交接能力。`target-smoke` 只执行本地合成 fixture；模型生成式优化、其他轨道调度、部署与回滚仍未实现。使用前读[目标进化契约](target-evolution-contract.md)。
