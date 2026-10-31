# 第六轮开发记录：报告覆盖与复核边界

基线：`b76eb7a`（第五轮已合并）。原始失败继续保留在第五轮目录；本文件记录开发过程，不替代最终模型验收。

## 已确认的问题

- Cedar t1草稿仅四个分项，缺少Dune/Reed总体结论；Harbor t1漏一个成功率分项和两对象总体结论。见[原始失败诊断](../conversation-round5/development/grading/failure-diagnostics.json)。这两个现象由实际输出确认。
- `assessment.py`的模型指令限制最多4条，而Pydantic/schema允许8条；`rag.py`最终复核也要求最多4条。
- `make_review_contract`将草稿statement集合固定，review schema要求逐字保留。因此即使review看到缺项，也不能合法增加总体结论。这是可确认的协议限制；不能声称仅这一处解释全部模型漏答。

## 取舍与实现

1. 将指令与既有8条schema对齐，要求双对象比较保留每个请求指标及总体结论；输出token和75秒预算不扩。
2. 不解除整个复核statement约束：任意新增会重新引入额外对象、旧问题和无依据命题。改为在固定review前，仅为当前用户明确提出的、单一命名对象对的总体验收要求预留问题项；review仍只填写固定集合。
3. 预留项初值是无引文的待复核问题，不算资料不足结论或事实。review必须用所给原文确定supported/contradicted/insufficient；规则中的AND/OR不能由服务端擅自替换为AND。
4. 超过8条时明确拒绝本次工具参数，不删除已有指标。保留已有有限重提参数机制，不新增供应商重试或模型修复调用。
5. 明确当前问题优先，历史只用于指代；不能像kl-t2一样在用户仅问历史行为时重新引入上一轮配置问题。

## 边界

词法槽位仅识别一个明确命名对及明确总体要求，不是通用问题分解；不从文档生成用户要求，不声称覆盖所有分项。两条预留项存在也不能证明答案正确、AND/OR使用正确或引用蕴含。最终仍按未修改gold评分。

模型可能因输出变长消耗更多复核时间。保留相同调用/时间/token上限进行一次回归，并记录任何截断、超时、阻塞和未知用量，不能用测试通过替代质量改善。

## 接入中暴露的旧测试假设

首次联调119项通过、2项失败：旧numeric-only fixture写“Cannot confirm 80 ms; no measurement was made.”，没有任何引用，却期待无finding。前半句是未知数值，后半句仍断言历史没有测量；新范围检查应拒绝后半句。将原数值隔离测试改为“no measurement record is supplied”，同时单独保留原句为负例（contradicted/insufficient两种verdict都必须报范围错误）。没有降低数值检查或删除失败样本。首次联调日志保存在本地开发目录，最终模型分数另记。

## 交叉审查后的修正（推理前）

另一AI只读审查发现：“比较Dune和Reed。更正，只给Dune结论”仍会强加Reed总判定，以及“Latency and Throughput”可能把指标误认成对象。修复为复用统一受限命名对识别，并在对象对之后的明确单目标更正时跳过自动补槽；仅重复名字但未缩小范围不跳过。新增四个控制用例。此时尚未运行第六轮真实模型，没有事后针对输出修补。

## 跨服务故障检查发现的真实回退

首次受控运行64项仅43项通过：快速成功路径通过30项，但复核超时路径缺失整段Agent轨迹。原始失败保留在 [runtime-fault-first/summary.json](runtime-fault-first/summary.json)，未覆盖。

定位发现Python新增 `review_coverage_slots` 事件，但Java的错误结果事件白名单未接受它。尽管v7版本号已经加入，safeAgentTrace遇到新事件仍返回null；任务状态、unknown用量保留，诊断轨迹却被丢掉。这说明单独Python测试和只含旧事件的Java测试没有覆盖完整新协议。

修复是将该已定义事件加入明确白名单，并让v7错误持久化用例同时包含read、coverage和review-timeout事件。没有改成接受任意事件。重新构建JAR后用相同6秒预算/8秒受控延迟另跑一份结果。两次都是本机合成provider，外部模型调用0；不能写成商业线上事故。

此外，coverage更正负例补充了“只看Dune，不是Reed”的中英文写法，按逗号拆分限定子句，避免另一个名字出现在排除项中绕过单对象更正。

## 测试配置核对

Maven首次与白名单修复后的首次构建均BUILD SUCCESS，但汇总XML检查发现34项MySQL/Redis集成测试因缺少 `LABLENS_INTEGRATION_TESTS=true` 被跳过，只有32项实际通过。终端成功状态不能当作66项全部通过。已保留跳过日志并明确更正进度说明，在独立测试库启用该开关后补跑；最终regressions.json仅记录经XML逐类核对的实际结果，不把跳过项计为通过。没有为获得绿灯取消条件或删测试。
