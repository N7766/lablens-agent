# 第十一轮证据复核记录

日期：2026-11-01。核对对象为 root 封存的 `final-candidate/`：JUnit 报告、逐事件 ledger、8 个场景摘要和源码哈希清单。复核通过重新解析原始 JSON/XML、计算 ID 集合与时间分位数完成，没有再次运行测试、修改生产实现或发起模型调用。

**身份边界：这是 AI 协作复核，核对者参与了集成测试编写，不能称为未参与开发者的独立人工验收、独立出题或新问题盲测。** “独立核对”仅指重新计算证据数值，未直接照抄汇总结果。

## JUnit 与源码对应

逐份合并本机封存的 12 份 `TEST-*.xml`（公开目录保留对应 JUnit 文本摘要，避免 XML 携带无关环境信息），得到发现 115 项、通过 **113 项**、失败 0、错误 0、跳过 **2 项**。其中真实依赖集成测试通过 56 项，其余通过 57 项；不能把跳过项算作已测。

`TaskMqIntegrationTest` 发现 10 项，8 项实际通过；`brokerRestartPrepare`、`brokerRestartVerify` 因条件变量未指定而跳过。因此这次全套 Maven 本身没有完成 broker 重启验收，真实重启必须由另外的进程和阶段证据证明。

`source-hashes.json` 包含 38 个文件。核对时与工作区逐文件重新计算的 SHA-256 全部相符，没有发现测试结果和当前实现文件不一致。源码清单 SHA-256：

```
c02fa2c635140ad326d8ad3baa2ca350a74017f6c1f83261856a0baddd0513c4
```

## 1,000 事件逐项对账

读取 `java-evidence/thousand-events-ledger.json` 的全部行，重新核对：

| 项目 | 重新计算结果 |
| --- | ---: |
| ledger 行数 | 1,000 |
| 不同 event_id / task_id / claimed task_id | 各 1,000 |
| ledger task_id 与独立记录的 claimed task_id 集合 | 完全相等 |
| PUBLISHED outbox / ACCEPTED inbox / SUCCEEDED task | 各 1,000 |
| attempts = 1 | 1,000 |
| usage_unknown = false | 1,000 |
| 不同任务领取 owner | 8 |
| created_at ≤ published_at ≤ accepted_at | 1,000 行均满足 |

这给出了事件→唯一 inbox→任务领取→合成完成记录的一一对应。场景用固定逻辑调用真实数据库 `claim/finish`，不等同于执行了 1,000 次 Python Agent 或外部模型请求。批量消费者的 dispatcher 为受控替身，真实 MQ→dispatcher→HTTP worker 链路由另一场景单独验证。

ledger SHA-256：

```
405d825e4aa50bd8388b588a754f6e300c884fb6797e4f517d2d29ba7023b03d
```

重新从 ledger 微秒时间戳计算 nearest-rank 分位数，与 `thousand-events.json` 完全一致：

| 测量起止 | p50（ms） | p95（ms） | p99（ms） |
| --- | ---: | ---: | ---: |
| outbox.created_at → published_at | 883.655 | 989.199 | 1,024.460 |
| outbox.created_at → inbox.accepted_at | 1,617.198 | 1,777.356 | 1,817.969 |

总场景记录为 2,697 ms。测试先提交所有任务，再顺序确认投递，再手工接收，最后执行 8 个并发数据库领取者。上述延迟包含阶段等待，不是持续在线消息延迟、最大吞吐或生产 SLA；不能据此宣称能承受某个真实 QPS。

## 八个场景与证据映射

路径均相对于 `final-candidate/`。

| JUnit 场景 | JSON 证据 | 核对结果 |
| --- | --- | --- |
| 1,000 事件与并发领取 | `java-evidence/thousand-events.json`、`thousand-events-ledger.json` | 唯一事件、inbox、领取与完成各 1,000，队列余量 0 |
| mandatory return | `java-evidence/mandatory-return.json` | 第 1 次为 RETRY/unroutable，第 2 次为 DEAD；inbox 0，没有误记 PUBLISHED |
| confirm 后未标记 DB | `java-evidence/confirm-before-db-mark-gap.json` | 2 次真实投递、2 次发布尝试、1 条 inbox、1 次 dispatcher signal |
| inbox 提交后 ACK 丢失 | `java-evidence/inbox-before-ack-gap.json` | broker redelivered=true；inbox 仍 1；重复 signal 0；任务仍 QUEUED |
| 乱序、重复和迟到通知 | `java-evidence/duplicate-reverse-terminal.json` | 5 次投递对应 3 条 inbox；仅 2 个排队任务发出 signal；原 FAILED/usage_unknown=true 保持 |
| 坏消息和 DB 重试耗尽 | `java-evidence/bounded-dead-letter.json` | 坏消息与 DB 故障各 1 条真实 DLQ；DB 尝试 3 次，inbox 0 |
| 真实 MQ 唤醒 dispatcher | `java-evidence/mq-only-dispatcher.json` | 无定时恢复 poll、无提交直接 signal；2 次消息对应 1 条 inbox、1 次本地固定 HTTP worker 请求，任务 SUCCEEDED |
| 拓扑不兼容 | `java-evidence/topology-mismatch.json` | 真实 broker 406 向调用者传播；旧 classic 拓扑未被悄悄重建 |

八份场景摘要均标记合成数据、外部模型调用 0，且使用同一全新 Spring context 拓扑命名空间 `round11.context.0d5d2a46-713a-48de-a38c-c9ec72a78a46`。逐项结果与 JUnit 对应测试均为通过一致。

## 必须保留的限制与失败

- 全套 Maven 的两个 broker 重启测试被跳过；不能用该测试集证明重启成功。
- confirm 后跳过 DB 标记是组件步骤；ACK 场景是真实关闭未 ACK 的 channel，都不等同于杀死并恢复完整 JVM。
- inbox 提交成功但 signal 前中断，重复消息不会重新 signal。该证据明确保留 QUEUED 状态，恢复仍依赖数据库轮询。
- NOT_QUEUED 包括 RUNNING、CANCEL_REQUESTED 和最终状态，不能把所有该类通知算成已完成任务。
- DEAD 表示通知投递预算耗尽或明确路由失败等可查状态；对于确认未知，不能推断 broker 一定未收到。
- 最终通过前曾发生真实 406：旧默认队列缺少新 `x-max-length-bytes` 参数。保留该次失败，测试改为 UUID 隔离 Spring 自身拓扑；没有删除旧队列或关闭严格校验。刻意拓扑冲突案例的首个不一致字段可能是 DLX 参数，不能把泛化 406 写成仅由 queue type 单独引起。
- 本轮为单节点 RabbitMQ、本机合成负载与 AI 协作核验；没有多节点 HA、真实业务流量、LLM 质量评测或模型压测结论。

真实 Spring listener/relay、Python HTTP 和 broker 停机/恢复已由下述阶段证据另行核对，不由上述组件证据代替。

## 补充复核：真实 Spring/Python 与 broker 进程重启

读取 `recovery/fixture.json`、`recovery/fallback-fixture.json` 后，始终按这两组固定 task_id 过滤各阶段的 task、outbox、inbox。第二组测试的状态接口同时含有先前 39 条记录，以下数量均为该组的增量，不把历史任务重复计入。

| 阶段证据 | 同一组任务 | outbox | 该组 inbox | worker HTTP 请求 |
| --- | --- | --- | --- | --- |
| `recovery/prepare.json` | 4 QUEUED；dispatcher 与 consumer 关闭 | 4 PUBLISHED，attempts 均 1 | 0 | 未启动业务执行 |
| `recovery/broker-down.json` | 同 4 QUEUED；dispatcher 开启、poll 关闭、broker 停机 | 同 4 PUBLISHED | 0 | counter 尚不存在（404）；任务没有 started_at |
| `recovery/recovered.json` | 同 4 SUCCEEDED | 同 4 PUBLISHED，attempts 仍 1 | 同 4 event_id，全部 ACCEPTED | 4 |
| `recovery/fallback-prepare.json` | 另 2 QUEUED；dispatcher 关闭、broker 停机 | 2 PENDING，attempts 0 | 0 | 未启动该组业务执行 |
| `recovery/fallback-broker-down.json` | 同 2 SUCCEEDED；dispatcher/poll 开启、broker 仍停机 | 2 RETRY，attempts 均 2 | 0 | 2 |
| `recovery/fallback-recovered.json` | 同 2 SUCCEEDED，未重置 | 同 2 PUBLISHED，attempts 均 3 | 同 2 event_id，全部 NOT_QUEUED | 仍为 2 |

逐组 task_id 与 fixture 完全一致，MQ-only 的 4 个 event_id 跨阶段不变；fallback 恢复后的 2 个收据均对应原任务。broker-down 和 recovered 观察阶段只有 GET，无新增任务提交，排除了 HTTP 提交直接 signal 掩盖 MQ/轮询路径的情况。6 个最终任务的执行用量与结果用量均为 model_calls=0、embedding_requests=0，provider_usage_unknown=false。

`recovery/lifecycle.jsonl` 记录两次真实的单节点 broker 进程替换：`51522 → 55546 → 56086`，停机时 AMQP 端口关闭。第一轮在重启前追加了旧 PID 已退出的检查；第二轮停止时已经确认旧 PID 不存在。JVM 的阶段配置记录分别为 MQ-only 的 `poll_enabled=false` 和 fallback 的 `poll_enabled=true`。这些证据支持：完整 Spring listener 恢复消息后唤醒任务，以及 broker 不可用时数据库轮询能继续处理已持久任务；属于本机有序停机/重启演练，不等同于突然断电、强制杀进程或多节点故障切换。

独立运行的 `brokerRestartPrepare`、`brokerRestartVerify` 日志各为 **1 项通过、0 跳过**，其 `message_id` 均为 `4f65e947-7de0-4222-917b-cf1e162fe171`。配合上述实际 broker 生命周期，证实同一条已确认 persistent 消息在 durable quorum queue 中跨进程重启保留。测试实现使用 passive declaration，验证阶段没有重建队列来掩盖持久化丢失。它们是全套 Maven 后的两次选定执行，不回写或改称全套报告中的 2 个 skip 原本已通过。

保留了两个观察器问题：第一次 stop 返回后立即查询旧 PID 过早，当时 `prior_process_exists=true`；随后查询明确为 false，才重新启动。`broker-down-observer-failed.json` 则记录 JSONDecodeError，修正后的同一任务观察显示 `lablens.worker.requests` 尚未注册、HTTP 404 空体。调整的是观察程序的等待/解析，业务任务未重置。这两处不能当成产品成功结果，也没有证据将它们称为业务丢失；已按观察器失败单列。

## 补充复核：32 条整机闭环负载

`system/summary.json` 的 13 项检查通过；重新解析 `system/observations.jsonl` 得到 **133 条 HTTP 记录**，序号连续且与摘要声明数量一致。对 32 个负载任务，逐个匹配原始终态 GET 响应和摘要 task，内容一致；32 个 task_id 均不同、全部 SUCCEEDED、cache_hit=false、BM25/extractive，已知执行模型/embedding 调用均为 0。源码清单 before/after 完全相同。

从逐条样本重新计算插值分位数，与摘要一致：

| 指标 | p50（ms） | p95（ms） |
| --- | ---: | ---: |
| 提交 HTTP 延迟 | 8.500 | 19.072 |
| 客户端端到端，含轮询 | 115.906 | 129.950 |
| 数据库时间戳端到端 | 4.560 | 6.439 |

这是 8 客户端、32 条小型合成请求的闭环观测；0.380762 秒窗口中记录约 84.04 成功任务/秒。窗口短，包含提交/轮询且使用小文档，不能外推真实业务容量或模型吞吐。该 evaluator 的 HTTP 提交仍有直接 dispatcher signal，因此 13/13 证明整机功能与控制约束，MQ 因果唤醒由前述隔离阶段证明。
