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

## repaired：待封存

仅在该阶段原始产物封存并核验后追加审阅；不会提前读取未封存响应。baseline 两次失败及其未知用量保留，不因后续结果而替换。
