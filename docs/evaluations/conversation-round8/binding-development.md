# Round 8：逐条结论与所选引文的有限绑定

## 实际问题与改动范围

Round 7 的数值锚点、原 statement 恢复和 exact-quote 检查，均未发现以下已发布错误：

- [Harbor 首轮](../conversation-round7/runs/round7-once/harbor-t1-final-task.json) `assessments[5]` 的 statement 为 Sable 总判定，explanation 与所选测量只写 Quill。
- [KiteLoad 第二轮](../conversation-round7/runs/round7-once/kl-t2-final-task.json) `assessments[0:2]` 声称 `max_retries=2`，却只选了没有该配置键的告警卡。数字碰巧一致、前轮答过配置，都不能给本条所选引文提供支持。

本轮新增 `lablens/evidence_binding.py`，接入现有 `answer_quality.make_review_contract` 和 `enforce_review_checks`。复核前把 draft 的明确绑定缺口与逐条检查范围交给既有的一次 review；复核后重新检查模型实际提交的行，不采信模型自报通过。没有增加模型调用、重试、检索或自动换引用。

## 两个明确但有限的契约

| 契约 | 检查条件与动作 | 不检查的范围 |
| --- | --- | --- |
| 当前用户命名对象 | 只复用已有 `named_user_pairs`，要求本轮用户恰好给出一对对象、statement 恰好出现其中一个；只有 explanation **和** selected quotes 都只出现另一对象时，产生 `explicit_subject_replaced_by_peer` | 代词、别名、隐含对象、同时出现两个名称时各谓词归属、非本轮对象、通用主语识别 |
| 当前配置字面键值 | 仅对 `supported/current_configuration` 行，将正向句中的 snake_case 数字、布尔或 null 赋值与本条所选引文的同键同值匹配；缺失产生 `configuration_literal_not_in_selected_quotes` | 自然语言设置、任意字符串、camelCase、配置是否生效、历史版本、来源权限与真实性、条件句来源是否支持实际配置 |

对象单侧只提对方时记录 `unverified`，不拒绝。例如“Birch 的结论：与 Maple 不同，它通过验收”，并引用同时列出两者正确结果，是合法对照加代词。不能因为 explanation 只出现 Maple 就把它当作确定错误。

每行记录实际检查项、未验证原因与违规字段；`checked_literal_presence` 仅指那个字面名称或键值存在。`semantic_entailment_verified`、`subject_predicate_binding_verified`、`source_authority_verified` 始终为 false。明确保留两个反例：两名字都出现但数值归错对象；条件/示例来源里包含配置字面量。这些不会被本检查识别，不能把“无 findings”计为语义正确。

配置键区分大小写；数值使用有长度/指数边界的 Decimal 比较，允许 `2` 与 `2.0` 的普通格式差异。否定、提问、假设或示例的有限句式直接跳过并记原因；`contradicted` 和 `insufficient` 行不把命题中的数值当作正向配置断言。这是保守跳过，仍不代表已经理解所有语言表达。

失败沿用已有清晰的暂不发布路径：保留 statement、quotes、citation_ids，verdict 标为 insufficient，解释明确说明“生成内容未通过检查，原结论未发布”，不声称工作区缺资料。`selected_evidence_checks.withheld_originals` 只保存行下标、原 verdict 与原 explanation；不复制整份证据，不静默修改失败事实。

## 开发中实际发现的难题

1. 第一版只要 explanation 或引用单侧出现另一对象就拦截。根代理读代码后提出合法“与另一对象不同，它……”反例。本轮改为两侧同时只提另一对象，牺牲检测覆盖以避免把代词对照误拒；增加该反例及双名称归错值的未验证测试。
2. 初次新增回归有 12 项失败：字面量末尾的句号被误当成数值的一部分，导致 `max_retries=2.` 未被检查，同时已有 ar-t1 源文中的 `total_attempts=2.` 未被识别而误拒。改正数值边界和句子分隔后重跑，保留原始冻结报告，既有 ar-t1/kl-t1/harbor-t2 均不新增 findings。
3. 独立代码复审又复现了 scalar 前缀截断：`max_retries=2+1`、`2 / 3`、`2,000`、`2-1`、`2**3` 会把前缀 `2` 错计为字面量存在。最小修复拒绝带后续运算符或数字分组的整个 scalar 匹配，不计算表达式；对断言和来源采用同一个边界。因此表达式断言标为未验证，表达式来源也不能为直接 `max_retries=2` 提供字面锚点。新增双向回归，同时保留简单整数、小数、科学计数法及普通句末标点的正例。
4. 复审指出提示规则中“explanation 或 quote 只出现另一对象均不可回答”与合法代词对照冲突，已改为要求核验当前对象的事实和代词/对照关系，不把单侧名称出现当成禁止条件。
5. 新规则不把一个检验通过升级为最终答案正确。例如 ar-t2 的比较错误拒答仍不在绑定契约范围；无 findings 只能说明这两个有限契约未发现问题。

## 离线重放与验证

使用原 Round 7 文件，未改 raw、gold、评分或重新调用模型：

| 原报告 | 此次离线发现 | 保留内容 |
| --- | --- | --- |
| Harbor t1 | 仅第 6 行产生对象替换的 explanation/selected_quotes 两条 finding | 前 5 行、各行 statement/quotes/citation_ids；失败解释保留在最小审计字段 |
| KiteLoad t2 | 第 1、2 行各产生配置字面量缺失 finding | 原第 3、4 行证据不足内容完整不变 |
| Harbor t2 / KiteLoad t1 / Alder t1 / Alder t2 | 绑定模块无 findings | 只验证不新增该类拦截；Alder t2 原语义失败仍然成立 |

原文件 SHA-256：

- Harbor t1：`d1fa0f72e8ebdae88a5941249b207c27035672e330b1bbd0fa9757593b6b19c4`
- KiteLoad t2：`473de22ce78f3924d9b6f22be6ba8eff3fbd78a0591fdeccdbf7de9b2903c367`

本模块新增 **81 项**控制测试；与原 answer_quality、evidence_scope、compact_review 组合为 **246 项通过**（一条已有 Starlette/httpx 弃用提示）：

```sh
.venv/bin/python -m pytest -q tests/test_round8_evidence_binding.py tests/test_answer_quality.py tests/test_evidence_scope.py tests/test_round7_compact_review.py
```

这是开发回归和原失败离线重放，不是独立人工评测、真实模型 Round 8 成功率、生产事故经验或流量指标。真实推理候选及结果需另行冻结和评分；不能把本模块回归当成已修复全部语义问题。
