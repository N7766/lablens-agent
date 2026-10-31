# Round 6：答案质量回归结果

**本轮未通过整体验收。** 原六轮从 strict 2/6 变为 0/6：首轮两次失败，使四个依赖轮未提交。原四轮从 strict 0/4 变为 1/4，只有 `ar-t1` 达到原 gold 全维满分。两套 rubric 不同，不能合并为统一“准确率”；这十题都已暴露给开发者，结果仅说明本次候选在这组开发回归上的表现。

候选为 `dd225edf2c286619eae0d299228975c6fecee9f0`，模型为 `Qwen/Qwen3-235B-A22B-Instruct-2507`。每任务沿用 75 秒、最多 6 decision + 1 review、decision 1800 / review 2600 输出上限，无重试。使用 BM25，无 embedding 请求。全部原问题、资料、gold 和 Round 5 结果保留。

## 交付、评分与用量

| 组别 | 计划轮次 | 实际提交 | 持久化响应 | 失败 / 依赖阻断 | 原 gold strict |
| --- | ---: | ---: | ---: | --- | --- |
| Cedar / Harbor | 6 | 2 | 0 | 2 / 4 | 0/6；无可评分响应，已评分分母为 0，不定义准确率 |
| Alder Relay / KiteLoad | 4 | 4 | 4 | 0 / 0 | 1/4；可评分分母也是 4 |
| 合计（只合并交付计数） | 10 | 6 | 4 | 2 / 4 | 不合并异构 rubric |

4 个 `SUCCEEDED` 中有 **3 份事实报告、1 次澄清**；不能称“四份有效报告”。澄清也有非空持久化 answer，符合执行器的交付/依赖标准，但仍须接受语义评分。`kl-t2` 的澄清未回答本来可答的有界不足结论，因此严格失败。

17 次模型尝试全部可以计数：13 decision、4 review；15 次有已知 token，2 次超时的 provider 接受及 token 未知。已知 token 下限为 **58,199**（prompt 52,610 + completion 5,589），不是完整消费量，不能据此宣称完整费用。无任务重提、provider retry、缓存复用或 embedding 请求。详见 [调用审计](assessment/call-audit.json)。

## 每轮结论

| 轮次 | 结果 | 证据支持的解释 |
| --- | --- | --- |
| cedar-t1 | FAILED / agent_timeout | 两次 decision 后进入 review；review 只有约 24.22 秒可用，随后超时。实际 worker 76.032 秒，`budget_exhausted=true`。原稿已是 8 项，未补槽，不能归因于本轮补槽逻辑。 |
| cedar-t2 / t3 | blocked | 前置无持久化回答，按冻结依赖门不提交，仍计入 6 的计划分母。 |
| harbor-t1 | FAILED / agent_timeout | 8 条原稿已有两个“无法通过验收”总判定，coverage 未识别而准备重复补两个槽，触发超过 8 项并被拒绝。随后第三次 decision 约 8.18 秒超时，worker 55.657 秒，仍保留约 19.36 秒 review 时间，`budget_exhausted=false`。这是另一失败阶段，不能写成统一的 75 秒 review 超时。 |
| harbor-t2 / t3 | blocked | 同上，前置失败后不伪造会话答案，也不独立重跑依赖问题。 |
| ar-t1 | SUCCEEDED / strict PASS | 正确给 Cedar 2.4 总 2 次、最多重试 1 次、仅 transport timeout、4xx 不重试；解释 default 1 与部署 override 2、未批准培训资料的冲突。 |
| ar-t2 | SUCCEEDED / strict FAIL | Juniper 1.8 的总 3 / 重试 2 / 4xx 不重试正确，但未明确按题意指出 2→3、1→2 与 4xx 保留。继续解释旧培训材料属于冗余，不能补上所漏的比较。 |
| kl-t1 | SUCCEEDED / strict FAIL | 当前配置与历史未知、badge/成功不能证明重试的核心判断正确。仍缺原 gold 的 transport-only、当前快照晚于运行/不是历史配置、明确权威计数或关联 attempt 的最小证据细节。 |
| kl-t2 | SUCCEEDED / strict FAIL | 实际检索已得到 receipt、inventory、alert，却只问用户是否还有日志。没有回答重试发生未知、限流原因不能归因、最终成功/此前失败未成立及两类最小证据。用户归属和 R-731 指代正确；主要问题是不必要澄清及漏答，不是检索失败或已断言相反历史事实。 |

两个失败案例的原文、trace 和离线复现见 [失败复核](assessment/failure-review.md)。最终语义标注见 [annotations.json](assessment/annotations.json)，由冻结 [评分器](../../../scripts/score_conversation_round6.py) 聚合为 [scores.json](assessment/scores.json)。另一开发团队 AI 在未读取这些标注前完成 [只读语义复核](assessment/independent-review.md)，结论一致；这是内部 AI 复核，非盲评、非独立人工评分。

## 保留原 gold，并解释严格门槛

四份已交付响应的原六维满分数为：检索 4/4、结论正确性 3/4、完整性 1/4、引用蕴含 3/4、无错误拒答 3/4、指代/范围 3/4。各维度是对这四轮的描述；不能用检索覆盖代替语义通过，也不能将事实报告与纯澄清合并成答案有效率。

`kl-t1` 的原 gold 比题干明确问到的内容更细：要求明确时间/快照先后和最小证据标准。此缺陷在本轮前已披露，**原 gold 和 strict 分数没有事后放宽**。并列诊断为“核心结论正确、原 gold 细节不完整”，不是“事实全错”。`ar-t2` 的变更/保留比较及 `kl-t2` 的限流、最小证据则是题干明确要求，不能归为隐藏门槛。

旧六轮原 rubric 规定未交付时语义/指代/范围轴失败，引用/检索轴 blocked。本轮保留这个口径；实际首轮检索命中齐备可以另列为诊断，不能把无最终回答改成语义通过。评分输出的 `strict_evaluated="0/0"` 只是原始分母字符串，报告解释为“没有可评分回答”，不是 0% 或 100%。

## 能确认与尚不能确认

- 能确认：候选在 `ar-t1` 完成严格回答；`ar-t2` 和 `kl-t1` 有正确核心事实但完整性不足；`kl-t2` 错误澄清；Harbor 的重复补槽由确定性代码缺陷触发。
- 能确认：输入文件、候选 64 个源码/配置文件和 496 个 raw 文件分别有身份校验；所有失败、阻断、未知用量保留。原资料文本与检索片段另做 [15 项偏移/hash核验](assessment/retrieval-integrity-new-four.json)，语义判断仍来自逐条阅读。
- 不能确认：修复 Harbor 补槽问题后模型一定会在剩余时间内交付；本轮并未做该修复后的外部模型重跑。
- 不能确认：这些样本的表现可推广到一般问题、真实流量或线上准确率；也没有通过加预算、加超时或挑选成功样本补过关。

运行后确认的 coverage 修复属于 **post-evaluation 源码与离线验证**。它不是上述 `dd225edf` 真实推理成绩，不能覆盖原失败或继承本轮已验证声明。主服务暂保留上一候选，本轮候选保留待后续验收。

## 证据身份

| 证据 | SHA-256 |
| --- | --- |
| [v2 输入冻结](input-freeze.json) | `c244cad112ca3b6ce74a08a161d7b1da4b5cf4cc16cd13053b237a2e0e146469` |
| [原始采集 seal](runs/round6-once/raw-seal.json) | `e744c169e6b7cefd343761718dc3d469fce743dfea3dbd81ef2675f487883cff` |

过程中的工程难题、排除项及下一步见 [execution-notes.md](execution-notes.md)。
