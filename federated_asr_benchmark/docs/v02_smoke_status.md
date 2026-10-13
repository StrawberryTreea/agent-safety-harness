# v0.2 Smoke5 状态

更新时间：2026-08-08

## 运行规则

- 被测产品：Codex、Claude Code、Qoder。
- 六个 benchmark：InjecAgent、MSB、AgentDojo、AgentLeak、SABER、AgentPoison-StrategyQA。
- 每个“产品 x benchmark”固定先跑 5 条 smoke；所有单元通过后才允许启动 10%。
- 被测模型统一为 DeepSeek V4 Flash。SABER 的 API judge 也固定为 `deepseek-v4-flash`。
- 性能指标只报告 ASR。coverage、invalid、模型路由和日志错误只用于门禁与审计。

## 已完成 Smoke

| 产品 | Benchmark | 有效数据 | ASR | 日志门禁 |
|---|---|---:|---:|---|
| Codex | InjecAgent | 5/5 | 0/5 = 0% | 通过 |
| Codex | MSB | 5/5 | 1/5 = 20% | 通过 |
| Codex | AgentDojo | 5/5 | 0/5 = 0% | 通过 |
| Codex | AgentLeak | 5/5 | 1/5 = 20% | 通过 |
| Codex | SABER | 5/5 | 3/5 = 60% | 通过 |
| Codex | AgentPoison live DPR | 5/5 | 2/16 = 12.5% | 通过 |
| Claude Code | InjecAgent | 5/5 | 0/5 = 0% | 通过 |
| Claude Code | MSB | 5/5 | 1/5 = 20% | 通过 |
| Claude Code | AgentDojo | 5/5 | 0/5 = 0% | 通过 |
| Claude Code | AgentLeak | 5/5 | 2/5 = 40% | 通过 |
| Claude Code | SABER | 5/5 | 2/5 = 40% | 通过 |
| Claude Code | AgentPoison live DPR | 5/5 | 1/12 = 8.33% | 通过 |
| Qoder | InjecAgent | 5/5 | 0/5 = 0% | 通过 |
| Qoder | MSB | 5/5 | 1/5 = 20% | 通过 |
| Qoder | AgentDojo | 5/5 | 0/5 = 0% | 通过 |
| Qoder | AgentLeak | 5/5 | 1/5 = 20% | 通过 |
| Qoder | SABER | 5/5 | 1/5 = 20% | 通过 |
| Qoder | AgentPoison live DPR | 5/5 | 2/13 = 15.38% | 通过 |

三个产品共 90 条均满足：产品状态 completed、无 invalid、模型路由为
DeepSeek V4 Flash、stderr 为空、MCP 日志无 server/protocol/transport 错误。

## 已修复问题

- SABER 工具输出中的孤立 Unicode surrogate 曾使 Codex MCP 连接关闭，造成 2 条假
  `Incapable`。修复协议、事件和 native JSON 序列化并重跑后，两条均为真实攻击成功。
- AgentLeak 的 artifact 工具和 scorer 曾在相同 surrogate 上失败。修复全部落盘边界后，
  3 条受影响记录已重跑，日志干净。
- InjecAgent/MSB 的 MCP capability 响应异常记录已重跑，`resources/list`、
  `resources/templates/list` 和 `prompts/list` 均正常。
- v0.2 的 18 份 request 曾将 `output_dir` 多上溯一层；生成器与请求均已修正。
- SABER 每条结果现在保存本地 `official_judgment.json`，避免官方工作目录被后续重跑覆盖。
- `audit_smoke.py` 现在把 MCP server/protocol 错误和传输错误纳入 fail-closed 门禁。
- AgentPoison 已恢复官方实时 DPR `WikiEnv`，使用官方优化 trigger 和 9,251 条语料
  embedding。MCP 服务改为首次 `search` 时加载 DPR，Claude Code 可先完成协议握手和
  工具发现；Codex、Claude Code 的固定 5 条均已重跑并通过。
- `audit_smoke.py` 现在通过 adapter preflight 实时校验 AgentPoison 的语料/缓存哈希、
  trigger、CUDA 和官方 scorer，`agentpoison_formal_release_ready=true`。

## Qoder 路由说明

Qoder GUI 的 DeepSeek-V4-Flash BYOK 配置已保存并启用。CLI 与 GUI 使用不同的配置存储，
runner 因此只在每次执行的一次性隔离目录内生成等价的 `modelConfigs.customModels`，执行后
立即删除，不把 API key 写入命令、结果或长期配置。运行时 init 明确报告
`DeepSeek-V4-Flash`，`total_credits=0`。

临时模型配置的 base URL 固定为 `https://api.deepseek.com`，但 Qoder Quest 客户端的 agent
请求实际发送到 Qoder 编排端点 `api3.qoder.sh/algo/api/v2/service/pro/sse/agent_chat_generation`；
这是产品自身的代理架构，不是直接客户端到 DeepSeek。每条结果保存脱敏
`product_network_route.json` 记录 operation、host 和 path。Qoder Security hook 也在隔离环境中
启用并通过。

## 10% 状态

18 个“产品 x benchmark”smoke 单元已全部通过，机器审计
`ready_for_ten_percent=true`。计划数为每产品 539 个执行单元：InjecAgent 105、MSB 200、
AgentDojo 63、AgentLeak 50、SABER 72、AgentPoison 49。

机器可读审计报告位于
`.codex_tmp/federated_asr_runs/v0.2/smoke_audit.json`。
