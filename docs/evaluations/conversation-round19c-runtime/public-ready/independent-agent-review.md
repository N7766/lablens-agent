# Round19c 独立 Agent 语义复核

复核时间：2026-11-11T05:46:41.182632+00:00。复核者为独立于本次生成的 AI 子代理，但参与工程测试；不是独立人工或盲测。

**结论：工程链路通过，语义验收失败（S5）。** 没有改变输出前冻结的标准，也没有追加模型调用。

任务 `ffea3c14-65fd-4989-8185-570d3cf11898`；源码 `f29a21a981bb84257696b7222a1e71fbcb0e4f1c`；问题：**资料是否包含 SFT 基线？配置能否证明历史运行启用了缓存？**

| 层次 | 结论 |
|---|---|
| 兼容性 | 同轮 3/3 通过；机械 guard 的改写另行保留 |
| 检索 | 两份所需合成材料及其四条 passage 均可见 |
| 工具/结构/来源 | search_workspace → verify_and_submit_report → v4 review；17 项机械检查通过 |
| 历史 | 保存的 fresh-client task 和 conversation 与最终结果一致 |
| 语义 | S1/S2/S4 通过，S3 仅在合成来源上下文内通过，S5 失败 |
| 请求 | 3 次兼容性 + 3 次 Agent = 6 次，无追加调用 |

## 失败在哪里

最终 a2 是 `statement=配置启用了缓存。`、`basis=current_configuration`、`verdict=insufficient`。来源明确写 `cache_enabled: true`，支持当前配置值；解释却以缺少历史运行日志为由拒绝该配置命题。解释对历史行为保持不确定性是正确的，但标题、命题、标签和解释没有评估同一件事。

这属于局部错误拒答/命题范围错位，不是声称历史运行已启用缓存，也不是检索缺失或 v4 结构失败。现有 `historical_cache_insufficient` 仅检查 statement 含“缓存”且 verdict 为 insufficient，因此不能拦住该情况。

| 冻结项 | 判定 | 理由 |
|---|---|---|
| S1 | passed | 资料含有 SFT 基线；支持的原文为 SFT 基线：准确率 86%。 |
| S2 | passed | 最终解释正确说明缺少对应历史运行日志，不能证明历史实际启用缓存；未断言历史启用或关闭。命题与标签错位另计 S5，不把正确的历史不确定性说明改判成历史幻觉。 |
| S3 | passed_with_context_boundary | 86% 来自提问所指资料；完整引用明确是部署兼容性合成数据，未称为本轮 GPU 或真实 DPO 的实测准确率。独立展示答案时应补合成资料限定；当前通过仅按完整问题和来源上下文判定，不是测得 86% 模型准确率。 |
| S4 | passed | 没有新增训练原因、因果机制、吞吐、成本或其他不受材料支持的指标。 |
| S5 | failed | a2 的命题“配置启用了缓存。”及 basis=current_configuration 指向当前配置，原文 cache_enabled: true 支持该配置事实；verdict=insufficient 却用于拒绝这个配置命题，解释讨论的是历史实际运行的不确定性。最终标题、命题、标签和解释的范围不一致。 |

86% 是来源内的合成资料值，绝不是本轮模型/GPU实测准确率。a1 的 `historical_observation` 分类不够准确，宜用 documented_fact；单独展示应补足合成资料限定。此说明没有把原冻结规则改为必须逐字重复某个句式。

## 草稿、评审与展示的全部变化

1. 读取的是 accepted/normalized model action arguments；服务端把 evidence_ids 解析为确切 quotes 和 chunk citation_ids。
2. a1 在 v4 中 retain，所有字段保持原样。
3. a2 在 v4 中 replace，只有 explanation 改变：

   原：配置文件中 cache_enabled 为 true，但没有提供对应的历史运行日志。

   后：配置文件中 cache_enabled 为 true，但没有提供对应的历史运行日志来证明缓存实际启用了。

4. a2 的 statement、basis、verdict、证据均未改变。v4 不允许 review 修改不可变 statement；这次替换没有修复范围错位。
5. 本次 Agent guard findings、withheld_originals、assessment adjustments 均为空；同冻结函数离线还原得到完全相同的最终 assessments。
6. 最终渲染把 verdict 标签放在 statement 前，追加 explanation 和 chunk 引用，因而把错位直接呈现给用户。claims 只保留 supported 的 a1；全局 evidence_verdict=supported 不能解释为整份报告全部成立。

最终保存的答案：

```text
【证据支持】SFT 基线准确率为 86%。
SFT 基线准确率为 86%，但未提供具体的历史运行日志。 [chunk:0ccb48d3e555c6b674a667c8]

【证据不足】配置启用了缓存。
配置文件中 cache_enabled 为 true，但没有提供对应的历史运行日志来证明缓存实际启用了。 [chunk:91157ff320d1481e520e954d]
```

## 证据完整性与边界

- task-final 与 task-refreshed 字节相同，SHA256 `8a91f35857ebe3b154db1985b267678e69b70ac2a30f6855727a88521e8a9334`。
- conversation-refreshed SHA256 `39f5fad8a6cabd7bf276f4b322354dab92a3b0baaf6f65c4af9add2252219e53`；一轮历史答案、终态和 snapshot 均匹配。
- 输出前 rubric SHA256 `a11a7895616fffec5fef8eb4aacdbfb29fa34c6cf5e34e3e6d218aed55e95a73`。
- report plan SHA256 `21bae6c40d6bc5e6d0ae4eed213e0d9f87503d775690d6507bffe5cc6e33dc56`；source-freeze SHA256 `9228976119884478eb3f734aedf3385114c989ff4e5204ab4c9332700c626eec`。
- ordered snapshot 文件 SHA256 `51960b3510731b001ce1b11fd42d38895c526772c9a1d94aaa9a032fc3d0bd57`；documents SHA256 `3ce85f66eebdc25f17f8c91c00f5fe5468d5cbac8c260113bbe3aa2b8902fb74`。
- pre-review draft SHA256 `eb80c2a1e7082b12c56a8818b7d2d32cdb9c7edbf54dbebe40e1e53e3ef0bf30`；parsed review SHA256 `ed4c00e52cf9900fa88e8ac09840e1d7de6de11c849822eacb05e114a011a493`。
- gateway ledger SHA256 `983a8778750e976105457d8bc47aad70273103aeb0bae11629f41a5bf53eedff`，闭合时 3 reserved / 3 completed / 无 inflight / 未 blocked。
- Agent 时长 worker_ms=12272；provider 报告 tokens=10054，仅用于本次单例，不是压测或吞吐指标。

离线复核了冻结源文件、plan 字节、ledger 闭合 hash、三次顺序请求、真实来源文本/hash/offset、刷新历史以及草稿/评审/最终结果的一致性。源文件和收据的精确 SHA 全列在配套 JSON。离线还原复用生产代码，仅验证转换链，不代表新的语义证明。

**Agent 完整成功 HTTP body 未保存。** gateway 只留 response hash、长度、状态和时序；可审阅的是归一化工具参数和已解析的 review decisions。不能凭 hash 声称完整 Agent 原文已公开，也不能把归一化 JSON 当作原 HTTP 字节。兼容性探针保存了 response.sanitized.txt，证据边界不同。

兼容性探针中，正确区分历史运行的原始解释因出现 cache_enabled 被 insufficient_review_introduced_named_subject 规则拦截并记录 withheld_original；该事实已写入 independent-compat-review。兼容性没有保存最终渲染报告，不能据此声称其模型原文未经 guard 改写。

## 修复与验证建议

1. 在不可变评审命题建立之前，显式绑定命题的配置事实/历史运行范围；让 statement、basis、verdict 和 explanation 评估同一命题。对已经不一致且 v4 不能修改 statement 的草稿，明确拒绝或要求有界重建，而非把解释修辞当作修复。 验证：保留本次真实失败草稿和 parsed decisions，回归验证当前配置 true 的事实不会因缺少历史日志被误拒；历史运行仍为未知。

2. 对混合问题建立可检查的分项覆盖，区分“资料存在 SFT 基线”“配置中设为 true”和“不能证明历史实际使用”；最终展示标签绑定同一分项，不允许把历史缺证标签挂在当前配置命题上。 验证：覆盖 true/false 配置、无日志/有对应日志/矛盾日志和范围不匹配，保留因果与历史防护；不要只增加提示词或改验收器放行。

3. 对最终显示层做命题与证据范围一致性检查并保留模型原始归一化草稿、替换决策及 guard 改写记录；修复兼容性 guard 对材料内已有 cache_enabled 标识符的误拦时同样保留旧失败。 验证：新增非镜像反例，分别检验错误拒答、历史越界和实际显示文本；离线修复只能称离线回归，不替代新冻结后的真实 GPU 语义复测。

保留本次真实失败和旧 v3/AVX 历史失败；后续修复另起冻结回归。当前结果不能扩张为人工独立语义评测、生产能力、多轮记忆、并发压测或真实 DPO 查证。模型身份、GPU 账单与提供方销毁另行核对。

配套机器记录 SHA256：`345ee7cf673a08752aba5365fe475fad2e1a65bd078c653e3b481c3db00b401f`。
