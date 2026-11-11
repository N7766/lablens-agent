# 第十九轮再次实跑：私有模型 Agent 链路与语义边界

2026-11-11。**固定私有模型部署成功，3 次兼容请求全部通过；随后真实 Agent 使用 3 次模型调用完成检索、核验、报告和持久化，17 项机械检查通过。独立 AI 语义复核发现一项命题与判断标签不一致，完整语义验收未通过。** 本轮共 6 次模型请求，无重试；收尾仍为 **7/12 轮完整交付、第 8 轮部分完成**。主服务没有升级。

## 本轮要证明什么

上一轮在虚机缺少 AVX 时启动失败，未产生推理请求。本轮重新冻结源码、资料和请求计划，用新的隔离环境验证：模型是否能按照生产协议选择工具，工具是否真的检索和核验资料，报告能否持久化，以及重新认证后是否还能读到同一份历史结果。模型答案另按输出前冻结的语义标准检查，不能由 HTTP 200 或任务 `SUCCEEDED` 代替。

本轮源码为 `f29a21a981bb84257696b7222a1e71fbcb0e4f1c`。部署保持 Qwen2.5-7B-Instruct revision `a09a35458c702b33eeacc393d103063234e8bc28`、vLLM 0.32.0 / 包版本 `0.32.0+cu129`、固定镜像 digest，以及固定哈希的 TorchCodec `0.17.0+cpu` 覆盖层。基础镜像经过明确记录的包替换，不能称为未经修改的官方镜像。[运行协议](evaluations/conversation-round19c-runtime/runtime-protocol.json)、[源码冻结](evaluations/conversation-round19c-runtime/source-freeze.json)、[覆盖层回执](evaluations/conversation-round19c-runtime/public-ready/codec-overlay.json)

用户批准专用 A6000 48GB 级、100GB 磁盘，3 次兼容通过后最多再做 3 次 Agent 请求；上限为 2 小时 / 10 美元，并受 500 元总预算约束。本轮独占实例为 `60169027`，租用意图记录于 05:21:22 UTC，硬截止为 07:21:22 UTC。其他聊天的实例不属于此次运行范围。隔离本地端口为 JVM 8086、worker 8770、Redis 6383，使用新数据库和账号；MQ 关闭、环境文件自动加载禁用，生成请求只经过有持久化配额的本地网关。原 8081 / 8765 服务未升级。

## 问题、材料与真实工具轨迹

问题是：“**资料是否包含 SFT 基线？配置能否证明历史运行启用了缓存？**”

冻结资料只有两份合成文件：结果文件明确写有“SFT 基线：准确率 86%。”，配置文件有 `cache_enabled: true`，同时明确未提供对应历史运行日志。这里的 **86% 只是测试资料中的数值**，不是本轮模型准确率，也不是实际训练实验指标。这是一道已知回归题，主题与兼容检查相关，不能作为未见题或人工盲测。[冻结问题](evaluations/conversation-round19c-runtime/report/plan.json)、[有序资料快照](evaluations/conversation-round19c-runtime/report/ordered-snapshot.json)

真实链路如下：

1. 客户端向隔离 JVM 提交任务，冻结 workspace revision 2 和两份资料版本。
2. 第一次模型决策选择 `search_workspace`；worker 实际执行 BM25 检索并复用索引。
3. 第二次模型决策选择 `verify_and_submit_report`；代码核验来源、引用文本、偏移、资料哈希和快照绑定。
4. 第三次模型调用按 `scoped-review-decisions-v4` 复核候选结论；服务端解析结果并形成最终报告。
5. JVM 持久化任务与会话。独立新进程重新认证，GET 任务和会话，随后以只读 MySQL 事务核对持久结果与资料版本。

任务 `ffea3c14-65fd-4989-8185-570d3cf11898` 最终为 `SUCCEEDED`，Agent 状态 `completed`，两步工具调用，没有命中结果缓存，也没有 embedding 请求。[任务与工具轨迹](evaluations/conversation-round19c-runtime/report/run/task-final.json)、[机械检查](evaluations/conversation-round19c-runtime/report/run/summary.json)

## 分层结果与指标

| 层次 | 实际结果 | 解释范围 |
| --- | --- | --- |
| 环境与启动 | A6000 49,140 MiB；72 个逻辑 CPU 均暴露 AVX；启动与认证就绪检查通过 | 当前虚机检查，不是对所有 A6000 供应商环境的保证 |
| 部署兼容 | 3/3 通过：中文输出、结构化工具动作、生产复核协议 | 固定样本的协议兼容，不是质量或容量验收 |
| 真实 Agent | 3 次模型调用；17/17 机械检查通过 | 已执行检索、来源核验、复核、报告和历史读取 |
| 再次读取历史 | 新进程认证后 5/5 检查通过，任务响应字节哈希与首次读取相同 | 无新增任务、模型请求或重试 |
| MySQL 持久化 | 15/15 修正后的只读核对通过；1 task、1 conversation、2 documents、2 versions | MQ 关闭，outbox / inbox 为 0 不代表 MQ 投递验收 |
| 独立语义复核 | 缓存项的命题与标签作用域错位，S5 不通过 | 链路成功不等于最终报告全部正确 |

兼容阶段耗时 **9.308661 秒**，供应商报告 prompt 3,430 / completion 140 / total 3,570 tokens。Agent 返回结果的 `latency_ms` 为 **12,248.049 毫秒**，供应商报告 prompt 9,699 / completion 355 / total 10,054 tokens；三次调用均由网关记账，无未知 usage。这些是各一次串行运行的观察值，不能据此推导吞吐、并发容量、p95 或生产 SLA。[兼容摘要](evaluations/conversation-round19c-runtime/compat/run/summary.json)、[Agent 原始任务结果](evaluations/conversation-round19c-runtime/report/run/task-final.json)、[运行与回读汇总](evaluations/conversation-round19c-runtime/public-ready/run-outcome.json)

数据库审计最初错误地要求首轮快照的会话 revision 为 0。源码显示：验证提交前 revision 后，先递增，再把已接纳轮次的 revision 1 写入快照、会话和 task sequence；实测一致。首次审计失败和修正说明均保留，没有改动任务、数据库或模型输出。这是审计假设修正，不是产品故障修复。

## 已确认的语义缺陷

最终报告正确识别资料包含 SFT 基线。缓存项则输出：

> 【证据不足】配置启用了缓存。  
> 配置文件中 cache_enabled 为 true，但没有提供对应的历史运行日志来证明缓存实际启用了。

其中 `basis=current_configuration`，命题是“配置启用了缓存。”，资料已经直接支持当前配置值为 true；“证据不足”适用于另一命题——**历史运行是否实际启用了缓存**。解释文字区分了配置与历史，但标题命题、basis 与 verdict 未对齐。第三次模型复核对 a2 做了 replace，仍留下这一作用域错位。

因此，不能把该输出记成“模型断言历史运行一定用了缓存”，也不能因为解释中承认未知就判定整份报告正确。输出前冻结的 S5 要求措辞与判断一致，本项失败；下一步应让候选命题和复核决策明确区分“当前配置事实”与“历史运行行为”，先加入离线回归，再冻结新样本开展新的授权评测。[输出前语义标准](evaluations/conversation-round19c-runtime/independent-semantic-rubric.md)、[独立 Agent 复核](evaluations/conversation-round19c-runtime/public-ready/independent-agent-review.md)

独立审阅由另一 AI 代理进行，采用输出前冻结的标准；不是未参与项目的人工出题者盲测，也不是大样本质量评估。17 项机械检查中的 `historical_cache_insufficient` 只能确认程序规则被满足，无法替代命题作用域的语义审阅。所有失败判定保留，本轮未追加调用寻找成功样本。

兼容探针还暴露了另一个规则边界：模型原始解释正确区分配置与历史，却因加入材料内已有的 `cache_enabled` 标识符，被 `insufficient_review_introduced_named_subject` 规则拦截并留下 withheld original。兼容协议通过不代表原解释未经改写；该误拦记录与 Agent 的命题错位分别保留。[兼容性独立复核](evaluations/conversation-round19c-runtime/public-ready/independent-compat-review.md)

## 部署排障、身份与工程证据

上轮确认的 UCX / AVX 问题已转成启动前检查。本轮在真实租用虚机上观察到所有逻辑 CPU 均满足 AVX 条件，随后模型服务成功启动；这补充了新 guard 的一次真实正向运行，不能证明所有原生依赖或所有硬件组合都兼容。容器可见内存为 87,181 MiB，不能写成供应商保证分配的内存配额。[宿主预检](evaluations/conversation-round19c-runtime/public-ready/hostcheck.json)

就绪诊断曾因镜像缺少可选工具 `ss` 而失败，保留该诊断失败后改用有界读取 `/proc/net/tcp` 与 `/proc/net/tcp6` 核对监听状态；没有为此安装包、重启模型或追加生成请求。后续检查确认服务仅监听远程 loopback，认证的模型列表接口可用。[就绪与诊断记录](evaluations/conversation-round19c-runtime/public-ready/model-readiness.json)

四个权重分片、config、tokenizer config、权重 index、LICENSE 和 chat template 的磁盘哈希均匹配固定 revision。该证据证明核验时缓存文件对应预期内容，**不证明运行中服务实际加载的每个字节**；tokenizer 原始 payload 缺少预先固定的对照哈希，完整镜像运行态身份也未被证明。[磁盘模型身份](evaluations/conversation-round19c-runtime/public-ready/model-identity.json)

兼容阶段保留完整响应证据；Agent 阶段保留规范化工具动作、解析后的 review、引用核验和最终任务结果，没有保留完整 HTTP 原始响应。网关响应哈希只能绑定当时观察到的字节，不能用解析结果重建缺失的原文。

工程测试继续引用本轮 GPU 前已完成的原源码结果：**2,485 项全量通过，1 项既有警告，75.94 秒**；部署专项 92 项通过。它们不是本轮重新执行的测试，也不能加到模型样本数中。另有部署 capsule 12 项、传输 bundle 11 项、归档 4 项本地检查及本轮冻结一致性回执，逐项来源和哈希见[工程证据](evaluations/conversation-round19c-runtime/public-ready/engineering-checks.json)。

## 费用、清理与可陈述范围

本轮实例于 05:21:33 UTC 创建，单次 DELETE 于 **05:41:29 UTC 返回 HTTP 200**；随后显式刷新实例页面，本轮实例已不存在，其他聊天的实例仍在且未被操作，Storage 显示 0。账户级 SSH key 列表为空；本轮 key 是实例级绑定，访问随实例销毁结束，但未独立查询后端 key 记录是否删除，不能称为执行了单独 remove-key 操作。05:44 UTC 本地隔离 JVM、worker、Redis 已退出，SSH 隧道也已退出；原 8081 / 8765 的 PID 和健康状态保持一致，隔离数据库留作证据。[供应商释放回执](evaluations/conversation-round19c-runtime/public-ready/provider-cleanup.json)、[本地清理](evaluations/conversation-round19c-runtime/public-ready/local-cleanup.json)

平台本轮实例独立账目暂记 **$0.18**：GPU $0.13、存储 $0.01、下载 $0.04、上传小于 $0.01，界面有舍入且近期活动可能延迟；这不是余额差，也不是最终结算。其他聊天仍有实例，不能声称账户整体无计费资源。2 小时进程截止本身不代表平台停止计费，此处释放结论来自 DELETE 与后续资源回读。本轮已用完 6 次调用额度，不再租用或重试。[费用观察](evaluations/conversation-round19c-runtime/public-ready/billing-observation.json)

可以据此陈述：在预算和调用数受控的真实 A6000 环境部署固定私有模型，接通 JVM / MySQL / Redis 与 Python Agent 的检索、核验、复核和持久化链路；用独立认证回读和数据库只读核对验证历史一致，并在机械检查通过后识别报告的语义作用域缺陷。不能称为生产流量能力、完整模型质量通过、多轮长期记忆验收或独立人工盲测通过。

前次 [Round19 的 3 次兼容请求、2 通过 / 1 失败](evaluations/conversation-round19/runtime-run.md)与 [Round19b 的 AVX 启动失败、0 次模型请求](conversation-round19b-runtime.md)保持原结论，均不混入本轮 6 次结果。完整收尾进度仍为 **7/12，第 8 轮部分完成**，详见[十二轮计划](finalization-12-rounds.md)。公开材料按白名单导出；哈希说明内容对应，不能单独证明来源可信或验收通过。[公开证据清单](evaluations/conversation-round19c-runtime/manifest.json)
