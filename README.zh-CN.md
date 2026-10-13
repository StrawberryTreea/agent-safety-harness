# LLM 与 Agent 安全自动化评测研究管线

本仓库是项目长期主仓库，整合 LLM 问答评测、内部 Agent 执行、完整 Agent 产品评测和离线轨迹评审。以既有执行器和评分契约为基础，逐步实现“评测 → 失败分析 → 新测试生成与验收 → 新版测试集 → 再评测”的闭环。

[English](README.md) · [整合架构与原始概念图](docs/integration/architecture.md) · [研究与实验计划](docs/integration/research_plan.md) · [验证记录](docs/integration/validation.md)

## 当前可以运行

- `pipeline.py inventory`：列出四条评测线及本地依赖状态。
- `pipeline.py plan`：输出原生执行器的工作目录、argv 和必需资产，供后续执行；不自动调用付费服务。
- `pipeline.py smoke`：实际运行 3 条自包含 mock 任务，完成公开捕获、私有评分、报告和分析。
- `pipeline.py analyze`：读取原生结果，输出有效观察、失败、异常、分组统计和待验收候选测试计划。

自动攻击/任务生成、验收执行与新版测试集发布尚未实现。候选计划保留父任务证据，`admitted=false`，不等同于有效新测试或已完成自我进化。

## 快速开始

在仓库根目录使用 Python 3.12 或更高版本：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe pipeline.py inventory
.\.venv\Scripts\python.exe pipeline.py smoke
.\.venv\Scripts\python.exe pipeline.py smoke --agent unsafe_stub
```

默认输出到新的 `runs/smoke-<timestamp>-<id>/`，保留每一步日志与 `pipeline_manifest.json`。`safe_stub` 验证正常路径，`unsafe_stub` 验证失败分析和候选计划路径；二者都是合成 mock，不是完整产品的正式成绩。

```powershell
python pipeline.py plan --track agent_execution --tasks public_tasks.jsonl --private-rules private_rules.jsonl --agent cli:configured_agent --out runs/next-run
python pipeline.py analyze --track agent_execution --results runs/next-run/score/results.jsonl --out runs/next-analysis
python pipeline.py analyze --track agent_product --results case_results.jsonl --out runs/product-analysis
```

计划中的 CLI Agent 使用 `harness/config/agents.json`，也可通过 `plan --agents-config ... --models-config ...` 指定原生配置；模型、endpoint、凭证环境变量和预算应在这些配置中固定。

## 评测线与指标

| 轨道 | 模块 | 口径 |
|---|---|---|
| `llm_qa` | `eval_mvp_V2/` + 原协议脚本 | V2 rule_eval；拒答计失败，API errors 单列 |
| `agent_execution` | `harness/` | 统一内部/source-derived 执行与确定性私有规则 |
| `agent_product` | `federated_asr_benchmark/` | 完整产品 + 原生环境/scorer；`success` 表示攻击成功 |
| `trace_judge` | `harness/ash.py judge` | 离线轨迹任务，独立统计 |

不混合不同轨道的分数。`unknown/invalid/excluded` 不作为安全通过；攻击测试缺少完成规则时不进入任务完成率分母。ASR 的 case 和 retrieval-event 单位保留原样。

## 外部数据与环境

仓库包含源码、schema、配置、固定索引、选择集、示例和测试。大型真实数据、官方第三方源码、虚拟环境、密钥和历史运行结果留在外部。

```powershell
$env:AGENT_EVAL_DATA_ROOT = 'D:\benchmark-data'
$env:AGENTDOJO_PYTHON = 'D:\runtimes\agentdojo\Scripts\python.exe'
$env:AGENTDYN_PYTHON = 'D:\runtimes\agentdyn\Scripts\python.exe'
$env:AGENTPOISON_PYTHON = 'D:\runtimes\agentpoison\Scripts\python.exe'
python federated_asr_benchmark/tools/build_case_index.py --data-root $env:AGENT_EVAL_DATA_ROOT --output-dir runs/rebuilt-index
```

`--data-root` 覆盖默认路径。完整产品实验 profile 中的模型、CLI 版本与 judge policy 继续按原配置锁定；需要原生 preflight 和真实运行证明资格。

LLM V2 真源继续是 `eval_mvp_V2/data/master_normalized_dataset_v2_final.jsonl`，派生统计是 `output/doc/v2_stats.json`，权威发布根为 `rule_eval_mvp/output_v2_final_authoritative/`。这些约定只绑定 LLM QA；数据与完整生产发布工具没有全部包含在仓库。

## 检查与项目约定

```powershell
python -m unittest discover -s tests -v
Push-Location harness
python -m unittest discover -s tests -v
Pop-Location
Push-Location federated_asr_benchmark
python -m unittest discover -s tests -v
Pop-Location
```

依赖外部数据/专用环境的集成测试会明确列出缺失资产并跳过。跳过不能视为该 benchmark 已通过正式验证。迁移来源与文件清单见 [migration_manifest.json](docs/integration/migration_manifest.json)，后续工作约定见 [AGENTS.md](AGENTS.md)。
