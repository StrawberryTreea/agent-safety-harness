# 整合验证记录

验证日期：2026-10-13。环境：Windows PowerShell、Python 3.14.4、仓库本地 `.venv` 与 `requirements-dev.txt`。下列结果来自本机实际执行；GitHub Actions 的结果单独以 PR checks 为准。

## 自包含检查

| 范围 | 发现测试数 | 通过 | 跳过 | 失败/错误 |
|---|---:|---:|---:|---:|
| 根目录 `tests/` | 33 | 33 | 0 | 0 |
| `harness/tests/` | 170 | 160 | 10 | 0 |
| `federated_asr_benchmark/tests/` | 192 | 138 | 54 | 0 |
| 合计 | 395 | 331 | 64 | 0 |

根目录检查覆盖原有数据/协议工具与新增的指标方向、未知结果、重复身份、产品模型/版本隔离、候选未接纳、输出保护、原生配置传递和中文阶段路由。harness 的 10 项跳过依赖未迁入的 Agent-SafetyBench、MSB 与 curated 发布数据。产品层 54 项跳过依赖明确命名的外部数据或专用解释器；独立 mock、评分和配置测试仍执行。任何真实断言或运行错误不会被转成跳过。

运行命令：在根目录和各模块目录分别执行 `python -m unittest discover -s tests -v`。已添加 Windows/Python 3.14 的 CI 工作流运行这些检查及两种 smoke。

还从 Git 暂存区导出全新目录，使用相同依赖解释器复跑三套测试与两种 smoke，结果一致。该目录没有原 checkout 的缓存、运行结果或外部数据，用于确认提交内容可独立启动。

## 实际 smoke 与失败分析

`pipeline.py smoke --out runs/final-safe` 与 `pipeline.py smoke --agent unsafe_stub --out runs/final-unsafe` 均实际调用 capture、private score、report 和 analysis，所有子步骤返回 0。

| 合成 Agent | 有效任务 | unknown | pass / fail | 失败观察 | 候选计划 | 接纳任务 |
|---|---:|---:|---:|---:|---:|---:|
| `safe_stub` | 3 | 0 | 3 / 0 | 0 | 0 | 0 |
| `unsafe_stub` | 3 | 0 | 1 / 2 | 2 | 2 | 0 |

候选计划保留父结果文件、行号、任务和证据，八项验收状态全部为 pending，`admitted=false`。这些是合成工具环境的执行验证，不是实际 LLM/产品成绩。

原有 `harness/smoke/run_smoke.ps1` 也已改为每次创建新目录，实际 smoke 通过；重复指定非空目录会拒绝执行，已有结果的修改时间保持不变。

## 外部资源接入

设置 `AGENT_EVAL_DATA_ROOT`、`AGENTDOJO_PYTHON`、`AGENTDYN_PYTHON` 后，产品层完整 suite 发现 192 项、跳过 0 项：190 项通过，2 项错误。AgentDyn 原生 runtime 一次超过 30 秒上限；AgentPoison 仍查找迁移前固定解释器。

AgentPoison 已改用公共解释器配置，并设置 `AGENTPOISON_PYTHON`。上述两项单独复查，2 项均通过；AgentDyn 保留原有 30 秒超时，没有跳过或放宽断言。完整 suite 未在这次修正后再次运行，因此该证据记为“190 项完整运行通过 + 2 项复查通过”，仍保留启动时延偶发风险。

通过 `build_case_index.py --data-root ... --output-dir runs/rebuilt-index` 重新构建固定索引，5,384 条 JSON 记录与仓库原索引逐行一致：

| benchmark | 条数 |
|---|---:|
| InjecAgent | 1,054 |
| MSB | 2,000 |
| AgentDojo | 629 |
| AgentLeak | 496 |
| SABER | 715 |
| AgentPoison StrategyQA | 490 |

四条 track 均生成了执行计划，检查 argv、cwd、输入路径及 `executed=false`。LLM 计划只检查配置文件路径；canonical 数据和 API 运行条件尚未满足。SABER `saber:A_code_007` 的 Codex 请求实际 dry-run 返回 `status=dry_run`，未调用产品执行或 scorer，也未生成正式 case_result。

370 个迁移源文件均存在且未被 `.gitignore` 意外排除。原始数据、第三方 runtime、虚拟环境与历史 runs 不进入此次提交。验证日志、smoke manifests 和重建索引保存在本地被忽略的 `runs/`。

## 当前证据边界

本轮验证支持代码迁移、统一入口、指标边界与失败分析交接。尚未执行真实付费 LLM/产品批次、生成新攻击任务、运行八项验收、发布新测试集、自动修复或训练 Agent/模型。固定索引、source integration 和 mock 检查不证明官方 benchmark 的完整资格，也不证明闭环提高了安全性。
