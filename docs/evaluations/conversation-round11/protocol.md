# 第十一轮：MQ 可靠性验收协议

预注册日期：2026-11-01。本文在真实 broker 集成验收前定义范围；列出的场景是待验证目标，不代表已通过。代码提交、环境、执行结果和原始日志另存本目录，结果不得回写为事先通过。

## 范围与费用边界

本轮外部 LLM、embedding、私有 GPU 调用均为 **0**。业务载荷使用隔离工作区的合成文档和 `bm25/extractive` 任务；允许固定响应的本地 worker 验证调度。真实依赖为 MySQL、Redis 和单节点 RabbitMQ。数据库、RabbitMQ vhost/queue、Redis 命名空间与主服务隔离，不修改主服务任务记录。

目标是验证“事务 outbox + publisher confirms + mandatory return + 手工 ACK + inbox 幂等 + 统一任务领取”的本地可靠性边界。采用至少一次投递和业务幂等，不宣称第三方端到端 exactly-once、生产吞吐、真实用户流量、多节点 quorum 故障切换或模型质量提升。

## 预定场景与断言

| 场景 | 输入或故障动作 | 必须观察到的结果 |
| --- | --- | --- |
| 原子提交/回滚 | 提交业务任务和 outbox；再使事务回滚 | 正常提交各一条；回滚不得残留任务或 outbox |
| 并发幂等与唯一领取 | 相同业务幂等键并发提交；多个投递者/任务领取者竞争 | 业务任务及事件唯一；lease owner/token 校验防止过期者提交 |
| 批量对账 | 至少 1,000 条隔离合成事件，经真实 RabbitMQ 接收 | 每条事件有 outbox 状态；成功接收的事件有唯一 inbox 行；不得只用任务终态代替消息到达证明 |
| 重复和乱序 | 重复投递相同 event_id；改变不同事件投递顺序；终态收到迟到通知 | 重复事件不增加 inbox 业务写入；不同事件可独立处理；终态任务不复活 |
| confirm 与无路由 | 持久消息发送到有效/无绑定路由，等待 publisher confirm | 有效路由获得确认；mandatory return 必须判失败，即使 broker 同时 confirm 也不得标为已投递 |
| confirm 后未写数据库 | 实际发送并获得 confirm，故意不调用 outbox published；过期后重新领取投递 | 同一 event_id 可重投；两次收到但 inbox 唯一；不会丢失或自动重新执行已结束任务 |
| inbox 提交后 ACK 丢失 | 消费真实消息、提交 inbox，再关闭未 ACK 的 channel | broker 重新投递；第二次接收被 inbox 判重复；完成 ACK 后队列无该未确认消息 |
| 有限重试与死信 | 无路由/连接故障/格式错误等可复现失败 | 有界重试或明确死信；错误可查；不无限快速重投，不将失败标成功 |
| 持久 broker 重启 | 持久 queue + persistent message 已确认后，真实停止并启动隔离单节点 broker | 重启后未消费事件仍可读取和对账；记录 broker PID/启动或日志证据 |
| MQ 唤醒到统一 claim | 禁用测试用调度器的恢复轮询和提交直接 signal，仅通过实际消息消费触发 signal | inbox 提交后唤醒；调用现有 WorkspaceStore.claim 完成唯一业务领取；重复通知不会形成第二条 worker/LLM 调用入口 |
| 未知模型结果不重发 | 任务标记 FAILED/usage_unknown 后收到迟到或重复事件 | 保持原终态和未知用量；worker 调用次数不增加 |

## 故障注入诚实边界

“发送成功后跳过 published”“提交 inbox 后关闭 channel”属于受控组件步骤或连接故障注入，不能描述为已经杀死并恢复整个 JVM 进程。RabbitMQ 真正停止/启动须另存进程或服务证据；若环境不支持该动作，结果明确列未完成，不用重新建一个 queue 代替 broker 重启。数据库事务和真实 broker 调用须独立观察，不以轮询兜底成功掩盖 MQ 未工作。

## 指标与封存

记录样本数、每场景通过/失败、outbox 各状态数、唯一 inbox 数、重复接收数、投递尝试数、明确死信数、最老积压年龄和投递/消费延迟（p50/p95/p99，说明测量起止点）。吞吐仅为本地合成组件测试观测值，包含当轮参数与硬件限制，不推导生产容量。

保存命令、退出码、Java/JVM 与依赖版本、代码 commit、环境中非秘密配置、JUnit XML/文本、关键对账 JSON 和 broker 重启证据；公开前过滤凭据。测试代码通过不等同于真实集成通过；被环境门控跳过的测试单列。失败保留后修复重测，每次结果单列。最终以实际证据报告完成范围，允许将未达成项移入后续轮次，不能将其计为通过。

## 首轮结果后、最终复测前的追加验收

首轮结果由独立运行日志保留。本次代码审查追加真实 broker 的拓扑不兼容场景：预先声明 classic queue，再用生产 `MqConfiguration.taskEventTopology` 所要求的 quorum queue 和生产 `RabbitAdmin` 声明策略尝试接管，必须观察 RabbitMQ `406 PRECONDITION_FAILED` 向调用者传播，不允许吞掉错误并误报拓扑可用。旧队列保留，测试结束后才清理。该场景是首轮后的显式追加，不回填为首轮已验证。

消费结果命名统一为 `NOT_QUEUED`，表示收到通知时业务任务不处于 QUEUED，涵盖 RUNNING、CANCEL_REQUESTED 和最终状态；不能把这一类别数量等同为业务完成数量。

## 拓扑参数升级暴露的隔离问题

新增队列字节上限后，最终复测的首次发布连接被严格 `RabbitAdmin` 拒绝：旧原型的默认队列缺少 `x-max-length-bytes`，与新声明不兼容，broker 返回 406。该失败保留于当次原始日志，不归因于业务消息丢失，也不通过关闭严格声明或删除旧队列消除。

修复测试隔离：每次 `TaskMqIntegrationTest` 类运行用 `DynamicPropertySource` 给 Spring context 自己的 exchange、queue、DLX 和 DLQ 分配新的 UUID 命名空间；每个场景已有的手工队列仍各自唯一。Spring context 的空队列可保留在隔离 vhost，并在每份证据记录其 namespace。刻意制造拓扑不兼容的独立测试继续要求真实 406 传播。该修改后需重新执行完整验收，旧失败不改为通过。
