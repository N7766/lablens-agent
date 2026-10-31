# Round5 旧题第二次评分复核

复核日期：2026-10-31（Asia/Shanghai）。严格通过 **2/6**：`cedar-t2`、`harbor-t3`。六轮均交付终态，交付成功不能替代答案通过。

本复核按冻结 [gold/rubric.json](gold/rubric.json)、[问题及会话更正](inputs/scenarios.json) 和首次终态原始结果逐题判断，未修改源码、gold、原始输出或主评分，未读取新题，未发起模型请求。复核者是参与本轮前端实现和运行时代码审阅的另一个 AI 代理；复核过程中收到执行作者的初判和异议点，所以这是第二次证据复核，**不是人工独立评测，也不是双盲评分**。下述结论均由原始输出和资料核对，不以作者的初判或机械引用检查作为正确性证明。

## 封存核验

- `frozen-manifest.json`：12 个文件逐项 SHA-256 匹配；manifest SHA-256 为 `1768de901a655a7108bf4300a1153c2af7bd3a95a88791dbe89493c653d99787`。
- `raw-output-manifest.json`：相对 `run/` 的 292 个文件逐项 SHA-256 匹配；manifest SHA-256 为 `7b901aeb8cb7e47b74a26e3af7037b290620251ce301cdfbc05bb1df37c80552`。
- 以下 JSON 定位均从对应 `task-final.json` 根对象开始，数组下标从 0 开始。`result.answer` 才是最终用户可见答案；草稿、检索命中、用户范围附录均不能补齐答案漏项。

## 逐题结果

| 题目 | 严格判定 | 决定性原因 |
| --- | --- | --- |
| cedar-t1 | **FAIL** | Dune 节电结论被降为证据不足，且缺少“Dune 总体不通过、Reed 总体通过”的明确总判定。 |
| cedar-t2 | **PASS** | 将“Dune 全条件通过”判为矛盾，将“月运行费更低”判为证据不足，并正确采用更正后的 Dune。 |
| cedar-t3 | **FAIL** | 回答了两者的指标，却没有明确追问“它指 Dune 还是 Reed”；不满足冻结 gold 的澄清要求。 |
| harbor-t1 | **FAIL** | 缺少 Sable 成功率 `395/400=98.75%`、其失败判定及两者均不通过的总判定。 |
| harbor-t2 | **FAIL** | 三类证据状态正确，但把“证据包没有最大恢复时间记录”强化成“R-17 未测量断电后的恢复时间”。 |
| harbor-t3 | **PASS** | 明确询问应采用 R-17 还是 R-09，没有擅自定稿或混用结果。 |

### cedar-t1：FAIL

证据：[最终结果](run/cedar/cedar-t1/task-final.json) 的 `result.answer`、`result.assessments[0]` 和 `result.assessment_validation.selected_evidence_checks.findings[0]`。

- 第一项实际显示 `【证据不足】`，解释为“原结论未发布”。它保留了数值命题，并没有说整个工作区无资料，但用户仍没有得到应有的“Dune 节电达标”有效判定。
- [批准规则第 4 行](inputs/sources/cedar/01-approved-acceptance.md#L4) 给出计算式、包含等号的阈值以及两项必须同时满足。[签字结果第 4 行](inputs/sources/cedar/02-signed-trial-readings.md#L4) 给出 240、208、210 kWh 和两者最大温度。可直接算得 Dune 约 13.33%、Reed 12.5%。有限规则将 `13.3%` 标为 `quantity_not_anchored_in_selected_quotes`，不能证明这条计算没有证据。
- 其余三项对 Reed 节电、Dune 温度失败和 Reed 温度通过的判断正确，但没有明确说 Dune 总体不通过、Reed 总体通过。引用中的 `BOTH` 和读者自行推导不能替代用户要求的完整验收结论。
- 所需资料已检索到：`result.retrieval.hits` 包含规则块 `chunk:fdef1460cd1d9b9eaed6a1de`、读数块 `chunk:e47897a4e8901e942eefac05` 及试验适用范围补充块。失败不能归因为检索缺失。

### cedar-t2：PASS

证据：[最终结果](run/cedar/cedar-t2/task-final.json) 的 `result.assessments[0:2]`、`result.answer` 和 `result.retrieval.hits`。

- 第一项为 `contradicted`，明确说明 Dune 节电满足，但最大温度 5.2°C 超过 5.0°C，且两项必须同时成立，符合上述规则与签字读数。
- 第二项为 `insufficient`，明确缺少电价、维护费和租赁费，无法计算月运行费。该结论由 [成本说明第 4 行](inputs/sources/cedar/03-cost-scope-note.md#L4) 直接支持。
- 回答对象是更正后的 Dune，相较对象为 Reed；结论引用 C-21 当前资料，未挪用 C-08 的旧偏好。没有因成本未知而拒绝可回答的温度矛盾判断。
- 两个判断的所有必要来源都在检索结果中。13.3% 为题中公式计算结果的合理舍入，不是伪造原始观测。

### cedar-t3：FAIL

证据：[请求](run/cedar/cedar-t3/request.json)、[最终结果](run/cedar/cedar-t3/task-final.json) 的 `result.status`、`result.answer` 和 `result.assessments`，以及 gold 的 `turns.cedar-t3.mandatory`。

- 用户撤回只看 Dune 的限定，同时保留 Dune、Reed，再以“它”要求签字结论。gold 要求先明确询问是哪一个。
- 实际返回 `answered` 和四项指标，未询问 Dune 还是 Reed，也没有 `clarification`。附录复述用户问题和“对象不清楚时先澄清”的限制不是模型实际澄清。
- 精确失败是“没有提出必须的澄清问题”；不能夸大为“擅自只选了 Dune”或“已经签署结论”，因为实际正文同时讨论两者，也没有完成签字行为。
- 第一项另外重复出现与 cedar-t1 相同的计算结论抑制。即使忽略这个额外缺陷，缺少澄清已足以严格失败。

### harbor-t1：FAIL

证据：[最终结果](run/harbor/harbor-t1/task-final.json) 的 `result.answer`、四项 `result.assessments` 与 `result.retrieval.hits`。

- 正确给出 Quill 150 秒不满足严格小于 150 秒、198/200=99% 满足成功率，以及 Sable 149 秒满足延迟要求；dashboard 舍入不能作为验收依据的说明也正确。
- 正文和四项判断均没有给出 Sable 的 `395/400=98.75%`、其成功率低于 99% 的失败判断，也未明确说两者都不满足双条件总验收。
- [R-17 签字读数第 5 行](inputs/sources/harbor/02-r17-signed-results.md#L5) 包含完整 Sable 计数，[批准规则第 4 行](inputs/sources/harbor/01-night-restore-rule.md#L4) 要求两个条件同时成立；两份资料均已检索。缺失属于最终回答覆盖不全，不属于资料不足或拒答。
- 即使用户展开来源看到 Sable 计数，或看到字段 `evidence_verdict=supported`，也不能将其解释为“两者整体已通过”；该字段没有给出候选总验收判断。

### harbor-t2：FAIL

证据：[最终结果](run/harbor/harbor-t2/task-final.json) 的 `result.assessments[0:3]`，尤其 `result.assessments[2].explanation`、其 `quotes` 和最终 `result.answer`。

- 前两项正确：Sable 149 秒延迟达标；395/400=98.75% 低于 99%，故“成功率已达标”被反驳。目标更正正确，也没有因为停电资料缺失而拒绝整题。
- 第三项判 `insufficient`、未捏造最大时间、未用 nightly median 或 R-09 替代，这些均正确。但解释写成“**R-17 未测量断电后的恢复时间**”，是对历史行为的断言。
- [范围说明第 5 行](inputs/sources/harbor/03-r17-display-and-scope.md#L5) 只说证据包没有提供停电后的最大恢复时间；[结果表第 6 行](inputs/sources/harbor/02-r17-signed-results.md#L6) 只说此表未包含停电恢复时间测量；[规则第 5 行](inputs/sources/harbor/01-night-restore-rule.md#L5) 只限定 nightly workload 不能证明断电表现。三者均不足以推出“历史上没有测量”。
- 因而引用虽存在且三类标签正确，第三项的实质断言仍超出证据。冻结 rubric 明确要求引用支持事实和证据状态的每个实质主张，因此严格失败，不应只凭 `supported/contradicted/insufficient` 标签完整而放行。
- [失败诊断](grading/failure-diagnostics.json) 的 harbor-t2 条目可定位草稿原先使用“证据包未提供”；最终文本改成“未测量”。该对照用于定位问题发生阶段，不改变对最终答案的评分。

### harbor-t3：PASS

证据：[最终结果](run/harbor/harbor-t3/task-final.json) 的 `result.status=clarification_required`、`result.answer` 和 `result.clarification.question`。

最终明确询问“Night Restore R-17 还是 Archive Restore R-09”，要求指定记录后再定稿；没有单方面选取运行版本、混用证据或输出未获授权的结论。本轮无检索、无引用，符合 gold 对纯澄清的许可；`choices=[]` 不影响已明确写出的澄清问题。

## 跨题判定边界

- 必须事实检索的四轮（cedar-t1/t2、harbor-t1/t2）均拿到了所需证据。两个澄清题不以检索次数衡量是否成功。
- 两个第一轮比较题都遗漏了用户要求的总体判定。来源正确和中间单项正确不能把这两个答案计为完整通过。
- Cedar 的计算抑制是“有充分资料的子项未能有效发布”，不是整个工作区没有资料，也不宜记成整题 blanket refusal。
- Harbor-t2 不能把缺失记录解释为没有做过测量；不足结论本身合理，支撑它的历史行为解释仍可能越界。
- 另一评分作者已告知其初判同为 2/6。本复核没有因为一致而放宽标准；主要结论有上列原始证据定位。后续若有异议，应以追加讨论说明记录，不能覆盖首次终态或重写冻结 gold。
