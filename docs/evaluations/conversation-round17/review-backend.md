# Round 17 Python 后端交叉检查

审查者：负责本轮 Java 记忆实现的 AI 子代理，未实现下列 Python 记忆及 HTTP 传输改动。这是工程代码交叉检查，不是人工独立出题、盲测或模型能力评估。审查日期：2026-11-08。

## 范围与结论

检查 `lablens/memory_context.py`、`workspace_agent.py`、`rag_api.py`、`bounded_http.py`、`cancellation.py`、`runtime_telemetry.py`，并追踪 `model.py`、`rag.py` 的调用、用量与错误分支。

在当前接口约束与单进程执行模型下，未发现尚待修复的阻断问题。下列判断来自代码、具体测试与本地日志；不推断线上吞吐或供应商内部行为。

| 检查点 | 对应证据 | 判断与边界 |
| --- | --- | --- |
| 来源与作用域 | `MemoryContext.validate_snapshot`；`test_memory_rejects_invalid_or_cross_scope_inputs_before_execution` 的 25 类输入；`test_hash_quote_and_admitted_snapshot_allow_unrelated_document_revision_changes` | 工作区、来源快照范围、当前提交文本 SHA 与精确引用匹配被检查。Python 文档快照不带版本字段，因此当前版本一致性仍由 Java 提交事务负责，不能称为 Python 独立认证历史资料。 |
| 到期跨越排队/重建边界 | `rag_api._answer` 的调用前检查；`WorkspaceAgent.run` 的执行前检查；`test_expired_memory_api_ends_before_provider_configuration`、`test_expiry_rechecked_at_execution_start_without_provider_call` | Pydantic 只校验形状与来源，动态到期在执行入口变成带来源、零调用用量的 `invalid_memory_context` 终态，避免重建请求时意外返回 500。到期策略为执行开始检查，不是运行过程中远程撤销。 |
| 记忆进入提示词的位置 | `WorkspaceAgent.run` 的 `untrusted_workspace_memory`；`MEMORY_INSTRUCTIONS`；`test_local_http_model_receives_untrusted_memory_retrieves_and_verifies_current_evidence` | 用户选定记忆进入普通用户上下文。它不直接增加已检索/已核验集合、证据目录或可用工具；最终审阅没有直接拷入记忆。固定响应服务验证了链路，不能证明真实模型总会抵抗语义诱导。 |
| 工具及上下文预算 | `test_memory_cannot_authorize_unsupported_tool_even_if_model_follows_injection`；`test_memory_counts_in_context_budget_and_cannot_be_silently_dropped`；`test_serialized_limit_counts_json_escaping` | 记忆不能增加 shell 等工具。序列化内容计入现有预算；过大时在调用前拒绝，不能悄悄删去已审批记忆。预算单位是保守字节估计，不是供应商 tokenizer 的精确 token。 |
| HTTP 慢响应绝对期限 | `bounded_http.post` 的 `deadline` 与 `asyncio.timeout`；`test_real_socket_deadlines_cancel_and_success` | HTTPX 阶段超时之外增加整次调用期限，持续滴流不能不断刷新读超时而无限延后。期限还包含创建循环/客户端的耗时预算；发请求前重新计算余额。 |
| 取消注册与关闭竞态 | `on_cancellation` 的注册/取消共用锁；传输回调一次投递、`active` 标识；`test_cancel_tears_down_async_stream_client_and_task_before_ack`、`test_late_snapshotted_cancel_cannot_interrupt_client_cleanup` | 取消仅要求停止，根/子执行退出前不报告 stopped。超时范围在最终客户端关闭前结束，迟到回调不能再次打断清理；没有抛出后继续运行的脱管请求线程。 |
| 操作系统清理边界 | `test_os_executor_cleanup_is_waited_for_not_falsely_acknowledged`；`bounded_http.py` 模块说明 | 同步等待清理和不可抢占解析器工作，可能超过配置期限。这里保证不虚报已退出，不保证所有 OS 故障下严格实时终止。 |
| 计费与终态 | `observe_request`、`rag._post`、Agent 的 `ExecutionCancelled`/`ModelError` 分支 | 完整响应前超时或取消保留用量未知，不自动重试。完整用量已记录后再取消可保留已知值。关闭本地套接字不能证明供应商停止计算或计费。 |

## 交叉检查中修正的接口问题

1. Java 默认 JSON 控制字符转义使用大写十六进制，Python 使用小写；相同记忆可能得到不同上下文 SHA。Java 规范序列化现在显式使用小写转义，新增中文、emoji、控制字符、换行、反斜线与引号样例。没有通过剥离来源字符规避问题。
2. `HTTPDeadlineExceeded` 已同时保留为 Java 允许的异常类别及 `deadline` 阶段，避免落库时丢失新的诊断分类。
3. 全量 Java 检查发现旧 Task 构造器默认 `memoryIds=null`、规范化后却变成空列表，导致记录等价性回归。未选择记忆时恢复旧表示，省略字段的请求 JSON 与旧路径保持一致。

## 已查看的验证记录

- `.local/round17/python-full.log`：2095 passed，1 条依赖弃用提示；本次交叉检查读取日志，未重复运行相同 Python 全量检查。
- `.local/round17/java-compat-deadline.log`：旧请求兼容及新期限诊断的定向检查通过。
- `.local/round17/java-full-v2.log`：212 discovered，210 passed，2 条条件式 broker 重启检查 skipped；`verify` 与打包成功。初次失败日志 `java-full.log` 保留。

以上均为本地工程验证。没有真实供应商请求、真实流量、人工独立语义评测或私有 GPU 部署结论。
