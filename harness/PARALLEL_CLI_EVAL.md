# 并行评测分发

推荐按“每人负责一个模型”的方式分发。每位评测人员使用自己的模型配置，在同一份任务上
运行四个固定版本 CLI，便于比较同一模型在不同 Agent CLI 下的行为。

配置文件只写模型 ID 和协议地址，不允许包含 API Key：

```powershell
python tools\package_parallel_cli_eval.py `
  --input <rule-ready.tasks.jsonl> `
  --out-root release\model_owner_eval `
  --profiles config\model_profiles.local.json `
  --calibration-size 0 --seed 42
```

`--profiles` 自动使用 `replicate`，每个 ZIP 含相同任务但使用不同模型配置。只分发对应的
`evaluator_NN.zip`，不要分发 `admin_manifest.json`、登录状态或 API Key。

## 隔离

- Docker 镜像固定安装四个 CLI 版本。
- 每个包使用自己的 `state/` 和 `outputs/`。
- Codex、Claude、CodeBuddy、Qoder 均使用包内配置目录。
- CLI 子进程只继承显式允许的环境变量。
- 容器根文件系统只读，仅 `state/` 和 `outputs/` 可写。
- 每题在临时工作区运行，并通过任务级 MCP 获取真实 benchmark 工具。

## 结果要求

每个任务直接产生 `task_success` 和 `safety_violation`。不调用外部裁判模型。
环境或规则执行失败的任务标记为 `unknown`，不得计入两个指标。
