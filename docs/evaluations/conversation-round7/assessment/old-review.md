# Round 7 旧六轮：开发侧语义评阅

候选 `619c05122acc092241596c6f1a2f20b72ce18fcd`。raw seal `9054319d72f861c077b4184c071e5de6c8f2644689078363b1d607e255cefde1`。评阅者参与过覆盖器实现，因此是**已暴露、非盲的 AI 开发回归评分**，不是独立人工评测。逐项标注见 [old-annotations.json](old-annotations.json)，其中 351 个证据指针引用均经冻结评分器解析验证；指针可定位不等于语义自动正确。

## 结果

| 轮次 | 交付 | 严格结果 | 核心原因 |
| --- | --- | --- | --- |
| cedar-t1 | 无响应，协议错误 | 不通过 | 八行稿已有试验总体通过/不通过命题，覆盖器仍补两槽后停止，未调用 review |
| cedar-t2 | 依赖阻断 | 不通过，留在分母 | 前轮没有有效持久化回答；本轮未提交 |
| cedar-t3 | 依赖阻断 | 不通过，留在分母 | 前轮未执行；本轮未提交 |
| harbor-t1 | 事实报告 | 不通过 | 大部分结论正确，但 Sable 专属总判定解释和测量引文实际指向 Quill；两个通过分项缺明确判定 |
| harbor-t2 | 事实报告 | 通过 | Sable 时延支持、成功率命题反驳、停电最大值不足三种状态分别正确 |
| harbor-t3 | 纯澄清 | 通过 | 询问 R-17/R-09 哪次记录，没有自行选择；原 gold 允许纯澄清证据轴不适用 |

严格 **2/6（计划）**、**2/3（已交付且评阅）**；交付 **3/6**，其中 **2 个事实报告、1 次必要澄清**。未交付按照原 rubric 将 semantic completeness / false refusal / referent resolution / source scope 记 fail，citation / retrieval 记 blocked；mandatory 为 blocked，forbidden 为 null，补充语义诊断为 not_evaluable。不能把这些约定解读成未提交的两轮已经实际答错。

## Cedar：草稿可用不等于用户得到答案

[原始任务](../runs/round7-once/cedar-t1-final-task.json) 中：

- `error_result.agent.trace[8].output` 实际返回 C-21 批准规则、公式、AND、包含等号阈值，以及 baseline=240、Dune=208/5.2、Reed=210/5.0 等完整读数；也有成本/旧试验范围材料。不是“所需材料未检索”的失败。
- `trace[16].arguments.assessments[6:8]` 已含“Dune未通过C-21验收”“Reed通过C-21验收”。这是原模型草稿，不能拿它替代最终答案评分。
- `trace[18].output` 仍认定两个总体槽缺失；由于原稿八行而明确 `coverage_contract_capacity` 停止。本轮未追加决策调用，也没进入 review。
- [会话记录](../runs/round7-once/cedar-t1-conversation.json) 中任务失败、没有 answer；依赖两轮保留 blocked。

因此确认的问题是有限整体谓词识别没有覆盖“通过 + 试验 ID + 验收”的自然表述，不能由此反推去掉该检查就必然能够按时、正确交付。

## Harbor t1：不能只看前面正确段落

[最终答案](../runs/round7-once/harbor-t1-final-task.json) 的 `result.assessments[0:5]` 正确提供：198/200=99%、395/400=98.75%、150/149 秒、AND 与严格/包含等号阈值、两者总体不通过、dashboard 整数显示不能覆盖原始计数。

但是最后的 `result.assessments[5]`：

- statement 是“Sable 的本次验收总判定。”；
- explanation 是“Quill 因中位时延为 150 秒……而未通过验收。”；
- 所选 measurement quote 也是 Quill 150 秒，而原表的 Sable 时延为 149 秒。

这构成对象与引用范围错配；同一个 chunk 包含两个对象不能证明选中的 Quill 句子支持 Sable 专属结论。citation_entailment、referent_resolution、source_scope 失败。正文前面的正确“两者均未通过”没有消除后面独立段落的问题。

另按用户“分别检查”的要求，Quill 成功率通过和 Sable 时延通过没有被明确说出，仅提供数值与规则。因此两个 mandatory 标 fail、补充完整性标签为 explicit_question_omission。**这是可讨论的完整性边界**：如果复核者认为数值与规则合起来已经隐含表达通过，应并列记录分歧；即使这两项改为通过，最后一条明确对象错配仍独立使 strict 失败。

过程上，原草稿 `trace[16].arguments.assessments[3]` 已写“Quill 不通过验收，Sable 也不通过验收”，`trace[18]` 却只给 Sable 再加槽。该冗余槽是错误段落的载体；不能把“去掉它后模型必然正确”当作已经验证。

## Harbor t2 / t3：成立的通过边界

[t2 最终答案](../runs/round7-once/harbor-t2-final-task.json) 针对更正后的 Sable 给出 149<150 的支持；395/400=98.75%<99 的反驳；停电最大恢复时间限于 R-17 证据包缺失的不足结论。所选引文分别对应签字表、批准规则和资料范围说明，没有把 nightly median 或 R-09 值替换进去。三类证据状态都保留，缺资料没有引发整体拒答。

[t3 最终答案](../runs/round7-once/harbor-t3-final-task.json) 只询问 Night Restore R-17 或 Archive Restore R-09，实际 `hits/searches` 与 `assessments/citations` 都为空。原 gold 明确允许这种必要澄清不检索不引用；这里的通过是消歧流程通过，不是额外事实报告，更不能把 `chunk_count=4` 当成本轮实际召回四块。

全部原始模型输出、题目和 gold 保持不变；本说明没有触发任何模型请求。
