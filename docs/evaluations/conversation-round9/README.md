# Round 9：验收规则、算术依赖与稀疏复核

当前是**评测工具和协议准备阶段**，尚未执行 prepare、生成 input-freeze 或调用模型。沿用已经暴露的 10 轮、16 份合成资料与两套原 gold；这是开发回归，不是新题、盲测或独立人工评测。

- [协议](protocol.md)：75 秒、6 decision + 1 review、输出 1800/2600，最多 10 个任务和 70 次尝试；阶段时间分配不变，无重试/重提/embedding。
- [工具准备与难题](harness-development.md)：保留 83 项历史控制，新增 11 项历史链校验。
- [Round 8 原结果](../conversation-round8/REPORT.md)和[失败复核](../conversation-round8/assessment/failure-review.md)保持原样：原 gold 严格 1/6、0/4，质量未达标。

## 执行顺序

1. 候选、离线检查和审查完成，经 root 明确允许后运行 `.venv/bin/python scripts/evaluate_conversation_round9.py prepare`。归一化 dataset 已准备，准备不代表输入冻结。
2. root 提供完整候选 Git SHA、源码/配置哈希、隔离 QA URL，以及模型、agent/review 协议、75 秒、decision 1800 / review 2600、阶段时间分配不变和无重试的配置证据。
3. 冻结候选后收到明确 GO 才执行一次；URL 以实际批准的隔离服务为准：

   ```sh
   .venv/bin/python scripts/evaluate_conversation_round9.py run \
     --go GO --base-url http://127.0.0.1:8083 \
     --approval-file .local/round9/evaluation-approval.json \
     --run-label round9-once
   ```

   日志不显示回答，raw seal 生成后才开始语义阅读；改 label 不能重新采集，失败不补跑。
4. 生成模板，逐项阅读最终回答、原文和工具轨迹，写证据位置后评分：

   ```sh
   .venv/bin/python scripts/score_conversation_round9.py template \
     --run docs/evaluations/conversation-round9/runs/round9-once \
     --output docs/evaluations/conversation-round9/assessment/annotations.json
   .venv/bin/python scripts/score_conversation_round9.py score \
     --run docs/evaluations/conversation-round9/runs/round9-once \
     --annotations docs/evaluations/conversation-round9/assessment/annotations.json \
     --output docs/evaluations/conversation-round9/assessment/scores.json
   ```

开发团队 AI 非盲评分，另一 AI 复核仅属内部交叉审查。分别报告原 strict、补充诊断和交付类型；unknown 不当零，不将两套 rubric 混合成统一准确率。
