# Round 8：对象、引用与比较回归

本目录保存 Round 7 实际失败修复后的有界回归。**一次采集、封存、评分与浏览器回看已完成，质量门未通过。** 沿用原 10 轮、16 份合成资料和两套 gold，全部已被开发者阅读，不是新题、盲测或独立人工评测。

- [结果](REPORT.md)：6报告+1必要澄清、1超时、2依赖阻断；原标准严格1/6、0/4，保留评分分歧。
- [执行过程](execution-notes.md)、[工程验证](implementation-validation.md)、[失败排查](assessment/failure-review.md)、[浏览器回看](browser-check.md)。
- 候选 `141a9186`、输入46文件、raw583文件均保留；封存后无源码修改或追加模型调用。下文命令为本次执行记录，不可再次调用已消耗的一次采集。

- [协议](protocol.md)：75 秒、6 decision + 1 review、最多 10 个任务和 70 次模型尝试，无重试/重提/embedding。
- [工具准备与难题](harness-development.md)：一次采集、历史证据绑定、标注完整性与失败保留。
- [Round 7 原结果](../conversation-round7/REPORT.md)和[失败复核](../conversation-round7/assessment/failure-review.md)继续保留；原 gold 在 `conversation-round5`。

## 执行顺序

1. 源码、离线检查和审查完成，经 root 明确允许后运行 `.venv/bin/python scripts/evaluate_conversation_round8.py prepare`。不得为了提前占位而冻结尚未审查的工具。
2. root 提供完整候选 Git SHA、源码/配置哈希、隔离 QA URL，以及模型、75 秒、decision 1800 / review 2600 和无重试的配置证据。冻结后运行期间不得修改。
3. root 明确 GO 后只执行一次；URL 以实际批准的隔离服务为准：

   ```sh
   .venv/bin/python scripts/evaluate_conversation_round8.py run \
     --go GO --base-url http://127.0.0.1:8083 \
     --approval-file .local/round8/evaluation-approval.json \
     --run-label round8-once
   ```

   运行日志不显示回答。生成 raw seal 后才开始语义阅读。一次 claim 被占用后，换 label 也不能再次采集；失败不重跑。
4. 生成模板，逐项看回答、原文和工具轨迹，写证据位置后评分：

   ```sh
   .venv/bin/python scripts/score_conversation_round8.py template \
     --run docs/evaluations/conversation-round8/runs/round8-once \
     --output docs/evaluations/conversation-round8/assessment/annotations.json
   .venv/bin/python scripts/score_conversation_round8.py score \
     --run docs/evaluations/conversation-round8/runs/round8-once \
     --annotations docs/evaluations/conversation-round8/assessment/annotations.json \
     --output docs/evaluations/conversation-round8/assessment/scores.json
   ```

标注由开发团队 AI 非盲阅读，另一 AI 复核仅是内部交叉审查。分组报告原 strict 和补充诊断，不合并两套 rubric 为统一准确率。报告/澄清/混合/未响应分别计数，unknown token/provider 接受状态不能记为零。
