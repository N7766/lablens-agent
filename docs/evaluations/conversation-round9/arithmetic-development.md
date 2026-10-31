# Round 9：已有算术结果在整体行被误拒的有限修复

这是开发回归与源代码记录，不是新一轮真实模型质量结果。Round 8 原始输出、gold、分数与失败分析未修改。

## 已确认的故障与边界

[Round 8 Cedar 首轮原始结果](../conversation-round8/runs/round8-once/cedar-t1-final-task.json) 的第 0 行写出了 Dune 的 `(240 - 208) / 240 × 100 = 13.3%`，所选测量包含 `Baseline consumed 240 kWh` 与 `Dune consumed 208 kWh`。第 4 行原解释保存在 `assessment_validation.selected_evidence_checks.withheld_originals`：复述节电 13.3%、比较 12.5% 阈值并结合温度给出整体验收结论；该行自己选了同一测量引文以及规则引文。

旧检查仅检查每行的显式公式。第 4 行没有重复公式，13.3% 也不在所选原始测量中，所以触发 `quantity_not_anchored_in_selected_quotes`。这并不表示资料缺失。另一个独立问题是原行使用 `causal_claim`：即使消除数值误拒，后续因果护栏仍会拒绝。测试分别保留了这两层结果，没有偷偷修改原 basis 来制造整体通过。

## 实现选择

新模块 [evidence_derivations.py](../../../lablens/evidence_derivations.py) 生成服务端的一跳算术证书，不增加模型 JSON 字段。生产者资格必须来自使用实际 source metadata 的 `validate_assessments` 预验证：原行仍 `supported`，statement、explanation、basis、quote 身份及 citation IDs 都没有被改动。随后还要通过原有独立 numeric/scope/binding 检查。来源只有配置的历史陈述、因果陈述、其他被预验证降级的行均不能发证。

证书记录生产行编号与 statement hash、显式公式、对象、基线及候选值、单位、结果和舍入容差，以及操作数的 `chunk_id + quote_sha256`。只接受具名对象、Wh/kWh 能量单位和已有有界 `(baseline - candidate) / baseline * 100` 公式。条件、否定、冲突、未知以及不支持的形式不发证。

消费行必须自己重新选择完全相同的操作数引文，自己选择完整、肯定的 `Use (baseline energy minus candidate energy) divided by baseline energy, times 100` 规则句，并在其所选全部引文上重新核算原式。若生产行本来选择了规则，该规则身份也须保留；Cedar 的生产行原来只选测量，此时证书只提供算术，消费行的规则是单独补齐的前提。公式规则检查仅确认该有界原句出现，不认证其来源真实、适用范围或验收语义。

消费行 statement 必须唯一命名同对象，文字须明确表达节电/能量下降，且只许可匹配的一个百分比 token 位置。不会把另一个对象、另一个量类型或同一行其他位置的同数值一起放行；不会从相同 chunk 的其他片段补引文，不复制公式、不改变原数值和句子。全部依赖审计保存在 `selected_evidence_checks.derivation_dependencies`。

没有真实 hit 元数据时，包括 draft review contract，复用保持关闭并标明 `producer_final_validation_unavailable`。消费后的行不能生成新证书，所以没有传递闭包、前后行顺序依赖或循环自证。

## 实现中的问题与取舍

1. **如果要求两行都已经选择规则，无法修复实际 Cedar 失败。** 原公式行只有测量，整体行才有规则。拆开“由测量可复算的百分比”和“本行适用规则”后，每个消费结论仍保留自己的全部引用；共享的是已审计的计算，不是把另一行引用当作本行缺失前提。
2. **仅依赖 supported 标记不够。** 旧调用顺序先做选引文检查，再执行带 source metadata 的完整 validator。新检查先用同一 validator 确定生产者是否保留原状，避免借用随后会因 config-only history 或因果护栏被拒的结果。未取得这一证明时保守关闭。
3. **整个结果数值集合会扩大许可范围。** 因此依赖只豁免具体百分比 token span；在同一行另写一个未锚定的 13.3% 概率，仍会触发原数量规则。
4. **旧公式识别器能识别较大表达式的子式。** 开发审计中为新证书层补充完整表达式边界：前置 `2 * (...)`、后置 `+ 1`、`/ 2`、`** 3`、`.5`、`,000` 等不能发证。未声称修复旧同行算术 parser 的全部语法。
5. **有限语法有意保守。** 新依赖仅覆盖能量下降、Wh/kWh、具名对象和完整英文公式规则句；别名、多对象、其他量类型、单位转换、自然语言隐含公式均不复用。片段外被截掉的限定词、来源真实性、完整适用范围、阈值与 AND/OR 逻辑及最终验收结论仍需语义复核；对应认证 flags 均为 false。

## 验证

新增 [test_round9_derivations.py](../../../tests/test_round9_derivations.py) **74 项**通过。与已有百分比算术、Round 8 绑定、answer quality 回归合计 **264 项**通过；有一个既有 Starlette/httpx 弃用提示。

```sh
.venv/bin/python -m pytest -q tests/test_round9_derivations.py tests/test_evidence_arithmetic.py tests/test_round8_evidence_binding.py tests/test_answer_quality.py
```

覆盖：恢复已保存原解释的 Cedar 整体行；保持原引文与 statement 不变；异对象/别名/错量类型/同数不同来源/同 chunk 不同引文；缺规则或缺测量；生产行原有 numeric/scope/binding 失败；真实 source metadata 触发配置历史护栏；条件/否定/冲突/错误公式/超长输入；只豁免一个 token；循环和链式复用不能自举。原 raw 文件哈希在回归前后相同。

这些是本地可复现控制测试，证明有限实现行为；不能换算为模型准确率、真实线上吞吐或独立盲测质量提升。
