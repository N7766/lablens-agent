# Round 20 配置与历史运行：独立 AI 反例设计

日期：2026-11-11。设计者为本轮未编写生产修复代码的 AI 审查子代理；反例和建议在开发期间向实现者公开。因此这是**开发可见的定向反例设计，不是独立人工评测、未见题盲测或真实模型复测**。设计本身没有调用模型服务，也没有租用 GPU。

本页规定语义期望与工程验收边界；表格不是已执行结果，不代表有限词法规则能够自动判断其中所有情况。后续运行记录须分别标明：规则/协议测试、受控响应回放、真实模型生成以及独立语义复核。

## 问题与判定对象

原始失败保留于 [Round 19c 最终任务](../conversation-round19c-runtime/report/run/task-final.json)：命题“配置启用了缓存。”使用 `basis=current_configuration`，却因为没有历史运行日志被标为 `insufficient`。不能以重新解释原命题、只改 basis 或通用拒答文案，把原始失败改算通过。

以下表格固定两个待判断命题，防止配置与运行结果互相替代：

- **C**：当前适用的 `config.yaml` 记录 `cache_enabled=true`。
- **H**：指定的 `run-7` 实际启用了缓存。

`supported-as-recorded` 表示所供、可定位的运行记录支持该观察；不是对日志真实性、实际执行或历史版本身份的外部认证。除明确缺失/冲突的条件外，表格假定已给出适用对象与版本、日志 run ID、明确的观察字段及必要关联，避免把缺少这些条件偷偷当成已满足。

## 十二项非镜像反例矩阵

| ID | 提供的材料 | C 的语义期望 | H 的语义期望 | 必须区分的边界 |
| --- | --- | --- | --- | --- |
| M01 | 当前配置 `true`；未提供运行日志 | supported | insufficient | 缺历史日志不能使已知当前配置值未知。 |
| M02 | 当前配置 `false`；未提供运行日志 | contradicted | insufficient | 拒绝历史推断时仍应保留反对 C 的配置证据。 |
| M03 | 当前配置 `true`；`run-7` 明确记录缓存启用 | supported | supported-as-recorded | 每个结论选择自己的配置/观测证据，不把配置当日志。 |
| M04 | 当前配置 `false`；`run-7` 明确记录缓存启用 | contradicted | supported-as-recorded | 当前配置与历史运行属于不同范围，可同时成立，不能自动判为同一事实冲突。 |
| M05 | 当前配置 `true`；`run-7` 明确记录缓存禁用 | supported | contradicted | 配置支持 C 不得覆盖反对 H 的运行记录。 |
| M06 | 当前配置 `false`；`run-7` 明确记录缓存禁用 | contradicted | contradicted | 两个否定结论也必须各自绑定命题，不能合并成“缓存信息不足”。 |
| M07 | 当前配置 `true`；仅有 `run-8` 缓存启用记录 | supported | insufficient | 错 run 的观测不能补足 `run-7` 证据。 |
| M08 | 当前配置 `true`；两条同样适用于 `run-7` 的记录分别称启用/禁用，无可靠优先级 | supported | contradicted，并说明来源冲突 | 必须展示双方；不能任选一条、把冲突扩散到配置命题或直接归零为缺资料。 |
| M09 | 同一适用版本的两份配置分别写 `true`/`false`，无可靠优先级；无运行日志 | contradicted，并说明来源冲突 | insufficient | 多份配置冲突与缺运行观测是两种不同原因。 |
| M10 | 没有当前配置快照；`run-7` 明确记录缓存启用 | insufficient | supported-as-recorded | 日志不反推出当前配置；`current_configuration + insufficient` 本身不是错误。 |
| M11 | 只给注释示例或条件句：`example: cache_enabled=true` / `If cache_enabled=true, ...`；无实际配置或运行记录 | insufficient | insufficient | 字面匹配不是配置事实；窄机械检查必须保留 unverified，不能自动升级 supported。 |
| M12 | 资料明确区分 `cfg-v1=true`、当前适用 `cfg-v2=false`；无运行日志 | contradicted | insufficient | 已确定的版本变化不是两份同时适用配置的冲突；不要丢弃版本限定。 |

另需将问题缩减为“只问 C”与“只问 H”各复用至少一个用例。上下文出现另一类信息不等于用户要求额外回答；反之，用户同时问 C/H 时，修复 C 一行也不能声称 H 已覆盖。

## 报告发布与证据保留要求

1. 原始 a2 负例回放必须终止发布，`answer`/`claims` 不含错误标签；原始 GPU 评测仍记失败。
2. v3/v4 的 retain 不能绕过已识别错误；replace 后仍错位也不能发布。允许已有的一次 review 修复，不增加重试或模型调用。
3. 合法 `supported` 配置行附带“历史执行仍未知”的限定，应保留；不能只因出现“历史/日志”就拒绝。
4. 配置资料缺失或互相冲突时，不能一律禁止当前配置命题使用 `insufficient`。尤其测试“当前配置快照没有提供，历史运行日志也没有提供。”这一自然表述。
5. 将旧行的 basis 改成 `historical_observation`，而 statement 仍是当前配置命题，不算修复。
6. 一次 review 若修正配置 verdict/explanation，可发布该配置结论，但 `question_coverage_verified` 仍为 false；不能凭单行修复宣称整题合格。
7. “配置启用且 run-7 实际生效”的混合命题不能由服务器悄悄拆写成 supported 事实；固定 statement 的评审无法拆分时应明确终止。
8. 终止时保留原草稿、规范化模型决策、恢复后的行、诊断阶段，以及真正触发后置渲染拒绝的行和调整记录；已有调用数、token 使用和失败轨迹不能丢失。
9. 材料内的 `cache_enabled` 被“新增命名主体”规则误拦是另一个失败，应单独回归，不与命题范围失败合并计算一个成功率。

## 词法检查的保守边界

“runtime configuration / 运行时配置”是配置名词的正常表达，不能仅因 `runtime / 运行时` 出现而断言它是历史执行命题。对于既可能指配置、又可能指执行的未解析表达，优先记录 `unclassified/unverified`，不把有限词法规则包装成语义裁判。

该矩阵的目标是检验事实、命题、标签、对象和时间范围是否一致。精确引文存在、JSON 正确、工具调用成功和测试代码通过，均不能单独替代这些语义期望。
