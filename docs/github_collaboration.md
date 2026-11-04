# GitHub 协作规范（5 人团队）

> **仓库**：https://github.com/StrawberryTreea/agent-safety-harness （公开）
> **适用对象**：王浩、刘轲、辰浩、一迪、伦锋
> **配套文档**：[分工与排期](work_assignment.md) · [第 0 天上手](day0_onboarding.md) · [框架契约](FRAMEWORK_CONTRACT.md)

这份文档回答一个具体问题：**5 个人同时改一个仓库，怎么用 GitHub 才不会乱。**
每一条规则都对应仓库里已经配置好的机制，不是"应该这样做"的口号。

---

## 1. 账号与权限

| 成员 | GitHub 账号 | 仓库权限 | 能做什么 |
|---|---|---|---|
| 王浩 | `@StrawberryTreea` | **admin** | 全部，含分支保护规则、CODEOWNERS、协作者管理、里程碑 |
| 刘轲 | `@deep-fry-xiaopengyou` | write | 建分支、开 PR、评论、关自己的 issue |
| 辰浩 | `@ennnnn3232` | write | 同上 |
| 一迪 | `@321ddi` | write | 同上 |
| 伦锋 | `@DylanWu6` | write | 同上 |

**谁能合并到 `main`**：只有走 PR 且通过保护规则的人（见第 3 节）。
**谁都不能直接推 `main`** —— 保护规则对 admin 同样生效（`enforce_admins: true`），王浩自己也要走 PR。

**谁能改契约**：改 `schema.py` / `registry.py` / `protocol/` 的 PR，CODEOWNERS 会自动要求 `@StrawberryTreea` 批准。

**账号换了怎么办**：在仓库 Settings → Collaborators 里改；CODEOWNERS 里的账号名要同步改。

---

## 2. 分支模型

只有三种分支，没有第四种：

| 分支 | 谁建 | 生命周期 | 规则 |
|---|---|---|---|
| `main` | 不用建 | 长期 | 只接受 PR 合入，禁止直接 push |
| `feat/xxx` `fix/xxx` `docs/xxx` `test/xxx` `chore/xxx` | 各人 | ≤ 3 天 | 命名规则见下，CI 会校验 |
| `release/x.y` | 王浩 | 需要发版时 | 常规任务不要用 |

**分支命名**：`type/小写短横线名`，例如：

```
feat/registry-real-engine
feat/schema-modality
fix/windows-os-replace
docs/github-collab
```

CI（`pr-policy.yml`）会在 PR 时校验：不符合 `type/小写名` 格式直接失败。

**为什么要短命分支**：4 个人同时改 `main` 必然冲突。分支活得越短，冲突越少、评审越快。
建议节奏：今天开工建分支，今天或明天提 PR，合了就删（仓库已开启合并后自动删分支）。

---

## 3. PR 流程（唯一入口）

```
git checkout -b feat/你的功能名
# ... 改代码 ...
git add <明确的文件>          # 不要 git add -A（会把 .workbuddy/ 之类的临时文件带进去）
git commit -m "feat(scope): 一句话说明"
git push -u origin feat/你的功能名
gh pr create --fill --title "feat(scope): 一句话说明"
```

**PR 标题格式**（CI 强制）：
```
type(scope): 描述
```
`type` ∈ `feat / fix / docs / test / refactor / perf / chore / build / ci`

例：`feat(engine): 接通 bench_intake 到 source_intake`

**PR 合并必须同时满足 4 个条件**（仓库已配置，缺一不可合）：

1. ✅ 至少 **1 人 approve**，且 approve 的人必须是**该目录的 CODEOWNER**
2. ✅ CI 全绿（`Portable checks` 的 `tests` 任务 + `PR 规范门禁` 的三个任务）
3. ✅ PR 里的**所有讨论已被解决**（`required_conversation_resolution`）
4. ✅ **不能 force push**、不能删 `main`（`allow_force_pushes: false`）

额外两条：`dismiss_stale_reviews`（你又推了新 commit，之前的 approve 作废，需要重审）、
`require_last_push_approval`（**不能自己 approve 自己最后推的代码——王浩也一样**，所以每条路径配了 ≥2 个 CODEOWNER）。

**谁能 approve**：必须是**该目录的 CODEOWNER**，且**不是你最后推的那个人**。
所以你的 PR 该找谁批，看上面那张表里的第二位。

> **CODEOWNER 是谁，看这个表**（`.github/CODEOWNERS` 是唯一权威）：
>
> | 你改的目录 | 自动找谁审 |
> |---|---|
> | `harness/src/agent_safety_harness/`（除 driver）、`harness/config/`、`scripts/source_intake.py` | 刘轲 + 王浩 |
> | `federated_asr_benchmark/` | 辰浩 + 王浩 |
> | `llm_eval/`、`scripts/result_analysis.py` | 一迪 + 王浩（`result_analysis.py` 另有伦锋） |
> | `evolution/`、`orchestrator/`、`harness/src/agent_safety_harness/driver/` | 伦锋 + 刘轲 + 王浩 |
> | `docs/`、`protocol/`、`references/`、`scripts/` 其余、`tests/` | 王浩 + 一迪 + 伦锋 |
>
> **实测已验证**（2026-11-04）：改 `docs/` → 自动请求 `DylanWu6` + `321ddi`；
> 改 `federated_asr_benchmark/` → 自动请求 `ennnnn3232`。
> 注意：**PR 作者本人不会被自动列为评审人**（GitHub 行为），
> 所以你看到只有 1 个 owner 被请求时，先看自己是不是那个 owner。


> **为什么每条路径都有 2 个以上 owner**：仓库开了 `require_last_push_approval`（**作者不能 approve 自己最后推的代码**）。
> 如果某个目录只有 1 个 owner，就会死锁——比如刘轲改 `harness/`，而他自己就是唯一 CODEOWNER，没人能批他的 PR。
> 所以每条路径都配了「主 owner + 兜底评审人」，保证你的 PR 一定有人能批。
> 兜底人选是这么定的：改自己的目录时，找**该路径的另一位 owner**；不确定时找王浩。
>
> ✅ **CODEOWNERS 已进 `main`（PR #8，2026-11-04），自动指派已实测生效，无需任何手工操作。**
>
> 历史备注（已解决，留作记录）：PR #8 是**引入门禁本身的那一个 PR**，
> 它的 base 分支还没有 CODEOWNERS，所以 GitHub 不会自动指派评审人，
> 同时它又被「必须 CODEOWNER approve」卡住——这是 bootstrap 死锁。
> 当时的解法是临时把 `enforce_admins` 关掉、用管理员权限合并该 PR，**合并后立刻恢复了**。
> 从此以后所有 PR 都走正常流程，不需要任何人再手动干预。


> ⚠️ **`driver/registrations.py` 是双人交接点**：registry 函数由刘轲提供、driver 编排由伦锋提供。
> 这个文件归两人共同 owner——**刘轲或伦锋提的 PR，都需要对方或王浩批准**。
> 提 PR 时在描述里写清"registry 部分归刘轲 / 编排部分归伦锋"，并 @ 对方。

**PR 描述要填什么**：仓库已配置 PR 模板（`.github/PULL_REQUEST_TEMPLATE.md`），
每个勾选框都有具体要求，最关键的三项：
- **可复现命令**（评审人能一键跑出你看到的输出）
- **测试结果**（通过数/失败数/跳过数，如实填，禁止写"应该没问题"）
- **泄密自查**（公开仓库必勾）

---

## 4. Commit 规范

```
type(scope): 一句话说明

为什么改（可选，1-3 行）
```

- `type` 与 PR 标题同一套
- 一行不超过 72 字符
- **一个 commit 只做一件事**（不要把「改代码 + 改格式 + 改文档」混在一起）

**禁止**：`git commit -am`（会带上你没打算提交的文件）、在 message 里写密钥。

---

## 5. Issue 怎么用

**Issue = 任务和讨论的载体，不是垃圾桶。**

**标签体系**（建好了，直接用）：

| 前缀 | 含义 | 取值 |
|---|---|---|
| `P0` `P1` `P2` | 优先级 | P0 第1周铁轨必通 / P1 第2-3周 / P2 第4周+ |
| `owner:xxx` | 负责人 | `owner:liuke` `owner:chenhao` `owner:yidi` `owner:lunfeng` `owner:wanghao` |
| `track:xxx` | 所属轨/模块 | `track:llm-qa` `track:agent-execution` `track:agent-product` `track:trace-judge` `track:engine` `track:evolution` `track:infra` |
| `type:xxx` | 类型 | `type:feature` `type:bug` `type:docs` `type:chore` |
| `needs-contract-review` | 触碰契约 | 契约类 PR 打这个 |
| `blocked` | 被阻塞 | 暂时做不了就打上并写清被谁挡着 |

**里程碑**：任务按里程碑推进 —— `P0 第1周：铁轨贯通` / `P1 第2-3周：扩展与加固` / `P2 第4周+：优质模型贯通与自进化`。

**当前已派发的 P0 任务**：

| # | 负责人 | 任务 |
|---|---|---|
| [#3](https://github.com/StrawberryTreea/agent-safety-harness/issues/3) | 刘轲 | schema.py 扩 modality + registrations 4 个 stub 接真实引擎 |
| [#4](https://github.com/StrawberryTreea/agent-safety-harness/issues/4) | 辰浩 | 真实产品 CLI 跑通出可复现 ASR + registry 版本 pin 与 preflight 证据 |
| [#5](https://github.com/StrawberryTreea/agent-safety-harness/issues/5) | 一迪 | 接入外部 benchmark 出官方口径分数 + result_analysis 产 failures.jsonl |
| [#6](https://github.com/StrawberryTreea/agent-safety-harness/issues/6) | 伦锋 | driver 端到端跑通 + nl_bridge 白名单拦截 + evolve 接 evolution/ |
| [#7](https://github.com/StrawberryTreea/agent-safety-harness/issues/7) | 王浩 | 契约仲裁响应 + 白皮书口径把关 + 免费额度协调 |

**开 issue 的规则**：
- 领任务前先看有没有现成 issue，别重复开
- 发现 bug → 用「缺陷」模板，**必须写复现命令**
- 发现新任务 → 用「任务」模板，**必须写验收命令**
- 任务做完 → 在 issue 里贴 PR 链接，PR 描述里写 `Closes #N`

---

## 6. 泄密红线（公开仓库，比私有仓库更严）

仓库已转公开。以下内容**任何时候都不能提交**，CI 会自动扫描 PR 新增行：

| 红线 | 正例 | 反例 |
|---|---|---|
| API key / token | `api_key_env: "ZHIPU_API_KEY"` | `api_key: "sk-xxxx"` |
| 本机绝对路径 | `AGENT_EVAL_DATA_ROOT`（环境变量） | `C:\Users\<你的用户名>\Desktop\...` |
| 内网地址 | 不写 | `10.x.x.x` / `a100-p<host>` |
| 他人个人信息 | `@321ddi` | 真实邮箱 `<someone>@qq.com` |
| 原始数据集 | 登记来源 + hash | 整个 benchmark 数据文件 |
| 运行输出 | 结论 + 指标 | `runs/` 整个目录 |

**其他三条**：
- **密钥只走环境变量**，且**各自申请各自测试**，不进配置文件、不进仓库、不发群里
- **白皮书 PDF 与 668,820 条样本只在本地**，不入库。仓库里的 `protocol/default_taxonomies.yaml` 和 `docs/default_taxonomies.md` 是维度定义（公开可见），正文和样本不公开
- **推之前自查**：`git diff --cached` 看一遍要提交的内容

如果**万一**推了密钥：立刻到 GitHub 仓库 Settings → Secrets 撤销该 key，再改历史。**先撤销 key，比先改历史重要。**

---

## 7. 日常节奏

| 频率 | 做什么 | 谁 |
|---|---|---|
| **每天开工** | 拉 `main`（`git pull --rebase`），看自己名下的 issue 是否有新评论 | 各人 |
| **每天收工前** | 提交当天改动 + 开 PR（不要攒超过一天） | 各人 |
| **每天一次** | 回应所有 PR 评论、resolve 讨论 | 各人 |
| **每周一次** | 看 `P0` 里程碑进度，更新看板 | 王浩 |
| **随时** | 契约有分歧 → 开 Discussion 并 @王浩 | 任何人 |

**冲突处理**：
- `git pull --rebase origin main` 能解决就用它，别一上来就 merge
- rebase 解决不了 → `git merge origin/main`，手动解冲突，**解完再跑一遍验收命令**
- 冲突文件是别人 owner 的目录 → **在 PR 里 @ 对方**，别自己硬解

---

## 8. 命令速查

```bash
# 第一次拉仓库
git clone https://github.com/StrawberryTreea/agent-safety-harness.git
cd agent-safety-harness
python -m venv .venv && .\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe scripts/check_repo.py

# 每天开工
git checkout main && git pull --rebase origin main
git checkout -b feat/你的功能名

# 提交（明确指定文件，不要 git add -A）
git add harness/src/agent_safety_harness/evaluation/schema.py
git commit -m "feat(engine): 扩展 schema modality 支持 LLM 模态"
git push -u origin feat/你的功能名

# 开 PR
gh pr create --fill
gh pr view                 # 看自己 PR 状态
gh pr checkout <编号>      # 拉别人的 PR 到本地看

# 评审别人的 PR
gh pr list --label "P0"
gh pr review <编号> --approve --body "看过了，schema 改动 OK"
gh pr review <编号> --request-changes --body "..."

# Issue
gh issue list --label "owner:liuke" --state open
gh issue comment <编号> --body "..."
gh issue close <编号>

# 本地验收（提交前必跑）
.\.venv\Scripts\python.exe scripts/check_repo.py
```

**没装 `gh` 也能用**：所有操作都有网页界面，地址规律是
`https://github.com/StrawberryTreea/agent-safety-harness/<路径>`。
PR 列表在 `/pulls`，Issue 列表在 `/issues`，设置在 `/settings`。

---

## 8.5 新人第一周照做清单（照抄即可）

**Day 1（今天，一次性）**

```bash
git clone https://github.com/StrawberryTreea/agent-safety-harness.git
cd agent-safety-harness
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe scripts/check_repo.py      # 期望：全绿
```

然后打开 `https://github.com/StrawberryTreea/agent-safety-harness/issues`，
找到标题带 **`[P0][你的名字]`** 的那个 issue（已指派给你），点开看交付物清单。

**Day 1 之后，每个工作日循环**

| 步骤 | 命令 / 动作 | 完成标志 |
|---|---|---|
| 1. 同步 | `git checkout main && git pull --rebase origin main` | 无输出 |
| 2. 看任务 | `gh issue list --label "owner:你的标签" --state open` | 列出你的 issue |
| 3. 开分支 | `git checkout -b feat/简短功能名` | 切到新分支 |
| 4. 干活 | 改代码 → `.\.venv\Scripts\python.exe scripts/check_repo.py` | 全绿 |
| 5. 提交 | `git add <具体文件>` → `git commit -m "feat(模块): 简短说明"` | 有 commit |
| 6. 推送 | `git push -u origin feat/简短功能名` | 远端有分支 |
| 7. 开 PR | `gh pr create --fill`（或网页 /compare） | PR 页面出现 |
| 8. 等评审 | 看 PR 页面的 "Reviewers" 是否已自动填上 | 有人被 @ 到 |
| 9. 回应评论 | `gh pr view <编号> --comments` | 评论已解决 |

**红线（3 条）**
1. **永远不要直接 `git push origin main`** —— 会被拒绝，这是设计。
2. **永远不要用 `git add -A`** —— 会把别人的目录、你本地的 `runs/`、`.workbuddy/` 一起带进去。逐个文件 `git add`。
3. **API key 只写环境变量名**（`api_key_env`），真 key 绝不进仓库、绝不发群。

---

## 9. 出问题找谁

| 情况 | 找谁 | 走哪条路 |
|---|---|---|
| 契约/schema/接口分歧 | 王浩 | Discussion 或 PR 里 @StrawberryTreea |
| `registry` 6 个函数签名 | 刘轲 | Issue 或 PR 里 @deep-fry-xiaopengyou |
| `failures.jsonl` 字段 | 一迪 | @321ddi |
| `driver` 编排 / `nl_bridge` | 伦锋 | @DylanWu6 |
| 产品 CLI / preflight / ASR | 辰浩 | @ennnnn3232 |
| GitHub 本身的问题（权限、CI、合并卡住） | 王浩 | Issue，标 `type:chore` |

---

## 10. 已配置的机制清单（不用你操心，已经生效）

| 机制 | 状态 | 位置 |
|---|---|---|
| 仓库公开 | ✅ | — |
| `main` 分支保护 | ✅ | Settings → Branches |
| 禁止直推 / force push / 删分支 | ✅ | 同上 |
| 必须 PR + CODEOWNER approve | ✅ | 同上 + `.github/CODEOWNERS` |
| CI 必过（`tests` + `pr-policy` 三任务） | ✅ | `.github/workflows/` |
| Squash / Rebase 合并，禁止 merge commit | ✅ | Settings → General |
| 合并后自动删分支 | ✅ | 同上 |
| 仓库公开协作 + Issues + Discussions | ✅ | — |
| PR 模板（含泄密自查） | ✅ | `.github/PULL_REQUEST_TEMPLATE.md` |
| Issue 模板（任务 / 缺陷） | ✅ | `.github/ISSUE_TEMPLATE/` |
| 标签体系（23 个） | ✅ | Issues → Labels |
| 里程碑（3 个） | ✅ | Issues → Milestones |
| CODEOWNERS 目录归属 | ✅ | `.github/CODEOWNERS` |
| PR 标题 / 分支名格式校验 | ✅ | `.github/workflows/pr-policy.yml` |
| PR 新增行密钥 / 路径 / 邮箱扫描 | ✅ | 同上 |
| 全部 5 项检查设为合并必需 | ✅ | Settings → Branches |
