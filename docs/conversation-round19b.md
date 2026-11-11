# 第十九轮续：报告决策协议与有界 Agent 验收准备

2026-11-11。收尾第8/12轮继续，**仍为7/12轮完整交付**。本次修复并验证本地工程链路，没有新增付费GPU或真实模型请求，主8081/8765未升级。真实私有模型的完整Agent验收仍待新一轮有界运行。

> 后续状态（同日）：上述是准备阶段的历史结果。其后已获授权并完成一次新租用，因虚机AVX缺失在引擎启动阶段失败，0模型请求，资源已释放；离线预检修复与2485项回归见[实跑复盘](conversation-round19b-runtime.md)。

## 问题和修改

上一次真实响应把 `a1` 同时放入保留与替换列表。旧校验正确拒绝，但旧JSON schema不能表达两个列表之间的互斥关系。新候选 `scoped-review-decisions-v4` 把每个原始ID设为必填对象属性，每个ID只有一个 `retain` 或 `replace` 决策；不允许保留的行只提供replace分支。

替换不能改写原命题，服务器按原顺序恢复内容；保留也必须经过原有证据、历史与因果检查。策略绑定当前草稿、证据和契约哈希。原始JSON还要递归拒绝重复键、转义后重名键和非有限值，避免字典解析静默覆盖决定。

默认协议保持v3，候选worker显式启用v4；未知协议在发送前失败。探针升级为v2，冻结时选定协议，运行时不能覆盖或自动回退。旧的真实失败、分数和封存文件全部保留，不能把新工程测试写成旧模型结果改善。

## 完整本地链路

在独立JVM8086、worker8770、Redis6383和新MySQL schema中，实际执行认证、导入、BM25索引、会话任务、HTTP工具检索、来源核验、报告保存及重新登录回读。MySQL daemon与主服务共用，数据/schema隔离；本轮关闭MQ和checkpoint，没有重测它们的交付能力。

| 本地固定响应场景 | 实际终态 | 检查 |
| --- | --- | --- |
| SFT基线与历史缓存证据 | SUCCEEDED / answered，基线supported、历史缓存insufficient | 16/16 |
| 未提供GPU费用 | SUCCEEDED / refused，无虚构引用 | 17/17 |
| 首次模型HTTP延迟超预算 | FAILED / agent_timeout，未知用量、无报告；晚到响应不改终态 | 10/10 |

共43项。三例使用本机脚本固定输出，模型形状HTTP请求为3+3+1；usage是合成数值。**这是实际存储与传输的工程验收，不是模型推理、独立语义评测或容量测试。** 早期较弱断言的预演另存，最终矩阵增加了资料版本/原文偏移/哈希、索引复用和会话终态一致性检查。

另做一次[网关组合预演](evaluations/conversation-round19b/gateway-integration/combined-summary.json)：实际probe程序的3项兼容检查通过，网关核对回执后切换Agent阶段，再经真实JVM/worker完成3次脚本响应及报告回读，共6次本地上游请求、零外部调用。三例矩阵与这次预演分别统计，重复的报告断言不追加成新的质量样本。

## 付费边界也需要实现

生产Agent默认最多可能请求7次模型，不能只按理想路径3次估算。新独立loopback网关在发送前独占预约并fsync，兼容阶段3次，Agent阶段限定2次决策加1次复核；先验证兼容探针的通过凭据及响应哈希，再开启Agent配额。异常、断连和未知结果消耗配额，不退款重试；阶段只能启动一次，重启不能重置。

只有网关持有上游key，worker只知道本地网关地址/key。它是本次实验的协作式边界，不是防本机管理员绕过的安全隔离，也不会停止云平台计费。GPU实例仍需单独销毁并核对平台状态。

## 验收与剩余边界

本地Python全量 **2465 passed、1项上游弃用警告，76.78秒**，见[输出](evaluations/conversation-round19b/python-tests.txt)。其中新增的协议、probe、driver与网关保护均计入全量；专项数不能再次相加。独立AI对[数据库及回读原件的审查](evaluations/conversation-round19b/private-agent-independent-review.json)确认语义字段一致；这不是独立人工模型评测。Java/前端未修改，本轮使用已核哈希的既有JAR，不把旧测试数当成本次重跑。

网关复核还实际复现了“SIGTERM后补齐请求体仍产生新调用”：[修复前](evaluations/conversation-round19b/gateway-stop-before.json)停止前0次、停止后1次；[修复后](evaluations/conversation-round19b/gateway-stop-after.json)先停止准入，新的预约被拒绝，已在途请求有界收尾。对应真实子进程回归计入51项网关专项，不能只用普通退出码证明停止语义。首次复现未保存源码哈希与精确时间，这两个字段保留null，不补造。

- 新schema尚未在真实vLLM/XGrammar编译与生成；上游源码/测试支持该结构，不等于本候选实测通过。
- 本轮没有真实模型新题、独立人工出题、吞吐/TTFT/P95或生产流量证据。
- API新登录回读已验证；本候选没有新增浏览器操作验收。下一次真实运行将只读回看已有报告。
- 新租用计划为同一固定模型、单张A6000 48GB级、100GB磁盘，最多2小时/10USD、总预算仍500CNY；3次兼容通过后才执行单题Agent最多3次，总上限6次。失败即停止，导出并释放，不补题重试。**准备时点该运行尚未获单独授权；后续授权和实跑结果见上方复盘，不能据此重复租用。**

[预声明协议](evaluations/conversation-round19b/protocol.md) · [本轮复现步骤](private-agent-acceptance.md) · [原始GPU失败](evaluations/conversation-round19/scoped-review-failure-analysis.md)
