## 这个 PR 改了什么

<!-- 一两句话说清：改了哪个模块、为什么改。不超过 3 行。 -->

## 对应 Issue

<!-- 关闭哪个 issue：Closes #123。多人并行开发时不要用 Closes，用 Refs #123。 -->

## 可复现命令

<!--
评审人必须能一键复现你看到的输出。写清楚实际执行的命令，例如：

python scripts/check_repo.py

PYTHONPATH=harness/src python -m agent_safety_harness.driver

不要写"运行测试看看"这种描述。
-->

```bash
# 实际执行的命令
```

## 测试结果

<!-- 三选一，如实填。禁止写"应该没问题"。 -->

- [ ] 通过（通过数 / 失败数 / 跳过数：__ / __ / __）
- [ ] 有失败（说明：__）
- [ ] 本次无测试改动（理由：__）

跳过数超过 0 时，请说明跳过原因和还缺什么环境。

## 影响面

<!-- 勾选所有涉及的项。没勾选的项视为未改动。 -->

- [ ] 改了数据契约 / schema（`schema.py`、`protocol/`、`canonical_schema.yaml`）→ **必须由 @StrawberryTreea 评审**
- [ ] 改了统一函数层签名（`registry.py`、`driver/registrations.py` 的 6 个函数）→ **必须通知 @DylanWu6（driver 调用方）**
- [ ] 改了 `failures.jsonl` 字段 → **必须通知 @DylanWu6（evolve 消费方）**
- [ ] 改了 `eval_method` / `taxonomy_ref` 相关默认行为 → **必须由 @StrawberryTreea 确认**（这两项是配置变量，不是铁律）
- [ ] 改了产品 registry / benchmark registry 的版本 pin → **必须附 preflight 证据**
- [ ] 只改文档

## 接口 / 版本变化

<!--
有就写，没有就写"无"。改了协议、数据集、产品 profile、模型 profile 都要写。
版本号规则见 CONTRIBUTING.md（根 VERSION 为准）。
-->

## 泄密自查（公开仓库，必勾）

- [ ] 未提交任何 API key / token / 密码（只写 `api_key_env` 环境变量名）
- [ ] 未提交原始数据集、运行输出、虚拟环境（见 `.gitignore`）
- [ ] 未提交本机绝对路径、内网地址、他人个人信息
- [ ] 新增的外部数据来源已写明来源与 license
