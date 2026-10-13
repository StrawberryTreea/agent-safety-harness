# 合成 smoke fixtures

[返回 Harness](../README.md)

`public_tasks.jsonl` 包含三个合成运行任务，`private_rules.jsonl` 是配对的合成评分规则。这里的 rules 可公开，仅用于验证接口；正式任务的私有 oracle 必须隔离。

从仓库根目录运行：

```powershell
.\.venv\Scripts\python.exe pipeline.py smoke
.\.venv\Scripts\python.exe pipeline.py smoke --agent unsafe_stub
```

默认创建唯一 `runs/smoke-.../`。safe 预期 3/3 通过，unsafe 预期 1/3 通过与两个未接纳计划。目录包含 capture、score、report 与 analysis，详见[上手指南](../../docs/getting_started.md)。

旧的原生 PowerShell 包装器 `harness/smoke/run_smoke.ps1` 使用 PATH 上的 python，默认输出在 `harness/smoke/runs/smoke-.../`，只执行 capture/score/report；需要统一分析时使用 pipeline 入口。指定非空输出目录会失败。
