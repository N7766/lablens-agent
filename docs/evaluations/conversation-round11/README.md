# Round 11：MQ 可靠通知证据

2026-11-01，本机 MySQL 9.5 / Redis / RabbitMQ 4.4.0 / Java 24。使用隔离数据库、vhost 和端口；Java 21 / MySQL 8.4 的兼容性另由 CI 检查。全部资料与任务为合成数据，外部模型、embedding 请求为 0。

先读[项目报告](../../conversation-round11.md)、[验收协议](protocol.md)、[设计审查](design-review.md)和[逐项证据复核](independent-evidence-review.md)。本目录包含成功与失败；AI 协作复核不等于独立人工出题验收。

| 验收层 | 结果与原始记录 | 能证明的范围 |
| --- | --- | --- |
| Java 全套 | [JUnit 汇总](final-candidate/junit-summary.json)、[完整 Maven 日志](logs/final-isolated.log)：113 执行通过，2 条件跳过 | 默认关闭 MQ 的现有回归以及启用后的数据库/传输语义 |
| 1,000 事件 | [逐事件 ledger](final-candidate/java-evidence/thousand-events-ledger.json)、[摘要](final-candidate/java-evidence/thousand-events.json) | 1,000 唯一 outbox/inbox/领取/合成完成；8 个领取者；不等于 1,000 次 Agent 请求 |
| 真实 MQ 唤醒 | [准备](recovery/prepare.json)、[停机中](recovery/broker-down.json)、[恢复](recovery/recovered.json)、[进程记录](recovery/lifecycle.jsonl) | 4 个持久排队任务，JVM 禁用 poll 且不新提交任务，broker 恢复后真实 listener→dispatcher→Python 完成 4 次 |
| 数据库兜底 | [准备](recovery/fallback-prepare.json)、[broker 停机中](recovery/fallback-broker-down.json)、[恢复](recovery/fallback-recovered.json) | 2 个预先入库任务由 poll 完成，迟到消息补记 NOT_QUEUED，worker 请求仍为 2 |
| broker 消息持久化 | [prepare](recovery/broker-restart-prepare.json)、[verify](recovery/broker-restart-verify.json)，各自 [测试日志](logs/restart-prepare.log) / [测试日志](logs/restart-verify.log) | 真正停止、启动不同 PID 后，同一持久消息 ID 与字节体仍在；两阶段各 1 项通过 |
| 整机兼容 | [报告](system/report.md)、[原始 HTTP](system/observations.jsonl)、[汇总](system/summary.json) | 13/13 控制检查、32 个 BM25/extractive 任务；HTTP 提交仍直接 signal，不用此层证明 MQ 因果触发 |
| 结束与清理 | [系统状态](recovery/final-system-status.json)、[清理](recovery/cleanup.json) | 整机 41 条 outbox 均 PUBLISHED，41 inbox、0 积压；停止本轮服务，保留主服务与测试数据 |

## 原始失败与修复顺序

1. [首次编译日志](logs/first.log)：当前依赖没有 `setRabbitAdmin`，查本地 API 后改为 `setAmqpAdmin`。
2. [初轮执行](logs/integration-first.log)：110 项执行通过；当时尚未加入独立 publisher 连接、严格拓扑校验及最后的审查用例，不能冒充最终候选。
3. [严格复测失败](logs/final.log)：旧原型队列没有新增的 `x-max-length-bytes`，触发真实 406。保留旧队列；测试的 Spring context 使用独立 UUID 命名。正式部署需排空/迁移版本队列，不能吞掉声明错误或自动删除。
4. 真实停机观察器首次在 stop 返回后立即检查进程，早于进程完全退出；[lifecycle](recovery/lifecycle.jsonl) 保留失败以及稍后的退出确认。另一次[观察失败](recovery/broker-down-observer-failed.json)来自将不存在计数器的空 404 响应当 JSON 解析。修正观察器后只重复读取状态，未重置任务或重做业务负载。

`initial-evidence/`、`retest-failed-evidence/` 与 `final-candidate/` 分别保留每轮原始 ledger 与 JUnit 文本。发布 XML 的计数与耗时摘录，避免无关 JVM 属性进入公共记录；完整 XML 留在本地，CI 上传自己产生的原件。`harness/` 保存实际使用的操作脚本，含本机路径，只用于审计；移植启动请使用[服务说明](../../../services/workspace/README.md)。私有凭据文件不包含在仓库。

## 边界

本次 broker 使用有序 stop/start，不是断电、磁盘损坏或多节点故障。confirm 后未落库与 inbox 后丢 ACK 属于受控组件步骤；DB 重试耗尽场景为注入异常，不是实际 MySQL 停机。inbox commit→signal 间隙仍依赖默认数据库 poll。没有端到端 exactly-once 或生产 SLA 保证。

短窗合成 HTTP 吞吐与分批事件延迟用于复现本次验收；没有最大容量、优化前后收益或真实模型并发的结论。模型语义结果保持第九/十轮原记录。下一轮再按独立协议测过载、背压和持续流量。

`final-candidate/source-hashes.json` 记录本地 Java 验收时的源码；随后仅增加了 CI 的真实 broker 重启步骤及文档。`publication-manifest.json` 固定本轮发布证据文件的 SHA-256（不含自身），GitHub 检查状态以对应提交的运行记录为准。
