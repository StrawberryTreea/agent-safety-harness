# 生成协作者分发包

从仓库根运行现存打包器：

```powershell
.\.venv\Scripts\python.exe harness/tools/package_parallel_cli_eval.py --help
```

主要参数是 `--input`、`--out-root`、`--evaluators`、`--profiles`、`--assignment-mode shard|replicate`、`--calibration-size`、`--seed`、`--model`、`--openai-base-url`、`--anthropic-base-url`。参数格式和可用 profile 以帮助信息为准。

输入是含合格规则的内部 TaskSpec 集，不是单纯公开运行任务。打包前检查每条规则、来源许可、环境和版本。打包器执行规则校验；不能靠打包修补缺失的 oracle。

输出包会生成协作者运行配置和脚本，某些 profile 使用 Docker。Docker 脚本存在于生成包，不存在于仓库 `scripts/` 下。需在独立环境实跑生成包后再声称可分发；本地 mock 不证明某个 CLI/Docker profile 合格。密钥由运行者提供环境变量，不写进分发文件。

共享任务 schema 和输入配置见 [Harness](../README.md)。分发时保留公开运行与私有评分边界，接收轨迹后使用同版本 scorer，记录异常、排除、规则证据和资源信息。
