# Round 9：Cedar / Harbor 原六轮独立 AI 评阅

结论：**交付 0/6，原 strict 0/6；2 次提交失败、4 次依赖阻断，没有可评分正式回答。** 本组没有已评分轮，不能把 `0/0` 当准确率。全部问题已暴露，评阅人属于开发团队 AI；“独立”仅指在合并标注前单独阅读本组证据，不是人工独立出题或盲测。

候选 `77b7a550e6a3390a2fdac6c5136a792a126d5631`；input SHA `44fdc6c67b08166e28a02c1cf8fc0769aa0ac65cac12ad5570af6a7be89cef78`；raw seal SHA `574711014f477b414424cd50a56a6554f1c765b3bac469e547d144c5e16b2e0c`。已使用评分器验证封存清单和执行身份，再阅读首个终态、会话、实际检索及生命周期。未修改源码、输入、gold、raw 或重调模型。

## 按原规则保留结果

| 轮次 | 首个终态 | 正式交付 | 原 strict |
| --- | --- | --- | --- |
| cedar-t1 | FAILED / provider_failed；内部 review_error | 无 | 不通过 |
| cedar-t2 | 前置 cedar-t1 失败，未提交 | 无 | 不通过 |
| cedar-t3 | 前置 cedar-t2 阻断，未提交 | 无 | 不通过 |
| harbor-t1 | FAILED / provider_failed；内部 review_error | 无 | 不通过 |
| harbor-t2 | 前置 harbor-t1 失败，未提交 | 无 | 不通过 |
| harbor-t3 | 前置 harbor-t2 阻断，未提交 | 无 | 不通过 |

[执行汇总](../runs/round9-once/run-summary.json)保留全部六个身份及依赖关系。[标注](annotations-old.json)逐项保留原 mandatory 与 forbidden 文本和定位指针：mandatory 为 blocked；没有最终答案时 forbidden 的 present 为 null，不能把 null 解释为已证明不存在。

依原冻结 rubric 及评分器的未交付约定，semantic_completeness、false_refusal、referent_resolution、source_scope 均为 fail，citation_entailment 与 retrieval_coverage 为 blocked。这里的 fail 是未交付计分约定，不意味着已经观察到六轮全部事实错误、错误拒答或指代失败。七项补充语义诊断全部 `not_evaluable`，交付类型全部 `no_response`；未发布草稿不计作事实报告，也不用于补足最终答案。

## 已确认的失败阶段

| 信号 | cedar-t1 | harbor-t1 |
| --- | ---: | ---: |
| 已发模型尝试 | 2 decision + 1 review | 2 decision + 1 review |
| review 开始时刻（worker 内） | 35.032 秒 | 39.902 秒 |
| review HTTP 状态 / 返回 | 200 / response_received | 200 / response_received |
| review 请求耗时 | 24.835 秒 | 8.865 秒 |
| review 返回后剩余预算 | 15.133 秒 | 26.234 秒 |
| review completion token | 761 | 136 |
| budget_exhausted | false | false |
| 后续状态 | review_error，无 review_completed | review_error，无 review_completed |

对应原始位置为两份 final-task 的 `/error_result/agent/trace/21`（review 请求）、`/22`（返回）、`/23`（停止），以及 `/execution_usage`。任务：[Cedar](../runs/round9-once/cedar-t1-final-task.json)、[Harbor](../runs/round9-once/harbor-t1-final-task.json)；会话：[Cedar](../runs/round9-once/cedar-t1-conversation.json)、[Harbor](../runs/round9-once/harbor-t1-conversation.json)均只有 FAILED 记录，没有持久化 answer。

因此两项不应标为本轮超时。外围 `provider_failed` 和 worker HTTP 502 也不能解释成 DeepInfra 返回 502：provider 请求轨迹记录的是 HTTP 200。这组共 6 次尝试，全部用量已知，33,341 token = 30,519 prompt + 2,822 completion；失败调用不能因无答案而记作零消耗。

已确认请求返回之后未能完成报告发布，但封存结果没有失败 review 的原始正文或 finish_reason，不能区分 JSON/输出截断、delta ID 分区、schema、引文或其他最终复核失败，更不能仅凭错误名称归责 provider。成功响应审计字段只在成功构造 generation_review 后生成，尚不能填补失败路径的响应保留缺口。该缺口是后续诊断改进目标，不能在本轮事后修改候选再补采来抹去失败。

## 检索已到达的证据与评分边界

Cedar 的真实 `/error_result/agent/trace/8/output/hits` 中：

- `hits/0/text` 是 C-21 原规则正文，包含 BOTH 条件、节电至少 12.5%、最大温度至多 5.0°C、等号有效、公式和最大值不能用平均值替代。
- `hits/1/text` 是签字测量正文，包含 baseline 240、Dune 208 / 最大 5.2、Reed 210 / 最大 5.0，以及同一 24 小时负载条件。
- `hits/4/text` 补齐规则尾部“不证明总运行费或其他地点部署授权”。同时检索到的 C-08 有明确不适用当前 C-21 的说明。

Harbor 的同一检索事件中：

- `hits/0/text` 包含 R-17、median 严格小于 150、success 至少 99%、BOTH、未舍入比例判定和非停电范围。
- `hits/2/text` 是签字结果，包含 Quill 150 秒及 198/200、Sable 149 秒及 395/400，分母包含全部尝试。
- `hits/1/text` 说明 dashboard 对两者显示整百分比 99%，签字计数优先，且不能据普通 median 推出停电最大恢复时间。

这些是已读正文，不是仅凭标题判断覆盖。两项任务检索到了原 gold 计算和验收所需关键证据，但没有可评分正式答案，所以正式证据轴仍按原失败规则记 blocked；实际检索观察单独记录在 `retrieval_review`。后四轮根本未提交，不能借用前一轮检索给其覆盖通过。

与第八轮本组 3/6 交付（2 报告 + 1 必要澄清）、strict 1/6 相比，本次交付更差。有限机械修复和受控协议测试通过，不足以宣称新协议已改善真实模型质量或可用性；这次失败应保留，而不是只展示工程测试数量。
