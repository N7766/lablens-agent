# Round 14：ABBA 完成后的真实 HTTP 功能与混合读取协议

2026-11-08。运行前冻结 `check_history_runtime.py`、本协议、确定性 seed 定义、实际 jar 和提交 SHA。两个 action 使用不同的新输出目录；不能覆盖目录、重发失败 mutation 或重置已运行的 mixed 状态。仅使用 root 创建的 100,000 条合成历史数据库和隔离 loopback JVM。100,000 条 `SUCCEEDED` 行是直接 seed 的历史夹具，不能写成已经执行了 100,000 次 Agent/模型任务。

## read-only

只发送 GET。验证初始总数恰为 100,000 条终态 seed；读取 hot workspace 的首 25 条、服务实际返回 cursor 的第二页、按已知 seed 在 hot 90,000 条记录中 80% 处构造边界的深页，并逐 ID/问题/状态/模式/资料版本核对。summary 不含 snapshot/result/error-result/execution-usage 大 JSON。将同一 cursor 用于另一 workspace 必须返回 400。按 ID 读取完整报告，核对保存的合成正文及已知零用量。最后确认总数未变。此 action 不提供新增性能分位数或新的冷缓存对照。

## mixed

只在 ABBA 测量结束且无其他基准并行时执行。前置要求：100,000 条历史均已完成；JVM dispatch 开启、worker 数为 2；新的 `controlled_admission_worker.py` 进程，固定等待 400 ms 后运行真实 BM25/extractive，started/active/provider_calls 都为 0，worker 源码 SHA 与冻结文件一致。受控 worker 禁止 provider 配置，任务固定 `bm25` + `extractive`，不允许改为模型/向量路径。

创建一个独立 workspace 和一份 `runtime_sentinel` 合成资料，不向 hot 历史 workspace 插入新任务。随后同时启动 4 个提交协程完成 **30 个唯一请求/幂等键**，以及 8 个 reader 各进行 20 次 hot summary GET（每页 25，合计 160 次）。这是有限数量、闭环并发工作，不是开放到达率或真实生产容量实验。配置上限不等于实际 HTTP 并发，实际峰值另行记录。

30 个 POST 各发送一次，不自动重试；传输失败的受理结果保持未确认。本实验验证并发读取期间的背压拒绝与任务完成，30 是尝试数，不是执行数。严格验收要求每次尝试都有明确的 202 受理或精确容量拒绝契约：仅允许 `(429, queue_full)` 与 `(503, global_queue_full)`，且 `retryable=true`、响应头 `Retry-After: 1`、正文 `retry_after_seconds=1`；普通 503、`admission_unavailable`、`database_unavailable`、`rate_limited` 不算正常容量拒绝。至少 1 次受理、受理 ID 无重复，全部 160 次读取内容正确；完整保留容量拒绝率，不把它写成 30 次成功执行。受理任务通过 GET 轮询终态，总轮询预算 30 秒，从提交与 reader 工作结束后开始计算。预算取消剩余 GET，已收到记录保留。该预算不包含前置设置、工作阶段、最后诊断，HTTPX 每个 I/O 阶段超时 5 秒，不声称整个 action 有 30 秒墙钟上限。

逐任务要求 `SUCCEEDED`、cache miss、当前执行的模型/嵌入调用和 token 都明确为零，未知用量不能当零。worker started/completed 数与受理数相等，failed/active/provider_calls 为零、最大执行并发不超过 2。最后任务总数应等于 **100,000 合成历史 + 本次已受理真实离线任务**，不能混称为全部真实执行量。控制 worker 原始日志由 root 另行封存，worker 聚合计数不冒充按 ID 执行轨迹。

## 留证和判断边界

每个 HTTP 请求保存请求标识、响应状态、耗时、字节数/SHA 和合成 JSON 正文，连续写入 gzip 日志；每响应正文预算 128 KiB，超额明确失败并保留大小/哈希，不静默判成功。目录总量不截断，以免丢证据；压缩字节数写入汇总。另保存全部受理 ID、观测到的终态、未观测终态 ID、连接池 active/pending/max/acquire/timeout 的前后原始指标。失败诊断不能抹去已发生的请求。

连接池指标是整个 JVM 数据源活动，包含 reader 和观察器；前后值不能证明所有瞬间没有饱和，timer 的 COUNT/TOTAL_TIME 不能推导 p95。脚本不启动或停止服务，不接触 provider，也不删除历史或 mixed workspace。意外错误、非容量契约拒绝、超时和未完成任务使相应检查失败，原始结果永久保留；后续修复另设实验身份。
