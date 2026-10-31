# Round 9 原四轮 Alder Relay / KiteLoad 语义复核

评分发生在 `raw-seal.json` 完成且校验后。候选 `77b7a550e6a3390a2fdac6c5136a792a126d5631`，raw seal SHA-256 `574711014f477b414424cd50a56a6554f1c765b3bac469e547d144c5e16b2e0c`。沿用 [Round 5 原 gold](../../conversation-round5/new-scenarios/frozen/gold.json) 与 [0/1/2 协议](../../conversation-round5/new-scenarios/frozen/protocol.md)，没有修改原题、资料、gold、预算或原始输出。

这是已暴露合成开发题上的独立一次 AI 阅读。评分者参与本轮算术实现，不是未参与开发的人工出题/评分者，也不称新盲测。评分阅读了问题、原始来源、实际检索、最终报告和工具轨迹；失败 draft 不计为已交付答案。[结构化标注](annotations-new.json)。

## 四轮结果

| 轮次 | 交付 | 六维分数：检索 / 正确性 / 完整性 / 引文 / 无误拒 / 范围 | 严格通过 |
| --- | --- | --- | --- |
| ar-t1 | 5 条事实/矛盾报告 | 2 / 2 / 1 / 1 / 2 / 2 | 否 |
| ar-t2 | review 超时，无最终报告 | null / null / null / null / null / null | 否 |
| kl-t1 | review transport timeout，无最终报告 | null / null / null / null / null / null | 否 |
| kl-t2 | 前序无有效答案，未提交任务 | null / null / null / null / null / null | 否 |

原四轮交付 **1/4**，严格 **0/4**；仅在可评分回答内为 **0/1**。失败与阻断留在四轮主分母，不把 null 写成语义零分，也不以唯一成功回答掩盖交付失败。[运行汇总](../runs/round9-once/run-summary.json)。

## ar-t1：主答案正确，原 gold 细节和逐行引用仍不足

[最终原始报告](../runs/round9-once/ar-t1-final-task.json) `result.assessments[0..4]` 回答 Cedar 2.4：最多尝试 2 次、包含初始尝试、按 2−1 算得 1 次重试；仅传输超时可重试；HTTP 4xx 最终且不重试；培训材料的“一律三次”被判为矛盾，其解释指出未批准且复制了旧 1.8 内容。三个所需来源——2.4 policy、签名 register、培训更正——均真实出现在 `agent.trace[8].output.hits`，另有可选 1.8 quickstart。检索记 2。

结论正确性记 2。第 1 行未单独重复“最多”二字，但当前问题明确问上限、第 0 行明说“最多尝试 2 次”，第 2 行又限定“仅传输超时可重试”。按原 gold 允许语义等价表述的规则，这里表达配置重试上限，没有断言某次历史请求已经重试。不能为了沿用前轮措辞问题，把正确的上下文推导说成历史幻觉。

完整性记 1：原 gold `ar1-c3` 要求解释 **2.4 默认 total_attempts=1，Cedar 签名部署 effective=2 优先**。答案虽然说明 Cedar 的配置是 2，却没有解释默认 1 与部署覆盖优先的关系。该事实出现在引用原文里，不等于答案已经解释它。用户本轮显式提问的次数、4xx 和培训冲突都有回应，因此诊断分为“主结论正确”和“gold_specific_detail 缺口”，不把严格失败混称为整个答案错误。

引文蕴含记 1：第 0、1 行均只选 register。它支持 Cedar 的 `total_attempts=2` 与“configured limits”，但不提供总尝试包含初始的定义；该前提在 2.4 policy，未被第 1 行选入。第 1 行的 `2 - 1 = 1` 算术正确，仍不意味着自己已经引用全部推导前提。第 2 行选择 register＋policy，第 3 行选择 policy，第 4 行选择培训更正＋policy＋register，后三行组合引文支持对应结论。报告其他行出现正确来源，不能替前两行补上缺失引用。

错误拒答与范围均记 2：没有拒答、澄清、错误版本替换或把上限推成历史执行。第 2 行较短的 statement 写“Alder Relay 2.4…最多 2 次”，其 explanation 明确限定 Cedar，没有据此把 2 次推广到所有部署。

本次 delta response 的 `retained_assessment_ids` 为空，5 行全部 replacement。成功发布证明这一次替换路径完成，不证明模型使用了保留行的输出压缩，也不能从此声称延迟已经优化成功。`generation_review.delta` 与 `agent.trace[22]` 保存了这次复核的实际记录。

## ar-t2：有检索和 provider 响应，仍没有最终交付

[失败任务](../runs/round9-once/ar-t2-final-task.json) 状态 `FAILED/agent_timeout`，`result=null`。实际检索包含 Juniper 所需的 register 和 1.8 quickstart，但没有已发布答案可用于判定修正后的三项比较是否正确。

`error_result.agent.trace[21]` 显示 review 开始时剩余约 49.31 秒；`trace[22]` 记录 HTTP 200、已收到响应、已知 usage 7,811 tokens，但返回时 elapsed 75,354.011 ms、remaining 0，outcome 为 error，之后 stopped timeout。**provider 返回成功与任务成功不同**。不对超时前 draft 或未接受的 review 内容给语义分，也不猜测未公开的最终比较结论。

## kl-t1：review transport timeout；kl-t2 保持依赖阻断

[kl-t1 失败任务](../runs/round9-once/kl-t1-final-task.json) 的实际检索含配置、完成回执、告警卡与历史证据清单四份所需材料。`error_result.agent.trace[22]` 记录 review 请求 timeout，`response_received=false`、`provider_acceptance=unknown`、usage 三项为 null；任务没有保存最终报告。

因此没有证据表明本轮已经正确交付“当前 2 次重试 / 3 次总尝试”以及“R-731 历史重试未知”的区分，亦不能把中间 draft 当成通过。kl-t2 在 [run-summary](../runs/round9-once/run-summary.json) `turns[9]` 中明确是 `blocked_no_valid_prior_response`，被 kl-t1 阻断，没有 task ID。用户更正归属、是否至少重试一次、上游限流与最小证据这几个结论均保持 not_evaluable。

## 本轮能与不能证明什么

唯一交付回答正确处理了 Cedar 配置上限和培训资料冲突，仍缺默认/覆盖说明及逐行前提引用。本组两次执行失败都发生在 review，但一次为响应到达后总预算用尽，一次为未收到响应的 transport timeout，不能把它们合并成同一种 provider 行为。原四轮没有任何 strict pass，不能宣称总体质量或可用性提升；有限算术与 basis 工程测试通过不替代真实模型的交付和语义验收。
