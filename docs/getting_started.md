# 从零安装与本地运行

以下所有命令在仓库根目录运行。当前验证平台：Windows PowerShell、Python 3.11 与 3.14。真实 benchmark 的解释器可能不同，见[配置](configuration.md)。

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

## 6. 比较 LLM / Agent 目标候选

先对基线运行 `analyze`。若基线目标是 JSON/YAML 提示或策略配置，可用显式白名单 recipe 构建并登记候选：

```powershell
python pipeline.py target-build --baseline-asset work/targets/agent-v1.yaml --recipe work/evolution/agent-v2-recipe.yaml --failures runs/baseline/analysis/failures.jsonl --out runs/evolution/agent-v2-build
```

提示、策略、工具配置、代码、模型路由或训练插件等外部候选，也可直接登记；登记不会自动修改或执行候选：

```powershell
python pipeline.py target-register --track agent_execution --baseline-id agent-v1 --candidate-id agent-v2 --change-type policy --candidate-asset work/candidates/agent-v2.json --failures runs/baseline/analysis/failures.jsonl --out runs/evolution/agent-v2-registration
```

复制并填写 `assets/templates/target_evolution_policy.yaml` 与 `assets/templates/target_evaluation_manifest.json`，在同一冻结安全集和独立良性回归集上分别运行基线/候选，然后执行：

```powershell
python pipeline.py target-compare --candidate-manifest runs/evolution/agent-v2-registration/target_candidate.json --baseline-results runs/baseline/score/results.jsonl --candidate-results runs/candidate/score/results.jsonl --baseline-regression-results runs/baseline-benign/score/results.jsonl --candidate-regression-results runs/candidate-benign/score/results.jsonl --evaluation-manifest work/evolution/development-set.json --policy work/evolution/policy.yaml --out runs/evolution/agent-v2-development
```

开发比较通过只输出 `development_pass`。停止调优后使用对候选隐藏的封存发布集，才可能输出 `promote`；该记录也不会执行部署。完整要求见[目标进化契约](../references/target-evolution-contract.md)。

内部 Agent 候选可从 `assets/templates/target_agent_config.yaml` 与 `target_evaluation_job.yaml` 创建配置，然后自动执行配对安全集和良性回归集：

```powershell
python pipeline.py target-evaluate --candidate-manifest runs/evolution/agent-v2-build/registration/target_candidate.json --job work/evolution/development-job.yaml --policy work/evolution/policy.yaml --out runs/evolution/agent-v2-evaluation
```

真实 `model:<id>` 或 `cli:<id>` 目标分别从 `assets/templates/target_model_config.yaml`、`target_cli_config.yaml` 创建。执行前先运行不产生外部调用的预检：

```powershell
python pipeline.py target-preflight --candidate-manifest runs/evolution/agent-v2-build/registration/target_candidate.json --out runs/evolution/agent-v2-preflight
```

预检把报告写入 `readiness.json`，检查凭据变量是否存在但不读取/记录值，也不调用 `--version`；CLI 身份以解析路径、文件大小和 SHA-256 记录。只有在报告 `ready=true` 且明确接受 API 费用或本地命令执行后，才给 `target-evaluate` 增加 `--allow-external-targets`。目标提示/策略会物化到每个 run 的 `runtime_config/`；密钥值只放环境变量，不写入目标资产。

默认只允许本地 stub。真实 `model:`、`reference:` 或 `cli:` 目标可能使用凭据、API 预算或本地 CLI，调用者必须显式添加 `--allow-external-targets`。当前没有为 LLM QA、产品或离线轨迹轨道实现同类调度。

可先运行 `python pipeline.py target-smoke --out runs/target-smoke` 验证本地控制链。它只使用 safe/unsafe stub 与三条合成任务，不调用模型 API，也不证明真实目标优化有效。

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
