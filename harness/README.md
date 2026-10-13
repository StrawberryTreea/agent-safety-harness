# 内部 Agent Safety Harness

[项目入口](../README.zh-CN.md) · [配置](../docs/configuration.md) · [适配状态](BENCHMARK_ADAPTER_STATUS.md)

本模块把来源任务转换为内部 TaskSpec，记录执行轨迹并用私有确定性规则评分；离线 trace judge 单独运行。来源映射不等于官方 benchmark 复现，mock 不代表真实产品成绩。

## 最小运行

从仓库根目录、已安装根 requirements 的环境运行：

```powershell
.\.venv\Scripts\python.exe pipeline.py smoke
.\.venv\Scripts\python.exe pipeline.py smoke --agent unsafe_stub
.\.venv\Scripts\python.exe harness/ash.py --help
.\.venv\Scripts\python.exe harness/ash.py list
```

[Smoke 说明](smoke/README.md)；所有结果写到新目录。

## 原生执行链

下面完整使用随仓库的合成任务与示例规则：

```powershell
.\.venv\Scripts\python.exe harness/ash.py capture --agent safe_stub --input harness/smoke/public_tasks.jsonl --out runs/manual-agent/capture
.\.venv\Scripts\python.exe harness/ash.py score --private-input harness/smoke/private_rules.jsonl --capture-run runs/manual-agent/capture --out runs/manual-agent/score
.\.venv\Scripts\python.exe harness/ash.py report --run runs/manual-agent/score
.\.venv\Scripts\python.exe pipeline.py analyze --track agent_execution --results runs/manual-agent/score/results.jsonl --out runs/manual-agent/analysis
```

`capture` 只使用运行任务，保存 `traces.jsonl`、manifest 和环境证据。`score` 在独立阶段按 task_id 配对私有规则，生成 `results.jsonl`。`report` 生成 summary 与 report；分析生成未接纳候选。真实规则不能传进产品提示。随仓库公开的 smoke rules 是合成测试 fixture。

支持 `safe_stub`、`unsafe_stub`、`model:<id>`、`cli:<id>`；真实模型/CLI 配置和 `--workers` 通过原生参数指定。见 [agents](config/agents.json)、[models](config/models.json)。内部提示包装与完整产品轨道分别记录。

## 数据与接口

实现位于 [src/agent_safety_harness](src/agent_safety_harness)，包括 schema、importers、agents、runners、evaluation、reporting、adapters、tool backends。

- TaskSpec：task_id、benchmark、profile、user_goal、tools、initial_state、labels、metadata；来源和环境版本保留在 metadata。
- TraceEvent：工具调用、响应、状态变化、policy 与 sandbox 证据。
- ResultSpec：安全违规、任务完成、安全通过、unknown、规则命中与资源信息。
- `import agentharm` / `import mapped` 按[导入配置](config/benchmark_imports.json)转换来源。
- `judge --benchmark ... --input ... --out ...` 评估已有轨迹，不与执行任务混算。
- `review` 检查已评分通过记录的未参与/遗漏攻击证据；`mcp-server` 为 CLI 暴露任务工具。

各子命令参数以 `harness/ash.py <command> --help` 为准。执行失败或不可评分保留 unknown；攻击任务可以只含 safety_violation 规则，没有 task_success oracle 时不输出任务完成率。

## 分发与环境

[分发工具](docs/distribution.md)会在输出包中生成运行脚本；这些生成的 Docker 脚本不是 checkout 已有入口。真实 benchmark 环境、Docker/VM 或特定 CLI 需单独准备。导入、工具可用、规则可评估、正式资格是不同状态。

模块测试在 harness 目录用 `python -m unittest discover -s tests -v`；统一验收从根运行 `scripts/check_repo.py`。缺外部资产的 skip 必须报告。
