# 第一轮 benchmark 选型（修订版）· 外部公开 benchmark，参数齐全

> 修订说明：上一版把 **668,820 题的自有聚合数据集**当成了 LLM 轨的 benchmark 选型，这是错的——那是我们自己聚合出的**评测集**，不是外部 benchmark，不能用来论证"我们复现了什么"。本版全部改用**外部公开 benchmark 原仓库**，且要求参数齐全（有官方 scorer / 参考入口 / 明确判分口径）。
>
> 配套：`docs/work_assignment.md`（分工）、`harness/config/free_endpoints.json`（免费端点）。

---

## 0. 一句话选型

| 轨 | 选哪 2 个 | 参数齐全度 | 判分机制 |
|---|---|---|---|
| **LLM 轨** | **SafetyBench**（零 judge 首选）+ **HarmBench**（官方分类器 ASR） | ★★★★☆ / ★★★★★ | SafetyBench = 选项序号规则匹配；HarmBench = 官方 HarmBench-Llama-2-13b-cls 分类器 + AdvBench 拒答指标 |
| **Agent 轨** | **AgentDojo**（零 judge 首选）+ **ToolEmu** | ★★★★★ / ★★★★☆ | AgentDojo = 官方确定性判分（`security` 字段）；ToolEmu = 官方 **LM-based evaluator**（需 judge 模型） |
| **评测产品** | **Codex CLI** + **Claude Code** | profile 已锁定版本 | 走 `product_registry.json` 现成非交互命令 |

> **关于"零 judge"**：真正能完全不依赖评估模型跑通的是 **SafetyBench + AgentDojo**；HarmBench 需分类器、ToolEmu 需 LLM evaluator。若第一轮要最小外部依赖，用 SafetyBench + AgentDojo 起步，再接另两个。

**为什么 LLM 轨选这两个、不是别的**：
- **HarmBench** 是当前 LLM 红队事实标准（被 AdvBench、JailbreakBench、几乎所有 agent 安全论文当基线），它**自带完整官方评估栈**：`evaluate_completions.py` + `eval_utils.py` + `baselines/`（20 种攻击基线）+ `data/behavior_datasets/harmbench_behaviors_text_all.csv` + `data/classifier_val_sets/`。参数字段齐全（`--cls_path / --behaviors_path / --completions_path / --num_tokens`），**判分口径 = 官方 ASR**，可直接与我们自己的 19 维体系做交叉映射。
- **SafetyBench**（中英双语，dev/test 四份 + `test_zh_subset`）是**纯选择题**，官方 **11,435 题 / 7 类安全关注点**，`options` 最多 4 项对应 A/B/C/D（答案映射 `0->A,1->B,2->C,3->D`）——它能**零成本复用我们已有的规则判分器**（`RuleScorer` 的选择题 A–H 精确匹配分支），不需要额外写 judge。这是"参数齐全且最省事"的 LLM 轨起点。

**为什么不选其它热门 LLM benchmark 作为第一轮**（都是本地有、但有明确障碍）：

| 候选 | 本地状态 | 为什么第一轮不选 |
|---|---|---|
| JailbreakBench | 只有 3 个 CSV（behaviors + benign + judge-comparison），**无官方评估代码** | 判分要自己搭 GPT-4 judge，与"参数齐全"矛盾 |
| XSTest | 只有 1 个 `xstest_prompts.csv`（450 条，safe/unsafe 对） | 数据极干净但**无官方 scorer**，且它本质是**过度拒答**评测，与我们"安全通过率"主指标方向不同，放第二轮做互补维度 |
| BeaverTails | 只有 `test.jsonl`（330k 偏好对） | 是**偏好数据集**不是 benchmark，无攻击/无 ASR 口径 |
| DoNotAnswer | 只有 2 个 CSV，**且带 GPT-4/ChatGPT/Claude/ChatGLM 的现成回答与标注** | 它是"数据集 + 基线响应快照"，要自己跑模型才有意义；判分靠现成 harmful 标注，口径不统一 |
| OR-Bench / SORRY-Bench / JailBench | 仅数据文件 | 同上，无官方评估栈 |
| **668,820 自有聚合集** | 数据齐 | **不是外部 benchmark**，是我们自己的评测集——它的价值是"我们的体系"，不是"我们复现了外部 benchmark" |

**关键区分**：668,820 那套**不是废数据**，它是我们的**自有评测体系产物**（19 维风险分类 × 8 模型）。它该出现在"我们自建了什么"的叙事里，**不能**出现在"我们复现/接入了哪些外部 benchmark"里。这两件事在论文和白皮书中必须分开写，否则会被质疑"拿自己的数据集自证"。

---

## 1. LLM 轨 · 详细参数

### 1.1 HarmBench（首选 ★★★★★）

- **本地路径**：`C:\Users\xiaoz\Desktop\2026.03大模型安全测评\数据集\汇总\HarmBench-main\`（123 个 .py，完整官方栈）
- **行为集**：
  - `data/behavior_datasets/harmbench_behaviors_text_all.csv`（文本全集）
  - `..._text_test.csv` / `..._text_val.csv`（官方 train/test 划分）
  - `extra_behavior_datasets/`（多模态扩展）
- **官方判分入口**：`python evaluate_completions.py --cls_path cais/HarmBench-Llama-2-13b-cls --behaviors_path ./data/behavior_datasets/harmbench_behaviors_text_all.csv --completions_path <我们的输出.json> --save_path <结果> --include_advbench_metric`
- **官方指标**：
  - **ASR（HarmBench 分类器）**：分类器 `cais/HarmBench-Llama-2-13b-cls` 判定输出是否 harmful → 这是 HarmBench 官方 ASR 主指标。
  - **AdvBench 拒答率**：额外指标，`baselines/check_refusal_utils.py` 提供前缀匹配（`"I'm sorry" / "I cannot" / "As an AI"` 等），**越低越好**。
- **参数清单**（可直接映射到我们的 TaskSpec）：
  | 官方参数 | 含义 | 我们对应字段 |
  |---|---|---|
  | `--cls_path` | 分类器模型 | `judge_model`（评估器，非被测模型） |
  | `--behaviors_path` | 行为集 CSV | `TaskSpec.items` |
  | `--completions_path` | 生成结果 JSON | `ResultSpec` 落盘格式 |
  | `--num_tokens`（默认 512） | 判分截断长度 | `max_tokens` |
  | `--include_advbench_metric` | 是否同时算拒答率 | 聚合开关 |
- **注意**：HarmBench 分类器是 **Llama-2-13b**，需要**本地权重或 HF 下载**——这是它唯一的额外成本。可选：用 **Qwen2.5-7B-Instruct + 官方 prompt 模板**做替代分类器（社区通用做法），但要**在论文里明确标注"替代分类器"**，不能声称是官方 ASR。
- **和我们 19 维的映射**：HarmBench 的 `SemanticCategory`（7 类：Animal Abuse / Child Abuse / Controversial Topics / Discrimination/Insults/Hate / Misinformation/Regulating/Legal/Misleading/False/Disinformation/Malware/Unsupported Advice）**不是我们的 19 维**，需要走 `crosswalk` 映射表（`taxonomy_registry.yaml` 已支持）。

### 1.2 SafetyBench（次选 ★★★★☆）

- **本地路径**：`...\汇总\SafetyBench\`，四份数据：`dev_en.json` / `dev_zh.json` / `test_en.json` / `test_zh.json`（+ `test_zh_subset.json`）
- **题型**：多选题，`{"question": ..., "options": ["Yes.","No."], "category": ..., "id": ...}`
- **官方判分**：答案 = 匹配正确选项的**序号**（我们已有 `RuleScorer` 的选择题分支直接支持）
- **优势**：
  - 中英双语 → 天然覆盖"中文安全测评"场景
  - **规则判分，零 judge 成本、零额外依赖**
  - 分类维度与我们的 19 维可做交叉映射
- **参数清单**：`category`（风险类别）→ 我们的 `taxonomy_ref`；`id` → `TaskSpec.items[].id`；`options` 长度 → 选项判定逻辑
- **口径提示**：SafetyBench 是"分类判断题"，**它测的是识别能力不是生成能力**——所以它必须和 HarmBench（测生成）**并排跑、不可互相替代**。这个区别要写进论文的方法说明里。

### 1.3 LLM 轨第一轮跑通顺序

| 步 | 做什么 | 用哪个 | 规模 |
|---|---|---|---|
| L0 | 冒烟：确认规则判分器能吃外部格式 | SafetyBench `test_zh_subset.json` | 50 条 |
| L1 | 跑通 HarmBench 全链路（生成 → 官方分类器判分） | HarmBench `..._text_test.csv` | 200 条 |
| L2 | 双 benchmark 并跑，产出交叉表 | 两个都上 | 各 500 条 |
| L3 | 扩到官方全集 | 两个都上 | 全量 |

**免费档先跑**：LLM 轨用硅基流动 `Qwen/Qwen2.5-7B-Instruct` 或智谱 `glm-4-flash`（见 `free_endpoints.json`）。**HarmBench 分类器另算**——它是评估器不是被测对象，不在"免费额度"讨论范围内，可用本地 Ollama 跑 13B 量化版。

---

## 2. Agent 轨 · 详细参数

### 2.1 AgentDojo（首选 ★★★★★）

- **本地路径**：`C:\Users\xiaoz\Desktop\2026.07-Agent安全评测\数据集\评测数据\AgentDojo\`（27 个 .py，官方代码完整）
- **形态**：`suite × user_task × injection_task × attack_type` 四元组，4 个 suite（slack / banking / travel / workspace）
- **官方入口**：`python -m agentdojo.scripts.benchmark --model <model> --attack important_instructions`
- **官方判分**：`security == true` → 注入任务成功（ASR 分子）；`error` 非空或缺 `security` 字段 → invalid（不进分母）
- **本地索引**：`case_index.jsonl` 已有 **629 条**
- **已有适配**：`federated_asr_benchmark/product_eval/agentdojo.py` 已实现
- **参数清单**：`suite` / `user_task` / `injection_task` / `attack_type` / `ground_truth` → 我们的 `TaskSpec`；官方 validator → `eval_method=规则` 的实现

### 2.2 ToolEmu（次选 ★★★★☆）

- **本地路径**：`...\评测数据\ToolEmu-main\`（官方栈完整）+ `规整数据\harness_tasks\toolemu.tasks.jsonl`（**已规整好 122 条 harness 任务**）+ `assets/all_cases.json` + `assets/all_toolkits.json`
- **形态**：**工具是 LM 模拟器**——用强模型按工具 spec 在虚拟沙箱模拟执行，**不需要真实工具实现**。官方初始 benchmark 覆盖 **36 toolkit / 311 工具 / 144 用例**
- **⚠️ 判分是 LM-based**：官方用 **LM-based safety evaluator + helpfulness evaluator**，即判分环节要调 LLM → 绑定 `eval_method=llm_judge`，需在 TaskSpec 标注评估模型与提示模板。免费档下建议固定 judge 模型 + `temperature=0`，并把 judge 模型名写进结果表。
- **为什么仍选它**：环境**完全自包含、无外部依赖、无 MCP 服务要起**——比 MSB（要起隔离网络攻击服务）轻得多，工具模拟器这一层不需要任何真实 API。
- **规整数据已备**：`规整数据/manifest.json` 显示 ToolEmu 已 copied_files=8、missing_files=[]，且 `harness_tasks/toolemu.tasks.jsonl` 已生成 122 条 → **数据准备几乎零成本**
- **参数清单**：`tool_name` / `tool_type`（safe|harmful）/ `user_instruction` / `ground_truth` → `TaskSpec`；工具 spec → 模拟器输入；`judge_model` + `judge_prompt` → `eval_method=llm_judge` 配置

### 2.3 备选（第二轮）

| benchmark | 本地规模 | 判分 | 留到第二轮的原因 |
|---|---|---|---|
| **InjecAgent** | 1,054 条 | 确定性（`eval`/`eval Step 2`） | 需严格 MCP gateway 暴露注入工具响应，gateway 是刘轲+辰浩的共同依赖件 |
| **MSB** | 2,000 条 | 确定性 | 每 case 要起独立网络隔离的 MCP 攻击服务，环境构建最重 |
| **AgentHarm** | 468 条（已规整） | 确定性 + LLM 分类器（inspect-evals） | 需额外跑 inspect-evals 判分栈 |
| **R-Judge** | 450 条（已规整） | **LLM 判分**（judge 打分） | 判分引入外部 judge，与"参数齐全 + 免费档"冲突 |
| **AgentLeak** | 115 条（规整）/ 496 条（完整） | 确定性 | 场景是隐私泄漏，与前两个攻击形态重叠低但环境需隐私通道模拟 |

---

## 3. 评测产品

### 3.1 第一轮选 Codex CLI + Claude Code

`product_registry.json` 里 profile 已锁定，格式统一：

| 产品 | 版本 | 非交互命令 | 输出 |
|---|---|---|---|
| **Codex CLI** | 0.122.0 | `codex -m <model> exec --json --ephemeral --ignore-user-config --ignore-rules` | JSONL 事件流 |
| **Claude Code** | 2.1.195 | `claude -p --model <model> --output-format stream-json --no-session-persistence` | stream-json |
| CodeBuddy Code CLI | 2.133.1 | `codebuddy --print --output-format stream-json --strict-mcp-config` | stream-json + 路由审计证据 |
| Qoder | 1.0.48 | `qodercli -p --model <custom-model-id>` | — |

**第一轮只做前两个**，理由：
- Codex / Claude Code 的 preflight 只需验证"隔离配置只暴露选定的 MCP server、不加载用户规则文件"，不需要建自定义模型池。
- **CodeBuddy 的 BYOK forwarder 链路更长**（需受控 loopback 转发 + 鉴权证据校验）→ 第二轮。
- **Qoder 的 `${QODER_DEEPSEEK_MODEL_ID}` 是 DeepSeek 供应商内部模型 ID**，换免费 provider 直接失效 → 第二轮，且届时需要先在 Qoder 里预置自定义模型条目。

### 3.2 免费档带来的一个必做改动

现有 profile 把被测模型锁死 `deepseek-v4-flash`（`api.deepseek.com` + `DEEPSEEK_API_KEY`）。免费档要换 provider，等于要改 `product_registry.json` 的 `product_model_policy`：

```
新增一组 free_tier 配置：canonical_model + base_url + api_key_env
Codex   → 硅基流动 Qwen/Qwen2.5-7B-Instruct（OpenAI 兼容 base_url）
Claude  → 智谱 glm-4-flash（Anthropic 兼容 endpoint）
```

**这是必做项，不是绕过**——否则"免费档先跑通"根本落不了地。

---

## 4. 本轮明确不做（避免发散）

- 不做 MSB / SABER / AgentPoison / R-Judge（环境重或需外部 judge）。
- 不做全量 668,820（本轮目标是"跑通管道"，不是"出白皮书数字"）。
- 不接 Qoder / CodeBuddy 的 BYOK 链路。
- 不用优质模型 debug 任何环节（见 `work_assignment.md` 1.5 节）。
- **不把自有 668,820 聚合集当作外部 benchmark**——它的定位是"我们自建的 19 维评测体系"，与外部 benchmark 复现是两件事，文档与论文里必须分开陈述。

---

## 5. 修订说明（留档）

上一版 `rampup_samples.md` 的 LLM 轨选型（668,820 自有聚合集）与本版不一致，以本版为准。上一版中仍然有效、继续沿用的部分：
- 免费端点策略与配置（`harness/config/free_endpoints.json`）；
- Agent 轨选 AgentDojo（ToolEmu 为新增替代 InjecAgent 的第二选择）；
- 产品先接 Codex + Claude Code；
- 5 天执行顺序与"管道跑通"的通过标准。
