# LabLens 第十二轮：全局任务准入与过载恢复

2026-11-08。**十二轮收尾的第 1 轮已完成本地工程验收**，实现 MySQL 全局活动任务上限、Redis 不可用时的 provider 新请求拒绝、幂等重放及明确的重试响应。当前为候选分支，未合并发布。后续 11 轮见[计划](finalization-12-rounds.md)。

## 为什么要做

此前只有每工作区上限与每 JVM 的 2 个执行槽。多个工作区持续提交时，每个工作区都合法，却仍会让数据库积压放大。旧版在本次 4 工作区实验中实际达到 32 个活动任务；新版采用全局上限 12，按数据库事务串行检查容量。这里活动包括排队、运行及等待取消完成的任务。

新提交先获得 Redis 速率判断，再以 READ_COMMITTED 按 global guard → workspace → conversation 加锁，任务与 outbox 同事务提交。缓存故障可降级，有成本的新任务在速率门启用但 Redis 不可用时明确拒绝。已经提交的同键任务优先返回原记录，不因队列满或 Redis 故障而失去查询结果。[实现取舍与实际难题](evaluations/conversation-round12/design-and-execution.md)、[配置与故障处置](../services/workspace/README.md#task-admission-and-overload)。

## 真实对照结果

同一台机器，独立空数据库、4 个合成工作区，2 个 worker；每任务固定等待 400 ms 后执行真实 BM25/extractive。顺序运行旧版、新版各一次：2 / 20 / 2 req/s 各 60 秒，每组 1,440 次提交。没有外部模型、embedding 或 GPU 调用。发送端允许最多 32 个未完成提交，实际两组峰值均为 1；本实验验证的是业务积压与拒绝，不称 32 并发 HTTP 压测。

| 指标 | 旧版 | 新版 |
| --- | ---: | ---: |
| 预定 / 实际发送 | 1,440 / 1,440 | 1,440 / 1,440 |
| 受理并最终成功 | 558 | 539 |
| 明确容量拒绝 | 882 | 901 |
| 漏发 / 传输错误 / 意外 HTTP | 0 / 0 / 0 | 0 / 0 / 0 |
| 全局活动数采样最高 | 32 | 12 |
| 全部提交响应 p95 | 16.942 ms | 20.912 ms |
| 已受理任务排队 p95 | 6,225.847 ms | 2,073.528 ms |
| 已受理任务端到端 p95 | 6,640.847 ms | 2,485.195 ms |

两组最后 30 秒恢复阶段均 60/60 受理，最终活动任务排空；新版容量拒绝带 Retry-After。原始请求、最终数据库任务和 worker 开始/完成逐 ID 对账，避免仅凭总数相等断言没有重复。见[旧版汇总](evaluations/conversation-round12/load-baseline/summary.json)、[新版汇总](evaluations/conversation-round12/load-candidate/summary.json)、[AI 开发证据复核](evaluations/conversation-round12/evidence-review.md)。

排队分位数属于各自受理集合，新版更早拒绝部分请求，因此不能解读为模型或计算吞吐提速。全局 cap 的并发正确性由事务设计与真实数据库并发用例补强；压力采样不证明所有瞬时峰值。此实验未测最大 QPS、长期稳定性或真实用户流量。

## 故障与回归

完整 Java verify：**135 项发现、133 通过、0 失败/错误、2 条条件重启用例跳过**。其中新增 12 项准入集成测试，覆盖跨工作区争槽、RC 等待者、回滚、取消占位、幂等竞争、慢 Redis 不持全局锁、会话一致性与错误契约。真实 MQ 普通矩阵仍执行；本地两条跳过项不计为 broker 重启通过。[JUnit 汇总](evaluations/conversation-round12/java/junit-summary.json)。

正式负载后，另行停止只属于本轮的 Redis 6383：新的 provider 请求返回 503 admission_unavailable 且没有新增任务；原键重放返回 200 与相同任务 ID；BM25/extractive 在停机时仍完成。恢复 Redis 后再次提交离线任务并成功。故障观察与负载 worker 日志分开封存，不把额外任务混入 1,440 次账本。[故障原始结果](evaluations/conversation-round12/redis-outage/summary.json)。

两套验收运行器的 **29 项本地单测通过**。真实故障的首次预检因 Redis 配置未声明 PID 文件而安全停止，没有 POST 或停止进程；补齐配置后在新目录继续，保留[首次预检失败](evaluations/conversation-round12/redis-outage-preflight-failed/summary.json)。

## 难题、边界与简历素材

- 容量锁要短：Redis 判断放在正常 HTTP 提交事务之前；内部已有 RC 事务调用仍须遵守不预持资源锁的约束。不能把这个局部改动写成所有路径都不跨事务调用 Redis。
- 容量数据不能漂移：直接读取 task 状态，避免维护另一份需要失败补偿的容量计数；代价是全局 guard 热点，后续测持续负载和数据库连接等待。
- 速率统计必须说清：Redis 限制新提交 attempt，已提交重放不计；第一次提交前同键竞态可能多计，容量拒绝也不退款。它不是精确 token 费用额度。关闭 limiter 会同时关闭这项 Redis 故障拒绝保障。
- 工具路径也会失效：原 `/tmp` 下 Maven/Redis 已被清理，保留已创建的隔离状态，重建工具后继续；未删除主库、未替换主服务。观测记录明确缺失与采样范围，不补造峰值。
- 模型超时仍未解决：重核旧封存文件后，确认旧日志没有超时子类及首字节阶段，不能认定代理或 provider 根因。[下一轮有界诊断](evaluations/conversation-round12/model-diagnostic-next-step.md)。

可用于简历的一条事实：

> 为 Spring Boot / MySQL 异步任务系统实现跨工作区全局准入与 Redis 故障隔离，通过事务锁序、幂等键和 429/503 重试契约控制积压；在 4 工作区、每组 1,440 次合成开放流量对照中，活动任务采样峰值从旧版 32 降到配置上限 12，并验证过载后恢复、Redis 停机时的幂等重放和离线降级。

这不是线上事故经验、生产容量或模型质量提升。当前仍无公网鉴权、分布式费用额度和 GPU 部署；模型第九轮严格 0/6、0/4 与第十轮四次超时保持原记录。下一轮为**收尾 2/12：持续负载与资源预算，同时补齐模型传输诊断并进行有界小样本**。
