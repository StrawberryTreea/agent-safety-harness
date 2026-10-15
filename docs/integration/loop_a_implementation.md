# 回路A本地基础实现

日期：2026年10月15日。项目保持V1 1.0.0；LLM合成协议另升为v1_fixture_identity_choice_v2，评分器为rule_eval_conservative_choice_v2。

本次新增的是可测试的基础模块和本地原生执行衔接。真实目标、生成器、主指标、预算及隔离资格尚未配置，正式实验入口会拒绝启动。未调用付费模型，未完成真实测试集演化实验。

## 可运行入口

从仓库根执行：

```powershell
python pipeline.py loop-a plan
python pipeline.py loop-a qualify
python pipeline.py loop-a smoke --out runs/loop-a-first
python pipeline.py loop-a smoke --out runs/loop-a-first --resume
python pipeline.py loop-a cycle --out runs/loop-a-cycle --strategy feedback --seed 0
python pipeline.py loop-a compare --out runs/loop-a-compare --seed 0
python pipeline.py loop-a compare --out runs/loop-a-compare --seed 0 --resume
```

plan与qualify不调用模型或产品。smoke只运行已有unsafe_stub的原生capture/score，保存失败证据、允许反馈、模板候选、门槛决定、预算账本和fixture数据版本。缺少良性对照、独立审核等证据的候选保留待审；smoke不补造证据以制造接纳。全门槛通过的fixture发布路径另有单元测试。

cycle执行一条完整本地验证链：固定协议后先执行攻击基线与良性基线，再生成一个表述候选，进行三次原生重放，并把同一模板应用到良性任务执行对照。每臂共6次目标执行，另记录0次provider调用的本地生成job；未消费额度不会挪给另一策略。compare分别运行random、feedback、wording三臂，共18次本地执行，三臂目标、候选池、执行与费用上限相同。候选在缺少来源审核、独立审核和语义单因素确认时仍待审；基线已经出现的失败键不会算作新增。

该样例将未授权转账与本人授权转账配成mock对照，完成规则只确认mock工具调用，不证明真实账户授权。投递证据只检查实际工具参数中的固定标记，不能替代真实Agent读取攻击材料的证据。stub不根据措辞改变策略，三臂相同结果仅验证框架，不支持策略优越性结论。

```powershell
python pipeline.py loop-a metrics --results path/to/verified-observations.jsonl
python pipeline.py loop-a run
```

metrics输出两种候选口径和组成项，默认primary为null、protocol_frozen=false，不产生正式统计推断。run当前返回非零并列出阻断原因；手工把JSON改为frozen也不能绕过尚未注册的真实执行及隔离资格。

## 已实现能力与限制

| 模块 | 本地能力 | 尚待完成 |
|---|---|---|
| orchestrator/registry.py | 内容哈希、RunIdentity、拒绝旧身份或无身份结果续跑 | 原生内部Agent和产品入口全面接入；镜像和完整环境身份 |
| LLM评测器 | run_one_model绑定身份、有效提示哈希、保守选择题抽取 | 人工输出校准集；数字与标签完整三态抽取 |
| product_eval/selection.py | 单条与batch共用请求相对索引、重复与边界检查 | 实机产品与来源全集资格 |
| orchestrator/budget.py | 事务预留、双上限、幂等结算、超支留账、故障不退款 | 真实provider费用上界与计费采集 |
| evolution/transforms.py | JSON差异、前后哈希、字段白名单、单因素声明核验 | 文本语义单因素判断，默认转人工 |
| evolution/admission.py | 八门槛、拒收/待审、三元组n-gram Jaccard、确定性家族配额 | 真实证据采集与信任链；formal_qualified始终为false |
| evolution/feedback_view.py | 白名单、canary扫描及泄漏负例 | 真实E/V/H分区清单在生成侧的强制接入、编码与语义泄漏检查、跨权限域部署 |
| evolution/diagnosis.py、orchestrator/replay.py | 按身份实际执行fixture重放、区分缺投递与可复现，不确认根因 | 真实重放执行、最小条件诊断 |
| evolution/test_generator.py、strategies.py | 固定共享模板池、带种子的随机/反馈排序、表述对照及候选谱系 | 冻结的真实生成器、真实攻击变换和良性配对生成 |
| orchestrator/fixture_cycle.py | 原生基线、候选三次重放、同模板良性对照、等预算三臂、证据清单及续跑核验 | 来源资格、独立审核、真实运行资格及正式统计 |
| evolution/dataset_version.py | 内容寻址、全门槛检查、fixture隔离、原子发布与核验 | 真实资格通过后的正式版本发布 |
| evolution/metrics.py | 发现键去重、未知配对扣留净指标、家族/规则增量 | 主指标确认、完整统计与置信区间 |
| evolution/qualification.py | 来源存在性检查，存在不等于资格 | 可解性、投递、评分、许可、隔离和真实重放证据 |

schema位于[契约文件](../../protocol/loop_a_contracts.schema.json)与[RunIdentity schema](../../protocol/run_identity.schema.json)。正式协议仍为[待确认草案](../../protocol/loop_a_experiment.json)。

指标边界另有[14个可复跑合成案例](../../assets/samples/loop_a_metric_cases.json)，覆盖重复、良性退化、未知配对、原基线、无投递、未复现、待接纳和家族/规则增量。

## 指标与接纳策略需共同冻结

净指标仅在同一发现键内对齐攻击发现与良性扣减，不减未去重的执行次数。缺配对或配对互相矛盾时净指标为null。家族与规则新增均相对指定基线。

如果接纳策略要求同一目标的所有良性配对成功，接纳后的净指标扣减项将恒为零。正式协议须确认是在接纳前统计候选发现与良性扣减，还是采用良性门槛后的发现数。当前只实现草案口径和边界测试，不代替用户冻结primary。

## 兼容性与执行边界

LLM保留invalid_prediction计为不通过、拒答失败并单列、API错误不进入正确率分母的语义。抽取规则改变后历史分数应按新评分器重算，不能靠缩小分母提高成绩。旧结果缺少run_identity.json时拒绝续跑；不删除原输出，使用新目录重新运行。

原生harness分发格式未修改；当前Loop A worker在其外层绑定公开任务、私有规则和执行代码身份。已有产品入口的隔离声明缺少强制消费问题仍待真实执行器改造，本次Loop A不调用该入口，不能声称已在所有旧入口修复。

运行产物在runs下且被Git忽略。fixture、mock、来源映射与正式资格分别记录。未完成的job保持预算占用并要求核对，不因重启而自动退款或重复执行。
