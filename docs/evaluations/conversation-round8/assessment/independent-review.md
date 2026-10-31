# Round 8 独立阅读复核（开发团队 AI 内部）

本复核在读取本轮其他评分者的标注之前完成，直接阅读 7 份已交付回答、1 份失败任务、全部 10 轮计划、引用正文、检索轨迹及原 gold。复核者参与评测工具开发，因此这是内部交叉审查，非人工、非盲、非开发独立评测。未修改问题、gold、raw、候选或运行预算，未追加模型调用。

候选 `141a9186dbd187a4621d78169f212c67c78eeb87`；输入 SHA `94d640750e66a8e9d5332da3da64f774b4828834ba2197bc4fafe60578037822`；raw seal SHA `39645ea4e213c7da0c4b43ff42b09157558638413d26a45c33813483064a86f2`。已校验全部 583 个 raw 文件以及执行前后源码和输入身份。

## 交付与可评分边界

10 轮计划中提交 8 次：7 个任务成功，Harbor t1 超时，Harbor t2/t3 按依赖阻断。逐条阅读确认是 **6 份事实报告 + 1 次必要澄清**。`kl-t2` 的内部 status 为 `refused`，但它实际给出历史重试和原因的有界不足结论，应分类为 fact_report，不能仅凭 status 计作纯澄清或无回答。

- `raw:run-summary.json#/turns`
- `raw:cedar-t3-final-task.json#/result/answer`
- `raw:kl-t2-final-task.json#/result/answer`

本文件记录独立判断和评分边界，不用“引用存在”或模型自评 supported 代替语义正确性，不合并两套原 rubric。

## 逐轮阅读

| 轮次 | 独立阅读结论 | 对严格评分的影响 |
| --- | --- | --- |
| cedar-t1 | 数值计算和最大温度方向正确，但两项整体验收均被错误降为不足；两个节电数值也未在最终回答里显式判定是否过 12.5% 阈值。 | 明确漏答、部分错误拒答；strict 不通过。 |
| cedar-t2 | 对象已改成 Dune；“所有条件通过”被反驳，月运行费不足，分别解释 5.2 > 5.0 和成本缺项。没有移用 C-08。 | 核心判断、引用及有界不足正确；支持通过。若把 mandatory 的背景性节电通过要求解释为必须重复，应保留这一阈值争议。 |
| cedar-t3 | 明确询问需要 Dune、Reed 两者还是其中之一，没有代用户选定对象或生成签字结论。 | 必要澄清；支持通过，纯澄清的检索/引用不适用。 |
| harbor-t1 | 已检索规则与结果，进入 review 后超时；没有最终报告。 | 按原 gold 记执行失败，不给草稿打答案分。 |
| harbor-t2 | 前置 Harbor t1 没有有效持久化回答，未提交。 | 阻断，保留计划分母。 |
| harbor-t3 | 同上。 | 阻断，保留计划分母。 |
| ar-t1 | Cedar 2.4 的配置总尝试 2、重试 1、4xx 不重试及旧培训材料冲突方向正确；没解释 default=1 与部署 override=2 的优先关系。 | 原 gold 的完整性未满分；“次数为”缺少“最多”有措辞边界，不能据此直接指称编造历史执行。 |
| ar-t2 | 新对象 Juniper 1.8；三项比较清楚：2→3、1→2、4xx 不变；引用重新检索的注册表及两个版本政策。 | 已修复上一轮核心错误拒答。gold 要求显式排除 2.4 default/Cedar override 的说明并未逐字出现，需由原 gold 审核其语义是否已由当前 1.8 适用范围充分覆盖，不能因改善自动记满分。 |
| kl-t1 | 当前 2 次重试/3 次尝试、历史未知、徽标“3”与最终成功不能证明重试均正确。 | 原 gold 还要求 transport-only、配置时间晚于运行且非运行快照、最小所需证据，这些未完整进入回答；strict 不通过，但不能说核心结论全部错。 |
| kl-t2 | 用户更正归属明确，历史是否至少重试与上游限流原因均判不足，并给出所需记录；但额外重复两条当前配置并降为不足，第一条解释拿历史记录缺失去回答当前配置。未明确重申最终 SUCCEEDED 与此前失败尚未建立。 | 核心历史问题基本正确；跨范围解释、额外错误拒答、原 gold 完整性仍失败。配置行未再以 supported 发布，不代表其解释和引用已正确。 |

### Cedar：覆盖修复交付了报告，但保护规则仍会误伤可算结论

`cedar-t1` trace 16 的原草稿已经有 8 行、Dune 未通过和 Reed 通过两行，coverage trace 18 无额外补槽。两行原草稿均被模型标成 `causal_claim`。模型 review 后，Dune 的解释为“虽节电 13.3% ≥ 12.5% 达标，但最大温度 5.2°C > 5.0°C，两项需同时满足”；数量检查将这行的 `13.3%` 判为不在所选引文中并保留 withholding 原文。它实际上可由已引用 240、208 和公式推算，这不是资料缺失。Reed 这一行随后被 causal-claim 规则降为不足，要求额外受控因果评阅；C-21 规则验收本身不是需要证明机制因果的结论。

这条证据链只能确认本次**发生了什么**：源材料充分、覆盖槽不再溢出、review 成功返回，而最后发布内容被后处理降级。不能从一次采集证明模型为何选 causal_claim，也不能断言新提示导致该标签。

- `raw:cedar-t1-final-task.json#/result/agent/trace/16/arguments/assessments/4`
- `raw:cedar-t1-final-task.json#/result/agent/trace/16/arguments/assessments/5`
- `raw:cedar-t1-final-task.json#/result/agent/trace/18/output`
- `raw:cedar-t1-final-task.json#/result/assessment_validation/selected_evidence_checks/withheld_originals/0`
- `raw:cedar-t1-final-task.json#/result/assessment_validation/selected_evidence_checks/findings/0`
- `raw:cedar-t1-final-task.json#/result/assessment_validation/adjustments/0`
- `raw:cedar-t1-final-task.json#/result/assessments/4`
- `raw:cedar-t1-final-task.json#/result/assessments/5`

温度行也存在引用范围问题：最终第 3、4 行声称 5.0°C 阈值与等号判定，却只选测量表，其本身不规定阈值；规则虽在别的行引用，逐条引用仍需评阅。原 gold 要求每个实质规则判断由对应引文支持，不是全文出现过规则就自动正确。

- `raw:cedar-t1-final-task.json#/result/assessments/2`
- `raw:cedar-t1-final-task.json#/result/assessments/3`
- `input:docs/evaluations/conversation-round5/development/gold/rubric.json#/scoring_rules/2`

### Harbor：同一 75 秒边界内，review 仍会超时

Harbor t1 的两次 decision 分别耗时 5,034.416 ms、39,188.742 ms；review 在 elapsed 44,248.445 ms 发出，允许 30,751.810 ms，实际观察至 75,565.146 ms 时 timeout。review 请求体 26,166 bytes，provider 是否接受和 token 都未知。没有最终回答，也没有后继两轮的新模型请求。

这证明本轮的端到端边界和失败依赖行为，不能证明超时因输入长度、网络、排队或模型生成中哪一项造成；当前无服务端分段遥测或受控消融。不能拿未交付的 Harbor 草稿声称对象错配已在线修好。

- `raw:harbor-t1-final-task.json#/error_result/agent/trace/3`
- `raw:harbor-t1-final-task.json#/error_result/agent/trace/12`
- `raw:harbor-t1-final-task.json#/error_result/agent/trace/21`
- `raw:harbor-t1-final-task.json#/error_result/agent/trace/22`
- `raw:run-summary.json#/turns/4`
- `raw:run-summary.json#/turns/5`

### Alder：比较从错误拒答变为有依据的回答，但完整性仍需原标准评阅

ar-t2 最终 7 行中第 5–7 行明确给出三个变更/保留比较，均引重新检索的本轮资料。该轮保留上一轮上下文，未 drop，不能把改善归于“找回之前丢失的历史”。原先“对象不同所以无法比较”的拒答未出现。修改对象后仍保留一条不必要的培训材料讨论；它并未替代三个明确子问。

- `raw:ar-t2-final-task.json#/result/assessments/4`
- `raw:ar-t2-final-task.json#/result/assessments/5`
- `raw:ar-t2-final-task.json#/result/assessments/6`
- `raw:ar-t2-final-task.json#/result/retrieval/searches/0`
- `raw:ar-t2-final-task.json#/result/context`
- `input:docs/evaluations/conversation-round5/new-scenarios/frozen/gold.json#/turns/ar-t2`

ar-t1 的数字应按问题中的“最多”和配置注册表的 configured limits 语境理解；最终文字写“次数为”，确实欠清楚，但没有断言具体历史请求实际执行。最确定的缺口是未解释 2.4 默认 1 与 Cedar 覆盖 2 的关系，保留该事实比把语气不严谨说成历史幻觉更准确。

- `raw:ar-t1-final-task.json#/result/answer`
- `raw:ar-t1-final-task.json#/result/assessments/0/quotes/0`
- `input:docs/evaluations/conversation-round5/new-scenarios/frozen/gold.json#/turns/ar-t1/required_conclusions/2`

### KiteLoad：同模型 review 降级了错误配置引用，但没有完成正确的报告修订

kl-t2 trace 16 原草稿的两条 current_configuration 行仍把 `max_retries=2` 解释配到告警卡 `paf81cfc1f90b1fb6`，本轮检索只有 alert/receipt/inventory，没有 config。最终 review 已把两条降为 insufficient，故这次不能再说最终回答把告警卡当作支持配置值的证据；但第一行 basis 又改为 historical_observation，解释历史行为缺证，仍答非所述，第二行也未必要地拒答当前配置。有限 literal binding 的 findings 为空，因为该检查范围只包括肯定的 current_configuration scalar，这些最终不足行属于 unverified，空 findings 不是通过语义检查。

历史两项本身正确地保留未知，最小证据也比上一轮明确：权威重试计数/关联尝试记录，以及对应上游响应/关联失败记录。仍没有明确说明成功回执的已知事实与失败前提尚未建立，不能把“原因未知”写成“已确认一次失败”。

- `raw:kl-t2-final-task.json#/result/agent/trace/16/arguments/assessments/0`
- `raw:kl-t2-final-task.json#/result/agent/trace/16/arguments/assessments/1`
- `raw:kl-t2-final-task.json#/result/retrieval/hits`
- `raw:kl-t2-final-task.json#/result/assessments/0`
- `raw:kl-t2-final-task.json#/result/assessments/1`
- `raw:kl-t2-final-task.json#/result/assessments/2`
- `raw:kl-t2-final-task.json#/result/assessments/3`
- `raw:kl-t2-final-task.json#/result/assessment_validation/selected_evidence_checks/evidence_binding`
- `input:docs/evaluations/conversation-round5/new-scenarios/frozen/gold.json#/turns/kl-t2/required_conclusions/2`

kl-t1 的已知核心结论均合理，严格 gold 对 transport-timeout、配置时间与最低记录要求更细；这些未进入最终答案，应区分 gold 细节遗漏与本轮明确询问的历史问题答错。

- `raw:kl-t1-final-task.json#/result/answer`
- `input:docs/evaluations/conversation-round5/new-scenarios/frozen/gold.json#/turns/kl-t1`

## 用量与结论边界

独立按 sealed request lifecycle 逐请求相加，未调用 runner 的用量帮助函数：**22 次尝试 = 15 decision + 7 review**；已知 token 下限 **100,686 = 93,645 prompt + 7,041 completion**。Harbor t1 的 review token 与接受状态未知，不能把下限称作全部 token 或完整费用。与 aggregate 及运行汇总一致，未观察 embedding、任务重试、缓存复用或额外 repair call；详情见 [独立调用审计](independent-call-audit.json)。

本轮改善能明确落在 ar-t2 的三项有依据比较，以及 Cedar 覆盖不再在提交前溢出。仍需修复验收判断与因果类型混淆、算术结果被 literal 数量检查误伤、引用缺规则前提、与当前问题无关的配置行和 review 超时。单次八任务、十个计划轮的已暴露合成回归，不能支撑生产吞吐或通用准确率结论。
