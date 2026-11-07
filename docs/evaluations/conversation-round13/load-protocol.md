# Round 13 / 收尾 2：持续负载与资源诊断协议

2026-11-08。运行前冻结源码、协议和实际 jar。正式 candidate 只执行一次；未发送、HTTP 异常、未观测终态及失败原样封存，不覆盖失败重跑。若需要修复后新实验，另建身份与目录。原 Round 12 两组证据保持只读。

## 固定负载与预算

- 一台本机、独立空 MySQL 数据库、Redis namespace、单 JVM、2 个执行 worker。仅 4 个合成工作区，每区一份 sentinel 文档。
- 真实 BM25/extractive 前固定等待 400 ms；受控 worker 拒绝任何 provider 路径。MQ 关闭；普通 MQ 回归另行执行，不用本次 HTTP 负载证明 MQ 因果触发。
- 固定开放到达率 2 / 20 / 2 req/s，各 **180 秒**，共 **540 秒、4,320 个预定请求**。请求内容与幂等键每次唯一；预定发送不等前一个任务完成。
- 数据库活动上限全局 12、每工作区 8，Redis 每区每分钟 attempt 上限 10000。JVM `-Xms128m -Xmx512m`；Hikari max/min 12、连接获取超时 3000 ms；Tomcat max/min spare 200/10、max connections 8192、accept count 100。显式保留原有默认预算，不把改参数当性能优化。
- 客户端最多 32 个未完成提交、HTTPX 各 I/O 阶段超时 5 秒、最大调度落后 100 ms；错过槽位记录 unsent，不补发、不自动重试。最大请求保护 10000，排空轮询与逐 ID 查询共用 90 秒预算；收尾 worker 状态请求与在途指标请求另计，记录实际总耗时，不声称整个收尾有 90 秒硬墙钟上限。实际 HTTP in-flight 峰值单列，不能把配置上限写成实测并发。
- 状态每约 1 秒、actuator 每约 5 秒顺序采样。采样耗时会推迟后续采样，保留实际时间点；观察器也产生数据库和 HTTP 请求。模型小诊断、其他 CPU 密集测试不与正式负载重叠。

## 测量与检查

运行器复用 Round 12 的开放调度、每槽位账本、逐任务终态与接受/拒绝对账。新 profile 附加源码和本协议哈希，不改变旧默认 60 秒 CLI。日志包含配置、jar 来源提交和哈希。发送器/终态逻辑全部先做离线 fake 测试。

关注以下层次，不把它们混成一个延迟：

1. HTTP 提交：全部接受和拒绝响应 p50/p95/p99、漏发、异常、实际发送并发。
2. 持久业务：全局活动数、队列年龄、每个接受任务的 created→started→finished、worker 次数与缓存/用量。高水位与采样都不等于连续峰值。
3. 数据库连接池：Hikari active/idle/pending/max、获取连接 timer COUNT/TOTAL_TIME/MAX、timeout counter。计数是整个 JVM 数据源活动，包括观察器，不只业务提交。
4. 全局准入：guard 锁 SQL 调用 timer（包括 SQL 往返，不等于服务端纯锁等待）；提交事务 callback 到 completion timer（不含之前 Redis 或连接获取；外层 RC 调用包含 caller 后续事务时间）。原子不超限由数据库并发回归补强。
5. JVM/线程：heap area 指标与原聚合指标分开；executor 按 worker/dispatch/cancellation tag 读取 active/queued/预算；Tomcat busy/current/configured max。heap pool 聚合不是 RSS，timer MAX 是 Micrometer 时间窗口最大值，不当作精确全程峰值；COUNT/TOTAL_TIME 不能伪造 p95。

严格工程验收：全部 4320 预定槽位有记录；0 unsent/transport/unexpected HTTP；所有接受任务有唯一 ID、终态 SUCCEEDED、无缓存、明确零 provider 用量；全局采样≤12、业务执行并发≤2；过载有正 Retry-After 的明确容量拒绝；恢复后半阶段无容量拒绝且成功受理；最终排空；逐 ID 与 worker 开始/结束对齐；数据库任务总数恰好等于接受数。

资源验收：指定关键资源有真实有效样本，缺失值写 unavailable、不填 0；状态采样无错误；连接池无获取超时。早期未注册 timer 的 404 单列，与稍后有样本区分。资源预算从配置与状态核对；资源异常或过载造成的拒绝仍完整记录。另做真实数据库控制用例，刻意持有 guard、耗尽隔离连接池来证明观察器能识别等待；不能只用无等待负载推断诊断有效。

按每分钟输出 9 个窗口，包括拒绝与慢请求。先报告观测事实，再依据 worker 容量、连接等待、锁等待、线程与 CPU 判断瓶颈。若没有证据证明连接池或 HTTP 线程饱和，不随意扩大池子。9 分钟能补强短窗重复负载证据，不能证明数小时/多日稳定性、无内存泄漏或真实生产容量。

## 对照边界和模型诊断

Round 12 为每阶段 60 秒、本轮 180 秒，采样密度及仪表不同，不能据二者的时延差异宣称因果性能收益。原始 Round 12 只作为历史参考。本轮优化首先是使资源等待可观测、预算可配置和故障可复现；后续大数据量优化另设同条件前后对照。

本协议不产生模型调用；模型 probe 使用独立冻结协议、输入、预算、once-only claim 和结果目录。小请求或报告失败不重试，也不会改变已封存旧分数。主服务不升级，保持候选 draft。
