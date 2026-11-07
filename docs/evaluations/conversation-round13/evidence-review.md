# Round 13 持续负载账本只读复核

复核日期：2026-11-08（北京时间）。实验 `7527ce32-272c-4a3c-b435-e92698753738`，冻结源码提交 `55cf2be0c9c9c44b5f7590fb83a8a35c752fe0aa`。

**账本相互一致，但本次严格负载验收未通过。** 原因是一个预定请求在发送前超过调度迟到门槛；没有修改门槛、补发该请求、覆盖失败或执行第二次正式负载。原 [核心结果](load-candidate/summary.json) 为 17/18 项通过，`passed=false`；[资源结果](load-candidate/resource-summary.json) 为 15/16 项通过，失败项继承核心验收失败。

复核者是参与 Python 传输诊断开发的另一名 AI agent；本次独立编写账本核对逻辑，没有调用负载运行器的汇总函数。身份为 **AI 开发只读复核**，不是独立人工评测，也不是模型语义质量评测。未读取本轮模型新题或答案标准，未调用模型，未改变原始证据。

## 每个请求与任务的对应关系

从 [requests](load-candidate/requests.jsonl.gz)、[HTTP observations](load-candidate/observations.jsonl.gz)、[tasks](load-candidate/tasks.jsonl.gz) 和 [worker](load-candidate/worker.jsonl.gz) 解压后重新计算，而非只核对汇总数量。

| 阶段 | 预定槽位 | 已发送 | 接受 | 明确拒绝 | 未发送 |
| --- | ---: | ---: | ---: | ---: | ---: |
| 低负载，0–180 秒 | 360 | 360 | 360 | 0 | 0 |
| 过载，180–360 秒 | 3,600 | 3,599 | 876 | 2,723 | 1 |
| 恢复，360–540 秒 | 360 | 360 | 359 | 1 | 0 |
| 合计 | 4,320 | 4,319 | 1,595 | 2,724 | 1 |

重新生成固定的 2/20/2 req/s、各 180 秒日程后，4,320 个 `sequence` 各出现一次，阶段与每个预定时间完全相符。每个已发送槽位均能以唯一幂等键匹配一条实际 POST 观察记录，HTTP 状态与响应正文逐条相同；没有重复 POST、传输错误或非预期 HTTP。

1,595 个接受任务的 ID 多重集合与以下三组完全相等，且每个 ID 在每组都恰好出现一次：

- 逐任务 GET 终态记录；全部 HTTP 200、`SUCCEEDED`、无错误码、无缓存命中。
- worker `started` 记录。
- worker `completed` 记录；不存在额外或缺失的任务事件。

每个 ID 另行匹配提交序号、workspace、幂等键、请求哈希、请求内容与资料 snapshot；worker 开始和结束的 snapshot 与最终检索结果一致，均使用固定 400 ms 等待。开始时间早于结束时间。按 worker 原始事件时间重新计算并发，最大同时执行 2 个，最后归零；这项是 worker 事件账本的重建结果，不是实际 HTTP 提交并发。

每个任务在 `result.usage`、`execution_usage` 和 worker 完成事件三处都记录整数零的模型调用、embedding 请求/输入/token 与生成 token，且 `provider_usage_unknown=false`。该结论只适用于本次 BM25/extractive 负载，不包含分开的模型 probe。模型字段为零不能替代对真实模型生成质量的评估。

2,724 次拒绝逐条均为 HTTP 503、`global_queue_full`，包含 `Retry-After: 1`、`retryable=true` 和 `retry_after_seconds=1`，正文没有已接受任务 ID。恢复阶段初始仍有 1 次容量拒绝；**恢复最后 90 秒的 180 个槽位全部接受**，没有拒绝或漏发，不能简写为整个恢复阶段零拒绝。

## 唯一漏发证明了什么

[requests](load-candidate/requests.jsonl.gz) 中保留如下原始记录：

```json
{"sequence":3237,"stage":"over","scheduled_offset_s":323.8,"outcome":"unsent","send_offset_s":null,"lateness_ms":111.089,"unsent_reason":"scheduler_late"}
```

[冻结 manifest](load-candidate/manifest.json) 的 `max_lateness_ms` 为 100。该槽位的幂等键 `admission-7527ce32-272c-4a3c-b435-e92698753738-3237` 在 HTTP POST 观察账本中不存在；全部已接受任务也已与真实 POST 对齐。因此证据支持：**发送器在发送前观测到 111.089 ms 调度迟到，触发既定 100 ms 门槛，记录未发送且没有补发。**

这不是一条服务器拒绝或服务器 HTTP 超时记录。现有材料没有该时段的 Python 事件循环、操作系统调度或进程暂停剖析，不能把迟到归因于 Java、Redis、MySQL、GC、网络或 CPU 中的任一项。发送器与服务在同一台机器上运行，服务负载是否间接影响了调度也没有被本次证据排除。后续若调查原因，应单设新协议；不能将本次漏发改成成功，或仅放宽门槛重算通过率。

## 冻结与公开证据核对

核对 [manifest](load-candidate/manifest.json) 与 [summary](load-candidate/summary.json) 的前后来源哈希：9 项全部一致。其中 8 份源码/协议文件也分别与当前文件及冻结提交中的 Git blob 相符；实际测试 jar 的 SHA-256 为 `dbf9a8bfe0d033c4afdf5d3ea6d7af05994c920936dbf409dc36c860f9b70992`，与冻结记录一致。

公开的 requests、observations、tasks、samples、worker 五份 gzip 解压后，与本地原始 JSONL 逐字节相同；公开 manifest、核心 summary、资源 summary 和 report 也与原始副本逐字节一致。复核过程没有编辑这些文件。

本次新增的 16 项独立账本核对均一致，含逐 ID 关联、原始 POST、恢复尾段、来源与压缩副本检查。**这是证据一致性结论，不能替换原先 18 项严格负载验收的失败结论。** 本报告不另行解释资源瓶颈；资源采样与指标语义由单独资源诊断复核负责。9 分钟、单机、固定等待的合成工作负载也不能证明长期稳定性、生产 SLA 或模型服务容量。
