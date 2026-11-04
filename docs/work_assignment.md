# 四人分工与排期 · 分发版（agent-safety-harness）

> 配套规范：`docs/FRAMEWORK_CONTRACT.md`（契约死规定）、`docs/project_architecture.html`（看板）。
> 本文是**执行分发版**——给刘轲 / 辰浩 / 一迪 / 伦锋 每人一份，标明"具体实现什么、优先做哪、跟谁衔接"。
> 王浩 = 总体框架与设计，定契约 + 仲裁，**不写具体评测业务代码**。

---

## 0. 一句话角色

| 人 | 角色 | 一句话职责 |
|---|---|---|
| 王浩 | 总体框架与设计 | 锁契约、仲裁接口、答疑；不碰业务代码 |
| 刘轲 | 评测引擎组 | 核心引擎 + 统一函数层（registry）+ 环境构建（build_sandbox） |
| 辰浩 | 产品评测组 | 用真实入口驱动"完整产品/系统"跑原生 benchmark，出产品级 ASR |
| 一迪 | LLM QA + 分析组 | 外部 benchmark 规则评测（HarmBench/SafetyBench/AgentDojo/ToolEmu）+ 四轨聚合分析（产 failures） |
| 伦锋 | 自进化组 | 自进化双回路 + driver 智能编排（CLI/NL 统一调函数层） |

---

## 1. 真实起点盘点（已有代码 ≠ 从零写，别重写）

| 模块 | 状态 | 说明 |
|---|---|---|
| `harness/src/agent_safety_harness/` 包 | ✅ 已实大半 | `adapters/ sandbox/ evaluation/ importers/ cli/ config/ runners/ llm/ reporting/` 等大量真实模块已在 |
| `driver/registrations.py` 6 个函数 | ⬜ 占位 stub | `bench_intake / build_sandbox / run_capture / score / analyze / evolve` 标了 TODO，是统一函数层接口模板 |
| `federated_asr_benchmark/` | ✅ 已实大半 | `product_eval/` 下 8+ adapter（agentdojo/agentdyn/agentharm/agentleak/agentpoison/injecagent/msb/saber…）+ `run_product.py`/`aggregate_results.py`/`build_case_index.py`(5,384 条)/`preflight.py`/`score_*.py` |
| `llm_eval/multi_model_rule_eval_prompt_only.py` | ✅ 已实 | 规则判分执行器（可直接接外部 benchmark 格式） |
| `scripts/result_analysis.py` | ✅ 已实 | 四轨归一 + **八门控（GATES 元组已内置）** + 四件套（observations/failures/invalids/candidate_plans） |
| `evolution/` 目录 | 🟡 部分 | 控制面框架在；Loop-A 分支（`loop-a-local-harness`）未合；Loop-B 的 LLM 生成式优化未接 |
| `driver/planner.py executor.py nl_bridge.py` | 🟡 骨架 | 编排逻辑占位，需接真实 registry 跑通 |

**铁律（分发前再强调一次）**：5 标准产物（TaskSpec / TraceEvent / ResultSpec / 分析四件套 / 进化 manifest）是 4 人衔接的"铁轨"，所有人严格按 `schema.py` 与 `FRAMEWORK_CONTRACT.md` 第 5 章产出，否则代码接不上。分类体系（`taxonomy_ref`）与判分方法（`eval_method`）是**可配置变量**，不是写死。

---

## 1.5 测试与模型资源策略（先免费 API 打通，后优质模型贯通）

> **王浩备注**：测试阶段先用免费/低价 API 把管道功能跑通，**全部跑通之后**再用优质模型做贯通测试。不要一上来就用高价模型烧钱，也不要用免费模型的最终分数当论文/白皮书结论。

> **配套文件**：免费端点的具体配置（base_url / api_key_env / 模型 / 限速）见 `harness/config/free_endpoints.json`；第一轮跑哪些 benchmark、哪些产品、几天跑完见 `docs/rampup_samples.md`。

| 阶段 | 用的模型 | 目的 | 谁负责 |
|---|---|---|---|
| **功能打通（P0，第 1 周）** | 免费 / 低价模型（如 DeepSeek V4 Flash 等受控 profile 锁定的免费档） | 验证"管道能不能跑、schema 对不对、铁轨通不通"，不计较分数高低 | 4 人各自 P0 |
| **扩展调试（P1，第 2–3 周）** | 仍以免费档为主，个别需要判分的环节可小批量试中档模型 | 扩展 benchmark / adapter / 自进化循环，看工程正确性与稳定性 | 4 人各自 P1 |
| **贯通测试（P2，第 4 周+）** | **优质模型**（GPT / Claude / 旗舰开源等，按白皮书/论文口径选定） | 全量、正式出分；只在这个阶段的产物才进白皮书/论文 | 辰浩、一迪为主，伦锋做全链路编排 |

**硬性纪律**：
1. 任何"管道没跑通"的环节，都只允许用免费档，禁止用优质模型去 debug。
2. 优质模型的贯通测试必须**全量、可复现**（带源哈希 + 固定索引），且只在 P2 阶段启动。
3. 最终对外报告/论文的数据，**只能来自优质模型贯通测试**，免费档的跑分不得混进结论。
4. 模型 profile 由 `config/llm_roles.json`（under_test / harness_driver / generator 三角色）统一配置，切换"免费↔优质"只改配置、不改动 schema 与业务代码。
5. **免费端点配置**已备好 `harness/config/free_endpoints.json`（10 个 profile：智谱 glm-4-flash / 硅基流动 Qwen2.5-7B / Groq / OpenRouter / Gemini / NVIDIA NIM / GitHub Models / 本地 Ollama 等），全部 OpenAI 兼容 → **零代码接入**，直接复制进 `config/models.json` 即可用。

---

## 2. 刘轲 · 评测引擎组（P0 最高优先，底座）

**负责目录**：`harness/src/agent_safety_harness/` 全包 + `driver/registrations.py` 中 `bench_intake/build_sandbox/run_capture/score`（真实引擎替换占位）+ `scripts/source_intake.py`（GitHub 拉取）。

| 优先级 | 具体功能 | 产出 / 完成标准 | 衔接 |
|---|---|---|---|
| **P0** | 把 `registrations.py` 的 4 个 stub 接成真实调用：`bench_intake`(GitHub @固定revision+哈希校验) / `build_sandbox`(读环境声明自动构建隔离runtime) / `run_capture`(公开执行只产轨迹) / `score`(注入私有规则产 ResultSpec) | 6 函数中至少 4 个非占位；现有 1 个真实 benchmark（AgentSafetyBench）端到端打通 | 供给伦锋（registry 底座）、一迪（run_capture 调 llm_eval）、辰浩（build_sandbox 供隔离 runtime） |
| **P0** | 守住"公开执行 + 私有评分分离"：capture 阶段 `evaluation` 字段必须为空，评分规则绝不进 capture | 代码层强制校验，有单测 | 全体（安全边界） |
| **P1** | 新增 ≥2 个真实 benchmark（除 AgentSafetyBench 外，优先 InjecAgent / AgentDojo），每个走 `build_sandbox` 自动构建 | 一键 `build_sandbox` + 跑通 ≥3 个 benchmark | 辰浩（产品轨可复用其 scorer）、一迪 |
| **P1** | 扩展 `adapters/`（native_environment / unified_scenario）覆盖新 benchmark 的环境与任务格式 | adapter 注册可插拔 | 伦锋（driver 调） |
| **P2** | `build_sandbox` 对脏环境（非干净 CLI 产品）的兼容与手工兜底文档 | 覆盖率/成功率证据 | 辰浩 |
| **P2** | CI：registry 函数签名变更告警 + 四轨 schema 校验 | CI 绿 | 伦锋 |

**刘轲的明确红线**：只填真实引擎，**不要重写** `adapters/ sandbox/ evaluation/ importers/` 等已实模块；它们能用就复用。

---

## 3. 辰浩 · 产品评测组

**负责目录**：`federated_asr_benchmark/`（tools / product_eval / release）。

| 优先级 | 具体功能 | 产出 / 完成标准 | 衔接 |
|---|---|---|---|
| **P0** | 真实数据接入 + 真实跑出结果：取 ≥1 个真实产品 runtime/CLI + BYOK 凭证（受控 profile 锁 DeepSeek V4 Flash；Codex 0.122.0 / Claude Code 2.1.195 / Qoder 1.0.48 锁定版本），跑 `run_product.py`/`run_product_batch.py` + `aggregate_results.py` | 至少 1 个真实产品在真实数据上出**可复现 ASR**，带源哈希 + 固定索引入仓 | 产物 `failures.jsonl` → 伦锋（进化种子） |
| **P0** | 维护 `product_registry.json` / `benchmark_registry.json` 与各 benchmark 原生 scorer 的**版本 pin + preflight 证据**（可复现，preflight 不过不许跑） | preflight 通过记录入库 | 伦锋、王浩（复现审计） |
| **P1** | **把"接入一个被测系统"做成注册式自动化机制**：新增系统 = 注册一个 adapter + 一条 product profile（runtime/CLI 形态、BYOK 凭证、版本 pin）；harness 据此（结合刘轲 `build_sandbox`）自动构建隔离 runtime、自动注入参数与凭证隔离 | 加系统只需加 adapter+profile，无需手工改代码 | 依赖刘轲 `build_sandbox`（可先用现有 BYOK 包装，非硬阻塞） |
| **P1** | 扩展"完整产品/系统"定义：把**学术界自研 Agent**（有真实 CLI/运行时/API 入口）纳入可测对象 | 至少 1 个学术 Agent 端到端跑通 | 伦锋 |
| **P2** | 跨 benchmark 的 `Macro-ASR` 报告（覆盖率门槛 + AgentPoison 官方 ASR-r 单位不互转） | 报告产物 | 王浩（白皮书/论文数据） |

**辰浩的明确红线**：判别标准是"真实入口 + 真实系统行为"，不只商用产品；产品原生 ASR **不得**和模型排行榜混排（测量单元是系统不是模型）。

---

## 4. 一迪 · LLM QA + 分析组

**负责目录**：`llm_eval/multi_model_rule_eval_prompt_only.py` + `scripts/result_analysis.py` + canonical 数据集接入 + `pipeline_cli analyze`。

| 优先级 | 具体功能 | 产出 / 完成标准 | 衔接 |
|---|---|---|---|
| **P0** | 接入**外部公开 benchmark**跑出分（第一轮：LLM 轨 = HarmBench + SafetyBench，Agent 轨 = AgentDojo + ToolEmu；选型与参数见 `docs/rampup_samples.md`）。自有 668,820 题聚合集是**我们自建的 19 维评测体系**，不作为"外部 benchmark 复现"，它属于 P2 的自有体系产出 | 各模型在外部 benchmark 官方口径下的分数（SafetyBench 规则通过率、HarmBench 官方 ASR、AgentDojo/ToolEmu 官方成功率），带源哈希 + 固定索引入仓 | `failures.jsonl` → 伦锋 |
| **P0** | 验证 `scripts/result_analysis.py` 稳定产出**分析四件套**（observations/failures/invalids/candidate_plans），其中 `failures.jsonl` 是给伦锋 Loop-A 的"种子" | `failures.jsonl` 字段/数量达标 | 伦锋 |
| **P1** | 把 `analyze` registry stub 接 `result_analysis.py`（伦锋的 driver 能直接调） | `analyze` 非占位 | 伦锋 |
| **P1** | 四轨聚合质量校验：确认 `eval_method`(规则/llm_judge/自定义) 与聚合口径（通过率分母、拒答处理、API 错误处理）是**配置项**，默认走规则法 | 配置开关可用，不改 schema | 王浩（契约变量） |
| **P2** | 把失败观察转成可实例化的候选计划（给 Loop-A 当种子，含八门控预判） | candidate_plans 质量 | 伦锋 |
| **P2** | 自有 668,820 题聚合集（19 维体系产物）与外部 benchmark 口径的交叉映射 + 白皮书/论文数据产出 | 19 维结果与外部 benchmark 结果的可比性说明 | 王浩（白皮书/论文数据） |

**一迪的明确红线**：执行器 `multi_model_rule_eval_prompt_only.py` 是 2026.03 实测验证过的方案，**不要重写**，直接接数据跑。**但要分清两件事**：① 外部 benchmark 复现（HarmBench / SafetyBench / AgentDojo / ToolEmu，用各自官方判分口径）；② 我们自建的 19 维评测体系（668,820 题聚合集）。两者在文档和论文里**必须分开陈述**，不能混为"我们测了这些 benchmark"。判分方法是默认配置（规则法），后期可换 `eval_method=llm_judge`，不是铁律。

---

## 5. 伦锋 · 自进化组（研究赌注，带闸门）

**负责目录**：`driver/`（planner.py / executor.py / nl_bridge.py / __main__.py / __init__.py 编排部分）+ `evolution/` + `registrations.py` 中 `evolve` stub + CI。

| 优先级 | 具体功能 | 产出 / 完成标准 | 衔接 |
|---|---|---|---|
| **P0** | driver 端到端跑通：planner(指令→plan) → executor(顺序/条件执行，dry_run 安全模式) → 调 registry 真实函数 | `python -m agent_safety_harness.driver` 能跑通一条真实 plan（非占位） | 依赖刘轲（registry 真实现） |
| **P0** | `nl_bridge.py` 白名单验证：自然语言 → plan，强制只调已批准函数（不 shell CLI 字符串） | 白名单拦截测试通过 | 王浩（安全边界） |
| **P0** | 把 `evolve` stub 接 `evolution/`（先返回 pending/存档，等种子） | `evolve` 非占位 | 消费一迪/辰浩的 `failures.jsonl` |
| **P1** | Loop-A（测试集进化）合入/实做：失败观察 → 八门控（source_and_license/replay/benign_solvability/private_oracle/deduplication/diversity/holdout_isolation/review）→ 出新测试集；**人工审批闸门必留** | 闭环可跑 + 审批节点 | 依赖一迪/辰浩 `failures.jsonl` |
| **P1** | Loop-B（方法进化）控制面接 LLM 生成式优化（受控、带人工在环），**禁止全自动** | 控制面 + 审批流 | 王浩（研究赌注标注） |
| **P2** | CI：四轨 schema 校验 + registry 签名变更告警 + 进化 manifest 落库 | CI 绿 + 看板 | 刘轲 |

**伦锋的明确红线**：自进化是**研究赌注**，能做成"受控生成 + 八门控 + 人工审批"的循环，但"自动产出更优评测数据"是否有效是开放问题——**绝不承诺全自动**，必须人在环。driver 编排只调 registry 函数，不拼 CLI 字符串。

---

## 6. 协作衔接图（谁 block 谁）

```
王浩(契约/仲裁)
   │ 锁死 5 标准产物 + 四轨接口 + 分类/判分可配置
   ├──▶ 刘轲  registry 真实引擎 + build_sandbox ──┬─▶ 伦锋(driver 编排调 registry)
   │                                             ├─▶ 一迪(run_capture 调 llm_eval)
   │                                             └─▶ 辰浩(隔离 runtime，可先用 BYOK 包装)
   ├──▶ 辰浩  product ASR + failures.jsonl ─────────▶ 伦锋(evolve 种子)
   ├──▶ 一迪  llm_qa 出分 + failures.jsonl ────────▶ 伦锋(evolve 种子)
   └──▶ 伦锋  driver 编排 + 自进化(带闸门)
```

**硬性衔接点（铁轨，断一处全断）**：
1. **registry 6 函数** = 统一函数层，CLI 与 driver 都调它；刘轲填真实现，伦锋调它编排。
2. **`failures.jsonl`**（一迪 + 辰浩 各产各轨的）→ 伦锋 `evolve` 消费。字段必须一致（见 `result_analysis.py` 的 `IDENTITY_FIELDS`）。
3. **`eval_method` / `taxonomy_ref`** 是配置变量，各人按需在 TaskSpec 设，不写死、不互相覆盖。
4. **capture 阶段 `evaluation` 字段为空**，评分规则只在 score 阶段注入（刘轲强制，安全边界）。

---

## 7. 分期排期（先打通铁轨，再扩展，最后自进化）

### 第 1 周（P0 · 各自跑通 + 铁轨联调 — 全程用免费/低价 API，见 1.5 节）
- 王浩：发布契约 + 建立答疑机制（接口分歧即时仲裁）。
- 刘轲：替换 4 个 registry stub 为真实调用；确认 ≥1 个真实 benchmark 端到端跑通。
- 辰浩：取真实产品 runtime/凭证，用现有 federated 跑通 ≥1 产品端到端出 ASR（**不等刘轲**）。
- **一迪**：接外部 benchmark（HarmBench + SafetyBench），50 条冒烟 → 200/500 条扩量；验证 `result_analysis` 产 `failures.jsonl`。
- 伦锋：driver dry_run 端到端跑通；nl_bridge 白名单拦截测试；`evolve` 先存档不生成。
- **第一周联调（关键）**：用一迪/辰浩 的 `failures.jsonl` 喂给伦锋 `evolve`，哪怕只打印/存档，验证铁轨贯通。

### 第 2–3 周（P1 · 扩展 + 衔接加固）
- 刘轲：新增 ≥2 benchmark（第二轮：InjecAgent / MSB），harden `build_sandbox`。
- 辰浩：注册式自动化接入（adapter+profile），扩到学术自研 Agent。
- 一迪：`analyze` stub 接 `result_analysis`；四轨聚合质量校验；**自有 668,820 题聚合集（我们自建的 19 维体系）**接外部 benchmark 口径做交叉映射。
- 伦锋：Loop-A 合入/实做（八门控 + 人工审批）；Loop-B 控制面接 LLM 生成（带闸门）。

### 第 4 周+（P2 · 自进化研究赌注 + CI/看板 — 此阶段启动优质模型贯通测试）
- **优质模型贯通测试**：切到 `config/llm_roles.json` 的旗舰/优质 profile，对全部已打通的管道做全量、可复现出分（带源哈希 + 固定索引）；**只有这阶段的产物才进白皮书/论文**。
- Loop-A/B 闭环调优；CI（schema 校验 + registry 签名告警 + 进化 manifest 落库）；对外看板/报告。

---

## 8. 分发检查清单（每人领任务前确认）
- [ ] 已读 `FRAMEWORK_CONTRACT.md` 第 3/4/5/10 章（四轨接口、分类/判分可配置、各人章节）。
- [ ] 已读本文 **1.5 节测试与模型资源策略**：P0/P1 只用免费 API 打通，P2 才用优质模型贯通，且最终结论只取优质模型产物。
- [ ] 已读 `docs/rampup_samples.md`（第一轮跑哪些 benchmark / 哪些产品 / 5 天执行顺序），并从 `harness/config/free_endpoints.json` 里挑好本轮要用的免费端点。
- [ ] 已 clone 仓库并 `PYTHONPATH=harness/src python -m agent_safety_harness.driver` 跑通 dry_run。
- [ ] 确认自己负责的模块**哪些是已有代码复用、哪些是 stub/TODO**（见第 1 节），不重写已有。
- [ ] 认领第 7 节第 1 周 P0 任务，并登记到看板 `project_architecture.html`。
- [ ] 接口有分歧 → 找王浩仲裁，不私自改 schema。
