# Round14 / 收尾3：证据入口

日期2026-11-08。候选未合并，主8081/8765未升级；隔离服务已停止，数据库和原始证据保留。[结果报告](../../conversation-round14.md) / [下一轮输入](next-round-handoff.md)。

| 范围 | 实际结果 | 原始入口 |
| --- | --- | --- |
| 最终Python回归 | **1917 passed**；1条依赖警告 | [最终日志](python-final.log)；早期1897项日志保留为python-full.log，不是同一最终矩阵 |
| Java真实MySQL/Redis/MQ矩阵 | 151发现，**149通过、2条件重启用例跳过** | [JUnit汇总](java/junit-summary.json)、[日志](java/verify.log.gz)、逐类txt与MQ事件 |
| UI回归 | **74/74** | [日志](ui-tests.log) |
| 历史读取ABBA | 3档×4窗口×155 = **1860响应正确**，180预热包含在内 | [12窗口](history/abba-windows.json)、[对照](history/comparison.json)、各目录requests.jsonl/summary |
| SQL与存储 | 1000/10000/100000真实seed行，热点90%；实际计划和分配字节 | history/seed-*、history/plans-* |
| HTTP分页功能 | **17/17观测**，10项检查通过，零修改 | [read-only汇总](runtime-readonly-once/summary.json)与gzip完整响应 |
| 混合工作 | **30提交=8受理成功+22容量拒绝**；160读取正确；总242观测、峰值12 HTTP/2 worker，0 provider | [混合汇总](runtime-mixed-once/summary.json)、[逐ID对账](runtime-ledger-review.json)、worker日志 |
| 浏览器回读 | 25→50条，按需详情，刷新后可再次读取；62 GET，0控制台warning/error | [checks](browser/checks.json)、access.log、DOM文本、[截图](browser/history-desktop.png) |
| 发送器机制 | 两个144槽位；shared漏6、isolated漏0；原漏发未改写 | [冻结协议](sender-protocol.md)、[汇总](sender/summary.json)、原始账本/心跳/观察器 |
| 模型 | 3次HTTP200，7251已知token，1份直接RAG报告；tiny精确输出仍失败 | [封存run](model/run/summary.json)、[独立AI语义审阅](model/review.md) |

混合运行前已冻结：429 queue_full 或503 global_queue_full的完整容量契约属于正常背压；普通503、rate_limited或结果未知均失败，不要求超出队列上限的30次请求全部受理。实际22次都是queue_full，没有重试。8个任务与worker的8次开始/8次完成逐ID一致，已知零用量、无缓存。最后总数100008 = 100000合成历史 + 8真实离线任务。242观测含设置/诊断/轮询，不当作242业务提交；0.247秒提交/读取窗口与2.097秒收尾不当持续容量。

混合p95：30次提交31.920ms（包括拒绝），160次25条摘要读取33.106ms。此处有并发写入、观察器和不同页大小，不能与100条纯读ABBA直接比较。连接池前后原始metrics仅描述所观测时点/累计量，不能证明每个瞬间无等待。

模型输入与报告均为AI合成资料，另一AI代理在封存后判报告级要求通过，有三项引用/措辞改进。不是独立人工、外部机构或完整Agent验收；无通用准确率结论。原Round13的格式门失败、一次漏发与更早模型评分均保持。

## 冻结身份与复现边界

- baseline `0f732e4` 对应的已归档jar，生产树与Round13测试构建一致。
- 本轮Java/UI实现 `51504e6`；HTTP测量工具修复冻结于 `ab6e92c`。[source-freeze](source-freeze.json)含86个文件哈希、两份jar哈希，[source-verification](source-verification.json)确认未变。
- sender运行冻结于 `8744a93`；model运行冻结于 `edb4bcc`，其plan与raw-seal自带完整哈希；post-benchmark运行工具冻结于 `a097988`，实际jar保持同一份。
- seed/SQL计划先运行于初版工具，记录各自script SHA；后续只修HTTP证据落盘次序，无更改seed/SQL。不能把初版manifest的null source_commit误称已记录git身份；对应初版已提交于`7edfa9f`。
- `.gz`采用mtime=0；[compression-manifest](compression-manifest.json)保存原始/压缩哈希；模型封条和旧轮次证据未改。

可在独立环境按[JVM说明](../../../services/workspace/README.md)构建Java21+服务，并使用项目venv。命令入口为 `scripts/evaluate_history_scale.py {seed,plans,measure}`、`scripts/check_history_runtime.py {read-only,mixed}`；具体资源/顺序见[history协议](history-protocol.md)、[mixed协议](mixed-protocol.md)。`harness/`是本机启动脚本快照，路径和凭据文件需按环境配置，不包含密钥，也不是跨机器一键启动承诺。先建立指定空schema并让Flyway迁移再seed；已有目录/数据拒绝覆盖，失败保留。模型脚本的已使用claim不可删除后重跑；后续模型实验需另定身份、输入、价格与预算。

单机macOS/MySQL9.5结果不能替代生产负载或Linux/MySQL8.4 CI兼容；CI状态以精确PR提交的检查为准。当前公开候选仍无公网鉴权。
