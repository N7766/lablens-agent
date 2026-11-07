# Round 13 模型探针复核：传输返回，精确输出门未通过

复核日期：2026-11-08（Asia/Shanghai）。本次只读检查已封存结果，没有修改协议、请求、原始记录或追加模型调用。新题和本次复核由同一名独立于生产遥测实现的 AI 开发代理完成；这不是独立人工评测，也不是对出题人的独立审阅。

**本次仅发送 1 次 tiny 请求，HTTP 200 且用量已知；模型回答 `OK.`，未满足预声明的精确 `OK` 输出条件，因此按照原协议停止。RAG 生成、复核和最终语义均未测。**

## 封存与候选身份

- 候选 commit：`55cf2be0c9c9c44b5f7590fb83a8a35c752fe0aa`；见 [冻结计划](run/plan.json)。
- `verify-seal` 复核通过：封存的 12 个文件的集合及 SHA-256 均未改变。原始 seal：[raw-seal.json](run/raw-seal.json)，SHA-256 `60b40636a16c23d2ad447097f56d463b0a976be73ad89050fa192506d3ca711f`。
- [新题](fixture.json) SHA-256 `e1626e6a23e78d4641e5e0d34fd8b803f5cae81baf807be6d965d130be04f066`；[语义标准](semantic-checklist.md) SHA-256 `c85e34abc29b6be05da3f164b37bb5a1fc77c288b3c9db8828703e8cb9e9b41d`。两者与候选冻结前已记录的版本相同。
- [协议](protocol.md) SHA-256 `09faa3435462c6cb869122359fc858ad38085916362147fb5a49a69f77b91875`。本次不放宽输出条件，不把失败改写为通过。

## 每一层实际得到什么

| 层次 | 观察与判断 | 对应证据 |
| --- | --- | --- |
| 请求发送 | 1 次 tiny POST，正文 142 bytes，输出上限 64 tokens，实际传输 timeout 约 9.999983 秒；无重试、无第 2/3 次请求 | [请求意图](run/01-tiny/request-intent.json)、[总汇总](run/summary.json) |
| 传输及 HTTP | 收到完整 HTTPX Response，HTTP 200；本次请求没有超时 | [响应元数据](run/01-tiny/response-metadata.json)、[生产轨迹](run/trace.json) |
| 供应商 JSON / 完成标志 | 返回可解析的 completion，`finish_reason=stop`，内容为 `OK.`；不是截断输出 | [原始 response body](run/01-tiny/provider-response.bin) |
| 精确输出门 | `.strip()` 后的 `OK.` 不等于协议要求的 `OK`，故 **fail** | [请求原文](run/01-tiny/request.json)、[失败记录](run/failure.json)、[运行器](../../../../scripts/run_model_probe_round13.py) |
| 用量 | prompt 13、completion 3、合计 16 tokens，`provider_usage_unknown=false` | [usage](run/usage.json) |
| 依赖的 live RAG | **not_run**。未进行新题生成和复核，也没有可评分的最终报告 | [总汇总](run/summary.json) 中 `dependent_rag=not_run`、`final_semantic_status=not_evaluable` |
| Agent / 历史持久化 | 本协议没有覆盖；本次更没有进入该链路 | [协议范围](protocol.md) |

`failure.json.status=provider_error` 是当前运行器复用的宽泛错误标签，**不能据此说供应商 HTTP 失败或又发生了超时**。`tiny_failed` 精确指本轮 tiny 阶段没有通过全部条件；结合原始响应，失败原因已缩小到精确输出不匹配。记录中没有必要为这次成功传输填入 timeout phase。

时间口径也应分开：请求包装器记录 1048.153 ms，生产 request span 记录 1064.009 ms，整个运行器记录 1085.460 ms。测量范围不同，且包含关系不能相加。一次约 1 秒的小请求返回，不证明较大的 JSON schema 请求、历史 review 或供应商长期可用性已恢复。

原始 response 的 `usage.estimated_cost` 为 **US$0.00000282**，且 `service_tier=default`。这与 [pricing.json](pricing.json) 记录的标准输入/输出单价按 13/3 tokens 计算一致；它是供应商返回的估算字段，未与账户账单核对。三请求 US$0.11051112 的保守规划上界不是本次消费金额。

## 检索和语义验收必须分开

冻结阶段执行的是本地 BM25/extractive 预检。已核对 [计划中的 offline_retrieval](run/plan.json)：4 个必需文档均出现在 4 个 hits 中，文本、source 和 content SHA 与 fixture 一致，故可以写 **离线检索覆盖 4/4**。这一步没有请求模型。

实际 live 运行在 tiny 阶段结束；不存在生成的 Alder 结论，不能把冻结检索结果称作 live RAG 成功，更不能写“支持、矛盾、缺失信息判断通过”。按预先冻结的标准：

| 标准 | 本次结果 |
| --- | --- |
| 1. 检索所需四份资料 | 离线预检 pass，4/4；live RAG 未执行 |
| 2. 已知 publisher-confirm 观察 | not_evaluable |
| 3. 两份 consumer 计数的矛盾 | not_evaluable |
| 4. 缺失原因与因果克制 | not_evaluable |
| 5. 引用对命题的真实支持 | not_evaluable |
| 6. 错误拒答控制 | not_evaluable |

新题受 [RabbitMQ 官方语义说明](https://www.rabbitmq.com/docs/confirms#are-publisher-confirms-related-to-consumer-delivery-acknowledgements) 启发，所有 Alder 运行记录和计数均为合成材料；它既不是实际线上事故，也不是人工独立问题集。即使未来完成一题，也不能据此发布稳定成功率。

## 发现的协议设计问题与下一轮边界

本次停止符合冻结规则，但规则把“多一个句号的指令遵循错误”绑定成了后续传输诊断的依赖门。结果是已经证明当前 tiny 请求能够返回，仍无法观察较大的真实 RAG 请求。这一门槛会妨碍诊断推进；问题在于探针目标与依赖条件没有分开，不需要将 `OK.` 误称为网络故障。

下一轮如继续模型诊断，应在任何调用前另立新的实验身份和 once-only claim，预先分别声明：

1. **传输门**：预定 endpoint/model 返回 HTTP 成功、可解析 completion、正常完成标志、已知 usage，并且未超过预算。满足后才允许下一项已声明请求。这里不使用标点是否精确作为传输成功的替代条件。
2. **指令检查**：仍按事先声明的原始目标单独记录 exact-match pass/fail；若业务确实要求精确格式，可以作为产品验收失败，但不要隐含改变传输诊断的执行顺序。
3. **RAG 独立门**：保持真实生产生成/复核/最终校验、固定最大次数和预算；最后按来源、结论、矛盾、缺失信息与错误拒答评分。不得把 HTTP 200 或引用原文存在直接记成语义通过。

以上只是下一轮协议建议。本轮 claim 不删除、不恢复，不补发送剩余两个请求，不更改既有通过条件。Alder 新题与标准已暴露给开发流程；若以后重用，应标作已暴露样本回归，不再宣传为新的盲测题。Round 10 的 4 次大请求超时仍未解释或修复，本次小请求成功无法倒推出其历史超时阶段、网络路径或根因。
