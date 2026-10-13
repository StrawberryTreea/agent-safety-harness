# 整合与清理验证记录

最新协作者验收见本文件末尾。下面的首轮迁移记录对应 bff2ef 提交之前/当时的工作；其计数和资产存在性不是清理后的当前状态。

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

### GitHub CI 兼容修正

首次 GitHub Actions（Windows、Python 3.14.7）发现两处本机环境未暴露的问题：命令构建测试依赖本机已安装 Codex；英文 Windows 的默认编码可能把 GB18030 中文 JSON 静默解码成乱码。

命令构建测试已模拟 native Codex 路径，仍检查非交互参数、MCP 配置及内置工具禁用约束。SABER 评分读取优先尝试 `utf-8-sig` 与 `gb18030`，最后才使用系统编码；同一回归测试固定英文 `cp1252` 环境，分别检查 UTF-8、UTF-8 BOM 和 GB18030，修正前实际复现 BOM 读取错误和中文乱码，修正后全部通过。再次运行产品层完整自包含 suite：192 项、138 项通过、54 项跳过、0 失败/错误。远端修正后的结果以 PR checks 为准。

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

首轮迁移检查时 370 个迁移源文件均存在且未被 `.gitignore` 意外排除；清理后删除项见 cleanup.md 和 migration manifest 的 dispositions。原始数据、第三方 runtime、虚拟环境与历史 runs 不进入此次提交。验证日志、smoke manifests 和重建索引保存在本地被忽略的 `runs/`。

## 当前证据边界

本轮验证支持代码迁移、统一入口、指标边界与失败分析交接。尚未执行真实付费 LLM/产品批次、生成新攻击任务、运行八项验收、发布新测试集、自动修复或训练 Agent/模型。固定索引、source integration 和 mock 检查不证明官方 benchmark 的完整资格，也不证明闭环提高了安全性。


## 协作者清理后的最新验收

日期：2026-10-13。Windows PowerShell、Python 3.14.4；安装入口是 requirements-dev.txt → requirements.txt。当前实现与删除记录见 [cleanup.md](cleanup.md)。

实际执行 `python scripts/check_repo.py --out runs/cleanup-check`，再从 Git 暂存源码 tree `3873a07c54847a96559b99f853b706c1f2f20e0a` 导出无 .venv/runs/缓存的新目录，创建全新虚拟环境、安装依赖、运行同一验收入口。两次结果一致：

| 范围 | 发现 | 通过 | 跳过 | 失败/错误 |
|---|---:|---:|---:|---:|
| 根测试（含 reserved phase 与 evaluator 退出码回归） | 36 | 36 | 0 | 0 |
| Harness | 170 | 160 | 10 | 0 |
| 产品模块 | 192 | 138 | 54 | 0 |
| 合计 | 398 | 334 | 64 | 0 |

- 文档：24 份 Markdown、100 个本地目标链接，0 个悬空链接。
- safe/unsafe 实际调用 capture → 私有 score → report → analyze，分别 3/3、1/3 通过，候选计划分别 0、2，接纳均为 0。
- LLM 三条多选题参考答案自测：3 条 success/pass，分析中 0 unknown、0 候选、0 接纳。没有调用模型 API。
- 新环境安装了 PyYAML 6.0.3、jsonschema 4.26.0、psutil 7.2.2 及其依赖。
- 全新源码不含旧工作区数据或运行输出；验证 tree 之后的改动仅为文档记录，不修改已测源码。

本地日志保存在被忽略的 `runs/cleanup-check/`，新安装记录在 `.tmp/collaborator-clean-20261013/runs/fresh-install-check/`。summary.json 逐步骤记录 returncode、测试通过/跳过数及示例结果。CI 同样运行 scripts/check_repo.py，并通过 acceptance-logs artifact 保存证据；远端结论以当前 PR 的最新 checks 为准。

64 项 skip 是外部数据/环境缺失，不代表正式产品/benchmark 资格。真实 API 批次、任务生成、验收执行和新版测试集发布仍需后续实现/验证。

## README 流程图补齐与重绘

日期：2026-10-13。本次只修改文档，补齐此前仅链接到架构文档的首页图示。

- 中英文 README 各增加项目总流程与协作者运行顺序，共 4 张。
- Harness README 增加公开捕获、独立私有评分、报告与分析流程，离线 judge 单独绘制。
- 产品 README 增加 preflight、固定选择集、原生执行与 scorer、聚合与阶段资格检查流程。
- 架构文档重绘详细总图，区分现有能力、待实现测试集反馈链与 Agent/模型改进研究链。
- 用户原始 PNG 保留为设计参考；它没有被重绘或当作已实现状态图。

7 个 Mermaid 图块均使用 `@mermaid-js/mermaid-cli` 12.0.0 和本机 headless Edge 实际渲染，逐图返回 0，并检查中文/英文标签、连线与布局。渲染工具仅安装到被忽略的 `.tmp/`；预览与日志位于 `runs/readme-diagrams/`，不增加项目运行依赖。

实际运行 `python scripts/check_repo.py --out runs/readme-diagram-check`：398 项发现、334 项通过、64 项外部资产 skip、0 失败/错误；safe/unsafe 和 LLM 参考答案自测与上轮一致。文档检查覆盖 24 份 Markdown、104 个本地目标链接，0 悬空链接。随后只有图示布局与本验证段落调整，最终链接检查见 `runs/readme-diagram-docs-final/summary.json`。这些文档检查与合成示例不新增真实产品资格或自动演化实验结论。
