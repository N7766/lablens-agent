# Round16 超时排查：已经确认什么

本轮没有调用付费模型。先保留 Round15 的失败原件，再用真实本地 HTTP 连接复现等待响应头、响应体停顿和持续小块响应。诊断确认了一个运行时限制：**当前 HTTPX read timeout 不是绝对总截止时间；Agent 的总预算和取消检查是在阻塞模型调用返回后继续执行。** 本轮将它记录为待修复项，没有改大超时、伪造成功报告或重复扣费重试。

## 历史失败的解释范围

证据：[Round15 原始任务](../conversation-round15/run/agent-final.json)。

| 已确认 | 对应证据 |
|---|---|
| 第一轮模型请求完成，耗时 25.374 s | `request_finished/r1.duration_ms=25374.305`；HTTP 200；usage=2410 tokens |
| 模型已选检索工具，本地检索不是这次 35 s 超时的主要耗时点 | `stage_finished/search_workspace.duration_ms=14.931`；检索原文包含 SFT 基线 |
| 第二轮分配约 34.598 s 是为最终审核预留 20 s 后的调用额度 | `r2.timeout_ms=34597.896`；开始时 `remaining_ms=54597.582`；配置 `review_reserve_seconds=20` |
| 第二轮读超时，总 80 s 尚未用完 | `r2.duration_ms=35045.431`、`ReadTimeout`；结束时 `remaining_ms=19552.480`；`budget_exhausted=false` |
| 最终没有可评价的语义报告 | 任务 `FAILED/agent_timeout`，`result=null`；不能记为回答正确，也不能记为错误拒答 |

**仍未知**：第二轮是供应商队列、推理计算、网络等待还是已发响应头后的停顿；供应商是否受理、完成以及计费。`response_received=false` 在当前 telemetry 中只表示 HTTPX 没有返回完整响应对象。已知第一轮 tokens 不等于完整任务消费，第二轮未知不能按 0 计费。

## 可控复现

协议在运行前固定：[协议](timeout-protocol.md)。完整事件、软件版本及源码 SHA：[v2 原始事件](timeout-run-v2/raw-results.json)、[源码固定](timeout-run-v2/source-freeze.json)、[判定结果](timeout-run-v2/summary.json)。每个场景只请求一次，均为本地合成服务；以下时间是这一次机器上的工程观察，不是服务商性能指标。

| 场景 | 配置 | 实测 | 结论 |
|---|---|---|---|
| 等待头 | 450 ms 才发头；read 200 ms | 201.860 ms 后 ReadTimeout；未观察到头/体 | 超时可发生在头之前 |
| 体停顿 | 立即发头及首块，再停 450 ms；read 200 ms | 202.391 ms 后 ReadTimeout；已观察到头和首块 | 同样的 ReadTimeout 也可发生在体中 |
| 持续小块 | 8 块、间隔 80 ms；read 200 ms | 完整接收 587.385 ms | read timeout 限制连续等待，不限制总耗时 |
| 现有 HTTPModel | 同样持续小块；分配 200 ms | 615.227 ms 后正常返回 | 当前应用确实受此语义影响 |
| 现有完整 Agent | 总预算 1 s；16 块、间隔 100 ms | 1615.963 ms 后 `agent_timeout`；1 次模型、0 次工具 | 终止后不会继续发工具，但 1 s 不是在途调用的硬墙钟上限 |
| 在途协作取消 | 103.434 ms 发取消 | 当时 `stopped=false`；1608.643 ms 后 `agent_cancelled`，随后 `stopped=true` | 取消确认等待在途 HTTP 结束；不能宣称立即打断供应商 |

两个现有 HTTPModel 读超时场景都保留 `response_received=false` 和 `usage_known=false`，尽管其中一个本地服务已发头和首块。独立流式观察器能区分这两种传输情形，但**这些新观察并未补写到 Round15 历史日志，也未上线为生产 telemetry**。首块是应用层收到的 chunk，不是模型 TTFT；服务端排队与计算需要供应商自身指标才能继续区分。

Agent 完整接收了合成响应，因此这两个 Agent 场景保留的 7 tokens 只是固定测试夹具，并非模型消费。取消路径优先结束为 `agent_cancelled`，其 `budget_exhausted=false` 不能解释为墙钟没有超过 1 s。

## 本轮遇到的问题和处理

第一次运行中，6 个 HTTP 场景已经完成；2 个 Agent 场景因为并行加入的默认开启服务身份检查，在发送请求前返回 `unavailable`。保留了[首次原件](timeout-run/raw-results.json)、[失败说明](timeout-run/summary.json)、当时脚本和协议。补充一个仅供探针使用的固定合成服务身份后，在新目录运行 v2，8 个场景全部满足预先说明的诊断判定。这里的“通过”表示复现和记录正确，不表示时间预算缺陷已修复。

定向自动检查：`tests/test_timeout_probe_round16.py` 4 项通过，覆盖真实连接场景、观测顺序、日志不含凭据/正文和禁止覆盖证据。仍有环境既存的 Starlette/httpx 弃用警告。

## 下一步最小修复

在新一轮为所有模型请求实现**可中断的在途执行和绝对截止时间**，明确区分 read inactivity、总体 deadline、用户取消三种终止原因。增加持续小块、头等待、体停顿、取消竞态和异常释放连接的验收；保留已完成 usage，未知 usage 不填 0，停止后禁止自动补发。响应头/首块的脱敏事件可以增加可诊断性，但单靠这些事件仍不能确认供应商内部排队或计算。

本轮没有把新机制嵌入生产客户端，以避免将“诊断完成”写成“运行时修复完成”。后续修复须把本探针记录的当前限制作为修复前证据，再生成新目录对照结果。[HTTPX 官方 timeout 语义](https://www.python-httpx.org/advanced/timeouts/)、[HTTPcore 官方 trace 扩展](https://www.encode.io/httpcore/extensions/)。
