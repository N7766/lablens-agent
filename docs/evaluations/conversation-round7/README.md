# Round 7：已暴露回归

本目录保存 Round 6 实际失败修复后的下一次有界回归。执行、封存与内部 AI 评阅已完成：6报告+1必要澄清，两组严格2/6、0/4，质量门未过。先读[结果报告](REPORT.md)、[排查过程](execution-notes.md)和[封存后离线修复](../../post-evaluation-round7.md)。下列命令保留为实际执行流程，不授权再跑；一次性claim已占用。

- [协议](protocol.md)：同 10 轮、原 gold、75 秒、最多 70 模型尝试，无重提/重试。
- [工具准备与难题](harness-development.md)：复用已有保护，并强化一次执行与交付分类。
- 原始题目/gold 在 `conversation-round5`，Round 6 首次失败、评分和 raw seal 继续保留在 `conversation-round6`。

## 执行顺序

1. 源码和离线回归完成后，先运行 `.venv/bin/python scripts/evaluate_conversation_round7.py prepare`，冻结输入、协议和工具。
2. root 建立完整候选 Git SHA、源码/配置哈希清单、隔离 QA URL、预算/模型/输出限制的 approval JSON。运行期间不得改源码、工具、题目和 gold。
3. root 明确 GO 后才执行一次：

   ```sh
   .venv/bin/python scripts/evaluate_conversation_round7.py run \
     --go GO --base-url http://127.0.0.1:8083 \
     --approval-file .local/round7/evaluation-approval.json \
     --run-label round7-once
   ```

   URL 以 root 实际批准的隔离服务为准；不要对主工作区运行。执行器全程不输出回答，生成 raw seal 后才开始语义阅读。
4. 生成标注模板、逐条查看回答/原文/轨迹并写出证据位置，再由评分工具聚合：

   ```sh
   .venv/bin/python scripts/score_conversation_round7.py template \
     --run docs/evaluations/conversation-round7/runs/round7-once \
     --output docs/evaluations/conversation-round7/assessment/annotations.json
   .venv/bin/python scripts/score_conversation_round7.py score \
     --run docs/evaluations/conversation-round7/runs/round7-once \
     --annotations docs/evaluations/conversation-round7/assessment/annotations.json \
     --output docs/evaluations/conversation-round7/assessment/scores.json
   ```

标注是开发团队 AI 的非盲逐条阅读，不是人类独立评测。内部另一 AI 复核不改变这一属性。缺失记录及 review 超时的 token/provider 接受保留 unknown；尝试次数已知不等于费用全知。
