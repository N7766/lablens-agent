# Round 10 复核协议：运行前独立检查

2026-10-31。检查起点为 `11bf4130ac1f018220aa8c16b5eb7326b0c355f7`。本记录在 Round 10 新的真实请求前形成；此时未修改生产源码，未调用模型。检查范围为 Round 9 复核提示、响应 schema、ID 恢复与已经封存的失败证据。

## 已确认与尚未确认

Round 9 Cedar / Harbor 复核的 HTTP 200 返回、已知用量和未耗尽总预算已保存；失败正文、finish_reason 和校验明细没有保存。因此，不能声称下面的任何候选就是历史失败的根因。新调用即使重现错误，也只直接证明新调用的失败机制，不能恢复旧响应。

1. **同一个 system 提示包含互相冲突的输出要求。** `assessment.py:183-188` 要求仅有 `assessments` 顶层、1–8 行、每行含 `statement`；`rag.py:427-455` 接着改成每个固定 ID 都返回一行、用 `assessment_id` 替代 `statement`；`review_delta.py:10-20` 最后要求两个顶层列表，允许已保留的行没有替换内容。后置指令可以表达覆盖关系，但全部要求同时暴露给模型，完整复核与稀疏复核没有各自唯一的格式描述。新 schema 则只接受双列表。
2. **JSON Schema 合法不等于完整 ID 分区合法。** 当前 schema 只对列表类型、长度和单项枚举设限，没有跨列表全集和唯一性约束。后端 `restore_review_delta` 仍正确拒绝缺行、重叠、重复及不允许保留的 ID；不能为了提高交付率删掉这些检查。
3. **候选必改行需要从多个字段推导。** `draft_findings` 使用从 0 开始的 `assessment_index`，而输出使用从 1 开始的 `aN`。策略只显式列允许保留的 ID，没有单列其补集。这不是已经确认的错误，但可增加模型推理负担。保留许可也不代表语义正确：任意允许行仍可能需要替换。

代码来源：[提示组装](https://github.com/N7766/lablens-agent/blob/11bf4130ac1f018220aa8c16b5eb7326b0c355f7/lablens/rag.py#L391)、[通用选择提示](https://github.com/N7766/lablens-agent/blob/11bf4130ac1f018220aa8c16b5eb7326b0c355f7/lablens/assessment.py#L183)、[稀疏恢复器](https://github.com/N7766/lablens-agent/blob/11bf4130ac1f018220aa8c16b5eb7326b0c355f7/lablens/review_delta.py#L10)、[旧失败边界](../conversation-round9/assessment/failure-review.md)。

## 可复现的机械反例

构造 2 行、均允许保留的普通事实草稿，使用 `_generation_payload` 生成当前 schema。通过本机 `jsonschema.Draft202012Validator` 校验响应，再交给实际 `restore_review_delta`。不调用供应商，未安装新依赖。

| 响应 | JSON Schema | 实际恢复器 |
| --- | --- | --- |
| `retained_assessment_ids=[]; assessments=[]` | 通过 | 拒绝：Every original ID requires an explicit review decision. |
| `retained_assessment_ids=[a1,a1]; assessments=[]` | 通过 | 拒绝：Unknown, duplicate or non-retainable ID. |
| 保留 `a1`，替换中再次给出 `a1`，遗漏 `a2` | 通过 | 拒绝：Unknown, duplicate or overlapping replacement ID. |

这是 schema 与业务约束的边界验证，不是模型实际输出记录。已有 `tests/test_round9_review_delta.py` 的负例也覆盖这些本地拒绝路径。

从封存 Round 9 的提交工具参数、检索结果和会话上下文重建提示后，Cedar / Harbor 都有 8 行，各有 7 行允许保留：Cedar 强制替换 `a3`，Harbor 强制替换 `a4`；对应原有数值检查分别是 `13.33%`、`150 秒` 缺少选中引文锚点。成功的 `ar-t1` 有 5 行且全部允许保留，但实际响应全部替换。该重建不能证明两次失败如何分配了 ID，也不能把前述数值发现直接当作失败原因。

## 最小通用修复候选（尚未实施）

优先候选是**仅清理提示契约**：将现有选择提示拆成可共用的证据语义规则和各路径专用的输出格式。普通选择保留原有单列表/statement 格式；固定 ID 全量复核只描述其固定行格式；稀疏复核只描述双列表、哪些行可保留、所有原 ID 必须覆盖一次。保留阈值、因果、配置/历史、来源和逐行证据要求的原文与含义，不删减事实约束以换取简短。

可选补充为显式给出 `required_replacement_assessment_ids`，其值必须严格等于原始 ID 减去允许保留 ID，并强调允许保留仍须逐行判断。这只是派生提示信息，不能赋予新的保留资格，也不替代服务端校验；是否纳入应在 baseline 封存后说明。

本轮不建议仅靠 `uniqueItems` 或提高 `minItems` 宣称解决分区问题：前者不能排除内容不同但 ID 相同的两条替换，也不能排除跨列表重叠；后者不能保证缺失的恰好是必改行。改成按 ID 的对象或全量 action 协议属于响应结构变更，应单独评估供应商支持、解析及预算。本轮也不应以自动补齐、隐式保留、重试、增加超时或提高 token 上限掩盖拒绝。

## 变更前后应保留的反例

- 三条路径各自只出现当前响应形状；稀疏路径不再要求顶层仅有 assessments、不再要求替换列表至少 1 行、不再要求每行输出 statement。
- 稀疏路径的全保留、混合及全替换恢复后与对应完整报告一致，仍执行原有数值、来源、因果和配置/历史检查。
- 缺行、重复、重叠、未知 ID、非许可保留、无效证据、截断 JSON 仍失败；不能改成返回草稿或追加调用。
- 若新增必改 ID 提示，所有许可/必改集合与原始 ID 构成严格分区；无引文占位行和已知数值/绑定错误行不能被错列为可保留。
- 保留语义盲点反例：一条用错误结论解释合法引文的草稿，即使 schema 和字面检查都通过，也仍不能宣称语义正确。
- 对本轮至多 2 个原协议诊断请求及至多 2 个修复后请求逐次保存请求、响应、finish_reason、精确失败原因、用量、时限和输入/源码身份。不重跑旧 10 轮评分；新结果不回填 Round 9 丢失响应。

## 风险与解释边界

提示单源化可能改变模型选择保留还是替换的分布，甚至降低回答质量；既有单元测试不能证明真实模型稳定遵守协议。新诊断使用已暴露旧稿和同一供应商，最多 4 次请求不足以估计成功率、时延分布或泛化能力。若新 baseline 未复现旧错误，应记录“未复现”，不能反推旧版本没有问题。若修复后通过，只能报告具体请求通过及其审计证据，不能升级为整条 Agent 链路质量验收。

## Baseline 封存后的实施记录

主任务确认 baseline 的 17 个原始文件已封存，seal SHA-256 为 `d509c9a6606016f09e3a6d171db4824402ea51da80f9e90b4a54e6e08d5d968a`。两次直接复核均传输超时，没有供应商响应正文或用量；因此没有复现可分类的 ID/JSON 失败，提示冲突仍是代码层面的已知问题，不能归因为旧失败根因。

获得实施指令后，仅修改 `assessment.py` 的提示常量拆分和 `rag.py` 的提示组装：原有完整证据规则与规则判定要求逐字共用，每个 scoped 路径各自仅有一个响应格式。普通生成、非作用域复核的 system 字节与[运行前哈希](reconstruction.md#附录诊断-runner-的只读数据核对与非作用域提示基线)完全一致。离线完整固定 ID builder 也只保留自身单列表格式；生产 delta 仍使用原双列表协议。

没有修改 `review_delta.py`、schema、校验器、保留资格、调用/token/时限上限，也没有新增必改 ID 字段。新测试 [test_round10_review_prompt.py](../../../tests/test_round10_review_prompt.py) 覆盖旧提示哈希、各路径唯一格式、完整证据规则保留、全替换/混合/全保留及错误分区仍拒绝。

定向验证：新提示测试、Round 9 delta / rule basis / failure audit、Round 7 compact review、Round 8 review context，共 **154 passed**；`git diff --check` 通过。该实施子任务没有进行真实供应商调用，后续有界诊断结果由主任务独立封存。
