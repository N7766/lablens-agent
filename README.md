# LabLens

[![Tests](https://github.com/N7766/lablens-agent/actions/workflows/ci.yml/badge.svg)](https://github.com/N7766/lablens-agent/actions/workflows/ci.yml)

面向研发与模型实验排查的 **RAG / Agent 工作区**。从分散的文档、配置和日志摘要中查找证据，排查“结果为何不同、依据是什么、还缺哪些信息”。

[启动说明](docs/v0.12-workspace.md) · [真实排查案例](docs/cases/dpo-preference-generation.md) · [本轮证据](docs/conversation-round18.md) · [文档索引](docs/README.md)

![精简后的工作区，展示已保存的历史 Agent 报告](docs/assets/v013-round5-workspace.png)

## 核心能力

- **带来源的检索**：BM25、向量与 RRF 混合检索，保留原文片段、偏移及哈希。
- **多轮工具调用**：模型选择检索、读取来源、澄清或提交报告；记录耗时、调用预算与失败轨迹。
- **显式项目记忆**：手动保存和选用，绑定原文版本，处理冲突、过期与删除；备注仍需重新核验。
- **可恢复的任务**：MySQL保存完整工具边界，显式恢复沿用原快照和累计预算；未知请求阻止重放。
- **有权限的协作**：持久登录会话、OWNER/EDITOR/VIEWER、旧资料显式接管和独立服务身份。

## 技术结构

| 组件 | 职责 |
| --- | --- |
| Java 21 / Spring Boot | 登录与工作区权限、任务调度、租约与取消 |
| MySQL / Redis | 资料版本、快照与历史 / 缓存与限流 |
| RabbitMQ（可选） | outbox 投递、inbox 去重与任务唤醒 |
| Python / FastAPI | RAG、Agent 工具循环、来源及数值检查 |
| OpenAI-compatible HTTP | 模型接入、发送许可与调用预算 |

## 运行

完整工作区按 [Compose 或手动启动说明](docs/v0.12-workspace.md)配置，入口为 <http://127.0.0.1:8081>。

只体验确定性工具，可按[本地安装步骤](CONTRIBUTING.md#本地验证)运行[离线示例](docs/demo.md)，无需 API key；真实模型接入见[配置与预算](services/workspace/README.md#configuration)。

## 当前进展

**v0.13 候选，尚未正式发布；收尾进度7/12。** 本轮交付[有界历史与中断恢复](docs/workspace-recovery.md)：37项真实跨服务检查通过，包含worker进程被杀后恢复、检索不重放和未知结果阻断。已有MySQL任务、Redis限流、RabbitMQ outbox/inbox、持久检索和显式项目记忆；十万条合成历史列表响应体减少84.49%的对照见[第十四轮](docs/conversation-round14.md)。

本轮使用本机固定响应，外部模型调用0，只证明工程协议。恢复报告保留为独立任务分支，旧真实Agent失败和语义验收缺口仍保留；GPU部署、模型压测与最终验收按[十二轮计划](docs/finalization-12-rounds.md)推进。当前验证不代表生产容量或商业流量。

## 参与与跟进

[Issues](https://github.com/N7766/lablens-agent/issues) · [Pull requests](https://github.com/N7766/lablens-agent/pulls) · [贡献指南](CONTRIBUTING.md) · [更新记录](CHANGELOG.md)
