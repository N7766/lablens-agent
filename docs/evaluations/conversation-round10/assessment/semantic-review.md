# Round 10 直接复核诊断：语义审阅

判定依据为在 provider 调用前写定的[语义清单](../semantic-checklist.md)。本审阅仅针对已暴露 Cedar / Harbor 首轮草稿的直接 review，不能替代 Agent 端到端、检索、新题或人工独立评测。失败调用仍保留在实验清单中；没有最终结果的调用不评分答案语义。

## baseline：封存后检查

先重新计算并核验 [raw seal](../runs/baseline/raw-seal.json) 的全部 **17** 个文件及完整文件集合，SHA-256 为 `d509c9a6606016f09e3a6d171db4824402ea51da80f9e90b4a54e6e08d5d968a`。候选 commit 为 `98e1843821298ab082c24270511907547f7ef4cf`；输入 SHA-256 为 `a228c9a4766d7adbfed6918d4068c4502ac75cf8860aca0a2f9571722c42cc39`。

| 场景 | 实际请求 | 复核限额 / 本次调用总耗时 | 传输结果 | 最终语义 |
| --- | ---: | --- | --- | --- |
| Cedar t1 | 1 | 39,968.432 / 40,538.019 ms | timeout；未收到响应，HTTP 状态未知 | `not_evaluable` |
| Harbor t1 | 1 | 35,098.544 / 35,673.607 ms | timeout；未收到响应，HTTP 状态未知 | `not_evaluable` |

证据分别见 Cedar [attempt](../runs/baseline/cedar-t1/attempt.json)、[trace](../runs/baseline/cedar-t1/trace.json)、[failure](../runs/baseline/cedar-t1/failure.json)、[usage](../runs/baseline/cedar-t1/usage.json)，以及 Harbor [attempt](../runs/baseline/harbor-t1/attempt.json)、[trace](../runs/baseline/harbor-t1/trace.json)、[failure](../runs/baseline/harbor-t1/failure.json)、[usage](../runs/baseline/harbor-t1/usage.json)。两例均无 `provider-response.bin` 或 `result.json`。最终[运行汇总](../runs/baseline/summary.json)保留两次失败，没有自动重试。

两次 provider 请求的 `request_finished` 都记录 `response_received=false`、`provider_acceptance=unknown` 和 `usage_known=false`。生成 token 的三项值均为 `null`；这表示用量和计费未知，不能写成零 token、零费用，也不能确定 provider 没有处理请求。外层 `stage_finished.outcome=error` 是 RAGError 转换后的阶段分类；判定传输超时应读内层 request 事件和 failure，而不是只看外层标签。

没有可评的最终结论，因此算术、阈值边界、AND 总体验收、引文蕴含、错误拒答和越界推断均为 `not_evaluable`。未交付没有证明答案正确，也没有证明模型必然答错。两次调用总耗时分别比给定复核时长多约 570 / 575 ms；不能把请求 timeout 参数当作严格的外层总耗时上界。

本阶段只证实这两次新诊断请求遇到超时。它没有收到新响应，不能定位其 JSON、ID 分区或引文恢复错误，更不能代替 Round 9 原先“HTTP 200 后报告未交付”的历史失败机制。原始历史响应未保留的证据缺口仍然存在。

## repaired：封存后检查

在收到该阶段封存完成通知后，重新核验 baseline 与 repaired 的完整文件集合及每个文件 SHA。两份 seal 均通过，baseline 仍为上文的 17 文件；[repaired raw seal](../runs/repaired/raw-seal.json) 也是 **17** 文件，SHA-256 为 `1eb8a399959ef6e57398ffa886659039575abf7350fbd54a6e648edee2c67687`。修复候选 commit 为 `d6632110468b9e081648c08c242582acdc08f6de`。

| 场景 | 实际请求 | 复核限额 / 本次调用总耗时 | 传输结果 | 最终语义 |
| --- | ---: | --- | --- | --- |
| Cedar t1 | 1 | 39,968.432 / 40,543.270 ms | timeout；未收到响应，HTTP 状态未知 | `not_evaluable` |
| Harbor t1 | 1 | 35,098.544 / 35,963.737 ms | timeout；未收到响应，HTTP 状态未知 | `not_evaluable` |

证据分别见 Cedar [attempt](../runs/repaired/cedar-t1/attempt.json)、[trace](../runs/repaired/cedar-t1/trace.json)、[failure](../runs/repaired/cedar-t1/failure.json)、[usage](../runs/repaired/cedar-t1/usage.json)，以及 Harbor [attempt](../runs/repaired/harbor-t1/attempt.json)、[trace](../runs/repaired/harbor-t1/trace.json)、[failure](../runs/repaired/harbor-t1/failure.json)、[usage](../runs/repaired/harbor-t1/usage.json)。两例同样没有 `provider-response.bin` 或 `result.json`，三项生成 token 均为 `null`，provider 是否接收/处理以及计费未知。

### 两阶段可比性核查

- 对照封存的 [baseline plan](../runs/baseline/plan.json) 和 [repaired plan](../runs/repaired/plan.json)：输入 SHA 同为 `a228c9a4766d7adbfed6918d4068c4502ac75cf8860aca0a2f9571722c42cc39`，协议与语义清单 SHA 各自相同，repaired 的 baseline seal 引用准确匹配原 seal。
- 分别对照两场景实际保存的 request 对象。仅 `messages[0].content`（system 提示文本）不同；将该字段替换回 baseline 值后，整个 JSON 对象完全相等。问题、资料、草稿、上下文、response schema、model、temperature 和 max_tokens 均未改变。来源为 Cedar 的 [baseline](../runs/baseline/cedar-t1/request.json) / [repaired](../runs/repaired/cedar-t1/request.json)，以及 Harbor 的 [baseline](../runs/baseline/harbor-t1/request.json) / [repaired](../runs/repaired/harbor-t1/request.json)。
- HTTPX 编码的请求正文长度：Cedar **29,533 → 28,759 B**，Harbor **28,064 → 27,290 B**，两者各少 774 B。这是请求正文的机械长度变化，不是模型 token、计费或响应延迟收益。四次均没有响应，无法评价格式提示修复是否提高遵循率。
- 阶段汇总、逐例 attempt、request trace 和两份 seal 共同支持 **2 + 2 = 4 次请求、0 份最终报告、4 次用量未知**。[总汇总](../summary.json)与这些原始记录一致。没有自动重试、没有用后续调用替换先前失败；计划上限已用完。

## 本轮语义结论

四次调用全部没有可评的最终结果，因此所有答案语义维度均为 `not_evaluable`，不能给出答案正确率或“语义回归通过”的结论。两阶段均超时，没有得到足以证明实际交付、完整 Agent 链路或模型格式遵循改善的证据。

本轮保留了实际请求和失败诊断的追溯能力，并控制了两阶段输入一致性；这属于实验可核验性。提示缺陷的代码修复是否解决 Round 9 的历史验证错误仍未经真实响应确认，不能把这四次传输失败作为其成功修复或失败根因的证明。未保留的旧 provider 响应、当前 provider 内部处理状态、计费以及完整 Agent 端到端状态仍然未知。
