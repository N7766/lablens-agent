# Changelog

## Unreleased — v0.13 candidate, conversation round 5

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
