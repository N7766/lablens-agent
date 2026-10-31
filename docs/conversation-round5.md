# LabLens 会话第五轮：界面精简与可见证据去重

日期：2026-10-31。模型评测候选 `6f79300c7e06f2ae57ba92915c911908cc024009` / `workspace-agent-v6`；仍为 v0.13 候选。前端与执行路径改进已交付，答案质量仍未验收通过。

## 场景与改动

首页保留项目/会话选择、提问和报告；创建上传、会话管理、查找选项、历史和运行轨迹按需展开。模型发送许可、当前模式、费用和失败状态持续可见，问题/资料/会话变化会撤销许可。原文摘录默认展示来源；生成报告先展示正文，再展开逐条判定与来源。数据库不可用明确显示红色，Redis 单独失效显示缓存降级。

执行链路针对第四轮 Cedar 已完整获取原文仍重复读取的问题：每次打包决策上下文时，只将当前快照、资料哈希、来源元数据和完整 passage 都一致的资料视为可见；重复 passage 用 evidence ID 指向同一请求里此前保留的完整原文。裁剪整个工具交换组后重新计算可见性、引用和 read 工具 schema；被裁剪或部分可见的资料仍可读取。原始工具轨迹不变，最终报告复核仍使用全部已检索原文。

复核发现最初的 tool_call_id 引用会被 structured 请求转换丢掉，已改为最终请求里可解析的 evidence ID，并补引用闭合回归。冗余 read 防线记录 already_visible；混合 read 仅恢复缺失资料。该逻辑不代表当前资料已语义核验或答案已完整。

75秒仍是调用边界预算，最多6次决策加1次复核；HTTPX超时不是严格的上游计算终止，没有自动重试或扩大预算。本轮不增加 MQ、长期记忆或私有模型部署。

## 工程验证与正文对照

[工程回归](evaluations/conversation-round5/regressions.json)：Python **763**、JVM **66**（其中真实 MySQL/Redis **34**）、UI **58**项通过。新增20项证据可见性测试涵盖部分原文、版本/哈希变化、ID碰撞模拟、上下文裁剪、工具batch、实际HTTP schema和混合read。保留原有取消与未知用量回归。[可控跨服务运行](evaluations/conversation-round5/runtime-fault/README.md)64项通过，外部模型调用0；脚本provider的响应和usage是合成数据。

[GitHub CI](evaluations/conversation-round5/implementation-ci.json)的源码 push/PR 两次运行各4个job通过，包含Python 3.11/3.13、Java21/MySQL/Redis及UI。[源码复核](evaluations/conversation-round5/source-review.md)是范围内AI只读审查，不是安全审计或质量证明。

[离线请求对照](evaluations/conversation-round5/decision-payload-comparison.json)逐一复原第四轮5个实际决策的记录字节：Cedar旧第三步24,317→22,660 bytes（减少6.814%），Cedar第二步20,187→20,149，Harbor第二步19,227→19,153；首步因为增加可见性说明各增加362 bytes。旧第三步是对历史冗余read轨迹的反事实打包，新工具schema不再提供该read。不能把这些数字当作实际新轨迹、token/费用或时延降幅。

## 冻结真实模型结果

模型：DeepInfra `Qwen/Qwen3-235B-A22B-Instruct-2507`，BM25、不调用embedding。旧题与新题串行运行；源码、输入、原始输出和评分分别封存。完整分母保留失败与依赖阻塞。

| 集合 | 任务交付 | 严格通过 | 调用 / 已知tokens | 范围 |
|---|---:|---:|---:|---|
| 已暴露Cedar/Harbor，6轮 | 6/6 | 2/6 | 16 / 74,143 | AI开发侧回归，另一个AI复核；非盲、非人工独立 |
| 新冻结2场景，4轮 | 2/4；1失败、1阻塞 | 0/4 | 9 / ≥26,462；1次复核用量未知 | 未参与实现的AI出题并评分；非人工独立 |

[旧题完整评分](evaluations/conversation-round5/development/REPORT.md)：四个必需检索轮覆盖4/4，五次事实报告都保留最终来源核验和单独模型复核，无read动作、无超时。第四轮是两次超时加四轮阻塞；本轮Cedar/Harbor首轮worker耗时仍达64.430/72.044秒。不同时间的单次运行未控制供应商负载、搜索措辞和模型输出，不能证明稳定提速或把交付提升全部归因于去重。

严格仅Cedar t2、Harbor t3通过。原始失败确认四类缺口：有来源的链式计算被窄规则误拦截；草稿遗漏指标/总体结论而固定命题复核不能补全；同轮重新引入多个对象却未先澄清；复核把“资料没有最大值记录”扩大为“历史没有测量”。引用真实存在不能证明这些结论正确。评分后未修代码或重跑。

[新题完整记录](evaluations/conversation-round5/new-scenarios/README.md)使用2场景、4轮、8份英文合成资料。ar-t1在复核阶段超时，依赖轮未提交；两个kl回答的历史重试/因果不确定性主判断基本正确，但分别遗漏配置适用条件、时间范围或充分证据要求，以及把上一轮的配置问题带回并错误声称无法确认。按预先冻结的六维全满分标准为0/4，不等于所有已回答主结论都错误。作者未参与实现或阅读旧题，但同一AI出题和评分，不能写成人工独立评测。

[第二次评分复核](evaluations/conversation-round5/new-scenarios-secondary-review.md)保留了评分边界：kl-t1核心问题已经正确回答，冻结gold要求的明确时间先后、历史快照区分和最低权威证据比原问题表述更具体，因此严格失败对完整性门槛敏感。kl-t2的“当前评估”也有本轮检索范围的歧义；准确缺陷是把有资料可答的多余配置子项降为不足，不能夸大成声称整个工作区没有配置。保留原始严格成绩和复核异议，后续出题前先对齐显式问题与gold，不在出分后改线。

新题实际9次调用尝试（6决策、3复核），次数完整已知；8次已知响应的token合计26,462只是已报告下界，超时复核是否被上游接受及用量未知。新题ar-t1的worker为75.822秒，说明相同预算下仍可能超时。不能只引用旧题6/6交付而说可靠性问题已解决。

## 展示与升级

[浏览器记录](evaluations/conversation-round5/browser/summary.json)覆盖真实创建/导入、离线摘录、会话、折叠后的许可失效、刷新和历史；1440×1000桌面与390×844手机无水平溢出。数据库故障后的红色提示通过响应拦截验证，未为截图停服务。最初实际发现本机MySQL/Redis已停止，复用既有数据恢复；没有重新初始化数据，也没有确认进程停止的根因。

主服务旧浏览器标签页还暴露“新HTML+缓存旧脚本”的组合：资源Timing显示旧CSS/JS transferSize=0，`connectionStatus`仍不存在；无缓存HTTP读取的资源与新源码一致。最后只给HTML里的CSS/JS URL加实际内容SHA-256前12位，并增加一致性测试。模型评测全部封存后才做这项修复：[部署清单](evaluations/conversation-round5/deployment-freeze.json)单列`ad141154092d77c67121cb7a44470054f34befd6`，相对模型候选59个源文件仅index.html变化，Agent、JVM、配置及CSS/JS字节均未变。58项UI测试通过；没有把修复后的HTML冒充模型评测使用的版本。[缓存修复升级](evaluations/conversation-round5/main-upgrade-cache-fix.json)再次保留全部主库历史摘要。

[首次主服务升级](evaluations/conversation-round5/main-upgrade.json)保持13个工作区、42份资料、58个资料版本、182条任务和0条主库会话的全字段摘要一致，4条历史API摘要一致，Flyway仍V5。旧JVM停止到新JVM健康约2.76秒，不称无停机。私密备份已创建但未做恢复演练。QA评测数据独立保存，不混入主库。[结束后仅停止本轮闲置QA服务](evaluations/conversation-round5/qa-cleanup.json)，5表摘要保持一致；主服务、MySQL与Redis继续运行。

## 后续顺序与简历

下一轮先收敛上述四类质量失败（[总体结论 #41](https://github.com/N7766/lablens-agent/issues/41)、[证据范围 #46](https://github.com/N7766/lablens-agent/issues/46)、[链式计算 #47](https://github.com/N7766/lablens-agent/issues/47)，澄清继续跟踪 [#10](https://github.com/N7766/lablens-agent/issues/10)）：冻结最小反例，修计算/覆盖/范围与澄清，再跑同集回归。之后推进[工程路线阶段2](backend-llm-capability-roadmap.md#阶段-2rabbitmq事务-outbox-与消费幂等)：真实RabbitMQ、MySQL outbox/inbox、重复投递/confirm丢失/消费后ack丢失；一次消息重投不得自动重发结果未知的LLM调用。再做准入背压与容量，GPU租赁前另定费用和时长上限。

可用简历表述：“为Java/Python资料诊断Agent实现随上下文裁剪重算的证据可见性与工具约束，避免完整原文重复读取，保留快照来源、完整轨迹与最终复核；通过763项Python、66项JVM及58项UI测试和64项跨服务故障检查。建立冻结语义评测并将交付率、检索覆盖、结论质量和未知用量分别记录。”

面试需同时说明旧题严格2/6及新题实际结果。不能写成已解决幻觉、生产高并发、大规模真实用户、已有商业线上事故经验或已完成MQ/自动长期记忆/私有化部署。
