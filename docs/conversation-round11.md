# LabLens 第十一轮：任务通知可靠性与真实停机恢复

2026-11-01。本轮接通默认关闭的 RabbitMQ 通知链路，并用真实 MySQL、Redis、单节点 RabbitMQ 和隔离 HTTP 服务验证。**1,000 条合成元数据事件完成逐项对账；真实 broker 停机后，分别验证消息恢复唤醒和数据库轮询兜底。** 本轮外部模型、embedding 和 GPU 调用为 0，不改变此前模型评测结果。

## 解决的问题与取舍

原系统已将任务持久化到 MySQL，但进程内唤醒信号不能作为跨进程可靠通知。直接先写数据库再发送消息，会留下“任务已提交、通知没发出”的窗口；发送或 ACK 结果未知时盲目重做任务，又可能重复触发有成本的模型调用。

本轮让任务和 outbox 事件在同一事务提交；投递器在事务外等待 broker 确认，失败有限退避，超限保留 `DEAD`。消费者核验任务和快照身份，持久化唯一 inbox 收据后唤醒已有调度器。MySQL 的领取、owner 和租约仍决定任务是否可执行，重复或迟到通知不会重置失败、取消或用量未知的任务。[设计与限制](evaluations/conversation-round11/design-review.md)

MQ 默认关闭，消息只携带引用。保留数据库轮询，是为了恢复“inbox 已提交但进程内 signal 丢失”等窗口；测试中关闭轮询只是用于证明消息链路实际工作。这里的保证是至少一次通知投递加业务幂等，不是第三方端到端 exactly-once。配置和处置步骤见[服务说明](../services/workspace/README.md#optional-rabbitmq-notifications)。

## 已执行结果

| 验证层 | 实际结果 | 证据与口径 |
| --- | --- | --- |
| Java 主矩阵 | 发现 115 项，113 通过、0 失败、0 错误、2 条条件重启测试跳过 | [JUnit 报告](evaluations/conversation-round11/final-candidate/surefire-reports/)、[重新计算记录](evaluations/conversation-round11/independent-evidence-review.md)；跳过不算通过 |
| 单独 broker 重启用例 | prepare / verify 两阶段各 1 项通过 | [准备日志](evaluations/conversation-round11/logs/restart-prepare.log)、[恢复日志](evaluations/conversation-round11/logs/restart-verify.log)；与主矩阵分别报告 |
| 合成元数据批量对账 | 1,000 个事件均为 PUBLISHED，1,000 条唯一 inbox、1,000 次唯一任务领取、1,000 条合成完成记录，8 个领取 owner | [逐事件 ledger](evaluations/conversation-round11/final-candidate/java-evidence/thousand-events-ledger.json)；调用真实数据库 claim/finish，不是 1,000 次 Agent 或模型请求 |
| 真实停机后的 MQ 唤醒 | 原 4 条排队任务恢复后全部 SUCCEEDED；4 条 inbox、4 次 Python worker 请求 | [恢复结果](evaluations/conversation-round11/recovery/recovered.json)；JVM 恢复轮询关闭，未重新提交任务 |
| broker 不可用时轮询兜底 | 2 条任务均完成，该批 inbox 为 0；broker 恢复后收到 2 条 NOT_QUEUED，worker 请求仍为 2 | [停机期间](evaluations/conversation-round11/recovery/fallback-broker-down.json)、[恢复后](evaluations/conversation-round11/recovery/fallback-recovered.json)；只按该批 task ID 对账，不混用全库累计计数 |
| 原 HTTP 整机回归 | 13/13 检查通过，含 32 条 BM25/extractive 任务全部完成 | [整机报告](evaluations/conversation-round11/system/report.md)；小型本机闭环负载，0 次模型调用，非线上容量 |

批量场景先提交、再投递、再接收、最后并发领取，因此延迟包含阶段等待。outbox 创建到确认的 p50/p95/p99 为 **883.655 / 989.199 / 1,024.460 ms**，到 inbox 收据为 **1,617.198 / 1,777.356 / 1,817.969 ms**。这些数值用于本次对账，不能解释为在线消息延迟或最大 QPS。[测量起止与原始汇总](evaluations/conversation-round11/final-candidate/java-evidence/thousand-events.json)

真实恢复分两条路径证明：第一条先关闭调度与消费，通过 HTTP 保存 4 个任务并确认通知已入 broker，再停止 broker/JVM；确认旧 broker 进程已退出后启动 JVM，保持轮询关闭，此时任务仍排队。broker 恢复后，生产 listener 触发原调度器，真实 Python BM25 返回并入库。第二条在 broker 关闭时保存另外 2 个任务，重启 JVM 并开启轮询，先由数据库完成业务；随后通知迟到，只登记 `NOT_QUEUED`，没有增加 worker 请求。[准备状态](evaluations/conversation-round11/recovery/prepare.json)、[停机状态](evaluations/conversation-round11/recovery/broker-down.json)、[进程时间线](evaluations/conversation-round11/recovery/lifecycle.jsonl)

组件故障还覆盖无路由时不能把 ACK 当成功、确认后未写数据库导致重投、inbox 提交后关闭未 ACK channel、坏消息/数据库重试耗尽进入死信，以及真实拓扑错配拒绝。跳过一次方法调用是受控组件步骤，不冒充杀死完整 JVM；真实进程恢复由上面的独立记录证明。[场景对应与证据核对](evaluations/conversation-round11/independent-evidence-review.md)

## 实际难题与修正

- **依赖 API 不能凭记忆调用。** 首次编译使用了不存在的 `setRabbitAdmin`，对照当前 Spring AMQP 3.2.12 改为 `setAmqpAdmin`，保留失败与后续验证。
- **拓扑错配需要迁移，不能忽略。** 加入严格检查后，早先创建的同名队列缺少 `x-max-length-bytes`，真实返回 406。保留原队列和失败现场，测试改为独立命名；部署时应先对账、排空或迁移新版本队列，不能自动删除存量。发布者同时改用独立连接；消费者结果改为 `NOT_QUEUED`，避免把 RUNNING 误称为终态。
- **停止命令返回不等于进程已退出。** 首次观察器过早检查 OS 进程，后续补记旧 PID 已退出、端口关闭及新 PID；没有通过重建任务掩盖等待窗口。[原始时间线](evaluations/conversation-round11/recovery/lifecycle.jsonl)
- **观测代码也会失败。** 未发生 worker 请求时，尚未注册的计数指标返回 404 空体，观察器直接解析 JSON 失败。保留原失败，修正观察器后继续核对相同任务，没有重置任务或重复提交。[观察器失败记录](evaluations/conversation-round11/recovery/broker-down-observer-failed.json)

源码哈希、原始事件和逐次失败共同约束结论；本轮证据复核由 AI 协作者完成，不能称为独立人工验收。CI 和 PR 状态不由本地测试推断，以实际远端记录为准。

## 简历表述与下一轮

> 为研发诊断工作区实现 Spring Boot / MySQL 事务 outbox 与 RabbitMQ 通知链路，通过 inbox 幂等、租约 token 和有限重试避免重复业务处理；在真实本地依赖上对账 1,000 条合成事件，并验证 broker 停机恢复、数据库轮询兜底及迟到通知不重复执行，保留故障记录与复现证据。

本轮没有证明生产流量、多节点高可用、百万数据量、长期记忆或模型私有部署。第十轮四次真实直接复核仍全部超时、用量未知；第九轮严格 0/6、0/4 原样保留，没有重跑旧 LLM 评测。不能把消息可靠性写成回答正确率或模型吞吐提升。[旧模型结果](conversation-round10.md)

下一轮集中做**并发准入、背压与饱和恢复**：先冻结本地负载和资源上限，再逐档提高到达率，观察拒绝、队列年龄、数据库连接等待、任务延迟和恢复排空；明确区分固定/离线 worker 与真实模型负载。单独保留模型交付验收入口，尚未验证的性能不填入简历。[后续路线](backend-llm-capability-roadmap.md)
