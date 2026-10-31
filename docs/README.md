# 文档索引

首页只介绍用途、启动和当前边界；本页保留实现说明与历史证据。不同版本的测量条件不同，不能直接把测试数量、检索召回率和回答准确率合并比较。

## 启动与接口

- [完整工作区与 Compose](v0.12-workspace.md)：Python、Java、MySQL、Redis。
- [JVM 服务与 API](../services/workspace/README.md)：版本许可、任务、会话、取消、预算与运行配置。
- [离线演示](demo.md)、[实验数据导入](import-format.md)、[查询工作台](v0.4-workbench.md)。
- [计划与报告导出](v0.11-workbench.md)、[离线报告示例](reports/v0.11/README.md)。

## 当前实现与待验证项

- [近期 Issue 与维护流程](github-maintenance.md)：已完成诊断、待修超时、回答完整性、评测和 MQ 的跟踪入口。

- [会话第七轮](conversation-round7.md)：比较覆盖、短 ID 复核、一次真实回归与未解决的语义错配；[封存后修复](post-evaluation-round7.md)仅有离线验证。
- [会话第六轮](conversation-round6.md)：答案质量改动、冻结失败、后置离线修复与暂缓升级决定。
- [工程迭代记录](engineering-journal.md)：每轮优化、实际难题、取舍与结果索引。
- [会话第五轮](conversation-round5.md)：精简界面、上下文可见证据去重、完整失败与评分边界。
- [会话第四轮](conversation-round4.md)：分阶段超时诊断、复核正文去重及真实评测。
- [后端与 LLM 工程路线](backend-llm-capability-roadmap.md)：MQ、并发背压、规模化查询、故障、记忆和 GPU 部署的分阶段验收。
- [会话第三轮](conversation-round3.md)：逐命题复核、数值引文检查及该轮超时失败。
- [会话第二轮](conversation-round2.md)：结构化动作、持久取消及真实模型结果。
- [会话第一轮](conversation-round1.md)：多轮上下文、用户固定状态、版本冲突与澄清。
- [会话与恢复计划](conversation-and-recovery-plan.md)：能力范围及未完成事项。
- [v0.13 验收](v0.13-acceptance.md)、[冻结候选回答质量](../evaluation/independent_v013/RESULTS.md)。
- [真实 DPO 排查案例](cases/dpo-preference-generation.md)：配置、指标、日志和不能确定的解释。
- [Agent 故障验证](evaluations/v0.13/agent-fault/README.md)、[第四轮运行证据](evaluations/conversation-round4/README.md)。

## 评测方法

- [总体评测说明](evaluation.md)：工程检查、模型评测与费用边界。
- [数值校验范围](numeric-validation.md)：确定性检查能证明和不能证明的内容。
- [v0.12 检索与并发测量](v0.12-evaluation.md)：小样本检索、数据库与任务故障实测。
- [v0.12 收尾记录](v0.12-closure.md)：失败用量、重试入口与历史结果。

检索命中不代表证据充分，引用准确不代表语义成立，任务交付也不等于答案正确。超时、依赖阻塞和未提交的题目需分别计数；未知用量保留为未知。合成题、已暴露回归和人工独立评测应明确区分。

## 历史版本

| 范围 | 报告 |
| --- | --- |
| v0.11 / v0.10 | [报告复核](v0.11-evaluation.md) / [任务规则与修正](v0.10-evaluation.md) |
| v0.9 / v0.8 | [选择范围](v0.9-evaluation.md) / [跨快照验证](v0.8-evaluation.md) |
| v0.7 / v0.6 | [请求核对](v0.7-evaluation.md) / [规划预算](v0.6-evaluation.md) |
| v0.5 / v0.4 | [混合任务](v0.5-evaluation.md) / [查询表格](v0.4-evaluation.md) |
| v0.3 / v0.2 | [重复对照](v0.3-evaluation.md) / [单次对照](v0.2-evaluation.md) |

最早的 [DeepInfra 三题联调](deepinfra-validation.md)是历史接入验证，不代表当前模型质量。[CHANGELOG](../CHANGELOG.md)与[GitHub 维护约定](github-maintenance.md)记录迭代方式；原始失败报告不因后续修复而重写。
