# Round 12 / 收尾 1：准入背压的开放到达率验收协议

本协议在正式负载前冻结。正式 `baseline` 和 `candidate` 各一次；失败、发送器漏发与超时完整留存，不删除失败重跑挑选结果。实现修复后的新实验必须另命名、重新冻结，不覆盖本目录记录。

## 被测范围和固定条件

- HTTP → 真实 JVM / MySQL / Redis → `controlled_admission_worker.py` → 真实 BM25 / extractive。Worker 在计算前固定等待 400 ms；只接受 BM25/extractive，拒绝 live、Agent、dense、hybrid。Worker 记录每个任务的开始、结束、快照、用量和进程并发。
- 不使用真实用户资料。4 个新工作区，每个仅一份自动生成的合成 sentinel 文本。每个问题和 Idempotency-Key 唯一，预期结果缓存不命中。每次独立数据库及 Redis namespace，由操作者准备；负载脚本不访问数据库、不启停服务、不删除历史。
- 单 JVM，2 个执行 worker；工作区活动任务上限 8，全局活动任务上限 12。这里活动包括 QUEUED / RUNNING / CANCEL_REQUESTED，不能将上限误写成仅等待队列长度。候选实现状态字段是验收依据；基线不具备全局上限。
- 此实验 MQ 关闭，轮询兜底保留。真实 MQ matrix 在 Java 回归中独立报告。速率限制设为每工作区每分钟 10000，隔离队列准入条件。
- 三档连续固定开放到达率，各 60 秒：under 2 req/s、over 20 req/s、recovery 2 req/s，共 1440 个预定请求。每个请求至多发送一次；下一次发送不等待前一个任务完成。
- 发送端最多 32 个未完成 HTTP 提交，单请求超时 5 秒；落后预定时刻超过 100 ms 或发送并发已满时记录 `unsent` 及原因，不追赶突发、不丢弃记录。请求数总保护 10000；总时长保护由阶段时长、HTTP 超时和最多 90 秒 drain 组成。操作者如调整参数，必须在运行前形成 manifest，基线和候选参数一致。
- 禁止将不同配置、worker delay、运行器、机器、JVM 或资料的结果解释为仅准入补丁造成的差异。脚本保留协议、源码、运行 jar SHA，环境 manifest 和固定参数；基线与候选采用顺序运行，不能排除机器背景负载和时序偏差。

## 原始记录与指标

`manifest.json` 在发送前保存协议/脚本/实际 jar 哈希、固定配置、阶段计划、版本标签和来源 commit（操作者提供）。`observations.jsonl` 留存所有 HTTP 观察；`requests.jsonl` 每个预定请求恰好一行，包含 scheduled/send/response 时间、调度落后、HTTP 状态、拒绝码、Retry-After、task ID 和未发送原因；`samples.jsonl` 保留系统状态与 CPU/JVM 内存 actuator 采样；`tasks.jsonl` 对每个已接受任务单独查询最终持久记录。Worker 原始日志由操作者一并封存。

分别计数 scheduled、sent、HTTP response、accepted（200/202）、rejected（429/503）、unexpected HTTP、transport error、unsent；必须可逐条相加对账。HTTP timeout 后服务端是否受理可能未知；不通过自动重试掩盖不确定性。终态只对拿到 task ID 的接受集合确认，transport 发生时正式验收不通过。

分阶段报告提交响应 p50/p95/p99、发送器落后、接受/拒绝数；对已接受任务报告由 MySQL 持久时间计算的排队和端到端耗时，不能仅对成功且快的请求取样。采样 QUEUED/RUNNING/CANCEL_REQUESTED、dispatcher active / max_observed_active、最老排队时长、CPU usage、JVM memory used/max；瞬时采样最大值不是连续峰值证明。严格不超限同时依赖原子事务实现与并发集成测试；压力采样只是运行证据。

## 判定

共同工程检查：调度账本完整；所有预定时刻均有记录；发送线程/连接上界成立；无意外 HTTP / 传输错误 / 未发送；所有接受请求有唯一任务 ID；drain 后全部 SUCCEEDED；当前 usage 的模型、embedding 请求/输入均为 0 且用量明确；结果缓存不命中；dispatcher 进程最大并发 ≤ 2；负载前后源码不变。拒绝不应产生 task ID。

候选额外检查：全局活动采样不超过 12；状态声明的全局/工作区上限为 12/8；过载阶段出现明确的 global 或 workspace 队列拒绝及正整数 Retry-After；恢复阶段后半段有成功受理且无容量拒绝；最终所有活动任务排空，拒绝后无需重启。工作区/global 原子不超限由 Java 多连接并发测试补强，不以一次压力采样宣称证明全部交错。

基线：旧 Round 11 jar，仍有工作区 8 的上限，没有全局 12。观察到全局 > 12 是预声明能力缺口，单独标注；不把该预期缺口记作运行器故障，也不预先强迫基线一定出现缺口。基线旧拒绝响应可能没有 Retry-After，只记录，不套用候选新契约。

不宣称吞吐提升、线上 SLA、真实用户流量、大数据容量或模型服务能力。受控延迟下的接受数不是 LLM capacity；引入背压可能降低接受数但保护数据库活动积压上界。CPU/内存从 actuator 采样，不等于操作系统全机资源峰值；没有可用值时写 unavailable，不填 0。

## Agent 诊断的最小后续入口（本轮不调用）

Round 10 `summary.json` 保留 4 次直接 review 调用，4 次超时、0 份报告、4 次 usage unknown；两场景请求约 27–30 KB，旧源码及原始计划已封存。这些记录不证明答案正确性改善，也没有完整 Agent 重验。本轮不改旧分数、不再使用已有 once-only claim 重跑。

下一轮优先新增独立小诊断协议：在现有失败输入上先离线确定最小必要证据和输出约束；分别记录排队/发送/首字节/完成/解析/证据校验，明确 provider response 与预算超时。仅在新的预算、输入哈希、单次调用上限与失败保留规则冻结后运行一次 bounded probe；若能返回才执行语义检查和一条完整 Agent 验收，不用增加 max_tokens 或盲目重试替代根因分析。
