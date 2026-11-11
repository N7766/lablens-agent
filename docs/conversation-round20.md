# 第二十轮：配置与历史命题错位的离线修复

2026-11-11。本轮针对 [Issue #67](https://github.com/N7766/lablens-agent/issues/67) 修复报告发布边界，并加强固定题验收器。**原始错误不再因机械检查通过而直接发布；既有的一次 review 可以修正单个配置命题，但修好这一行不等于回答了历史运行问题。** 本轮没有真实模型、付费 API 或 GPU 调用，不能据此宣布私有模型语义验收通过。整体仍为 **7/12 轮完整交付、第 8 轮部分完成**；[PR #66](https://github.com/N7766/lablens-agent/pull/66) 保持草稿，Issue #67 保持开放。

## 原问题与没有改动的历史证据

[Round 19c](conversation-round19c-runtime.md) 的真实 Agent 使用三次模型请求完成检索、核验、复核和持久化，17 项机械检查通过；独立 AI 复核的 S5 则失败。最终缓存行如下：

| 字段 | 原始输出 | 问题 |
| --- | --- | --- |
| statement | 配置启用了缓存。 | 判断对象是资料中的配置事实。 |
| basis | current_configuration | 与配置命题一致。 |
| verdict | insufficient | 资料已经写明 `cache_enabled: true`，却拒绝了这个配置命题。 |
| explanation | 配置文件中 cache_enabled 为 true，但没有提供对应的历史运行日志来证明缓存实际启用了。 | 缺失的是历史执行证据，不是已经引用的配置值。 |

这是**命题与判断标签作用范围错位**。不能把它改称“模型断言历史运行一定启用了缓存”，也不能因解释承认历史未知，就把整个结论判为正确。原始[任务结果](evaluations/conversation-round19c-runtime/report/run/task-final.json)、[输出前语义标准](evaluations/conversation-round19c-runtime/independent-semantic-rubric.md)及[S5 失败复核](evaluations/conversation-round19c-runtime/public-ready/independent-agent-review.md)均保留，本轮不重写历史分数。

## 修复如何进入实际报告链路

新增的 [proposition_scope.py](../lablens/proposition_scope.py) 使用有限的中英文配置/历史表达，审计 `statement`、`basis` 与拒答理由是否明显错位。它只产生可检查的范围诊断，不根据关键词生成事实，不自动修改命题，也不把未识别的语言判为正确。

1. **草稿合同保留可修复机会。** `make_review_contract` 将范围发现加入草稿检查，因此 v3/v4 的 retain 不能保留已经识别的坏行。单个配置命题的错误标签仍可由既有的一次 review 用 replace 修正，不增加模型调用、不自动重试。
2. **不可拆的混合命题提前终止。** 如果 statement 同时声称配置值和历史执行，固定 statement 的 Agent review 无权拆成两个命题。该路径在 review 前明确终止；普通、没有固定 statement 合同的 full review 仍可按原有能力重写并拆分草稿，不能因为共享检查而一并封死。
3. **审查恢复后的原始 review。** 服务端先恢复模型决策，再检查命题范围。如果原始 review 仍错位，不让后续通用拒答文案覆盖问题后再通过。
4. **再次审查最终渲染行。** 精确引用验证器可能改写 explanation/verdict 或加入配置行，因此最终将用于报告的 assessments 再检查一次。仅检查模型 JSON 不足以覆盖这些后置变化。
5. **错误作为报告校验失败结束。** Agent 返回外层 `agent_protocol_error`、具体状态 `report_scope_error`，说明未发布报告且没有自动重试。它不把校验失败写成“工作区缺少资料”，也不把坏结论放入正常 answer/claims。

失败诊断保留触发阶段、原草稿、规范化 review、恢复后的行；若在最终渲染阶段失败，还保留最终 assessments 与验证器调整记录。工具轨迹及已发生调用的 usage 继续可见。这里保存的是规范化诊断，不宣称补齐历史运行未留存的完整 HTTP 原文。[RAG 复核链路](../lablens/rag.py)、[Agent 终止路径](../lablens/workspace_agent.py)、[范围回归测试](../tests/test_round20_proposition_scope.py)

配置命题本身缺资料或存在冲突时，`current_configuration + insufficient` 仍可能合理；配置行带“历史行为未知”的限定也不应仅因出现“历史”而被拒绝。`runtime configuration / 运行时配置` 作为配置名词处理。有限规则未识别的表达保留 `unclassified`，`semantic_correctness_verified` 和 `question_coverage_verified` 均为 false。

## 固定题验收器区分缓存配置与历史命题

[verify_private_agent.py](../scripts/verify_private_agent.py) 升为 `private-agent-acceptance-v2`。旧 `historical_cache_insufficient` 只检查是否有一行 statement 含“缓存”且 verdict 为 insufficient，既没有检查该命题是否指历史，也没有读取解释或 basis，因此错误地接受了“配置启用了缓存”这一行。新检查按 statement 的保守完整表达分类，分别要求基线、可选的配置事实、历史运行命题对应自己的 basis/verdict，未知改写和混合命题不自动通过。

验收器独立于生产范围检查，不直接调用生产规则给自身评分。它还绑定固定资料快照、文档 ID、精确引文、来源偏移和内容哈希，拒绝错误来源或其他资料中的同词同数。版本和源码哈希参与冻结计划核对，旧计划不能直接在新验收器上继续执行；需要重新准备并冻结候选。

这里的“独立”指实现与生产 guard 分开，不指人工盲测。它仍是**一道已暴露合成题的保守机械验收**，不能解析任意自然语言或证明来源真实。[验收器回归](../tests/test_private_agent_acceptance.py)专门保留旧输出误过的负例。

## 配置键误拦与精确引用边界

另一个已知问题来自兼容探针：原始解释正确区分配置与历史，但引入 `cache_enabled` 时命中了 `insufficient_review_introduced_named_subject`。本轮检查原始响应后进一步确认：该 a2 的 **`evidence_ids=[]`**，草稿也没有选择配置引文。

修复后的 `selected-evidence-checks-v5` 只为**本行选取的精确引用、绑定到唯一 hit 的 scalar snake_case 配置键**增加有限豁免。任意检索资料出现该词、其他行选择了该词、仅有 citation ID、错误 chunk、伪造引文、重复 chunk ID，均不能提供新豁免；把 `other_cache_enabled` 的后缀截成 `cache_enabled` 也不能冒充完整来源标识符。选择包含 `Node-east` 的真实资料，不等于可以自行引入新的拒答主体。[规则实现](../lablens/answer_quality.py)、[精确引用回归](../tests/test_answer_quality.py)

因此，**原始空引用回放仍不通过这项来源约束**，不能声称原样回放已经成功。另一个控制组只在内存中为同一解释补选现有配置 passage，证明该 guard 不再误拦有精确引用的配置键；没有修改封存证据或在服务端自动寻找引文补给模型。后置的既有“仅配置不能证明历史运行”验证器仍会改写该控制组的 explanation，最终历史 verdict 保持 insufficient。该行为同样有测试记录，不能把此 guard 通过写成“最终原文完全未改”。

这是词法来源豁免，不是值、主体或完整语义认证。既有来源名语法豁免仍是有限启发式，不构成通用实体识别或来源可信证明。新提示要求 insufficient 解释若陈述具体配置事实，也选择该事实对应的 passage。

## 三组离线回放与各自含义

[replay_round20_scope.py](../scripts/replay_round20_scope.py) 读取封存的真实任务、原始规范化草稿与解析后的 review，回放中替换模型传输并封锁 socket、HTTP 和本地服务访问。输出以新文件方式创建，核对封存输入和运行源码未变化，不覆盖旧结果。三组都只有一次**脚本化 review 调用**，不是新模型样本：

| 回放 | 输入变化 | 要核对的边界 |
| --- | --- | --- |
| original_review | 不改原草稿和解析后的原 review | 旧错位在 `review_result` 阶段被拒绝，报告不发布。 |
| configuration_only_repair | 控制响应仅将 a2 verdict 改为 supported | 原配置命题可正常发布；仍缺独立历史命题，因此固定题完整验收必须失败。 |
| explicit_split_propositions | 控制草稿分别给出基线、配置与历史行，引用均来自原冻结 passage | 配置显示“证据支持”，历史显示“证据不足”，可通过该已知题的保守机械检查。 |

正式封存结果为 **3 组回放、11/11 项回归期望通过**，外部模型调用 0、网络尝试 0，封存输入和源码一致性检查通过。[离线回放结果](evaluations/conversation-round20/offline-replay.json)包含各组完整诊断、最终显示、固定题检查及源码哈希。

这组对照用于证明修复后的拒绝/修正路径，不能修复历史模型输出，也不能证明模型下一次会自行生成正确拆分。报告中的 `generation_review.calls=1` 在这些回放里表示 stub 调用计数，实际外部模型调用数为 0。

另外，[独立 AI 反例设计](evaluations/conversation-round20/independent-case-design.md)给出了配置 true/false、缺日志、匹配与错误 run、同 run 冲突、配置冲突、缺配置、条件示例和版本变化等十二项语义期望。设计者未编写生产修复，但设计在开发时公开；这不是人工独立出题、未见题盲测或十二个真实模型样本。矩阵列出语义期望，不代表当前有限规则已经自动识别全部情形。

## 开发过程中的失败与修正

第一轮全量回归曾出现 **8 项失败、2,547 项通过**，不是一次直接全绿的修改。失败分为三类：

| 首次失败 | 原因 | 修正方式 |
| --- | --- | --- |
| 5 项 malformed response 测试 | 新范围检查过早索引不可信 JSON，漏出 `KeyError: assessments` | 检查前验证有界行列表与文本字段，将错误返回为受控模型/引用失败；保留已有用量与失败轨迹。 |
| 1 项 no-draft 合同测试 | 缺少草稿时先做范围访问，漏出 `NoneType` 错误 | 将既有 scoped-review 合同验证放在新范围检查之前，保留原有的请求前拒绝契约。 |
| 2 项冻结 prompt 测试 | 新提示被加到共享系统提示，改变了普通生成和无合同 review 的冻结字节 | 将新增提示限制到需要它的 Agent/限定审阅路径，恢复普通路径的既有冻结系统字节；没有修改旧 prompt 期望来消除失败。 |

还确认了一处容易过度拒绝的条件：同一条既说“当前配置快照没有提供”，又说“历史日志未提供”，属于两类资料都缺失，不能仅凭后半句判为拒错命题。检查为真实配置缺口保留通路。[独立 AI 代码审查](evaluations/conversation-round20/independent-code-review.md)记录了开发期提出并修复的五项问题：配置名词误判、合法缺配置误拦、不可信 JSON 异常泄漏、后置失败缺少最终行诊断，以及普通 full-review 拆分能力被过度限制。这是开发可见的代码审查，不是新模型评测。

| 最终验证 | 结果 | 口径 |
| --- | --- | --- |
| 本地全量 Python 测试 | **2,567 通过、0 失败、0 跳过；82.81 秒** | 1 项既有 Starlette/httpx 弃用警告。 |
| 范围专项 | **35 通过** | 全量测试的子集，不相加。 |
| 原 Round 19c 公开证据 | **68 份文件一致** | 没有改写原始模型结果或历史失败判定。 |

[工程验证回执](evaluations/conversation-round20/engineering-validation.json)保留最终日志/JUnit 哈希、变更源码哈希及首次八项失败记录。本轮数据不借用旧运行的 2,485 项成绩，也不计为模型准确率。

## 完成范围与下一步

本轮交付的是发布边界、诊断保留、固定题验收器和离线反例；**实际模型调用 0、付费 API 调用 0、GPU 租用/调用 0**。没有用新的成功样本覆盖 Round 19c S5 失败，也没有据此升级主服务、完成私有模型质量验收或宣称抗住生产流量。

下一步应在这些离线修复和候选源码重新冻结后，分别验证模型能否生成正确的独立命题、缺失与冲突证据是否正确处理，以及最终报告是否覆盖当前问题。新的真实模型评测需要单独的明确运行边界，不能复用旧计划或把控制响应当模型成功。短期简历可写“根据真实 GPU Agent 运行暴露的问题，完成报告范围一致性检查、保留原始失败和诊断，并增加独立实现的固定题验收与离线回放”；暂不能写“答案质量验收通过”。

进度保持 [十二轮收尾计划](finalization-12-rounds.md)中的 **7/12，第 8 轮部分完成**。GitHub 最终提交、CI 和桌面交付校验由本轮收尾记录补充；本地回归通过不替代这些交付检查。
