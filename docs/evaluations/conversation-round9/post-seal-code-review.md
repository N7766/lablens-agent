# Round 9 封存后诊断补丁：独立代码复查

**本次复查未发现尚未处理的阻断项。** 检查范围为 `lablens/workspace_agent.py`、`lablens/rag.py`、`tests/test_round9_review_failure_audit.py` 的封存后差异，并只读检查相关 JVM 失败轨迹透传和 Redis 使用路径。评阅者没有修改根源码或旧评测文件。

这份补丁发生在真实采集候选 `77b7a550e6a3390a2fdac6c5136a792a126d5631` 之后。它**不能恢复本次 Cedar/Harbor 已丢失的 review 响应，不能改变原评分，也没有追加真实模型调用**。原 raw、gold、失败记录与分母保持不变。只有受控本地响应验证了新诊断行为，不构成新候选的在线质量或部署验收。

## 复查时的源文件身份

| 文件 | SHA-256 |
| --- | --- |
| `lablens/workspace_agent.py` | `14d31e7ca987f33e205d1d97fefedfaaab8849587810df0b00a35c5e3ee9b060` |
| `lablens/rag.py` | `10aba8c285f938bcdc2357e9950bf4a9883be91b8043d79c231ea687a8e9d3b5` |
| `tests/test_round9_review_failure_audit.py` | `428a5b53f4d5818baf5cfff6c5f5654f3ea27ccd77abb037369ad599446873de` |

## 边界检查

1. **有界保存。** observer 只接收已解析 provider envelope 中的 finish_reason 和 message.content。字符串正文保存完整 SHA-256 与字节长度，但只保留至多 16,384 个 UTF-8 bytes 的前缀；多字节截断后忽略不完整尾字符，原长度及 truncated 单独记录。finish_reason 限 80 字符。restore/validate 异常的 message 限 1,024 字符，另留 stage 和 exception_type；这里的字符上限不等同字节上限。
2. **观察不是通过。** 每份 observation 固定 `accepted_report=false`，保存在失败的 `tool_rejected.output.phase=review` 中。恢复 ID 分区、引用解析、`enforce_review_checks` 和 `validate_assessments` 的顺序不变，异常继续返回失败及空 answer；未新增返回草稿、默认保留、重试或重新进入 decision 循环的路径。
3. **未知不伪造。** HTTP 超时或 envelope 没有有效 message.content 时 observer 不生成内容哈希、长度或前缀，记录 `content_observed=false`。已计数的尝试和已知/未知 usage 沿原逻辑保存，不把无答案当零调用。
4. **请求头不进入新诊断。** observer 没有 config、HTTP request 或 headers 参数，只挑选 finish_reason/content。新增 error 已改为固定文案，不复制任意 `RAGError` 字符串；validation_detail 仅由 restore/validate 分支建立。受控 Authorization sentinel 未出现在最终 result。这里证明的是不自动带入 transport 凭证；模型正文仍是工作区/模型数据，16 KiB 截断不是通用脱敏器。
5. **不作为模型上下文或缓存输入。** review_observation 是单次局部字典，失败后追加事件即停止，不送入后续 messages。RedisSupport 的 get/put 均在非 extractive 模式直接返回；本补丁保持 Agent 模式不读写结果缓存，也没有对失败结果写缓存的路径。Agent 版本标签仍为 v10，实际补丁身份由 implementation SHA 与 Git 提交区分。
6. **跨服务可保留。** JVM `safeAgentTrace` 已允许 `tool_rejected.output`，新增 Java 控制明确断言 bounded observation 能透传，而不会变成 answer 或 generation_review。此处已读测试和运行路径；Java 测试实际执行结果由 root 的验证记录负责，不把静态阅读写成运行通过。

## 发现与修复

初次独立反例向 `_generation_json` 输入 message 为 null/string/list。新增 observer 原先直接执行 `choice['message'].get(...)`，导致 AttributeError 逃出 RAGError 路径；这不会发布错误答案，但可能把预期的可审计失败变成未处理异常，丢失已知 usage 的正常错误包装。

root 随后添加 choice/message 必须为 dict 的检查，异常进入原有 ValueError → RAGError 转换，并补入 message null/string/list 与 choice null 四个控制。独立重放三项 message 反例均变为 `invalid_citations` 的 RAGError，且没有伪造观察内容。

root 的全量回归还暴露任意异常字符串不能写入公开轨迹的问题；最终补丁改用固定 error 文案。独立复查重新运行旧“private provider error 不进入结果”和遥测异常隐私控制，均通过。保留这一过程是为了说明诊断增强也可能引入异常/隐私回归，不能只增加日志而不验证错误路径。

## 本次独立验证

- `tests/test_round9_review_failure_audit.py` 与 `tests/test_round9_review_delta.py`：**57 passed**（11 诊断 + 46 原协议控制）。
- 既有 review 失败不重入及 request error 细节/凭证不进 trace：**4 passed**。
- 只读临时脚本模拟真实 `_post → httpx.post` 的 review timeout：2 decision + 1 review，恰 3 次尝试；空 answer、provider_usage_unknown=true、content_observed=false，Authorization sentinel 不在 result。仅本地 loopback 检索和 mock provider，没有外部模型请求。
- 同时出现已有 Starlette/httpx deprecation warning，不影响上述通过结果；未把这些控制扩展解释为生产压力测试、模型兼容性或答案质量通过。

原始缺失的失败正文仍然缺失。新的 observer 只有在未来请求实际经过这些已修代码时才会产生诊断，本次既有的两组评分必须继续关联采集候选和旧 raw，不能归到此补丁名下。
