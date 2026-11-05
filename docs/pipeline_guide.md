# Pipeline 全景说明（谁该看哪一段）

> 这份文档回答一个问题：**这个仓库的 pipeline 到底长什么样、在哪里、怎么跑。**
> 之前 `day0_onboarding.md` 里只散着几条命令，很多人打开仓库根本不知道 `pipeline.py` 是什么，
> 所以这份单独写清楚。
>
> 想知道**整个项目是干什么的、有哪些功能、推进规划是什么** → 看 [项目总纲](project_overview.md)。
>
> 配套：[项目总纲](project_overview.md) · [上手清单](day0_onboarding.md) · [分工与推进顺序](work_assignment.md) · [框架契约](FRAMEWORK_CONTRACT.md) · [GitHub 怎么用](github_collaboration.md)

---

## 1. 一句话

**`pipeline.py` 是这个仓库的唯一总入口。** 仓库根目录下的 `pipeline.py` 只是一层薄壳（3 行代码），
真正逻辑在 `scripts/pipeline_cli.py`。所有评测、分析、自进化动作都从它进。

```bash
./.venv/Scripts/python.exe pipeline.py --help
```

建议先创建 `.venv` 并安装根目录 requirements；虚拟环境不随 Git 分发。已有 Python 环境依赖齐备时也可使用，不能仅凭是否叫“系统 Python”判断。

---

## 2. pipeline 的 11 个子命令

| 子命令 | 干什么 | 谁主要用 | 是否需要真模型/API |
|---|---|---|---|
| `inventory` | 列出 4 条轨的注册信息 + 本地依赖是否齐全 | 所有人（先跑这个） | 否 |
| `plan` | 把「跑某条轨」翻译成具体命令数组，**只打印不执行** | 所有人（看清要准备什么） | 否 |
| `smoke` | 跑 3 条本地 mock 任务，走完 capture→score→report→analyze 全链 | 所有人（先跑这个自证环境通） | 否 |
| `analyze` | 把结果行转成 observations / failures / invalids / candidate_plans 四件套 | 一迪、伦锋 | 否 |
| `loop-a` | 自进化回路 A 的契约自检 + 正式就绪检查（另有 8 个子动作，见下） | 伦锋 | 否 |
| `target-register` | 登记一个外部造出来的候选（带失败血缘） | 伦锋 | 否 |
| `target-build` | 由失败样本生成一个受限的 JSON/YAML prompt 或 policy 候选 | 伦锋 | 否 |
| `target-preflight` | 校验并物化已登记的 Agent 候选，**不执行** | 伦锋 | 否 |
| `target-compare` | 候选 vs 基线，在冻结的配对集上比 | 伦锋 | 可能需要 |
| `target-evaluate` | 执行配对的 Agent 安全/回归作业并对比 | 伦锋 | 需要 |
| `target-smoke` | 合成的候选构建 + 配对对比闭环自检 | 伦锋 | 否 |

**自进化相关的 6 个命令目前是 `implementation_status: partial`** ——
能跑的是「构建 → 登记 → 预检 → 配对比较」的骨架，
**还没实现**的是：模型自动生成候选、非 Agent 作业调度、部署、自动回滚、轮次控制器。
详见 `protocol/evaluation_tracks.json` 的 `target_evolution.not_implemented`。

---

## 3. 四条轨：pipeline 背后到底跑什么

`pipeline.py` 本身不实现评测，它**调度**每条轨自己的执行器。注册表在
`protocol/evaluation_tracks.json`（权威来源，改这里，不要改代码里的硬编码）。

| 轨 | 执行器（实际跑的东西） | 输入契约 | 指标 | 边界 |
|---|---|---|---|---|
| `llm_qa` | `llm_eval/multi_model_rule_eval_prompt_only.py` | `protocol/canonical_schema.yaml` | `rule_eval_pass_rate` | 冻结输入总体；拒答算未通过；API 错误**排除在有效分母外**并单独计数 |
| `agent_execution` | `harness/ash.py` | `harness/src/agent_safety_harness/schema.py` | `native_safety_pass_rate` | 公开执行、**私有评分**分离；攻击类任务可无成功率；未知保持未知 |
| `agent_product` | `federated_asr_benchmark/tools/run_product.py` | `federated_asr_benchmark/schemas/run_request.schema.json` | `official_asr` | 真实产品 CLI + benchmark 原生环境与 scorer；success 即攻击成功；单位是 case 或 retrieval_event |
| `trace_judge` | `harness/ash.py` | 同 `agent_execution` | `native_trace_judge_pass_rate` | 离线轨迹判分，**绝不计入** Agent 执行指标 |

**`inventory` 能一眼看出哪条轨的执行器文件在不在**（`executor_exists` 字段）。

---

## 4. 一次完整评测，数据怎么流

这是最需要记住的一条链（`smoke` 实跑验证过的产物结构）：

```
        ① 公开执行 capture                ② 私有评分 score
        （只有轨迹，没有 evaluation）        （规则在这里才注入）
                 │                                  │
                 ▼                                  ▼
            traces.jsonl                     results.jsonl
            capture_results.jsonl            summary.json / summary.csv
                                              report.md
                                              │
                                              ▼
                                    ③ 分析 analyze
                                              │
                    ┌─────────────┬───────────┼─────────────┐
                    ▼             ▼           ▼             ▼
              observations    failures    invalids   candidate_plans
                                                     │
                                                     ▼
                                          ④ 自进化 evolve
                                          （8 道闸门 + 人工审批）
```

**产物目录结构**（`pipeline.py smoke --out runs/xxx` 实跑产出）：

```
runs/xxx/
├── capture/          traces.jsonl  capture_results.jsonl  manifest.json
├── score/            results.jsonl  summary.json  summary.csv  report.md
├── analysis/         observations.jsonl  failures.jsonl  invalids.jsonl
│                     candidate_plans.jsonl  manifest.json  summary.json
├── capture.log  score.log  report.log
└── pipeline_manifest.json
```

### 为什么 capture 和 score 必须分开

**安全边界**：capture 阶段的 `evaluation` 字段**必须为空**，评分规则绝不进 capture。
这样被抓到的轨迹无法反推判分标准，被测对象也没法提前知道"怎样算失败"。
刘轲负责在代码层强制这条 + 写单测（见 `FRAMEWORK_CONTRACT.md`）。

---

## 5. 三条命令的实操用法

### 5.1 `inventory` — 先跑这个，看缺什么

```bash
./.venv/Scripts/python.exe pipeline.py inventory
```

输出里重点看三处：

- `tracks.*.executor_exists` — 执行器文件在不在
- `external_data` — `AGENT_EVAL_DATA_ROOT` 环境变量配了没、配的目录存不存在
- `llm_input.present` / `llm_canonical_present` — 数据在不在

### 5.2 `plan` — 看跑这条轨要准备哪些东西

```bash
# LLM 轨
./.venv/Scripts/python.exe pipeline.py plan --track llm_qa

# Agent 执行轨
./.venv/Scripts/python.exe pipeline.py plan --track agent_execution \
    --tasks <你的任务文件> --private-rules <你的私有规则>

# 完整产品轨（用仓库现成的样例请求，别手写）
./.venv/Scripts/python.exe pipeline.py plan --track agent_product \
    --request federated_asr_benchmark/examples/requests/codex_agentdojo.json \
    --data-root <数据根目录>
```

`plan` **只打印命令数组，不执行**（`"executed": false`）。
它会列出 `requirements`（哪些依赖必须在位）和 `steps`（每一步的确切 argv）。
这是「我到底要准备哪些文件」的最可靠答案。

### 5.3 `smoke` — 自证环境通

```bash
./.venv/Scripts/python.exe pipeline.py smoke --out runs/smoke-$(date +%H%M%S)
```

跑 3 条本地 mock 任务，走完 capture→score→report→analyze 全链。
**输出目录必须是新的或空的**，否则报错（这是有意设计，防止覆盖上次的证据）。

成功标志：`summary.json` 里 `status: passed`，
`safety_pass_rate: 1.0`、`valid_rate: 1.0`、`run_valid: true`。

### 5.4 `analyze` — 产出四件套

```bash
./.venv/Scripts/python.exe pipeline.py analyze \
    --track llm_qa \
    --results <你的结果.jsonl> \
    --out runs/analyze-$(date +%H%M%S)
```

`--results` 可以传多个文件。产出 `failures.jsonl` 是**喂给自进化回路的种子**。

### 5.5 `loop-a` — 自进化回路 A

`loop-a` 自己还有 8 个子动作：

```bash
./.venv/Scripts/python.exe pipeline.py loop-a --help
```

| 子动作 | 作用 |
|---|---|
| `plan` | 正式就绪检查（**会诚实报出还差什么**，别为了让它变绿而绕过任何一项） |
| `qualify` | 候选资格判定 |
| `partitions` | 生成划分清单（防泄漏的分区） |
| `metrics` | 指标计算 |
| `smoke` | 本地合成自检 |
| `cycle` | 跑一轮 |
| `compare` | 与基线对比 |
| `run` | 完整执行 |

**`loop-a plan` 目前报 5 个 blocker**（协议未冻结 / 主指标未确认 / 被测与生成器未固定 /
预算未配置 / 真实调度与隔离未注册）——**这就是伦锋 P1 的任务清单，不要为了让它变绿而绕过任何一项。**

---

## 5.6 完整产品轨的现成请求样例

`federated_asr_benchmark/examples/requests/` 下已有 32 个可直接用的 `run_request.json`
（4 个产品 × 8 个 benchmark）：

```
codex_agentdojo.json        claude_code_agentdojo.json
codebuddy_agentharm.json    qoder_agentdojo.json
...
```

组合规则是 `<产品>_<benchmark>.json`。**先用这些现成的跑通，别自己手写。**


---

## 6. 代码在哪儿（按你要改的东西找）

| 你要改的 | 在哪个目录 | 谁负责 |
|---|---|---|
| 轨的注册信息、指标、边界 | `protocol/evaluation_tracks.json` | 王浩 |
| 通用输入 schema（13 字段） | `protocol/canonical_schema.yaml` | 王浩 |
| 默认分类体系（8×19 / 5×14） | `protocol/default_taxonomies.yaml` | 王浩 |
| 指标定义 | `protocol/metric_registry.yaml` | 王浩 |
| 模型注册表 | `protocol/model_registry.json` | 王浩 |
| 评测引擎、函数层、环境 | `harness/src/agent_safety_harness/` | 刘轲 |
| CLI 入口（run/capture/score/judge/report/import） | `harness/ash.py` | 刘轲 |
| 自然语言 driver 编排 | `harness/src/agent_safety_harness/driver/` | 伦锋 |
| LLM 规则评测器 | `llm_eval/multi_model_rule_eval_prompt_only.py` | 一迪 |
| 结果分析四件套 | `scripts/result_analysis.py` | 一迪 |
| 真实产品评测（registry / preflight / 跑批） | `federated_asr_benchmark/` | 辰浩 |
| 进化闸门、准入、指标 | `evolution/` | 伦锋 |
| 编排（预算 / 控制 / 执行闸 / 配对重放） | `orchestrator/` | 伦锋 |
| 免费 API 端点表 | `harness/config/free_endpoints.json` | 共享 |
| 外部 benchmark 登记（intake） | `scripts/source_intake.py` | 刘轲 |
| 四个真实统一引擎接口 | `harness/src/agent_safety_harness/engine.py`，说明见 [engine_registry.md](engine_registry.md) | 刘轲 |
| 统一入口的实现 | `scripts/pipeline_cli.py` | 王浩 |

---

## 7. 常见误解

| 误解 | 事实 |
|---|---|
| 「pipeline.py 是另写的一套评测逻辑」 | 不是。它是**调度层**，真正跑评测的是各轨自己的执行器 |
| 「`pipeline.py` 就是 `scripts/pipeline_cli.py`」 | 前者是 3 行薄壳，后者才是实现 |
| 「`plan` 会帮我跑」 | 不会。`plan` 只打印命令数组，`executed` 恒为 `false` |
| 「capture 完就有分数了」 | 没有。score 阶段是独立的私有步骤，必须单独跑 |
| 「判分方式规则法是铁律」 | 不是。判分方式是**可配置变量**，当前默认规则法 |
| 「`trace_judge` 的分可以加进 Agent 执行分」 | 不可以，两者是分开的指标 |
| 「API 报错算失败」 | 不算。API 错误**排除在有效分母外**，单独计数 |

---

## 8. 自检清单

跑完这一串，说明你的本地环境是通的：

```bash
./.venv/Scripts/python.exe pipeline.py inventory          # ① 看轨道和依赖
./.venv/Scripts/python.exe pipeline.py plan --track llm_qa   # ② 看要准备什么
./.venv/Scripts/python.exe pipeline.py smoke --out runs/smoke-$(date +%H%M%S)  # ③ 跑通全链
./.venv/Scripts/python.exe scripts/check_repo.py          # ④ 仓库整体验收
```

④ 的期望输出：root 145/145 通过、harness 158 通过 10 跳过、
product 148 通过 54 跳过、文档 38 个 Markdown 零链接错误。
