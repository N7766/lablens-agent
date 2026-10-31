# Round 7：已暴露多轮回归协议

本轮检查 Round 6 的超时、漏答、错误澄清及其 post-evaluation 修复，复用 Round 5 的 Cedar / Harbor 六轮与 Alder Relay / KiteLoad 四轮，共 **4 个合成场景、16 份资料、10 个计划轮次**。这十题及答案缺陷均已被开发者阅读，全部属于开发回归，不称新题、盲测、独立人工评测或通用准确率评测。原问题、资料、状态更新、gold、Round 5 / 6 分数和首次失败输出保持不变。

## 执行前冻结

1. `scripts/evaluate_conversation_round7.py prepare` 离线校验两份原始输入清单和 Round 6 raw seal，把问题转换为统一 API 输入格式；只补场景标题及暴露标签，不改题目、资料正文、source、依赖和会话状态。输出输入清单覆盖协议、执行/评分工具、两组原始输入/gold、归一化数据及 Round 6 原报告/分数/seal。
2. root 提供不可变 candidate Git SHA、源码/配置文件 SHA-256 清单及独立 QA URL。所有源码哈希在每次任务提交前及结束时核对；候选正在修改时不得运行。
3. root 核实同一 DeepInfra 模型 `Qwen/Qwen3-235B-A22B-Instruct-2507`、每任务最多 6 次 decision + 1 次 review、服务边界 75 秒、无 provider retry、无自动重提、原输出 token 上限不增加；把配置来源及上限写入 approval JSON。执行器必须同时收到 `--go GO` 才可运行。不得加载 main 工作区已有资料或会话。
4. 运行期间不看语义、不修题、不改提示词/生产源码；完成采集并生成 raw seal 后再读答案。脚本日志只显示提交、终态和用量，不显示回答。

## 成本、失败与依赖

- 顺序运行 Cedar → Harbor → Alder Relay → KiteLoad；每场景新建工作区和会话。`mode=agent, strategy=bm25, top_k=5`，不产生 embedding 调用。
- 最多 10 个 task POST、70 次模型尝试；同一计划轮只提交一次。`run` 函数与 CLI 都检查 GO，执行前写独占 once-only claim，另换 run label 也不能复用本轮授权；中断不自动清除 claim 或重跑。按原会话依赖执行，保留状态更新。前置必须 `SUCCEEDED` 且持久化会话中有非空 answer；语义错误但已保存的答案不阻断后续，失败/无答案则阻断依赖轮。所有轮保留在分母中。
- 客户端只读轮询最长 200 秒；这不是扩充模型的 75 秒预算。观察超时最多发送一次取消控制请求，停止后续提交。POST 结果不确定、基础设施错误、源码变动、尝试次数未知/超额、embedding 活动、任务缓存复用均停止新提交，不重试。
- 模型尝试数与 token 可知性分开记录。超时尝试已被计数但 token 或 provider 是否接受未知时，保留 unknown；只要已知尝试数仍在硬上限内，可继续其他无依赖场景。不得把 unknown 写成 0，不用已知 token 下限推算完整费用。
- 完整已知 request lifecycle 的 prompt/completion/total token 分项合计须与 aggregate 一致；不一致时 token 标为 unknown，原值保留，不影响已知的尝试计数。
- 保存原始 HTTP 请求/响应字节、轮询、任务终态、会话、资料修订、运行配置、源码清单、request lifecycle 轨迹和用量。模型密钥、环境文件不进入输出。HTTP 仅显式 loopback IP，禁代理和重定向。

## 两套原 gold 并列保留

**Cedar / Harbor（6 轮）**：继续用原 rubric 的 6 个轴 `semantic_completeness / citation_entailment / retrieval_coverage / false_refusal / referent_resolution / source_scope`，取 pass/fail/blocked/not_applicable。逐条核查 mandatory、禁止推断、实际检索与引用蕴含。strict 必须全部适用轴通过；纯澄清的检索/引用允许按原规则不适用。未交付按原 rubric 保留 fail/blocked，不伪造可评分答案。

**Alder Relay / KiteLoad（4 轮）**：继续用原 gold 和 6 维 0/1/2 评分；strict 必须六维全为 2。未交付维度 null，保留在 4 的计划分母。原 `kl-t1` gold 对配置时间、transport-timeout 范围及最小证据的完整性要求比题干更细；这是已知阈值争议，**不修改 gold、不追溯改 Round 5 分数**。

报告各组 strict/计划轮数、strict/已评分轮数，以及交付/失败/阻断和每个维度。可以附 10 轮总交付数，但不要把异构 strict rubric 混成“统一准确率”。Round 5 基线分别是 strict **2/6、0/4**，交付 **6/6、2/4**；Round 6 为 strict **0/6、1/4**，交付 **0/6、4/4**，其中 3 份事实报告、1 次澄清。任何改善只说明已暴露小样本回归。

逐轮人工阅读标注交付类型 `fact_report / clarification / mixed / no_response`，必须和持久化状态一致，但类型不由任务 `SUCCEEDED`、引用数量或关键词推断。能给有界不足结论的事实回答仍属报告；只请用户补资料属澄清；已给实质回答并同时提出澄清属 mixed。分类单列计数，不把纯澄清叫作事实报告，不用类型代替质量评分。

## 预注册补充诊断（不覆盖 strict）

每轮另外记录以下标签与答案/原文/工具轨迹位置，解释“为什么失败”，不产生新的二元过关率：

| 项目 | 可用标签 | 审核依据 |
| --- | --- | --- |
| 核心结论 | correct / partly_correct / incorrect / not_evaluable | 回答的实质判断是否正确；材料空缺不能推成历史未发生，当前配置不能推成历史执行。 |
| 完整性缺口 | none / explicit_question_omission / gold_specific_detail / mixed / not_evaluable | 区分明确问到的漏答与 gold 更细的预设要求；每个遗漏列原问题、gold 项和答案位置。 |
| 错误拒答 | none / partial / blanket / not_evaluable | 资料可答的内容是否被不必要降级；不能因本轮未检索到就说工作区不存在。 |
| 证据范围 | preserved / ambiguous / unsupported_inference / not_evaluable | 对象、版本、历史和用户线索是否分开；不能把“当前评估”狭义缺口夸大成全局资料不存在。 |
| 缺失资料处理 | bounded_answer / overclaimed / unnecessary_clarification / not_applicable / not_evaluable | 缺资料时明确未知范围和最低证据；不能把资料缺失推成历史未发生，也不能用追问替代现有资料已经能给的有界答案。 |
| 矛盾证据处理 | reconciled / overclaimed / omitted / not_applicable / not_evaluable | 是否按版本、资料性质及作用范围解释冲突，避免选择性引用或无依据决定谁一定正确。 |
| 错误澄清 | none / partial / blanket / not_evaluable | 真正对象歧义应澄清；可回答对象已经明确时，追问不能代替全部或部分明确子问。与错误拒答分开记录。 |

评分由开发团队 AI 执行，必须声明非盲、非人工。另一 AI 的复核属于内部复核，不改变此身份。必须同时看最终可见回答、所引原文及实际检索轨迹，不以关键字匹配、引文存在或自评 supported 代替语义正确性。用户问题回显不计回答得分。附加诊断有争议时并列写出原判断与复核意见，不偷偷修改已封存评分。

评分输入必须完整并且自洽：所有已交付维度都必须是显式 0/1/2，不能留 null；strict 满分不可同时声明核心错误、明确漏答、错误拒答或越界推断。需要事实报告的题目不能以纯澄清声称全部结论满足；旧六轮的真正对象歧义题仍允许正确澄清。发现矛盾时评分器拒收，要求人读原始证据后修订标注，不自动改 gold 或给出新的语义判断。

正式评分要求封存的 approval、source-before、source-after、inputs-after 身份齐备一致。采集发生源码/输入身份失败时，保留 raw 和失败报告，但阻断正式评分。普通基础设施中断且身份有效时，允许保留部分结果和全部计划分母，评分必须带 fatal 原因及 `valid_partial_collection`；不能把部分采集称为完整运行。

## 过程难题与处理记录

| 问题 | 处理选择 | 验证方式与限制 |
| --- | --- | --- |
| 两组旧 runner 对前置成功、失败和用量的语义不同 | 统一使用持久化会话 answer 作为依赖门；原输入/gold 不变，原 raw 不覆盖 | 准备工具记录所有原始文件哈希，原始分数保留；这是执行一致性改进，不是题目难度变化。 |
| 原 `unknown_usage_tasks` 实际只检查 model_calls，易被误读为 token 全已知 | 新报告分 `unknown_model_call_tasks` 和 `unknown_token_or_provider_tasks` | 保留原始 usage、outer usage_unknown 与 trace；完整费用未知时只报已知 token 下限。 |
| strict 0/4 容易误读为四题事实全错 | 原 strict 与核心正确性/明确漏项/gold 细节要求并列 | 不放宽 gold；预先冻结补充维度，不以事后重定义制造通过率。 |
| 以旧题修复后再测可能过拟合 | 明确十题全已暴露，禁止报告泛化 | 后续独立、题干与 gold 对齐的新题评测需另开版本，不混入本轮。 |
| 上轮 4 个成功任务实际包含 1 次错误澄清 | 交付类型与 strict、错误澄清标签分别标注 | 不把持久化响应数包装成事实报告数。 |
| 只靠 run label 不能体现一次采集授权 | 首次运行写独占 claim，拒绝已有采集/改 label 重跑 | 离线测试验证第二次不能再提交；不修改 Round 6 冻结 runner。 |

本协议必须先冻结再执行；运行中遇到的新难题另写 `execution-notes.md`，不得修改已冻结协议来适配结果。
