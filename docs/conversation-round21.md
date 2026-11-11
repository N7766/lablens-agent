# 第二十一轮：兼容探针与真实 Agent 对齐，重新准备有界回归

2026-11-11。本轮推进第8/12阶段的真实模型复测准备，未租用新GPU、未注册平台SSH key、未调用外部模型。当前仍7/12轮完整交付、第8轮部分完成；不是收尾第10轮已完成。

## 冻结前发现的新问题

Round20已在实际Agent的复核结果和最终渲染行加入范围检查，但兼容探针 `verify_private_model.py` 还只执行恢复、通用质量规则和引用验证。独立代码审查构造了固定历史命题被标为 `current_configuration` 的输出：旧探针返回passed，生产范围检查却明确拒绝。这会造成“探针通过、真正Agent拒绝”的验收流程差异，不能将其归因为模型波动。

本轮只修改探针验证：恢复后的模型review和最终渲染行均调用与生产相同的命题范围检查。失败仍为 `structured_contract_failed`，无修复重试、无新增调用；成功详情保留两次audit，语义正确性仍为未评估。生产Agent、来源规则、固定问题和调用预算均未改动。

新增6项纯函数回归：v3/v4分别覆盖合法历史证据不足、原始错误basis在通用文案改写前拒绝、最终渲染新增错误再次拒绝。完整Python回归 **2,573通过，0失败/跳过，81.16秒，1项既有弃用警告**。这不是模型质量结果。[测试回执](evaluations/conversation-round21/engineering-validation.json)

## 真实本地跨服务检查

使用独立JVM8086、Python8770、Redis6383及新MySQL schema，模型接口连接固定响应服务8771。正常报告、证据不足、超时三种场景共 **46/46项检查通过**：19项报告检查、17项不足检查、10项超时检查。另逐项核对MySQL状态、错误码、result和error_result与API终态一致。

总计7次本地脚本响应，外部模型调用0。报告可经新认证会话回读；超时后晚到响应不能改变终态；本轮拥有的测试进程全部退出，合成数据库保留。此矩阵运行于9aa13f1，在探针专属修复之前完成；实际Agent生产文件与矩阵使用的driver没有改变，因此不将结果冒称修改后的探针实跑。[矩阵收据](evaluations/conversation-round21/local-matrix/summary.json)与[25份文件manifest](evaluations/conversation-round21/local-matrix/manifest.json)

## 下一次实际运行方案

[冻结协议](evaluations/conversation-round21/protocol.md)保持最多3次兼容请求加1道已知Agent题（最多3次模型请求），不重试。任何前置项失败，依赖步骤明确blocked；不利用空余额度补题。实际创建前核对当前报价、固定镜像和临时访问范围；总费用上限10美元、从租赁意图开始最多2小时，仍受项目500元预算约束。

当前只读候选为A6000 48GB/100GB，报价约0.495美元/小时含磁盘，传输另计；以2小时和51GB总传输按较高费率估算约1.20美元。报价非承诺或已发生账单，创建前必须复核。[报价与估算](evaluations/conversation-round21/offer-review.json)。平台[计费说明](https://docs.vast.ai/guides/instances/pricing)区分计算、存储和传输，停止实例仍有存储费用，因此收尾需确认本次实例删除。

兼容、网关和Agent计划必须使用新独占目录重新冻结，旧目录保持不变。当前探针与生产逻辑同步不等于已完成新的GPU实验。具体候选源码、计划SHA、资料快照和零请求准备结果以随后生成的冻结回执为准；任何实跑结果另建证据，不覆盖本报告。

## 新题与后续边界

另准备2道由未编写生产修复的AI协作者设计的题，覆盖缺失信息与矛盾证据，分别定义检索覆盖、结论正确性和错误拒答。题目设计在本轮可见，不标为人工盲测；它们不在本次3+3运行范围内，不是已执行样本。[新题资料](evaluations/conversation-round21/future-cases.json)与[评分说明](evaluations/conversation-round21/future-cases.md)

即使本次已知题回归通过，也还要完成新题语义验收及模型容量/成本实验。原Round19c的S5失败保持不变。GitHub候选继续draft，主服务不升级；桌面材料记录本轮问题、验证和剩余工作。
