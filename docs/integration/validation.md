# V1 验收记录

## 2026-10-16：Python 3.11、回路 B 受控候选与真实运行时适配

在 Windows PowerShell、现有 `llm-safety-eval` Conda Python 3.11.15 环境中安装 `requirements-dev.txt` 后运行：

```powershell
conda run -n llm-safety-eval python scripts/check_repo.py
```

统一验收结果为 418 项测试：354 通过、64 因明确缺少外部数据或专用 runtime 跳过、0 失败/错误。根目录 59/59 通过，新增覆盖非执行式模型凭据检查、CLI 可执行文件/提示/MCP 接线检查，以及阻断态退出码和“不启动目标”的保证。Harness 为 158 通过/10 跳过，新增验证 `{python_executable}`、`{harness_ash}` 与 MCP 任务/事件路径占位符的真实展开；产品模块为 137 通过/54 跳过。

同时通过默认协议绑定、26 份 Markdown/142 个本地链接、safe/unsafe Agent smoke、三题 LLM 自测与分析。统一验收记录位于本地忽略目录 `runs/check-20261016T081515-e90b979d/summary.json`；非执行式预检 smoke 位于 `runs/target-preflight-smoke-20261016T0830/`，结果为 `ready`。

产品 runner 的只读状态目录清理同时兼容 Python 3.11 的 `shutil.rmtree(onerror=...)` 和 Python 3.12+ 的 `onexc=...`。回路 B 已能按白名单 recipe 构建 JSON/YAML 提示/策略候选，把已登记资产物化为带哈希的只读模型/CLI 运行配置，并执行内部 Agent 的冻结安全/良性配对作业。模型提示进入 system message；CLI 提示进入标准输入/提示文件且不进入命令。新增 `target-preflight` 会在执行前物化同一套只读运行配置，检查凭据环境变量名、CLI/MCP 可执行文件身份、提示传输和 MCP 任务/事件路径，同时不调用模型 API、不启动目标 CLI，也不授予后续外部执行权限；实际端点可达性、CLI 版本输出与 MCP 握手仍须另行授权执行后确认。验收没有调用真实模型 API；CLI 端到端项使用本地测试进程和合成规则。仍没有模型生成式目标优化、其他轨道调度、部署或回滚资格。

日期：2026-10-13。整合项目版本为 V1（1.0.0）。旧版本验收计数不作为当前结果。

## 统一验收

在 Windows PowerShell、Python 3.14.4 下，最新全新 Git 检出运行：

```powershell
.\.venv\Scripts\python.exe scripts/check_repo.py --out runs/fresh-install-check
```

| 测试范围 | 发现 | 通过 | 跳过 | 失败/错误 |
|---|---:|---:|---:|---:|
| 根目录 | 36 | 36 | 0 | 0 |
| Harness | 165 | 155 | 10 | 0 |
| 产品模块 | 191 | 137 | 54 | 0 |
| 合计 | 392 | 328 | 64 | 0 |

64 项跳过需要仓库未附带的外部来源数据或专用 runtime；日志保留具体测试及缺失资产。跳过不算通过，也不授予真实产品或官方 benchmark 资格。

- 默认协议的三条输入数量、输入文件与评测器 SHA-256 校验通过。
- 24 份 Markdown、101 个本地文件链接，悬空目标为 0。
- `safe_stub`：3/3 安全通过，候选计划 0。
- `unsafe_stub`：1/3 安全通过，未接纳候选计划 2。
- LLM 参考答案自测：3/3 规则通过，不调用模型 API。
- 接纳候选数为 0；生成、验收、发布和自动改进仍需实现。

## 全新检出与安装

为验证协作者拿到 Git 文件后可以运行，用 `git checkout-index --all` 从暂存 Git blobs 检出独立目录，让实际 Git 换行属性生效；在该目录新建虚拟环境，安装 `requirements-dev.txt`，执行同一个统一验收入口。创建环境、安装依赖、完整验收均返回 0，结果为 328 通过、64 跳过、0 失败/错误。

- 检出快照 tree：`5fa7dce1dcb6612e1b321acabe2e4407c81865ca`，包含 341 个受版本控制的文件。
- 检出目录：`.tmp/collaborator-v1-20261013T103445Z/`。
- 记录：`runs/v1-fresh-check.json`。
- 日志：`runs/v1-fresh-venv.log`、`runs/v1-fresh-install.log`、`runs/v1-fresh-acceptance.log`。
- 三套测试及示例的逐步记录：检出目录内的 `runs/fresh-install-check/summary.json`。

冻结输入 SHA-256 为 `6316390b628383d111ae45d3c384d7ad682db25eecf97e849dcd547b2680a5bb`；评测器为 `59e183a034325fa94d9ef284405073d9fd3c75d2d94822bbeb32304d077d7717`。两者在 Git blob 和新检出文件中相同。

`.gitattributes` 固定 JSONL 与协议绑定评测器使用 LF。产品选择集测试同时验证 `case_index.jsonl` 的原始字节哈希与 manifest 相同，确保 Windows 的 `core.autocrlf` 不改变冻结来源索引。

以上快照已包含首页分支说明、旧输入绑定清理和跨盘符修复；随后仅更新本验收记录的时点、计数与修复说明。最终提交的 CI 使用同一入口再验收。日志、虚拟环境和预览均被 Git 忽略，协作者应生成自己的记录。

## 首页图与文档

中英文首页表达同一套三张图：

| 图 | 内容 |
|---|---|
| 1 | 评测、失败分析、候选规划与后续改进闭环；虚线标明待实现步骤 |
| 2 | 模块架构、原生入口、结果流向和私有评分边界 |
| 3 | 协作者从安装、验收到真实运行和候选审查的流程 |

首页共 3 张逻辑图，中英文各 3 个 Mermaid 块。Harness、产品模块和架构文档各有一张补充 Mermaid 图，单独统计。共 9 个块通过 Mermaid CLI 渲染；首页六个语言版本已逐张检查文字、连线与布局。预览位于 `runs/readme-diagrams/`，不构成运行依赖。

旧首页三张 PNG 已删除；用户原始概念图保留在架构文档。旧版本名称、历史选择集和一次性审计的处理见 [cleanup.md](cleanup.md)。

## 配置与工具检查

- 30 个产品请求模板通过 schema 校验；引用选择集均存在且非空，记录为 `runs/v1-request-audit.json`。
- 六个现有来源的 V1 `smoke20` 与 `pilot100` 每来源分别 20/100 条，总计 120/600 条，互不重叠；完整性、可复现抽样与来源哈希由选择集测试检查。
- `release.ps1` 语法解析通过，`Verify` 在缺少 manifest 时正确拒绝执行；路径已改为当前选择集和 `runs/release-dev/`。
- 来源/执行构建器不再绑定旧发布数量；按当前 manifest 与显式最小数量验证。来源 parity 与合成 replay 成功不等于 `formal_ready`。
- 分发打包测试按当前输入核对完整集、抽样子集、执行/离线类别和 manifest，不再要求旧实验固定行数；输入可通过 `AGENT_EVAL_CURATED_TASKS_ROOT` 指定。
- 另外提供 60 条合成 TaskSpec（执行 50、离线 judge 10）运行打包回归，1 项通过；日志为 `runs/v1-current-packaging-test.log`。这是额外工具检查，不改变统一验收的通过/跳过计数。

首轮 GitHub CI 暴露仓库在 D 盘、临时目录在 C 盘时的相对路径异常；选择集生成器已改为同盘保存相对来源路径、跨盘保存绝对来源定位，并新增回归检查。来源原始哈希和抽样结果保持可验证。最终提交的 CI 结果见 PR 当前 HEAD checks。

## 验收范围

本轮证据确认本地安装、协议检查、来源选择、入口、轨迹、评分和分析链路可以运行。真实模型评测需另冻输入、模型与 scorer；真实产品运行需外部资产、专用环境、版本与 preflight 证据。候选任务必须完成独立验收后才能接纳。自动生成、测试集发布和 Agent/模型自动演化尚未完成。
