# Round19 第三次 scoped review 失败分析

结论：失败发生在生产 `restore_review_delta` 的跨列表互斥检查。模型把 `a1` 同时放进保留列表和替换列表；生产代码正确拒绝重叠决策。原始结果继续为 `structured_contract_failed`，整轮继续为 `blocked`。

## 原始证据

- 响应与冻结计划、manifest 的 SHA256 已核对；冻结生产源码全部匹配。
- 第三次响应：HTTP 200，finish_reason=`stop`，完整响应；usage 为 3249 input / 178 output / 3427 total。
- 保留 ID：`['a1']`；替换 ID：`['a1', 'a2']`；交集：`a1`。
- 生产异常：`Unknown, duplicate or overlapping replacement ID.`。
- 冻结提示明确要求：每个原始 ID 在两张列表中恰好出现一次。
- 原始响应 SHA256：`82923328664e6eb4c785fc07f9b7fb810e4ff3acf336a30d7b84ddb84807a38c`。
- 冻结计划 SHA256：`90c06e1a9ff049a4a5bd51a801e6dc7d184bd55cd5203c1557b023d04962d99e`。

## 分层结果

| 边界 | 结果 |
| --- | --- |
| HTTP / finish / usage | 正常 |
| 外层与 content JSON 的生产严格解析 | 通过 |
| 冻结 schema 的全部实际字段约束 | 有界离线检查通过；使用本次 schema 关键字专用检查器，并非通用 JSON Schema 认证 |
| production restore_review_delta | 失败：a1 同时保留和替换 |
| enforce_review_checks / validate_assessments | 原始输出未执行到 |
| 语义评测 | 未开展 |

冻结 schema 限制字段、类型、枚举、长度和列表大小，但未表达两张列表的互斥关系。因此不能把该失败描述为 JSON 生成失败、XGrammar 没有遵守 schema，或模型已经通过语义评测。生产恢复校验承担了额外协议约束。没有发现该样本的 grader 误判；原始异常可以复现。

## 仅用于定位的内存反事实

离线分别删除 `a1` 的替换项，或删除 `a1` 的保留决策，其余内容不变。两条分支均能经过生产 restore、enforce 和 final validation，输出 2 行，verdict 为 supported / insufficient，机械 findings 为空。它们不是模型新输出，也没有写回原响应；不得据此把本轮失败改成通过。

## 结论边界

一次样本不能估计失败率或证明提示词设计是唯一成因；没有独立语义判分。确认的是跨列表协议违规及现有 fail-closed 行为生效。后续可以单独评估更容易约束的逐 ID 单一决策结构，但需要新回归和独立冻结评测；本次没有更改生产协议、profile、probe 或新增调用。

完整逐层结果与文件哈希见 [JSON 分析](scoped-review-failure-analysis.json)。

## 公开复现材料

- [原始合成响应](runtime/probe-03/run/03-scoped_review/response.sanitized.txt)、[单次判定](runtime/probe-03/run/03-scoped_review/attempt.json)、[整轮结果](runtime/probe-03/run/summary.json)、[冻结计划](runtime/probe-03/run/frozen-plan.json)。这些证据的原始内容 SHA256 保留在 JSON 分析中。
- [真实失败样本回归](../../../tests/test_private_model_review_regression.py) 保留原始 assistant content，调用生产 `restore_review_delta`，验证重复保留和替换必须拒绝。
- 运行 `python -m pytest -q tests/test_private_model_review_regression.py`：本次实际 **1 passed**，零模型调用。测试通过表示失败保护生效，不表示本次模型响应通过。
