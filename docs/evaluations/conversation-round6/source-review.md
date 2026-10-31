# Round 6 源码接入复核

日期：2026-10-31。范围：本轮答案质量模块、Agent 审阅接入和 Java 错误轨迹保存。**这是功能与回退复核，不是安全审计，也不是语义质量验收。** 复核者是实现 `evidence_scope.py` 的 AI 子代理；本次只读审阅 root 的接入改动，执行本地受控测试并写本文档，没有修改生产代码，没有调用外部模型。

本次审阅实际发现两个接入问题，已看到 root 修正源码并补入针对性测试。Python 控制回归已通过；本文首次写入时，修正后的 Java JAR 重建和跨服务故障复跑尚待 root 完成，不能把源码修正当作跨服务验收通过。

## 审阅中发现并处理的问题

### 1. 新增事件导致失败任务整段 Agent 轨迹丢失

`workspace_agent.py` 在审阅前新增 `review_coverage_slots`，但 Java `WorkerResults.safeAgentTrace` 最初只新增 `workspace-agent-v7` 版本，没有把该事件加入白名单。审阅开始后发生超时或取消时，错误结果包含这个事件，Java 会返回 null，丢掉整段 Agent trace。

这是实际影响可观测性的回退，而不是只缺一个显示字段。root 同时在真实 JVM/MySQL/Redis/Python 的本地故障检查中复现：[首次运行 summary](runtime-fault-first/summary.json) 为 **43/64**，其中成功任务通过，审阅超时任务丢失轨迹。首次原始结果保留在 `runtime-fault-first`，没有覆盖为成功结果。

已核对的修正：

- Java 明确允许 `review_coverage_slots`，仍保留既有 snapshot 身份、版本、事件、长度和时序字段检查。
- `v7FailureRetainsCoverageVisibilityAndReadGuardAlongsideUnknownProviderUsage` 将 read、coverage、review timeout 三种事件放入同一错误轨迹，并断言三个都保存、未知 usage 仍保留。
- Python 原有 deadline/cancellation 检查没有因修正被绕过。

首次检查数据为本地脚本供应商响应、6 秒任务预算、8 秒人为延迟、0 次外部模型调用。它验证传输和持久化，不证明真实模型速度或语义质量。修正后必须重建 JAR 再复跑，不能用旧 JAR 成功测试推断新源码生效。

### 2. 同轮“只看 X，不是 Y”仍强制补 Y 总判定

第一次只修“更正后只出现一个名字”的分支，以下请求仍产生两个总判定槽：

> 比较 Dune 和 Reed。更正，本轮只看 Dune，不是 Reed，给 Dune 的本次验收结论。

> Compare Dune and Reed. Correction: only Dune, not Reed, needs an overall verdict.

原因：更正子句只按句号/分号切分，`mentions` 同时包含 Dune 和 Reed，`len(mentions) == 1` 不成立。多加的 Reed 槽会让固定命题审阅重新回答用户已排除的对象。

已核对修正：候选名称继续复用 `named_user_pairs`，而缩小范围检查新增中英文逗号边界；上面两条复现现在都返回空自动补槽列表。root 加入中英文回归；最新 `tests/test_answer_coverage.py` **20 项通过**。此前两个控制也仍通过：单名更正不补双对象槽，普通 `Latency and Throughput` 不被当作候选。

这里的空列表只表示不强制添加双对象总判定，不是拒绝用户的单对象问题；原模型草稿仍按当前请求进入审阅。

## 其余接入核对

| 检查项 | 结论与证据边界 |
| --- | --- |
| 用户指代契约 | `answer_contract` 委托 `user_reference_contract`。只看用户消息，不从回答、固定记忆或检索材料创造候选。强制澄清时原工具限制仍在。 |
| 百分比计算 | 接入 `inspect_percentage_derivations` 的 derived 数量与 findings；**数字/百分比检查仍只针对 supported 肯定行**，避免把被否定或未知命题中的数字当观测。不是对所有 verdict 做相同数字检查。 |
| 未测量范围 | `measurement_scope_findings(raw)` 在数值循环后对完整 raw 运行，因此也覆盖 insufficient/contradicted 解释中的肯定历史断言。 |
| 引用范围 | arithmetic/scope 使用本条已选引文；证据 ID 在 `resolve_selections` 里映射，固定命题审阅后仍进行 exact source 验证，没有给补槽制造引文或事实。 |
| 总判定补槽 | 先验证原草稿，再添加明确请求的总判定问题占位；占位是 insufficient、无引文，并明确不是缺资料判定。复核仍只允许准备后的固定命题集合。 |
| 行数上限 | 选择 schema 原本允许最多 8 行，本轮把 prompt 的最多 4 行对齐到 8 行。新增槽导致超过 8 行会拒绝，不静默删掉指标；此情况仍走既有有限报告修正流程，不能称为任何输入都不会再规划。 |
| 模型调用与预算 | 未增加第二次审阅或供应商自动重试。默认仍为 75 秒总预算、6 次决策加 1 次审阅；新增本地准备步骤后，完整 payload 仍经过 context guard，再检查取消并绑定原 deadline。 |
| 取消与上下文 | 审阅前、供应商返回后、超时异常后原检查仍在；过大上下文在发送前明确结束。旧资料不能作为当前快照证据的检查未更改。 |
| 版本与缓存 | Python Agent 为 v7；Java 保留 v1–v6 兼容并接受 v7；缓存 namespace 更换为 round6-v7，避免默认复用旧 Agent 结果。配置显式覆盖 namespace 的行为仍以运行配置为准。 |
| 使用范围 | 新 guard 的生产执行路径是携带 scoped review contract 的 Agent 审阅。不能将本轮改动描述为所有无契约的普通 RAG 生成路径都已覆盖。 |

## 实际测试与时间边界

本次执行的组合：

```text
.venv/bin/pytest tests/test_answer_quality.py tests/test_answer_coverage.py \
  tests/test_evidence_scope.py tests/test_evidence_arithmetic.py \
  tests/test_workspace_agent.py tests/test_cancellation.py \
  tests/test_nested_cancellation.py tests/test_conversation.py \
  tests/test_visible_evidence.py tests/test_runtime_telemetry.py -q
```

结果为 **298 passed / 1 warning / 20.37 秒**。其中 telemetry 使用实际本地 HTTP 受控供应商，包含审阅超时、总 deadline、审阅中取消、usage 未知保留等；不接入外部模型。唯一 warning 为已有 Starlette/httpx 弃用提醒。

这组组合测试在本次审阅发现的更正分句与 Java 白名单修正前启动，因此不用于声称验证了后加的断言。修正后另跑 coverage：**20 passed / 1 warning / 0.65 秒**，并直接复现验证中英文“only X/not Y”都不加双对象槽。Java 新测试与新的 64 项跨服务检查由 root 在重建 JAR 后记录最终结果；本文未把首次 43/64 重写为通过。

## 剩余风险

更多行和补槽可能增加审阅输出与耗时；deadline 没变不代表超时问题已解决。名称识别、范围检查与算术均为有限语法，存在保守拒绝和未覆盖表达；完整回答是否覆盖指标、是否正确采用 AND/OR 条件、是否正确解释互相矛盾的来源，仍须由冻结语义评测检验。没有发现本轮 diff 直接删除取消/时间/上下文边界，但这不是生产流量或模型可靠性证明。


## Root验证收尾

重建后同一受控脚本已取得[64/64通过](runtime-fault/summary.json)，外部推理0，原[43/64失败](runtime-fault-first/summary.json)继续保留。Java集成测试最终结果须以regressions.json的无跳过XML统计为准；此前Maven success包含34项skip，过程记录已明确纠正。
