# Round 7 内部 AI 语义复核

复核者是同一开发团队的另一 AI；先独立阅读本轮封存原答、实际来源与轨迹，再核对其他 AI 的标注。题目、gold 及前次缺陷均已暴露，因此不是盲评或独立人工评测。未调用外部模型，未改源码、原始资料或评分。

## 执行与用量核对

[独立调用审计](independent-call-audit.json) 逐任务核对 request_started/request_finished 的一一对应、provider 接受、分项 token 和 aggregate：

- 10 个计划轮次、8 次提交、1 个失败、2 个依赖阻断、7 个持久化响应。
- 21 次模型尝试 = 15 decision + 6 review；每个请求均有已知返回用量，无未闭合请求或未知接受状态。
- 90,140 token = 83,382 prompt + 6,758 completion，各任务生命周期分项与 aggregate 完全一致。它们是 provider 报告的 token，不是独立账单核验；这里不换算美元成本。
- 7 个响应实际为 **6 份事实报告 + 1 次澄清**（harbor-t3），不是七份通过质量验收的报告。
- 0 embedding、无任务缓存复用；run summary `fatal=null`，冻结输入和候选身份均保持。

## 先于他人标注形成的判断

| 轮次 | 原 gold strict 初步判断 | 语义依据 |
| --- | --- | --- |
| cedar-t1 | fail | 8 行原稿已有两个总判定，但词法漏认“通过C-21验收”，重复补槽超过容量，最终无回答；不是本次超时。 |
| cedar-t2/t3 | 未交付/依赖阻断 | 不能读取旧答案替代；仍留在 6 的计划分母。 |
| harbor-t1 | fail | 主要计数、阈值、总体判断正确；最后 Sable 总判定行解释 Quill 的 150 秒失败，主语错位。至少引用蕴含和范围不能通过。 |
| harbor-t2 | pass | Sable 149 秒达标、98.75% 反驳达标、停电最大时间未知均分开；使用本次 R-17 资料，无错误全拒答。 |
| harbor-t3 | pass | 明确询问 R-17 或 R-09，未擅自选择或混合两次记录；纯澄清适用原 rubric 的例外。 |
| ar-t1 | fail | 2 总/1 重试、4xx 不重试、培训来源冲突正确；最终回答没有解释原 gold `ar1-c3` 要求的 default 1 与 Cedar override 2 的关系。引用正文有该句不等于回答了该项。 |
| ar-t2 | fail | 新目标值正确，但三项补槽都以“对象不同”拒绝可由已检索来源完成的比较。 |
| kl-t1 | fail | 当前 2/3 和历史重试未知正确；原 gold 要求的 transport-only、配置晚于历史运行/非历史snapshot及最低证据要求未明确完成。保留原 gold 的已知较细要求。 |
| kl-t2 | fail | 相较旧纯澄清，历史未知、因果不足、用户归属已有实质回答；但额外配置两项错误引用 alert，最小证据仍不完整。 |

因此初步严格结果是旧六轮 **2/6**、原四轮 **0/4**。两套 rubric 不同，不能合并成准确率。交付从 Round 6 的 4/10 到 7/10不代表语义同比改善：原四轮 strict 从 1/4 变为 0/4；旧六轮从 0/6 回到 2/6，仍未超过 Round 5 的 2/6。

## 检索与引用必须分开

- Cedar 实际工具输出命中批准规则和完整签字测量（还含范围说明与被替代 C-08）；错误对象里顶层 retrieval 被裁掉，不能因此说没检索。冻结轨迹中 `search_workspace.output.hits` 保留 5 块。按原 rubric 无最终答案的检索轴仍 blocked，此处只提供失败诊断。
- Harbor t1/t2 均命中 R-17 rule/results/scope，t1 还收到被排除 R-09；t3 为明确对象澄清无需检索。
- Alder t1 命中 current/deployments/training（及 legacy），t2 命中 legacy/deployments/current，满足必要检索覆盖。ar-t2 缺比较不是检索缺口。
- KiteLoad t1 命中 config/run/alert/retention；t2 命中 run/retention 和可选 alert，满足本轮历史问题的必要检索范围。但 kl-t2 自己新增当前配置断言，其所选 alert 引文不能支持这些断言。必要检索覆盖通过与引用蕴含失败可以同时成立。

详细失败原因、排除项和仍属假设的解释见 [失败复核](failure-review.md)。

## 与其他评分者的核对

已读取 [旧六轮标注](old-annotations.json) 和 [原四轮标注](new-annotations.json)，逐项检查 mandatory/claim、各轴说明与所引原文。**严格通过名单一致：只有 harbor-t2、harbor-t3；2/6 与 0/4。** 复核时两份文件 SHA 及比较结果记录在 [annotation-cross-check.json](annotation-cross-check.json)，未修改评分者文件。

有两处维度解释边界需要保留，而不伪装所有阅读判断完全一致：

1. `ar-t1` 评分者因“重试 1 次”一行没有重复“最多”，将核心结论计为 1/2。本复核认为结合紧邻的“传输超时最多尝试 2 次”和原问题，上限语境可以覆盖下一行，因此其核心配置数值可读为正确。措辞确有歧义，但不能把它升级为已断言历史必然重试。无论采用哪个维度判断，default 1 / override 2 的解释确实遗漏，strict 均失败；原分数保留。
2. `harbor-t1` 已给出 Quill 99% 与 ≥99% 规则、Sable 149 秒与 <150 秒规则。是否必须额外逐项写“通过”，是表达完整性门槛；评分者按题目“分别检查”判两项未明确完成。本复核保留这个较严格标注，同时强调最后 Sable 行解释 Quill 是独立、确定的主语/引文错配，足以使 strict 失败，无须依赖门槛争议。

`ar-t2` 错误拒绝跨对象比较、`kl-t2` 的 alert 引用错配和明确最小证据漏答、`kl-t1` 原 gold 的较细要求，均与评分者实质判断一致。评分器能检查标注是否自洽，不能解决上述语义门槛选择；这也是当前小样本 AI 评分的限制。
