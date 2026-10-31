# Round 6：已暴露多轮回归协议

本轮检查已有失败是否修复，复用 Round 5 的 Cedar / Harbor 六轮与 Alder Relay / KiteLoad 四轮，共 **4 个合成场景、16 份资料、10 个计划轮次**。这十题及答案缺陷均已被开发者阅读，全部属于开发回归，不称新题、盲测、独立人工评测或通用准确率评测。原问题、资料、状态更新、gold、旧分数和失败输出保持不变。

## 执行前冻结

1. `scripts/evaluate_conversation_round6.py prepare` 离线校验上轮两份输入清单，把问题转换为统一 API 输入格式；只补场景标题及暴露标签，不改题目、资料正文、source、依赖和会话状态。输出输入清单覆盖协议、执行/评分工具、两组原始输入/gold 和归一化数据。
2. root 提供不可变 candidate Git SHA、源码/配置文件 SHA-256 清单及独立 QA URL。所有源码哈希在每次任务提交前及结束时核对；候选正在修改时不得运行。
3. root 核实同一 DeepInfra 模型 `Qwen/Qwen3-235B-A22B-Instruct-2507`、每任务最多 6 次 decision + 1 次 review、服务边界 75 秒、无 provider retry、无自动重提、原输出 token 上限不增加；把配置来源及上限写入 approval JSON。执行器必须同时收到 `--go GO` 才可运行。不得加载 main 工作区已有资料或会话。
4. 运行期间不看语义、不修题、不改提示词/生产源码；完成采集并生成 raw seal 后再读答案。脚本日志只显示提交、终态和用量，不显示回答。

## 成本、失败与依赖

- 顺序运行 Cedar → Harbor → Alder Relay → KiteLoad；每场景新建工作区和会话。`mode=agent, strategy=bm25, top_k=5`，不产生 embedding 调用。
- 最多 10 个 task POST、70 次模型尝试；同一计划轮只提交一次。按原会话依赖执行，保留状态更新。前置必须 `SUCCEEDED` 且持久化会话中有非空 answer；语义错误但已保存的答案不阻断后续，失败/无答案则阻断依赖轮。所有轮保留在分母中。
- 客户端只读轮询最长 200 秒；这不是扩充模型的 75 秒预算。观察超时最多发送一次取消控制请求，停止后续提交。POST 结果不确定、基础设施错误、源码变动、尝试次数未知/超额、embedding 活动、任务缓存复用均停止新提交，不重试。
- 模型尝试数与 token 可知性分开记录。超时尝试已被计数但 token 或 provider 是否接受未知时，保留 unknown；只要已知尝试数仍在硬上限内，可继续其他无依赖场景。不得把 unknown 写成 0，不用已知 token 下限推算完整费用。
- 保存原始 HTTP 请求/响应字节、轮询、任务终态、会话、资料修订、运行配置、源码清单、request lifecycle 轨迹和用量。模型密钥、环境文件不进入输出。HTTP 仅显式 loopback IP，禁代理和重定向。

## 两套原 gold 并列保留

**Cedar / Harbor（6 轮）**：继续用原 rubric 的 6 个轴 `semantic_completeness / citation_entailment / retrieval_coverage / false_refusal / referent_resolution / source_scope`，取 pass/fail/blocked/not_applicable。逐条核查 mandatory、禁止推断、实际检索与引用蕴含。strict 必须全部适用轴通过；纯澄清的检索/引用允许按原规则不适用。未交付按原 rubric 保留 fail/blocked，不伪造可评分答案。

**Alder Relay / KiteLoad（4 轮）**：继续用原 gold 和 6 维 0/1/2 评分；strict 必须六维全为 2。未交付维度 null，保留在 4 的计划分母。原 `kl-t1` gold 对配置时间、transport-timeout 范围及最小证据的完整性要求比题干更细；这是已知阈值争议，**不修改 gold、不追溯改 Round 5 分数**。

报告各组 strict/计划轮数、strict/已评分轮数，以及交付/失败/阻断和每个维度。可以附 10 轮总交付数，但不要把异构 strict rubric 混成“统一准确率”。Round 5 基线分别是 strict **2/6、0/4**，交付 **6/6、2/4**；任何改善只说明已暴露小样本回归。

## 预注册补充诊断（不覆盖 strict）

每轮另外记录以下标签与答案/原文/工具轨迹位置，解释“为什么失败”，不产生新的二元过关率：

| 项目 | 可用标签 | 审核依据 |
| --- | --- | --- |
| 核心结论 | correct / partly_correct / incorrect / not_evaluable | 回答的实质判断是否正确；材料空缺不能推成历史未发生，当前配置不能推成历史执行。 |
| 完整性缺口 | none / explicit_question_omission / gold_specific_detail / mixed / not_evaluable | 区分明确问到的漏答与 gold 更细的预设要求；每个遗漏列原问题、gold 项和答案位置。 |
| 错误拒答 | none / partial / blanket / not_evaluable | 资料可答的内容是否被不必要降级；不能因本轮未检索到就说工作区不存在。 |
| 证据范围 | preserved / ambiguous / unsupported_inference / not_evaluable | 对象、版本、历史和用户线索是否分开；不能把“当前评估”狭义缺口夸大成全局资料不存在。 |

评分由开发团队 AI 执行，必须声明非盲、非人工。另一 AI 的复核属于内部复核，不改变此身份。必须同时看最终可见回答、所引原文及实际检索轨迹，不以关键字匹配、引文存在或自评 supported 代替语义正确性。用户问题回显不计回答得分。附加诊断有争议时并列写出原判断与复核意见，不偷偷修改已封存评分。

## 过程难题与处理记录

| 问题 | 处理选择 | 验证方式与限制 |
| --- | --- | --- |
| 两组旧 runner 对前置成功、失败和用量的语义不同 | 统一使用持久化会话 answer 作为依赖门；原输入/gold 不变，原 raw 不覆盖 | 准备工具记录所有原始文件哈希，原始分数保留；这是执行一致性改进，不是题目难度变化。 |
| 原 `unknown_usage_tasks` 实际只检查 model_calls，易被误读为 token 全已知 | 新报告分 `unknown_model_call_tasks` 和 `unknown_token_or_provider_tasks` | 保留原始 usage、outer usage_unknown 与 trace；完整费用未知时只报已知 token 下限。 |
| strict 0/4 容易误读为四题事实全错 | 原 strict 与核心正确性/明确漏项/gold 细节要求并列 | 不放宽 gold；预先冻结补充维度，不以事后重定义制造通过率。 |
| 以旧题修复后再测可能过拟合 | 明确十题全已暴露，禁止报告泛化 | 后续独立、题干与 gold 对齐的新题评测需另开版本，不混入本轮。 |

本协议必须先冻结再执行；运行中遇到的新难题另写 `execution-notes.md`，不得修改已冻结协议来适配结果。
