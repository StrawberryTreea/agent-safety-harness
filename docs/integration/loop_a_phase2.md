# 回路A第二批实现：分区约束与执行预检

日期：2026-10-23。范围为T02与T06至T08的工程补强，正式实验协议继续保持草案。

## 本次行为变化

生成器和选择器现在必须接收冻结的GenerationScope。注册表按task_id记录family_id、E/V/H分区和公开任务内容哈希；同一家族不能跨分区，同一ID不能重复。只有E任务可以进入生成：V/H、未注册任务、冒用E身份但替换了内容、家族不符等情况在读取反馈和生成文本之前被拒绝。

反馈除ID之外还要与父任务的完整允许字段匹配；不允许夹带原始reason、额外私有字段或自行声明根因。接纳阶段再次核对控制器持有的分区清单，候选不能靠把partition写成E绕过限制。分区哈希进入cycle/compare运行身份、候选和门槛决定，三组比较也检查分区清单一致。

分区清单由显式分配构建，不从题目文本猜家族。它保证已冻结登记的执行边界，不证明登记本身正确，也不证明语义近重复或真实来源资格已被审核。

## 分区清单入口

仓库带有三条公开合成任务及对应E/V/H分配，仅用于功能验证：

```powershell
python pipeline.py loop-a partitions --tasks assets/samples/loop_a_partition_tasks.jsonl --assignments assets/samples/loop_a_partition_assignments.json --out runs/partition-check
```

输出partitions.json和summary.json。新目录不可被静默覆盖；前者通过schema和内容哈希校验后才能构造GenerationScope。示例中的H任务是公开的合成负例，不是正式留出集。

## 产品入口的隔离声明开始生效

run_product.py和run_product_batch.py都会先消费并校验isolation声明，再考虑选择集、目标、评分服务或续跑。当前宿主机子进程入口无法强制执行声明中的容器/虚拟机和网络边界，因此所有非dry-run调用都在启动产品、benchmark或judge前明确拒绝；写入container、fresh_product_state或verified标记不会获得执行资格。

可只读检查现有请求，不启动任何产品或模型：

```powershell
python federated_asr_benchmark/tools/run_product.py --request federated_asr_benchmark/examples/requests/codex_agentdojo.json --case-id unused --preflight-only
```

该命令预期返回2，status为blocked，isolation_verified和formal_ready均为false。加--dry-run则返回0及plan_only，但不会声称隔离已经验证。

这是原生产品CLI的兼容性变化：以前能直接在宿主机启动的真实命令现在会被拒绝，直到接入可强制隔离的启动器。现有ProductRunner库的工具行为与评分规则未修改；本次未实现容器/虚拟机启动器、真实计费或真实资格，也未启动付费实验。Qoder包装入口最终调用同一单条入口，但仍保留其原有runtime文件预检。

## 运行身份与迁移

产品dry-run计划绑定选择集原始字节、请求、目标、运行路径、执行入口及评分源码；单条和批量共用同一实现。批量在检查旧完成标记之前校验身份。无身份的非空目录或身份改变都要求使用新目录，旧结果不会被自动认领。Qoder批量计划与包装入口使用一致的固定CLI版本与执行路径。

上述身份明确标记为dry_run_plan，不代表实际产品版本已测量，也不代表外部来源内容和系统镜像已通过资格验证。

候选契约升级为test_candidate_v2，必需partition_manifest_hash。generate、select和evaluate调用需显式提供scope；接纳与版本发布检查分区哈希。旧候选不能直接按新契约发布；旧运行目录的代码身份也会变化，应使用新目录重跑，保留原始证据。

## 本次与后续范围

本轮补齐逻辑分区和产品入口的拒绝执行条件。T03真实来源资格、真实隔离启动器、目标工具循环、provider用量采集、真实生成器和审核证据链仍待完成；T09预实验和T10正式比较仍未启动。当前primary保持null，正式发布继续关闭。

## 验证结果

新增19项回归：11项分区与生成边界测试、8项产品身份及执行预检测试。全仓库验收记录为`runs/check-20261023T081920-a8d27c4a/summary.json`，状态passed，合计461发现、397通过、64项外部资产/runtime跳过、0失败。根目录94/94，Harness 155通过/10跳过，产品模块148通过/54跳过；三组本地闭环仍共18次目标执行，无付费调用。

公开合成E/V/H样例的分区构建已实际运行，输出`runs/partitions-20261023/`，每区1条。此结果只验证分区工程边界，不表示真实来源或正式留出集已完成审核。
