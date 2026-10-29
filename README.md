# LabLens

[![Tests](https://github.com/N7766/lablens-agent/actions/workflows/ci.yml/badge.svg)](https://github.com/N7766/lablens-agent/actions/workflows/ci.yml)

面向研发与模型实验排查的 RAG / Agent 工作区：结合文档、配置和日志摘要检索证据，保留引用、资料版本与执行轨迹。

[快速启动](#快速启动) · [文档索引](docs/README.md) · [真实排查案例](docs/cases/dpo-preference-generation.md) · [待办与反馈](https://github.com/N7766/lablens-agent/issues)

## 解决什么问题

实验记录分散、配置版本不同、指标口径不一致时，单看一份报告容易得出错误结论。LabLens 将检索、确定性计算和来源核验接入有预算限制的工具循环，帮助回答“结果差在哪里、证据是什么、还有哪些信息不足”。

- **检索与引用**：BM25、向量及 RRF 混合检索；记录原文片段、偏移和哈希。
- **多轮排查**：模型选择检索、读取来源、澄清或提交报告；旧回答只作上下文，事实需要重新检索。
- **持久任务**：MySQL 保存资料版本、不可变任务快照与历史；支持幂等提交、取消和失败记录。
- **实验对比**：保留独立的只读实验工具，计算指标差值、筛选与 Pareto 集合，导出可重算报告。

![持久工作区界面，v0.12 截图](docs/assets/v012-workspace.png)

## 技术结构

| 层 | 实现 |
| --- | --- |
| 工作区与任务 | Java 21 / Spring Boot，MySQL，Redis 缓存与限流 |
| 检索与 Agent | Python / FastAPI，结构化动作，来源及数值检查 |
| 模型接入 | OpenAI-compatible HTTP 接口，显式发送许可与调用预算 |
| 验证 | Python、JVM、UI 回归；检索、故障和回答质量分别评测 |

## 快速启动

### 离线 Python 演示

需要 Python 3.11+。在仓库根目录执行，无需 API key：

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
lablens demo --output reports/offline-demo.json
uvicorn lablens.api:app --host 127.0.0.1 --port 8765
```

打开 <http://127.0.0.1:8765> 使用离线条件查询。CLI demo 是固定脚本演示，不调用真实模型，也不是回答质量评测。

### 完整持久工作区

需要 Python worker、Java 21、MySQL 和 Redis。按 [Compose 启动说明](docs/v0.12-workspace.md)或 [JVM 手动启动说明](services/workspace/README.md#run)配置，浏览器入口为 <http://127.0.0.1:8081>。这与上面的 Python 演示是两个入口。

真实模型为可选配置：参考 [.env.example](.env.example)，在本地设置 `LLM_BASE_URL`、`LLM_MODEL` 和 `LLM_API_KEY`。Python 可读取本地 `.env`，Java 需要导出环境变量。模型与嵌入调用可能产生费用；发送资料前必须确认当前版本。详见[配置与预算](services/workspace/README.md#configuration)。

## 当前状态与限制

当前为 **v0.13 候选迭代，尚未正式发布**。工具链、持久任务和多轮会话已实现，整体回答可靠性仍在验证：

- 第四轮新冻结 AI 合成题交付 6/6、严格通过 5/6；另测的已暴露旧题仍为 2 次超时、4 轮依赖阻塞。已增加分阶段耗时与预算诊断，可靠性验收仍未完成。[结果与原始证据](docs/conversation-round4.md)
- 引文存在、工程测试通过、同模型复核，都不等于结论正确或独立评审通过。已暴露问题的回归成绩不替代新题评估。
- 默认面向本机单用户演示，尚无公网鉴权、自动长期记忆或步骤级断点恢复。不要直接暴露到公网。
- 内置 DPO 摘要来自检查点选择用验证集；其中长度单位为 `whitespace_words`，不是 tokenizer token。原模型实验结果不作为 Agent 的准确率。

## 文档与开发

[文档索引](docs/README.md)集中保存启动、评测、失败记录和历史版本；[CHANGELOG](CHANGELOG.md)记录代码迭代。公开仓库不包含 API key、模型权重或原始训练样本。

运行离线测试：`pytest -q`。反馈问题时请提供脱敏复现、预期结果和实际结果。后续迭代按真实问题推进，见 [Issues](https://github.com/N7766/lablens-agent/issues) 和[维护约定](docs/github-maintenance.md)。MQ、并发背压、数据库规模优化和 GPU 部署按[后续路线](docs/backend-llm-capability-roadmap.md)逐阶段验收。
