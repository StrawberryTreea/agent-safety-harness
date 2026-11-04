# 项目文档导航

**不确定该读哪份、从哪读起，先看这份**：
**[文档地图](DOCUMENT_MAP.md)** ⭐ —— **整体五层结构、按角色/按任务的阅读路径、
文档冲突时以谁为准、编码工具的检索范围与陷阱清单**（`AGENTS.md` 指向此文件）。

**第一次加入项目，按这个顺序读**：

1. **[项目总纲](project_overview.md)** ⭐ —— **目标是什么、有哪些功能、怎么规划、什么算做完**（全局视角，先读这份）
2. [文档地图](DOCUMENT_MAP.md)：全部文档分层、阅读路径、权威性裁决顺序。
3. [项目入口](../README.zh-CN.md)：目标、当前实现、四条评测线、代码地图。
4. [上手运行](getting_started.md)：安装、统一验收、独立示例、结果目录、排错。
5. [配置](configuration.md)：模型 API、CLI、数据与专用环境。
6. [架构与原始思路图](integration/architecture.md)：模块职责、评分边界、反馈链。
7. [协作指南](../CONTRIBUTING.md)：扩展代码、测试和提交要求。

**四位评测者第一次上手，先读这四份**：
[上手清单](day0_onboarding.md)（先敲什么命令、看到什么算成功、卡住怎么办）→
**[Pipeline 全景说明](pipeline_guide.md)**（**这个仓库的 pipeline 到底长什么样、11 个子命令、四条轨、数据怎么流**）→
[四人分工与推进顺序](work_assignment.md)（每人 P0–P2 做什么、跟谁衔接）→
[GitHub 协作规范](github_collaboration.md)（**分支怎么开、PR 怎么提、代码找谁审、密钥红线**）→
[跑通样例选型](rampup_samples.md)（第一轮用哪几个 benchmark、哪些产品）→
[benchmark 获取指南](benchmark_acquisition.md)（从哪下、下到哪、怎么登记与验收）。
测试阶段先用免费 API 打通管道，全部跑通后再用优质模型做贯通测试——免费端点见 `harness/config/free_endpoints.json`。

**任何人临时有事、或长期不上线怎么办**（含王浩的最终决定权与代理人接手规则）：
**[决策权、缺席与长期不上线](governance_and_continuity.md)** → [名册数据](roster.json)。

## 按工作选择文档

| 工作 | 文档 |
|---|---|
| **不知道该读哪份、从哪读起** | **[文档地图](DOCUMENT_MAP.md)**（**五层结构、按角色/按任务阅读路径、权威性裁决、编码工具陷阱清单**） |
| **想先搞清这个项目整体是什么样** | **[项目总纲](project_overview.md)**（**目标、边界、60+ 项功能清单与状态、三阶段规划、什么算做完**） |
| **不知道 pipeline 是什么、在哪、怎么跑** | **[Pipeline 全景说明](pipeline_guide.md)**（**总入口、11 个子命令、四条轨、数据流向、产物目录、代码在哪**） |
| **第一次分工 / 拿任务** | **[上手清单](day0_onboarding.md)**、**[四人分工与推进顺序](work_assignment.md)**、**[框架契约](FRAMEWORK_CONTRACT.md)**、**[架构看板](project_architecture.html)** |
| **提交代码 / 提 PR / 找谁审** | **[GitHub 协作规范](github_collaboration.md)**（权限矩阵、分支模型、PR 流程、CODEOWNERS、泄密红线、命令速查） |
| **收到一堆邮件 / 想知道任务什么时候派** | **[通知节流与派工节奏](notification_and_dispatch.md)**（哪些操作会触发通知、模板已改掉的通知源、draft PR 规则、派工就绪的 6 条判据与一次性派工入口） |
| **自己有事 / 别人长期不上线 / 谁来接手** | **[决策权、缺席与长期不上线](governance_and_continuity.md)**（王浩的最终决定权、代理人接手与自动改派） |
| **第一轮跑哪个 benchmark、怎么拿数据** | **[benchmark 获取指南](benchmark_acquisition.md)**、**[跑通样例选型](rampup_samples.md)** |
| **免费 API 端点（先用免费档跑通）** | [免费端点配置](../harness/config/free_endpoints.json) |
| 回路A当前代码与本地验证 | [基础实现与限制](integration/loop_a_implementation.md) |
| 回路A分区与产品预检 | [第二批实现与迁移](integration/loop_a_phase2.md) |
| DeepSeek 普通 LLM 工程测试 | [预算、真实调用与结果边界](integration/deepseek_smoke.md) |
| DeepSeek 生成、重放与良性对照 | [受控LLM实际试验](integration/deepseek_pilot.md) |
| Agent 执行与私有评分 | [Harness](../harness/README.md)、[adapter 状态契约](../harness/BENCHMARK_ADAPTER_STATUS.md) |
| 分发给多位评测者 | [分发包](../harness/docs/distribution.md) |
| 完整产品评测 | [产品模块](../federated_asr_benchmark/README.md)、[分阶段选择集](../federated_asr_benchmark/docs/staged_execution.md) |
| LLM 数据与协议 | [资产地图](../references/repo-asset-map.md)、[阶段契约](../references/phase-contracts.md)、[协议契约](../references/protocol-contract.md) |
| LLM / Agent 受控候选构建、非执行式预检、Agent 配对执行与比较 | [目标进化契约](../references/target-evolution-contract.md) |
| 下一轮闭环实验 | [研究计划](integration/research_plan.md) |
| 自动化评测与自进化 Harness 设计 | [结构图与模块方案（草案）](integration/harness_design.md) |
| 查看 V1 验收与清理 | [验证记录](integration/validation.md)、[清理记录](integration/cleanup.md) |

项目版本记录在根 `VERSION`。旧实验快照与失效入口已清理；以当前配置、preflight 和运行证据判断资格。
