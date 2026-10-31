# Round 6：有来源的链式百分比计算修复记录

本页记录 Issue #47 的局部开发过程。它是确定性规则的开发回归，不是独立题集、真人评测或真实模型效果提升证明。Round 5 的输入、模型输出及评分文件均保持原样。

## 原始现象与证据

Round 5 Cedar t1 / t3 将 `Dune 的节电百分比 (240-208)/240×100=13.3%` 降为“证据不足”，finding 为 `quantity_not_anchored_in_selected_quotes`。被选引文已经包含 240 kWh baseline、208 kWh Dune 读数与相对 baseline 的节电公式；13.3% 是 13.333…% 的合理一位小数展示。

- [原始失败及诊断](../conversation-round5/development/grading/failure-diagnostics.json)
- [签字读数](../conversation-round5/development/inputs/sources/cedar/02-signed-trial-readings.md)
- [批准计算规则](../conversation-round5/development/inputs/sources/cedar/01-approved-acceptance.md)
- [原轮评分说明](../conversation-round5/development/REPORT.md)

在 Round 5 合并提交 `b76eb7a4553583aaa8ca3a19bca83a59da67e9e6` 上，`answer_quality._arithmetic_results` 只识别单个二元表达式，例如 `96-80=16 ms` 和 `1/600=0.17%`；链式表达式明确在其语法范围之外。对上述 Dune 算式实际调用旧函数返回 `set()`。这确认的是规则覆盖不足，不能由此宣称检索不到资料或 Dune 没有测量。

还有一个需要一起处理的困难：原规则按结果数值匹配引文。Reed 的结果 12.5% 恰好也是阈值原文，数字出现本身不能验证对象或分母。如果只向“允许数值”集合补 13.3%，错误算式 `(240-208)/240×100=12.5%` 仍可能利用阈值数字通过。因此新检查必须同时返回合法推导和明确失败项，接入处不能只消费合法结果。

## 选择的实现与放弃的方案

新增 [evidence_arithmetic.py](../../../lablens/evidence_arithmetic.py)，不执行模型生成的代码，不发起模型调用，只识别一个有界表达式：

```text
(baseline - candidate) / baseline * 100 = result%
```

1. 先识别类似百分比链式表达式的外形，再完整匹配严格语法。缺操作数、错误操作符会给出失败项，不能因结果碰巧等于来源数字而通过。
2. 从**本条所选引文**抽取简单明确测量，例如 `Baseline consumed 240 kWh. Dune consumed 208 kWh.`。要求 baseline 与当前命名候选均唯一；重复的同一事实去重，冲突读数保留为歧义。
3. 当前 assessment 只能明确指向一个被引用的候选；操作数必须分别属于 baseline 和这个对象，分母必须等于 baseline，baseline 必须大于零；要求同一单位。不能把 Reed 的 210 当作 Dune 的 208，也不能把功率 W 与能量 kWh 混用。
4. 用局部 `Decimal` 上下文计算，允许写出结果末位半个单位的舍入误差。13.3% 容差为 0.05 个百分点；13.34% 不会通过 13.333…% 的两位小数校验。
5. 返回 `derived_quantities`、可记录的 `accepted` 明细及 `findings`。失败表示这一局部检查未通过；它不改写公式、补齐资料或推断新的结论。

考虑后没有采用的做法：

- **直接放开所有链式算式或使用 `eval`**：增加执行面，也没有解决操作数与对象、单位的对应问题。
- **看到结果数字在任一资料中就放行**：会把阈值、另一对象的读数或历史样本误当本次推导依据。
- **通过模型追加一轮算术复核**：本问题已有明确、可重现的规则缺口，额外调用增加时间与费用，也不能替代确定性负例。
- **把所有算式规范化成通用表达式树**：本轮真实失败只需要一个相对下降公式。通用运算优先级、量纲转换和语义对象绑定的范围更大，应有单独设计与评测。

## 接入要求

在 `selected_evidence_findings` 处理每个 `supported` assessment 时，将 statement 与 explanation 合并为 `prose`，本条已选 quotes 合并为 `quotes`，调用：

```python
inspection = inspect_percentage_derivations(prose, quotes)
derived |= inspection.derived_quantities
findings.extend(dict(assessment_index=index, **finding) for finding in inspection.findings)
```

必须保留原二元表达式规则、原本的数量锚定失败项和这次的明确失败项。不能用 `accepted` 证明整个 assessment 有语义蕴含，也不能以它覆盖同段中的另一错误算式。相应的集成回归及最终模型结果由 Round 6 总报告记录；此模块开发测试自身不宣称已完成整条 Agent 验收。

## 本地测试与实测差异

命令：

```bash
.venv/bin/python -m pytest tests/test_evidence_arithmetic.py -q
```

实际结果：**45 passed in 0.04s**。测试包括：

- 直接读取 Round 5 未修改的 Cedar 失败 assessment，所选引文完整时得到 `('ratio', Decimal('0.133'))`，无新增算式 finding。
- Dune 13.3%、13.33%、13.333% 的舍入，以及 Reed 的 12.5% 等号边界。
- 新对象 Orchid、1,250 / 1,000 ms 的 20% 下降，确认规则没有硬编码 Cedar 的名称或数字。
- 错误分母、交换对象、跨对象拼接、缺读数、冲突读数、单位不一致、缺操作数、错误倍数、错误结果、零分母及越界数字拒绝推导。
- 结果恰好等于原文 12.5% 阈值的错误算式仍产生明确 finding。
- 代码形状文本不执行；超长输入、超过 16 条表达式的输入拒绝处理；计算精度不污染调用者的 Decimal 上下文。

这 45 项只验证局部语法与边界，不应写成“45 道真实问答正确”或“模型准确率提高”。这里的优化收益是修复已观察到的错误拒绝，并补充会阻断错误放行的负例；没有实测 token、延迟或整体语义准确率提升。

### 已保存的旧草稿 before / after 对照

另使用 [离线回放脚本](../../../scripts/replay_arithmetic_round6.py) 生成了 [arithmetic-before-after.json](arithmetic-before-after.json)，不是只在说明中声称修复：

```bash
.venv/bin/python scripts/replay_arithmetic_round6.py \
  --output docs/evaluations/conversation-round6/arithmetic-before-after.json
```

脚本从 Cedar t1 / t3 原始 `task-final.json` 中读取模型提交的 draft，使用基线提交的 passage catalog 解析原 evidence IDs；分别调用基线及本轮 guard，保留完整输入、输出与源码/原始文件 SHA-256。**两份相同旧草稿均从 1 项数值误拒绝变成 0 项**；额外明确标注的负例把结果改为错误的 12.5%，基线无 finding，新 guard 产生 `percentage_result_incorrect`，不会被原文的 12.5% 阈值数字放过。statement 与 explanation 都写了该错误算式，因此记录中有两条对应 finding。

首次回放文件 SHA-256 为 `0e1209f0b2c8a30d27103c650b49d2d806e8c3789bd3a7fdce7d212ee03d8eab`，provider 调用 **0**。脚本拒绝覆盖已有文件，且执行前后核对历史 raw 与当前 guard 源码未变化。该记录绑定开发时点的源码 hash；若后续集成代码改变，应另存新版对照，不修改这个结果。此对照不回写 Round 5 报告、历史结果或旧分数，也不证明新模型能生成正确答案。

## 仍未解决的边界

- 同一 assessment 同时混写多个候选、别名、代词或复杂中文对象名时，规则保守拒绝建立推导；没有实现通用实体消歧。
- 只有列举数字而没有简单测量语法，或省略算式、使用其他等价算式顺序时，不自动猜测推导。`32/240×100=13.3%` 不足以表明 32 确实来自当前所选读数。
- 不自动转换 kWh / Wh、ms / s；同单位要求减少错误推断，但可能拒绝正确的单位换算。
- 来源中的否定、假设、版本、实验条件、测量权威性及总体可比性需要模型复核与独立语义评测。此模块只对支持的字面测量句式提供有限绑定，不认证文档真实性。
- 原有数值锚定规则仍然是有限检查，不因此成为所有数量陈述的语义验证器。Round 5 的总体结论漏项、材料缺失与历史未执行混淆、复核超时必须分别修复和验收。
