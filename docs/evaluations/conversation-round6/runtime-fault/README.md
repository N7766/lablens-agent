# Round6 受控跨服务故障回归

使用未修改的 `scripts/evaluate_runtime_round4.py`，真实JVM/MySQL/Redis/Python连接本地脚本provider。首次运行 **43/64**：成功路径30项通过，超时路径新coverage事件被Java白名单拒绝，Agent轨迹丢失。[首次原件](../runtime-fault-first/summary.json)保留，未覆盖。

明确接受该新事件并补Java错误持久化断言，重建JAR后再次运行 **64/64**。[修复后原件](summary.json)包含source/JAR hash、数据库/API一致性、调用时序、未知用量和进程清理。相同6秒Agent预算与8秒模拟review延迟；两次外部模型调用均0、embedding0，所有自有临时进程已停止。

这些检查证明本机协议与故障轨迹的回归，不证明真实模型答案质量、商业线上稳定性或吞吐。脚本保留旧round4数据库前缀，本轮新建隔离数据库，不复用历史输出。
