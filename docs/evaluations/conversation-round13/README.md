# Round 13 / 收尾 2：持续负载和传输诊断

候选实现冻结于 `55cf2be0c9c9c44b5f7590fb83a8a35c752fe0aa`。本轮分别验证后端资源观测与模型传输，不能把两者合并为 Agent 效果。

## 证据入口

- [负载协议](load-protocol.md)、[环境](environment.json)、[完整源码与 jar 哈希](source-freeze.json)。正式执行为 2 / 20 / 2 req/s 各 180 秒，单机合成资料、两 worker、固定等待 400 ms 后真实 BM25/extractive，外部模型调用为零。
- [执行难题](execution-notes.md)：容量仪表竞态、计数检查与预算边界。资源仪表保留缺失值，不填 0 或从 timer 总和伪造分位数。
- [Java 汇总](java/junit-summary.json)、[日志](java/verify.log)、[首次日志](java/first-verify.log)与 [Python 全量结果](python-full.log)。最终本地 139 项 Java 通过、2 项条件跳过；Python 1,838 通过。
- [模型协议](model/protocol.md)、[冻结计划](model/plan.json)、[仅一次运行](model/run/summary.json)、[原始封存哈希](model/run/raw-seal.json)、[后置解读](model/review.md)。一次 tiny HTTP 200，返回 `OK.`，不满足事先固定的 `OK` 条件，因此依赖 RAG 未执行。不能写成模型超时已解决或新题语义通过。

本轮持久化的 `run/` 不能删除、改写或恢复推理。新样本来自另一 AI 开发代理及官方资料改编，不是独立人工验收。主服务未升级、候选未合并；旧模型严格分数不变。

## 复现入口

源码提供 `scripts/evaluate_sustained_load.py`、`scripts/run_model_probe_round13.py` 与相应离线测试。`harness/` 保存本次本地启动参数；环境路径需在复现机上替换，私有凭据文件不发布。使用新的隔离库、Redis namespace 和输出目录，先按协议建立空状态。正式请求的完整命令记录在负载 manifest；不要向已有主库或原证据目录重放。

压缩日志采用 gzip mtime=0；压缩清单同时记录原始字节和压缩文件哈希。模型 `raw-seal.json` 只覆盖原始运行文件，后置评审另列在发布清单。JUnit XML 留在本地，因为包含环境属性；公开 TXT、日志和计数足够追溯测试边界。
