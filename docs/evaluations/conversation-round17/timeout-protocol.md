# Round17 本地 HTTP 截止时间与取消验收协议

先固定协议，再运行。保留 Round16 `timeout-run-v2` 原始失败；新结果写入新目录，已有目录一律拒绝覆盖。没有外部模型请求、重试或模型质量评分。

1. 重用 Round16 loopback 服务的 8 块、80 ms 间隔响应。结构化选择和普通 completion 各分配 200 ms，均应以 `HTTPDeadlineExceeded / timeout_phase=deadline` 结束；单次请求、完整响应未返回、usage 未知。允许本地调度和关闭开销，验收墙钟上限 450 ms。
2. 等待响应头与收到首块后停顿，各设 read inactivity 150 ms、绝对截止时间 600 ms。两者应是 `ReadTimeout / read`，而不是伪造为 absolute deadline。完整 Response 均未返回；首块不等于完整模型输出。
3. 原完整 Agent 连续 16 块、100 ms 间隔，总预算 1 s；应为 `agent_timeout`，1 次模型、0 次工具，墙钟不超过 1300 ms。
4. 同一 Agent 在首块后 50 ms 请求取消，应为 `agent_cancelled`；请求取消时 stopped=false，所有本地执行栈退出之后才 stopped=true。请求取消至调用结束不超过 300 ms；只请求一次、usage 未知、0 次工具。
5. 正常 8 块响应在 1 s 预算内应完整返回，保留合成 usage 7 tokens；不以一律拒绝来伪造超时通过。
6. 两个取消竞态补充单元测试：注册前取消不得漏掉、注册解除后取消不得影响下一个请求。子执行 lease 未退出时不确认停止。真实连接取消后服务端应观察关闭；异步 transport 退出前须确认 `aclose` 已执行，无遗留请求任务。

前 7 个 HTTP/Agent 场景各运行一次。原始事件记录应用计时、服务端发包/关闭事件、runtime telemetry、版本与源码 SHA；不保存请求/响应正文或 HTTP 认证头。合成 usage 与真实账单分开。允许一般操作系统调度误差；这是本地工程回归，不是线上 SLO、模型 TTFT 或供应商队列观测。

实现边界：`asyncio` 截止时间取消正在等待的 HTTP 任务；客户端、stream 和事件循环清理完成才返回，不使用后台遗留请求线程。操作系统 DNS 解析、TLS 初始化和清理可能包含不可抢占操作，取消时等待其退出，因此不能宣称任意 OS 故障下严格实时上限。关闭本地 socket 不证明上游推理或计费停止，未知用量继续记为未知。

依据：[HTTPX 阶段超时语义](https://www.python-httpx.org/advanced/timeouts/)、[Python 3.11 asyncio 截止时间与取消](https://docs.python.org/3.11/library/asyncio-task.html)、[Runner 资源清理](https://docs.python.org/3.11/library/asyncio-runner.html)。

运行：`.venv/bin/python scripts/verify_http_deadlines_round17.py --output docs/evaluations/conversation-round17/timeout-run`
