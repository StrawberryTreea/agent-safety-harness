# Adapter 状态与正式准入

本文件描述代码契约，不把迁移来源的个人实验计数或抽检成绩当成当前 checkout 的能力。

## 四种状态

| 状态 | 必需证据 | 不能据此声称 |
|---|---|---|
| 来源已映射 | 原始字段、provenance、TaskSpec 校验 | 原生环境已执行 |
| 工具环境可执行 | 版本、工具 schema、状态变化与轨迹 | 评分 oracle 已成立 |
| 私有规则可评估 | 规则 ID、命中证据、可重放、unknown 路径 | 全集有正式资格 |
| 正式任务集可用 | 上述证据、许可、去重、隔离、验收清单与覆盖率 | 其他版本/产品也合格 |

具体入口见 [Harness README](README.md)、[benchmark 配置](config/benchmarks.json)、[来源导入配置](config/benchmark_imports.json)、[adapter 实现](src/agent_safety_harness/adapters)。产品原生 adapter 另见[产品模块](../federated_asr_benchmark/README.md)。

## 评分规则

- 私有 oracle 不进入公开任务或被测产品提示。
- safety_violation 与 task_success 独立；缺完成规则的攻击任务没有完成率分母。
- 环境、工具、规则错误输出 unknown，不能补成安全通过。
- 确定性命中需要规则与执行证据；人工/LLM judge 保留不同契约。
- 源数据规模、固定索引和 mock 结果不等于正式可评分集。

当前自包含证据和外部验证的限制统一记录在[验证记录](../docs/integration/validation.md)。新增/更新 adapter 要提交对应环境、规则、失败与 unknown 回归。
