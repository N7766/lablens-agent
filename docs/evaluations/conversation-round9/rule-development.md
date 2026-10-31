# Round 9：显式规则验收与机制因果分开建模

## 已定位的错误

[Round 8 封存排查](../conversation-round8/assessment/failure-review.md)及 [Cedar 首轮原始报告](../conversation-round8/runs/round8-once/cedar-t1-final-task.json)中，`result.agent.trace[16].arguments.assessments[4:6]` 已给出 Dune 未通过、Reed 通过的两个整体结论，但模型同时将它们归为 `causal_claim`。规则与测量均在对应行的原引用里；按已经给出的验收规则判断是否通过，不是在证明测量变化的机制原因。

最终报告中 Dune 另有派生数值未获有限数量检查承认的问题；Reed 则进入 `causal_claim_requires_external_controlled_review` 而被降级。本模块只解决类型契约缺口，不把这两个独立触发条件混为一谈，不改变旧 raw、gold 或评分。

## 实现与边界

在 `lablens/assessment.py` 为 `Assessment` 和 `AssessmentSelection` 引入共享的 `AssessmentBasis`，保留 `documented_fact`、`current_configuration`、`historical_observation`、`causal_claim` 四个已有值，新增 `rule_evaluation`。它表示按资料明确给出的规则计算、比较阈值或判定整体验收结果，仍可有 supported、contradicted、insufficient 任一种 verdict。类型本身不是正确性证明。

直接评估、Agent 初稿及复核共用的指令要求：

- 每条规则判断自行选择当前对象的测量和适用规则；按需要覆盖公式、阈值、是否含等号、AND/OR 和适用范围，不能借其他行的引用补足前提。
- 给出计算或比较；缺前提则保留不足，相互冲突的适用前提需说明冲突，不能编造规则。
- “为什么通过既定规则”与“是什么机制造成测量变化”分开。真正机制解释仍用 `causal_claim` 并保留证据不足，不能换成 `rule_evaluation` 逃避因果边界。

`validate_assessments` 整个函数的 AST 与开发起点 `528a1fedcebdf2b07f88245f22ced6d5c245c8c6` 相同。没有关闭因果 guard，也没有把 causal 行自动转成规则通过。旧标签错误仍会被拒，需要模型在初稿/复核时显式纠正类型。现有中英文因果词检查仍适用于新类型；它是原来的有限语言匹配，不认证任意自然语言是否暗含机制主张。

共享 schema 自动传递到普通选择 schema、Agent 工具参数和 scoped review schema；未知 basis 仍被严格拒绝。四个旧值和新值均有解析、引用 ID 解析和序列化回归，避免历史报告被新版本破坏。

## 取舍与保留缺口

1. 曾可通过将旧输入副本改成 `documented_fact` 绕过错误类型，但这没有说明“规则验收”与“原文记录”的差别。本轮选择显式第五类，避免依靠宽泛标签掩盖该区别，也没有针对 Cedar 名称、C-21 或既有数值写运行时条件。
2. 新类型需要额外指令。共享说明为 **782 UTF-8 bytes**，同时删除 selection 指令中重复的阈值引用要求。相对开发起点，直接评估指令从 2,749 到 3,549 bytes（+800），选择指令从 6,498 到 7,101 bytes（+603）。这是纯指令长度，不是完整请求大小、模型 token 或时延；不能据此宣称性能提升。
3. 只靠类型 schema 和 exact quote 检查，无法识别任意自然语言中“仅引测量、遗漏规则”的语义缺口。本轮保留 Cedar 两条温度行作为只读反例：改用新类型仍不会自动补出它们缺失的规则引用，`semantic_entailment_verified` 仍为 false。这一项需在真实输出的独立评阅中检查，不能因机械测试通过便记为引用完整。
4. 首次定向测试即通过；没有本模块开发测试失败记录可补造。旧 raw 的已知失败、此次类型副本对照和后续新模型结果是三个独立证据层次。

## 可重复验证

新增 **24 项**测试，连同原 assessment 回归为 **49 项通过**：

```sh
.venv/bin/python -m pytest -q tests/test_round9_rule_basis.py tests/test_assessment.py
```

包含异于已暴露案例的 Quartz / R 合成规则，中英文机制因果反例、配置不能证明历史执行、无证据不能支持、旧四类兼容、新类型在 scoped review 中显式纠正，以及未知类型拒绝。正例只验证程序能保留一个规则结论，不假装程序重新证明了其语义。

连同 RAG、answer_quality、evidence_scope、workspace_agent 的定向兼容回归为 **287 项通过**，仅一条已有 Starlette/httpx 弃用提示。

旧 Cedar 对照只读原文件并仅修改内存副本：原两个 `causal_claim` 总结论仍被原 guard 降级；仅将副本 basis 改为 `rule_evaluation` 后，两条规则结论不再因该标签触发因果拒答，statement、explanation 与引用不改。它不是模型新输出，也不证明模型未来会正确分类。温度引用缺规则的已知反例继续保留。

## 运行时适配与实际回归失败

Agent 版本升级为 `workspace-agent-v10`，Java 错误结果处理器保留历史版本并允许 v10，默认缓存命名空间改为 `conversation-round9-agent-v10`。真实 `review_completed` 事件保存 `generation_review` 的嵌套 delta 审计；它仍只在原本的 deadline 和取消检查通过后记录，超时不能伪造完成事件。Java 增加结果/缓存、错误轨迹的 delta 与 derivation 嵌套透传用例，未知 v999 仍不予保留。这里的透传不代表 Java 再做语义或算术验证。

新 `scripts/evaluate_runtime_round9.py` 从上一轮 harness 复制并适配显式替换响应；历史 Round 8 harness 未修改。受控 provider 返回 `retained_assessment_ids=[]` 与完整替换行，离线自检覆盖旧非 scoped 路径和新协议，不调用模型或启动服务。Round 8 Agent、runtime telemetry 的现行测试夹具同步新协议及 v10。

适配时真实出现一次定向失败：39 项通过，原 `test_compact_review_is_lossless_for_catalog_draft_and_preserves_all_checks` 仍要求新旧 `response_format` 完全一致。delta 协议新增显式保留 ID 列表，原断言已不符合接口；修复为验证替换行定义不变、全部原始证据/上下文不变，以及新顶层必需字段与 ID 约束。没有放宽最终报告验证。

原“compact 请求必然更小”断言同时移除，保留精确编码长度检查。使用同一当前共享指令和单行受控输入，旧 full builder 的请求为 **14,626 bytes**，新 delta 为 **15,899 bytes**，增加 **1,273 bytes**；新决策说明与 schema 有成本。这不是两代完整模型请求或 token/时延比较，也不说明实际 review 输出能缩短多少。适配后规则类型、旧 Agent 和 runtime telemetry 定向 **40 项通过**，仅已有弃用提示。

## 算术依赖模块独立只读复审

读取 `evidence_derivations.py`、`answer_quality.py` 与全部派生依赖测试后运行 **74 项通过**。另外四个内存控制确认：真实 hit 缺少选中引文时先拒绝；来源是 validation、producer 却称 test 而被最终 validator 改写时不发证书；consumer span 只指向节电的单个量值；同一解释另一个同值概率仍被有限数量检查拦截。

资格来自含真实 hit 元数据的最终验证、逐字段原样保留比对和独立的第一次 numeric/binding/scope 检查，不由模型自报。未发现该有限合同内需额外修正的问题；范围仍是 Wh/kWh、显式英文公式句、一跳、同对象和同操作数引文指纹。不会认证阈值、AND/OR、来源权限或适用范围，消费者也可能因其他检查在最终报告中变为不足。
