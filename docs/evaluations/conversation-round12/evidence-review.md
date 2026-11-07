# Round 12 负载证据复核

复核日期：2026-11-08（Asia/Shanghai）。这是 **AI 开发复核**：审阅者参与过负载运行器编写，因此不属于未参与开发的独立人工评测。本记录重新读取逐请求、逐任务、worker 事件和系统采样，不以运行器的 `passed=true` 代替核验。

本次证据支持：在指定本机合成负载下，候选实现将跨 4 个工作区的活动任务采样峰值约束为 12；过载明确拒绝、已接受任务全部完成，降载后自动恢复。它不证明真实 LLM 吞吐、线上 SLA、长期稳定性、集群容量或独立答案正确性。

## 实验身份与可比性

- [基线 manifest](load-baseline/manifest.json)：运行 jar 来自 `a55dd036cb11f4f11ae98cc4145f55c058c76754`，SHA-256 `a1fb4cd07dc48bcfceabbc014d222071f8965da2c8c1ee3884ba4f5db8e42c4f`。
- [候选 manifest](load-candidate/manifest.json)：运行 jar 来自冻结提交 `75ce36a33757f247b6513f5111275738a075ccfa`，SHA-256 `610ec707d54705cb943419b5451ede60a379b2fb53846ba3b888628b29a77f60`。
- 协议、运行器、受控 worker 和 `rag.py` 的哈希在两组 manifest 中一致，运行器前后校验未变。jar 文件哈希与操作者启动记录一起确定部署身份；单凭磁盘文件哈希不能证明任意正在运行进程加载了它。
- Apple M4 Pro、12 逻辑核、24 GiB 内存；Java 24.0.2、MySQL 9.5.0、Redis 8.2.2。JVM `-Xms128m -Xmx512m`，Redis 128 MiB，MQ 关闭，真实 MySQL/Redis，真实 BM25/extractive；无模型和 embedding 调用。
- 同机顺序基线→候选，各只执行一次正式负载；独立空任务数据库、Redis namespace 和 fresh worker。两组均为 4 工作区、2 执行 worker、400 ms 固定等待、3×60 秒、2/20/2 req/s。基线只有工作区上限 8；manifest 中的全局 12 对基线是比较阈值，并非旧 jar 已实现的限制。
- 发送端配置最多 32 个未完成 HTTP 提交，但**两组实际峰值均为 1**。过载阶段提交间隔 50 ms，而实际提交响应均不足 35 ms。因此这是开放到达率与业务积压实验，不能写成“32 并发 HTTP 压测”；真实数据库的 16 并发准入测试是独立证据。

## 从原始记录重算

| 指标 | 基线 | 候选 |
|---|---:|---:|
| 预定 / 实际发送 / 唯一 Idempotency-Key | 1440 / 1440 / 1440 | 1440 / 1440 / 1440 |
| 已接受（均 HTTP 202） | 558 | 539 |
| 明确拒绝 | 882 | 901 |
| 未发送 / 传输错误 / 非预期 HTTP | 0 / 0 / 0 | 0 / 0 / 0 |
| 已接受任务最终 SUCCEEDED | 558 / 558 | 539 / 539 |
| worker started / completed / failed | 558 / 558 / 0 | 539 / 539 / 0 |
| 系统状态成功采样 / 失败采样 | 683 / 0 | 682 / 0 |
| 全局活动任务采样峰值 | 32 | 12 |
| dispatcher / 受控 worker 最大执行并发 | 2 / 2 | 2 / 2 |
| 已接受任务持久排队 p50 / p95 / p99，ms | 6002.848 / 6225.847 / 6257.715 | 1983.190 / 2073.528 / 2085.200 |
| 已接受任务持久端到端 p95，ms | 6640.847 | 2485.195 |
| 全部提交响应 p95 / max，ms | 16.942 / 34.717 | 20.912 / 34.748 |
| 发送器落后预定时刻 max，ms | 12.264 | 5.334 |
| 最终 QUEUED / RUNNING / CANCEL_REQUESTED | 0 / 0 / 0 | 0 / 0 / 0 |

排队由 `started_at − created_at`、端到端由 `finished_at − created_at` 计算；分位数用独立的 `statistics.quantiles(..., method='inclusive')` 重算，和运行器线性插值一致。提交响应统计包括所有接受和拒绝响应，没有只挑快速成功请求。已接受任务全部取回，无终态缺失、失败或缓存命中；已接受集合的大小不同，因此两组排队 p95 是各自准入后分布，不是固定同一请求集合的配对提速实验。

候选以更早拒绝保护积压上界：本次少接受 19 个请求，队列等待缩短，但提交 p95 从约 16.94 ms 增至 20.91 ms。**没有证据支持“吞吐提升”或“模型提速”**。

| 阶段 | 基线接受 / 拒绝 | 候选接受 / 拒绝 |
|---|---:|---:|
| under，0–60 秒，2 req/s | 120 / 0 | 120 / 0 |
| over，60–120 秒，20 req/s | 319 / 881 | 300 / 900 |
| recovery，120–180 秒，2 req/s | 119 / 1 | 119 / 1 |
| recovery 后 30 秒（上行子集） | 60 / 0 | 60 / 0 |

两组恢复阶段开始都仍有 1 次容量拒绝，不能写成“降载立即全部成功”。采样首次观察到恢复阶段 QUEUED=0 的时刻分别为第 130.460 / 123.187 秒；这是采样时刻，不是精确恢复耗时承诺。整个阶段及最终逐任务取证用时约 180.519 / 180.482 秒，属于 3 分钟阶段实验，不属于长时间 soak test。

## 逐项对账补强了运行器的哪些检查

1. 每个 `sequence` 与冻结的完整 schedule 一一相同；全部 1440 个 `admission-<run_id>-<sequence>` key 在 HTTP observations 中各出现一次，无自动重试。
2. 对 accepted `task_id`、最终任务记录 `task_id`、worker `started.task_id`、`completed.task_id` 分别构建 Counter：每组四个 Counter 完全一致，每个计数均为 1。运行器只对 worker 总数做检查，本次补充了逐 ID 核验；可表述本场景每个接受任务执行一次，不能推广成端到端 exactly-once LLM 保证。
3. 初始数据库任务总数为 0；最终全部状态计数之和分别恰好 558 / 539，与接受数相等，且所有接受 ID 均有 SUCCEEDED 持久记录。该隔离数据集没有出现额外任务；仅“拒绝响应不带 ID”本身不足以证明没有数据库副作用。
4. 基线 882 次拒绝逐条都是 `HTTP 429 / queue_full`，均没有 Retry-After；这是旧契约事实。候选 901 次逐条都是 `HTTP 503 / global_queue_full`，均有 `Retry-After: 1`。候选工作区 429 契约由 Java HTTP 集成测试覆盖；本次实际负载先达到全局上限，没有观察到候选工作区拒绝。
5. 终态结果 usage 的 `model_calls`、`embedding_requests`、`embedded_inputs` 全为 0，`provider_usage_unknown=false`；worker 原始事件一致。这里验证的是零 provider 受控链路，不是模型业务容量。
6. 候选采样中全局活动数最大 12，最终 `admission.observed_pending_high_water=12`；该高水位仅覆盖当前进程成功创建和状态采样。它与普通采样都不是连续完整历史。事务原子性由并发集成测试另证，不把未采到超限当作穷尽交错证明。

[基线结果](load-baseline/summary.json) 的 14/14 与[候选结果](load-candidate/summary.json) 的 18/18 反映各自预声明检查。它们判定集合不同，不能把 14→18 当作质量得分改善。

## 资源观测边界

每组各保留 36 次 actuator 资源采样。JVM `process.cpu.usage` 报告的比例最大值为 0.02210 / 0.02259；同机 `system.cpu.usage` 最大值为 0.19453 / 0.23401。`jvm.memory.used` 未按 area/pool 过滤，聚合采样最大值为 228,367,760 / 227,699,960 bytes，约 217.79 / 217.15 MiB。

这些是 JVM 暴露的采样值，不是 OS RSS、全机瞬时峰值或单核占用率。`jvm.memory.max` 聚合值约 1.862 GB，包含多个内存池，不能将它当作 heap `Xmx`；配置 `-Xmx512m` 与聚合值不是同一指标。两次顺序运行背景系统负载不同，不根据这些小差异声称 CPU/内存优化收益。

## 原始证据与复算方式

两组公开的 requests、tasks、samples、observations、worker 共 10 个 gzip 均已解压，并与私有原始 ledger 逐字节比较一致。原始及压缩 SHA 保存在统一的[压缩清单](compression-manifest.json)。候选使用负载结束即封存的 worker 副本，后续 Redis 故障演练产生的新任务不包含在负载指标中。

关键 raw 身份：

| 原始文件 | SHA-256 |
|---|---|
| baseline requests | `6a26bcb68f35d94e755d4ead1d17dc355468dfb7b90bff857dd884fe62551a02` |
| candidate requests | `ef3a1d0aec9fbe48cebe4a3e28258707910dfb4e0abbb466b6f447127ee91d5a` |
| baseline worker | `2cdc834df052f809ebf35186d487acb96c8cf23b4de9c9220e76692e0c4411b8` |
| candidate worker（负载专用） | `21f080be2623e9d871fb1c6c00d33dcb39f0a9361c18e566d8d6c7cfeaa59ac9` |

下面仅使用 Python 标准库，可从仓库根目录独立验证关键集合与排队分位数，无网络和服务调用：

```python
from pathlib import Path
from collections import Counter
from datetime import datetime
import gzip, json, statistics

root = Path('docs/evaluations/conversation-round12')
for variant in ('baseline', 'candidate'):
    path = root / f'load-{variant}'
    def rows(name):
        return [json.loads(line) for line in
                gzip.decompress((path / f'{name}.jsonl.gz').read_bytes()).decode().splitlines()]
    requests, tasks, worker = rows('requests'), rows('tasks'), rows('worker')
    manifest = json.loads((path / 'manifest.json').read_text())
    accepted = Counter(x['task_id'] for x in requests if x['outcome'] == 'accepted')
    assert accepted == Counter(x['task_id'] for x in tasks)
    assert accepted == Counter(x['task_id'] for x in worker if x['event'] == 'started')
    assert accepted == Counter(x['task_id'] for x in worker if x['event'] == 'completed')
    assert all(n == 1 for n in accepted.values())
    keys = Counter(x['idempotency_key'] for x in rows('observations')
                   if x['method'] == 'POST' and x['url'].endswith('/tasks'))
    assert keys == Counter(f'admission-{manifest["run_id"]}-{x["sequence"]}' for x in requests)
    assert len(requests) == len({x['sequence'] for x in requests}) == 1440
    def dt(text):
        return datetime.fromisoformat(text.replace('Z', '+00:00'))
    queue = [(dt(x['task']['started_at']) - dt(x['task']['created_at'])).total_seconds() * 1000
             for x in tasks]
    print(variant, len(accepted), statistics.quantiles(queue, n=100, method='inclusive')[94])
```

剩余限制：单节点/单 JVM、合成小资料、HTTP 回环、3 分钟阶段、没有真实并发用户会话和 provider 流量；状态采样与资源采样有间隔。HTTPX 的 5 秒配置分别约束网络阶段，并非通用的 5 秒墙钟 SLA。本次所有实际提交响应均不足 35 ms，因此这项区别不影响本次对账，但后续慢连接/流式响应故障场景需要单独的绝对截止时间验证。
