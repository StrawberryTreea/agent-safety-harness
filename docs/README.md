# 项目文档导航

第一次加入项目，按这个顺序阅读：

1. [项目入口](../README.zh-CN.md)：目标、当前实现、四条评测线、代码地图。
2. [上手运行](getting_started.md)：安装、统一验收、独立示例、结果目录、排错。
3. [配置](configuration.md)：模型 API、CLI、数据与专用环境。
4. [架构与原始思路图](integration/architecture.md)：模块职责、评分边界、反馈链。
5. [协作指南](../CONTRIBUTING.md)：扩展代码、测试和提交要求。

## 按工作选择文档

| 工作 | 文档 |
|---|---|
| Agent 执行与私有评分 | [Harness](../harness/README.md)、[adapter 状态契约](../harness/BENCHMARK_ADAPTER_STATUS.md) |
| 分发给多位评测者 | [分发包](../harness/docs/distribution.md) |
| 完整产品评测 | [产品模块](../federated_asr_benchmark/README.md)、[分阶段选择集](../federated_asr_benchmark/docs/staged_execution.md) |
| LLM 数据与协议 | [资产地图](../references/repo-asset-map.md)、[阶段契约](../references/phase-contracts.md)、[协议契约](../references/protocol-contract.md) |
| 下一轮闭环实验 | [研究计划](integration/research_plan.md) |
| 查看验证与迁移历史 | [验证记录](integration/validation.md)、[迁移快照](integration/migration_manifest.json)、[清理记录](integration/cleanup.md) |

`migration_manifest.json` 和保留的产品 audit JSON 是历史快照，不能作为当前数据可用、产品合格或正式成绩的证明。以当前配置和现场 preflight/运行结果为准。
