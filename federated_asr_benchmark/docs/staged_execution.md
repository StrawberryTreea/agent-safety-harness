# 分阶段产品评测协议

[返回产品模块](../README.md)。本文件描述固定选择集和放行门槛，不是当前阶段已完成的成绩。所有命令从仓库根执行，真实执行前准备外部资产并通过 preflight。

## 固定矩阵

- 受控产品：Codex、Claude Code、Qoder；CodeBuddy 单独通过同样门槛后可加入对照。
- Benchmark：InjecAgent、MSB、AgentDojo、AgentLeak、SABER、AgentPoison-StrategyQA。
- 每个 `产品 x benchmark` 先执行 20 条 smoke，再执行另外 100 条 pilot。
- 同一 benchmark 的所有被测产品共享完全相同的 case 集合。
- smoke 与 pilot 不重叠；pilot 不是 smoke 的扩容集合。
- 任一阶段失败只影响对应 case 的有效性，不用替补样本改变选择集。

因此 smoke 共执行 `3 x 6 x 20 = 360` 个产品 case，pilot 共执行
`3 x 6 x 100 = 1,800` 个产品 case。

## 固定选择集

选择集位于 `federated_asr_benchmark/selections/`。每个 benchmark 有独立 JSONL，阶段目录中的
`all.jsonl` 是六个 benchmark 的合并索引：

```text
selections/
  manifest.json
  smoke20/
    all.jsonl
    <benchmark_id>.jsonl
  pilot100/
    all.jsonl
    <benchmark_id>.jsonl
```

选择算法使用固定种子和确定性哈希。InjecAgent 按攻击轨道和类型平衡，MSB
按 agent 与攻击类型平衡，AgentDojo 按 suite 平衡，AgentLeak 按领域和攻击类
平衡，SABER 按 scenario 和 category 平衡。AgentPoison 本地测试文件没有答案或
类别标签，因此只做确定性哈希抽样。多字段 benchmark 另加低权重联合层，减少
smoke 集中在少数交叉组合的风险。

重建索引、查看选择集生成参数并校验（生成新的选择集时明确指定输出，保留既有版本）：

```powershell
python federated_asr_benchmark/tools/build_case_index.py --data-root $env:AGENT_EVAL_DATA_ROOT --output-dir runs/rebuilt-index
python federated_asr_benchmark/tools/build_stage_selections.py --help
python scripts/check_repo.py
```

## 执行

每个 benchmark 使用自己的产品 adapter。以 SABER smoke 为例：

```powershell
python federated_asr_benchmark/tools/run_product_batch.py `
  --request federated_asr_benchmark/examples/requests/codex_saber.json `
  --case-index federated_asr_benchmark/selections/smoke20/saber.jsonl `
  --data-root $env:AGENT_EVAL_DATA_ROOT `
  --run-id v1-codex-saber-smoke20 `
  --output-root runs/deepseek_v4_flash/smoke20/codex/saber `
  --resume --keep-going --publish-native-result
```

pilot 只需要把 case index 换成 `pilot100/saber.jsonl`。三个产品不得共用输出目录
或产品状态目录。只有 benchmark 环境、官方 scorer 和固定 case 选择是共享的。

超过 1 条的 SABER 非 dry-run 会先校验官方 judge 的 `config.json`。judge 未就绪时，
批量入口会在调用任何产品前失败，避免付费生成无法计算 ASR 的轨迹。仅当明确需要
独立采集未判分轨迹时，才使用 `--allow-unjudged-traces` 覆盖该门禁；这类轨迹不算
smoke 或 pilot 完成结果。

阶段聚合必须显式声明子集模式：

```powershell
python federated_asr_benchmark/tools/aggregate_results.py runs/deepseek_v4_flash/smoke20/<product>/*/case_result.jsonl `
  --case-index federated_asr_benchmark/selections/smoke20/all.jsonl `
  --allow-subset `
  --run-id <run_id> `
  --output runs/deepseek_v4_flash/smoke20/<product>/summary.json
```

不传 `--allow-subset` 时，聚合器仍严格要求完整的 5,384 条 case index，防止把
阶段结果误当成全量结果。

## 放行门槛

smoke 用于验证产品版本、隔离、工具路由、官方 scorer 映射、结果落盘和凭据清理。
某个 `产品 x benchmark` 的 20 条全部生成官方判定并可计算 ASR 后，才放行其 pilot。pilot
达到 benchmark registry 的有效覆盖率门槛后，才讨论全量运行。ASR 是唯一性能
指标；case 数、invalid 数、coverage 和错误原因仅作为执行审计字段。
