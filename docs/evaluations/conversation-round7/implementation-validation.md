# Round 7 工程验证与证据边界

2026-10-31。实现为 `workspace-agent-v8`。最终源码版本和逐文件哈希由 `candidate-freeze.json` 记录；本文件的工程测试不代表真实模型质量通过。

## 接入与兼容

- 当前问题的显式结论/缺证据请求，在已完成检索后限制澄清动作；第一次对象不明、原指代 guard 和用户明确要求确认范围的路径保留。返回未提供的动作在执行工具前停止，无修复请求。
- 比较槽仍受 8 条容量限制；容量错误记录通用 `missing_review_slots` 并保留旧字段。保留 Harbor 原八条稿的整体判定识别修复。
- 最终复核使用字符串 ID 绑定原命题，随后仍走精确来源、数字及证据范围检查。输出上限 2600、Agent 75 秒、6 次决策 + 1 次复核保持。
- Java 接受 v8 轨迹并保存澄清策略；错误文案区分“请求或执行时限”与“总预算耗尽”。原 Harbor 第三次决策超时时还余 19.355 秒，这条历史证据不足以支持总预算已用完。
- 默认缓存命名空间更新为 `conversation-round7-agent-v8`。隔离 QA 使用新数据库和 namespace；主服务保持原版本，未复用主工作区做评测。

## 已执行的测试

| 层 | 命令或记录 | 结果与边界 |
| --- | --- | --- |
| Python 最终完整工程回归 | `.venv/bin/python -m pytest -q --junitxml=.local/round7/python-tests-final.xml` | 1163 passed，1 条既有 Starlette/httpx 弃用提醒；无真实模型质量结论 |
| JVM + 真 MySQL/Redis | `LABLENS_INTEGRATION_TESTS=true`，隔离 `lablens_round7_tests_20261031`，Maven verify | 68 tests，0 failure/error/skipped；其中34项实际基础设施集成 |
| 前端逻辑 | `node --test services/workspace/tests/workspace-ui.test.cjs` | 58 passed，0 failed/skipped |
| 实际跨服务受控协议与超时 | [runtime-fault/summary.json](runtime-fault/summary.json)，`scripts/evaluate_runtime_round7.py` | 两场景64项检查通过；真实JVM/MySQL/Redis/Python，模型为loopback固定返回，0外部调用，不是LLM效果或负载测试 |

受控超时采用 6 秒 Agent 预算、8 秒 review 延迟以缩短复现实验；真实小评测仍是 75 秒，两者不混为一次测量。新 runtime runner 复制既有 Round4 工具并只适配 ID 输出协议，历史 runner、题目、原始失败与 gold 均未改写。测试库名沿用共享 runtime helper 的 round4 前缀加随机运行 ID，不是主库或上轮 QA 库。

## 已记录的实际难题

1. 比较谓词可能被相邻指标借用，纯问句也可能被误当已答；三条实际反例先失败再修，见 [coverage-development.md](coverage-development.md)。
2. 只识别“先问”会漏掉“如果资料有多个部署，向我确认对象”的条件偏好；内部复核后保留该显式偏好，见 [clarification-development.md](clarification-development.md)。
3. 短 ID 减少了构造响应的重复文本，但新增协议说明使请求增加184字节；不把字节变化写成延迟改善，见 [review-development.md](review-development.md)。
4. 评测器也需要负例：未完成标注、与 strict 冲突的诊断、失效候选身份及 token 汇总冲突在冻结前单独复核，见 [harness-development.md](harness-development.md)。

最终真实模型的交付、检索覆盖、语义正确性、拒答与澄清均需在新 raw seal 生成后另行标注。旧十题已暴露，不能称为盲测或独立人工评测。

最终评分工具75项由另一开发AI复验，原5个反例全部按要求拒收或标未知；最终完整Python回归1163项通过。输入冻结30文件，SHA-256 `1c7c2dbf099c7b04d8403a8df366ae47034e29e28ff9e94fc189f33376618d98`。
