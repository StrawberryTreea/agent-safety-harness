# 通知节流与派工节奏

> **配套文档**：[GitHub 协作规范](github_collaboration.md)（日常怎么提 PR）· [分工与推进顺序](work_assignment.md)（每人做什么）· [决策权与缺席机制](governance_and_continuity.md)（谁拍板、谁接手）· [名册数据](roster.json)
>
> **这份文档回答两个问题**：
> 1. **为什么 GitHub 一直在给我们发邮件？** 哪些操作会真的触发通知，怎么让它停下来。
> 2. **任务什么时候派？** 仓库什么状态才算"可以开工了"，派工怎么一次性发出去。

---

## 0. 一句话结论

**仓库里的每一个 @ 提及、每一次指派、每一次评审请求，都是一封真邮件。**
所以规则只有两条：

1. **不在模板和脚本里写 @username**——模板会被每个人每次复用，写死就是每次都打扰。
2. **任务等仓库就绪后一次性派发**，不零散指派。

另外一条已经生效的机制变化（2026-11-04）：**仓库取消了"必须有人 approve 才能合并"的门槛。**
现在 CI 全绿就能合上，谁都能独立提交自己的 PR。
CODEOWNERS 保留，但作用从"谁能批"变成"**谁该知道**"——提醒照发，但不阻塞。

---

## 1. 哪些操作真的会发邮件

下面这些**会**给当事人发邮件（GitHub 通知，不是网页站内信）：

| 操作 | 谁收到 | 什么时候发生 |
|---|---|---|
| 在 issue / PR 正文里写 `@某人` | 被 @ 的人 | 每次提交或编辑正文 |
| 把 issue 指派给某人 | 被指派的人 | 每次指派 |
| 请求某人评审 PR | 被请求的人 | 每次请求，且**没处理会持续催** |
| 在 PR 里 @ 某人 | 被 @ 的人 | 每次提交或编辑正文 |
| CODEOWNERS 自动请求评审 | 该路径的 owner | **每个 PR 自动触发** |
| 往 issue 里加评论 | 该 issue 的订阅者 | 每次评论 |
| 巡检脚本改派 issue + 留评论 | 被改派的人 + 订阅者 | 每次 `--apply` |

**不会**发邮件的（放心用）：

- 仓库内的文档正文里写 `@某人`（比如本文件、`docs/github_collaboration.md`）
- `.github/CODEOWNERS` 里的 `@某人`——这是 GitHub 的机制开关，不是通知
- CI 日志里的 `::notice` / `::error` 文本

**关键区别**：文档是"写下来给人看"，issue/PR 正文是"主动叫某人来看"。

---

## 2. 已经改掉的三处通知源

### 2.1 PR 模板里的 4 处 @ 提及

`.github/PULL_REQUEST_TEMPLATE.md` 的"影响面"清单原来写着"必须由 @StrawberryTreea 评审"。
**这份模板每开一个 PR 都要渲染一次**，等于每次开 PR 都给这几个人发一封"你被提到了"的邮件，
而实际上大多数 PR 根本不涉及契约。

现在改成了纯文本（"必须知会王浩（StrawberryTreea）"），并加了一段说明：
**真要通知谁，在你自己写的 PR 描述里手写 @某人**——那时候才是真的需要打扰他。

### 2.2 Issue 模板里的 3 处 @ 提及

`.github/ISSUE_TEMPLATE/task.yml` 的"负责人"和"依赖与衔接"输入框，
原来 placeholder 里带 `@321ddi` 这类账号名。用户提交 issue 时，占位符会连同 @ 一起进正文 → 触发通知。

现在改成"一迪（321ddi）"这样的纯文本写法，信息不丢，不再打扰。

### 2.3 巡检脚本看板里的 5 处 @ 提及

`scripts/oncall_roster.py` 生成的「在岗与代理看板」issue，正文里每个成员的账号都带 @。
看板是**每次巡检都会重写一次**的 issue，等于每次巡检都给 5 个人发一遍通知。

现在看板用纯文本账号，改派留言也不再 @ 被移交的人
（改派本身已经通过 assignee 变更通知了对方，再 @ 一次是重复打扰）。

---

## 3. 剩下一个通知源：CODEOWNERS 自动评审请求

这是**唯一一个我们不主动触发、但删不掉**的通知源。

**机制**：`.github/CODEOWNERS` 里的 `@某人` 会让 GitHub 在每个碰到该目录的 PR 上，
自动把该路径的 owner 加进评审人列表。
**注意：这是 CODEOWNERS 文件自身的行为，与分支保护里"要不要人 approve"无关。**
GitHub 官方文档原话："Code owners are automatically requested for review when someone opens a pull
request that modifies code that they own."；而"必须由 code owner 批准才能合并"是分支保护**额外**加的一层，
2026-11-04 已把这层去掉。

**已经实证的边界**（2026-11-04）：

| 手段 | 结果 |
|---|---|
| `DELETE /pulls/{n}/requested_reviewers` | ❌ 返回 200 但请求还在，时间线里**没有** `review_request_removed` 事件 |
| 把 PR 转成 draft | ✅ 生效。draft PR 不再自动请求 code owner，评审请求也不再催促 |
| 给 PR 加 reviewer bypass 名单 | ❌ 本仓库该端点返回 404（不支持） |
| 用 Ruleset 替代分支保护 | ❌ `required_pull_request` 类型被 API 拒绝（422） |

**所以本仓库的规矩是**：

> **PR 先以 draft 状态开，等真正要评审了再转 ready for review。**

draft 期间 CI 照跑（CI 看的是提交，不是 draft 状态），但**不产生评审请求、不发催促邮件**。
转 ready for review 的那一刻，才会发出评审请求——那时才真的需要有人看。

代价是 draft PR 不能直接合并（GitHub 对 draft PR 的 merge 返回 405）。这是有意的：
转 ready 就是"我要人评审了"这个动作，应该由作者在内容确实准备好时做。

### 3.1 取消 approve 门槛后，提醒该怎么配

取消"必须有人 approve 才能合并"之后，CODEOWNERS 的作用只剩一个：**通知相关的人**。
这时候每条路径配太多人反而是坏事——每多一个人就是多一封邮件。

所以现在的配法是 **每条路径 2 人：主 owner + 代理人**：

| 谁 | 为什么在名单上 |
|---|---|
| 主 owner | 这块的实际负责人，改动第一时间该让他知道 |
| 代理人 | 主 owner 有事或长期不上线时的接手人（见 `docs/roster.json` 的 `backup` 字段） |

**为什么不能只配 1 个**：GitHub 不会把 PR 作者列为自动评审人。
只配 1 人时，如果那人正好是这次改动的作者，就**完全没人被提醒**。配 2 个则保证另一个人一定在名单上。

**提醒到了就行，回不回是他的事**——因为合并不再需要任何人点头。
要让某人一定看到，在 PR 描述里手写 `@某人`。

---

## 4. 派工：等就绪了再一次性派

### 4.1 为什么不能零散派

5 个人 5 个任务，如果一个一个指派，就是 5 个不同时刻、5 封邮件。
收件人的体感是"这个仓库一直在给我派活"，而不是"团队开始干活了"。

一次性派工的差别：**5 封邮件在同一个时刻到达**，看起来就是一次有组织的开工。

### 4.2 "就绪"的判据

派工前仓库必须同时满足下面 6 条。**每一条都是能用程序检查的事实，不是主观判断**：

| # | 判据 | 怎么检查 | 不满足意味着 |
|---|---|---|---|
| 1 | **契约文件** | `schema.py` / `registry.py` / `driver/registrations.py` / `default_taxonomies.yaml` 存在且已入库 | 任务要求改的接口不存在，派了也是空转 |
| 2 | **流水线可运行** | `pipeline.py --help` 能跑通 | 任务的第一条验收命令就执行不了 |
| 3 | **整仓验收** | `python scripts/check_repo.py` 全绿 | 交出去的底座本身是坏的 |
| 4 | **文档地图一致** | `python scripts/check_doc_map.py` 通过 | 新人按文档地图找不到文档，等于没准备 |
| 5 | **名册与 owner 覆盖** | 每条 CODEOWNERS 路径 ≥ 2 个知情人，账号都在名册里 | 改了目录没人被提醒，改动悄悄溜过去 |
| 6 | **任务内容完整** | 每个任务都有 owner 标签、有完成标准或验收命令 | 收件人不知道做到什么程度算完成 |

### 4.3 怎么用

```bash
# 第一步：只检查，不改任何东西。全部通过才打印"可以统一派工"
python scripts/dispatch_tasks.py

# 想看机器可读的版本
python scripts/dispatch_tasks.py --json runs/dispatch/readiness.json

# 第二步：确认就绪后，一次性派工
python scripts/dispatch_tasks.py --dispatch

# 想先看看会派成什么样，不真的写
python scripts/dispatch_tasks.py --dispatch --dry-run
```

**`--dispatch` 在就绪判据没全过时会直接拒绝**，不做任何写入。
这是刻意的：派工是不可逆的通知动作，不该在一个"还没准备好"的仓库上发生。

脚本只改 assignee，**不改 issue 正文、不发评论、不加 @**——
所以每个人只收到一封"你被指派了 #N"的邮件，仅此而已。

### 4.4 任务是怎么找到人的

按 `owner:` 标签认领：`owner:liuke` → 名册里的刘轲 → GitHub 账号 `deep-fry-xiaopengyou`。
名册查不到的 owner 会被跳过并报出来，不会指派给错的人。

---

## 5. 现在的状态

| 项 | 状态 |
|---|---|
| 5 个 P0 任务（#3–#7） | 已写好完整交付物与验收标准，**已统一派发**（2026-11-04 一次性指派，任务横幅已翻为「🚀 状态：已派发」） |
| 派工时机 | ✅ 已到：`python scripts/dispatch_tasks.py` 6 条判据全过，已执行 `--dispatch` |
| 派工入口 | `python scripts/dispatch_tasks.py --dispatch`（已执行一次；重复执行是幂等的，只补漏） |
| 通知密度 | 每人**只收到 1 封**「你被指派了 #N」的邮件——脚本只改 assignee，不发评论、不加 @ |
| PR 通知 | 所有 PR 保持 draft 直到要评审为止 |
| 待审 PR | 无（#16 已于 2026-11-04 squash 合并入 `main`；#12、#14 内容已被它完全包含，已关闭） |

**任务内容一个字没删**——派发只做了一件事：把 issue 的 assignee 指向对应的负责人，
交付物、验收命令、红线、依赖关系全部原样保留在 issue 里。

### 派工为什么必须排在 #16 合并之后

#3–#7 的正文里链向 `docs/project_overview.md`、`docs/pipeline_guide.md`、
`docs/notification_and_dispatch.md`——这些文档随 #16 才进 `main`。
在 #16 合并前派工，收到任务的人点进去全是 404。

**已按此顺序执行**：#16 先合并 → 6 条就绪判据在合并后的 `main` 上重跑全绿 → 再执行 `--dispatch`。
每人收到的邮件里，链接点进去就是活文档。

### 顺带修掉的两个真问题（已随 #16 提交）

- **闸门用错解释器**：原用 `sys.executable` 跑验收套件，而套件依赖 `jsonschema` 等装在项目 `.venv` 里的包。
  宿主机 Python 缺依赖时报 `ModuleNotFoundError`，闸门据此判"未就绪"——这是环境问题，却会挡住派工。
  现改为优先使用 `.venv`（兼容 POSIX 布局），无 `.venv` 才回退当前解释器。
- **stub 服务器在 Windows 上偶发断连**：`tests/test_oncall_roster.py` 的 stub 在 PATCH 时不读请求体就响应并关连接，
  Windows 因接收缓冲区残留未读数据触发 RST，客户端收到 `ConnectionAbortedError (WinError 10053)`。
  该测试**单独跑通过、全量跑偶发失败**。现改为先按 `Content-Length` 读掉请求体，与另两个 stub 保持一致。

---

## 6. 相关文档

- [GitHub 协作规范](github_collaboration.md)：分支模型、PR 流程、CODEOWNERS 表、泄密红线
- [分工与推进顺序](work_assignment.md)：每人 P0–P2 的具体交付物
- [决策权与缺席机制](governance_and_continuity.md)：王浩的最终决定权、代理人接手、长期不上线处置
- [名册数据](roster.json)：成员、账号、目录、代理人、阈值（机器可读）
- [上手清单](day0_onboarding.md)：环境准备与第一批命令
