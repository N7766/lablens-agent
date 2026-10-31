# Conversation round 5 — frozen new synthetic scenarios

这次小型新题评测计划 **2 场景 × 2 轮，共 4 轮**，结果为 **2 轮完成、1 轮 review 超时失败、1 轮依赖阻塞**。按预先冻结的六维全满分标准，严格通过 **0/4**；只看有回答的两轮也是 **0/2**。完成回答中的历史重试/因果不确定性主结论正确，但仍存在完整性遗漏和范围/错误拒答问题。

题目、英文合成材料、完整 gold、评分规则和 runner 均由同一 AI 作者预先冻结；该 AI 在原始记录封存后完成语义评分。作者出题时只读公开 API README，未读生产实现、旧题或旧评测结果，未用外部模型出题。后来仅计算候选源文件哈希以核对身份，未展示或分析实现内容。**这不是人工独立评测、独立模型评测或一般准确率估计。** 当前题目随结果公开，后续再使用即属于已暴露题。

## 冻结与执行边界

- 候选：`6f79300c7e06f2ae57ba92915c911908cc024009`。
- [候选清单](candidate-freeze.json) SHA-256：`e89701c9dbffcfabb61b68e9637ea5a0fb261253026a172d9a66f09adc2619c8`。
- [题目/协议/脚本冻结清单](freeze-manifest.json) SHA-256：`fcae7978594b1a2b0c5823c308fdcef41e4b697bc20c6d8e5fe1e44301d1d237`。
- [原始记录 seal](runs/round5-new-once/raw-seal.json) SHA-256：`029bc8a0100a12bb35e7adf936c65ecd968b74c05a505509188f9486d18af06f`。
- QA：`http://127.0.0.1:8083`。运行使用 BM25 / Agent / top_k 5，串行执行，不重试 POST，不重试模型，不重跑失败任务。
- 预算：每任务最多 6 次决策 + 1 次 review，4 任务最多 28 次调用；服务 75 秒调用边界预算，客户端每任务最多观察 200 秒。前序无有效持久化答复即阻塞依赖轮。
- 运行前后 59 个候选源文件哈希一致；273 个 raw 记录文件与 7 个冻结文件均通过校验。先 seal，再语义读取；[校验记录](post-run-integrity.json)保留时间和数量。

冻结的[数据集](frozen/dataset.json)、[完整 gold](frozen/gold.json)、[六维评分规则](frozen/protocol.md)和[运行脚本](frozen/runner.py)均未根据结果修改。每个判断可通过 [annotations.json](assessment/annotations.json) 中的 JSON Pointer 找到原始回答、引用或检索记录；[evidence-index.json](assessment/evidence-index.json)提供便于审阅的派生摘录，原始任务仍是最终证据。

## 每轮结果

维度取 0/1/2；依次为检索覆盖、结论正确性、完整性、引文蕴含、无错误拒答、指代/范围。失败与阻塞不编造语义得分，也不从四轮主分母移除。

| 轮次 | 执行 | Provider 调用 | 六维得分 | 严格通过 |
| --- | --- | ---: | --- | --- |
| ar-t1 | review 阶段 `agent_timeout` | 3 | 不可评 | 否 |
| ar-t2 | 前序无有效回答，未提交 | 0（未发任务） | 不可评 | 否 |
| kl-t1 | SUCCEEDED | 3 | 2 / 2 / 1 / 2 / 2 / 2 | 否 |
| kl-t2 | SUCCEEDED | 3 | 2 / 1 / 1 / 1 / 1 / 1 | 否 |

[评分汇总](assessment/scores.json)保留四轮结果。各维满分数分别是 **2/4、1/4、0/4、1/4、1/4、1/4**；在两轮可评分答复内分别是 **2/2、1/2、0/2、1/2、1/2、1/2**。

**ar-t1 / ar-t2：** [首轮任务](runs/round5-new-once/ar-t1-final-task.json)的 trace 已取回全部三个必需来源侧面，另含旧版本来源；这只证明检索覆盖。第三次 provider 请求是 review，约在 48.274 秒启动、约 75.804 秒以 timeout 结束，未产生持久化最终回答，worker_ms 为 75,822。没有把待 review 的草稿算作成功答复。依赖轮按冻结规则阻塞，未生成替代前序。

**kl-t1：** [原始回答](runs/round5-new-once/kl-t1-final-task.json)正确给出当前最多 2 次重试、3 次总尝试；正确指出成功回执和无图例的 badge 都不能证明历史运行重试过。引用分别支持这些已说出的结论。完整性不足在于：未说明当前配置仅对传输超时重试；未明确说明配置快照晚于历史运行且不是其运行时快照；对最小充分重试证据只列出缺失日志/计数类别，未清楚提出权威计数或关联尝试记录。相关限制均已被检索到。

**kl-t2：** [原始回答](runs/round5-new-once/kl-t2-final-task.json)正确把“那次运行”指向 R-731，明确把分片解释归于用户，且没有据此断言重试为零或至少一次；也正确保留上游限流归因的不确定性。它没有明确补回“最终 SUCCEEDED、尚未建立失败尝试”的边界，并把上一轮的当前配置问题重新带入。这一额外项因本轮没有取回配置文档而声称配置无法确认，但不变的工作区仍含该文档，上轮也已引用其值。评分将它记作范围/部分错误拒答与缺少引文支持的歧义，未将其夸大为“历史重试主结论错误”。

引文评分逐项检查“文段是否支持所附结论、版本/时间范围是否匹配”。原文存在、quote 校验通过、`evidence_verdict=supported` 和同模型 review 成功都没有自动被计为语义通过。

## 用量和时间证据

[调用审计](assessment/call-audit.json)将每个 `request_started` 与 `request_finished` 按 task/request_id 配对：**9/28 次 provider 调用尝试，含 6 次决策和 3 次 review，embedding 请求为 0**。三个提交任务各 3 次，未触及每任务 7 次上限。

调用次数完整已知，**token 用量不完整**：ar-t1 的 review 超时且没有响应，其 provider 是否接受和 token 用量未知。已知响应的 token 合计只是下界 **26,462**（prompt 24,362；completion 2,100）。任务保存的未知 token 仍为 null；没有改写为零，也没有声称知道最终费用。75 秒是调用边界预算；该次 trace 约 75.804 秒结束，不构成远端硬取消或严格墙钟截止保证。

## 离线复核

在仓库根目录运行以下命令，仅校验冻结/原始证据哈希并从既有语义标注重新计算分数，不发送请求：

```sh
python3 docs/evaluations/conversation-round5/new-scenarios/frozen/score.py \
  --run docs/evaluations/conversation-round5/new-scenarios/runs/round5-new-once \
  --annotations docs/evaluations/conversation-round5/new-scenarios/assessment/annotations.json \
  --output /tmp/round5-new-scenarios-rescored.json
```

这复现的是聚合规则，不会把 AI 语义标注变成人工独立判断。独立审阅应逐条对照 frozen gold、原始任务的回答/assessments/citations、检索 trace 和标注中的指针。再次执行 provider runner 属于新的运行，须另行授权和单独保留记录，不能覆盖本次一次性结果。
