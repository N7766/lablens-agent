# 最终实现的上下文预算故障复核

在加入工具调用选项与拒绝诊断后重跑真实 JVM→Python→MySQL 链路，**26/26 检查通过**。本目录保留独立运行记录，不覆盖前一次故障实验。

- 配置窗口4096 units；序列化输入9703，输出预留2600，安全余量1024，总计13327，首个模型请求前停止。
- 任务准确记录 `FAILED / context_budget_exceeded`；上下文身份、用户固定状态、资料快照和停止轨迹均保存。
- 模型、嵌入与token用量为0，未知用量标记为false；受控本机供应商监听器收到0个请求，没有外部调用。
- API刷新、会话历史、SQL原始记录和同键幂等重放一致。
- 检查期间源码/JAR指纹不变，测试进程均已停止。参见 [summary.json](summary.json)、[HTTP原始记录](http-observations.json) 和 [空供应商事件记录](provider-events.json)。

复现：先构建 JVM JAR，在本机 MySQL 可用时执行 `python scripts/evaluate_conversation_budget.py --output docs/evaluations/conversation-round1/budget-fault-repeat`。本机凭证路径和端口均可通过参数指定，脚本不输出凭证；已有输出目录不会覆盖。

这是合成资料、极小窗口的确定性故障实验。字节估计不是精确tokenizer；结果不证明模型质量、长期记忆或中断续跑。
