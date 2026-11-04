# Benchmark 获取指南（agent-safety-harness）

> 配套：`docs/rampup_samples.md`（选型与参数）、`docs/work_assignment.md`（分工）、`harness/config/free_endpoints.json`（免费端点）。
> 本文只解决一件事：**第一轮要用的 4 个外部 benchmark，从哪下、下到哪、怎么让 harness 认到、怎么验证拿到了。**
> 所有上游地址均从本地已获取的仓库 `git remote` 与 `README` 提取，非凭记忆书写。

---

## 0. 三分钟速查表

| benchmark | 轨 | 上游地址 | clone 后的目录名 | 本机已有位置 |
|---|---|---|---|---|
| **HarmBench** | LLM | `https://github.com/centerforaisafety/HarmBench` | `HarmBench-main/` | `2026.03大模型安全测评\数据集\汇总\HarmBench-main\` |
| **SafetyBench** | LLM | `https://github.com/thu-coai/SafetyBench` | 数据在 HuggingFace（`download_data.sh`） | `2026.03大模型安全测评\数据集\汇总\SafetyBench\` |
| **AgentDojo** | Agent | `https://github.com/ethz-spylab/agentdojo` | `agentdojo/` | `2026.07-Agent安全评测\数据集\评测数据\AgentDojo\` |
| **ToolEmu** | Agent | `https://github.com/ryoungj/ToolEmu` | `ToolEmu/` | `2026.07-Agent安全评测\数据集\评测数据\ToolEmu-main\` |

> **判分机制速览**（决定第一轮能否零 judge 跑通）：
> - **HarmBench**：LLM 分类器判 ASR（评估器要跑模型）
> - **SafetyBench**：**纯规则判分**（选项序号匹配）→ 零 judge 成本
> - **AgentDojo**：**官方确定性判分**（`security` 字段）→ 零 judge 成本
> - **ToolEmu**：官方为 **LLM-based evaluator** → 需配 judge 模型（`eval_method=llm_judge`）
>
> 也就是说：**真正能零 judge 跑通的是 SafetyBench + AgentDojo**；HarmBench 与 ToolEmu 各需要一个评估模型。若第一轮要完全避免 judge 依赖，用 SafetyBench + AgentDojo 起步。

---

## 1. 获取方式：两条路

### 路径 A：本机已有 → 直接登记（最快，推荐第一轮用这条）

四个 benchmark 在本机都已有完整副本（见上表最后一列）。**不用重新下载**，只需把路径登记进 harness 的配置，让 `source_intake.py` 扫到它们。

### 路径 B：换机器 / 干净环境 → 从上游拉

```bash
# 建议放在仓库同级或统一的数据根目录，不要塞进 git 仓库
mkdir -p ~/agent-safety-data
cd ~/agent-safety-data

git clone https://github.com/centerforaisafety/HarmBench.git
git clone https://github.com/thu-coai/SafetyBench.git
git clone https://github.com/ethz-spylab/agentdojo.git
git clone https://github.com/ryoungj/ToolEmu.git

# SafetyBench 的测试题在 HuggingFace，官方提供下载脚本
cd SafetyBench && bash download_data.sh
```

**为什么不放进仓库**：这些是第三方数据集（含各自 LICENSE），体积大、许可各异，混进本仓库会污染提交历史并可能违反再分发条款。仓库里只放**配置与索引**，不放数据本体。

---

## 2. 目录约定（harness 怎么找到数据）

harness 的数据根目录由 `--benchmark-root` 指定，**没有硬编码路径**。约定：

```
<DATA_ROOT>/
├── HarmBench-main/
│   └── data/behavior_datasets/harmbench_behaviors_text_all.csv
├── SafetyBench/
│   ├── test_en.json / test_zh.json / dev_en.json / dev_zh.json
├── AgentDojo/          (或 agentdojo-main/)
└── ToolEmu-main/       (或 ToolEmu/)
```

`source_intake.py` 会递归扫描 `--benchmark-root` 下所有数据文件，自动推断 schema、映射到 canonical 字段、算 sha256、产出 catalog。

---

## 3. 登记与验证：一条命令

### 3.1 跑 intake

```bash
cd <repo-root>
python scripts/source_intake.py \
  --benchmark-root "<DATA_ROOT>" \
  --protocol-dir protocol \
  --output-dir work/phase1 \
  --format json
```

产出（在 `work/phase1/`）：

| 文件 | 作用 |
|---|---|
| `dataset_catalog.json` | 每个 benchmark 的目录：源路径、sha256、行数、字段映射、canonical schema |
| `source_inventory.csv` | 全量资产清单（含证据文件） |
| `field_mapping_report.json` | 字段映射完整性报告（`ready` / `needs_review` / `failed`） |
| `manifests/pending/*.pending.yaml` | **字段映射不完整时**自动生成待填清单：给出建议映射 + 缺失字段 + 待填位置 |

**关键**：`needs_review` 不等于失败——它表示"数据读到了，但必填字段映射需要人确认一次"。把 `pending.yaml` 里的映射填好再重跑即可。

### 3.2 校验 schema

```bash
python scripts/canonical_validate.py --help   # 按实际参数填
```

### 3.3 判定"拿到手了"的标准

一份 benchmark 算**可用**，必须同时满足三条：

1. `dataset_catalog.json` 里 `status == "ready"`（字段映射完整）；
2. `source.sha256` 与 `source.row_count` 已被记录（**可复现的依据**）；
3. 官方参考入口能跑通一次（见下节各 benchmark 的官方命令）。

---

## 4. 各 benchmark 的官方参考入口（判分口径必须用官方）

### 4.1 HarmBench（LLM 轨首选）

- **获取**：`git clone https://github.com/centerforaisafety/HarmBench` → 目录 `HarmBench-main/`
- **行为集**：`data/behavior_datasets/harmbench_behaviors_text_all.csv`（另有 `_text_test.csv` / `_text_val.csv` 官方划分、`extra_behavior_datasets/` 多模态扩展）
- **生成 + 判分（官方脚本，参数名已核对官方源码）**：
  ```bash
  python generate_completions.py \
    --model_name <模型名> \
    --behaviors_path data/behavior_datasets/harmbench_behaviors_text_all.csv \
    --save_path completions.json \
    --max_new_tokens 256

  python evaluate_completions.py \
    --cls_path cais/HarmBench-Llama-2-13b-cls \
    --behaviors_path data/behavior_datasets/harmbench_behaviors_text_all.csv \
    --completions_path completions.json \
    --save_path results.json \
    --include_advbench_metric
  ```
  > `generate_completions.py` 的官方参数是 `--model_name`（不是 `--model_path`），且默认走 `configs/model_configs/models.yaml`；用 vLLM 本地推理需加 `--generate_with_vllm`。判分脚本 `evaluate_completions.py` 的 `--num_tokens` 默认 512（判分截断长度，与生成的 `--max_new_tokens` 是两回事）。
- **官方指标**：
  - **ASR**：`cais/HarmBench-Llama-2-13b-cls` 分类器判定 harmful → 官方主指标（越低越好）
  - **AdvBench 拒答率**：`baselines/check_refusal_utils.py` 前缀匹配（`"I'm sorry"` / `"I cannot"` / `"As an AI"` 等），额外指标（越低越好）
- **依赖**：`pip install -r requirements.txt`（含 `transformers` / `vllm` / `accelerate`）
- **⚠️ 分类器成本**：官方分类器是 Llama-2-13b，需本地权重或 HF 下载。**若改用 Qwen2.5-7B 等替代分类器，必须在结果表与论文里标注"替代分类器"**，不能声称是官方 ASR。
- **攻击基线**：`baselines/` 含 20 种（autodan / gcg / pair / tap / pap / pez / uat / fewshot / zeroshot / direct_request 等）——复现攻击时直接用，不要自己实现。

### 4.2 SafetyBench（LLM 轨次选，零 judge 成本）

- **获取**：`git clone https://github.com/thu-coai/SafetyBench` → **测试题在 HuggingFace**，官方提供 `download_data.sh` / `download_data.py`（下载 test_zh / test_en / test_zh_subset + few-shot 示例）
- **数据文件**：`test_en.json` / `test_zh.json` / `dev_en.json` / `dev_zh.json`（另有 `test_zh_subset.json`）
- **规模与类别**：官方 **11,435 道多选题**，覆盖 **7 类安全关注点**（Offensiveness / Unfairness and bias / Physical health / Mental health / Illegal activities / Ethics and morality / Privacy and property），中英双语
- **题型结构**：`{"question": ..., "options": [...最多 4 项 A/B/C/D...], "category": ..., "id": ...}` — 纯多选题
- **官方判分**：答案 = 正确选项序号，**映射规则 `0->A, 1->B, 2->C, 3->D`**；官方评测代码见仓库 `code/` 与 `eva.py`（2025-07 起官方已开源测试答案）。→ **我们已有的 `RuleScorer` 选择题分支直接支持，无需额外写 judge**
- **官方实验设定**：zero-shot 与 five-shot（`dev_*` 各 5 例/类作 few-shot 示例）→ 我们的 TaskSpec 要显式记录 `shot` 设置，不同 shot 的结果不可混写
- **口径提示**：SafetyBench 测的是**安全理解/识别能力**，不是生成能力 → 必须与 HarmBench（测生成）**并排跑、不可互相替代**。这一点要写进论文方法说明。
- **依赖**：无（纯数据 + 规则判分）；提交榜单格式为 `{"<id>": <选项序号>}`

### 4.3 AgentDojo（Agent 轨首选）

- **获取**：`git clone https://github.com/ethz-spylab/agentdojo` → 目录 `agentdojo/`
- **官方入口**（click CLI，选项名已核对官方源码）：
  ```bash
  python -m agentdojo.scripts.benchmark \
    --model <模型> \
    --attack important_instructions \
    --suite slack \
    --logdir runs/
  ```
  > 常用选项：`--model` / `--model-id` / `--attack` / `--defense` / `--suite` / `--user-task` / `--injection-task` / `--logdir` / `--benchmark-version` / `--max-workers` / `--force-rerun`。可重复 `--suite` 跑多个 suite。
- **官方判分**：`security == true` → 注入任务成功（ASR 分子）；`error` 非空或缺 `security` 字段 → invalid（**不进分母**）
- **形态**：`suite × user_task × injection_task × attack_type` 四元组；4 个 suite = **slack / banking / travel / workspace**（目录在 `src/agentdojo/data/suites/`）
- **规模**：`federated_asr_benchmark/case_index.jsonl` 已索引 **629 条**
- **已有适配**：`federated_asr_benchmark/product_eval/agentdojo.py` 已实现（`product_adapter` 字段描述了如何把官方 suite 工具包成严格 MCP server 暴露给被测系统）
- **依赖**：`pip install -e .`（本仓库自带）；注意官方 runner 需要能调被测模型

### 4.4 ToolEmu（Agent 轨次选，环境自包含但判分要 LLM）

- **获取**：`git clone https://github.com/ryoungj/ToolEmu` → 目录 `ToolEmu/`
- **形态**：**工具是 LM 模拟器**——ToolEmu 用一个强模型（如 GPT-4）按工具 spec 在虚拟沙箱里模拟执行，**不需要真实工具实现**。官方初始 benchmark 覆盖 **36 个 toolkit / 311 个工具 / 144 个测试用例**
- **⚠️ 判分机制（重要，别当成规则判分）**：官方提供 **LM-based safety evaluator** 与 **LM-based helpfulness evaluator** ——即**判分环节要调 LLM**，不是确定性规则。
  - 因此 ToolEmu 与 `eval_method=llm_judge` 的配置绑定，需在 TaskSpec 显式标注评估模型与提示模板；
  - **免费档下 judge 模型会引入额外噪声**，建议：跑通管道阶段用固定 judge 模型 + 固定 temperature=0，并把 judge 的模型名写进结果表；
  - 若要完全确定性，得自己写规则判分替代官方 evaluator——那就**不是官方口径**了，需明确标注。
- **本机资产**：`assets/all_cases.json` + `assets/all_toolkits.json`（完整工具与用例）；`2026.07-Agent安全评测\数据集\规整数据\harness_tasks\toolemu.tasks.jsonl`（**122 条**，manifest 显示 `copied_files=8 / missing_files=[]`）
- **优势仍在**：环境完全自包含，**不用起任何外部服务**，比 MSB（要起隔离网络攻击服务）轻得多
- **依赖**：`pip install -r requirements.txt`；judge 模型需可调用（走 `api_key_env`）

### 4.5 备选（第二轮再取）

| benchmark | 上游 | 本机规模 | 留到第二轮的原因 |
|---|---|---|---|
| InjecAgent | `https://github.com/uiuc-kang-lab/InjecAgent` | 1,054 条 | 需严格 MCP gateway 暴露注入工具响应（刘轲 + 辰浩共同依赖件） |
| MSB | 见官方仓库 | 2,000 条 | 每 case 要起独立网络隔离的 MCP 攻击服务，环境最重 |
| AgentHarm | `https://github.com/AgentHarm/AgentHarm` | 468 条（已规整） | 需额外跑 inspect-evals 判分栈 |
| R-Judge | `https://github.com/amazon-science/R-Judge` | 450 条（已规整） | **LLM 判分**，引入外部 judge，与"免费档 + 参数齐全"冲突 |
| AgentLeak | 见官方仓库 | 115 条（规整）/ 496 条（完整） | 需隐私通道模拟环境 |

---

## 5. 免费 API 端点的获取方式

配置模板：`harness/config/free_endpoints.json`（11 个 profile，全部 OpenAI 兼容 → **零代码接入**，复制进 `harness/config/models.json` 即可）。

### 5.1 国内优先

| 渠道 | 注册地址 | 拿 key 路径 | 免费政策（2026-11 核实） |
|---|---|---|---|
| **智谱 BigModel** | `https://open.bigmodel.cn/` | 注册 → 实名 → 控制台「API Key」→ 新建 key | 新用户 2000 万 token（**永久有效**）；**GLM-4-Flash / GLM-4.7-Flash 永久免费不限调用量**（30 并发/QPS 限速） |
| **硅基流动 SiliconFlow** | `https://cloud.siliconflow.cn/` | 注册 → 实名 → 控制台「API Key」→ 新建 | 新用户 2000 万 token；**9B 及以下参数模型永久免费不限量**（仅 RPM 限速） |
| **百度千帆** | `https://console.bce.baidu.com/qianfan/` | 实名 → 创建 AK/SK | ERNIE-Speed / Lite 永久免费不限量；主流模型每模型 100 万 token / 3 个月 |
| **阿里云百炼** | `https://dashscope.aliyun.com/` | 实名 → 开通服务 → API Key | 每模型 100 万 token / 3 个月（可叠加） |
| **火山引擎豆包** | `https://console.volcengine.com/ark/` | 实名 → 开通 → 创建推理接入点 | 每日 200 万 token；**需手动开通模型** |
| **ModelScope 魔搭** | `https://modelscope.cn/` | 登录 → 访问令牌 | 54 个模型免费 |

### 5.2 国外（需代理）

| 渠道 | 注册地址 | 拿 key 路径 | 免费政策 |
|---|---|---|---|
| **Groq** | `https://console.groq.com/keys` | 邮箱注册 → 立即出 key（免卡、免手机验证） | 约 30 RPM / 14,400 RPD，永久 |
| **OpenRouter** | `https://openrouter.ai/keys` | 邮箱注册 → 出 key（免卡） | 模型名带 `:free` 后缀的约 35-40 个；约 200 RPD |
| **Google AI Studio** | `https://aistudio.google.com/api-keys` | Google 账号 → Get API Key（免卡） | Gemini 2.5 Flash 免费，约 10 RPM / 250 RPD；**1M 上下文**（免费方案唯一） |
| **NVIDIA NIM** | `https://build.nvidia.com/` | 邮箱注册（部分需手机验证） | 约 40 RPM 且**无每日 token 上限**，100+ 模型 |
| **GitHub Models** | 用 GitHub token 直调 | 无需额外注册 | 永久可用、限速；含 GPT-4.1 / o3 / 有限 Claude 额度 |
| **Cerebras** | `https://cloud.cerebras.ai/` | 注册 → 出 key | 免费，含 gpt-oss-120b |
| **本地 Ollama** | `https://ollama.com/download` | 装完 `ollama pull qwen2.5:7b-instruct` | **完全免费、数据不出机**；key 填任意非空串即可 |

### 5.3 三条必须知道的事实

1. **OpenAI 与 Anthropic 官方都没有免费 API 档**。市面上"免费 GPT key / 免费 Claude key"多为共享或泄露 key，随时限流失效——**只可用于本地冒烟，不可用于正式评测或论文数据**。
2. **免费档有数据政策差异**：Google AI Studio 免费档的输入可能被用于产品改进。正式出分用付费档更稳。
3. **免费额度 ≠ 免费并发**：智谱 30 并发、硅基流动 RPM 限速、Gemini 10 RPM。**全量跑必须分片 + 多机 + 断点续跑**——`llm_eval/multi_model_rule_eval_prompt_only.py` 已内置 `NUM_SHARDS` / `SHARD_INDEX` / `RESUME` / AIMD 自适应并发，直接用，别重造。

### 5.4 密钥怎么放（不写进仓库）

```bash
# Windows PowerShell / Git Bash 均可
export SILICONFLOW_API_KEY=sk-xxxx     # 或 setx SILICONFLOW_API_KEY sk-xxxx
export ZHIPU_API_KEY=xxxx.xxxx
export GROQ_API_KEY=gsk_xxxx
```

- 密钥**只以环境变量形式存在**，`api_key_env` 字段只写变量名（`free_endpoints.json` 与 `llm_roles.json` 均如此）。
- **禁止**把 key 写进任何 JSON / YAML / 提交进 git。
- CI 与他人复现时，只需设置同名环境变量即可。

---

## 6. 端到端验收（第一轮的"跑通"标准）

按顺序做完这 4 步，就算管道跑通：

| 步 | 命令 / 动作 | 通过标志 |
|---|---|---|
| 1 | `python scripts/source_intake.py --benchmark-root <DATA_ROOT> --protocol-dir protocol --output-dir work/phase1` | `dataset_catalog.json` 里 4 个 benchmark 全部 `status=ready`，sha256 与 row_count 已记录 |
| 2 | 把 `free_endpoints.json` 里选中的 profile 复制进 `harness/config/models.json`，设好环境变量 | `models.json` JSON 合法；环境变量已设 |
| 3 | 用免费档跑 SafetyBench 50 条 + HarmBench 200 条 | 出 `results.jsonl` / `summary.json` / `leaderboard.csv`，含 `passed` / `score_status` / `api_status` |
| 4 | 用免费档跑 AgentDojo + ToolEmu 各一小批（官方 runner） | 出官方口径结果（AgentDojo 的 `security` 字段、ToolEmu 的调用命中），能算出 ASR 与 valid 覆盖率 |

**验收不通过时的排查顺序**：① API 错误率（看 `api_status` 列）→ ② 字段映射（看 `field_mapping_report.json`）→ ③ 模型输出格式（看 `extracted_prediction`）→ ④ 才怀疑判分逻辑。

---

## 7. 本文档的维护纪律

- **上游地址**：改版本或换源时必须同步更新本文 + `federated_asr_benchmark/benchmark_registry.json` 的 `source_root`。
- **版本 pin**：每个 benchmark 首次接入后记录 commit SHA 或 release tag（`git rev-parse HEAD`），写进 `benchmark_registry.json`，保证可复现。
- **不许做的事**：不把第三方数据集提交进本仓库；不把自有 668,820 聚合集当成外部 benchmark；不用替代分类器冒充官方 ASR。
