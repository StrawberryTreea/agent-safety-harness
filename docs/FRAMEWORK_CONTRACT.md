# Agent Safety Harness · 框架契约（FRAMEWORK CONTRACT）

> **定位**：本文件是 `agent-safety-harness` 项目的**权威框架规范**，供总体框架（项目 owner）与 4 位开发成员开工使用。
> 它规定的是"所有人必须遵守的铁轨"——数据格式、接口边界、质量门、分类体系与自进化协议——而不是具体某条轨道的实现细节。
> 配套可视化看板：`docs/project_architecture.html`（架构图 + 状态色）。本文件与看板逐条对齐，本文件为可 diff、可 review 的纯文本权威版。
> **变更流程**：任何字段/门控/接口的增删改，须经总体框架审批，并同步更新本文件与看板第 5 章（见第 11 章）。

---

## 0. 项目一句话目标

构建一个**以 CLI 命令行为主、自然语言描述为辅**的 LLM & Agent **自动化安全评测 harness**，并且能**根据评测结果反向自进化**——反向优化评测数据（Loop-A）与评测方法（Loop-B）。

- **CLI 优先**：所有能力最终都注册成函数，命令行直接调函数。
- **自然语言为辅**：`harness_driver` 解析自然语言指令后，也调同一批函数，底层零重复（详见第 8 章）。
- **自进化**：评测产出失败观察 → 反哺测试集与方法，但**不自动部署、不自动晋升版本、unknown 不补通过**（质量门硬约束）。

### 0.1 人员结构

| 角色 | 职责定位 |
|---|---|
| **🧭 你（总体框架与设计）** | 定契约 + 仲裁，不写具体评测业务代码 |
| **成员1 · 评测引擎组** | 核心引擎 + 统一函数层（`agent_safety_harness/` 全包 + `registry` + `driver/` 注册表部分） |
| **成员2 · 产品评测组** | 完整产品评测（`federated_asr_benchmark/`） |
| **成员3 · LLM QA + 分析组** | 纯 LLM 问答评测 + 四轨聚合分析（`llm_eval/` + `result_analysis.py`） |
| **成员4 · 自进化组** | 自进化双回路 + driver 智能编排（`evolution/` + `driver/` 编排部分 + CI） |

---

## 1. 分层架构（文字版，与看板图对应）

```
交互层（兄弟关系）
 ├─ CLI 入口（scripts/pipeline_cli.py · harness/ash.py）        ← 人敲命令
 └─ 自然语言驱动（agent_safety_harness/driver/）               ← 自然语言指令
       两者都直接调 ↓
统一能力函数层 TOOL REGISTRY（agent_safety_harness/registry.py）← 单一实现入口
        │
        ↓
评测轨道层（四轨）            核心引擎层（被轨道调用）
 ├─ llm_qa                    ├─ adapters/（benchmark → TaskSpec）
 ├─ agent_execution           ├─ evaluation/（确定性评分，不调 LLM judge）
 ├─ agent_product             ├─ sandbox/（隔离执行）
 └─ trace_judge               ├─ runners/ agents/（执行 + 多类 Agent）
        │                     └─ importers/（外部集 → TaskSpec）
        ↓
统一分析层（scripts/result_analysis.py）── 产出「分析四件套」
        │
        ↓（失败观察 = 自进化燃料）
自进化层
 ├─ Loop-A 测试集进化（反向优化评测数据）   ← 仅分支内，未合入 main
 └─ Loop-B 方法进化（反向优化评测方法）     ← main 已实现受控控制平面
        │
        ↓
数据资产层（config/ protocol/ canonical 数据 / 外部官方 benchmark 源）
```

**关键架构决定（已拍板）**：CLI 与自然语言驱动是**兄弟交互壳**，底层共享同一个 `tool_registry` 函数层；`harness_driver` 直接调 Python 函数，**绝不 shell 出 CLI 字符串**（避免 subprocess 慢 + 结构化数据在内存中丢失）。

---

## 2. 统一数据契约 —— 贯穿全流程的 5 个标准产物

数据在管道里**只以这 5 种格式流动**。每个人只认格式、不认人。改格式须经总体框架审批。

### 2.1 TaskSpec（任务定义）
**来源**：`harness/src/agent_safety_harness/schema.py` 的 `TaskSpec`。
**生产者**：各轨道 / importers / adapter / Loop-A ｜ **消费者**：运行器、评分引擎、沙箱。

| 字段分组 | 字段 | 说明 |
|---|---|---|
| 公共（必填） | `task_id` `benchmark` `modality` `profile` `taxonomy_ref` `labels` `evaluation` `metadata` | `modality` 是可**扩展枚举**（默认集 `{llm, agent}`，新增模态在注册表登记 track adapter + 结构契约即可）；`taxonomy_ref` 引用已注册分类体系（缺省 = 默认体系）；`evaluation` 在 capture 阶段**必须为空** |
| LLM 模态（默认示例，纯 prompt） | `test_text` `reference_answer` `type`(判断/选择/生成/问答) `eval_method`(规则) `note.rule` `label_mapping` | **无 tools / initial_state / user_goal** |
| Agent 模态（默认示例，需工具/环境） | `user_goal` `attack_goal`(选) `tools`(name/type/risk) `initial_state` | `profile` ∈ {harmful_agent_task, prompt_injection_tool_env, tool_use_emulated_sandbox, trajectory_judge} |

**硬规定**：
- `modality` 是分派键（llm → llm_qa 轨；agent → 其余三轨），但枚举可扩展，新增模态须总体框架审批。
- 结构契约按模态走：llm 自动忽略 tools/initial_state/user_goal；agent 必须有 user_goal 或 tools/initial_state。
- 分类体系不写死：维度标签经 `taxonomy_ref` 引用，benchmark 原生体系直接注册引用即可，不必套默认体系。

> **代码现状 gap（成员1 必须落地）**：当前 `schema.py` 的 TaskSpec 仍是 **agent-only**（含 `user_goal`/`tools`/`initial_state`，缺 `modality` 与 LLM 字段）。成员1 须：① `modality` 改为可注册可扩展枚举（默认集存于配置而非关闭的 Literal）；② 新增 LLM 模态字段，**维度不再硬编码进 schema，改由 `taxonomy_ref` 引用 `config/taxonomies`**；③ `from_record` 按 modality 校验必填；④ 所有旧 adapter/importer 的 TaskSpec 产出须补 `modality=agent`。这是 4 人开工前必须先落地的契约基础。

### 2.2 TraceEvent（执行轨迹）
**字段**：`event_id` `task_id` `step` `role`(user/agent/tool/environment/judge/policy) `event_type`(message/tool_call/tool_result/state_diff/refusal/policy_check/judge_verdict) `content` `timestamp`。
**硬规定**：轨迹里**不得包含私有评分规则**；泄漏检查在评分进程做。

### 2.3 ResultSpec（评分结果）
**字段**：`task_id` `benchmark` `agent_id` `task_type` `safety_pass` `reason` `task_success` `safety_violation` `attack_success` `unsafe_tool_call` `data_leak` `refusal` `policy_verdict`(pass/fail/warn/unknown) `score` `evidence` `cost`。
**生产者**：确定性评分引擎 / 官方 scorer ｜ **消费者**：分析层。
**硬规定**：`safety_violation` 与 `task_success` **独立**；攻击型任务可无 `task_success` 分母；`policy_verdict=unknown` **不补成通过**。

### 2.4 分析四件套（JSONL）
**字段**：`observations`(有效结果+证据) `failures`(有效失败，区分 safety_violation vs task_success) `invalids`(API/环境/评分 unknown/格式) `candidate_plans`(以失败为父，`admitted=false`) + `summary`/`manifest`。
**生产者**：分析层（`scripts/result_analysis.py`）｜ **消费者**：Loop-A / Loop-B / 人。
**硬规定**：重复 run/subject/benchmark/case 标识**直接终止分析**；`candidate_plans` 缺原任务/环境快照/私有规则/轨迹时只能当待办，不能实例化。

### 2.5 自进化产物（manifest）
- **Loop-A**：新 TaskSpec + `dataset-version`（含 parent task / 生成策略 / 模型版本 / 提示版本 / 种子 / 环境·scorer 版本 / 拒收原因 / 谱系）。
- **Loop-B**：决策记录（`decision=promote/development_pass/reject/insufficient_evidence`）+ 回滚指针 + 资产哈希。
**硬规定**：新任务先入 staging；`root_cause` 恒为 `unconfirmed`（禁止把 reason 当已证实机制）。

---

## 3. 四轨接口契约（写死，来自 `protocol/evaluation_tracks.json`）

| 轨道 | 执行器 | 输入契约 | 正向指标 | 边界硬规定 |
|---|---|---|---|---|
| **LLM 问答安全** `llm_qa` | `llm_eval/multi_model_rule_eval_prompt_only.py` | LLM 模态 TaskSpec（test_text / reference_answer / eval_method=规则 / note.rule） | `rule_eval_pass_rate` | **纯 prompt 评测，无 tools/环境/trajectory**；冻结输入总体；拒答 = 主指标失败；API error 单列排除，不进有效分母 |
| **Agent 执行安全** `agent_execution` | `harness/ash.py` capture/score | `schema.py` TaskSpec | `native_safety_pass_rate` | 来源派生任务；私有规则评分捕获轨迹；攻击型任务 task_success 可选；unknown 保持 unknown |
| **完整产品评测** `agent_product` | `federated_asr_benchmark/tools/run_product.py` | `run_request.schema.json` | `official_asr`（越低越好） | 完整产品 CLI + 原生环境 + scorer；status=success 即攻击成功；单位保持 case 或 retrieval_event，不互转 |
| **离线轨迹评审** `trace_judge` | `harness/ash.py` judge | `schema.py` TaskSpec | `native_trace_judge_pass_rate` | 离线轨迹评估；**绝不计入 Agent 执行人口** |

---

## 4. 评测分类体系（默认体系 + 可插拔 · 可修改变量）

**设计原则（已拍板，反转"写死两套"）**：
1. **体系可插拔**：每个体系在 `protocol/` 下有一份自包含定义（`id` + 维度编码 + 层级 + 适用 modality + 逐维定义），运行时由 `config/taxonomies/` 的注册项引用；改体系 = 改配置，不动 schema 代码。
2. **默认体系兜底**：我们提出的体系（LLM 8×19 / Agent 5×14）仅作 benchmark 无原生体系时的兜底。
3. **原生体系优先**：官方 benchmark 自带体系（AgentBench 任务族、InjecAgent 注入子类、ASRBench 错误码等）直接注册引用，**不强行套我们的体系**。
4. **跨体系对照（crosswalk）是可修改的配置变量**：只为统一报告提供"映射到默认体系"的派生视图，改不改、怎么映射由使用者编辑，不影响原始数据。

| 体系 | 性质 | 定义位置（仓库内，外部可访问） | 接入方式 |
|---|---|---|---|
| 默认 · LLM（`default-llm`） | 默认兜底（我们提出） | `protocol/default_taxonomies.yaml` + `protocol/taxonomy_registry.yaml`（LLM 8 个一级 × 19 个二级伤害类型） | benchmark 无原生体系时归到该体系，每模型按 19 维出分 |
| 默认 · Agent（`default-agent`） | 默认兜底（我们提出） | `protocol/default_taxonomies.yaml`（Agent 5 个一级 × 14 个二级风险） | benchmark 无原生体系时按 5 域出"执行安全率" |
| benchmark 原生体系（`<benchmark>-native`） | 首选（官方自带） | 随 benchmark 自身定义 | TaskSpec 的 `taxonomy_ref` 指向它 |
| 跨体系对照（crosswalk） | 可调变量（派生视图） | `config/taxonomies/` 注册项 | 纯配置，不映射也能跑 |

**铁律**：不再要求"新 benchmark 必须映射到我们的两套维度"。原生体系是一等公民，我们的体系只是默认兜底与统一报告参考系。新增/修改体系 = 改 `config/taxonomies/` 注册（可热插拔），不需要改 schema 代码，也不需要总体框架逐条审批。

> **文件状态**：机器可读定义已落地于 `protocol/default_taxonomies.yaml` 与 `protocol/taxonomy_registry.yaml`。**人读版 `docs/default_taxonomies.md` 尚未创建，由成员1 落地**（白皮书 PDF 与 668,820 条样本仅存于作者本地，不随仓库分发，正式引用以 PDF 原文为准）。

---

## 5. 自进化双回路与闸门（写死，不可删减）

### 5.1 Loop-A · 测试集进化（反向优化"评测数据"）
**八门控**（数据进主集的硬性验收闸门，新测试集须全过才发布 `dataset-version`）：
`source_and_license` / `replay` / `benign_solvability` / `private_oracle` / `deduplication` / `diversity` / `holdout_isolation` / `review`

**硬规定**：
- 演化池 / 验证池 / 最终留出集**实验前冻结**；留出集轨迹·答案·私有规则不得进入生成提示。
- 候选生成器只见允许的失败摘要与脱敏公开轨迹；私有 oracle 留在验收进程做泄漏检查。
- `automatic_admission=false`：自动生成 ≠ 自动接纳，须经门控 + review。

**现状**：🔴 仅存在于 `loop-a-local-harness` 分支（fixture 级原型，`formal_qualified` 恒 false，未调付费模型），**未合入 main**。成员4 负责合入并实现真生成器。

### 5.2 Loop-B · 方法进化（反向优化"评测方法"，`automatic_promotion=false`）
**本期已实现并作为契约的 15 项**：受控 JSON/YAML 提示·策略构建、不可变候选登记、失败谱系、配对安全比较、良性回归门、配对 agent 执行作业、非执行式预检、凭据/可执行就绪检查、公私 oracle 分离检查、输入/策略漂移检查、显式外部目标授权、有效性与成本门、封存发布决策记录、回滚指针、合成受控端到端 smoke。

**本期明确不做（待立项）的 5 项**：模型生成式候选优化、非 agent_execution 轨道调度、部署、自动回滚、轮次控制。

**硬规定**：`target-build` 禁改 command/base_url/credential/evaluation；`promotion_action_performed=false`（封存决策不自动部署）。

---

## 6. Benchmark 复现与规范化流水线

### 6.1 既定机制（4 人必须复用，不得另起炉灶）

| 阶段 | 脚本 | 输入 | 输出（已实现真实产物） |
|---|---|---|---|
| Phase 1 源接入 | `scripts/source_intake.py` | `--benchmark-root` 数据文件 + `--intake-config` + canonical schema | `dataset_catalog.json`（含 sha256/字段映射/status）、`field_mapping_report.json`、`needs_review.json`、`pending_manifests` |
| Phase 2 规范化 | `scripts/canonicalize_datasets.py` | Phase 1 的 `dataset_catalog.json` | `canonical.jsonl`、`stats.json`、`dataset_sources_clean.csv`、`quarantine.jsonl`、`membership_diff.json`（含源哈希校验、去重、类型转换） |

### 6.2 你要的"从 GitHub 拉取 + 为 agent 构建评测环境"——三个写死扩展点

| 扩展点 | 职责 | 归属 | 设计依据 |
|---|---|---|---|
| ① GitHub 拉取器 | 把"本地 `--benchmark-root`"前置为：按**固定 revision clone** 各 benchmark 仓库 → 落本地 → 哈希校验 → 交 Phase 1 | 成员1 | 复用 AgentSafetyBench 固定 revision 加载范本（无源即报错，防漂移） |
| ② 评测环境构建器 | 读 benchmark 的**环境需求声明**（Python 版本/依赖/容器镜像），自动构建隔离 runtime，把官方 scorer 包成统一 ResultSpec 评分 | 成员1 构建 + 成员2 接入（agent_product 专用 runtime） | 复用现有专用解释器变量（`AGENTDOJO_PYTHON`/`AGENTDYN_PYTHON`/`AGENTPOISON_PYTHON`）机制，从手动指定升级为自动构建 |
| ③ TaskSpec 适配 | 把规范化后的 benchmark 任务 → TaskSpec（含 tools/labels/evaluation） | 成员1 | 复用 `importers/mapped.py` + `importers/agentharm.py`，扩到更多 benchmark（当前 16 注册仅 1 真实） |

**铁律**：所有外部 benchmark 一律先过 Phase 1/2 规范化 + ③ 转 TaskSpec，才允许进评测管道。这是"统一数据"与"4 人衔接"的源头闸门。

---

## 7. 四人包的接口边界（写死的稳定 CLI 契约）

内部实现各包可自由重构，只要下列接口的输入/输出契约不变，4 人互不影响。

| 包（负责人） | 稳定暴露接口 | 输入契约 | 输出契约 |
|---|---|---|---|
| 成员1 评测引擎 | `ash capture/score/judge/import` + adapter 注册接口 | TaskSpec（capture 阶段无 evaluation） | captures.jsonl / traces.jsonl / ResultSpec |
| 成员2 产品评测 | `run_product` + 各 benchmark 原生 adapter/scorer | run_request + 数据根 + 隔离 runtime | case_result.json / official ASR |
| 成员3 LLM QA + 分析 | `llm_eval` 执行 + `analyze` | config + canonical 题目 / 各轨结果 | 每题结果 / 分析四件套 |
| 成员4 自进化 | `target-*` + `loop-a-*` | failures.jsonl / 候选资产 | 决策记录 / dataset-version manifest |

---

## 8. LLM 角色与配置

**统一配置位**：`harness/config/llm_roles.json`。**密钥只存环境变量名（`api_key_env`），绝不进文件**。

**为什么需要三个角色**：harness 要"接指令输入 + 自动行动 + 自进化"，底层都靠 LLM，但不同用途必须用不同角色、隔离配置。现有代码把"被评对象"散在 `harness/config/models.json` 与 `llm_eval` 的 MODELS 两处，且缺"harness 大脑"与"生成器"两个角色。

| 角色 | 含义 | 配置现状 | 用途 |
|---|---|---|---|
| `under_test`（被评对象） | 被测的模型/Agent（llm_qa 与 agent_execution 轨目标） | ✅ 已有，散在两处，成员1 须合并到 `llm_roles.json` 的 `under_test` 组 | 被测 Agent 的 `system_prompt` 即它的"指令输入"，它据此自动调工具行动（这是**被测对象**行动，非 harness） |
| `harness_driver`（harness 大脑） | 解析自然语言指令 → 规划 → **直接调 tool_registry 函数**自主串联全流程 | 🔴 缺失，需新增，落位 `agent_safety_harness/driver/` | 接收高层自然语言指令，产出结构化 plan 后逐条调注册函数；可带 `system_prompt` 约束只调已批准函数 |
| `generator`（自进化生成器） | Loop-A 出题 / Loop-B 未来"模型自动优化方法" | 🟡 现混在 Loop-B 配置里，须独立 | 吃 `failures.jsonl` 生成新 TaskSpec（Loop-A）或 candidate 配方（Loop-B）；与 `under_test` 物理隔离 |

**每个角色条目字段**（统一）：`provider` / `base_url` / `api_key_env` / `model` / `temperature` / `max_tokens` / `timeout_seconds` / `system_prompt`。

**铁律**：
- `under_test` 与 `generator` **必须物理隔离**——出题/优化用的 LLM 绝不能是被测对象，否则自进化结果自欺欺人。
- `harness_driver` 默认只读已批准函数清单（`tool_registry` 白名单），不能越权执行未注册动作；它调函数而非 CLI 字符串，保持结构化数据在内存流转。
- 密钥一律 `api_key_env` 引用环境变量，配置文件不得出现任何 key 明文。

### 8.1 harness_driver 落位与内部构成（成员1/4 共建）
「搭沙箱、跑评测、触发自进化」等所有"行动"本质是 `tool_registry` 里的一个函数；`harness_driver` 只负责**理解意图 + 选函数 + 按序调用 + 看返回决定下一步**，本身不重新实现引擎能力。

| 子模块 | 职责 | 输入 → 输出 |
|---|---|---|
| `agent_safety_harness/registry.py`（包级，CLI 与 driver 共用） | 函数注册表：每个能力装饰注册为 `{name, input_schema, output_schema, fn}`；提供 `list()`（白名单给 driver 看）/ `call(name, **kwargs)` | 引擎函数 → 统一可调用目录 |
| `driver/planner.py` | 把自然语言/半结构化指令拆成有序函数调用 plan（可含分支） | 自然语言指令 + 已批准函数清单 → 结构化 plan（list of `{fn, args}`） |
| `driver/executor.py` | 顺序/条件执行 plan，调 `registry.call` | plan → 各阶段产物 + 自进化触发信号 |
| `driver/nl_bridge.py` | 调用 `harness_driver` 角色 LLM，把指令 → plan；受 `system_prompt` 约束只产已批准函数 | 自然语言 → plan（json） |

**等价用法**：手敲 `pipeline_cli bench-intake X && build-sandbox X && run-capture X`，与对 driver 说"复现 X 并构建隔离环境跑一轮"——最终都落到同一批 registry 函数。前者给人用，后者给想用自然语言的协作者用，**底层零重复**。

> **代码骨架现状**：`registry.py` 与 `driver/`（planner/executor/nl_bridge/registrations/__main__）已搭建占位骨架，dry_run 链路已跑通（`python -m agent_safety_harness.driver`，需 `PYTHONPATH=harness/src`）。`driver/registrations.py` 内 6 个函数为占位（明确标 TODO），真实引擎由成员1/4 替换。

---

## 9. 衔接保证（4 人并行不接不上的根本保证）

1. **铁轨统一**：数据在管道里只以 5 种格式流动（TaskSpec / TraceEvent / ResultSpec / 分析四件套 / 自进化 manifest）。每个人只认格式不认人。
2. **接口是函数不是人**：成员1 把能力注册成 `registry` 函数，别人调函数即可，不关心内部。
3. **改格式要总体框架批**：谁想加字段/改门控，先过总体框架，且同步更新本文件第 2–5 章。
4. **CI 每晚拼一遍**：`scripts/check_repo.py` 把四块拼起来跑合成链路，接不上立刻红灯。
5. **准入闸门**：adapter 注册表 + Loop-A 八门控 + Loop-B 8 门槛，谁产出的数据要进管道先过门，不过就停 staging，不污染主集。

---

## 10. 4 人分工详细卡

### 10.0 你 · 总体框架与设计（跨模块，定契约）
- **定跨模块契约**（4 人全依赖，改要你批）：`schema.py`（5 个标准产物）、`protocol/`（四轨输入契约 + 自进化门控）、`config/`。
- **CLI 顶层设计**：统一入口 `scripts/pipeline_cli.py` 与内部 `harness/ash.py` 的子命令语义对齐。
- **自进化协议仲裁**：Loop-A 八门控、Loop-B 8 门槛 + "模型生成式优化"接口形状由你拍板。
- **质量门硬约束**：CI 必跑端到端 smoke；unknown 不补通过；自进化产物不自动部署、不自动晋升版本。
- **不做**：不写具体某条轨道的业务代码；不替成员决定内部实现——只定边界与交接格式。

### 10.1 成员1 · 评测引擎组（最核心，所有人都靠它的函数层）
- **负责目录**：`harness/src/agent_safety_harness/` 全包 + `harness/ash.py` + `registry.py` + `driver/` 里的注册表部分。
- **写什么**（按优先级）：
  1. **统一函数层（填实骨架）**：把每个评测能力用 `@REGISTRY.register_fn` 注册成带 schema 的函数（`bench_intake` / `build_sandbox` / `run_capture` / `score` / `analyze`）。CLI 和 driver 都从这里取函数。
  2. **benchmark 环境构建（新增）**：写 `sandbox/build.py`，读 benchmark 的"环境需求声明"（Python 版本/依赖/容器镜像），自动构建隔离 runtime，替代现在手动设 `AGENTDOJO_PYTHON` 等。
  3. **扩官方基准接入**：现在 16 个注册只有 1 个真实现（AgentSafetyBench），把更多官方 benchmark 接成真实 importer/adapter。
  4. **强化沙箱隔离 + 确定性评分算子**。
  5. **schema 扩展**：按第 2.1 章 gap 说明，给 `schema.py` 加 `modality` 可扩展枚举 + LLM 模态字段（这是开工前提）。
- **输入 → 输出**：吃 TaskSpec（capture 阶段无私有评分规则）→ 吐 ResultSpec + 隔离执行 runtime。
- **对外接口**：上面 5 个注册函数 + `harness/ash.py` 的 capture/score/judge。成员2/3/4 通过调用这些函数或读它们产出的文件协作。
- **完成标准**：① registry 里 5 个核心函数都是真实调用（非占位）；② 至少 N 个官方 benchmark 能一键 `build_sandbox` 自动构建并跑通；③ 守住"公开执行 + 私有评分分离"（评分规则绝不进 capture）。
- **与成员4 共建 driver**：成员1 出 registry + 引擎函数，成员4 出 planner/executor/nl_bridge。
- **现在起点**：核心引擎已实；本次已搭好 `registry.py` + `driver/` 骨架（占位可 dry_run 跑通）；官方基准仅 1 真实。

### 10.2 成员2 · 产品评测组（评"完整产品"）
- **负责目录**：`federated_asr_benchmark/`（tools / product_eval / release）。
- **写什么**：把"完整产品"（例如一个语音助手）接进 harness 评测。已有 8 个原生 adapter、`run_product.py`、BYOK 隔离、ASR 统计。
- **关键交付**：接真实外部数据 + 专用 runtime（和成员1 的 `build_sandbox` 对接），**跑出第一份真实产品评测结果**——这是整个项目目前最大缺口（现在 `runs/` 全是验收日志，没真跑过）。
- **维护**：8 个 benchmark 原生 scorer 与版本/preflight 证据（证明评测可复现）。
- **输入 → 输出**：吃 run_request + 数据根 + 隔离 runtime → 吐 case_result.json / 官方 ASR（**越低越好**）。
- **对外接口**：`run_product` 命令 + 各 adapter/scorer。成员4 的 Loop-A/B 会拿它的失败 case 当进化种子。
- **完成标准**：至少一个真实产品在一组真实数据上跑出可复现的 ASR 数字，并带源哈希 + 固定索引入仓。
- **现在起点**：代码已实现，缺真实数据接入与真实产出。

### 10.3 成员3 · LLM 问答评测 + 统一分析
- **负责目录**：`llm_eval/` + `scripts/result_analysis.py` + canonical 数据集 + `pipeline_cli analyze`。
- **写什么**：
  1. **llm_qa 轨**：纯 prompt → 模型输出 → 规则判分（不调 LLM judge）。即 2026.03 实测那套（668,820 题、规则通过率、拒答=失败、API 错误单列）。
  2. **四轨聚合分析**：`result_analysis.py` 把四轨结果归一，产出**失败观察**（自进化燃料）。
  3. **把失败观察转成可实例化的候选计划**（给 Loop-A 当"种子"）。
- **输入 → 输出**：吃 config + canonical 题目 / 各轨结果 → 吐每题结果 + **分析四件套**（observations / failures / invalids / candidate_plans）。
- **对外接口**：`llm_eval` 执行 + `analyze`。它的 `failures.jsonl` 是成员4 的输入。
- **完成标准**：llm_qa 能跑 2026.03 真实数据集出分；分析层能稳定吐出 `failures.jsonl` 供 Loop-A 消费。
- **现在起点**：llm_qa 与分析层已实现，缺真实数据（canonical 数据默认在 `data/` 未随仓库）。

### 10.4 成员4 · 自进化组（让 harness 评完反向优化自己）
- **负责目录**：`evolution/` + CI + `driver/`（planner/executor/nl_bridge）+ `docs/integration`。
- **写什么**：
  1. **Loop-A（反向优化评测数据）**：合入 `loop-a-local-harness` 分支，实现真实测试集生成器 + 八门控执行器 + 数据集版本发布（失败案例 → 更难更多样测试题 → 过八门控 → 发新版本）。
  2. **Loop-B（反向优化评测方法）**：从现有"受控控制平面"升级到"模型生成式候选优化"（仍走受控比较与封存，不自动部署）。
  3. **harness_driver 智能部分（已搭骨架）**：planner / executor / nl_bridge，和成员1 的 registry 对接。
  4. **CI 质量门**：每次 PR 跑端到端 smoke，接不上就红灯。
- **输入 → 输出**：吃 `failures.jsonl` / 候选资产 → 吐决策记录 / dataset-version manifest / 自然语言执行结果。
- **对外接口**：`evolve` 注册函数 + driver 自然语言入口。成员3 的 `failures.jsonl` 是它的输入。
- **完成标准**：① Loop-A 能真实产出一版新测试集并过八门控；② Loop-B 能跑"模型生成式候选 → 受控比较 → 封存决策"；③ driver 能用自然语言驱动跑通一条真实链路。
- **守住约束**：不部署、不自动晋升、unknown 不补通过；driver 只读已批准函数清单。
- **现在起点**：Loop-B 受控平面已实现；Loop-A 在分支内（fixture 级）；driver 本次已搭骨架（占位 + dry_run 跑通）。

---

## 11. 第一周执行顺序（建议）

1. **你（第 1–2 天）**：确认 `schema.py` 已含 modality/双模态字段、registry 接口定稿，发本规范 + 看板给 4 人。
2. **成员1（并行启动）**：把 registry 里占位函数换成真实引擎调用（至少 `run_capture`/`score` 先真），并搭好 `build_sandbox` 框架。
3. **成员2 / 成员3（并行）**：接真实数据，目标第 1 周结束跑出第一份真实分数（自进化燃料，最优先解锁）。
4. **成员4（等成员1 的 registry 与成员3 的 failures）**：先合入 Loop-A 分支，再让 driver 接成员1 的 registry 做真实编排。
5. **汇合点**：第 1 周末用 CI 跑一次端到端（真实数据进 → 出 ResultSpec → 出 failures → 触发一次 Loop-A 生成），验证四块接得上。

**当前最该先补的两块（决定"自进化"能否成立）**：① 真实评测产出（成员2/3 接数据跑出第一份真实分数）；② Loop-A 合入与 Loop-B 模型生成式优化（成员4）。

---

## 12. 规范变更流程（硬性）

1. 任何人提议修改第 2–5 章的字段/门控/接口 → 提交变更说明给总体框架。
2. 总体框架审批后，变更同时落地到：**代码（schema.py / protocol/ / config/）** + **本文件** + **看板 `docs/project_architecture.html` 第 5 章**。
3. 涉及 `protocol/` 的版本化文件（如 `taxonomy_registry.yaml`）结构性变更，须 bump 版本号并附显式可比性声明（见文件内 `compatibility_note`）。

---

## 附录 A · 关键文件索引（仓库内真实路径）

| 文件 | 作用 | 状态 |
|---|---|---|
| `harness/src/agent_safety_harness/schema.py` | 5 个标准产物的数据结构定义 | ✅ 存在（TaskSpec 仍 agent-only，待成员1 扩展） |
| `harness/src/agent_safety_harness/registry.py` | 统一函数层 ToolRegistry | 🔴 本次新增骨架 |
| `harness/src/agent_safety_harness/driver/` | harness_driver 子包（planner/executor/nl_bridge/registrations） | 🔴 本次新增骨架 |
| `harness/config/llm_roles.json` | LLM 三角色配置 | 🔴 本次新增（占位配置） |
| `protocol/evaluation_tracks.json` | 四轨输入契约 + 自进化门控 | ✅ 存在 |
| `protocol/canonical_schema.yaml` | canonical 数据字段定义 | ✅ 存在 |
| `protocol/taxonomy_registry.yaml` | 默认 LLM 8×19 维度编码 | ✅ 存在 |
| `protocol/default_taxonomies.yaml` | 默认 LLM/Agent 体系自包含定义 | ✅ 存在 |
| `docs/default_taxonomies.md` | 默认体系人读版 | 🔴 待成员1 落地 |
| `scripts/source_intake.py` | Phase 1 源接入 | ✅ 存在 |
| `scripts/canonicalize_datasets.py` | Phase 2 规范化 | ✅ 存在 |
| `scripts/result_analysis.py` | 四轨聚合 + 分析四件套 | ✅ 已实现 |
| `scripts/check_repo.py` | CI 端到端 smoke | ✅ 存在 |
| `harness/ash.py` | Agent 评测 CLI（capture/score/judge） | ✅ 已实现 |
| `llm_eval/multi_model_rule_eval_prompt_only.py` | llm_qa 轨执行器 | ✅ 已实现 |
| `federated_asr_benchmark/tools/run_product.py` | agent_product 轨执行器 | ✅ 已实现 |
| `evolution/` | Loop-B 受控控制平面 | ✅ 部分实现 |
| `adapters/` `evaluation/` `sandbox/` `importers/` | 核心引擎层 | 🟡 1/16 基准真实 |

> 仓库地址：`https://github.com/StrawberryTreea/agent-safety-harness`（含固定 revision 复现机制，外部协作者可直接访问协议与默认体系文件，不依赖本地路径）。
