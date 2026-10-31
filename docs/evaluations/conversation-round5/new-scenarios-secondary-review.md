# Round5 新题评分复核（2026-10-31）

**封存 strict 0/4 与预注册 gold 一致，但不能表述成“四题都答错”或“两份完成回答都结论错误”。** 实际为 2 份完成回答、1 次超时、1 轮因前置失败未提交；完成回答按该 gold 为 0/2。本文是开发团队内第二个 AI 的事后复核，不是盲评或人工独立评测；未改封存文件，未发模型请求，也未重复请求用量审计。

| 题目 | 复核结果 | 关键证据与评分边界 |
| --- | --- | --- |
| ar-t1 | 未交付，非答案语义评分 | [终态](new-scenarios/runs/round5-new-once/ar-t1-final-task.json)：`status=FAILED`、`error_code=agent_timeout`、无最终答案。 |
| ar-t2 | 未提交，非答案语义评分 | [运行摘要](new-scenarios/runs/round5-new-once/run-summary.json)：`blocked_no_valid_prior_response`；保留在计划分母，不能计为已完成。 |
| kl-t1 | 原 gold 下 FAIL；对严格完整性要求敏感 | [最终答案](new-scenarios/runs/round5-new-once/kl-t1-final-task.json) 正确回答 2 次重试/3 次尝试、成功不证明重试、无日志/计数而不能确定历史、无图例的 badge 不能证明两次重试，且四份资料均已检索。遗漏的主要是仅 transport timeout 可重试，以及当前配置不是历史快照的明确解释。没有发现捏造重试次数或错误拒答。 |
| kl-t2 | 原 gold 下 FAIL；存在可定位的多余不足结论 | [最终答案](new-scenarios/runs/round5-new-once/kl-t2-final-task.json) `result.assessments[0]` 无引文却写“无法确认当前配置”；后面正确把分片解释归于用户，并保留重试和限流原因的不确定性。最终没有重新说明已知 `SUCCEEDED`，也未明确给出可靠重试计数或关联尝试记录这一最小证据标准。 |

**gold 与问题是否对齐：** [原问题](new-scenarios/frozen/dataset.json) 的 kl-t1 要求“区分能够确定的事实和缺失的证据”，没有逐字要求说明配置日期先后，也没有像 kl-t2 一样明确要求“最小证据”。[gold](new-scenarios/frozen/gold.json) 的 `kl1-c4`（必须明示时间/快照关系）和 `kl1-c5`（必须提出权威或关联证据标准）比问题字面更具体；答案已分开当前配置与历史行为，并列出缺失计数、事件和日志。因此 kl-t1 应报告为“核心结论正确、未达到预设完整性门槛”，不能把这些遗漏当成事实错误。transport-timeout 限制是有价值的适用范围补充，但它的遗漏不使“最多 2 次重试”这个上限本身变错。

**kl-t2 的资料依据：** [同一工作区已保存资料](new-scenarios/runs/round5-new-once/kite-load-documents.json) 的 `kl-config` 明确包含 `max_retries=2` 及首次尝试说明；两轮 `snapshot_revision` 都为 4。t2 的 `result.retrieval.hits` 只含 alert、run、retention，并未重新检索 config。因此这不是“资料根本不存在”，而是本轮未取到资料却额外输出配置不足结论。其“当前评估”可狭义理解为本轮检索范围，故宜描述为**有资料可答的多余子项被降级、范围表述有歧义**，不夸大为它明确宣称整个工作区没有配置。该题原本也不要求重复配置。

保留原始 strict 计数，不事后放宽 gold 改分；对外并列说明交付率、核心结论与严格完整性。下一批题应在冻结前明确需要的适用范围、证据充分性标准和必须重述的前提，避免隐藏的细节要求主导二元通过率。
