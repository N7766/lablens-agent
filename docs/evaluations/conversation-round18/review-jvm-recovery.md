# JVM 恢复边界实现与排查记录

本记录由实现 JVM 部分的 AI 子代理整理，不是独立人工验收，也不证明模型回答正确率。全套最终测试和受控 HTTP 故障演示由本轮主验收记录汇总。

## 完成的边界

- V11 为 `tasks.parent_task_id` 加唯一索引，`task_checkpoints` 保存递增、不可覆盖的回执；删除测试任务时回执通过外键级联清理。
- 内部回调只接受专用服务密钥、UUID 执行 owner 和精确 POST 路径。即使开发模式关闭浏览器身份认证，内部服务密钥也不能绕过。浏览器 cookie、Origin 和 Bearer 凭据不进入这个入口。
- 每次回调先锁 task，要求 `RUNNING`、owner 一致、数据库租约未过期。相同最新版本和摘要可读回既有回执；版本跳跃、改写旧版本、旧 owner、取消中或终态任务一律拒绝。
- 初始完整 boundary 后可连续记录 decision、embedding、review 等 operation_started；整个工具批次提交新 boundary 前，结果仍按未知处理。仅有部分回执不能恢复。
- 恢复先复用持久幂等结果，再经过 Redis 速率门禁、全局 admission guard、workspace 锁、权限复核、全局/工作区容量检查；child 与 outbox 在同一 MySQL 事务提交。parent 唯一索引防并发分叉。
- 子任务沿用原快照，当前文档修改和显式记忆删除不会改写已批准输入。最多一层恢复，终态父任务之外的请求、较新会话版本、未知操作均拒绝。子任务绕过结果缓存。
- 子任务不推进会话 revision，不覆盖原失败任务；活跃恢复子任务通过 parent join 阻止同会话新轮次。子任务报告位于任务历史，后续会话轮次不会自动把它替换进原失败轮次。
- 返回元数据绑定 parent、checkpoint revision、状态字节 SHA、恢复前步数/工具次数/模型次数/活动预算。`result.usage` 是累计量；`execution_usage` 只保存当前任务增量。未知 token 不被减成零。恢复前的零调用失败必须带明确 scope，不能冒用累计用量。
- 取消 registry acknowledgement 不含恢复来源；它不能把累计用量覆盖到子任务本次用量上。已有来源校验的取消结果可保存增量，否则保守保留 unknown。

## 实际修正

1. 初稿要求每次 operation_started 前都是 boundary。检查真实 Agent 流程后发现，一次批次内可能先决策再复核或 embedding。改为初始 boundary 后允许连续操作回执，直到整个批次完成才清除 pending。测试覆盖 boundary → decision → review → boundary。
2. 恢复子任务的 `conversation_id` 留空以避免重复推进原轮次，导致原活动查询可能漏掉它。改为查询子任务及 parent 的 conversation，并验证 child 活跃时新轮次返回 `conversation_busy`，child 终态后才接受。
3. Python 从检查点恢复累计用量；若直接写入 child.execution_usage，会重复计算父任务已知调用。改为从不可变 checkpoint 派生继承量并验证 worker provenance，再计算本次差量。前置失败和未知 token 单独保守处理。
4. 第一次 focused run 为 47 项、1 失败：测试声明 MQ 关闭，却断言应生成 Outbox。生产 enqueue 在关闭 MQ 时正常不写 Outbox。修正测试配置为 MQ 开启、relay/consumer 关闭，继续验证提交事务；没有放宽产品断言。

## 验证记录

- `java-recovery-focused-v2.log`：53/53 通过。
- `java-recovery-focused-v3.log`：56/56 通过，包括 MySQL 恢复事务 13 项、内部身份边界 2 项、恢复结果用量 6 项、既有 Worker/Memory/Dispatcher 35 项。
- v3 后补充 `usage_scope` 严格校验及一条“前置失败谎报调用量必须拒绝”的断言；由主验收的最终完整 Java suite 覆盖，不把 v3 当成此最终改动的验证结果。
- MySQL 集成用真实隔离数据库；MVC 回调用固定状态，不调用外部模型。选中记忆的场景经真实控制器完成边界、两个操作回执、最终边界、删除当前记忆后明确恢复，并确认子任务保留原记忆快照。

没有执行自动模型重试，没有修改父任务结果，没有实现供应商账单对账、跨机器 exactly-once 调用或无限恢复工作流。
