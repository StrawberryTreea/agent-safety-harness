# 文档地图：整体结构与阅读路径

**这个文件解决两件事**：

1. **人**：新来的人按哪条路径读，能知道自己在哪、该读什么、别读什么。
2. **编码工具**（Codex / Cursor / Claude Code 等）：本仓库文档分散在 4 个目录、44 个文件，
没有索引时检索会命中已删除的旧副本、或把历史记录当成现行规范。本文件是**唯一索引**。

> **给编码工具的一句话**：动手前先读本文件第 2 节（阅读路径）和第 6 节（陷阱清单）。
> 本文件与 `AGENTS.md` 配套：`AGENTS.md` 是工具自动加载的约定入口，本文件是它指向的完整索引。

---

## 1. 整体结构：五层文档

分层依据是**权威性**，不是主题。同一主题的文档分散在不同层时，
冲突时按第 3 节的裁决顺序取用。

```
L0  入口层    AGENTS.md · SKILL.md · README.md · README.zh-CN.md
    │         工具自动读 / 人第一眼看到的
    ▼
L1  契约层    docs/FRAMEWORK_CONTRACT.md · protocol/* · references/*contract*.md
    │         写死的规范与 schema。改这里 = 改全项目约定
    ▼
L2  导航层    docs/DOCUMENT_MAP.md（本文件）· docs/README.md · docs/project_overview.md
    │         docs/pipeline_guide.md · docs/work_assignment.md · docs/day0_onboarding.md
    │         回答"该看哪份""这仓库长什么样"
    ▼
L3  操作层    docs/github_collaboration.md · docs/governance_and_continuity.md
    │         docs/benchmark_acquisition.md · docs/rampup_samples.md
    │         docs/configuration.md · docs/getting_started.md · CONTRIBUTING.md
    │         harness/README.md · federated_asr_benchmark/README.md 等模块文档
    │         干活时查的具体步骤
    ▼
L4  记录层    docs/integration/*（9 篇）· validation / cleanup / 草案
              历史与草案。读来了解背景，不要照着改
```

**跨层的一句话**：`protocol/` 与 `references/` 是机器可读的规范源，`docs/` 是它的可读版本与操作说明。
两者冲突时以 `protocol/` 为准（唯一例外见第 3 节）。

---

## 2. 阅读路径

### 2.1 通用路径（任何角色都先走这四步）

| 步骤 | 读什么 | 为什么是它 |
|---|---|---|
| 1 | [docs/project_overview.md](project_overview.md) | 目标、边界、功能清单、什么算做完。不读这个，后面全是盲做 |
| 2 | [docs/pipeline_guide.md](pipeline_guide.md) | pipeline 的真实结构：入口、11 个子命令、四条轨、数据怎么流 |
| 3 | [docs/FRAMEWORK_CONTRACT.md](FRAMEWORK_CONTRACT.md) | 写死的契约。**和你要改的代码对不上时，先怀疑自己，再怀疑契约** |
| 4 | [docs/work_assignment.md](work_assignment.md) + [docs/day0_onboarding.md](day0_onboarding.md) | 你的 P0 任务、第一天敲什么命令、卡住怎么办 |

### 2.2 按角色

| 角色 | 重点读 | 可跳过 |
|---|---|---|
| **王浩**（框架/仲裁） | 总纲 → 契约 → [docs/github_collaboration.md](github_collaboration.md) → [docs/governance_and_continuity.md](governance_and_continuity.md) → 看板 `docs/project_architecture.html` | L3 模块细节 |
| **刘轲**（评测引擎/统一函数层） | 契约 §2 §3 §7 → [docs/pipeline_guide.md](pipeline_guide.md) §6 → `harness/src/agent_safety_harness/` → `harness/config/` | 产品轨细节 |
| **辰浩**（产品评测/CLI） | 契约 §3 §7 → [docs/rampup_samples.md](rampup_samples.md) §3 → `federated_asr_benchmark/README.md` → [docs/staged_execution.md](../federated_asr_benchmark/docs/staged_execution.md) | L4 记录层 |
| **一迪**（LLM QA/分析） | 契约 §3 §7 → [docs/configuration.md](configuration.md) → `llm_eval/` → `scripts/result_analysis.py` → [docs/default_taxonomies.md](default_taxonomies.md) | 产品轨内部实现 |
| **伦锋**（自进化/driver） | 契约 §5 → [references/target-evolution-contract.md](../references/target-evolution-contract.md) → `evolution/` → `orchestrator/` → `harness/.../driver/` | 产品轨内部实现 |

### 2.3 按任务

| 我要做 | 读 |
|---|---|
| 复现一个外部 benchmark | [docs/rampup_samples.md](rampup_samples.md) → [docs/benchmark_acquisition.md](benchmark_acquisition.md) → [docs/pipeline_guide.md](pipeline_guide.md) §5.1–5.2 |
| 改判分方式或分类体系 | [docs/FRAMEWORK_CONTRACT.md](FRAMEWORK_CONTRACT.md) §4 → `protocol/default_taxonomies.yaml` → [docs/default_taxonomies.md](default_taxonomies.md) |
| 接入一个新评测产品 | [docs/rampup_samples.md](rampup_samples.md) §3 → `federated_asr_benchmark/README.md` → `federated_asr_benchmark/schemas/run_request.schema.json` |
| 提 PR / 提交代码 | [docs/github_collaboration.md](github_collaboration.md) → [.github/PULL_REQUEST_TEMPLATE.md](../.github/PULL_REQUEST_TEMPLATE.md) → [CONTRIBUTING.md](../CONTRIBUTING.md) |
| 想知道通知从哪来、我们靠哪个渠道沟通 | [docs/notification_and_dispatch.md](notification_and_dispatch.md) |
| 想知道任务什么时候派、怎么派 | [docs/notification_and_dispatch.md](notification_and_dispatch.md) §4 |
| 做自进化或改候选生成 | [docs/FRAMEWORK_CONTRACT.md](FRAMEWORK_CONTRACT.md) §5 → [references/target-evolution-contract.md](../references/target-evolution-contract.md) → [docs/integration/research_plan.md](integration/research_plan.md) |
| 我不在 / 别人长期不上线 | [docs/governance_and_continuity.md](governance_and_continuity.md) → `docs/roster.json` |
| 配模型 / API 端点 | [docs/configuration.md](configuration.md) → `harness/config/llm_roles.json` → `harness/config/free_endpoints.json` |

---

## 3. 权威性裁决：文档冲突时以谁为准

**按这个顺序取用，前面的覆盖后面的**：

| 顺位 | 来源 | 为什么排这么高 |
|---|---|---|
| 1 | `protocol/*.json` / `protocol/*.yaml` | 机器可读，代码直接消费，无法用文字含糊 |
| 2 | `docs/FRAMEWORK_CONTRACT.md` | 人工契约，可 diff、可 review。§3 四轨表直接对应 `protocol/evaluation_tracks.json` |
| 3 | `docs/project_architecture.html` | 看板，与契约逐条对齐（改契约必须同步改它） |
| 4 | `docs/project_overview.md` | 总纲与规划口径 |
| 5 | L3 操作层文档 | 具体步骤；与 1–4 冲突时服从上面 |
| 6 | `docs/integration/*` | **历史记录，最后看**。里面的数字与现状可能已不同步 |

**四条已确认的具体裁决**：

| 冲突点 | 结论 | 依据 |
|---|---|---|
| 分类体系：md 与 YAML 不一致 | **以 `protocol/default_taxonomies.yaml` 为准**，改任一方须同步另一方 | `docs/default_taxonomies.md` 开头原文声明 |
| 668,820 题聚合集是不是外部 benchmark | **不是**。它是自有聚合集，外部 benchmark 是 HarmBench / SafetyBench / AgentDojo / ToolEmu，各自独立 | [docs/rampup_samples.md](rampup_samples.md) §0 |
| 六个统一函数实现到哪了 | intake/build/capture/score 已接真实引擎；analyze/evolve 仍为占位 | 读 `harness/src/agent_safety_harness/driver/registrations.py` 与 [统一接口](engine_registry.md) |
| Phase6–8 是否可用 | **未实现**，只保留路由名，不产出可运行命令 | [references/phase-contracts.md](../references/phase-contracts.md) |

---

## 4. 全量文档清单

路径相对仓库根。`层` 按第 1 节；`状态` 区分"现行规范"与"历史/草案"。

### L0 入口层

| 路径 | 读它的时机 | 它管什么 |
|---|---|---|
| `AGENTS.md` | 编码工具自动加载 | 仓库级硬约束（数据治理、密钥、判分边界、验收命令） |
| `SKILL.md` | 工具以 skill 形式调用 | 仓库工作流与契约摘要 |
| `README.md` | 英文读者 | 项目入口、benchmark 选型、代码地图 |
| `README.zh-CN.md` | 中文读者 | 同上（中文版） |

### L1 契约层

| 路径 | 读它的时机 | 它管什么 |
|---|---|---|
| `docs/FRAMEWORK_CONTRACT.md` | 改任何跨模块接口前 | 分层架构、5 个标准产物、四轨契约、分类体系、双回路闸门、四人 CLI 边界、LLM 三角色 |
| `docs/default_taxonomies.md` | 讨论 8×19 / 5×14 分类时 | 两套默认体系的逐维定义与实测证据 |
| `references/protocol-contract.md` | 初始化/校验/比较评测协议时 | Phase 0 协议必需产物与判定 |
| `references/target-evolution-contract.md` | 改被评目标（Loop-B）时 | 候选构建、配对执行、比较、晋升决策的边界 |
| `references/phase-contracts.md` | 理解阶段路由时 | 各阶段实现入口与状态（Phase6–8 保留未实现） |

### L2 导航层

| 路径 | 读它的时机 | 它管什么 |
|---|---|---|
| `docs/DOCUMENT_MAP.md` | 任何时候 | 本文件 |
| `docs/README.md` | 按主题找文档 | 主题 → 文档索引 |
| `docs/project_overview.md` | 开局 | 目标、边界、60+ 功能清单与状态、三阶段规划、什么算做完 |
| `docs/pipeline_guide.md` | 不知道 pipeline 长什么样 | 总入口、11 个子命令、四条轨、数据流向、产物目录、代码在哪 |
| `docs/work_assignment.md` | 领任务 | 各角色 P0–P2、协作衔接图、推进顺序 |
| `docs/day0_onboarding.md` | 第一天 | 环境准备、各人首批命令、成功标志、卡点处理 |

### L3 操作层

| 路径 | 读它的时机 | 它管什么 |
|---|---|---|
| `docs/github_collaboration.md` | 提 PR 前 | 权限矩阵、分支模型、PR 流程、CODEOWNERS、泄密红线、命令速查 |
| `docs/notification_and_dispatch.md` | 通知从哪来、任务什么时候派 | 微信群与邮件各管什么、哪些操作会触发 GitHub 通知、模板与脚本已改掉的通知源、派工就绪的 6 条判据与一次性派工入口 |
| `docs/governance_and_continuity.md` | 决策权/缺席问题 | 王浩绝对权边界、代理人接手、长期不上线处置 |
| `docs/benchmark_acquisition.md` | 拿 benchmark 数据 | 两条获取路径、目录约定、登记与验收、官方参考入口 |
| `docs/rampup_samples.md` | 定第一轮跑什么 | HarmBench / SafetyBench / AgentDojo / ToolEmu / Codex CLI / Claude Code 的参数 |
| `docs/configuration.md` | 配模型与端点 | LLM QA、内部 Agent、完整产品三类配置 |
| `docs/engine_registry.md` | 接入统一引擎、复跑 Issue #3 | 模态契约、四个真实函数、固定来源与 Docker 公开捕获/私有评分、交接限制 |
| `docs/getting_started.md` | 从零安装 | 安装、整仓验收、单跑示例、排错 |
| `CONTRIBUTING.md` | 扩展代码前 | 从哪一层扩展、测试与提交要求 |
| `harness/README.md` | 改 Agent 执行轨 | 内部 TaskSpec、轨迹捕获、私有评分 |
| `harness/BENCHMARK_ADAPTER_STATUS.md` | 加 adapter | 四种状态与正式准入条件 |
| `harness/docs/distribution.md` | 分发协作者 | 打包器用法 |
| `harness/smoke/README.md` | 跑冒烟 | 合成 smoke fixtures |
| `federated_asr_benchmark/README.md` | 改产品轨 | 用完整产品 CLI 替换 Agent runner，保留原生 scorer |
| `federated_asr_benchmark/docs/staged_execution.md` | 选产品评测集 | 固定选择集与放行门槛 |
| `references/repo-asset-map.md` | 找文件位置 | 仓库资产 → 路径映射 |
| `.github/PULL_REQUEST_TEMPLATE.md` | 提 PR 时 | PR 描述与泄密自查模板 |
| `assets/templates/evaluation_protocol_card.md` | 建评测协议卡 | 协议卡模板 |
| `assets/templates/release_checklist.md` | 发布前 | 人工发布检查清单 |

### L4 记录层（历史与草案，不作为现行口径）

| 路径 | 是什么 | 什么时候看 |
|---|---|---|
| `docs/integration/architecture.md` | V1 架构与原始概念图映射 | 想理解模块职责与评分边界的来龙去脉 |
| `docs/integration/harness_design.md` | Harness 设计**草案** | 想看目标架构；注意文中完整闭环尚未完成 |
| `docs/integration/loop_a_implementation.md` | 回路A本地基础实现与限制 | 改回路A前 |
| `docs/integration/loop_a_phase2.md` | 回路A分区约束与执行预检 | 改回路A前（比上一篇新） |
| `docs/integration/deepseek_smoke.md` | DeepSeek 普通 LLM 工程测试记录 | 查真实 API 调用怎么记账 |
| `docs/integration/deepseek_pilot.md` | 受控 LLM 生成/重放/良性对照试验记录 | 查受控试验的预算与口径 |
| `docs/integration/research_plan.md` | 下一轮闭环实验的研究问题 | 规划后续实验 |
| `docs/integration/validation.md` | V1 验收记录 | 查某功能**曾经**验证到什么程度 |
| `docs/integration/cleanup.md` | V1 整理与删除记录 | 查为什么某个旧文件不在了 |
| `protocol/evaluation_protocol_card.md` | 仓库内协议卡实例（fixture） | 对照模板看填写方式 |

### 同属文档体系的非 Markdown

| 路径 | 是什么 |
|---|---|
| `docs/project_architecture.html` | 架构与分工看板（与框架契约逐条对齐，需在浏览器打开） |
| `docs/roster.json` | 成员、账号、负责目录、代理人、阈值——机器可读的唯一权威 |

---

## 5. 编码工具怎么用这份地图

### 5.1 检索范围（必须限制）

**只检索受 Git 跟踪的文件**，不要全盘 grep。仓库里存在 `.tmp/` 目录，
内含 616 MB 的旧仓库副本（`collaborator-clean-20261013` 等），其中包含**已删除**的模块
（如 `eval_mvp_V2/`）和**过时的项目名**（"LLM and Agent Safety Evaluation Pipeline"）。
不排除就会得出"这个模块存在""项目叫这个名字"的错误结论。

```bash
git ls-files '*.md' '*.py' '*.json' '*.yaml'   # 受管文件全集
```

`.venv/`、`runs/`、`data/`、`__pycache__/` 同样应排除。

### 5.2 动手前的最小信息集

| 你要改 | 先读 | 再看 |
|---|---|---|
| `harness/src/agent_safety_harness/` | 契约 §2 §3 §7 | `harness/README.md` |
| `harness/config/` | `harness/config/llm_roles.json` 头部注释 | `docs/configuration.md` |
| `llm_eval/` | `protocol/evaluation_tracks.json` 的 `llm_qa` | [docs/default_taxonomies.md](default_taxonomies.md) |
| `federated_asr_benchmark/` | `federated_asr_benchmark/schemas/run_request.schema.json` | `federated_asr_benchmark/README.md` |
| `evolution/` `orchestrator/` | [references/target-evolution-contract.md](../references/target-evolution-contract.md) | 契约 §5 |
| `protocol/` `references/` | 契约 §2 | —（这些是规范源，改动需王浩评审） |
| `docs/` | 本文件 | [docs/README.md](README.md) |

### 5.3 事实以代码为准的几处

文档可能滞后，以下几处**读源码**而不是读文档：

| 事实 | 权威位置 |
|---|---|
| 六个统一函数实现状态 | `harness/src/agent_safety_harness/driver/registrations.py`（4 个真实引擎入口、2 个待接入） |
| pipeline 实际子命令 | `python pipeline.py --help`（当前 11 个） |
| `llm_eval` 执行器参数 | `llm_eval/multi_model_rule_eval_prompt_only.py`（**只有 `--config` 一个参数**） |
| `run_product` 参数 | `federated_asr_benchmark/tools/run_product.py`（`--request` 与 `--case-id` 必填） |
| 已登记的 benchmark | `harness/config/benchmarks.json`（当前 3 条内置 fixture 条目） |
| 分类体系维度 | `protocol/default_taxonomies.yaml` |

---

## 6. 陷阱清单（编码工具与新人都适用）

| 陷阱 | 后果 | 正确做法 |
|---|---|---|
| 全盘 grep 命中 `.tmp/` 旧副本 | 得出"模块存在""项目名如此"的错误结论 | 用 `git ls-files` 限定范围 |
| 用系统 `python` 跑验收 | 缺 `jsonschema`，产生**假失败** | 用 `./.venv/Scripts/python.exe`（Windows） |
| 把 `docs/integration/` 当现行规范 | 引用已过期的数字与设计 | 走第 3 节裁决顺序 |
| 直接改 `protocol/` 下的文件 | 越过契约仲裁 | 需王浩知悉（CI 门禁 `契约变更需王浩知悉`） |
| 在文件里写 API key | 泄密（CI 有扫描门禁） | 只写 `api_key_env` 环境变量名 |
| 覆盖非空输出目录 | 毁掉已有运行证据 | 需显式 overwrite 选项 |
| 把 mock / 样例结果当真实成绩 | 结论失真 | 以真实运行证据 + 官方 scorer 为准 |
| 直接推 `main` | 被保护规则拒绝 | 走 PR；分支名 `feat/`、`fix/`、`docs/`、`chore/` |

---

## 7. 本文件如何维护

| 情况 | 动作 |
|---|---|
| 新增/删除/重命名任何 `.md` | 更新第 4 节清单，并跑 `python scripts/check_doc_map.py` |
| 新增契约类文档 | 同时更新第 1 节的层级归属与第 3 节裁决顺序 |
| 某文档从"草案"升级为"现行" | 改第 4 节的状态列，并在第 3 节写明它与谁冲突时以谁为准 |
| 发现两处口径打架 | **不要各自保留**，按第 3 节裁决顺序删掉过时的一处，并在第 3 节表格留记录 |

CI 会跑 `scripts/check_doc_map.py`：清单里的路径必须存在，
且每个受 Git 跟踪的 `.md` 都必须出现在清单里——**漏登记会让 PR 直接失败**。

---

## 8. 相关文档

- 契约：[docs/FRAMEWORK_CONTRACT.md](FRAMEWORK_CONTRACT.md) · 看板 `docs/project_architecture.html`
- 导航：[docs/README.md](README.md) · 总纲 [docs/project_overview.md](project_overview.md)
- Pipeline：[docs/pipeline_guide.md](pipeline_guide.md)
- 分工：[docs/work_assignment.md](work_assignment.md) · 上手 [docs/day0_onboarding.md](day0_onboarding.md)
- 协作：[docs/github_collaboration.md](github_collaboration.md) · 连续性 [docs/governance_and_continuity.md](governance_and_continuity.md)
- 通知与派工：[docs/notification_and_dispatch.md](notification_and_dispatch.md)
- 工具入口：`AGENTS.md` · `SKILL.md`
