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
| 回路A当前代码与本地验证 | [基础实现与限制](integration/loop_a_implementation.md) |
| 回路A分区与产品预检 | [第二批实现与迁移](integration/loop_a_phase2.md) |
| DeepSeek 普通 LLM 工程测试 | [预算、真实调用与结果边界](integration/deepseek_smoke.md) |
| DeepSeek 生成、重放与良性对照 | [受控LLM实际试验](integration/deepseek_pilot.md) |
| Agent 执行与私有评分 | [Harness](../harness/README.md)、[adapter 状态契约](../harness/BENCHMARK_ADAPTER_STATUS.md) |
| 分发给多位评测者 | [分发包](../harness/docs/distribution.md) |
| 完整产品评测 | [产品模块](../federated_asr_benchmark/README.md)、[分阶段选择集](../federated_asr_benchmark/docs/staged_execution.md) |
| LLM 数据与协议 | [资产地图](../references/repo-asset-map.md)、[阶段契约](../references/phase-contracts.md)、[协议契约](../references/protocol-contract.md) |
| 下一轮闭环实验 | [研究计划](integration/research_plan.md) |
| 自动化评测与自进化 Harness 设计 | [结构图与模块方案（草案）](integration/harness_design.md) |
| 查看 V1 验收与清理 | [验证记录](integration/validation.md)、[清理记录](integration/cleanup.md) |

项目版本记录在根 `VERSION`。旧实验快照与失效入口已清理；以当前配置、preflight 和运行证据判断资格。
