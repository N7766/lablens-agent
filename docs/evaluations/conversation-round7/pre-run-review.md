# Round 7 实现交叉审查

开发团队另一 AI 只读审查当前实现及控制测试；不是独立人工评测。本记录先保留修复前复现，后续验证追加。外部模型调用为 0。

## 修复前发现

### 条件澄清偏好被错误禁用

输入：`Give conclusions and missing evidence, but ask me which deployment if the records identify more than one.`

调用：`clarification_policy(question, lookup_count=1)`。

实际：`allow_clarification=False`，规则为 `explicit_conclusion_and_evidence_gap_after_retrieval`。中文“请给出结论和缺失的证据，若记录对应多个部署就向我确认对象。”同样失败。

影响：用户已明确要求多对象时先确认，而初始策略只识别 first/before 等顺序表达。检索后才发现同名部署时，正确澄清动作会被删去；若模型仍选择它，整合层会以 unavailable action 终止。需要保留条件及一般明确澄清偏好，不应把“要求结论和缺失证据”理解成用户已排除范围歧义。

复现时策略实现哈希：`09f0b1276c518e0b42a4c38285c6af767bd6413101089e4fbeed43189797607c`。

### 多指标 statement 借用了邻项 explanation

问题：`与上一轮相比，请指出超时上限、并发数中哪些需要变更、哪些保留。`

一条原稿：statement=`超时上限为20秒，并发数由25改为50。`，explanation=`从25改为50。`。

实际：`prepare_review_draft` 的 `added_review_slots=[]`。并发数已有比较，超时上限仅有新值；后者却因“标题拥有解释”的 fallback 被视为已覆盖。

影响：fallback 只要求 statement 包含 subject，未限制单指标标题。局部谓词规则虽已阻止正文邻项借用，仍可从 explanation 借回来。应限制解释归属为单指标，并保留此原样反例。

## 其余已检查边界

- 不可用 action 在执行任何工具前终止，没有新增修复模型请求。
- `CoverageContractError` 直接停止；不删原稿、不截断、不进入普通无效报告重规划路径。
- 8 行上限、最多 6 decision + 1 review、75 秒未因此改动。
- JVM v8 接受版本和 `clarification_policy` 轨迹字段已更新；这里是源代码审查，不能代替真实 JVM 故障路径运行。
- 初始 targeted 测试 `test_round7_comparison_coverage.py + test_clarification_policy.py + test_round7_actions.py + test_answer_coverage.py`：134 passed。现有测试通过未发现上面两个缺口，因此新增原样反例是必要回归，不能只引用旧通过数。

## 修复复核

待作者完成后追加结果；不能将初次发现覆盖为“全程无问题”。


### 已完成的修复复核

两个原样反例复核通过：条件/一般明确澄清偏好保留动作；多指标 statement 不再借用未归属 explanation。相关正例仍保留：没有歧义偏好且明确要求结论和缺口时，在检索后保持报告/继续检索选择；单指标标题仍可拥有自己的比较解释。

对 `rag.py` 的短 ID review 协议额外只读审查：

- `_scoped_review_statements` 在请求前检查不可变原稿与 contract 的顺序、数量、唯一性。
- `_scoped_selection_schema` 使用封闭 assessment_id 枚举，固定原行数；`_restore_review_statements` 拒绝缺行、重复、未知 ID、额外字段，按原序恢复原 statement。
- 恢复后的输出仍进入旧 evidence selection、引用、数量与范围检查；并未以 ID 正确代替语义正确。
- 输出上限仍为 2600，只有一次 `_post`，无失败后自动扩展/补调。一般 RAG 无 scoped contract 时保留原 statement 协议。
- malformed 与完整恢复的控制测试通过，不代表真实模型会遵从新 schema，也不保证时延改善。

此阶段合并 targeted 验证（compact/comparison/policy/action/coverage/capacity）：169 passed，1 个既有 Starlette/httpx warning。新评测工具修改在下节单独记录。

### 评测工具内部复核后的修复

另一开发团队 AI 复现并报告：已交付回答可以留下 null 维度、满分与 incorrect 诊断可同时存在、需事实答案的新四题纯澄清可被标满分，以及封存 source-after-error 被评分器忽略。它还确认仅把 aggregate tokens 清零后，原 runner 仍错误视为 token 全知。这些是评测工具可靠性问题，不是模型质量分数变化。

处理：

1. 已交付维度必须显式整数 0/1/2；未交付维度才允许 null。
2. strict 通过必须与补充诊断和交付类型一致，否则拒收整份矛盾标注，不自动修改原 gold 或决定分数。旧六题中真正歧义题仍可用正确澄清通过。
3. 正式评分要求封存 approval/source-before/source-after/inputs-after 身份完整且一致，拒绝 source/input 错误及采集中曾发生的身份失败。身份有效的普通基础设施部分失败仍可评分，输出明确带 `execution_fatal` 和 `valid_partial_collection`，保留计划分母。
4. 完整已知 request lifecycle token 分项求和必须等于 aggregate；不一致标记未知，保留原始数值。次数已知与 token 完整仍分开。假 API fixture 原先也不一致（aggregate20 vs trace6），同步修正为诚实一致的 synthetic control。

新增原样反例与控制后 harness：75 passed。外部模型调用仍为 0；原 Round 5/6 的冻结执行器、评分器、输入和结果没有修改。正式采集必须等 root 完整验证、源码/输入冻结及明确 GO。
