# Phase 1/2/3 当前实现说明与交接报告

本文用于同步当前 `llm-safety-eval-research-pipeline` 中 Phase 1/2/3 的实现边界、使用方式、已完成能力、校验机制和后续待改进项。本文不是 README 的最终文档对齐版本；README 建议等代码流程进一步稳定后再统一更新。

## 1. 当前整体结论

当前 Phase 1/2/3 已经形成从原始 benchmark/evidence 输入到 canonical dataset、再到 smoke evaluation protocol 的前置闭环：

```text
Phase 1 Source Intake
  raw benchmark/evidence
  -> field mapping reports
  -> ready/needs_review/failed 三态分类
  -> dataset_catalog.json

Phase 2 Canonical Standardization
  dataset_catalog.json
  -> canonical JSONL
  -> stats
  -> quarantine
  -> membership diff

Phase 3 Protocol Shaping
  canonical JSONL
  -> deterministic 100-row smoke subset
  -> run_config.yaml
  -> phase3_report.json
```

完整执行层面的 smoke/self-test 会进入 Phase 4。也就是说，Phase 1/2/3 负责“准备可评测输入和协议”，Phase 4 负责“拿 Phase 3 产物真正跑 evaluator，验证执行闭环”。

## 2. Phase 1: Source Intake

入口脚本：

```bash
python scripts/source_intake.py \
  --benchmark-root "<benchmark_root>" \
  --evidence-root "<evidence_root>" \
  --output-dir work/phase1 \
  --protocol-dir protocol \
  --canonical-schema protocol/canonical_schema.yaml \
  --format json
```

其中 `--evidence-root` 可选。如果暂时只有 benchmark，也可以不传 evidence。

### 2.1 输入

Phase 1 接收两类来源：

- benchmark：真正会进入 canonical dataset 的结构化数据，支持 `.jsonl`, `.json`, `.csv`, `.tsv`
- evidence/whitepaper：作为后续核对证据，不参与 canonical 修改逻辑

### 2.2 输出

默认输出到：

```text
work/phase1/
  dataset_catalog.json
  source_inventory.csv
  evidence_inventory.jsonl
  reports/
    field_mapping_report.json
    needs_review.json
    failed_files.json
  manifests/
    pending/
      <benchmark>.pending.yaml
```

### 2.3 三态处理

Phase 1 会把每个 benchmark 文件分成三类：

- `ready`：required fields 都已映射完成，可以进入 Phase 2 canonicalization
- `needs_review`：文件可读，但 required field 映射缺失或存在多候选歧义，需要人工补 pending manifest
- `failed`：文件不可读、格式坏、非法 JSON/CSV、样本结构混乱到无法判断

### 2.4 字段映射逻辑

Phase 1 根据 `protocol/canonical_schema.yaml` 的 canonical fields 和内置 alias 表推断字段映射。

例如：

```yaml
test_text:
  - test_text
  - prompt
  - question
  - query
```

如果同一个 source 文件里同时出现 `prompt`, `question`, `query`，且它们都可能映射到 `test_text`，当前实现会强制进入 `needs_review`，不会自动选择其中一个进入 canonical。

这种情况下会生成 pending manifest，例如：

```yaml
benchmark: ambiguous
status: needs_review

ambiguous_mappings:
  test_text:
    selected: prompt
    candidates:
      - prompt
      - question
      - query

field_mapping:
  test_text:
  reference_answer: label
  safety_l1_dimension: l1
  safety_l2_harm_type: l2
```

人工补完后，通过 intake config 覆盖字段映射，再重新运行 Phase 1。

### 2.5 Phase 1 已完成能力

- 支持 benchmark/evidence 分离 intake
- 支持 ready/needs_review/failed 三态分类
- 支持自动生成 pending YAML
- 支持多候选 required field 强制人工 review
- 支持 source/evidence sha256、byte size、row count 等溯源记录
- 旧的 `mapping_suggestions` 已被 reports 和 pending manifests 替代

## 3. Phase 2: Canonical Standardization

入口脚本：

```bash
python scripts/canonicalize_datasets.py \
  --catalog work/phase1/dataset_catalog.json \
  --canonical-schema protocol/canonical_schema.yaml \
  --output eval_mvp_V2/data/master_normalized_dataset_v2_final.jsonl \
  --stats output/doc/v2_stats.json \
  --protocol-dir protocol \
  --format json
```

### 3.1 输入

Phase 2 使用 Phase 1 的 `dataset_catalog.json` 作为 membership 和 mapping 来源。只有 Phase 1 中 `ready` 的 benchmark 会进入 canonicalization。

### 3.2 输出

默认输出：

```text
eval_mvp_V2/data/
  master_normalized_dataset_v2_final.jsonl

output/doc/
  v2_stats.json

work/phase2/
  dataset_sources_clean.csv
  quarantine.jsonl
  membership_diff.json
  canonicalization_report.json
```

### 3.3 Canonical record 行为

Phase 2 会根据 canonical schema 做类型处理：

- `str/string`：规范化为空白压缩后的字符串
- `list/array`：保留 list；如果 source 是 JSON string，会尝试 parse
- `dict/object/map`：保留 dict；如果 source 是 JSON string，会尝试 parse
- `int/integer`, `float/number`, `bool/boolean`：按 schema 类型转换

如果某条记录类型转换失败，会写入 `quarantine.jsonl`，不会导致整个文件全部失败。

### 3.4 source_id 规则

当前规则：

- 如果原始记录有 mapped `source_id`，保留原值
- 如果原始记录没有 `source_id`，按源文件记录顺序生成：

```text
<benchmark_id>:<8-digit-ordinal>
```

例如：

```text
bench:00000002
```

这样后续能稳定追溯到原始源文件中的第几条记录。

### 3.5 hash 校验

Phase 2 现在新增了 source hash 强校验：

- Phase 1 在 `dataset_catalog.json` 记录每个 source 文件的 `sha256`
- Phase 2 在读取 source 文件前重新计算当前文件 sha256
- 如果两者不一致，Phase 2 直接失败，不继续生成 canonical JSONL

这个设计避免出现“Phase 1 catalog 描述的是旧文件，但 Phase 2 实际 canonicalize 的是新文件”的不可追溯情况。

### 3.6 Phase 2 已完成能力

- 从 Phase 1 catalog 生成 canonical JSONL
- 按 schema 类型 canonicalize 字段
- 保留 list/dict 等结构化扩展字段
- 自动生成缺失 `source_id`
- 自动生成稳定 canonical `id`
- 输出 stats、membership diff、canonicalization report
- 对非法记录写 quarantine
- 对 source 文件漂移做 sha256 强校验
- 支持 canonical validation：

```bash
python scripts/canonical_validate.py \
  --canonical eval_mvp_V2/data/master_normalized_dataset_v2_final.jsonl \
  --stats output/doc/v2_stats.json \
  --protocol-dir protocol \
  --canonical-schema protocol/canonical_schema.yaml \
  --format json
```

## 4. Phase 3: Protocol Shaping

入口脚本：

```bash
python scripts/prepare_phase3.py \
  --canonical eval_mvp_V2/data/master_normalized_dataset_v2_final.jsonl \
  --output-dir work/phase3 \
  --smoke-output eval_mvp_V2/data/test_eval_100_dataset_type_coverage.jsonl \
  --run-config work/phase3/run_config.yaml \
  --protocol-dir protocol \
  --sample-size 100 \
  --format json
```

### 4.1 输入

Phase 3 读取 Phase 2 生成的 canonical JSONL。

要求每条记录至少具备：

- `id`
- `test_text`
- `reference_answer`

### 4.2 输出

默认输出：

```text
eval_mvp_V2/data/
  test_eval_100_dataset_type_coverage.jsonl

work/phase3/
  run_config.yaml
  phase3_report.json
```

### 4.3 smoke subset 抽样策略

当前 smoke subset 是确定性抽样，不使用随机数。

优先覆盖：

- `dataset_name`
- `type`
- `safety_l1_dimension`
- `safety_l2_harm_type`
- `(safety_l1_dimension, safety_l2_harm_type)`
- `(dataset_name, type)`

如果覆盖样本不足 `sample_size`，再按 canonical 读取顺序补齐。默认 `sample_size=100`。

### 4.4 run_config.yaml

Phase 3 会生成 evaluator 可读的外部协议配置：

```yaml
phase: phase3
protocol:
  protocol_version: ...
  taxonomy_version: ...
  metric_version: ...
  prompt_version: prompt_only_v2
  scorer_version: rule_eval

dataset:
  canonical_jsonl: eval_mvp_V2/data/master_normalized_dataset_v2_final.jsonl
  canonical_sha256: ...
  smoke_subset_jsonl: eval_mvp_V2/data/test_eval_100_dataset_type_coverage.jsonl
  selection_strategy: deterministic_coverage_then_stable_order

fields:
  id: id
  prompt: test_text
  reference: reference_answer

evaluator_config:
  input_jsonl: eval_mvp_V2/data/test_eval_100_dataset_type_coverage.jsonl
  run_root: rule_eval_mvp/output_smoke
  run_mode: self_test
  id_field: id
  prompt_field: test_text
  reference_field: reference_answer
```

### 4.5 evaluator 外部配置支持

`eval_mvp_V2/multi_model_rule_eval_prompt_only.py` 现在支持：

```bash
python eval_mvp_V2/multi_model_rule_eval_prompt_only.py \
  --config work/phase3/run_config.yaml
```

外部 config 会覆盖默认 `CONFIG` 中的关键字段，例如：

- `INPUT_JSONL`
- `RUN_ROOT`
- `RUN_MODE`
- `ID_FIELD`
- `PROMPT_FIELD`
- `REFERENCE_FIELD`
- `PROMPT_SUFFIX`
- `SYSTEM_PROMPT`
- `USE_EVAL_WRAPPER`
- `MODELS`

旧的无参数运行方式仍保留，不会破坏历史脚本用法。

### 4.6 Phase 3 已完成能力

- 从 canonical dataset 生成 100 条 deterministic smoke subset
- 生成 `run_config.yaml`
- 生成 `phase3_report.json`
- 将 prompt/scorer/dataset/field/evaluator 绑定进外部协议文件
- evaluator 支持读取外部 config

## 5. Phase 4 的位置

Phase 1/2/3 目前完成的是“数据与协议准备闭环”。真正执行 evaluator 并验证整个链路能跑通，放在 Phase 4。

Phase 4 的典型命令是：

```bash
python eval_mvp_V2/multi_model_rule_eval_prompt_only.py \
  --config work/phase3/run_config.yaml
```

当前 `run_config.yaml` 默认使用：

```yaml
run_mode: self_test
```

因此 Phase 4 首先应该跑 self-test，不调用真实模型 API。self-test 通过后，再进入 API smoke 或 full evaluation。

## 6. 当前 hash 设计说明

当前存在多类 hash，需要区分用途。

### 6.1 正式协议锁定 hash

位于：

```text
protocol/evaluation_protocol_card.md
```

包括：

- `dataset.sha256`
- `evaluation_script.sha256`

这是正式协议锁定用 hash。它们用于判断 dataset 或 evaluator 是否发生协议漂移。

校验位置：

```bash
python scripts/protocol_manager.py validate \
  --protocol-dir protocol \
  --repo-root . \
  --format json
```

以及 canonical validation：

```bash
python scripts/canonical_validate.py ...
```

### 6.2 Phase 1/2 source hash

位于：

```text
work/phase1/dataset_catalog.json
work/phase1/source_inventory.csv
work/phase2/dataset_sources_clean.csv
```

作用是记录原始 source 文件身份。

当前 Phase 2 已经使用该 hash 做强校验：如果 Phase 1 后 source 文件被改动，Phase 2 会失败。

### 6.3 Phase 3 canonical hash

位于：

```text
work/phase3/run_config.yaml
```

字段：

```yaml
dataset:
  canonical_sha256: ...
```

当前主要用于记录 smoke subset 来源于哪一版 canonical dataset。它是 provenance 信息，目前还不是 evaluator 的强校验入口。

### 6.4 evaluator 内部 hash

包括：

- shard 分片用 hash
- prompt hash
- 自动生成 canonical id 使用的 hash

这些不是文件完整性校验 hash，而是执行逻辑或结果追踪字段。

## 7. 当前验证方式

### 7.1 单独验证 Phase 1

```bash
python scripts/source_intake.py \
  --benchmark-root "<benchmark_root>" \
  --evidence-root "<evidence_root>" \
  --output-dir work/phase1 \
  --protocol-dir protocol \
  --canonical-schema protocol/canonical_schema.yaml \
  --format json
```

检查：

- `ready > 0`
- `needs_review == 0`，或者 pending manifest 已补完并重跑
- `failed == 0`，或者 failed 文件已确认不进入 canonical

### 7.2 单独验证 Phase 2

```bash
python scripts/canonicalize_datasets.py \
  --catalog work/phase1/dataset_catalog.json \
  --canonical-schema protocol/canonical_schema.yaml \
  --output eval_mvp_V2/data/master_normalized_dataset_v2_final.jsonl \
  --stats output/doc/v2_stats.json \
  --protocol-dir protocol \
  --format json
```

然后：

```bash
python scripts/canonical_validate.py \
  --canonical eval_mvp_V2/data/master_normalized_dataset_v2_final.jsonl \
  --stats output/doc/v2_stats.json \
  --protocol-dir protocol \
  --canonical-schema protocol/canonical_schema.yaml \
  --format json
```

### 7.3 单独验证 Phase 3

```bash
python scripts/prepare_phase3.py \
  --canonical eval_mvp_V2/data/master_normalized_dataset_v2_final.jsonl \
  --output-dir work/phase3 \
  --smoke-output eval_mvp_V2/data/test_eval_100_dataset_type_coverage.jsonl \
  --run-config work/phase3/run_config.yaml \
  --protocol-dir protocol \
  --sample-size 100 \
  --format json
```

### 7.4 用 phase_executor 查看推荐命令

当前 `phase_executor.py` 可以输出每个 phase 的检查项和推荐命令：

```bash
python scripts/phase_executor.py --phase phase1 --emit-commands
python scripts/phase_executor.py --phase phase2 --emit-commands
python scripts/phase_executor.py --phase phase3 --emit-commands
```

注意：当前它主要是 command planner，不是真正的自动 runner。

## 8. 自动化边界

当前状态：

- Phase 1/2/3 都可以单独运行
- `phase_executor.py` 能生成对应命令
- 单测覆盖 Phase 1/2/3 的关键行为
- Phase 4 可用 Phase 3 生成的 `run_config.yaml` 启动 evaluator self-test

尚未完成：

- `phase_executor.py --run` 自动执行 phase
- 一键串联 Phase 1 -> Phase 2 -> Phase 3
- 自动刷新 protocol card 中 dataset/evaluator hash
- Phase 4 的正式 smoke failure report

## 9. 稳定后建议改进项

### 9.1 增加显式 hash 同步命令

建议新增：

```bash
python scripts/protocol_manager.py sync-hashes \
  --protocol-dir protocol \
  --repo-root .
```

该命令只在人工确认代码和 dataset 稳定后执行，用于更新：

- `protocol.dataset.total`
- `protocol.dataset.sha256`
- `protocol.evaluation_script.sha256`

不建议每次编辑 evaluator 后自动改 protocol card。protocol hash 应该是“确认后锁定”，不是“编辑后静默漂移”。

### 9.2 增加 phase_executor --run

建议未来支持：

```bash
python scripts/phase_executor.py --phase phase1 --run --benchmark-root ... --evidence-root ...
python scripts/phase_executor.py --phase phase2 --run
python scripts/phase_executor.py --phase phase3 --run
python scripts/phase_executor.py --phase phase4 --run
```

这样每个 phase 仍可独立执行，但不需要人工复制命令。

### 9.3 精简 Phase 1 reports

当前 `needs_review.json` 和 `dataset_catalog.json` 仍略显冗余。建议后续精简为：

- `needs_review.json` 只保留人工需要处理的重点
- `dataset_catalog.json` 只保留 Phase 2 必需字段和核心 provenance
- pending manifest 继续作为人工补 mapping 的主要入口

### 9.4 Phase 3 canonical hash 强校验

当前 `run_config.yaml` 记录 `canonical_sha256`，但 evaluator 不会强制检查 canonical hash。

未来可以在 Phase 4 runner 中检查：

- `run_config.dataset.canonical_sha256`
- 当前 `run_config.evaluator_config.input_jsonl` 或 canonical 文件 hash

这样可以避免 smoke subset/run_config 与实际输入不一致。

### 9.5 Phase 4 smoke report

Phase 4 应补充：

```text
work/phase4/
  smoke_run_report.json
  smoke_failures.md
```

用于记录 self-test 是否通过、失败样本、无效输出、字段缺失、聚合结果等。

## 10. 当前测试覆盖

当前已有单测覆盖：

- Phase 1 ready/needs_review/failed 三态输出
- pending manifest 生成
- 多候选字段强制 needs_review
- intake override 解决歧义
- Phase 2 canonicalization 和 validation
- list/dict schema 类型保留
- source_id 保留与自动生成
- Phase 2 source hash drift 检测
- Phase 3 smoke subset 和 run_config 生成
- evaluator 外部 config 读取

当前验证命令：

```bash
python -m unittest tests.test_phase12_tools
python -m unittest discover tests
```

最近一次验证结果：

```text
python -m unittest tests.test_phase12_tools
Ran 6 tests ... OK

python -m unittest discover tests
Ran 16 tests ... OK
```

## 11. 给后续开发同学的建议入口

如果继续开发 Phase 4，建议优先看：

- `scripts/prepare_phase3.py`
- `work/phase3/run_config.yaml`
- `eval_mvp_V2/multi_model_rule_eval_prompt_only.py`
- `scripts/phase_executor.py`

Phase 4 的目标不应该重新做 Phase 1/2/3 的事情，而是消费 Phase 3 的 `run_config.yaml`，运行 evaluator self-test，并产出明确的 smoke validation report。

