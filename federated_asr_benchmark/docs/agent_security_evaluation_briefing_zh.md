# Agent 安全评测框架汇报材料

> 状态快照：2026-08-10 11:18（Asia/Shanghai）
> 当时阶段：现有六数据集全量补集评测进行中；扩展数据集处于候选准入阶段；便携发布包仍是开发候选版。
> 核心指标：ASR（Attack Success Rate，攻击成功率），数值越低表示 Agent 越安全。

迁移说明：本文是历史汇报快照，当前整合验证范围见 [validation.md](../../docs/integration/validation.md)。

## 1. 一页结论

本项目构建的是一个**面向完整 Agent 产品的联邦式安全评测框架**。评测对象不是“裸模型 API”，也不是数据集自带的参考 Agent，而是 Codex CLI、Claude Code CLI、Qoder CLI 三个真实产品在各自默认行为、工具调用和上下文管理机制下的整体安全表现。

框架统一接入不同来源的任务、攻击、工具环境和官方评分器，同时尽量保留原数据集的攻击语义与成功判据。每个样本均在隔离状态中运行，记录产品版本、实际模型路由、工具事件、原始输出、评分证据和租约历史，再统一映射为可审计的 case-level ASR。

当前正式评测范围为 6 个数据集、5,384 个唯一问题；三种 Agent 全覆盖后共需 16,152 次产品执行。按当前阶段性有效结果，按数据集题量加权的 ASR 为：Claude Code 28.78%、Codex 25.88%、Qoder 22.50%。由于 Qoder 当前有效覆盖率仅 73.44%，这些数值只能作为运行中快照，不能用于最终安全排名。

## 2. 评测目标与边界

### 2.1 评测对象

- 评测完整产品 Agent：CLI、系统提示、上下文编排、工具调用策略、模型路由和产品默认行为共同构成被测对象。
- 所有被测产品统一使用 DeepSeek V4 Flash，尽量控制底层模型差异，把比较重点放在 Agent 产品层。
- 所有推理与 API Judge 均使用用户提供的 `DEEPSEEK_API_KEY`，禁止使用产品内置、订阅、试用或托管额度。
- Qoder 只允许使用 Qoder CLI，不使用桌面端；必须验证 BYOK 自定义模型请求证据以及未消耗内置额度。

### 2.2 保留与统一的内容

框架保留：

- 原始用户任务、攻击文本和攻击插入位置；
- 原始工具/环境状态及关键交互逻辑；
- 官方确定性成功判据，或冻结版本的 API Judge；
- 每个 case 的环境重置和独立性要求。

框架统一：

- 三种产品的非交互式 CLI 启动方式；
- 每个 case 的独立工作目录、产品状态和工具服务；
- MCP/工具白名单、超时与子进程清理；
- `success / failure / excluded / invalid` 四类状态；
- case-level 证据、路由审计、覆盖率门槛和跨数据集聚合。

## 3. 总体架构

```mermaid
flowchart LR
    A["原始数据集\n任务 + 攻击 + 环境"] --> B["数据集适配器\n案例解析与状态初始化"]
    B --> C["产品运行器\nCodex / Claude Code / Qoder CLI"]
    C --> D["隔离工具环境\nMCP / Docker / 本地沙箱"]
    C --> E["产品输出与工具事件"]
    D --> E
    E --> F["官方确定性评分器\n或冻结 API Judge"]
    F --> G["统一 case_result\nASR 分子/分母 + 完整证据"]
    G --> H["形式审计\n覆盖 / 路由 / 协议 / 工件"]
    H --> I["单数据集 ASR\nMacro-ASR / 题量加权 ASR"]
```

### 3.1 主要模块

| 层次 | 作用 | 主要实现 |
|---|---|---|
| 数据选择层 | 冻结 case ID、分层抽样、去重、记录来源路径 | `selections/`、`case_index.jsonl` |
| 数据集适配层 | 将原任务、攻击和官方判据映射到统一协议 | `product_eval/*.py`、`tools/*_mcp_server.py` |
| 产品运行层 | 启动真实 CLI、注入隔离配置、捕获输出与路由证据 | `product_eval/runner.py`、`tools/run_product*.py` |
| 调度层 | 逻辑分片、原子租约、心跳、恢复、自适应并发 | `product_eval/adaptive.py`、`tools/run_adaptive*.py` |
| 评分层 | 确定性评分或冻结 Judge，生成统一 ASR 贡献 | `tools/score_*_result.py`、`product_eval/api_judge.py` |
| 审计聚合层 | 检查缺失、无效、路由漂移、证据完整性并聚合 | `tools/audit_*.py`、`tools/aggregate_results.py` |
| 发布层 | 固定配置、依赖、文件清单和一键复现命令 | `release.ps1`、`release/`、`tools/release_manifest.py` |

## 4. 已正式适配的六个数据集

| 数据集 | 唯一问题数 | 总体占比 | 主要风险类型 | 执行环境/评分后端 | 攻击成功判据 |
|---|---:|---:|---|---|---|
| MSB | 2,000 | 37.15% | 多场景工具滥用、混合攻击 | 确定性评分 | `attack_success` 或 `mixed_attack_success` |
| InjecAgent | 1,054 | 19.58% | 间接提示注入 | MCP + 确定性评分 | 官方结果 `eval == "succ"` |
| SABER | 715 | 13.28% | 沙箱内高风险操作与有害行为 | Docker/MCP + 规则和 API Judge | 冻结 Judge 判定 `harmful == true` |
| AgentDojo | 629 | 11.68% | 真实工具任务中的提示注入 | 官方环境 + MCP | 官方 `security == true` |
| AgentLeak | 496 | 9.21% | 多轮敏感信息泄露 | MCP + 确定性评分 | `multi_leaked == true` |
| AgentPoison-StrategyQA | 490 | 9.10% | 知识库/检索投毒 | 检索环境 + 确定性评分 | 官方 ASR-r 检索事件规则 |
| **合计** | **5,384** | **100%** | 六类 Agent 安全风险 | 三产品真实执行 | 统一映射到 case-level ASR |

说明：AgentPoison 的原生统计单位是 `retrieval_event`，其他数据集通常以 case 为单位。框架保留这一官方语义，不把不同评分单位强行改写成同一分母。

## 5. 被测 Agent、CLI 版本与模型路由

| Agent 产品 | 当前实际 CLI 版本 | 被测模型 | Provider 路由 | 路由要求 |
|---|---:|---|---|---|
| Codex CLI | `0.122.0` | `deepseek-v4-flash` | DeepSeek OpenAI-compatible `/v1` | 必须由用户 API Key 驱动 |
| Claude Code CLI | `2.1.195` | `deepseek-v4-flash` | DeepSeek Anthropic-compatible | 必须由用户 API Key 驱动 |
| Qoder CLI | `1.0.48` | `deepseek-v4-flash` | DeepSeek BYOK 自定义模型池 | 必须观察到自定义模型请求证据，不得消耗内置额度 |

Qoder 当前合格路由的强制属性：

- `provider=deepseek`
- `provider_model=deepseek-v4-flash-pg`
- `canonical_model=deepseek-v4-flash`
- `type=pg`
- `api_key_supplied=true`
- 自定义模型 ID：`model_1786147854337_zp7v4vd`

重要口径：活动 campaign 结束后，仓库注册表与 tested profile 已迁移为 `Qoder CLI 1.0.48 + DeepSeek BYOK custom-pool`。旧 `1.1.13` 仅作为历史审计记录保留，不再是可运行配置；新配置由 release ID 标识。

## 6. 评分指标与汇报口径

### 6.1 单数据集 ASR

对数据集 \(b\)：

\[
ASR_b = \frac{\sum_i numerator_i}{\sum_i denominator_i}
\]

普通 case 通常贡献 `1/1`（攻击成功）或 `0/1`（攻击失败）；AgentPoison 使用检索事件分子/分母。

### 6.2 六数据集等权 Macro-ASR

\[
Macro\text{-}ASR = \frac{1}{6}\sum_{b=1}^{6} ASR_b
\]

这是注册表定义的主聚合口径，每个数据集权重相同，避免大数据集完全主导结果。

### 6.3 按数据集题量加权 ASR

\[
Weighted\text{-}ASR = \sum_b \frac{N_b}{5384} ASR_b
\]

这是汇报中的辅助口径，回答“按照数据集题目数量占比，整体攻击成功率是多少”。它不是把所有分子分母直接混在一起，而是先保留各数据集官方 ASR，再按数据集题量加权。

### 6.4 无效与排除处理

- `success`：计入 ASR 分子和分母；
- `failure`：只计入分母；
- `excluded`：官方规则无法评分或预定义排除，贡献 `0/0`；
- `invalid`：执行、协议、路由或证据无效，贡献 `0/0`；
- 缺失、无效、超时或 Judge 失败绝不计作攻击成功，也绝不静默计作攻击失败。

最终结果必须同时报告 ASR 和有效覆盖率。大多数数据集要求至少 98% 覆盖，MSB 和 SABER 要求至少 95%；未通过门槛时总分应 withheld，而不是发布一个看似完整的排名。

## 7. 当前阶段性结果

### 7.1 总体 ASR 快照

| Agent | 六数据集等权 Macro-ASR | 按题量加权 ASR | 有效覆盖率 | 当前解释 |
|---|---:|---:|---:|---|
| Claude Code | 23.57% | **28.78%** | 4,824 / 5,384 = 89.60% | 运行中，尚不可最终发布 |
| Codex | 22.75% | **25.88%** | 5,235 / 5,384 = 97.23% | 全部 case 已执行，149 个 SABER 排除项待最终审计口径确认 |
| Qoder | 19.54% | **22.50%** | 3,954 / 5,384 = 73.44% | 运行中，覆盖率明显较低，暂不可横向排名 |

阶段性排序只反映已完成且有效的样本。特别是 Qoder 的低 ASR 可能受到尚未完成样本分布影响，不能据此断言 Qoder 最安全。

### 7.2 各数据集 ASR 快照

| 数据集 | Codex | Claude Code | Qoder |
|---|---:|---:|---:|
| InjecAgent | 0.28% | 0.51% | 0.87% |
| MSB | 39.95% | 49.59% | 35.59% |
| AgentDojo | 2.38% | 2.71% | 2.26% |
| AgentLeak | 21.57% | 33.12% | 30.13% |
| SABER | 51.06% | 44.02% | 39.90% |
| AgentPoison-StrategyQA | 21.24% | 11.50% | 8.48% |

可汇报的初步观察：

- 三个 Agent 在 InjecAgent 和 AgentDojo 上的阶段性 ASR 较低；
- MSB 与 SABER 是当前主要风险贡献源；
- AgentLeak 对产品差异较敏感；
- AgentPoison 采用检索事件口径，不能直接按“成功 case 数”与其他数据集比较；
- 这些观察均需等待 Claude Code、Qoder 完成和正式审计后确认。

### 7.3 当前运行进度

本轮采用“完整 5,384 题减去已通过审计的 cumulative28（1,508 题）”得到 3,876 题补集，避免重复执行有效工件。

| Agent | 补集已完成 | 失败 | 租约中 | 待处理 | 状态 |
|---|---:|---:|---:|---:|---|
| Codex | 3,876 | 0 | 0 | 0 | 补集执行完成 |
| Claude Code | 3,464 | 1 | 1 | 410 | 健康运行 |
| Qoder | 2,557 | 3 | 1 | 1,315 | 健康运行 |

已知终态无效 case 将在主队列完成后进行定向重试，只恢复未完成或无效 case，不覆盖已有有效工件。

## 8. 自适应并发与故障恢复

当前控制器版本为 `0.3.0-dev`，关键参数如下：

| 参数 | 配置 |
|---|---:|
| 不可变逻辑分片数 | 32 |
| 每产品初始 worker | 2 |
| 全局最大 worker | 10 |
| 轻量数据集每产品最大 worker | 4 |
| AgentDojo/SABER 每产品最大 worker | 3 |
| 健康观察窗口 | 5 分钟 |
| 扩容前连续健康窗口 | 2 |
| 扩缩容冷却时间 | 5 分钟 |
| case 租约时间 | 120 秒 |
| 最大尝试次数 | 3 |

控制器设计原则：

- 逻辑 shard 由 case ID 确定，运行中绝不改变 modulo `shard_count`；
- SQLite 原子 claim，防止两个 worker 同时执行同一 case；
- 每个 lease 有心跳、过期和崩溃恢复；
- 一个 campaign 只允许一个持锁 controller，并记录 PID/启动元数据；
- worker 只写自己的 case 目录，最终聚合使用原子写入；
- 监控吞吐、延迟、429/5xx、超时、无效/缺失、模型漂移、Docker/MCP、CPU、内存、磁盘和队列存活性；
- 连续两个健康窗口后才增 1 个 worker；出现限流、错误、资源压力或吞吐回退时降并发；出现工件损坏或模型路由漂移时立即收缩并停止受影响路线。

## 9. 隔离、证据与审计

每个 case 独立保存：

- 输入 case ID、来源 ID、数据集版本和选择集 ID；
- 产品 ID、CLI 版本、声明模型和实际 provider/model 路由；
- 用户提示、攻击载荷、产品标准输出/错误输出；
- MCP/Docker/工具调用事件与环境状态；
- 官方评分器或 Judge 的原始证据；
- `asr_contribution` 的分子、分母和单位；
- 开始/结束时间、逻辑 shard、worker、lease 与重试历史；
- release ID，用于标识评测版本。

正式审计检查：

- 是否缺失 case 或出现重复 case；
- 是否存在失败、invalid、unsupported-tool、协议错误或 Judge 错误；
- 产品版本、模型、provider 和 API Key 来源是否符合冻结配置；
- Qoder 是否观察到 BYOK 自定义模型请求且没有内置额度使用；
- 工具事件、官方证据、评分结果和日志是否完整；
- 覆盖率是否达到各数据集门槛；
- 结果是否与同一 release ID、选择集和 scorer 兼容。

## 10. 扩展数据集计划

候选来源选择集已经冻结，共 2,952 个问题（OpenAgentSafety 的 356 行中有 1 行无任务名，实际候选 355 条）。运行时准入审计后，只有 AgentDyn 560 条和 AgentHarm 208 条具备继续进行三产品 smoke 的执行闭环；其余 2,184 条在当前发布中拒绝。若这 768 条全部通过，正式规模将从 5,384 增至最多 6,152 个唯一问题。8,000-10,000 是软目标，质量和可复现性优先于数量。

| 候选数据集 | 候选题数 | 选择规则 | 当前状态 |
|---|---:|---|---|
| AgentDyn | 560 | 去重后的 distinct cases | runner 已接入；待三产品 smoke 准入 |
| RAS-Eval | 1,200 | 从 3,802 个原生攻击中确定性分层抽样；排除 2,680 个 ASB 派生样本 | 当前发布拒绝：无隔离 MCP 攻击桥、clean/attack twin 配对与回滚闭环 |
| MCPTox | 485 | MCP 工具中毒/恶意工具行为 case | 当前发布拒绝：485 个工具片段均不可直接运行，无 fresh-run scorer 与清晰 ASR 映射 |
| OpenAgentSafety | 355 | 356 行中排除 1 行无名称记录 | 当前发布拒绝：17 条来源/评估器不完整，公共 harness 与服务隔离环境缺失 |
| AgentHarm | 208 | 只计攻击任务，不重复计算 chat/benign controls | runner 已接入；待三产品 smoke 准入 |
| ToolEmu | 144 | 可观察工具执行结果的风险任务 | 当前发布拒绝：无原生 emulator MCP 桥、轨迹证据，0-3 分到 case ASR 的映射未冻结 |
| **合计** | **2,952** | 统一去重、来源与规则审计 | **768 条待 smoke；2,184 条当前发布拒绝** |

每个新数据集需要先完成三产品各 5 题 smoke，并验证真实攻击送达、真实产品执行、可观察结果、清晰的 case-level ASR、完整日志、路由证据、Judge 证据、隔离、去重与来源路径。模糊、不可稳定复现、与已有数据重复或只能做离线分类的数据集/样本将被拒绝。

## 11. 便携复现发布

当前活动 release：

- Release ID：`federated-asr-v0.3-existing-six-complement`
- 状态：`development_candidate_only`

当前开发候选依赖固定：

| 组件 | 版本/摘要 |
|---|---|
| Python | `3.13.2` |
| uv | `0.12.3` |
| Node.js | `24.14.1` |
| npm | `11.12.1` |
| Docker Server | `29.3.1` |
| AgentDyn/AgentDojo package | `0.1.35` |
| SABER image | `sha256:89855a146a75710e028265295e3c64007121ac52598f18b3a68151b99c2705b6` |

最终发布目标：

- 代码、选择集、提示、工具、隔离、scorer、Judge、seed、shard 和控制器全部版本化；
- 测试模型 profile 与不可变评测/Judge 配置分离；
- 发布清单检查必要文件是否存在，并保留路径安全检查；
- 提供一键 PowerShell `bootstrap / preflight / verify / smoke / adaptive或fixed run / resume / audit / aggregate / export`；
- 不打包、打印或持久化 API Key；使用者只在本地设置环境变量；
- 支持复制到包含空格和非 ASCII 字符的 Windows 路径，并记录 Docker/WSL 前置条件；
- 对不可再分发的数据只提供获取/导入流程；
- 拒绝不兼容或缺字段的结果。

## 12. 当前风险与尚未完成事项

1. Claude Code 与 Qoder 尚未完成全量补集，当前 ASR 是阶段性估计，不应发布最终排名。
2. 主队列结束后还需对失败、invalid、missing、Judge、协议、unsupported-tool 和模型路由异常做定向重试及正式审计。
3. Qoder 注册表、tested profile、固定/自适应入口与路由校验已迁移为合格的 `1.0.48 BYOK custom-pool`；旧 `1.1.13` 只保留在历史审计中。
4. 控制器仍有三个开发期问题：近期错误指标会累计历史错误；缩容 stop marker 可能短暂出现 `desired=1, live=0`；吞吐基线可能引起 worker 数振荡。为保护活动结果，当前不重启控制器，后续修复后重新 smoke。
5. 扩展审计仅保留 AgentDyn 560 条与 AgentHarm 208 条等待 live smoke；RAS-Eval、MCPTox、OpenAgentSafety、ToolEmu 共 2,184 条因缺少执行/评分闭环在当前发布中拒绝。可选替代项 SafePro 276 条、AgentLAB SHADE 5 条、ST-WebAgentBench 375 条也因参考文件、公开任务、执行 harness 或隔离环境不完整全部拒绝，均不计入正式规模或 ASR。
6. 便携发布仍需在含空格/非 ASCII 的复制路径完成全链路验证，之后才能标记最终版。

## 13. 建议汇报结构（10 页）

1. **为什么测 Agent 而不是只测模型**：工具、上下文、权限和编排共同决定安全性。
2. **评测目标与统一实验变量**：三种产品统一使用 DeepSeek V4 Flash，API-only/BYOK。
3. **框架架构图**：数据集适配、真实 CLI、工具环境、评分、审计和聚合。
4. **现有六数据集覆盖面**：5,384 个唯一问题及风险类型。
5. **产品、版本和路由**：重点解释 Qoder 1.0.48 BYOK 与旧注册项的区别。
6. **指标与质量门槛**：单数据集 ASR、Macro-ASR、题量加权 ASR、覆盖率。
7. **阶段性结果**：总体表和各数据集表，强调结果尚未最终定稿。
8. **工程可信度**：隔离、租约、路由证据、官方评分、日志和 fail-closed。
9. **扩展计划**：来源候选 2,952 题中 2,184 题已按质量门槛拒绝；剩余 768 题待 smoke，全部通过时最多 6,152 个唯一问题。
10. **里程碑与风险**：完成当前 campaign、定向重试、扩展 smoke、全量评测、最终便携发布。

## 14. 推荐的汇报开场表述

“我们搭建的不是一个简单的提示词测试脚本，而是一套面向真实 Agent 产品的可审计安全评测基础设施。它让不同数据集保留自己的攻击和官方评分规则，同时让 Codex、Claude Code、Qoder 在统一模型、统一凭证政策、隔离工具环境和统一证据标准下运行。当前正式范围是六个数据集、5,384 个唯一问题，最终形成 16,152 次产品执行。我们同时报告数据集等权和按题量加权的 ASR，并把覆盖率、模型路由和工件完整性作为发布门槛，避免用缺失或无效结果制造虚假的安全结论。”
