# guard-final-replay：逐句语义复核

复核者为另一位 AI 助手，参与过本项目架构审查及早期界面开发，知道旧失败与迭代背景。不是盲审、外部独立作者或人工评审。本次没有模型 API 调用，也没有读取独立盲测目录。

范围：4 条保存结果；pass 1、partial 3、fail 0。该计数只描述这些已知开发案例，不是一般成功率。
原 h01、d01 两道开放排查题继续保持 partial；定向题不替代开放题的完成度。

- 结果 SHA-256：`a8e4fffe4ebae52fb1b4b540d464a08e7c2041d0e126e260be0cc7b53e3e5d9d`
- 固定输入 SHA-256：`eca48810a4d336bc33069b2cb7f0a9930d389277fa4f7ce8a6fc3562ed267dc5`
- 元数据 SHA-256：`9954a896e7fd76729dd5911a93df9e7cd5fb6358105797a6aaca0d4fad084b0e`

判断同时阅读命题、解释和该题全部8份固定资料；引用存在、verdict 标签与哈希通过均不作为语义正确性的证明。逐文档哈希、逐行结果哈希及完整评语见同目录 JSON。

## h01 — partial

Stage 2 的奖励准确率几乎满分，为何答题仍很差，先查哪些证据？

事实摘要基本正确，但开放式排查请求被错误地当成必须有现成优先级文档的问题，未交付可执行排查顺序。保留 partial。

1. **supported** — Stage 2的奖励准确率接近满分但答题准确率差
   stage2:results 给出 reward accuracy=0.9913 和自由生成 validation accuracy=26.62%，stage2:summary 给出123/462，与表述一致。
2. **supported** — 答题行为退化表现为输出格式漂移
   stage2:results 确实记录重复模板、错误的早期 Answer 行和无关续写；只能当作该报告记录的定性现象。
3. **supported_with_scope_caution** — DPO训练未保留自由生成的解题行为
   解释明确归于 Stage 2 报告结论，且报告原文如此；不能外推所有 DPO，也不能据此证明某个优化变量是唯一原因。
4. **over_refusal_and_incomplete** — 应先检查评估脚本和生成参数
   用户要排查建议，不要求资料已有一份优先级清单。固定资料已提供评估实现、max_new_tokens=128/batch_size=4、答案提取和 LoRA 训练入口，可据此提出明确标注为建议的核查步骤。回答没有做。

- 未完成：未形成核查评估参数和答案抽取、逐例检查格式漂移、对齐模型起点/提示与预算、再比较受控变量的具体步骤。
- 边界：本判断只依据该题8份固定资料，不认证完整项目历史或模型权重身份。

## d01 — partial

Stage 4 preference accuracy 是100%，但自由生成只有33.12%，如何定位退化？

已正确保留偏好与自由生成指标、摘要参数和历史权重身份边界，但没有完成如何定位退化的开放式请求。保留 partial。

1. **supported** — Stage 4偏好评估准确率为100%
   stage4:training 确实记录 Preference eval rewards accuracy=1.00。
2. **supported** — Stage 4自由生成准确率为33.12%
   stage4:summary 为153/462=0.33116883116883117，stage4:results 为33.12%；解释数字正确。引用表中的 correct 行本身不是百分比，但所引完整文档及另一条summary给出必要信息。
3. **appropriate_abstention** — 自由生成与偏好评估使用相同模型但任务不同
   相同名称/目录不足以认证历史权重相同；最终解释保留这个边界，未把“使用相同模型”作为已确认事实。不过两种评价任务不同这一较窄事实没有单独展开。
4. **supported_as_recorded_metadata** — 自由生成评估使用max_new_tokens=128、batch_size=16
   summary 明确记录 max_new_tokens=128、batch_size=16。这支持报告记录的参数，不能替代历史执行的独立观测。

- 未完成：未利用 stage4:results 中的 SFT 对照439/462、95.02%提出对照核查；不属于再次谎称SFT不存在，但答案仍不完整。
- 未完成：未给出可执行的退化定位顺序，未明确列出哪些归因需要新增受控实验。
- 边界：protocol:final-test 是计划，不是已完成测试结果；该回答未将其冒充实测。

## h01-targeted — pass

根据 Stage 2 配置 precompute_ref_log_probs: false，能否确认历史运行每一步实际重算参考模型概率？请区分配置记录与实际运行事实。

最终展示正确确认配置 false，同时拒绝从配置/参数透传推断历史每一步重算。

1. **supported_with_correct_scope** — 引用配置记录 precompute_ref_log_probs: false
   配置原文明确写 precompute_ref_log_probs: false。最终说明仅确认配置记录而不认证历史生效，既未错误拒答配置事实，也未将其推断成运行事实。
2. **appropriate_abstention** — 配置 precompute_ref_log_probs: false 表示训练时每一步都重算参考模型概率。
   给定 impl:dpo-args 仅将布尔值传入参数；没有每一步执行的 TRL 内部实现或历史观测。该命题与 explanation 一起阅读是证据不足，而非肯定每步重算。
3. **appropriate_abstention** — 历史运行实际是否每步重算参考模型概率，取决于配置是否被执行。
   没有运行日志、实际依赖版本及启动参数来认证逐步重算。最终解释没有继续夹带“机制已证明动态重算”的肯定结论。

- 边界：这是保存回复的确定性规则重放；不能把最终展示正确归功为模型重新推理或同模型自我纠正。

## d01-targeted — partial

有人说资料没有 SFT 自由生成基线，这个说法是否成立？请核对基线数值，并判断这些比较能否证明过拟合是唯一根因。

已纠正“SFT基线不存在”，数值和不可确认唯一根因的主要边界正确；显著性与证据缺失措辞仍不够严格，不能记无保留通过。

1. **supported_with_scope_caution** — 资料没有 SFT 自由生成基线的说法不成立，Stage 3 LoRA SFT 的准确率 95.02% 作为基线存在
   stage4:results 清楚列出Stage 3 LoRA SFT、439/462、95.02%，并注明同一462题验证集。可称描述性比较基线；“有效基线”不得引申为匹配训练起点的因果对照。
2. **needs_qualification** — Stage 4 相比 Stage 3 准确率从 95.02% 下降到 33.12%，性能显著下降
   95.02%→33.12%及下降61.90个百分点正确，但“性能显著下降”“差异显著”未区分数值幅度与统计显著性。固定资料没有显著性检验，不能当统计结论。
3. **correct_boundary_imprecise_absence_claim** — 仅凭性能下降不能证明过拟合是唯一根因，缺乏归因分析
   不能确认过拟合为唯一或主导原因是正确的。泛称“未提供误差类型分析、训练动态或控制变量实验”应限定为这8份资料未提供足以归因的证据；其中stage4:training确有训练结果摘要，不能让读者误解为没有训练证据或整个项目未做过分析。
4. **appropriate_abstention** — Stage 4 使用 DPO 而非 SFT，训练方法改变可能导致性能变化
   full_dpo 的配置事实存在；“方法改变可能导致变化”仅为假设。最终规则说明不认证因果，并要求匹配起点和评估条件的受控对照，边界正确。

- 边界：baseline存在不等于matched causal control；该组核心修复成立，但输出精度问题保留。

旧失败报告和原始结果均保留未改。没有按本复核更换样本、重跑模型或把规则重放计作新推理。
