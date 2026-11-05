# 项目总纲：目标、功能清单与推进规划

> 这份是**入口文档**，回答三个问题：
> ① 这个项目是干什么的、边界在哪；
> ② 现在**具体有哪些功能**、每个功能是干什么的、状态如何；
> ③ 接下来按什么**规划**推进、什么算做完。
>
> 其他人写的都是局部视角（怎么跑、怎么分工、怎么协作），这份是全局视角。
>
> 配套：[Pipeline 全景说明](pipeline_guide.md)（怎么跑）· [框架契约](FRAMEWORK_CONTRACT.md)（接口与红线）· [分工与推进顺序](work_assignment.md)（各人做什么）· [GitHub 协作规范](github_collaboration.md)（怎么协作）

---

## 第一部分：项目是什么

### 1. 一句话

**一个自动化评测 LLM 与 Agent 安全性的 harness**：给定评测数据，自动跑目标、判分、分析失败，
并把失败样本转成下一轮可用的评测材料。

### 2. 解决什么问题

现在的状况是：评一个模型或 Agent 的安全性，评测者要自己拼脚本、自己写判分、自己整理结果、
自己想办法造下一批数据。每换一个 benchmark、换一个模型就要重写一遍。

这个项目把这些做成**一套可复用的管线**：

| 痛点 | 这里的做法 |
|---|---|
| 每个 benchmark 一套跑法 | 统一入口 `pipeline.py`，4 条轨各自适配但调用方式一致 |
| 判分标准容易泄密给被测对象 | **公开执行 / 私有评分分离**：capture 阶段只产轨迹，评分规则在独立阶段才注入 |
| 失败样本用完就没了 | 失败 → 分析四件套 → 自进化候选 → 下一轮评测数据 |
| 换模型/换产品成本高 | 统一数据契约（TaskSpec / TraceEvent / ResultSpec）+ 注册式接入 |
| 结果口径混乱 | 每条轨的指标、边界写死在 `protocol/evaluation_tracks.json`，API 错误排除在有效分母外 |

### 3. 边界（不做什么）

| 不做 | 原因 |
|---|---|
| 不做「通用实验调度平台」 | 控制器（调度/预算/恢复）目前是设计模块，无执行器 |
| 不承诺全自动自进化 | 自进化是**带闸门和人工审批的研究赌注**，不承诺自动产出更优数据 |
| 不做「模型排行榜」 | 测量单元分轨独立：模型分、Agent 执行分、产品 ASR 不混排 |
| 不替代 benchmark 官方 scorer | 外部 benchmark 复现用官方口径；自有 19 维体系是另一件事，必须分开陈述 |
| 不接管被测系统内部 | 完整产品轨用真实入口驱动已发布产品或学术自研 Agent，不改被测系统 |

### 4. 人员结构

| 人 | 角色 | 主责模块 |
|---|---|---|
| 王浩 | 总体框架与设计 | 契约、协议、文档、轨道注册表、pipeline 总入口 |
| 刘轲 | 评测引擎组（底座） | `harness/` 统一函数层、schema、sandbox、adapters |
| 辰浩 | 产品评测组 | `federated_asr_benchmark/` 真实产品接入、preflight、ASR |
| 一迪 | LLM QA + 统一分析 | `llm_eval/`、`scripts/result_analysis.py` |
| 伦锋 | 自进化组 + driver 编排 | `evolution/`、`orchestrator/`、`driver/` |

详细分工见 [work_assignment.md](work_assignment.md)。

---

## 第二部分：功能清单

**图例**：✅ 已实现（有代码入口且能跑） · 🟡 部分实现（有协议/配置，关键环节缺失） · ⬜ 未实现（仅设计）

### 2.1 四条评测轨

**这是整个项目的骨架。四轨并行，互不阻塞，各自统计自己的指标。**

| 轨 | 评什么 | 执行器 | 指标 | 状态 |
|---|---|---|---|---|
| `llm_qa` | LLM 直接问答的安全性 | `llm_eval/multi_model_rule_eval_prompt_only.py` | `rule_eval_pass_rate` | ✅ |
| `agent_execution` | 内部 Agent 在受控环境里的执行安全 | `harness/ash.py` | `native_safety_pass_rate` | ✅ |
| `agent_product` | 完整产品的原生 ASR | `federated_asr_benchmark/tools/run_product.py` | `official_asr` | ✅ |
| `trace_judge` | 离线轨迹评审（**不计入 Agent 执行指标**） | `harness/ash.py` | `native_trace_judge_pass_rate` | ✅ |

轨道注册表：`protocol/evaluation_tracks.json`（**权威来源**，含每轨的边界说明）。

### 2.2 功能明细

#### A. 执行与评测

| # | 功能 | 入口 | 说明 | 状态 |
|---|---|---|---|---|
| A1 | LLM 规则评测 | `llm_eval/multi_model_rule_eval_prompt_only.py --config <yaml>` | 多模型批量规则判分；**只有 `--config` 一个参数**，数据路径写在 config 的 `evaluator_config.input_jsonl` | ✅ |
| A2 | Agent 基准执行 | `harness/ash.py run` | 跑一个 agent execution benchmark | ✅ |
| A3 | 公开执行（只产轨迹） | `harness/ash.py capture` | **capture 阶段 `evaluation` 字段必须为空**，评分规则绝不进 capture | ✅ |
| A4 | 私有评分 | `harness/ash.py score` | 独立阶段注入私有规则，产 ResultSpec | ✅ |
| A5 | 轨迹评审 | `harness/ash.py judge` | 离线 trace judge 任务 | ✅ |
| A6 | 报告生成 | `harness/ash.py report` | 由已有 run 生成 markdown 报告 | ✅ |
| A7 | 完整产品执行 | `federated_asr_benchmark/tools/run_product.py` | 真实产品 CLI + 原生环境 + 原生 scorer | ✅ |
| A8 | 产品 preflight | `federated_asr_benchmark/tools/run_product.py --request <r> --case-id <id> --dry-run`（独立工具：`tools/preflight.py --data-root <目录>`） | **preflight 不过不许跑**；`--dry-run` 只出计划不执行 | ✅ |
| A9 | 批量跑产品 | `federated_asr_benchmark/tools/run_product_batch.py --request <r>` | 多产品/多 benchmark 批跑 | ✅ |
| A10 | 结果聚合 | `federated_asr_benchmark/tools/aggregate_results.py` | 汇总多产品 ASR | ✅ |
| A11 | 来源校验 | `federated_asr_benchmark/tools/verify_sources.py` | 校验数据来源与哈希 | ✅ |
| A12 | MCP 服务端（按 benchmark） | `tools/*_mcp_server.py`（agentdojo / agentharm / agentleak / agentpoison / injecagent / msb / saber） | 给 Agent 提供 MCP 工具入口 | ✅ |

#### B. 数据接入与规范化

| # | 功能 | 入口 | 说明 | 状态 |
|---|---|---|---|---|
| B1 | Benchmark 登记（intake） | `scripts/source_intake.py --benchmark-root <目录> --protocol-dir protocol --output-dir work/phase1` | **唯一正规接入路径**，产出 catalog + manifest + sha256 | ✅ |
| B2 | 字段映射转换 | 同上，加 `--intake-config <yaml>` | 跑完后填 `manifests/pending/*.pending.yaml` 的 `field_mapping` 再重跑 | ✅ |
| B3 | canonical 校验 | `scripts/canonical_validate.py <路径>` | 校验是否符合 canonical schema | ✅ |
| B4 | 数据集规范化 | `scripts/canonicalize_datasets.py` | 原始数据 → canonical 格式 | ✅ |
| B5 | AgentHarm 导入 | `harness/ash.py import agentharm` | 原始 JSON/JSONL → TaskSpec JSONL | ✅ |
| B6 | 映射式导入 | `harness/ash.py import mapped` | JSON/JSONL/CSV 经字段映射预设导入 | ✅ |
| B7 | 已注册 benchmark 列表 | `harness/ash.py list` | 列出内置 benchmark、agent、已配置模型 | ✅ |

#### C. 分析与失败提取

| # | 功能 | 入口 | 说明 | 状态 |
|---|---|---|---|---|
| C1 | 结果分析（四件套） | `pipeline.py analyze --track <轨> --results <文件...> --out <目录>` | 产出 `observations` / `failures` / `invalids` / `candidate_plans` 四个 JSONL + manifest + summary | ✅ |
| C2 | 失败身份对齐 | 同上 | 身份字段 = `track, benchmark, run_id, subject, case_id, model, product_version` | ✅ |
| C3 | 候选计划（不自动接纳） | 同上 | `candidate_plans` 只是**候选**，需过闸门才进数据集 | ✅ |
| C4 | 协议管理 | `scripts/protocol_manager.py` | 读写 protocol 目录下的协议定义 | ✅ |

#### D. 自进化（研究赌注，不是交付承诺）

| # | 功能 | 入口 | 说明 | 状态 |
|---|---|---|---|---|
| D1 | 回路 A 就绪检查 | `pipeline.py loop-a plan` | **会诚实报出还差什么**，当前报 5 个 blocker | ✅ |
| D2 | 回路 A 动作集 | `pipeline.py loop-a {qualify,partitions,metrics,smoke,cycle,compare,run}` | 8 个子动作 | 🟡 |
| D3 | 候选构建（受限） | `pipeline.py target-build` | 由 recipe 生成受限 JSON/YAML prompt 或 policy | 🟡 |
| D4 | 候选登记（不可变） | `pipeline.py target-register` | 带失败血缘的不可变登记 | ✅ |
| D5 | 非执行式预检 | `pipeline.py target-preflight` | 校验并物化，**不执行**；含凭证名/可执行文件就绪检查 | ✅ |
| D6 | 配对比较 | `pipeline.py target-compare` | 候选 vs 基线，冻结配对集 | 🟡 |
| D7 | 配对执行 | `pipeline.py target-evaluate` | 内部 Agent 安全/回归作业 | 🟡 |
| D8 | 合成闭环自检 | `pipeline.py target-smoke` | 本地合成的 build→compare 闭环 | ✅ |
| D9 | 生成测试候选 | — | 任务/攻击/环境变体的生成 | ⬜ |
| D10 | 候选八道闸门准入 | `evolution/admission.py` | 门槛在 `protocol/evaluation_tracks.json` 的 `required_gates` 定义（8 项） | 🟡 |
| D11 | 测试集版本发布 | — | 进入下一版演化池 | ⬜ |
| D12 | 实验控制器 | — | 调度、预算、恢复、版本记录 | ⬜ |
| D13 | 重放诊断 | — | 验证失败条件、隔离私有信息 | ⬜ |
| D14 | 自动回滚 / 部署 | — | **明确不做**（只留回滚指针） | ⬜ |

#### E. 自然语言编排（与 CLI 平行）

| # | 功能 | 入口 | 说明 | 状态 |
|---|---|---|---|---|
| E1 | 自然语言 → plan | `PYTHONPATH=harness/src python -m agent_safety_harness.driver` | 指令解析成函数调用序列 | ✅ |
| E2 | 安全执行 | 同上 | 顺序/条件执行，支持 `dry_run` 安全模式 | ✅ |
| E3 | 白名单拦截 | `driver/nl_bridge.py` | **强制只调已批准函数，绝不拼 CLI 字符串** | 🟡 |
| E4 | 统一函数层 | `harness/src/agent_safety_harness/registry.py` + `driver/registrations.py` | 共享函数层与输入校验；现有自然语言 planner 仍需对接真实参数 | 🟡 4 个真实引擎接口、2 个待接入 |

**registry 六个函数**（`driver/registrations.py`）：`bench_intake` / `build_sandbox` / `run_capture` / `score` / `analyze` / `evolve`
—— intake/build/capture/score 已连接既有来源处理、Docker 构建、原生捕获和私有评分；
`analyze` 与 `evolve` 仍分别由一迪与伦锋接入。真实参数及工程验收见[统一引擎接口](engine_registry.md)，不能把旧 driver 的 dry-run 成功当成真实联调完成。

#### F. 沙箱与隔离

| # | 功能 | 入口 | 说明 | 状态 |
|---|---|---|---|---|
| F1 | 本地工作区沙箱 | `harness/.../sandbox/local_workspace.py` | 本地目录级隔离 | ✅ |
| F2 | 工具后端沙箱 | `harness/.../sandbox/tool_backend.py` | 工具调用级隔离 | ✅ |
| F3 | 组合沙箱 | `harness/.../sandbox/composite.py` | 多后端组合 | ✅ |
| F4 | 空后端 | `harness/.../sandbox/null_backend.py` | 显式不做隔离（用于对照） | ✅ |
| F5 | 真实隔离资格 | — | **宿主机入口无法强制所声明的隔离**，真实执行目前统一拒绝 | ⬜ |

#### G. 协议与配置

| # | 功能 | 位置 | 说明 | 状态 |
|---|---|---|---|---|
| G1 | 轨道注册表 | `protocol/evaluation_tracks.json` | 轨/执行器/输入契约/指标/边界 + 进化闸门 + target 进化状态 | ✅ |
| G2 | canonical schema | `protocol/canonical_schema.yaml` | 13 核心字段，多源汇总时套 | ✅ |
| G3 | 默认分类体系 | `protocol/default_taxonomies.yaml` | 8×19 / 5×14 维度定义 | ✅ |
| G4 | 指标注册表 | `protocol/metric_registry.yaml` | 指标定义 | ✅ |
| G5 | 模型注册表 | `protocol/model_registry.json` | 模型与角色配置 | ✅ |
| G6 | 三角色模型配置 | `harness/config/llm_roles.json` | `under_test` / `harness_driver` / `generator` | ✅ |
| G7 | 免费 API 端点 | `harness/config/free_endpoints.json` | 11 个 OpenAI-compatible 免费/低价端点 | ✅ |
| G8 | Loop-A 契约 | `protocol/loop_a_contracts.schema.json` + `loop_a_experiment.json` | 回路 A 的协议与实验配置 | 🟡 |
| G9 | 分类体系注册表 | `protocol/taxonomy_registry.yaml`（模板在 `assets/templates/`） | `taxonomy_ref` 引用的目标 | ✅ |

#### H. 工程与协作

| # | 功能 | 位置 | 说明 | 状态 |
|---|---|---|---|---|
| H1 | 统一验收 | `scripts/check_repo.py` | 一次跑完：绑定协议 + 三套测试 + 3 个 fixture + LLM 自检 + 分析 + Loop-A 对比 + 文档链接 | ✅ |
| H2 | CI | `.github/workflows/portable-checks.yml` | 检查名 `tests`，分支保护要求它必过 | ✅ |
| H3 | PR 规范门禁 | `.github/workflows/pr-policy.yml` | 标题/分支名校验 + 密钥/路径/邮箱扫描 + 契约变更需王浩知悉 | ✅ |
| H4 | CODEOWNERS | `.github/CODEOWNERS` | 按目录自动指派评审人，**每条路径 ≥2 owner** | ✅ |
| H5 | 外部数据登记 | `federated_asr_benchmark/benchmark_registry.json` + `product_registry.json` | benchmark 与产品的版本 pin | ✅ |

### 2.3 数据契约（贯穿全流程的 5 个标准产物）

| 产物 | 干什么 | 在哪定义 |
|---|---|---|
| **TaskSpec** | 任务定义（单个 benchmark 也必须套） | `harness/.../schema.py` + `protocol/canonical_schema.yaml` |
| **TraceEvent** | 执行轨迹 | `harness/.../schema.py` |
| **ResultSpec** | 评分结果 | `harness/src/agent_safety_harness/schema.py`（evaluation/schema.py 校验规则） |
| **分析四件套** | observations / failures / invalids / candidate_plans | `scripts/result_analysis.py` |
| **进化 manifest** | 自进化产物登记 | `evolution/` + `protocol/loop_a_contracts.schema.json` |

### 2.4 统一模板的三层适用边界

**这是最容易被误解的地方**，务必分清：

| 层 | 内容 | 什么时候套 |
|---|---|---|
| ① 任务信封 | TaskSpec（4 必填字段） | **单个 benchmark 也必须套**，代价极低 |
| ② canonical 13 字段 | 统一报告口径 | **多源聚合/统一报告时**才套 |
| ③ 8×19 分类体系 | 默认兜底分类 | **仅默认兜底**；外部 benchmark 用它自己的原生分类优先 |

**硬套 19 维会给不存在的标签造假。**

---

## 第三部分：推进规划

### 3.1 三阶段（依赖顺序，不是 deadline）

```
P0  各自跑通 + 铁轨联调      →  P1  扩展 + 衔接加固      →  P2  自进化 + 优质模型贯通
    （免费/低价 API）              （依赖 P0 产物）              （只有这阶段产物进论文）
```

| 阶段 | 目标 | 完成标志 |
|---|---|---|
| **P0** | 四条轨各自端到端跑通 + `failures.jsonl` 喂进 `evolve` | 5 人各自 P0 交付物落地；伦锋的 `evolve` 接到真实种子；CI 全绿 |
| **P1** | 新增 benchmark、注册式产品接入、四轨聚合校验、Loop-A blocker 消解 | registry 支持 ≥2 个外部 benchmark；产品接入不用手写；Loop-A blocker 逐条消 |
| **P2** | 切优质模型全量出分 + Loop-A/B 闭环 + 对外看板 | **可复现出分**（带源哈希 + 固定索引）；双回路闭环跑通 |

**P0 阶段全程只用免费/低价 API**（`harness/config/free_endpoints.json`）。
**只有 P2 的产物进白皮书/论文**——这是硬规则。

### 3.2 各阶段的关键路径与依赖

```
刘轲 schema.modality ──→ 一迪 llm_qa 产 TaskSpec
        │
        └──→ 刘轲 registry 6 函数 ──→ 伦锋 driver 接真函数
                                          ↑
一迪 failures.jsonl ────────────────────┤
辰浩 failures.jsonl ────────────────────┘
```

**没有依赖关系的可以并行做**：刘轲和辰浩互不阻塞；一迪除 schema 外可独立推进。

### 3.3 资源策略

| 阶段 | 模型 | 理由 |
|---|---|---|
| P0 | 免费/低价档（智谱 GLM-4-Flash、硅基流动等 11 个端点） | 够跑通管道，不浪费预算 |
| P1 | 免费/低价档 + 必要的付费档（王浩协调） | 扩展阶段仍不需要旗舰模型 |
| P2 | 优质模型（`harness/config/llm_roles.json` 的旗舰 profile） | **只有这里出的分才有论文价值** |

**API key 各自申请、各自测试**，只走 `api_key_env` 环境变量，不入库、不发群。

### 3.4 当前状态（2026-11-04）

| 项 | 状态 |
|---|---|
| 四轨执行器 | ✅ 全部就位（`pipeline.py inventory` 的 `executor_exists` 全为 true） |
| registry 6 函数 | 🟡 4 个真实接口已有实现，`analyze`、`evolve` 待对应负责人接入；自然语言参数联调仍需完成 |
| 外部 benchmark | 🟡 4 个已选型（HarmBench / SafetyBench / AgentDojo / ToolEmu），待拉取登记 |
| 产品请求样例 | ✅ 32 个现成 `run_request.json`（4 产品 × 8 benchmark） |
| Loop-A | 🟡 5 个 blocker 待消（协议冻结/主指标确认/被测与生成器固定/预算配置/调度隔离注册） |
| GitHub 协作机制 | ✅ 已落地并实测（分支保护 + CODEOWNERS + CI 门禁 + 5 个 P0 issue） |
| 验收 | ✅ 全绿：root 145/145、harness 158 通过 10 跳过、product 148 通过 54 跳过、文档 39 个 Markdown 零链接错误 |

### 3.5 什么算「做完」

**P0 完成的判据**（不是"写了代码"，是"能跑出结果"）：

| 轨 | 判据 |
|---|---|
| llm_qa | 外部 benchmark 在**官方口径**下出分，带源哈希 + 固定索引 |
| agent_execution | ≥1 个真实 benchmark 端到端跑通，capture 与 score 严格分离（有单测证明） |
| agent_product | ≥1 个真实产品跑出**可复现 ASR** |
| trace_judge | 离线判分独立跑通，且**指标未被混入** Agent 执行分 |
| 统一分析 | 四件套字段齐、数量对，`failures.jsonl` 能被 `evolve` 消费 |

**判分方式（规则法 / llm_judge / 自定义）是可配置变量，不是铁律。**
但**对外陈述必须区分两类**：
- 外部 benchmark **复现**（HarmBench / SafetyBench / AgentDojo / ToolEmu，用官方 scorer）
- 自建 19 维体系（668,820 题聚合集）

**这是两件事，文档和论文里必须分开陈述，不能混为「我们测了这些 benchmark」。**

---

## 第四部分：待确认与已知风险

| 事项 | 状态 | 谁定 |
|---|---|---|
| 19 维分类体系会先于白皮书公开（仓库已转 public） | ⚠️ 待确认是否需要先撤下 | 王浩 |
| 外部 benchmark 复现 vs 自建体系的口径分离 | 已在契约第 10.3 节写死，王浩兜底 | 王浩 |
| 自进化能否真的产出更优评测数据 | **开放问题**，不承诺 | 研究赌注 |
| 真实隔离无法在宿主机强制 | 真实执行统一拒绝，dry-run 可用 | 已知限制 |
| 68 万题聚合集的对外披露口径 | ⚠️ 需与白皮书一致 | 王浩 |

---

## 附：怎么找到对应文档

| 你想知道 | 看哪份 |
|---|---|
| **不确定该读哪份、文档冲突时以谁为准** | **[DOCUMENT_MAP.md](DOCUMENT_MAP.md)**（文档地图：五层结构、阅读路径、权威性裁决） |
| 怎么跑、命令是什么 | [pipeline_guide.md](pipeline_guide.md) |
| 接口约定、数据契约、红线 | [FRAMEWORK_CONTRACT.md](FRAMEWORK_CONTRACT.md) |
| 谁做什么、谁 block 谁 | [work_assignment.md](work_assignment.md) |
| 分支/PR/评审/密钥红线 | [github_collaboration.md](github_collaboration.md) |
| 第一轮跑哪些 benchmark、怎么拿数据 | [rampup_samples.md](rampup_samples.md) · [benchmark_acquisition.md](benchmark_acquisition.md) |
| 协议、指标、分类的具体定义 | `protocol/` 目录 + [default_taxonomies.md](default_taxonomies.md) |
| 每个模块的设计意图 | [integration/harness_design.md](integration/harness_design.md) |
| 已跑过的实验与验收记录 | [integration/validation.md](integration/validation.md) |
| 全部文档索引 | [README.md](README.md) |
