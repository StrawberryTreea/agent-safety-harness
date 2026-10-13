# 配置与外部资产

相对路径按执行命令的工作目录解析。本文默认仓库根目录。密钥仅放环境变量；输入、模型、版本、预算与输出配置应随实验保存。

## LLM QA

入口：`eval_mvp_V2/multi_model_rule_eval_prompt_only.py --config <yaml或json>`。示例：[llm_self_test.yaml](../assets/samples/llm_self_test.yaml)。模型列表放 `evaluator_config.models`（或 `config.MODELS`），不能放顶层 `models`。

真实 API 配置示例，保存为 `work/my-llm.yaml`，先替换 endpoint、model、输入路径：

```yaml
evaluator_config:
  input_jsonl: assets/samples/llm_qa_demo.jsonl
  run_root: runs/my-llm-smoke
  run_mode: api
  MAX_RETRIES: 2
  models:
    - name: my_model
      provider: openai_compatible
      enabled: true
      api_key_env: EVAL_API_KEY
      base_url: https://api.example.com/v1
      model: replace-with-model-id
      temperature: 0
      max_workers: 1
      rpm: 30
      tpm: 10000
```

```powershell
$env:EVAL_API_KEY = '<your-key>'
.\.venv\Scripts\python.exe eval_mvp_V2/multi_model_rule_eval_prompt_only.py --config work/my-llm.yaml
```

base_url 不带 `/chat/completions`。支持的 provider 与超时、重试、token 参数以评测器顶部 CONFIG 为准。`EVAL_INPUT_JSONL`/`EVAL_RUN_ROOT`/`EVAL_RUN_MODE` 等环境覆盖优先于文件，换实验前检查。示例输入仅三条；正式实验的小子集应先独立导出并冻结。费用按所用 provider 的报价和 usage 单独核算。每条 API error 保留为未评分，不因程序结束就视作覆盖完成。无匹配启用模型返回 2；模型级异常返回非零。

V2 的 canonical 真源、统计与权威发布路径是外部完整语料协议，见[协议契约](../references/protocol-contract.md)。合成示例不属于该语料，也不生成正式 leaderboard。

## 内部 Agent 与离线 judge

- [agents.json](../harness/config/agents.json)：CLI 命令及非交互参数，`--agent cli:<id>`。
- [models.json](../harness/config/models.json)：API provider、base_url、model、api_key_env、超时，`--agent model:<id>`。
- [policies.json](../harness/config/policies.json)：工具/运行策略。
- 使用 `--agents-config` / `--models-config` 指向实验副本，不改共享配置来隐式改变已有实验。

执行过程、工具 mock/工作区证据和私有规则见 [Harness](../harness/README.md)。内部 CLI 配置可以有 harness 提示包装，不能自动称为完整产品默认系统行为。

## 完整产品与原生 benchmark

- [product_registry.json](../federated_asr_benchmark/product_registry.json)：产品版本与运行约束。
- [benchmark_registry.json](../federated_asr_benchmark/benchmark_registry.json)：scorer、分母单位、覆盖率与 judge policy。
- [run_request schema](../federated_asr_benchmark/schemas/run_request.schema.json) 和 [请求示例](../federated_asr_benchmark/examples/requests/codex_saber.json)。

现有受控 profile 固定 DeepSeek V4 Flash 与特定 CLI 版本/Qoder BYOK runtime ID。它们不是任意模型通用模板。变更需另建版本化 profile，更新路由审计、preflight 与可比性证据。

```powershell
$env:AGENT_EVAL_DATA_ROOT = 'D:\benchmark-data'
$env:AGENTDOJO_PYTHON = 'D:\runtimes\agentdojo\Scripts\python.exe'
$env:AGENTDYN_PYTHON = 'D:\runtimes\agentdyn\Scripts\python.exe'
$env:AGENTPOISON_PYTHON = 'D:\runtimes\agentpoison\Scripts\python.exe'
```

`--data-root` 优先于默认数据根。没有显式路径时，兼容逻辑会寻找旧布局 `数据集/评测数据`；新协作者应明确配置。专用 runtime 的源码、数据、Docker 镜像和锁文件依赖需单独准备，根 requirements 只安装控制/测试依赖，不能替代它们。

准备好资产后从根目录运行：

```powershell
.\.venv\Scripts\python.exe federated_asr_benchmark/tools/build_case_index.py --data-root $env:AGENT_EVAL_DATA_ROOT --output-dir runs/rebuilt-index
.\.venv\Scripts\python.exe federated_asr_benchmark/tools/preflight.py --data-root $env:AGENT_EVAL_DATA_ROOT
```

preflight 会检查外部资产、产品与环境 pins、judge 配置及发布阻断项。失败时先修复。付费批量运行前固定产品状态隔离、case 选择、费用/token/时限与有效覆盖率；细节见[产品入口](../federated_asr_benchmark/README.md)。
