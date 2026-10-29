# 第四轮证据索引

日期：2026-10-29。主报告见 [会话第四轮](../../conversation-round4.md)，后续实施见 [后端与 LLM 路线](../../backend-llm-capability-roadmap.md)。本目录的工程、故障、模型、语义与部署证据各自使用独立分母。

| 证据 | 范围 |
|---|---|
| [candidate-freeze.json](candidate-freeze.json) | 实际候选提交及 59 个实现/配置文件 SHA；推理前冻结 |
| [regressions.json](regressions.json) / [CI](implementation-ci.json) | Python 743、JVM 65（含真实集成 34）、UI 42；GitHub 四个 job 通过 |
| [只读代码审查](source-review.md) | 有限实现审查，无新题访问、无模型调用 |
| [payload-comparison.json](payload-comparison.json) | 两份固定旧草稿的复核 JSON 正文减少 14.621% / 15.327%；零推理，不是速度提升 |
| [runtime-fault](runtime-fault/README.md) | 真实跨服务、脚本 provider，2 场景、64 检查；零外部模型调用 |
| [已暴露回归](development/REPORT.md) | 2 实际失败 + 4 依赖阻塞 = 0/6 交付；检索覆盖 2/2，最终答案 0 个，完整费用未知 |
| [新冻结题](new-scenarios/README.md) | AI 编写/评分；执行交付、结论完整性、引文和错误拒答分别核对，非人工独立评测 |
| [浏览器](browser/summary.json) | 已有真实失败任务的计时、未知用量、刷新和桌面/手机布局，无新模型调用 |
| [主服务升级](main-upgrade.json) | 数据摘要及历史保存；保留部署辅助脚本失败与约 60 秒升级间隔，备份未恢复演练 |
| [QA 清理](qa-cleanup.json) | 专属评测服务停止，数据库保留，主服务继续运行 |
| [完整性核对](artifact-audit.json) | 冻结输入、原始输出、评分文件、源文件及已配置敏感值检查 |

`development/` 和 `new-scenarios/` 保留冻结协议、题目、资料、gold、runner、HTTP/任务原始记录、评分和 SHA。后续只能新增修正说明或新的运行包，不能静默改写本次失败、评分或输入。公开后的新题下一轮即视为已暴露。

任何已知 token 数只统计返回 usage 的请求；未知请求不能填零。工程检查通过、原文存在、任务 SUCCEEDED 均不自动等于语义正确。小样本与脚本 provider 不能证明生产容量或模型吞吐。
