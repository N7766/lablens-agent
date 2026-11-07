# Changelog

## Unreleased — v0.13 candidate, completion round 7/12 / conversation round 18 (draft)

### Candidate — completion round 7 / conversation round 18

- Persist complete Agent tool boundaries in MySQL with service identity, owner/lease fencing, pending-operation receipts and one explicit original-snapshot continuation. Preserve cumulative budgets and separate current execution usage; unknown results cannot be replayed.
- Preserve exact attributed user history in a bounded summary, dropping assistant drafts rather than user corrections. Reject capacity overflow before further model work.
- Verify 2,146 Python, 235 Java and 112 UI tests (two conditional Java cases skipped), 37 real cross-service crash/recovery checks and browser approval/history readback. Keep the initial harness failure; all nine local scripted model-shaped requests are engineering fixtures, with zero external calls.
- Recovery remains a task-history branch and does not merge its report into later conversation context. No provider billing reconciliation, real-model acceptance or main-service promotion.
- See [Round18](docs/conversation-round18.md) and [recovery contract](docs/workspace-recovery.md).


### Candidate — completion round 6 / conversation round 17

- Add explicitly selected, shared workspace memory with current-source hashes/quotes, optimistic revision guards, expiration, deletion and immutable task admission snapshots. Treat member notes as untrusted hints, count them in context budgets and require fresh retrieval/verification.
- Interrupt established HTTP reads on an absolute deadline or cancellation; await connection cleanup before acknowledging stop. Preserve unknown usage, distinguish deadline/read phases, and retain OS resolver/cleanup limits.
- Verify 2,095 Python, 210 Java and 104 UI tests locally (two conditional Java broker-restart cases skipped), 46 real cross-service memory checks and seven controlled HTTP scenarios. Local scripted model responses exercise the full persistence/tool/report path; they do not establish real-model quality.
- Preserve initial Java compatibility regression and its fix, original timeout failures, cross-language canonical JSON correction and cancellation cleanup races. No external provider calls or main-service promotion.
- See [Round17](docs/conversation-round17.md), [memory contract](docs/workspace-memory.md) and [timeout comparison](docs/evaluations/conversation-round17/timeout-repair.md).


### Candidate — completion round 5 / conversation round 16

- Add default-on MySQL-backed sessions, BCrypt passwords, CSRF/origin checks, OWNER/EDITOR/VIEWER access, scoped workspace lists and explicit administrative adoption of unowned legacy workspaces. Existing owners cannot be replaced; revocation applies to subsequent authorization checks, not already admitted operations.
- Protect all Python API routes with service identity; propagate server-only credentials on JVM task/cancellation/index and Agent tool calls. Shared members retain existing workspace/global admission and model revision approval.
- Verify 2,049 Python tests and 184 Java tests (two conditional MQ restart cases skipped); all 53 real cross-service identity checks pass with zero provider calls. Final browser/UI and GitHub evidence are recorded separately in the delivery report.
- Reproduce eight local HTTP diagnostic scenarios: read inactivity timeout does not enforce an absolute deadline, and in-flight HTTP delays Agent budget/cancellation checks. Preserve the initial probe failures and successful diagnostic rerun; this round diagnoses but does not repair the runtime limitation.
- Prioritize interruptible absolute deadlines before memory implementation or new paid calls in completion round 6. Preserve all old model failures and unknown usage; no semantic-quality or production-deployment claim.
- See [Round16](docs/conversation-round16.md), [authentication contract](docs/workspace-auth.md) and [timeout evidence](docs/evaluations/conversation-round16/timeout-diagnosis.md).


### Candidate — completion round 4 / conversation round 15

- Add bounded MySQL bulk imports with atomic revisions, durable idempotency, queued cancellation, permanent-failure isolation and restart-safe polling.
- Persist exact snapshot-scoped chunks/vectors; validate worker identity, quarantine corrupt artifacts, fence builds, and preserve index provenance through Agent tool traces.
- Verify 1,964 Python, 167 Java and 83 UI tests (two conditional Java restart cases skipped). Run 36 frozen synthetic retrieval cases and a real worker restart; document vectors are reused with one query embedding.
- Preserve one real Agent timeout after successful tool selection/retrieval: two model attempts, no report, unknown total provider usage. No retry or semantic-success claim.
- See [Round15](docs/conversation-round15.md) and [raw evidence](docs/evaluations/conversation-round15/README.md).

### Earlier candidate — completion round 2 / conversation round 13

- Add configurable Hikari/Tomcat budgets, pool/executor snapshots and admission lock/transaction timers. Preserve existing resource limits and fix a non-atomic queue-capacity gauge before the frozen run.
- Add safe HTTPX timeout-phase diagnostics across RAG and Agent HTTP calls; retain complete-response semantics, unknown usage and unchanged request behavior.
- Pass 1,838 local Python tests and 139 Java tests (two conditional restart cases skipped). Real lock contention and pool exhaustion verify the diagnostic path.
- Retain one nine-minute synthetic run: 4,320 scheduled, 4,319 sent, 1,595 successful tasks, 2,724 capacity rejections and one sender-late unsent slot. Strict acceptance fails; no rerun or production-capacity claim.
- Retain one real tiny request: HTTP 200 and 16 tokens, but `OK.` fails the frozen exact `OK` gate. Dependent RAG and semantic evaluation did not run. Historical model scores remain unchanged.
- See [Round 13](docs/conversation-round13.md), [raw evidence](docs/evaluations/conversation-round13/README.md) and [next-round handoff](docs/evaluations/conversation-round13/next-round-handoff.md).

### Candidate — completion round 3 / conversation round 14

- Add explicit summary projection, workspace-bound keyset pagination and on-demand report details; preserve the legacy full-list API.
- Verify 1k/10k/100k synthetic history with same-database ABBA windows: all 1,860 reads correct; at 100k, 100-row payload drops from 363,311 to 56,339 bytes. Retain all windows and actual SQL plans; no production capacity claim.
- Separate a controlled scheduler-blocking experiment from the historical missed slot. Shared-loop/isolated observer miss 6/0 of 144 slots each; Round13 remains failed.
- Separate transport reachability from exact-output scoring before a new bounded three-request model run. One direct RAG report passes report-level AI review, with citation/wording issues; tiny exact output still fails and full Agent/human-independent validation remains open.
- See [Round14](docs/conversation-round14.md) and [raw evidence](docs/evaluations/conversation-round14/README.md).

### Earlier candidate — completion round 1 / conversation round 12

- Add MySQL global active-task admission with READ_COMMITTED lock ordering, capped indexed reads and after-commit metrics. Keep committed idempotent replays available during overload and Redis failure.
- Separate Redis limited/unavailable decisions, reject new provider-capable work when the enabled gate fails, retain bounded offline fallback, and expose explicit retryable 429/503 contracts.
- Verify 133 local Java passes (two conditional restart tests skipped), two frozen 1,440-request open-arrival experiments, and a real isolated Redis stop/recovery. Sampled active tasks reach 32 on the old build and stay at the configured 12 on the candidate; all accepted tasks complete with zero provider use.
- Record fixed-delay synthetic scope, attempt-rate semantics, shared-limit configuration, raw ledgers and the unchanged model-quality failures. No production throughput or model speedup claim.
- See [Round 12](docs/conversation-round12.md), [protocol](docs/evaluations/conversation-round12/protocol.md) and [twelve-round completion plan](docs/finalization-12-rounds.md).

### Earlier candidate — conversation round 11

- Add opt-in RabbitMQ task notifications with transactional MySQL outbox/inbox, fenced publisher leases, correlated confirms and mandatory returns, bounded delivery retry and quorum dead-letter queues. Existing database claim/lease logic remains the only execution authority; polling remains enabled by default.
- Preserve real failures and fixes: Spring AMQP method mismatch, incompatible prototype queue arguments, and two acceptance-observer errors. Use separate publisher connections and reject incompatible topology instead of silently changing it.
- Verify 1,000 synthetic metadata events with unique receipts/claims/completions. Local Java matrix: 113 executed passes plus two separately executed persistent-message restart phases. Real Spring/Python checks prove four MQ-only task wakeups across broker restart and two poll-only fallback completions while the broker is unavailable; later notifications do not re-execute either task.
- Existing cross-service controls pass 13/13 with 32 synthetic BM25 load tasks. Zero model calls; no production-throughput, model-quality, cluster-HA or end-to-end exactly-once claim. Main services remain unchanged.
- See [Round 11](docs/conversation-round11.md), [protocol](docs/evaluations/conversation-round11/protocol.md) and [evidence review](docs/evaluations/conversation-round11/independent-evidence-review.md).

### Earlier candidate — conversation round 10

- Give scoped review one output contract while preserving ordinary prompts, response schemas and all evidence/ID validators. The two diagnostic requests each shrink by 774 bytes; model cost or latency benefit is unproven.
- Reconstruct exposed historical inputs from pinned source and sealed traces; freeze two once-only phases with the same inputs and original review time allowances. Preserve interruption, unknown usage and response-capture safeguards.
- Retain all four real requests: both baseline and repaired phases timed out without responses. No report or semantic result is available; all four provider usage values are unknown. This does not identify the old HTTP 200 failure mechanism or change Round 9 scores.
- Full local Python regression: 1,741 passed. No additional model calls, main-service upgrade or PR merge. MQ reliability remains the next independent backend milestone.
- See [Round 10](docs/conversation-round10.md), [protocol](docs/evaluations/conversation-round10/protocol.md) and [execution record](docs/evaluations/conversation-round10/execution-notes.md).

### Earlier candidate — conversation round 9

- Add an explicit rule-evaluation basis, bounded one-hop arithmetic dependencies, and explicit retain/replace review decisions. Retained rows still undergo the final checks; Java preserves v10 audit metadata.
- Freeze candidate `77b7a55` and retain the only real run: one report over ten planned turns, five submitted tasks, four failures and five dependency-blocked turns. Original rubrics score 0/6 and 0/4.
- Separate two non-timeout review failures from two timeouts. Failed review bodies were not retained, so their exact content is unknown; one timeout has known usage and the other has unknown usage. The only successful review replaced every row, so no real retention benefit is established.
- Keep the post-seal diagnostic patch and its local tests separate from the original model result. Read-only browser checks verify saved history and failure states; they do not grade answer quality. PR remains draft and the main service is unchanged.
- Next, validate a diagnosable protocol on bounded samples and assess new questions; avoid indefinite special cases for the same exposed ten turns. MQ remains a separate backend milestone.
- See [Round 9 report](docs/conversation-round9.md), [execution record](docs/evaluations/conversation-round9/execution-notes.md) and [engineering journal](docs/engineering-journal.md).

### Earlier candidate — conversation round 8

- Add bounded per-row subject/configuration literal binding, retain withheld original explanations, and preserve v9 audit metadata through Java.
- Supply source-scoped previous/current comparison context and minimum-evidence guidance to the same fixed-row review; keep time, call and output budgets unchanged.
- Retain the only frozen run: six reports, one necessary clarification, one review timeout and two blocked turns. Original rubrics score 1/6 and 0/4; comparison improves locally, while old numeric/causal guards still withhold answerable conclusions.
- Preserve 583 sealed raw files, known-token lower bound and unknown review usage, AI grading disagreements, eight offline diagnostic contrasts, and read-only browser history checks. No post-seal source change or extra inference.
- Python 1,423, Java 69 (34 real MySQL/Redis integration), UI 58 and controlled cross-service 64/64 checks pass. Engineering tests are not model quality or production load. PR remains draft; main service is unchanged.
- See [Round 8 report](docs/conversation-round8.md) and [execution record](docs/evaluations/conversation-round8/execution-notes.md).

### Earlier candidate — conversation round 7

- Add bounded explicit comparison slots and post-retrieval clarification policy while retaining ambiguity paths, eight-row capacity and existing call/time limits.
- Bind review output IDs to original statements; controlled response serialization shrinks 18.6%, while request bytes increase. No real latency/token improvement claim.
- Retain the only frozen model run: six factual reports and one necessary clarification over ten planned turns; distinct original rubrics score 2/6 and 0/4. Exact source checks still permit subject/citation mismatch and wrong comparison refusal.
- Diagnose and separately repair deterministic coverage false rejections offline after sealing; retain original answers, scores and AI grading disagreements. PR remains draft; main deployment is unchanged.
- See [Round 7 report](docs/conversation-round7.md), [post-seal repair](docs/post-evaluation-round7.md) and [engineering journal](docs/engineering-journal.md).

### Earlier candidate — conversation round 6

- Align the eight-assessment contract, reserve explicit overall-verdict questions, bind percentage derivations to selected operands, and preserve limited evidence/clarification scope.
- Fix Java's missing coverage-event allowlist entry; retain the first 43/64 controlled failure checks and the corrected 64/64 result.
- Retain the only frozen model run: old six turns delivered 0/6, strict 0/6; original four delivered four responses (three reports, one erroneous clarification), strict 1/4. No overall quality or reliability improvement claim.
- Diagnose a new coverage rejection of an already complete eight-row draft; keep the post-evaluation offline fix separate from the frozen model results. Main deployment and PR merge are deferred.
- See [Round 6 report](docs/conversation-round6.md) and [engineering journal](docs/engineering-journal.md) for evidence, tradeoffs and remaining defects.

### Earlier candidate — conversation round 5

- Simplify workspace controls and report details while keeping consent, cost and terminal states visible; distinguish database outage from Redis degradation and version static assets by content hash.
- Recompute exact passage visibility and read-tool availability after context eviction, replace duplicate passages with evidence-ID references, and retain full traces and final review.
- Preserve frozen results: exposed regression delivered 6/6, strict 2/6; new AI scenarios delivered 2/4, strict 0/4 with one timeout and one blocked turn. No reliability or stable-latency claim.
- See [Round 5 report](docs/conversation-round5.md), including scoring caveats and unresolved quality failures.

### Earlier candidate — conversation round 4

- Persist decision/tool/review stages, actual JSON request bytes, remaining budgets, outcomes and known/unknown provider usage across Python, JVM and task history UI.
- Deduplicate exactly mapped review quotes and repeated fields while retaining evidence, fixed proposition schema and final checks; two offline bodies shrink 14.6% / 15.3%.
- Preserve actual old-regression timeouts, a separately frozen AI synthetic evaluation, and controlled cross-service fault evidence. Payload reduction is not a latency or reliability improvement claim.
- See [Round 4 report](docs/conversation-round4.md) and [backend/LLM roadmap](docs/backend-llm-capability-roadmap.md).

## 0.12.0 — 2026-10-28

Persistent JVM workspace and measured document RAG

- Add Java21/SpringBoot service backed by MySQL with immutable snapshots, idempotency and lease-fenced bounded task execution.
- Add Redis result caching, atomic admission limits, explicit degradation and current-versus-origin usage accounting.
- Add real BM25/dense/hybrid document retrieval, optional cited generation, a persistent workspace UI and runtime metrics.
- Fix an observed InnoDB dispatch/expiry deadlock with indexed expiry candidates, consistent write ordering and bounded database-only retries; retain the failed run and all three successful repeated checks.
- Preserve actual system, failure-injection and frozen-corpus evaluations, including hybrid retrieval misses and small-sample limits.
- See [v0.12 evaluation](docs/v0.12-evaluation.md) for measured results and limitations.

## 0.11.0 — 2026-10-16

- 从现有查询/计划收据先验证重算，再导出自包含HTML和Markdown；保留完整有序结果、比较方向、单位、原文资料、来源、未处理与当前规则检查。
- 页面三个入口支持下载；共享过期状态控制防止迟到响应误用。CLI验证后原子写入，阻止同路径及文件别名覆盖原收据。
- 457项测试通过；166份历史收据的332份报告与22类边界的44项检查，共376/376通过。5份历史合同失败保留原口径，不当作成功。
- 本轮0次模型调用；模型协议与收据格式不变。提供三组可阅读示例、完整矩阵/实现哈希和使用说明，见docs/v0.11-evaluation.md。

## 0.10.0 — 2026-10-16

- 新增有限完整句式的明确命名比较方向与资料操作核对，识别错误类型、遗漏、重复和部分引用；未知或引用表达保守跳过。
- 查询与新任务检查共享最多两次模型请求，合并可操作反馈；预览、执行和逐次轨迹展示方向对照，增加离线示例。
- 380项测试，83份历史计划结果兼容；自然开关对照两组最终均12/12，开启组一次原有分句校验修正。新增检查未在自然样本触发冲突，不宣称自然成功率提升。
- 三个预声明人为错误各经一次真实替换，3/3通过，单列统计；保留完整冻结数据、源码、费用与审核身份。见docs/v0.10-evaluation.md。

## 0.9.0 — 2026-10-16

- 模型提案显式拆分排序与全部/top-k/极值选择，公开API使用v2，旧v1保留供历史对照；执行计划和收据兼容。
- 有限规则反馈使用可修改的提案字段，记录实际修正信息；页面显示每次检查、错误、预算与失败候选状态。
- 311项测试，55份历史计划结果兼容；两份新合成快照8题各协议重复两次，v1首次/最终10/16、v2均16/16。v2未触发修正，不作修正收益归因。
- 保存全部原始回复、冻结源码、费用、失败与助手复核；真实页面完整Pareto生成/执行/下载/重算通过。详见docs/v0.9-evaluation.md。

## 0.8.0 — 2026-10-16

- 自动规划支持请求内导入数据，模型输入与预览绑定同一规范化副本，轨迹保留输入快照。
- 分析计划页增加独立导入、合成数据示例、发送当前快照的明确选择、128 KiB模型数据上限；数据变化丢弃旧异步回复并清除许可和结果。
- 词数阈值在token单位数据上明确未核对；筛选基线错误给出实际实验ID说明。
- 247项测试通过，兼容47份既有计划结果；冻结3份合成快照、6题各两次，8/12通过合同检查。4条Pareto截断错误两轮后停止，全部轨迹、费用与边界见docs/v0.8-evaluation.md。

## 0.7.0 — 2026-10-16

- 增加有限完整中文查询句式核对，独立关联同句多个查询并检查编译后的筛选、排序、截断及并列。
- 明确区分全量结果与并列极值；未知语法/否定/引用等未核对，部分原文的额外查询不会隐藏。
- 网页新增原文要求与实际参数对照、离线规则示例；自动生成遇冲突共享最多一次修正预算。手动执行保留冲突提示，旧receipt兼容。
- 209项工程测试，25份历史receipt重算；6题两组两次为9/12与11/12，未触发新增反馈，不作修正收益归因。事后重放两条错误并各真实修正一次，1/2成功。
- 全部失败、用量及范围见docs/v0.7-evaluation.md，通用语义验证继续未完成。

## 0.6.0 — 2026-10-16

- 新平坦动作提案明确指定排序、top-k和并列选项，编译到既有可编辑计划；支持前置查询ID直接引用。
- 增加分句关联清单及同句处理/未处理冲突检查；最多2次模型请求，允许1次本地校验修正，超时不自动重试。
- 保留原始提案、错误和实际调用数；后续失败不会返回过期计划，旧v0.5六份计划receipt可重算。
- 159项工程测试；4道新题三组各两次，完整任务0/8、7/8、6/8。24次均未触发修正，不能判断增加预算的收益；公开参数错误反例与全部源文件快照。
- 分句关联和执行成功均不证明语义完整，详见docs/v0.6-evaluation.md。

## 0.5.0 — 2026-10-15

- 混合查询同时展示计算比较、缺失字段及原文说明；保留原始模型消息。
- 增加最多6步的可编辑分析计划、实际条件预览、唯一查询引用、明确未处理项与完整receipt离线重算。
- 预览绑定问题、计划和数据；并列/空集不自动选取；数字哈希区分布尔值与零值。
- 129项离线测试；4道混合保留题双方案各两次，工具循环8/8，单次规划器0/8完整任务、6/8可执行partial；完整失败与口径见docs/v0.5-evaluation.md。
- 单次规划需人工核对；内部复核不代表语义、来源真实性或生产就绪。

## 0.4.0 — 2026-10-15

- 查询结果由服务端表格呈现；查询ID绑定数据和说明快照，原始模型消息继续保留。
- 支持请求内数据导入、条件预览与执行、结果receipt导出及离线重算。
- CLI增加inspect/query/verify与显式自定义数据；工作台加入三种模式。
- 108项离线测试；新4题三组两次评测与浏览器流程核验，见docs/v0.4-evaluation.md。
- 自然语言条件理解、复杂混合请求仍可能失败；未声明独立评审或生产就绪。

## 0.3.0 — 2026-10-15

- 结构化答案提交、JSON Schema直接回答与有界修正基线。
- 已读说明生成局限，筛选集合/顺序验证；区分查询阈值与实验指标。
- 复合筛选、排序并列、Pareto、数据覆盖说明与共享只读命令规则。
- 旧失败回归、新12题三组两次评测、公开完整轨迹与助手评分；见 docs/v0.3-evaluation.md。
- 更新工作台、96项离线测试、发布及个人主页。

## 0.2.0 — 2026-10-15

- 增加结构化数值证据校验、比较方向和单位检查、有界修正反馈。
- 定义通用实验格式，保留真实 DPO 导入与来源哈希；补充明确标注的合成导入夹具。
- 建立 32 题开发/保留评测集、固定评分规则、直接回答基线、执行前 manifest 与完整用量记录。
- 补齐真实运行报告、GitHub 展示与面试演示说明。实测结果见 docs/v0.2-evaluation.md。

## 0.1.0 — 2026-10-15

- 初始只读实验分析工具循环、BM25 方法说明检索、引用检查、本地 FastAPI 工作台。
- 打包 11 个真实实验摘要与 6 条方法说明；提供独立脚本回放。
- 后续接入 DeepInfra，保留前三题联调中的失败、修复和原始轨迹。
