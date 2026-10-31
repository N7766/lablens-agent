# Round 7：固定命题复核使用短 ID

本子项修改 `lablens/rag.py`，增加 `tests/test_round7_compact_review.py`。目标是减少最终复核重复输出既定命题的负担，并严格保留原有命题范围与证据检查。它不扩大 2,600 的输出 token 上限、不增加复核次数，不把字符串压缩等同于推理质量或延迟改善。

## 背景与设计边界

Workspace Agent 的复核已经固定了草稿命题集合，模型只能修改 verdict、explanation、basis 和 evidence_ids。此前仍要求它逐行复写完整 statement；schema enum 与草稿输入也重复出现相同长文本。长命题会占用输出，但现有证据不能证明它是上一轮超时的唯一原因。这里验证的是协议冗余的减少，真实超时改善要由冻结模型运行另行验证。

新协议仅在提供 `scoped_review_contract` 时启用：

- response schema 名称为 `evidence_review_by_id`，每行返回字符串 `assessment_id`，取值为 `a1` 至当前行数。没有 contract 的普通 RAG 仍使用 `evidence_assessments` 和原 statement 字段。
- `untrusted_draft` 每行保留原命题与对应 ID。原始引文若能精确映射 catalog，则仍使用第五轮已有的 evidence_id 输入压缩；不能精确映射时保留原引文，不做相似匹配。
- 模型不得返回 statement 或额外字段。服务端校验完整唯一 ID 集合，再按草稿顺序恢复原始 statement，依次进入已有 `resolve_selections`、`enforce_review_checks`、`validate_assessments`。
- 返回行顺序可以不同；缺行、重复 ID、未知 ID、整数/浮点/bool 索引、伪造 statement、替换 ID 为 statement、额外行或字段都导致明确失败。一次复核后失败即结束，没有第二次修复调用。
- 合法的 verdict、explanation、basis 和 evidence_ids 仍可被纠正；不能为了守住文本不变而保留错误 verdict。

`_scoped_review_statements` 在请求发出前验证 contract 与原草稿逐行完全相同、1–8 个非空唯一命题。这样不能用一个被换序或替换过的 contract 把 ID 错绑到另一个命题。`_restore_review_statements` 在收到输出后恢复原文；短 ID 不授权模型添加新命题。

## 与本轮覆盖修复的衔接

复核提示里的“Explicit overall-verdict slots”扩展为“Explicit user-requested slots”，因为新槽位还可能要求说明用户更正前后哪些条件变化、哪些仍适用。相关提示要求从本次提供资料核验两个范围；以前的助手回答不构成事实证据。缺少旧范围证据时保留比较的不确定性，不能猜旧值，也不能只凭旧答案复述。

这些服务端槽位的初始 insufficient 仍只是待复核标记，不是资料缺失的定论。固定 ID 保证模型没有增删命题，不保证这些命题已经覆盖所有用户问题，也不保证结论语义正确。

## 遇到的难题与处理

| 难题 | 实际处理 | 仍然保留的边界 |
| --- | --- | --- |
| 用整数索引可能接受 Python 中 `True == 1` | 使用 `a1` 形式的字符串 ID，执行精确类型与白名单检查 | ID 只定位原命题，不表示证据真实性 |
| schema enum 不能单独保证每个 ID 都出现一次 | 校验行数、唯一集合、字段集合，再恢复原顺序 | 模型选错 evidence_id 仍要由现有检查与语义评测发现 |
| 接受旧 statement 输出以兼容 mock 会削弱新协议 | scoped 模式不兼容旧输出；旧 mock 由 root 按新 schema 适配，legacy 无 contract 模式不变 | 真实模型不遵守协议时仍失败，不静默降级 |
| 原 review 输入有不能映射到 catalog 的短引文 | 保留原始输入表现，只添加 ID，不删除非 catalog 引文 | 不因此认可任意短引文，最终仍走原引文检查 |
| ID 输出可能掩盖固定范围校验 | 恢复 exact statement 后再执行原 enforce/validate | 数字正确、引用存在仍不等于语义蕴含 |
| 为解释协议和新旧比较增加指令，抵消一部分输入节省 | 如实测量并记录请求增加、响应减少，不只展示有利数字 | 没有从字节变化外推 token、首字延迟或成功率 |

## 离线序列化对照

使用 [第六轮 Harbor-t1 原任务](../conversation-round6/runs/round6-once/harbor-t1-final-task.json) 中已记录的八行 `verify_and_submit_report` 草稿，恢复精确引用后构造同一问题、资料与 contract。该原运行在复核前失败，所以这里的“响应”是把草稿的相同字段构造成两种传输形式，**不是任何一次真实模型的新输出**。

| 项目 | 修改前 | 修改后 |
| --- | ---: | ---: |
| 完整请求序列化字节 | 19,953 | 20,137 |
| 等义构造响应序列化字节 | 2,428 | 1,976 |
| 输出 token 上限 | 2,600 | 2,600 |
| 外部模型请求 | 0 | 0 |

请求增加 **184 字节**；响应减少 **452 字节（18.6%）**。来源 catalog 完全相同；新响应恢复原 statement 后与旧构造响应逐字段一致。不能将这些字节数称为精确模型 token 数，也不能据此声称线上延迟减少。

对照基线 `lablens/rag.py` SHA-256：`e54babb2d1e6781345d9a28218d384b9492f6841e3d02db4b2f18d4ac8d23686`。
原 Harbor 任务 SHA-256：`d8d752c44c6c8391a7955a3f09cd816dfad44ca9b7bc847422942c5873c6b310`。本轮最终代码 hash 由统一 freeze 记录，避免在文档修订前过早声称候选已经冻结。

## 定向验证

- `.venv/bin/pytest tests/test_round7_compact_review.py -q`：**29 passed**。
- 联合新澄清策略和原普通 RAG：`.venv/bin/pytest tests/test_round7_compact_review.py tests/test_clarification_policy.py tests/test_rag.py -q`：初次 **143 passed**；合入 root 追加的 3 个条件澄清控制后 **146 passed**，1 条既有 Starlette/httpx 提醒。
- 正向控制证明：反序 ID 响应恢复原顺序；可修正 verdict、解释、basis 和来源；只调用一次 provider；引用原文仍由服务端恢复。
- 拒绝控制证明：缺失/重复/未知/错误类型 ID、伪造 statement、扩展字段、越界/重复证据 ID 和非法 verdict/basis 均拒绝，不重试。错绑 contract 在首次请求前失败。
- 数值控制证明：短 ID 协议下凭空新增 `18 ms` 仍被原数值 guard 降为不足，不因 ID 合法而放行。

这些是协议与机械检查，不是模型答案质量评测。Root 的完整测试、实际跨服务超时轨迹与新模型封存结果应另行报告。
