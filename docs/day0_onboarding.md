# 第 0 天上手清单（分发当天照着做，2 小时内可自证跑通）

> 配套：`docs/work_assignment.md`（分工）、`docs/FRAMEWORK_CONTRACT.md`（契约）、`docs/rampup_samples.md`（选型）、`docs/benchmark_acquisition.md`（获取方式）。
> 本文只解决一件事：**你今天下午打开仓库，第一个小时到底敲什么命令、看到什么算成功、卡住了怎么办。**

---

## 0. 一分钟总览：你会碰到什么

| 现象 | 是 bug 吗 | 你该做什么 |
|---|---|---|
| `source_intake` 跑完报 `ready: 0 / needs_review: N` | ✅ **正常，是设计好的机制** | 打开生成的 `manifests/pending/*.pending.yaml`，填 `field_mapping`，再用 `--intake-config` 重跑 |
| `python -m agent_safety_harness.driver` 只打印 dry_run | ✅ 正常 | dry_run 是安全模式；真实调用要等 registry 的 stub 被替换（刘轲 P0） |
| 找不到 `data/canonical.jsonl` | ✅ 正常 | `data/` 默认不随仓，要自己从 benchmark 生成 |
| `import yaml` 报错 | ✅ 环境问题 | 用仓库自带 `.venv`：`./.venv/Scripts/python.exe`，**不要用系统 python** |

> **本机已实测**（2026-11-04）：`source_intake` 对 SafetyBench 实跑成功，产出 catalog + pending manifest + sha256；`driver` dry_run 端到端可跑；`pipeline_cli analyze` 参数已核实为 `--track/--results/--out`。

---

## 1. 环境准备（所有人，30 分钟）

```bash
# 1.1 clone
git clone <repo-url> && cd llm-safety-eval-research-pipeline

# 1.2 用仓库自带解释器（系统 python 缺 PyYAML，会在验收时失败）
./.venv/Scripts/python.exe -c "import yaml; print('deps OK')"

# 1.3 确认 driver 骨架能跑（期望：打印 4 步 dry_run，exit 0）
PYTHONPATH=harness/src ./.venv/Scripts/python.exe -m agent_safety_harness.driver
```

**自证成功**：看到 `bench_intake → build_sandbox → run_capture → analyze` 四个 `[ok]` 行。
**卡住**：报 `ModuleNotFoundError: yaml` → 换 `.venv`；报其它错 → 截图发王浩。

---

## 2. 刘轲 · 第一天要敲的命令

```bash
# 2.1 看现在 6 个 stub 长什么样（你 P0 就是填它们）
cat harness/src/agent_safety_harness/driver/registrations.py

# 2.2 看 adapter 注册表（你要接真 benchmark 的入口）
cat harness/src/agent_safety_harness/adapters/registry.py
cat harness/src/agent_safety_harness/adapters/base.py

# 2.3 拉 benchmark 并登记（这是 bench_intake 背后真实跑的脚本）
./.venv/Scripts/python.exe scripts/source_intake.py \
  --benchmark-root "<你的 benchmark 数据目录>" \
  --protocol-dir protocol \
  --output-dir work/phase1
```

**自证成功**：`work/phase1/` 下出现 `dataset_catalog.json` + `source_inventory.csv` + `reports/field_mapping_report.json`。
**注意**：`ready: 0 / needs_review: N` 是**正常**——它在等你填 mapping（见第 5 节通用流程）。

---

## 3. 辰浩 · 第一天要敲的命令

```bash
# 3.1 看现有产品 profile 和 benchmark registry（你要维护版本 pin 的地方）
cat federated_asr_benchmark/product_registry.json
cat federated_asr_benchmark/benchmark_registry.json

# 3.2 看 case 索引有多少条
wc -l federated_asr_benchmark/case_index.jsonl

# 3.3 preflight —— 不通过就不许跑，先跑这个
./.venv/Scripts/python.exe federated_asr_benchmark/tools/preflight.py --help

# 3.4 跑单个 case（run_product 的真实参数：--request 必填、--case-id 必填）
./.venv/Scripts/python.exe federated_asr_benchmark/tools/run_product.py --help
./.venv/Scripts/python.exe federated_asr_benchmark/tools/run_product_batch.py --help
```

**run_request 的结构**：看 `federated_asr_benchmark/schemas/run_request.schema.json`（这是唯一权威来源，仓库里没有现成样例 JSON，你要自己按 schema 造一份）。
**自证成功**：preflight 输出通过记录；`run_product.py --dry-run` 能解析你的 request。
**关于被测产品 CLI 的安装**：Codex CLI / Claude Code 等的**安装、登录、版本确认由你自己负责**——不需要等批准、不需要报备，装好后确认版本号与 `product_registry.json` 里 pin 的版本一致（Codex 0.122.0 / Claude Code 2.1.195 / Qoder 1.0.48）即可。这是你的工作项，不是等待外部依赖的阻塞理由。
**卡住**：preflight 报错指向版本不匹配 → 装对版本或更新 profile 的 pin（并说明理由）；指向凭证缺失 → 按 `product_registry.json` 的 BYOK 字段配置 `api_key_env`。

---

## 4. 一迪 · 第一天要敲的命令

```bash
# 4.1 看执行器的真实入口（**只有一个 --config 参数**，数据路径写在 config 里）
grep -n "add_argument" llm_eval/multi_model_rule_eval_prompt_only.py
cat assets/templates/run_config.yaml

# 4.2 配置骨架（照这个填，复制到自己的 run_config.yaml）
#   evaluator_config.input_jsonl  ← 指向你转换好的 jsonl
#   evaluator_config.models[]      ← base_url / api_key_env / model

# 4.3 看分析层
grep -n "IDENTITY_FIELDS" scripts/result_analysis.py
./.venv/Scripts/python.exe scripts/pipeline_cli.py analyze --help
```

**analyze 的真实参数**：`--track <llm_qa|agent_execution|agent_product|trace_judge>` `--results <一个或多个 jsonl>` `--out <输出目录>`。

**自证成功**：用 `assets/templates/run_config.yaml` 跑 50 条冒烟，`runs/` 下出 results jsonl；再 `pipeline_cli analyze` 产出 `failures.jsonl`。
**卡住**：`input_jsonl` 找不到 → 你需要先把外部 benchmark 转成 canonical 格式（第 5 节）。

---

## 5. 通用流程：外部 benchmark → canonical 格式（**一迪必读，其他人遇到同类需求照做**）

这是把外部 benchmark 喂进 harness 的**唯一正规路径**，不要自己写转换脚本。

```bash
# 5.1 登记（会自动算 sha256、生成 pending manifest）
./.venv/Scripts/python.exe scripts/source_intake.py \
  --benchmark-root "<benchmark 数据目录>" \
  --protocol-dir protocol \
  --output-dir work/phase1

# 5.2 打开生成的 pending manifest，把 null 填上
#     work/phase1/manifests/pending/<benchmark>.pending.yaml
#     填 field_mapping: 你的源字段名 → canonical 字段名
#     13 个核心字段对应关系见 protocol/canonical_schema.yaml

# 5.3 用填好的 mapping 重跑
./.venv/Scripts/python.exe scripts/source_intake.py \
  --benchmark-root "<benchmark 数据目录>" \
  --protocol-dir protocol \
  --output-dir work/phase1 \
  --intake-config work/phase1/manifests/pending/<benchmark>.pending.yaml
```

**canonical 的 13 个核心字段**（`protocol/canonical_schema.yaml`，全部必填）：
`id` / `dataset_name` / `source_id` / `test_text` / `reference_answer` / `expected_behavior` /
`l1_category` / `l2_category` / `eval_method` / `note` / `type` / `safety_l1_dimension` / `safety_l2_harm_type`

**两个原则**：
1. **填不出就留空/填 None，不要编。** `safety_l1_dimension` 取不到就是 None，代码不会造假。
2. **分类体系是配置变量**：想用 benchmark 原生体系（如 HarmBench 的 `SemanticCategory` 7 类），就把 `l1_category`/`l2_category` 填它的原生值，`safety_l1_dimension` 留 None；**不要为了凑满 19 维硬映射**——那会污染结论。

---

## 6. 伦锋 · 第一天要敲的命令

```bash
# 6.1 看 driver 三件套（planner / executor / nl_bridge）
cat harness/src/agent_safety_harness/driver/planner.py
cat harness/src/agent_safety_harness/driver/executor.py
cat harness/src/agent_safety_harness/driver/nl_bridge.py

# 6.2 看白名单在哪定义（你的 P0 是"验证拦截"，先找到规则）
grep -rn "allow\|whitelist\|ALLOW" harness/src/agent_safety_harness/driver/nl_bridge.py

# 6.3 看演进控制面现状
ls evolution/
grep -rn "GATES" scripts/result_analysis.py
```

**自证成功**：能说出"自然语言 → plan → 调 registry"这条链上，每个函数从哪个文件接进来、哪个是 stub。
**卡住**：找不到 evolution 的 LLM 生成入口 → 那是 P2 的活，**不用现在做**；P0 只需把 `evolve` 接成"先返回 pending/存档"。

---

## 7. 今天下班前，每人交一样东西（验收标准）

| 人 | 今天下班前必须交 |
|---|---|
| 刘轲 | ① `registrations.py` 6 个 stub 的**替换计划**（哪个接哪个现有模块，写清函数签名）② `source_intake` 在你选的 benchmark 上跑出的 catalog 截图/文件 |
| 辰浩 | ① 一份符合 `run_request.schema.json` 的 `run_request.json`（能 `--dry-run` 通过）② preflight 输出 |
| 一迪 | ① 一份填好的 `run_config.yaml`（含免费端点）② `source_intake` 产出的 pending manifest + 你填好的 mapping |
| 伦锋 | ① driver 链路图（哪个函数是 stub、哪个是真实现）② 白名单规则清单 |

**共同要求**：产物放到 `work/<你的名字>/`，不要散在仓库根目录；卡住的写清楚"卡在哪、试过什么、需要谁配合"。

---

## 8. 分发前王浩已确认的四件事

1. **免费端点 key：各自申请、各自测试。** `harness/config/free_endpoints.json` 里 11 个 profile 都需要对应 `api_key_env` 的真 key。**谁跑评测谁自己申请**，不要等统一发放。推荐先用**智谱 GLM-4-Flash**（`ZHIPUAI_API_KEY`）或**硅基流动 Qwen2.5-7B**（`SILICONFLOW_API_KEY`）——国内直连、永久免费档，够第 1 周全部冒烟用。**key 只写环境变量，不进仓库、不进配置文件、不发群里。**
2. **`loop-a-local-harness` 已合入 main**（提交 `5527f81`）。所有人从 main 直接开分支，**不要再切 loop-a 分支**。Loop-A 的代码位置：`orchestrator/`（budget / controller / execution_guard / fixture_cycle / registry / replay / workers）、`evolution/`（admission / contracts / partitions / qualification / metrics 等）、`protocol/loop_a_contracts.schema.json`。
3. **`schema.py` 的 modality 扩展确认归刘轲**（P0 首位）。契约第 5 章已标注：`schema.py` 目前仍是 agent-only，缺 `modality` 字段与 LLM 模态字段（`FRAMEWORK_CONTRACT.md:81`）。**这是刘轲 P0 的前置项**——不做，llm_qa 轨产出的 TaskSpec 会缺字段，一迪的活就白干。
4. **被测产品的 CLI 辰浩自己装。** Codex CLI / Claude Code 等的安装、登录、版本确认由**辰浩本人**负责，不需要等任何人批准，也不需要报备；装好后确认版本号与 `product_registry.json` 里 pin 的版本一致即可。**注意**：这部分是辰浩自己的工作项，不构成"等待外部依赖"的阻塞理由。

---

## 9. 合并 Loop-A 后新增的可执行命令（伦锋优先看）

```bash
# 9.1 看 Loop-A 的就绪检查（会诚实报出还差什么）
./.venv/Scripts/python.exe pipeline.py loop-a plan

# 9.2 看 Loop-A 的全部子动作
./.venv/Scripts/python.exe pipeline.py loop-a --help
#   action: plan / qualify / partitions / metrics / smoke / cycle / compare / run

# 9.3 Loop-A 相关文档（讲清了当前实现边界与哪些还没做）
cat docs/integration/loop_a_implementation.md
cat docs/integration/loop_a_phase2.md
```

**`loop-a plan` 的实际输出**（2026-11-04 实测）：

```json
{
  "formal_ready": false,
  "blockers": [
    "protocol_not_frozen",
    "primary_metric_not_confirmed",
    "target_or_generator_not_fixed",
    "paid_budget_not_configured",
    "real_dispatcher_and_isolation_qualification_not_registered"
  ],
  "paid_calls_dispatched": 0
}
```

**这是设计好的诚实输出，不是故障**——它明确告诉你"还差什么才能跑正式实验"。伦锋的 P1 就是在逐个消解这 5 个 blocker。**不要为了让 `formal_ready` 变 true 而绕过其中任何一项。**
