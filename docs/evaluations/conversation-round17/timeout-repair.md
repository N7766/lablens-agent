# Round17：绝对截止时间与在途取消修复

本地真实 HTTP 验收 **7/7 通过，外部模型调用 0 次**。这里验证执行边界和错误处理，不评价模型回答质量或生产吞吐。修复前原件仍在 [Round16 timeout-run-v2](../conversation-round16/timeout-run-v2/raw-results.json)，本轮 [协议](timeout-protocol.md)、[原始事件](timeout-run/raw-results.json)、[源码与版本](timeout-run/source-freeze.json)、[验收判定](timeout-run/summary.json) 分开保留；运行期间源码无变化。

| 场景 | 修复前保留证据 | 本轮实测 |
| --- | --- | --- |
| 结构化模型调用，分配 200 ms，8 块/80 ms | 615.227 ms 后正常返回 | 203.935 ms，以 `HTTPDeadlineExceeded` 终止，usage 未知 |
| 普通 completion，同样慢速小块 | 本轮增加的覆盖 | 211.380 ms，以 absolute deadline 终止 |
| 等待响应头，read 150 ms、总额度 600 ms | 原诊断用 read 200 ms；不能直接比较耗时优劣 | 184.529 ms，`ReadTimeout/read` |
| 首块后停顿，read 150 ms、总额度 600 ms | 同上 | 161.533 ms，`ReadTimeout/read` |
| Agent 总预算 1 s，16 块/100 ms | 1615.963 ms 后才报告超时 | 668.440 ms，`agent_timeout`，1 次模型、0 次工具 |
| 在首块后 50 ms 取消 Agent | 取消后仍等待阻塞调用返回，约 1.609 s 才退出 | 调用共 67.318 ms；取消请求至退出 **0.799 ms**；1 次模型、0 次工具 |
| 正常 8 块响应，额度 1 s | 合成对照 | 607.907 ms 正常返回，保留合成 usage 7 tokens |

Agent 的 1 s 是整体预算，首个 decision 还要扣除最终 review 的预留额度，实际 HTTP 分配约 2/3 s。因此 668 ms 结束不表示整个 1 s 额度被耗尽，也不是把 1 s 任意改成 668 ms。每个场景只发送一次请求，没有为成功而重试。计时为这一台机器的一次工程观察，不应写为供应商延迟优化百分比。

修复把同步 HTTP 包装改成当前请求线程内运行的 `asyncio.Runner + HTTPX AsyncClient`。HTTPX 的阶段 timeout 仍然控制连接/读写停顿；另有 monotonic 截止时间控制连续来数据的请求。[HTTPX timeout 语义](https://www.python-httpx.org/advanced/timeouts/)与 [asyncio timeout/cancellation](https://docs.python.org/3.11/library/asyncio-task.html)说明了两者的差别。新的 telemetry 分开保存 `deadline`、`read` 与 `cancelled`，保留“完整 Response 未返回”的观察边界，不将响应头、首块或 socket 关闭推断成供应商已接受、停止推理或停止计费。

取消订阅与注册共用执行记录上的锁，避免启动竞态漏掉取消。取消通知仅安排当前异步 HTTP task 停止，没有后台遗留请求线程，也没有自动重发。最终连接关闭在 timer 与取消回调作用域之外；回调在事件循环内再次检查 active 标记，防止已取消订阅、但迟到的回调打断关闭过程。MySQL 取消意图和 Python root/child lease 继续决定确认语义：提出取消时 `stopped=false`，所有本地执行栈退出后才 `true`。

本轮遇到并处理了三个容易掩盖的问题：

- 在途取消以前不会穿过 provider 传输层；改为立即取消后，既有异常分支可能把前面已知的 40 tokens 当成全部用量。现在只有本次已完整记录 usage 才保留已知值，其余聚合保持未知；父 Agent 没收到子检索的用量，不能拿测试服务知道的数字补写。
- 取消和 deadline 可能先后到达，第二个信号会打断 `aclose`；迟到的回调即使已解除订阅也可能仍在队列里。门控关闭测试覆盖两种竞态，并断言关闭完成以前不确认停止。
- 老评测脚本曾通过全局 `httpx.post` 拦截调用。换传输后必须同步迁移预算、次数、冻结校验和原始响应保存钩子到 `rag.bounded_post`，否则审计层可能被绕过。Round10/13/14 runner 的拒绝额外调用、保留失败与防覆盖测试已回归。

传输专项 **12 项通过**，覆盖真实慢连接、成功对照、读停顿与 deadline 分类、取消注册/解除订阅竞态、关闭过程、OS executor 清理和禁止覆盖证据；迁移相关的较大回归在增加最后一项竞态用例前为 **469 项通过**。项目全量验收另见本轮总报告，不混算。

仍然存在明确边界：OS DNS resolver、TLS 初始化或关闭资源可能包含不可抢占操作。`Runner` 会等待这些资源清理完成，不伪造快速停止，也不遗留请求线程。因此不能宣称任意 OS 故障下严格实时截止；专门的模拟 OS executor 测试确认超过 deadline 时 lease 仍然有效，直到清理结束。这次结论是修复了已复现的持续响应体及本地在途取消问题，不是提供操作系统级硬实时保证。
