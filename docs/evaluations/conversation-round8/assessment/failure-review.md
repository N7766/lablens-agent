# Round 8 封存失败排查

本排查仅阅读 candidate `141a9186dbd187a4621d78169f212c67c78eeb87` 的首次封存结果，并调用相同候选的纯本地函数，对**输入副本**作最小对照。未改源码、raw、gold、预算或评分，未发出额外模型请求，未读取其他评分者的本轮标注。所有副本结果都是机械回放，不能当作模型已经修好。

raw seal：`39645ea4e213c7da0c4b43ff42b09157558638413d26a45c33813483064a86f2`。原始报告、工具轨迹与所选引文都在 [once-only 原始目录](../runs/round8-once/)；逐输入对照和源码哈希在 [failure-replay.json](failure-replay.json)。

## 1. Cedar 的实际失败链

原草稿 `trace/16` 已包含 Dune 未通过 C-21、Reed 通过 C-21 两个总判定，均是 `supported`，但模型把两者的 `basis` 标为 `causal_claim`。coverage `trace/18` 识别到了原有总判定，没有补槽；本轮没有 Round 7 的错误容量拒绝，review 也成功返回。这些事实只证明覆盖与调用阶段已走通，不能给最终答案质量加分。

- `raw:cedar-t1-final-task.json#/result/agent/trace/16/arguments/assessments/4`
- `raw:cedar-t1-final-task.json#/result/agent/trace/16/arguments/assessments/5`
- `raw:cedar-t1-final-task.json#/result/agent/trace/18/output`
- `raw:cedar-t1-final-task.json#/result/agent/trace/22`

review 给 Dune 总判定的解释补进“节电 13.3% ≥ 12.5%”，却没有在**同一条**写计算式。所选规则和测量引用足以给出 `(240 - 208) / 240 × 100 = 13.3%`，但现有量值检查只承认本行显式派生式；它不从另一条已经展示的公式借用推导。因此 `quantity_not_anchored_in_selected_quotes` 记录了 `13.3%`，把 Dune 总判定降为不足，原解释保存在 `withheld_originals`。这不是数学计算错误或资料不存在，而是生成表述与有限校验语法之间的缺口造成可答结论被扣下。

- `raw:cedar-t1-final-task.json#/result/assessment_validation/selected_evidence_checks/findings/0`
- `raw:cedar-t1-final-task.json#/result/assessment_validation/selected_evidence_checks/withheld_originals/0`
- `raw:cedar-t1-final-task.json#/result/assessments/0`
- `raw:cedar-t1-final-task.json#/result/assessments/4`

Reed 的总判定则仍为 supported 进入 `validate_assessments`；其 `causal_claim` 标签触发 `causal_claim_requires_external_controlled_review`，最终要求额外受控因果证据。按既定 C-21 规则对观测值做验收判断并不等于证明机制原因，故本例是错误类型导致的错误拒答。Dune 在此前已降为 insufficient，因此最终日志只显示 Reed 的 causal adjustment；不能据此误认为 Dune 原本没有同一个 basis 问题。

- `raw:cedar-t1-final-task.json#/result/assessment_validation/adjustments/0`
- `raw:cedar-t1-final-task.json#/result/assessments/5`

## 2. 最小离线对照

回放先核验当前 HEAD 与候选一致、68 个源码/配置哈希与全部 raw seal 一致，再运行 `resolve_selections`、`enforce_review_checks`、`validate_assessments`。只改变内存中的输入副本；没有采集或生成新模型答案。

| 对照 | 输入来源与唯一改动 | 数量检查 | causal guard | 最终机械状态 |
| --- | --- | --- | --- | --- |
| 原完整 decision 草稿 | 原 trace 16 选择经已有 passage catalog 解析；无编辑 | 无 finding | Dune、Reed 均触发 | 两个整体结论均不足 |
| Dune review 总判定恢复 | 最终第 5 行的 basis/quotes，配回已保存的 original_verdict / original_explanation | `13.3%` 未锚定 | 前置已降级，不再触发 | 不足，与真实发布状态一致 |
| Dune 只改 basis | 上一副本 `causal_claim → documented_fact` | 仍未锚定 | 无 | 不足 |
| Dune 只补显式公式 | 同行追加 `Dune 的节电百分比为 (240 - 208) / 240 × 100 = 13.3%。` | 通过；有界百分比语法认可原引用操作数 | 触发 | 不足 |
| Dune 同时补公式和改 basis | 两项改动同时应用 | 无 finding | 无 | supported |
| Reed 原 decision 总判定 | 原 trace 16 第 6 行；无编辑 | 无 finding | 触发 | 不足 |
| Reed 只改 basis | 同一原 draft `causal_claim → documented_fact` | 无 finding | 无 | supported |
| 两条温度判断 | 原最终第 3、4 行，仍仅引测量表 | 无 finding | 无 | 两条均 supported，但语义引用仍缺阈值 |

Dune 的恢复不是杜撰 review 原文：只取已经保存的 withholding 原解释和原 verdict；最终 basis 与 quotes 原样保留。Reed 的原 review 解释被因果 guard 替换且未完整保存，因此其对照明确使用 **decision draft**，不冒称恢复了未知 review 内容。全部变更及原文保存在 replay JSON。

对照表说明两个机制分别如何阻断这份具体表述；它不证明只改提示词后模型就会按要求输出，也不证明将 basis 一律改为 documented_fact 是正确修复。真正因果命题仍需要因果边界，不能全局关闭 guard 或把所有因果标签自动改掉。

## 3. 旧 guard 与新增绑定检查不能混为一谈

本轮新增 row-local literal binding 对上述 Cedar 行的 `findings` 为空。它识别 Dune/Reed 名称，但共同引文含两个候选，明确记 `both_names_present_no_predicate_binding`、`unverified`，并未宣称谓词归属已经验证。Cedar 实际触发的是旧 quantity / causal 路径。

回放比较了候选与 Round 7 完成提交 `55f7c3f` 的 AST：`selected_evidence_findings`、`_arithmetic_results`、`validate_assessments` 三个函数未变。新增绑定调用所在的组合函数有变化，因此不能说整个模块未变；也不能据此推断模型此次选择 causal basis 的原因。相关哈希和 AST 比较结果已记录在 `failure-replay.json#/preexisting_guard_checks`。

- `raw:cedar-t1-final-task.json#/result/assessment_validation/selected_evidence_checks/evidence_binding`

同样，温度判断解释中的 `≤5.0°C` 和“允许等于”需要规则引用，单引测量表不能支持阈值关系。数量检查对这种单位/关系没有建立完整证明，引用 exact substring 检查也不会替代蕴含评阅。对照仍机械通过，故下一阶段必须保留这个失败用例，不能以 checks 无 findings 宣称引用正确。

- `raw:cedar-t1-final-task.json#/result/assessments/2`
- `raw:cedar-t1-final-task.json#/result/assessments/3`
- `input:docs/evaluations/conversation-round5/development/gold/rubric.json#/scoring_rules/2`

## 4. Harbor 超时的已确认边界

完整时序见 root 的 [timeout-diagnosis.json](timeout-diagnosis.json) 和本复核的 [逐请求用量审计](independent-call-audit.json)。两次 decision 成功后，review 在第 44.248 秒启动，剩余约 30.752 秒；第 75.565 秒记录客户端 timeout，没有收到 review 响应，provider 接受与 token 未知。后继两轮被依赖规则阻断，没有重试。

本地只能确认请求时序、输入字节和超时边界，不能确认服务端排队、生成耗时、网络问题或是否在客户端超时后继续执行；请求体 26,166 bytes 与超时同时发生不构成因果证据。不能把这个失败改算为语义通过，也不能拿草稿推测最终的对象—引用绑定结果。

- `raw:harbor-t1-final-task.json#/error_result/agent/trace/21`
- `raw:harbor-t1-final-task.json#/error_result/agent/trace/22`
- `raw:run-summary.json#/turns/4`
- `raw:run-summary.json#/turns/5`

## 5. 下一阶段可验证的修复方向

1. 区分按已给规则验收与机制因果命题，用新的正反例检查 basis 选择；保留真正因果问题的未知边界。
2. 让每条派生量包含自身可验证的源操作数与表达式，或缩减不必要的重复派生数值；不要放宽成“某处出现过相同数字就通过”。
3. 将“测量 + 阈值规则”放在同一条所选证据中，加入当前温度引用缺规则的回归；检查通过仍只代表有限规则。
4. 对 review 超时先做受控时序分析，分开输入/输出上限、模型排队和传输观察；不追加本轮真实重跑来抹去失败。

这些是后续工作方向，本轮只完成排查与输入副本验证；原采集、评分分母与结果保持原样。
