# 推理前交叉审查修订：input v1 → v2

**修订时本轮尚未启动真实评测、提交 task 或调用外部模型：模型调用为 0。** 以下是输入/执行器审查修复，不是看到模型答案后改变评分标准。原协议、两组问题与 gold 未修改；需要 root 对 v2 清单重新批准，v1 批准不能用于 v2。

原 `input-freeze.json` 的完整字节保存在 [input-freeze-v1-pre-review.json](input-freeze-v1-pre-review.json)，SHA-256 为 `fad318b35493b99492f4155b8c92b4141d4c9154c42807c88bdfe8c3eb83dd5d`。改变的两份工具和测试的原 v1 源码放在 `pre-review-v1/*.py.txt`，可与原清单相应 SHA 对照；v1 清单不会再拿来验证当前 v2 工具。

| 审查发现 | 修复 | 负例验证 |
| --- | --- | --- |
| 只在启动时核验输入，逐轮只核验生产源码；最终 summary 使用当前清单 hash，可能掩盖运行中输入变化 | 每个计划轮（含被依赖阻断轮）及 finally 都验证 approval 指定的原输入清单 SHA 和其中所有文件；summary 始终记录 approval SHA | 首轮之后与最后一轮之后分别篡改清单/输入文件，后续停止或最终标记失效，摘要仍保留原批准 identity。 |
| aggregate token 是非负以外的任意整数也被接受；aggregate 全知可能覆盖 trace 未闭合、usage_known=false 或 provider_acceptance=unknown | 核验 aggregate 与每次 request_finished 的 token 非负；按 request_id 一一配对 start/finish、phase 一致、usage_known 和 response_received 为真、provider_acceptance=response_received；有一处未知就保留 unknown | 真实上轮 trace 为基底，分别注入负 token、未闭合请求、usage 未知、provider 接受未知；调用次数仍可已知，token/provider 不能被聚合值洗成全知。 |
| evidence_pointers 仅要求非空 list，None、空串、不存在目标或越界片段仍可通过 | 每条引文指针必须为非空 string，属于 raw seal 或 input manifest 的实际文件，哈希仍匹配；可进一步定位 JSON pointer 或行范围 | 拒绝 None、空串、未封存但存在的文件、路径越界、JSON 键缺失/数组越界/null、行号越界；允许有效 raw/input 位置。 |

指针格式预先明确为：

```text
raw:cedar-t1-final-task.json#/result/answer
raw:cedar-t1-final-task.json#/result/agent/trace/0
raw:run-summary.json#/turns/0
input:docs/evaluations/conversation-round5/development/gold/rubric.json#/turns/cedar-t1/mandatory/0
input:docs/evaluations/conversation-round5/development/inputs/sources/cedar/01-approved-acceptance.md#L1-L4
```

指针的存在及哈希只是可审计前提；它不会自动判定某条结论是否被支持。结论正确性、引用蕴含、来源范围与遗漏仍须由评分者逐条阅读，保留原 gold 与所有失败。引用未知值时指向包含它的记录，不使用 JSON null 本身作为可读语义证据。

v2 生成只允许在保留的 v1 清单与当前原清单相同、且真实 runs 目录尚不存在时显式执行 `prepare --pre-run-revision 2`；禁止以此入口覆盖已运行研究。

本次离线执行器测试为 **37 项通过**；另一开发团队 AI 的修复后只读复核 [10 项检查全部通过](harness-cross-review-fixed.json)。这些都是工具约束验证，不是模型答案质量结果。
