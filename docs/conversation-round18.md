# 第十八轮：有界历史与工具边界恢复

**收尾第7/12轮完成工程验收，剩余5轮。** 新增MySQL检查点、原快照显式恢复和保留用户原话的有界摘要。候选保持draft，主服务未升级；所有本轮模型形状请求来自本机固定响应，不是实际LLM语义验收。

## 解决的场景

Agent检索已完成，但进程在生成报告之前退出。直接重新提交会重复检索和模型决策，还可能将新资料混入旧问题；如果请求已经发给供应商，重放甚至会造成重复费用。本轮把完整工具批次保存到MySQL，并在每次可能产生外部调用前提交pending记录。只有最新记录是完整边界时，用户才能确认原快照并创建唯一恢复子任务。

恢复校验请求/运行配置摘要、原文哈希、工具调用与回复配对，沿用下一步、已用次数和活动预算。服务密钥、owner、租约与任务状态共同阻止旧worker迟到执行；恢复事务沿用现有Redis/MySQL准入和Outbox。已完成检索不会重放，未知结果没有强制重放入口。[使用与一致性契约](workspace-recovery.md)

历史压缩只移除较早的助手草稿，保留用户原话、序号、快照和SHA256，避免丢掉否定、纠正和未决目标。摘要合计最多100条、16KiB；容量不足明确停止并提示新会话，不能把旧回答压缩成事实，也不自动写入长期记忆。[摘要设计](conversation-summary.md)

## 实际验证

| 验证范围 | 结果 | 边界 |
|---|---|---|
| Python全套 | 2,146通过，1项既有弃用警告 | 包括恢复、预算、原文及摘要回归 |
| Java全套真实依赖 | 237项：235通过、2项条件跳过 | 本地MySQL9.5、Redis、RabbitMQ；CI另执行broker重启阶段 |
| 前端交互 | 112通过 | 明确审批、角色变化、跨项目迟到响应、未知POST只GET核对 |
| 真实进程中断矩阵 | 37/37通过 | SIGKILL自有worker，MySQL保留状态，重启后显式恢复 |
| 实际浏览器 | 手动恢复、报告、刷新回读、未知结果阻断 | 新建合成夹具，一次恢复POST，后续回看只GET |

在通过的矩阵中，原任务先执行一次决策和一次BM25检索，然后在边界已提交但ACK被门控时杀掉worker。重启后恢复只增加一次报告决策和一次复核：累计3次本机模型形状HTTP、1次检索HTTP。独立代理计数确认检索没有增加，不仅依赖报告里的trace。当前资料由86%改为99%后，恢复报告仍引用原版本的86%。这里的数字是**合成资料内容**，不是模型准确率。[矩阵原始结果](evaluations/conversation-round18/recovery-matrix.json)

另一任务在第二次模型请求已被本机响应服务收到、尚未返回时杀掉worker。最新检查点保持pending；恢复返回409，调用数不增长，用量未知继续保留。浏览器另恢复一个已准备的安全边界任务，刷新后回读同一子任务，并展示未知任务没有恢复按钮。[浏览器证据](evaluations/conversation-round18/browser/summary.json)

恢复报告 `result.usage` 累计3次调用、45个合成token；子任务 `execution_usage` 仅记录新增2次、30个合成token。父任务连接中断留下的未知用量没有被改写成0。不能把父任务与子任务的累计报告数直接相加，也不能用合成token估算供应商账单。

本地本轮总计9次固定响应请求：初次脚本失败1次、修正后的矩阵及浏览器准备6次、浏览器恢复2次；外部模型和embedding调用均为0。数据库保留7个任务、19条检查点与各任务唯一Outbox事件。[持久账本](evaluations/conversation-round18/durable-ledger.json)用于证明落库与关联，不声称MQ唤醒速度或生产吞吐。

## 难题与修正

- **检查点ACK本身消耗预算。** 决策前剩余时间可能在等待数据库回执后耗尽。现在在ACK后再次检查取消、总截止和复核预留，再递增调用数；假时钟回归验证零额外请求。保存边界对丢失ACK保守计入最多3秒回调预留，不能把它叫实际处理耗时。
- **旧worker与新会话竞态。** 恢复子任务没有会话序号，原busy查询会漏掉它。改为关联父任务检查，活动恢复阻止新轮次，不重复增加会话revision。
- **累计用量重复计算。** 恢复结果带历史用量；JVM核对不可变检查点后计算本次增量。未知值仍未知，校验前失败明确标为本次执行，不能复制未经验证的历史计费。
- **跨语言重新编码漂移。** 状态包含检索浮点分数和耗时，Java重新编码可能改变精确字节。保存规范JSON字符串并核对其UTF-8摘要，再在Python验证内容；没有放松来源校验。
- **压缩与最终复核容量不一致。** 只检查最近20轮之外的摘要还不够。JVM同时预检最终复核需要合并的用户原话，超限时不创建任务、Outbox或新revision。
- **初次全量与脚本失败。** Python最初5项失败来自旧v11断言及live夹具携带新增Agent字段；Java专项曾关闭MQ却断言Outbox。首次真实实验又在worker尚未就绪时提交恢复子任务，被正常调度为失败。保留原件，仅修正夹具/脚本及断言；未让生产调度器自动重试。[初次运行](evaluations/conversation-round18/initial-run/matrix.json)、[Python初次日志](evaluations/conversation-round18/validation/python-initial-failure.log)、[Java初次日志](evaluations/conversation-round18/validation/java-initial-fixture-failure.log)

两位AI代理进行了跨模块源码复查，发现并跟踪ACK预算与会话busy问题；[Python恢复审查](evaluations/conversation-round18/review-python-recovery.md)、[Java/UI审查](evaluations/conversation-round18/review-recovery-ui-java.md)不是独立人工评测。

## 简历表述与限制

> 为双服务Agent实现MySQL持久化工具检查点，通过执行owner/租约校验、原快照审批与事务Outbox支持单次显式恢复；在真实worker进程中断测试中验证已完成检索不重放、未知请求阻断与累计预算延续，完成37项跨服务检查。

> 设计保留用户原话和来源哈希的有界会话摘要，处理上下文压缩时的否定/纠正保留、跨语言摘要一致性与容量拒绝，并区分历史累计用量和本次执行增量。

本轮仅一层任务恢复分支：原失败会话轮次不改写，恢复报告不会自动合入后续会话上下文。未知结果提供持久状态核对和阻断，尚无供应商结果/账单自动对账。默认需显式开启检查点配置。旧真实模型失败、独立语义验收、GPU私有部署和生产容量仍未完成，不能因工程通过宣称这些能力已验收。

101份运行源与候选JAR在实验前冻结，运行后核对未改变；仅测试脚本就绪顺序修正保留两份冻结记录。[协议](evaluations/conversation-round18/protocol.md) · [源码/JAR冻结](evaluations/conversation-round18/source-freeze.json) · [运行后核对](evaluations/conversation-round18/source-verification.json) · [资源清理](evaluations/conversation-round18/cleanup.json)

下一轮为第8/12轮：比较GPU资源、明确费用上限、固定模型及部署版本，再接入Agent。后续依次进行模型容量与新题、备份回滚、缺口补验和最终材料交付。
