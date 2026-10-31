# Round 6 答案质量回归

本目录保存先冻结、一次采集、后评分的完整记录。十轮都是开发者已看过的合成回归；保留 Round 5 两套原 gold 和旧失败记录，不称独立评测。

唯一一次采集已结束，整体验收未通过：[结果报告](REPORT.md) / [优化与难题记录](execution-notes.md)。旧六轮 strict 0/6（2 失败、4 依赖阻断）；原四轮 strict 1/4（3 份事实报告、1 次澄清）。17 次模型尝试、2 次 token/provider 接受未知，未补跑失败。

- [最终报告](REPORT.md)：整体验收未通过；两组strict为0/6与1/4，4个持久化响应包含1次错误澄清。
- [过程与难题](execution-notes.md)、[失败定位](assessment/failure-review.md)、[总迭代说明](../../conversation-round6.md)：新防护产生的回归、确定归因与后置修复。
- [预注册协议](protocol.md)：预算、依赖、停止规则、两套 strict 计分、补充诊断和已知评测难题。
- [执行工具](../../../scripts/evaluate_conversation_round6.py)：离线 prepare；明确 GO、QA 地址和候选源码哈希后才允许 run。
- [评分工具](../../../scripts/score_conversation_round6.py)：从已封存 raw 创建人工阅读标注骨架，检查完整性后分别统计两套原 gold。
- [离线执行器测试](../../../tests/test_round6_evaluation_harness.py)：验证依赖阻断、未知调用数停止、未知 token 保留、无重提与十轮分母。Fake API 不访问网络。
- [上轮旧题分数](../conversation-round5/development/REPORT.md) / [上轮四题分数](../conversation-round5/new-scenarios/README.md) / [原 gold 阈值异议](../conversation-round5/new-scenarios-secondary-review.md)。

## 操作入口

交叉审查在任何真实推理前发现并修复了输入持续冻结、trace 用量不确定性和评分指针有效性三处问题；详见 [v1→v2 修订记录](pre-run-revision.md)。当前准备流程保留原 v1 字节，root 须批准新的 v2 hash。评分指针使用 `raw:<封存运行文件>#/JSON/位置` 或 `input:<仓库相对冻结文件>#L1-L4`，不能引用不存在或未封存的文件。

```bash
# 仅离线校验并冻结输入。已存在的清单不覆盖。
.venv/bin/python scripts/evaluate_conversation_round6.py prepare

# 仅在 root 冻结源码并授权之后执行一次；审批文件放在 .local，不能包含密钥。
.venv/bin/python scripts/evaluate_conversation_round6.py run \
  --go GO --base-url http://127.0.0.1:8083 \
  --approval-file .local/round6/evaluation-approval.json --run-label round6-once

# raw seal 完成后，生成空白评分骨架；逐条读回答、检索、引文原文再标注。
.venv/bin/python scripts/score_conversation_round6.py template \
  --run docs/evaluations/conversation-round6/runs/round6-once \
  --output docs/evaluations/conversation-round6/assessment/annotations.json

.venv/bin/python scripts/score_conversation_round6.py score \
  --run docs/evaluations/conversation-round6/runs/round6-once \
  --annotations docs/evaluations/conversation-round6/assessment/annotations.json \
  --output docs/evaluations/conversation-round6/assessment/scores.json
```

示例 QA 端口不是运行授权；实际必须使用 root 提供的独立环境。approval JSON 必须提供这些字段：

```json
{
  "approved_for_once_only_inference": true,
  "candidate_sha": "<40 字符 Git SHA>",
  "input_freeze_sha256": "<input-freeze.json 的 SHA-256>",
  "endpoint": "http://127.0.0.1:8083",
  "model": "Qwen/Qwen3-235B-A22B-Instruct-2507",
  "max_decision_calls_per_task": 6,
  "max_review_calls_per_task": 1,
  "max_model_calls_per_task": 7,
  "max_model_calls_total": 70,
  "service_boundary_seconds": 75,
  "provider_retry_enabled": false,
  "task_retry_enabled": false,
  "embedding_enabled": false,
  "max_output_unchanged": true,
  "isolated_qa": true,
  "max_output_configuration": {"decision": "<实际值>", "review": "<实际值>"},
  "configuration_evidence": ["<配置和源码核验位置>"],
  "source_files": {"<仓库相对源码/配置路径>": "<SHA-256>"}
}
```

审批内容会进入 raw 证据，只能包含可公开配置值与文件指纹。两个脚本都不会读取 `.env`、启动服务、修改上轮文件或自行支付/租用 GPU。
