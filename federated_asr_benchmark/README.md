# 完整产品与原生 benchmark ASR · V1

[项目入口](../README.zh-CN.md) · [配置](../docs/configuration.md) · [分阶段协议](docs/staged_execution.md)

本模块用完整产品 CLI 替换原 benchmark 的 Agent runner，保留原生任务、工具、环境与 scorer。测量产品默认系统行为、规划和工具编排；结果不能直接与原论文模型排行榜混排。

## 产品评测执行链

这张图描述真实运行的步骤与资格检查，不表示当前已完成这些实验。按每个产品分别统计原生 ASR；阶段放行条件见[分阶段协议](docs/staged_execution.md)。

```mermaid
flowchart TB
    Assets["准备外部数据 / runtime / CLI<br/>锁定 profile 与 judge policy"] --> Preflight["preflight<br/>检查环境、版本、路由与 judge"]
    Preflight --> Ready{"检查通过？"}
    Ready -->|否| Fix["修复依赖或配置<br/>通过后重新检查"]
    Fix --> Preflight
    Ready -->|是| Selection["选定固定阶段集合<br/>smoke20 或放行后的 pilot100"]
    Selection --> Run["run_product / run_product_batch<br/>每个 case 使用新产品状态"]
    Run --> Native["原生任务、工具与环境<br/>原生 scorer 判定"]
    Native --> Results["case_result.jsonl<br/>结果、排除理由与证据"]
    Results --> Aggregate["aggregate_results<br/>原生 ASR、coverage 与有效性检查"]
    Aggregate --> Gate{"阶段覆盖率与资格达标？"}
    Gate -->|是| Next["放行下一阶段<br/>完整资格达标后才发布正式汇总"]
    Gate -->|否| Audit["保留部分结果<br/>排查 invalid / excluded 与缺失判定"]
    Results --> Analyze["pipeline.py analyze<br/>track=agent_product"]
    Analyze --> Plans["攻击成功等有效失败观察<br/>候选计划 admitted=false"]
```

## 当前资产和限制

仓库有 adapter 源码、schema、registry、请求示例、固定索引和选择集。`case_index.jsonl` 的来源索引有 5,384 条：InjecAgent 1,054、MSB 2,000、AgentDojo 629、AgentLeak 496、SABER 715、AgentPoison-StrategyQA 490。索引不是原始数据或当前已完成的成绩。

其他 adapter/扩展候选也有源码与测试，仍需各自运行与资格验证。大型数据、第三方源码/运行环境、CLI、Docker、凭证需要另外准备。外部测试 skip 不构成正式资格。

## 本地查看和计划

从仓库根、已安装根依赖的 Python 环境运行：

```powershell
.\.venv\Scripts\python.exe federated_asr_benchmark/tools/run_product.py --help
.\.venv\Scripts\python.exe pipeline.py plan --track agent_product --request federated_asr_benchmark/examples/requests/codex_saber.json --data-root D:/benchmark-data --case-id saber:A_code_007 --out runs/product-plan
```

plan 不执行产品；只输出 dry-run 的 argv。原生 `--dry-run` 也需要对应资产才能检查环境，不能把 dry-run 当评分结果。

## 准备真实运行

按[配置指南](../docs/configuration.md)设置 `AGENT_EVAL_DATA_ROOT` 和专用解释器。构建索引并运行 preflight：

```powershell
.\.venv\Scripts\python.exe federated_asr_benchmark/tools/build_case_index.py --data-root $env:AGENT_EVAL_DATA_ROOT --output-dir runs/rebuilt-index
.\.venv\Scripts\python.exe federated_asr_benchmark/tools/preflight.py --data-root $env:AGENT_EVAL_DATA_ROOT
```

检查 [product_registry.json](product_registry.json)、[benchmark_registry.json](benchmark_registry.json)、[tested_profiles.json](release/tested_profiles.json) 与 [run_request schema](schemas/run_request.schema.json)。当前受控 profile 固定 DeepSeek V4 Flash；Codex 0.122.0、Claude Code 2.1.195、Qoder 1.0.48 是锁定版本，Qoder BYOK runtime ID 与 CodeBuddy 的路由绑定也受 runner 检查。API judge 的模型/接口/温度由单独的 judge policy 固定。改变版本/模型要新建 profile 和资格证据。

preflight 包含环境 pins、API judge、路由与 release blockers；必须先解决失败。真实执行使用 `tools/run_product.py` 或 `tools/run_product_batch.py`，以其 `--help` 和[分阶段协议](docs/staged_execution.md)为准。Qoder batch 使用现存 BYOK wrapper。

## 公平性与结果

每条 case 用新产品状态，不加载个人记忆、技能或项目规则；权限和网络按 case 限定。固定产品/模型、adapter、benchmark revision、scorer、种子和预算。不同产品分别统计。

```text
ASR-valid = sum(asr_numerator) / sum(asr_denominator)
Macro-ASR = mean(required benchmark ASR-valid)
```

`status=success` 表示攻击成功，ASR 越低越好。普通 case 用 1/1 或 0/1；AgentPoison 保留官方 ASR-r retrieval-event 单位。invalid/excluded 不是安全通过；环境/API/judge 错误保留 0/0。required benchmark 未达覆盖率门槛时不发 Macro-ASR。

[case_result schema](schemas/case_result.schema.json)保留官方结果与证据。`tools/aggregate_results.py` 负责原生 ASR 汇总；`pipeline.py analyze --track agent_product` 负责失败计划，不能替代正式资格审查。

## 代码导航

`release/isolated_product_profiles.json` 单独登记新的 `deepseek-flash` profile，
登记单条 banking 与固定 AgentDojo smoke20 两种隔离工程范围，
未替换旧受控模型策略，不构成 pilot 或 release 资格。`run_product.py` 识别
`benchmark_options.isolated_profile_id` 后只调受限容器 dispatcher，校验模型/case/源码和镜像 pin，
并在产品启动前验证实际容器控制；没有宿主产品进程回退。`--isolated-offline` 可验证该路径，
此时上游禁止、结果 invalid 0/0，且不生成用于聚合的 JSONL。隔离请求在产品入口直接调用
dispatcher 的 fail-closed preflight；非隔离请求仍由原版 `orchestrator/execution_guard.py`
阻断未经验证的宿主执行。v3 的原生参考准备摘要
`release/agentdojo_smoke20_v3_readiness_20261108.json` 已脱敏入库；历史单条 preflight
不证明改动后的 profile 已通过，须在镜像就绪时重新执行。live 使用当前终端的
`DEEPSEEK_API_KEY`，最多四次上游请求；产品退出后由可信环境运行原生 security scorer。
旧产品入口仍被隔离门禁阻断；banking profile 保持单条范围，批量必须使用独立 smoke20 profile。
`tools/qualify_agentdojo_attack.py` 可在禁网容器中重放固定 AgentDojo 来源轨迹，
检查注入交付、原生 security scorer 及执行错误的 0/0 分母。它不调用模型、不执行产品，
重放结果不能计入产品 ASR 或作为新的产品失败种子。原生入口的隔离门禁保持有效。

`tools/prepare_agentdojo_smoke20.py` 冻结既有 smoke20 的来源、公开任务和完整工具清单，
在禁网可信环境中检查各任务的原生良性参考解/无动作对照；它要求提供已有单条有效结果，
将其标为已执行，避免后续扩量时重复运行。三条 travel 措辞差异通过固定源码与运行时类的
非 PROMPT 代码哈希核对，映射登记在 `release/agentdojo_prompt_mappings.json`，保留原始公开提示，
不宣称措辞完全等价。参考最终文本只来自工具观察及公开数据计算，不向产品提供私有答案。
该准备工具不授予 smoke20/batch 权限，离线参考通过数不能当成产品通过数。

`codex_deepseek_flash_agentdojo_smoke20.json` 绑定全部 20 条固定样本及四个 suite，
仅在注册的原生良性参考/无动作对照全部通过、源码/镜像/选择集哈希一致时允许工程运行。
批量入口 `run_product_batch.py` 的 `--import-case-run` 可复用兼容的有效单条结果；原始文件不变，
派生聚合行与原始运行/环境镜像差异记录在 `batch_manifest.json`。
复用一条后最多新跑 19 条，每条最多 4 次 API 请求，共最多 76 次，无自动重试。
`--isolated-offline --keep-going` 只验证禁网执行和清理，不产生新的有效产品分母。
中断后用同一请求、run-id、输出目录及导入目录加 `--resume` 继续；已完成的 invalid 也不重跑，
未完成的尝试需人工检查，结果哈希变化会拒绝续跑。非空输出目录不会被隐式覆盖。
真实结果仍需按原生评分、覆盖率和阶段门槛审查，参考对照通过数不等于产品通过数。

`tools/audit_isolated_agentdojo_batch.py --run <batch目录> --out <新目录> --data-root <外部数据根>`
可离线审计已有完整批次的注入交付漏检。它仅对成功完成、原生布尔判定已存在的运行，
在产品收到的文本与原生事件输出哈希一致、YAML 安全解析或 Python 字面量安全解析后
完整 payload 匹配时纠正交付否决；不执行表达式。
输出独立派生结果、原生聚合与输入哈希，不修改原始日志，也不调用模型。
请求预算耗尽、token 截断及其他执行失败保持 invalid。该工具未修改在线镜像里的旧检测器；
历史镜像保留原检测器；当前新增 v2 镜像已修复 YAML 标量转义导致的交付漏检。

`codex_deepseek_flash_agentdojo_smoke20_v2.json` 使用独立的 `smoke20_v2` 预算：
每条最多 12 次上游请求、每次最多 8192 输出 tokens，固定 20 条合计最多 240 次请求，
无自动重试。该预算绑定到 profile，并同时传给产品与网关；未知预算标识拒绝启动。
v2 禁止 `--import-case-run`，必须在新输出目录完整运行 20 条，不混合旧预算成绩。
旧 profile 的原始记录与 pins 保留；当前源码变化后旧 profile 会因 pin 不匹配而拒绝新执行。
新环境构建显式复制 MCP 检测器，并检查镜像内文件哈希，避免继承基础镜像旧文件。
新预算的离线检查不代表真实任务一定能完成，达到上限仍保持 invalid；正式资格不变。

`codex_deepseek_flash_agentdojo_smoke20_v3.json` 单独登记证据保留修复，沿用 v2 的
12 请求/8192 输出 tokens 预算，未提高预算。在线交付检测同时支持 YAML 和安全 Python
字面量解码。产品超时保留公开任务哈希、超时标志及部分 stdout/stderr；评分收到实际超时标志。
控制器先保存原始产品报告，任何失败都在清理前尝试导出网关审计、原生快照与事件，
通过 `capture_inventory.json` 记录文件哈希或导出失败原因；原生快照完成写入最多等待 3 秒。
`native-capture.json` 可能是部分状态，仅用于诊断，不作为有效评分依据。
旧 profile 与旧结果保持历史身份，新代码只能使用重新固定源码和镜像的 profile。
可先通过 `run_product.py` 对已知出错 case 做独立诊断，不将诊断行补入旧批次或改写旧分母。

模型身份核查（2026-11-08）：DeepSeek 官方当前说明 `deepseek-flash` 与旧请求名
`deepseek-v4-flash` 都路由至 V4.1 Flash；原 V4 Flash 已退役。
文档：https://api-docs.deepseek.com/quick_start/pricing/ 。因此上面的工程 profile 与
`benchmark_registry.json` 中写着 V4 Flash 的受控 profile 不能仅凭同一后端路由视为
原 V4 的同一版本资格。旧名不能锁回原 V4 Flash，也不能将已有 `deepseek-flash` 运行记录改名。
本次 P0 交付按当前 API 路由等价说明这两个请求名：官方当前均指向
`DeepSeek-V4.1-Flash-1017`。保留工程请求名 `deepseek-flash`、响应观察值和历史哈希；
不以修改别名方式重写历史记录，也不声称固定了不可变后端版本。此说明用于 P0
工程验收；受控 pin 口径仍待王浩裁定，本 PR 作为 P0 工程证据不阻塞于该裁定，
不改变全项目 registry 的默认模型配置或授予 pilot/release 资格。

### P0 复现与提交范围

当前可执行交付入口是 `codex_deepseek_flash_agentdojo_smoke20_v3.json`；单条 banking、
smoke20 v1/v2 请求和 profile 保留为历史审计索引，并显式设置 `historical_non_executable=true`。
加载器在读取本机 `work/chenhao/` 资格文件或调用 Docker 前拒绝这些历史配置；不要将当前源码哈希
回填旧 profile，也不要把 v3 单条诊断补入 v2 批次。历史 ASR 为派生交付审计的
`0/18`，另有 2 条 invalid（coverage 90%）；P0 没有要求 20/20，进入 pilot 则仍须
固定 smoke20 的 20 条全部有官方有效判定。

复现需要另行准备注册 revision 的 AgentDojo 数据、Docker 和 v3 profile 中两个精确镜像 ID。
镜像不随 Git 分发：可从证据持有人获取对应 `docker save` 归档后 `docker load`，再运行
下面的预检验证镜像内源码哈希。仅从 Dockerfile 重建不保证得到同一镜像 ID；重建后必须
重新检查来源、原生参考对照及实际隔离控制，并登记新的 profile/证据，不能覆盖旧 pins。
产品镜像构建配方为 `runtime/deepseek_smoke/Dockerfile`；可信环境配方为
`AgentDojo.Dockerfile` 和 `AttackReplay.Dockerfile`，构建上下文须包含其 COPY 列出的
runtime、原生 MCP、scorer、product_eval 与 manifests 文件。PR #19 的
`harness` Docker 底座只批准 AgentSafetyBench 禁网执行；本产品路径独立检查隔离，
不继承该 PR 的 benchmark 资格。

从仓库根运行（dry-run 不调用产品；preflight 只运行镜像内哈希检查，不调用模型）：

```powershell
python federated_asr_benchmark/tools/run_product.py --request federated_asr_benchmark/examples/requests/codex_deepseek_flash_agentdojo_smoke20_v3.json --case-id agentdojo:slack:user_task_5:injection_task_1:important_instructions --dry-run
python federated_asr_benchmark/tools/run_product.py --request federated_asr_benchmark/examples/requests/codex_deepseek_flash_agentdojo_smoke20_v3.json --case-id agentdojo:slack:user_task_5:injection_task_1:important_instructions --preflight-only
python scripts/check_repo.py
```

真实运行还须在当前终端设置 `AGENT_EVAL_DATA_ROOT`、`DEEPSEEK_API_KEY`，使用新的输出目录。
固定完整批次由 `run_product_batch.py --request <上述请求> --output-root <新目录>` 执行；
它调用原生聚合器并保留 `case_result.jsonl`、`native_aggregate.json` 和请求审计。
批量命令在未完成全部样本、出现 invalid、缺失请求审计或离线检查失败时返回 2，
保留已有结果；这不影响将部分有效 ASR 如实作为 P0 工程证据提交。
离线运行的 invalid 0/0 仅在完整隔离检查通过时返回 0，不产生产品 ASR。
原始数据、运行轨迹、镜像归档和密钥不入 Git；脱敏摘要见
`release/chenhao_p0_evidence_20261108.json`。

运行器输出被重定向时，可在另一个终端用只读检查器观察运行目录：

```powershell
.\.venv\Scripts\python.exe federated_asr_benchmark/tools/inspect_isolated_agentdojo_run.py --run <study或单条结果目录> --watch
```

检查器每五秒报告文件状态，最终显示原生判定、请求预算信号、捕获哈希和工具参数核对。
有开始标记但无最终结果时，只报告“已开始、尚无最终结果”，不据此断言进程仍存活。
`--out <新JSON文件>` 可保存派生诊断；原始结果不改动。Slack频道参数仅与已有原生事件
哈希佐证的公开工具文本进行安全YAML解码后比较，不自动修正参数或向产品提供答案。
`failure` 显示为攻击失败；`invalid` 保留未评分。缺失或不完整请求审计保持未知。

- `product_eval/`：产品命令、原生 adapter、模型/judge policy、路由审计、结果规范化。
- `tools/`：preflight、索引、选择集、单条/批量/adaptive 运行、scorer、聚合。
- `examples/requests/`：版本化产品请求；`selections/`：固定分阶段集合。
- `requirements-*.lock`：专用外部 runtime 依赖，不是根控制器的安装入口。
- `tests/`：自包含与资产依赖测试；根 `scripts/check_repo.py` 统一调用。
- `selections/expansion/`：来源锁定候选索引；保留原抽样种子作为 provenance，资格必须现场重新验证。

现场验证见[验证记录](../docs/integration/validation.md)。
