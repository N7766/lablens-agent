# Round 12 Redis 故障证据复核

2026-11-08。此文为 **AI 开发复核**：复核者也参与了故障运行器开发，不是未参与开发的人工独立评测。本次只读取封存产物、源码和哈希，没有再次发送业务 HTTP、停止进程或重跑实验。

结论：成功目录支持本机隔离 Redis 的真实进程停止、四个预声明 HTTP 业务操作和恢复验收。首次预检失败仍单独保留。成功目录的 41 个检查条目全部为真，但其中包括重复 GET 成功检查，**不能写成 41 种独立故障场景**。

## 来源与封存校验

- [成功 raw seal](redis-outage/raw-seal.json) 和 [首次预检失败 raw seal](redis-outage-preflight-failed/raw-seal.json) 各列 4 个文件。逐文件 SHA-256 和完整文件集合均匹配；未用新结果替换原目录。
- 两份 [manifest](redis-outage/manifest.json) 的运行器哈希相同，并与当前 `scripts/evaluate_admission_redis_outage.py` 匹配：`98f063daab56ddf852ece1c1670445ff6b229be56018c32b57c5f314e5f80ed0`。
- 解压公开的 [candidate observations](load-candidate/observations.jsonl.gz)，其原始内容 SHA-256 为 `1c43dfb34f0637bee5033a96024fca9a15a77acd4e682a59b06c6a064b3f46af`，与两个故障 manifest 中的来源记录一致。幂等重放的 URL、body、key 和 task ID 均与该原始成功请求逐项相同。
- `source-freeze.json` 中列出的源码/协议与当前文件逐项匹配。worker 状态中的实现哈希与当前受控 worker 源码相符，RAG 哈希也与本轮冻结记录相符。验收对象是使用固定 400 ms 延迟的 BM25/extractive 受控 worker，不是模型推理服务。

## 实际 HTTP 对账

[成功 observations](redis-outage/observations.jsonl) 共 **31 条**：27 次 GET、恰好 4 次 POST。没有遗漏失败后追加的 POST，也没有对这四个操作自动重试。

| HTTP 记录 sequence | 操作 | 返回与后续结果 |
| --- | --- | --- |
| 6 | Redis 不可用时提交新的 `mode=agent, strategy=bm25, approved_revision=1` | HTTP 503、`admission_unavailable`、`retryable=true`、`Retry-After: 1`、`retry_after_seconds=1` |
| 9 | 使用原成功请求的相同 URL、body 和 Idempotency-Key | HTTP 200、`reused=true`；仍返回 `544e07df-188e-43c2-8746-545127804a4b` |
| 12 | Redis 不可用时提交新的 BM25/extractive 请求 | HTTP 202，随后任务 `d8a54eef-858a-4c49-a931-3fe460a9964e` 为 SUCCEEDED |
| 24 | Redis 恢复后提交另一条新的 BM25/extractive 请求 | HTTP 202，随后任务 `ceb2290d-ef91-467a-b2a6-2ef7a12cf253` 为 SUCCEEDED |

以下计数均由原始 HTTP 状态响应和 worker 响应重新核对，而非只相信最终 passed 标志：

| 检查点 | MySQL 成功任务 / 活跃任务 | worker started / completed | Redis available |
| --- | --- | --- | --- |
| 故障前 | 539 / 0 | 539 / 539 | true |
| provider 请求拒绝后 | 539 / 0 | 539 / 539 | false |
| 幂等重放后 | 539 / 0 | 539 / 539 | false |
| 故障期间离线任务完成后 | 540 / 0 | 540 / 540 | false |
| 恢复后的离线任务完成后 | 541 / 0 | 541 / 541 | true |

所有这些检查点的 worker `failed=0`、`provider_calls=0`。两条新离线任务均 `cache_hit=false`，持久 `execution_usage` 中 model_calls、embedding_requests、embedded_inputs 均为 0，`provider_usage_unknown=false`。因此本次新 provider 请求被拒绝时没有创建任务、没有进入 worker；重放没有重复执行；两条新离线任务各增加一次执行。不能把两条样本的 worker_ms 差异当作经过控制的性能优化收益。

## 真实进程停止与恢复

[process-events.jsonl](redis-outage/process-events.jsonl) 保留 8 条事件：两次身份检查、SIGTERM 请求、旧进程退出且端口关闭、重启请求、launcher PID、恢复后的身份检查、restored。

- 原 PID **77462**，监听端口 **6383**；PID 文件、ps 和 loopback Redis INFO 的 process_id/tcp_port/config_file 对齐。
- 实际发送 SIGTERM 后，记录 `process_exited_and_port_closed`；这不是只关闭客户端连接，也不是用异常注入冒充 Redis 进程停机。
- 指定同一配置重新启动的 PID 为 **81684**，`pid_changed=true`，随后再次核验 INFO 身份；JVM 状态恢复 `available=true`，恢复后离线请求最终成功。
- Redis 的 ps 标题没有保留配置路径，记录明确为 `configuration_in_ps=false`；配置身份依据 INFO 中的 config_file 与显式绝对路径匹配，不能把它写成“ps 显示了配置路径”。

这些是验收时保存的进程观察，不能据此宣称该进程在读者查看文档时仍然运行。此演练只停止指定 6383 实例，不证明 Redis 集群切换、数据持久性、生产可用性或其他端口的故障恢复。

## 首次预检失败没有消失

[首次失败 observations](redis-outage-preflight-failed/observations.jsonl) 恰好 **4 次 GET、0 次 POST**；process-events 文件为空，summary 为 `phase=preflight, error_type=ValueError`、`restoration_required=false`、`passed=false`。因此该次执行没有进入停机阶段，也没有提交新任务。

[执行记录](design-and-execution.md) 说明原因：最初 PID 文件由启动器写入，Redis 配置未显式声明同一 pidfile；补齐该配置后另建目录重验。原始失败 summary 只保存通用 ValueError，没有保存异常原文或此前配置内容；该配置差异来自操作记录，不能说仅凭原始 HTTP 就重建了缺失配置。两次运行器 SHA 相同，支持未通过修改运行器放宽预检来取得通过结果。

本轮开发记录中两个 Python 运行器合计 **29 项单测**，其中故障运行器的 21 项 fake-only 测试覆盖身份拒绝、预检失败、异常/中断恢复和恢复失败记录。单测验证运行器逻辑；本页的进程故障结论来自上面的真实封存事件，两者分开计数。
