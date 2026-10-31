# Round11 数据库通知与传输设计复核

日期：2026-11-01。本文记录设计判断、审查中发现的问题和保留边界；实际执行结果以本目录封存的测试报告为准。

任务状态由 MySQL 中原有任务表负责。RabbitMQ 承载的是 `task.available.v1` 通知，消息仅包含事件 ID、任务 ID、工作区 ID、资料快照引用、schema 版本和事件类型；没有问题文本、文档正文、凭据或模型请求。消费者只会调用既有 `TaskDispatcher.signal()`，再由数据库领取任务，不新增模型调用入口。

## 提交、重复投递与状态边界

[WorkspaceStore](../../../services/workspace/src/main/java/io/lablens/workspace/WorkspaceStore.java) 在同一个提交事务中写入任务和 outbox。幂等请求已经存在时提前返回原任务，outbox 的 `task_id` 唯一约束进一步防止同任务重复创建通知。默认 `lablens.mq.enabled=false`，不采集新事件，原有数据库调度路径继续工作。

发布者在短事务中领取事件，事务返回后再联系 broker。确认成功后写入 `PUBLISHED`；如果 broker 已接收，但确认或数据库回写丢失，通知可以再次投递。每次领取都创建新的 `lease_token`，后续确认和失败回写同时检查事件 ID、owner、token、状态与未过期租约；即使同一个 owner 再次领取，也不能用旧 token 改写新尝试。

[TaskEventStore](../../../services/workspace/src/main/java/io/lablens/workspace/TaskEventStore.java) 会在一个事务中核对完整消息身份、原 outbox 和现存任务的快照引用，再写唯一 inbox 收据。已处理的 ID 也必须通过身份核验。接收结果含义如下：

| 结果 | 含义 | 后续动作 |
| --- | --- | --- |
| `ACCEPTED` | 首次收到，任务当前为 `QUEUED` | 提交 inbox 后唤醒原调度器 |
| `DUPLICATE` | 身份相符的收据已经存在 | ACK，不再唤醒 |
| `NOT_QUEUED` | 首次收到，但任务当前非 `QUEUED` | 保留实际 `task_status`，ACK，不改任务 |

`NOT_QUEUED` 包括 `RUNNING`、`CANCEL_REQUESTED` 及任务终态，故没有使用容易误解的 `TERMINAL` 命名。过期的 live/Agent 任务仍由原租约清理逻辑保留 `FAILED` 和用量未知；通知不会将其恢复为排队状态。

inbox 事务与 broker ACK 不是同一个原子事务。事务提交后、ACK 前中断会产生重复消息，唯一收据可以去重；事务提交后、`signal()` 前中断，则依靠原有数据库轮询恢复。关闭数据库轮询仅用于证明测试走了消息唤醒路径，不应当被当作生产恢复配置。

## 索引与锁设计

早期候选 SQL 将“待发送/重试”与“租约到期”分为多个状态条件，再按创建时间排序。该写法存在随着历史增长扩大扫描和锁竞争的风险，但本轮没有采集它的性能基线，不能报告一个未经测量的优化倍数。

当前 [V6 migration](../../../services/workspace/src/main/resources/db/migration/V6__task_event_outbox_inbox.sql) 用 `(next_attempt_at,event_id)` 组织可领取的时间：

- `PENDING`：提交时即可领取。
- `RETRY`：保存指数退避后的下一次时间，最多 60 秒。
- `SENDING`：该字段为当前租约到期时间；查询还核验 `lease_until` 确实到期。
- `PUBLISHED` / `DEAD`：该字段为 `NULL`，不进入 `<= 当前时间` 的领取范围。

领取查询显式使用该索引，按相同字段排序，使用 `LIMIT 1 FOR UPDATE SKIP LOCKED`，在 `READ COMMITTED` 下更新 owner、token 和尝试次数。broker 等待发生在事务之外。每次调用最多清理 100 个已耗尽尝试次数的记录，避免一个清理循环无限占用事务。

这里接受领取时跳过其他事务已锁住的事件，不承诺严格 FIFO。MySQL 将 `SKIP LOCKED` 定位为队列类表的并发处理手段；它跳过行锁，并不意味着所有类型的等待都会消失。[MySQL locking reads](https://dev.mysql.com/doc/refman/8.4/en/innodb-locking-reads.html)

`READ COMMITTED` 减少范围读取中的间隙锁影响，但外键/重复键检查仍有相应锁行为，死锁也仍需处理。数据库故障由上层有界重试及后续轮询处理，不写成“无锁”。[MySQL isolation levels](https://dev.mysql.com/doc/refman/8.4/en/innodb-transaction-isolation-levels.html)

运维查询每种状态最多数到 1,001，并明确返回 `capped`；明细最多返回 100 条。最老未完成事件年龄通过 `(state,created_at)` 查询。状态页的 `broker_connection=not_checked` 表示这些数据库记录并不能验证 broker 当前连通性。这些约束控制返回量及访问路径，不构成百万级数据量、磁盘容量或尾延迟的压测证据。

## 传输审查和故障边界

复核确认 `mandatory`、publisher return 与 correlated confirm 都已启用。先等待确认 future，再检查 returned 元数据，与 Spring 文档提供的顺序保证相符；单纯 ACK 不能替代路由检查。[Spring AMQP confirms and returns](https://docs.spring.io/spring-amqp/reference/amqp/template.html)

审查要求在本轮收口前落实两个修正：发布者使用独立连接，避免 broker 资源告警阻塞发布连接时连带阻塞消费者释放积压；已有同名队列参数不一致时，明确拒绝继续使用不匹配的拓扑。此前 `ignoreDeclarationExceptions=true` 会让错误拓扑的验收含义不清。独立发布连接是 Spring 提供的配置选项；最终配置及测试见 [MqConfiguration](../../../services/workspace/src/main/java/io/lablens/workspace/MqConfiguration.java) 和对应配置测试。[Spring AMQP separate connection](https://docs.spring.io/spring-amqp/reference/amqp/template.html#separate-connection)

消费时数据库异常有次数上限，耗尽后拒绝并转向配置的死信路径。源队列和死信队列采用 quorum，源队列启用至少一次死信转移。若死信目标不可用，源队列会保留待转移消息；因此需要实际拓扑核验，不能只看 `basicReject(false)` 调用次数就宣称死信保存成功。[RabbitMQ dead lettering](https://www.rabbitmq.com/docs/quorum-queues#dead-lettering)

保留以下限制：

- `confirmTimeoutMs` 仅约束 `send()` 返回后的确认等待，不是整个 socket 写入、连接建立、数据库锁等待的统一硬截止时间。
- 单个发布线程、有界执行队列和消费 prefetch 控制并发数量；还没有完成 broker 资源告警或 TCP 停读条件下的完整恢复测试。
- Quorum `reject-publish` 的长度限制允许少量在途消息超限，不能称为严格的物理条数上限。[RabbitMQ queue length](https://www.rabbitmq.com/docs/quorum-queues#length-limit)
- 本轮没有 quorum 多节点失效演练、跨机房一致性验证、磁盘耗尽测试或真实线上用户流量证据。
- 事件有限重试到 `DEAD` 后不自动复活；任务仍保留在数据库，是否可执行由原任务状态决定。后续人工处置需要核对原因及现有收据，不能无条件重新发起 LLM 请求。

## 验证与资料生命周期

[TaskEventStoreIntegrationTest](../../../services/workspace/src/test/java/io/lablens/workspace/TaskEventStoreIntegrationTest.java) 使用真实 MySQL/Redis，覆盖事务回滚、并发幂等提交、并发领取、跳过锁定行、同 owner 旧租约隔离、退避与最终死亡、完整身份核验、inbox 并发去重、收据回滚、非排队状态保持、过期 Agent 用量未知，以及状态查询和级联清理。broker 传输与丢失确认的验证单独放在 `TaskMqIntegrationTest`；数据库测试不冒充真实 broker 测试。

外键采用 `tasks → outbox → inbox` 的 `ON DELETE CASCADE`。这是明确的生命周期选择：测试删除任务时不留下孤儿记录；应用当前没有生产任务删除接口。若以后增加任务清理或合规留存功能，必须先确定审计导出/保留期限，不能声称删除任务后通知审计仍永久保存。当前 outbox 记录是最新投递状态、次数和最后错误，不是每次尝试的不可变事件日志。
