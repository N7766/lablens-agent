# 会话上下文预算：真实 HTTP 失败链路

运行时间：2026-10-28T18:01:45.366767+00:00 至 2026-10-28T18:01:48.982984+00:00（UTC）。

## 实测结论

真实 JVM → 真实 Python Workspace Agent → MySQL 持久化链路通过 **26/26 项检查**。模型预算在第一个模型调用前拒绝请求；并非用假的 Python worker 返回预制错误。

- `LABLENS_CONTEXT_WINDOW_TOKENS=4096`；实际估计输入 9678 units，计入输出预留 2600、安全余量 1024 后为 13302 units。
- Task 明确以 `FAILED / context_budget_exceeded` 结束，Agent 状态相同；未退化为通用 `worker_http_error`。
- 模型调用、embedding 调用、token 使用均为 0，`provider_usage_unknown=false`。
- 模型与 embedding 地址都被固定到独立的 loopback sentinel；该监听器收到 **0 个请求**。没有外部模型调用。
- 保留会话 ID、revision=1、用户固定状态、资料快照、预算判断和停止轨迹。
- 新的 GET 读取、会话历史、SQL 原始 JSON 和同幂等键重放均指向同一失败任务；同幂等键不会重发模型调用。
- 运行期间受检源码和 JAR 哈希保持不变。测试自己的 JVM 8084、Python 8768、Redis 6392、sentinel 8770 已全部停止；未修改主服务和其他 QA 服务。

## 原始证据

- `summary.json`：26 项断言、源码/JAR SHA-256、数据库行、最终任务、刷新和重放结果、进程清理记录。
- `http-observations.json`：按顺序记录真实 HTTP 请求与响应；仅使用合成资料。
- `provider-events.json`：空数组，证明 sentinel 未收到模型或 embedding 请求。
- 数据库保留：`lablens_conversation_budget_6ab66f31`，任务 ID `fa6be4ce-8c1d-44a1-8d20-735a6d6418d5`。

## 复现

先构建当前 JVM JAR，并确保 MySQL 正常运行、配置的本机管理 socket 可用。运行：

```sh
.venv/bin/python scripts/evaluate_conversation_budget.py --output docs/evaluations/conversation-round1/budget-fault-repeat
```

脚本读取本地服务凭证但不输出凭证，使用新的隔离数据库，并拒绝复用主服务/父任务 QA 端口。已存在的证据不会被覆盖。

## 解释边界

这是人为设置极小窗口的确定性故障测试，不是正常上下文窗口的模型回答质量评测。UTF-8 字节预算是保守估计，不是供应商 tokenizer 的精确计数。刷新及 SQL 验证证明本轮任务持久化，不代表已经实现中断续跑或长期记忆。
