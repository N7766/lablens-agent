# Round 8 原 Cedar / Harbor 六轮语义评分

候选 `141a9186dbd187a4621d78169f212c67c78eeb87`，输入冻结 `94d640750e66a8e9d5332da3da64f774b4828834ba2197bc4fafe60578037822`，raw seal `39645ea4e213c7da0c4b43ff42b09157558638413d26a45c33813483064a86f2`。本评分员参与本轮有限绑定模块开发，先读原 gold，封存后才读本轮最终回答、引用与轨迹；在提交此评分前未读其他评分员的本轮阅读意见。属于非盲、开发团队 AI 评分，不是独立人工评测。

沿用[原六轮 rubric](../../conversation-round5/development/gold/rubric.json)，不改问题、gold、失败样本或 strict 门槛。逐条结果见 [old-annotations.json](old-annotations.json)；其中 **388 个证据指针**已按冻结文件哈希及 JSON 路径校验存在。指针有效本身不等于语义正确，以下判断来自逐条阅读。

## 结果

| 轮次 | 交付 | 完整性 | 引用蕴含 | 检索覆盖 | 无错误拒答 | 指代 | 来源范围 | Strict |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| cedar-t1 | 事实报告 | fail | fail | pass | fail | pass | pass | 否 |
| cedar-t2 | 事实报告 | fail | pass | pass | pass | pass | pass | 否 |
| cedar-t3 | 必要澄清 | pass | N/A | N/A | pass | pass | pass | 是 |
| harbor-t1 | review 超时，无报告 | fail | blocked | blocked | fail | fail | fail | 否 |
| harbor-t2 | 前置失败，未提交 | fail | blocked | blocked | fail | fail | fail | 否 |
| harbor-t3 | 前置阻断，未提交 | fail | blocked | blocked | fail | fail | fail | 否 |

严格通过 **1/6 计划轮、1/3 已交付可评分轮**，为 cedar-t3。共交付 **2 份事实报告、1 次必要澄清**，执行失败 1 轮、依赖阻断 2 轮。不得把执行失败轴上的 fail 读成“未发布草稿所有事实错误”。同样，cedar-t2 的 strict 失败是原 gold 细节遗漏，核心两项结论正确。

Round 7 原六轮 strict 2/6、交付3/6；本轮交付总数相同，但通过的是不同场景。本轮不能宣称六轮总体质量提高，不能把先前 Cedar 不交付变成当前有报告包装为全部验收完成。

## Cedar 首轮：覆盖交付恢复，验收又被错误降级

[cedar-t1 原始任务](../runs/round8-once/cedar-t1-final-task.json) 已保存8行报告。`result.agent.trace[18]` 显示两个总判定已被识别，`added_review_slots=[]`，没有复现上一轮“含 C-21 的已写总判定被重复补槽”的容量失败。两次 decision 加一次 review 正常结束，但最终质量仍未过：

- `assessments[0:2]` 给出240→208与240→210及13.3%/12.5%，算术正确。两行只给百分比，没有说明对12.5%阈值分别达标。用户本轮明确要求逐项达标，不能靠模型内部草稿或引用原文替它完成解释。
- `assessments[2:4]` 正确判断最大温度5.2不达标、5.0等号达标；但两行都只引用签字测量块。该整份读数文档没有批准门槛，规则块虽被其他行引用，不能补足本行门槛的引用蕴含。
- `assessments[4]` 的 Dune 总判定标为不足，因旧数值规则认定本行解释的13.3%没有字面引文或本行显式计算式。`assessment_validation.selected_evidence_checks.withheld_originals[0]` 保存原解释“虽节电13.3%≥12.5%达标，但最大温度5.2>5.0，两项需同时满足”。该数值可由240/208推导，且前面一行已算出，但当前检查按行处理并要求显式公式。**最终可见回答没有发布这个解释，不能把审计原文当作已答对。**
- `assessments[5]` 的 Reed 总判定也标为不足。草稿 `trace[16]` 已把这一验收规则判定归为 `causal_claim`；最终 `assessment_validation.adjustments[0]` 触发既有 `causal_claim_requires_external_controlled_review`。判断是否满足规则不是在认证因果机制，因果护栏因此误降级了可回答的验收结论。

本轮新 `evidence_binding.findings=[]`，上述两条降级来自既有数值与因果机制，不能据此归因新对象检查。最终仍限定C-21并排除C-08，没有扩大部署/成本主张；因此来源范围与指代通过，整体完整性、引用与错误拒答失败。五条 mandatory 都是多个子条件的合取，标 fail 不表示其中每个数值都错。

这些记录能确认**输出如何被改成不足**；不能确认调整提示、改变检查或延长时间之后模型就会全部答对。没有运行该反事实。

## Cedar 第二轮：核心正确，严格细节边界单列

[cedar-t2 原始任务](../runs/round8-once/cedar-t2-final-task.json) 的两条命题清楚区分：

1. Dune 全部通过为 `contradicted`：最大5.2高于批准5.0，测量与规则同时引用。
2. 月运行费低于 Reed 为 `insufficient`：成本说明明确未收集电价、维护费、设备租赁价格，无法计算比较。

实际检索包括所有必要来源，最终保持更正后的 Dune 和C-21作用范围，没有用成本缺口拒绝已能判定的第一项，也没有借C-08的旧优先结论。

**评分边界有争议，不能隐藏：** 原 `mandatory[0]` 还写明“despite energy reduction passing”。最终未提节电达标，而严格规则要求每个 mandatory 全部满足，所以本评分给 completeness fail、strict不通过，并把附加诊断记为 **core_conclusion=correct、completeness_gap=gold_specific_detail**。如果从用户明确问到的两条主张是否正确判断角度看，此回答已经实质正确；这不等于可在本轮事后放宽原 gold。引用中存在240/208、前轮也曾算出13.3%，不替最终文字补上这项 gold 细节。

C-08 的排除按实际未使用旧结论认定，无须重复用户禁令；不是机械要求答案必须出现“排除C-08”字样。

## Cedar 第三轮：必要澄清通过

[cedar-t3 原始任务](../runs/round8-once/cedar-t3-final-task.json) 没有擅自沿用旧 Dune focus。最终询问需要 Dune 和 Reed 的全部结论还是仅其中之一，`result.clarification.choices` 明确为 `Dune/Reed`，并已在[第三轮会话](../runs/round8-once/cedar-t3-conversation.json)持久化。

它问的是“双对象还是单对象”，并非一句直白的“Dune还是Reed”；结合明确选项与未选择任何单一签字对象，满足原 gold 的先消歧语义目的。没有新的事实主张，实际检索与引用为空，原规则允许两个证据轴 N/A。这一次通过不能当作新事实检索或模型一般指代能力证明。

## Harbor：真实超时，不用草稿补分

[harbor-t1 原始任务](../runs/round8-once/harbor-t1-final-task.json) 的 `error_result.agent.trace[8]` 实际检索到R-17规则、签字读数、显示范围与R-09历史材料。`trace[16]` 有8行原稿，`trace[18]` 为零补槽；但没有最终报告：

- 两次 decision 分别约5.03秒和39.19秒。
- `trace[21]` review 开始时剩余约30.75秒，请求体26,166字节。
- `trace[22]` review 约31.32秒后以 timeout 结束，未收到响应，provider acceptance与token未知，剩余预算为0。
- [会话记录](../runs/round8-once/harbor-t1-conversation.json)保存FAILED且answer为空；后两轮在[run-summary](../runs/round8-once/run-summary.json)中明确阻断，均未提交。

可确认已有检索、已写草稿、coverage没有再补槽、review耗尽剩余时限；不能确认内部草稿经过review后会正确，不能把provider未知token写为0，也不能单次观察便把超时归因于新增提示体积。任务总预算与provider响应耗时共同形成这次时限结果，网络/排队/模型生成的各自贡献没有独立测量。

无最终答案时，原 rubric 规定引用和检索轴 blocked，其余轴 fail；本报告同时记录内部检索覆盖，避免把正式blocked误读成“确实没检索到资料”。Harbor t2/t3保留计划分母且语义标签not_evaluable，不以未运行当通过。
