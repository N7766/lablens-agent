# Round 7：跨轮变更与保留的显式答题槽

## 原失败与定位

这是对已暴露失败的开发侧修复，不能称为独立评测。

- [Round 6 封存 ar-t2 结果](../conversation-round6/runs/round6-once/ar-t2-final-task.json)：用户明确要求修正上一轮，并回答“总尝试次数、重试次数和 4xx 处理”中哪些变更、哪些保留。`result.agent.trace` 的 `verify_and_submit_report` 草稿只有四个命题：新集群尝试上限、新集群重试次数、4xx 不重试、培训材料错误。最终答案保留了新值，没明确比较前后。
- [原 gold](../conversation-round5/new-scenarios/frozen/gold.json) 的 `turns.ar-t2.required_conclusions` 中 `ar2-c3` 要求比较三个方面；允许新旧/保留列表，不强制逐字 from-to。此前答案若有错，应核对后纠正，而非机械重述错误旧值。gold 未修改。
- 固定 statement 的 review 可以修正每条的解释和引文，却不能添加不存在的比较项目；Round 6 的覆盖器仅处理双对象总验收结论，因此原草稿没有比较待答槽。

## 实现及取舍

[answer_coverage.py](../../../lablens/answer_coverage.py) 的 `prepare_review_draft(question, draft)` 保持原接口，新增有限列表解析 `comparison_requirements(question)`：当前问题必须明确指向上一轮/之前答案，同时要求哪些变更、哪些保留，并有一个可解析的 2–6 项列表。支持有限中文“说明 A、B 和 C 中哪些……”和英文“for A, B, and C, state which……”结构。项目仅来自当前问题，不从文档、保存的 focus 或旧 assistant 输出推断需求。

每个未找到比较结构的项目增加一个中性问题槽：“项目：与上一轮相比的变更或保留结论及依据”。初始 `insufficient` 是等待复核的占位值，不是已经判定资料缺失。槽内无数值、引文、引用 ID；解释明确旧回答不是证据，资料不足/矛盾不能猜旧值。原 statement、verdict、quotes 全部保留，交给同一次现有 review 核验。

已有项目连同明确的变更/保留谓词或“无法判断是否变更”时，可保留该待核验命题而不重复添加。该词法检查不证明比较正确、证据蕴含或覆盖完整：即使“从 9999 改为 1234”的错误命题也保留给 review，`semantic_coverage_verified` 始终为 `false`。没有支持所有同义改写；不支持的列表由模型处理。

保留 8 行总上限、Round 6 Harbor 整体谓词修复和满容量明确终止行为。缺少比较项且补槽超过 8 行时抛 `CoverageContractError`，不删除原指标，不额外调用模型。新增 `missing_review_slots` 供新消费者记录通用缺项；旧 `missing_overall_slots` 暂保留为兼容别名。

## 实际过程中的难题

1. **遗漏新旧比较，不等于新值错误。** 原 ar-t2 的新值本身有资料支持，但用户要求的是逐项变更/保留。修复不改 gold、不把原失败算通过，也不把“槽存在”计为语义正确。
2. **重复补槽可能再次触发容量误拒绝。** Round 6 Harbor 曾因此失败。本次保留已有比较行、满 8 行已有比较的正例，以及真满容量缺比较的失败例；已有 Harbor 封存稿也随原覆盖测试回归。
3. **初稿错误借用了邻项谓词。** 初轮相关 85 项通过后，自审新增 `超时上限为 20 秒且并发数由 25 改为 50。` 反例：初稿把超时上限也判为已有比较，实际 `added_review_slots=[]`，预期缺超时比较。这是实际失败，不是模型输出。改为只看当前项目之后、下一个明确列项之前的局部谓词。
4. **初稿把问题当成已回答。** 同次新增 `超时上限需要变更吗？` 反例也失败。原分句去掉问号，又没识别“吗”，把它当变更命题。改为保留句末标记并识别问号/吗/是否等；中性服务器槽另以精确 statement 保证幂等，不宣称它已经得到答案。
5. **独立复审发现标题回退仍会跨指标借用。** 首次局部谓词修复只限制了正文，标题/解释的回退路径仍接受长 statement。复现：statement 为“超时上限为20秒，并发数由25改为50。”，explanation 为“从25改为50。”，初稿错误返回零新增槽。补成要求 statement 与 explanation 都不出现其他请求列项，才能使用标题回退；原失败先加入测试复现，再修复通过。
6. **固定 review 的篇幅代价仍存在。** 原四条 ar-t2 草稿增加三条后为七条，更多行会占用 review 时间及输出预算。没有提高总时间、输出上限或调用次数；真实是否改善需在新冻结候选上观察最终答案，不能从这里的离线测试外推。

## 离线验证

执行：

```text
.venv/bin/python -m pytest -q tests/test_answer_coverage.py tests/test_round7_comparison_coverage.py tests/test_coverage_capacity_stop.py
```

最终 **88 passed**，1 条已有 Starlette/httpx deprecation warning。其中新文件 29 项；另 59 项原覆盖/终止回归。外部模型请求 **0**。

- 原 ar-t2 草稿经原 evidence ID 解析后 **4 → 7 行，3 个比较槽**；原四条内容逐字保留，封存文件字节不变。
- 不要求比较、没有列项/前轮锚点、单项、重复项、过长列表、多个列表、显式取消比较，不擅自生成比较需求。
- 只有新值、不同行/同句邻项谓词、纯问句，不能替代目标项目比较。
- 已有比较、作为标题的项目与自身比较解释、证据不足/矛盾行，保留原内容供 review 核验。
- 两个初稿失败及一个独立复审失败例修复后通过；满容量确实缺项则明确失败，恰好八行则维持固定 statement schema，删掉比较槽仍被既有 scope contract 拒绝。

这里只是开发侧结构、边界与接线验证。新旧适用范围、新旧数值、保留结论、引用是否真正支持，仍须由最终答案的语义评估分别判断。
