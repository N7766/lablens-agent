# Round 12 / 收尾 1：全局准入与背压证据

2026-11-08，本机隔离实验。[冻结协议](protocol.md)、[设计与过程](design-and-execution.md)、[后续模型诊断](model-diagnostic-next-step.md)。仅使用合成资料及固定 400 ms 延迟的真实 BM25/extractive worker，不调用模型或 embedding。

## 记录入口

- `java/junit-summary.json`、`java/surefire-reports/` 和 `java/verify.log`：完整 Java 135 项发现，133 通过、2 条条件重启用例跳过。完整 XML 留在本地以免无关 JVM 属性进入公共记录；公开计数直接取 XML，不从“BUILD SUCCESS”推断测试覆盖。
- `java/mq/`：本轮真实 MySQL / Redis / RabbitMQ 的普通集成场景。实际 broker 重启仍由 CI 分阶段执行；不能把本地 skipped 两项计为通过。
- `load-baseline/`、`load-candidate/`：每组各一次、180 秒开放到达率实验的 manifest、summary 和报告。四份原始 JSONL 与 worker 原始日志采用 gzip 无损压缩，保留每个预定请求及所有接受任务的终态。
- `harness/`：实际使用的本机启动/测试脚本，包含本机路径及私有配置文件引用但不含凭据值；用于审计，移植启动以服务说明为准。`load-*/jvm.log.gz` 保存进程 PID 与实际 jar 路径；候选 JVM 日志还包含随后独立故障演练。
- `environment.json`：实测机器和依赖配置；本机 MySQL 9.5 / Java 24 与 CI MySQL 8.4 / Java 21 分开记录。
- `baseline-jar.json`：留存旧 jar 与 Round 11 证据的关联；`source-freeze.json`：本轮正式运行前源码全量摘要。
- `redis-outage/`：成功的实际进程停机、HTTP 场景、恢复与原始封存；`redis-outage-preflight-failed/`：最初因配置未声明 PID 文件而停止的预检，无 POST、无停机。两个目录不互相覆盖。
- [开发证据复核](evidence-review.md)、[Redis 故障复核](redis-outage-review.md)：AI 开发复核，不是未参与开发的人工独立评测。
- `compression-manifest.json`：压缩和解压后 SHA-256、字节数和行数。`publication-manifest.json`：最终公开证据文件摘要（不含自身）。

解压示例：`gzip -dc load-candidate/requests.jsonl.gz`。无需模型凭据即可复核 `outcome`、HTTP 状态、任务 ID、worker 逐任务次数和终态。原始 usage 的 0 仅指本轮受控离线任务；此前模型失败仍保留未知用量。

## 复现条件

先按[服务说明](../../../services/workspace/README.md)准备真实 MySQL、Redis 和 JVM。两组必须使用不同的空数据库、不同 Redis namespace；每组都重启新的受控 worker。设置 2 个执行线程、工作区活动上限 8、全局上限 12、每工作区每分钟 attempt 上限 10000、MQ disabled。JVM 使用 `-Xms128m -Xmx512m`；负载服务与主服务端口隔离。旧版不会识别全局配置，这正是要观测的差异。

```bash
python scripts/controlled_admission_worker.py --port 8770 --delay-ms 400 --log NEW_WORKER_LOG.jsonl
python scripts/evaluate_admission_load.py \
  --variant candidate --endpoint http://127.0.0.1:8086 \
  --worker-endpoint http://127.0.0.1:8770 \
  --jar ACTUAL_RUNNING_JAR --source-commit ACTUAL_JAR_SOURCE_COMMIT \
  --output NEW_EMPTY_EVIDENCE_DIRECTORY --environment-json ENVIRONMENT.json
```

运行器不启停服务、不清空数据库、不删除既有证据。默认 2/20/2 req/s，各 60 秒，共 1440 个预定槽位，客户端未完成提交上限 32；落后超过 100 ms 记录 unsent，不补发。HTTPX 的 5 秒是 I/O 阶段配置，不声称整段请求有硬实时 5 秒截止。若改变协议，使用新实验身份和目录，保留原始失败。

全局 cap 的正确性由事务锁序和并发集成测试共同支持；一次压力采样不覆盖所有并发交错。两组 accepted 集合不同，已受理任务的延迟只能作为条件分布对照。单机顺序实验无法完全排除背景负载影响，不能证明最大 QPS、LLM 服务容量或生产 SLA。
