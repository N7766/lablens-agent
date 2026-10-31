# LabLens 第六轮：回答覆盖、计算和证据范围

日期：2026-10-31。候选 `dd225edf2c286619eae0d299228975c6fecee9f0`，Agent `workspace-agent-v7`。本轮从第五轮真实失败出发，保留原问题、gold与首次输出，记录修复和验证。**工程改动与复盘已完成，真实质量验收未通过；草稿PR保留，主服务继续运行第五轮版本。**

## 为什么做这轮

第五轮旧六轮交付6/6但严格2/6，新四轮交付2/4、严格0/4（含超时与阻塞）。引用存在、工程测试通过和任务SUCCEEDED不能证明回答完整或正确。最具体的缺口是漏总体结论、链式百分比被误拒、把“资料没提供”写成“历史没测量”，以及同轮重新引入两个对象后没有澄清代词。

## 优化与取舍

| 问题 | 本轮改动 | 明确边界 |
|---|---|---|
| 指令最多4条，但双对象指标与总判定需更多项；fixed review不能补草稿缺项 | 对齐既有8条schema；在review前仅为明确命名对象对及总体要求增加待答问题槽，仍固定statement集合 | 不从资料擅自生成用户需求；同轮单对象更正优先；不是通用问题分解或语义覆盖证明 |
| `(240-208)/240×100=13.3%` 被二元算式规则误拦 | 有界Decimal解析，绑定selected quotes中的baseline、候选对象、同单位操作数、分母与舍入 | 不执行模型代码，不通吃任意算式；换对象、错分母或缺原始数值不能因阈值数字相同而通过 |
| “资料未提供最大恢复时间”扩大为“历史未测量” | 所有verdict的解释都检查明确历史否定；要求同对象、run与指标的所选原文支持 | 未命中窄规则不证明蕴含；未知、没记录与明确没测区分；来源本身可信度仍需评估 |
| 撤回旧单对象限定后使用“它” | 基于当前/最近用户消息识别受限对象对，要求澄清；不使用旧助手答案或pinned focus强定对象 | 普通英文指标过滤，显式改正优先；不声称解决一般指代消解 |

75秒调用边界、最多6次decision+1次review、1800/2600输出token上限及无自动重试保持不变。增加报告容量可能增加复核耗时；需要用同预算观察，而不是先宣称提速。

## 工程验证

[冻结候选的本地回归](evaluations/conversation-round6/regressions.json)：**939 Python、66 JVM（34真实MySQL/Redis、0跳过）、58 UI**。候选的[push/PR CI](evaluations/conversation-round6/implementation-ci.json)各4个job通过，包含Python3.11/3.13和Java21真实跨服务验证。

[离线算术回放](evaluations/conversation-round6/arithmetic-before-after.json)取第五轮两份Cedar原始草稿，旧数值finding均为1，新检查均为0；显式错误结果恰好等于引用阈值的合成负例从漏检变为拒绝。0次外部模型调用，这是确定性检查的改变，不是最终模型答案通过率。

## 开发中遇到的难题

最有代表性的回退发生在**跨语言错误协议**：Python新增`review_coverage_slots`事件，Java虽接受v7版本，却没有接受该事件。快速成功路径正常；review超时后整个agent trace被Java错误结果清理器丢弃。首次受控检查实际**43/64**，原件保留；扩充明确白名单并补“read+coverage+review_timeout”持久化断言、重新构建后**64/64**。两次使用相同6秒预算和8秒脚本延迟、真实JVM/MySQL/Redis/Python，外部模型调用均0。见[首次失败](evaluations/conversation-round6/runtime-fault-first/summary.json)及[修复后证据](evaluations/conversation-round6/runtime-fault/README.md)。这是本机工程回归及故障排查，不是生产事故履历。

另外三类过程记录均保留：

- [覆盖与接入记录](evaluations/conversation-round6/coverage-development.md)：旧测试把“未知数值”与“从未测量”混在一起；review只看已有命题的限制；同轮“只看X，不是Y”的缩小范围；Maven success实际跳过34项集成测试，发现后启用开关重跑，最终不把skip计为通过。
- [算术开发记录](evaluations/conversation-round6/arithmetic-development.md)、[范围与澄清记录](evaluations/conversation-round6/scope-development.md)：为何不用eval/通用表达式执行或额外模型修复，怎样处理对象、单位、缺失、矛盾及保守误拒边界。
- [评测脚本审查与推理前修订](evaluations/conversation-round6/pre-run-revision.md)：输入只校验启动时、未知token被聚合值掩盖、无效评分指针也能通过，分别修复并加负例；v1原字节和v2保留，题目/gold未变、修订时模型调用0。

[只读接入复核](evaluations/conversation-round6/source-review.md)属于开发团队AI复核，不是安全审计或人工独立验收。

## 真实模型回归

按[冻结协议](evaluations/conversation-round6/protocol.md)在独立工作区顺序复跑4场景、16份资料、10个已暴露轮次。保留两套原gold并分别统计，额外报告核心结论、明确漏项、gold细节门槛、错误拒答及证据范围；不合并成通用准确率。唯一一次采集已经封存；[完整报告](evaluations/conversation-round6/REPORT.md)和[逐项评分](evaluations/conversation-round6/assessment/annotations.json)保留原始结论。

| 组别 | 第五轮 | 本轮持久化响应 | 本轮原gold严格结果 |
|---|---|---|---|
| Cedar/Harbor六轮 | 6/6响应、严格2/6 | 0/6；2首轮失败、4依赖阻断 | 0/6，无最终回答可评分 |
| Alder Relay/KiteLoad四轮 | 2/4响应、严格0/4 | 4/4；3事实报告+1澄清 | 1/4，仅ar-t1通过 |

本轮总计6次任务提交、17次模型尝试，15次已知响应的token下限58,199；另外两次用量与provider接受情况未知。没有重提、自动重试或外部模型补跑。这是全已暴露的AI合成开发回归，不能称独立人工评测、通用准确率提升或生产可靠性验证。

## 首次真实回归反而暴露的新问题

**Harbor的错误拒绝可以确定归因到新增代码。** 原始8条草稿已有“Quill因latency不达标而无法通过验收”和“Sable因成功率未达标而无法通过验收”，新coverage却只认“总体/验收结论”等字样，准备重复补两槽，触发八条上限。原引文选择、逐条校验均可通过；只有补槽函数对同一原始草稿稳定复现异常。随后额外decision只有约8.18秒额度，约55.66秒结束，仍保留约19.36秒review时间。[完整归因与原文位置](evaluations/conversation-round6/assessment/failure-review.md)区分了已证实拒绝原因与不能证明的反事实：去掉误拒绝也不保证模型一定按时交付。

**Cedar是另一条路径。** 草稿已有8条、补槽为0，review在约50.78秒才开始、剩余24.22秒，最后在总deadline附近超时。没有供应商服务端排队或推理计时，不能把原因写死为模型平台慢、网络慢，或自动补槽增大报告。

**另外两类问题仍未解决。** ar-t2新集群数值正确，但漏答用户明确要求的“哪些变更、哪些保留”；kl-t2已检索到缺失证据清单，却只问用户有没有日志，没给可答的未知结论和最小证据要求。kl-t1核心判断正确，但原gold要求更细，保留严格失败和门槛异议。所有语义判断均由开发侧AI阅读报告、引用、检索后作出，另一AI内部复核不改变其身份。

## 封存后的最小修复

保留`dd225edf`原失败和评分，另做后置离线修复：识别明确的候选整体通过/不通过谓词；真或假的整体命题都交给原review，不由关键词确定真伪。原Harbor8条草稿离线保持8条且不新增槽；若报告确实满容量又缺必答总判定，则明确`coverage_contract_error`结束，保留缺项和来源，不再调用模型尝试修补，也不删除指标或绕过检查。

后置交叉审查又发现“Quill在成功率指标中通过验收”“Quill满足验收条件1”等单项措辞会被误认整体；收窄变体并加入负例。这个取舍说明：扩大词法规则可能减少一种误拒，同时引入漏补，必须保存反例而非只验证原失败句。

后置最终测试及源码身份见[单独验证记录](post-evaluation-round6.md)。这些测试不能改写本次真实推理成绩，后置源码也没有经过新的真实模型验收。

## 浏览器与主服务

浏览器实际回看了已保存报告，整页刷新后重新选择历史，核对成功报告、Harbor失败阶段和KiteLoad澄清三种结果；截图、只读请求与已知UI局限见[浏览器记录](evaluations/conversation-round6/ui/checks.json)。本轮没有前端代码改动，不把历史回看算作新的模型成功演示。

[主服务决定](evaluations/conversation-round6/main-deployment-decision.json)：升级前私有备份已生成，但因真实验收失败而没有升级。13工作区、42资料、58版本、182任务的五表摘要及四条历史API保持一致；备份未做恢复演练。只停止本轮独立QA的两个空闲进程，保留4个工作区、16份资料、6个任务、4个会话及原始产物；[清理前后摘要](evaluations/conversation-round6/qa-cleanup.json)一致，主服务/MySQL/Redis未重启。

## 简历与后续安排

可以据实写：“为Java/Python RAG Agent实现逐调用预算与来源约束，构建冻结输入、原始轨迹、语义评分和故障回放流程；定位跨语言错误轨迹丢失及覆盖检查误拒绝，保留失败证据并建立回归。”工程数量与受控故障指标必须标明本地条件；不能写‘回答准确率已提升到某百分比’、‘已抗住生产流量’或‘已完成模型私有部署’。

[草稿PR #48](https://github.com/N7766/lablens-agent/pull/48)保存代码、原失败与后置修复；相关Issue继续开放，不在质量门未过时合并。下一轮先在新的候选身份下验证后置补丁，再处理跨轮比较漏答和不必要澄清；使用用户明确小问与报告项的对应检查，继续保留缺失/矛盾负例与调用预算。之后再进入RabbitMQ/outbox/inbox。详细路线见[后端与LLM工程能力路线](backend-llm-capability-roadmap.md)，过程索引见[工程迭代记录](engineering-journal.md)。
