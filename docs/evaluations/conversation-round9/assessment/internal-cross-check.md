# Round 9 标注合并与交叉核验

本记录在 raw 封存后完成。核验者是本轮开发团队 AI：先单独评分 Alder/Kite 原四轮，再阅读另一位 AI 对 Cedar/Harbor 的六轮标注并复核原始记录。这不是独立人工评测。没有重调模型、修改源码、原 gold、raw、旧/新分组标注，也没有从失败 draft 推断最终语义分数。

## 合并和评分

[annotations-old.json](annotations-old.json) 与 [annotations-new.json](annotations-new.json) 按原 template 的十轮顺序合成 [annotations.json](annotations.json)。逐行 JSON 内容保持相同；候选、input freeze、raw seal 三个 SHA 完全一致。

两份源标注的顶层 scope 文字不同：六轮文件写明本组全部未交付，新四轮沿用通用已暴露合成开发评测说明。第一次严格等值检查在写合并文件前停止；这不是评测执行失败。最终保留 template 的通用 scope，并以 `source_annotation_scopes` 原样保留两份源 scope。未把“开发团队 AI”改写成独立人工或盲测。

原评分器成功生成 [scores.json](scores.json)：

```sh
.venv/bin/python scripts/score_conversation_round9.py score --run docs/evaluations/conversation-round9/runs/round9-once --annotations docs/evaluations/conversation-round9/assessment/annotations.json --output docs/evaluations/conversation-round9/assessment/scores.json
```

结果：旧六轮 strict **0/6**、交付 **0/6**；原四轮 strict **0/4**、交付 **1/4**，可评分内 strict **0/1**。共十个计划轮，5 次提交、1 次交付、4 次失败、5 次依赖阻断。旧六轮无可评分回答；评分器序列化为 `0/0`，这不是一个已定义的准确率。

[机器核验记录](cross-check-validation.json)验证了 input/raw seal、执行身份、template 顺序、源行未变、226 条理由记录及 609 个证据指针实例。评分器同时校验原 gold 文本、维度可评分状态与交付类型的一致性。

## 旧六轮未交付约定的复核

逐项复读 [旧六轮评阅](old-review.md)、Cedar/Harbor 首轮的 final-task、会话终态、检索正文以及 run-summary 依赖关系。两组首轮均 `FAILED` 且没有 `result`，会话 answer 为空；第二、三轮按前序失败/阻断而未提交，没有模型回答可评分。

六轮的 mandatory 均 blocked，forbidden 的 present 均 null，七类语义诊断均 not_evaluable。原冻结计分约定中的四个 fail 轴属于“未交付”，引用及检索轴为 blocked；不能把这些 fail 描述成已观察到事实全错、错误拒答或指代失败，也不能把 null 当作已证实没有禁止性断言。标注、评阅文字与运行证据一致，未发现需要改动源标注的语义分歧。

Cedar 和 Harbor 的检索实际包含各自规则、签字测量与相应限制；它们有助于定位故障已发生在检索之后。但正式答案没有交付，不能据此补成最终质量通过，更不能把首轮检索复用于未提交的后续轮评分。

## 四次失败必须保留三种不同证据状态

| 任务 | 原始 review 返回记录 | 停止与可确认结论 |
| --- | --- | --- |
| cedar-t1 | HTTP 200、response_received=true、usage_known=true；剩余 15,133.061 ms | review_error，budget_exhausted=false；不是超时，失败细因未保留 |
| harbor-t1 | HTTP 200、response_received=true、usage_known=true；剩余 26,233.506 ms | review_error，budget_exhausted=false；不是超时，失败细因未保留 |
| ar-t2 | HTTP 200、response_received=true、usage_known=true；elapsed 75,354.011 ms、剩余 0 | 总预算到期，未发布最终报告；provider 返回不等于任务成功 |
| kl-t1 | outcome=timeout、response_received=false、usage_known=false，HTTP 状态未知 | review transport timeout；provider 是否完成及实际 token 消耗未知 |

位置均可由各任务 `error_result.agent.trace[22]`、`[23]` 和 `execution_usage` 核对：[Cedar](../runs/round9-once/cedar-t1-final-task.json)、[Harbor](../runs/round9-once/harbor-t1-final-task.json)、[ar-t2](../runs/round9-once/ar-t2-final-task.json)、[kl-t1](../runs/round9-once/kl-t1-final-task.json)。

前两次的外围 `provider_failed` 或 worker HTTP 502 不能改写为 DeepInfra 返回 502，因为被记录的模型请求是 HTTP 200。封存包没有保存这两个失败 review 的完整内容与 finish_reason；JSON 截断、delta ID 分区、schema、引用或其他复核失败都只是待查解释，不能从 136/761 个 completion token 或错误名字直接定因。

两次超时也不能合并叙述成“模型都没返回”：ar-t2 有已知响应与 usage，kl-t1 没收到响应、usage 未知。只读交叉核验确认 [新四轮评阅](new-review.md) 区分了这两者。

## 唯一可评分回答与剩余边界

ar-t1 的主结论正确，六维 `2/2/1/1/2/2` 与原 gold 和真实引用相符。完整性缺口是没有解释默认 1 次与 Cedar 覆盖 2 次的优先关系；引用缺口是前两行只有 register，缺少 initial 定义的规则来源。它的上限上下文明确，不因一行未重复“最多”就认定虚构历史执行。

没有分数分歧需要并列裁定。源标注保持原样；严格结果仍未通过。工程修复、schema 检查、完整检索以及独立 AI 阅读都不能代替真实交付与语义正确性，更不能把这次 1/10 的交付包装为整体可用性提升。
