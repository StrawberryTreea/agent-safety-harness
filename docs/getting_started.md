# 从零安装与本地运行

以下所有命令在仓库根目录运行。当前验证平台：Windows PowerShell、Python 3.14。真实 benchmark 的解释器可能不同，见[配置](configuration.md)。

V1 的 [PR #2](https://github.com/StrawberryTreea/llm-safety-eval-research-pipeline/pull/2) 合并前，先执行 `git switch integrate-llm-agent-pipeline`。默认 `main` 尚未包含本轮整理；合并后使用 `main`。

## 1. 安装

```powershell
python --version
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
```

直接调用虚拟环境解释器，无需修改 PowerShell 执行策略。runtime 依赖统一在 `requirements.txt`；dev 文件引用它，测试使用标准库 unittest。

## 2. 一次验收整个仓库

```powershell
.\.venv\Scripts\python.exe scripts/check_repo.py
```

每次新建 `runs/check-<UTC时间>-<id>/`，包含 `summary.json`、三套测试日志、两种 Agent 输出、LLM 自测和分析。非零退出码表示失败，打开 summary 的 error 和对应 log。外部数据缺失的 skip 保留在日志中。

该入口执行本地合成示例，不调用模型 API。它清除继承的 `EVAL_*` 覆盖，为 LLM 示例强制使用参考答案自测与独立目录。其他模块的外部集成测试按当前资产是否存在运行或跳过。

## 3. 单独运行 Agent 示例

```powershell
.\.venv\Scripts\python.exe pipeline.py inventory
.\.venv\Scripts\python.exe pipeline.py smoke
.\.venv\Scripts\python.exe pipeline.py smoke --agent unsafe_stub
```

控制台返回输出目录。每个输出目录内：

```text
pipeline_manifest.json     步骤、退出码和原生统计
capture/                  公开执行轨迹和环境证据
score/results.jsonl       私有规则结果
score/summary.json        原生指标与有效性
score/report.md           原生报告
analysis/summary.json     失败/异常/分组计数
analysis/candidate_plans.jsonl  待验收候选计划
capture.log / score.log / report.log
```

safe 应为 3/3 安全通过；unsafe 应为 1/3，产生两个计划，接纳数仍为 0。未知不计安全通过。

## 4. 单独运行 LLM 自测

确认当前终端没有设置其他 `EVAL_INPUT_JSONL`、`EVAL_RUN_ROOT`、`EVAL_RUN_MODE` 覆盖，然后运行：

```powershell
.\.venv\Scripts\python.exe llm_eval/multi_model_rule_eval_prompt_only.py --config assets/samples/llm_self_test.yaml
.\.venv\Scripts\python.exe pipeline.py analyze --track llm_qa --results runs/llm-self-test/self_test_reference/results.jsonl --out runs/llm-demo-analysis
```

自测直接将三条多选题的参考答案作为输出，应得到三条 `api_status=success, passed=true`。这只检验读取/规则评分/聚合/分析。LLM 输出含 `eval.log`、每模型 `results.jsonl`/`summary.json` 和 `leaderboard.csv`。

LLM 评测器支持断点续跑；重复自测可能复用已有结果。需要独立新证据时使用统一验收，它每次建新目录。`analyze` 拒绝非空目录，重复独立例子时换 `--out`。

## 5. 配置真实目标

读[配置指南](configuration.md)，先冻结输入、模型/产品版本、权限、scorer 与预算，然后小批量验证。`pipeline.py plan` 仅检查路径并输出执行命令，不保证 API、外部环境或评分资格。

## 常见问题

| 现象 | 处理 |
|---|---|
| 找不到 PyYAML/jsonschema/psutil | 用同一个 `.venv\Scripts\python.exe` 安装 requirements，再运行 |
| INPUT_JSONL 不存在 | 相对路径从仓库根解析；先运行随仓库的 self-test 配置 |
| 没有匹配的 enabled 模型 | 核对 run_mode、provider 与 enabled；入口返回 2 |
| 模型运行异常 | 看 eval.log；入口返回非零，检查每条 API 状态与覆盖率 |
| 输出目录非空 | 换新目录；需要续跑时使用该原生入口的 resume 选项 |
| product preflight 失败 | 按提示准备专用环境、版本与凭证；不能用跳过代替正式资格 |
| 看到 unknown/invalid | 排查环境/工具/API/scorer，修好后重跑，不能记作安全通过 |

验收成功也可能有集成测试 skip；正式产品/benchmark 声明还需要真实运行证据。详见[验证记录](integration/validation.md)。
