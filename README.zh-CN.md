# LLM Safety Eval Research Pipeline

[English README](./README.md)

这个仓库是一个项目内使用的 Codex skill，用来把一个大模型安全评测项目整理成一套可复用、可审计、部分可执行的工作流。

它不是一个替代原始评测代码的新框架。它的定位是覆盖在现有安全评测代码库之上，提供：

- 一个把用户请求映射到协议阶段的 intent router
- 一个执行前检查并生成可执行命令提示的 phase executor
- 一层可复用的协议层，用于管理数据、指标、分类体系、发布与报告
- 一层模板层，用于 manifest、registry 和 release checklist
- 一套 truth model，防止白皮书和报告反过来成为事实源

当前版本绑定到一个具体的仓库结构和一套具体的 V2 发布流水线。它的目标是复用已有本地脚本和产物约定，而不是另起一套平行编排系统。

## 这个 Skill 解决什么问题

大模型安全评测项目通常会逐渐积累出很多内容：

- canonical dataset
- 不断演化的 taxonomy 和 metric 定义
- 各种一次性的重跑脚本
- 临时的 leaderboard 刷新
- 白皮书和报告产出
- 数据、结果与文字之间的人工一致性检查

时间久了，真正难的往往不是“没有代码”，而是“没有操作协议层”：

- 当用户说“更新 leaderboard”时，到底该进哪个 phase
- 发布前必须检查哪些条件
- 哪些文件才是权威事实源
- 应该调用哪些脚本
- 跑完之后应该产出哪些文件
- 在什么条件下必须中止而不是继续执行

这个 skill 就是在补这一层。

## 适用范围

这个 skill 适用于：

- 补充或规范化一个新 benchmark
- 更新 taxonomy 或 metric 定义
- 准备 smoke 子集和 run config
- 运行或重跑评测
- 只重跑 API error
- 对新数据做 append-only evaluation
- merge 旧结果与 delta 结果
- 发布 authoritative final outputs
- 刷新 leaderboard 和白皮书
- 生成可审计的 manifest 与 release artifacts

这个 skill 不打算做的事情包括：

- 替换原始评测脚本
- 掩盖项目特定假设
- 从报告文字里自动反推事实
- 在不绑定路径的前提下泛化到任意仓库
- 在当前版本中把所有运行时变更完全自动化

## 真值模型

这个 skill 基于一个严格的 source-of-truth 层级。

### Canonical Inputs

- `eval_mvp_V2/data/master_normalized_dataset_v2_final.jsonl`
  这是 canonical dataset 的事实源。
- `output/doc/v2_stats.json`
  这是一个派生出来的统计快照，必须与 canonical JSONL 保持一致。

### Authoritative Outputs

- `rule_eval_mvp/output_v2_final_authoritative/`
  这是最终 leaderboard 和报告消费时唯一的 authoritative publish root。

### Derived Artifacts

- 白皮书
- markdown 摘要
- DOCX 报告
- distribution report
- leaderboard 文字说明
- 图表和叙述性分析

这些派生产物可以解释结果，但不能定义结果。

## 运行原则

1. canonical dataset 优先。
2. 报告是派生产物，不是事实源。
3. authoritative publish 只能来自 merged clean results。
4. canonical 计数必须从数据集直接重算，不能从 prose 抄数。
5. metric 和 taxonomy 的变更必须在评测和发布前显式锁定。
6. 增量重跑不能静默污染 legacy outputs。

## 仓库结构

```text
.
├── SKILL.md
├── README.md
├── README.zh-CN.md
├── .gitignore
├── agents/
│   └── openai.yaml
├── references/
│   ├── phase-contracts.md
│   └── repo-asset-map.md
├── scripts/
│   ├── intent_router.py
│   └── phase_executor.py
└── assets/
    └── templates/
        ├── evaluation_protocol_card.md
        ├── metric_registry.yaml
        ├── model_registry.json
        ├── publish_config.yaml
        ├── release_checklist.md
        ├── release_manifest.yaml
        ├── run_config.yaml
        └── taxonomy_registry.yaml
```

## 核心组成

### `SKILL.md`

这是 Codex 实际读取的 skill 定义文件，负责说明什么时候触发 skill、约束哪些协议规则、要读哪些 references、要调用哪些脚本。

### `references/`

按需加载的紧凑型操作参考文件。

- `phase-contracts.md`
  每个 phase 的简版合同说明。
- `repo-asset-map.md`
  仓库资产复用地图与绑定提示。

### `scripts/`

最小可执行原型层。

- `intent_router.py`
  把自然语言请求转成协议 phase。
- `phase_executor.py`
  做 phase precheck、列出可用 model roots，并输出与现有项目脚本绑定的命令提示。

### `assets/templates/`

可复用的治理与发布产物模板。它们应该被复制和填写，而不是每次从零重写。

## Phase 体系

整个工作流被拆成十个 phase。

| Phase | 名称 | 作用 |
| --- | --- | --- |
| `phase0` | Protocol Setup | 锁定 taxonomy、metrics、models 和可比性规则 |
| `phase1` | Source Intake | 收集 benchmark 来源与白皮书证据 |
| `phase2` | Canonical Standardization | 规范 membership，并保持 canonical JSONL 与 stats 对齐 |
| `phase3` | Protocol Shaping | 定义 prompts、scorers 和 smoke subsets |
| `phase4` | Smoke Validation | 在小样本上验证协议 |
| `phase5` | Full Evaluation | 在 canonical dataset 上跑正式评测 |
| `phase6` | Incremental Rerun | 重跑 API-error 或 append-only delta |
| `phase7` | Merge And Publish | merge 旧结果与 delta，清洗到 canonical IDs，发布 authoritative results |
| `phase8` | Reporting | 生成 leaderboard、图表、DOCX 和白皮书产物 |
| `phase9` | Agent Safety Extension | 扩展到 trajectory-level agent safety |

## Intent Routing

router 当前是关键词驱动的。这是刻意设计，不是在做“深度语义理解”，而是在做“操作层的 phase 映射”。

例子：

- `补一个新 benchmark` -> `phase1 + phase2`
- `只重跑 API error` -> `phase6`
- `更新 leaderboard` -> `phase7 + phase8`
- `生成白皮书` -> `phase7 + phase8`
- `换一套指标` -> `phase0 + phase3 + phase5 + phase8`
- `换一套风险维度` -> `phase0 + phase2 + phase3 + phase8`

如果没有明显命中信号，router 默认回落到 `phase0`，先在协议层完成范围界定。

## Phase 执行模型

当前 executor 是有意保持保守的。

对每个 phase，它目前可以：

- 检查必需路径和脚本是否存在
- 展示当前有哪些 model 目录可用
- 给出协议约束和执行风险提示
- 生成和现有脚本接口一致的命令提示

它当前还不做：

- 自动修改上游 config 文件
- 无监督地直接串起多 phase 全流程
- 自动写回外部系统
- 自动补全 append-only 场景里缺失的数据集路径

## 已绑定的项目脚本

这个 skill 目前显式绑定以下项目脚本：

- `extract_whitepaper_datasets.py`
- `build_v2_final_dataset_report.py`
- `eval_mvp_V2/multi_model_rule_eval_prompt_only.py`
- `rule_eval_mvp/incremental_tools/run_v2_incremental_eval.py`
- `rule_eval_mvp/incremental_tools/run_append_only_eval.py`
- `rule_eval_mvp/incremental_tools/append_new_dataset_results.py`
- `rule_eval_mvp/incremental_tools/merge_v2_incremental_results.py`
- `rule_eval_mvp/incremental_tools/rebuild_merged_summaries.py`
- `rule_eval_mvp/incremental_tools/publish_v2_final_authoritative_results.py`

这层绑定本身就是重点。这个 skill 的目标是把既有代码库操作化，而不是把它抽象掉。

## 模板与 Registry 文件

`assets/templates/` 目录提供了一组最小可用的审计层产物。

### `evaluation_protocol_card.md`

用于冻结 run protocol、scope、assumptions、metric mode、model set 和 comparability statements。

### `model_registry.json`

用于声明 model identifiers、结果路径以及模型级运行元数据。

### `taxonomy_registry.yaml`

用于声明当前启用的 taxonomy version 和 label structure。

### `metric_registry.yaml`

用于声明当前 metric version、打分语义和 primary metric 选择。

### `run_config.yaml`

用于把最小 run-layer 配置外置化，便于对比和记录执行。

### `publish_config.yaml`

用于声明 publish roots、选定模型和 release-time 规则。

### `release_manifest.yaml`

它是机器可读的 release record，包含：

- release id
- canonical dataset path 与 total
- taxonomy 与 metric version
- source result roots
- published result path
- 缺失 ids、额外 ids、deleted-id 清理状态等 validation 字段

### `release_checklist.md`

用于外部发布前的人类审查清单。

## 推荐用法

### 1. 路由请求

```powershell
python scripts/intent_router.py --request "更新 leaderboard" --format json
```

### 2. 运行协议检查或命令生成

```powershell
python scripts/phase_executor.py --request "更新 leaderboard" --emit-commands --format json
```

### 3. 直接查看某个 phase

```powershell
python scripts/phase_executor.py --phase phase7 --models doubao_seed_2_0_pro_full --emit-commands --format json
```

## 典型工作流示例

### 情况 1：基于已有 delta results 刷新 leaderboard

1. 把请求路由到 `phase7 + phase8`
2. 运行 `phase_executor.py --phase phase7 --emit-commands`
3. 检查 canonical dataset、stats、deleted ids、merge script、publish script
4. 执行 merge、rebuild summaries 和 publish
5. 只用 authoritative output 刷新 leaderboard 和报告

### 情况 2：只重跑 API-error cases

1. 把请求路由到 `phase6`
2. 验证 legacy output 和 incremental rerun script 是否存在
3. 生成带 canonical dataset 与 delta output root 的 `run_v2_incremental_eval.py` 命令
4. 如果需要对外产物更新，再在 rerun 完成后进入 merge 和 publish

### 情况 3：修改 metric 定义

1. 把请求路由到 `phase0 + phase3 + phase5 + phase8`
2. 更新 `metric_registry.yaml`
3. 在 `evaluation_protocol_card.md` 中冻结 comparability statement
4. 重新验证 smoke subset 与 scorer 假设
5. 重跑受影响评测，再生成新的报告产物

## Guards 与失败策略

在完整 guard engine 上线前，这个 skill 已经编码了一些硬边界。

Fatal 边界：

- canonical total 不匹配时必须停止
- publish 试图绕过 merged clean results 时必须停止
- 报告数字无法追溯到 canonical 或 authoritative artifacts 时必须停止

Warning 边界：

- 协议已经变化但 smoke validation 没刷新时，不能静默继续
- append-only 执行缺少新数据集路径时，不能假定默认路径继续跑
- stats snapshot 过期时，不能当作无害问题忽略

## 当前限制

这个仓库已经是一个可用的一版原型，但它仍然是一版原型。

当前限制包括：

- routing 还是关键词驱动，不是配置驱动
- guards 目前是代码检查和 notes，不是独立 machine-readable guard registry
- executor 目前输出命令，但还没有完整的多 phase `--run`
- 模板填充还是手工的
- 仓库绑定目前只适配一个具体的 V2 safety-eval 项目结构

## Roadmap

后续自然的升级方向包括：

- 引入 machine-readable guard schema
- 给 `phase_executor.py` 增加真正的 `--run` 模式
- 支持把模板 materialize 到指定工作目录
- 把 routing rules 外置到配置文件
- 增加 audit log 产出
- 在不破坏当前 invariants 的前提下支持 V3 或行业变体

## 当前验证状态

这个仓库已经作为独立 skill repository 做过本地验证。

验证项包括：

- skill 结构通过 `quick_validate.py`
- `intent_router.py` 能处理中文和英文请求
- `phase_executor.py` 能输出可工作的 phase7 检查和命令提示
- Windows 下的 UTF-8 控制台输出已补齐

## 适用对象

这个仓库尤其适合：

- 管理持续演化安全 benchmark 的研究者
- 手里有很多本地脚本但缺协议层的团队
- 需要把发布与报告边界做成可审计流程的项目
- 依赖 Codex 自动化、且需要意图路由和真值控制的工作流

## 开发说明

- `SKILL.md` 始终是 Codex 的 canonical skill entrypoint
- `README.md` 和 `README.zh-CN.md` 面向人和仓库协作
- 优先更新 templates 和 references，而不是把 `SKILL.md` 不断写胖
- 如果项目脚本替换了现有执行器，要同时更新 `SKILL.md` 和 `scripts/phase_executor.py`
- 不要把运行时缓存或生成结果目录提交进这个仓库
