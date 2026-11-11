# Round 20 候选修复的独立 AI 代码审查

日期：2026-11-11。审查者为没有编写本轮生产修复代码的 AI 子代理；实现者在开发期间收到以下发现、反例及修复建议。**这是开发可见的 AI 代码审查，不是独立人工盲测，也不是新模型质量评测。**

审查基线为 `af9d7daad6843ff05995d8e68e1fa547b56ba8cc` 之后的本地候选工作树；具体被复核内容以下表的文件 SHA-256 为准。本审查只新增反例设计和本报告，没有修改生产代码，也没有进行外部模型调用、联网检索、GPU 租用或云资源操作。测试使用受控响应和本地资料。

## 审查范围与证据

阅读了 `proposition_scope.py`、`answer_quality.py` 的合同及命名主体检查、`rag.py` 的评审和渲染路径、`workspace_agent.py` 的终止分支，以及对应测试。审查同时参照 [原始 Round 19c 任务](../conversation-round19c-runtime/report/run/task-final.json) 与 [十二项反例设计](independent-case-design.md)。

| 被复核文件 | SHA-256 |
| --- | --- |
| `lablens/proposition_scope.py` | `58ca81eb59ca0f6ac18cc1583cbffde85fa9ee5fe7d5ef7061af1d2ecb34bff8` |
| `lablens/answer_quality.py` | `ad4a674180bb4e59ae0a126cab2fdb312be4c5cf33714569962cf57847deee4a` |
| `lablens/rag.py` | `aab2474d1ab10fc25634abb0943c0e4a39bcfbe4caab7f4a76ede062d25199d7` |
| `lablens/workspace_agent.py` | `b8d552d7ae05ce0fd75afae4de3630e3a9bbc94f1bef8a496dba6477c0772775` |
| `tests/test_round20_proposition_scope.py` | `95b22294a9c7aaa8283650c87275e4f70e719ad90ecac4a228aa35f1b8c42f4d` |
| `tests/test_answer_quality.py` | `8c1ac106cb0c5017191d157bcc2824720aa03b88190c8138eb91e66c79a90e63` |

文件哈希限定本次复核的版本，并不表示运行时已加载这些文件或已将修复部署到真实模型服务。

## 审查中发现及随后修复的问题

| ID | 初始候选中的具体问题 | 最终候选中的处理与复核 |
| --- | --- | --- |
| R1 | `The runtime configuration sets cache_enabled=true.` / “运行时配置记录 cache_enabled=true。”被仅因 `runtime / 运行时` 判为历史执行，导致合法 `current_configuration` 行遭拒绝。 | 在判断前排除这些明确的配置名词短语；两个原始复现输入本地纯函数复核均没有 finding。该有限消歧不是通用自然语言分类器。 |
| R2 | 合法解释“当前配置快照没有提供，历史运行日志也没有提供。”被误判为仅因缺日志而拒答配置。 | 配置资料缺口识别补充“配置快照没有提供”形式；原始反例本地复核没有 finding。缺当前配置可以合法保留 `insufficient`。 |
| R3 | 普通 RAG 的未验证模型 JSON 先进入 scope 检查；`{}`、`assessments=None` 或缺 basis 的行泄漏 `KeyError/TypeError`，绕过受控 RAG 错误出口。 | 检查所消费的草稿结构，并转换为 `RAGError(status=invalid_citations)`，不发送后续 review；上述三种原始复现得到受控失败。固定命题合同仍先执行原有合同校验。 |
| R4 | 后置渲染检查拒绝时，仅记录渲染前的 `resolved_review`，没有保留真正触发拒绝的最终行，无法查清 validator 改写后的 verdict/explanation。 | `rendered_report` 失败诊断增加 `rendered_assessments` 与 `rendered_adjustments`，并保留原草稿、模型决策与恢复后的评审。定向测试确认渲染前 supported 行和渲染后被拒解释均保留。 |
| R5 | 无固定命题合同的普通 full-review 路径，也因混合草稿被提前拒绝；但该已有评审本来允许拆分 statement。 | 只有 scoped contract 的不可分混合命题提前拒绝；普通路径仅预检形状，保留原有一次 review 的拆分机会，评审结果和最终渲染仍执行 scope 检查。新增受控测试确认一次 review 可以返回独立配置/历史两行。 |

以上五项是开发中实际指出并修复的问题。记录它们是为了保留过程证据，不应将它们继续列为该哈希版本的未修复缺陷。

## 最后一次定向验证

最后复核新修改，执行：

```sh
.venv/bin/pytest -q \
  tests/test_round20_proposition_scope.py::test_unscoped_review_keeps_its_existing_ability_to_split_a_mixed_draft \
  tests/test_round20_proposition_scope.py::test_post_validation_audit_covers_legacy_scalar_rows_without_coverage_claim \
  tests/test_round20_proposition_scope.py::test_post_validation_rendered_rows_are_checked_again
```

结果：**3 passed，1 warning，0.05 秒**。warning 为现有 FastAPI/Starlette TestClient 使用 httpx 的弃用提示。该运行只验证受控协议和本地处理，不是三道真实模型问答。

另外，本地纯函数复核了 R1 的两个表达、R2 的合法缺配置解释，以及 R3 的三种 malformed draft；均符合上述修复结果。`git diff --check` 通过。整体回归、CI 和其他工作流结果由主执行者另行记录，本报告不以这三个测试替代它们。

## 保留的能力边界

- 原始 GPU 输出仍是语义失败。离线拦截它不等于原模型已会生成正确答案，也不构成新一轮 GPU 验收通过。
- 检查基于有限的显式词法范围，不认证来源真伪、命题极性、run 身份或语义正确性。未识别表达保留 unverified；本审查不宣称不存在其他误拦、绕过或未发现问题。
- 配置行经已有一次 review 修好，仍不证明历史问题已被覆盖。新增审计继续将 `question_coverage_verified` 和 `semantic_correctness_verified` 保持为 false。
- 既有 `validate_assessments()` 仍可能新增字面配置行；本轮测试确认这些最终行也纳入后置审计，没有把原评审固定 statement 合同扩大解释为整个最终报告绝不增行。
- 原始与后置检查在不同阶段都执行，避免通用拒答文案覆盖原本的范围错误。已识别的失败明确终止发布，不被重新表述为工作区缺资料；不增加自动重试。
- 十二项矩阵包含错 run、矛盾观测、不同版本及示例配置等语义要求。它们是后续评测设计，不代表本轮有限 scope 检查已自动覆盖全部要求。

本次复核在上述范围内未再报告新的具体问题；这不是对整个系统、所有语言表达或未来修改的无缺陷保证。
