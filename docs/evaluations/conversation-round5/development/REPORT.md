# Round5 已暴露回归：六轮均交付，严格质量仍为 2/6

本次保留第三轮原有的 Cedar / Harbor 两个合成场景、6 轮中文问题、8 份英文资料与 gold。它们在此前开发中已经暴露。运行者和主评分者参与了本轮实现，因此这是 **AI 开发侧回归，不是盲测、人工独立评测、生产流量或真实用户结果**。历史输入中的“holdout”标签是逐字保留的原文；本次作用由 [protocol](protocol.md) 和 [runner metadata](run/runner-metadata.json) 覆盖为 exposed development regression。

候选为 `6f79300c7e06f2ae57ba92915c911908cc024009` / `workspace-agent-v6`。前后均验证 [59 文件源码清单](code-freeze.json)、12 个冻结输入及原始输出 hash。每任务 75 秒调用边界预算、最多 6 次决策 + 1 次复核；BM25；最多 6 任务 / 42 模型调用；任务与 provider 均不重试。客户端观察上限仍为 200 秒。没有为成功扩预算、补写历史、改 gold 或根据这次结果修代码。

## 六轮的完整分母

[逐项评分](grading/grades.json)对每条 mandatory、实际检索原文和被引内容分别检查。[原始运行](run/run-summary.json)共提交 **6/6**，均有可查看的最终文本，0 失败、0 阻塞；五次为事实报告，一次为澄清。**按冻结 gold 严格通过 2/6**：Cedar t2 与 Harbor t3。服务 `SUCCEEDED` 只表示完成交付，不能当作答案合格。

| 轮次 | 最终交付 | 严格结果 | 主要依据 |
|---|---|---|---|
| Cedar t1 | 报告 | 未通过 | 有来源前提的约13.3%节电判断被数值规则拦截；未汇总Dune失败、Reed通过 |
| Cedar t2 | 报告 | 通过 | Dune全部通过被反驳；月运行费资料不足；用户更正及C-21范围保持 |
| Cedar t3 | 报告 | 未通过 | 没有先询问“它”指Dune还是Reed，直接给两候选报告；再次数值拦截 |
| Harbor t1 | 报告 | 未通过 | 遗漏Sable 395/400=98.75%及失败判断，也未明确两者整体不通过 |
| Harbor t2 | 报告 | 未通过 | 三类主要判断正确，但把“资料未提供最大恢复时间”改为“R-17未测量恢复时间” |
| Harbor t3 | 澄清 | 通过 | 明确询问R-17还是R-09，没有混用运行或擅自定稿 |

必须检索的四轮覆盖 **4/4**；两个澄清题的检索轴按冻结协议记不适用。Cedar t3 实际多做了一次检索，但不会因此加分。全部 5 次实际检索的 23 个片段实例（9 个不同片段）原文偏移与 SHA 检查通过，文件见 `grading/*-retrieval-integrity.json`。完整 gold、来源片段与 mandatory 对应在 grades.json 中；“找到资料”不能替代正确且完整的结论。

已输出事实的引用支撑与完整性分开评估：Harbor t1 已陈述的四条均有支撑，仍因遗漏而失败；Cedar t1/t3 的正面事实引用可支持，数值不必要拦截由错误拒答/完整性轴记录，不把真正的引文误判为伪造。用户范围尾注中回显的问题与限制不计作模型回答。Cedar t3 没有武断挑中一个候选，问题是漏掉明确澄清义务，不能夸大为已观察到错误选人。

## 本轮执行路径证据

五次事实报告均沿 `search_workspace → verify_and_submit_report → 单独最终复核` 完成；Harbor t3 直接 `request_clarification`。**没有 read_source_passages 调用**，也没有内部或用户侧补发。每次搜索后，下一决策的实际上下文记录均显示所有当前片段完整可见，`read_action_available=false`；模型仍可选择搜索、提交或澄清。不存在“服务器自动代答”的步骤。最终来源核验和独立于决策调用的模型复核仍执行。

这验证了可见性约束在本次真实链路上生效。与第四轮 Cedar 的第三次决策超时相比，本次只进行了两次决策，并成功进入复核。但两次运行不是控制供应商负载的配对实验，搜索措辞、模型输出和上游延迟也不同，**不能证明本改动单独导致交付改善或给出稳定提速百分比**。所有 context guard 均 fits=true；本次没有发生裁剪，裁剪后的引用闭合由工程测试覆盖。

[离线对照](../decision-payload-comparison.json)是另一项实验：重建的旧正文大小逐一对应第四轮实际记录；Cedar 旧第三次决策的反事实打包从24,317变为22,660 bytes（−6.814%）。首步增加362 bytes，约3.7%。新 schema 原本已不提供该冗余 read，因此该第三步不是本次真实模型轨迹，更不是耗时测量。

## 每个真实请求

[完整阶段审计](grading/runtime-audit.json)保留全部请求、阶段和上下文选择。客户端时间包含本地序列化、网络、上游等待及解析；不等于纯模型计算。HTTPX timeout 是网络阶段/不活动设置，75秒是继续发起调用的总边界，不是上游硬取消。嵌套 tool/review span 不可相加。body bytes 不是 tokens 或完整网络包大小。

| 任务 / 请求 | 阶段 | 客户端耗时 s | timeout s | 结束剩余预算 s | JSON正文 bytes | 已返回total tokens |
|---|---|---:|---:|---:|---:|---:|
| cedar-t1 / r1 | decision | 3.555 | 54.980 | 71.425 | 10,148 | 1,847 |
| cedar-t1 / r2 | decision | 34.877 | 51.413 | 36.536 | 20,130 | 5,615 |
| cedar-t1 / r3 | review | 25.948 | 36.531 | 10.583 | 18,662 | 5,319 |
| cedar-t2 / r1 | decision | 3.447 | 54.986 | 71.538 | 12,776 | 2,734 |
| cedar-t2 / r2 | decision | 14.360 | 51.531 | 57.171 | 22,722 | 6,271 |
| cedar-t2 / r3 | review | 12.074 | 57.169 | 45.095 | 20,309 | 5,633 |
| cedar-t3 / r1 | decision | 3.096 | 54.986 | 71.890 | 14,580 | 3,294 |
| cedar-t3 / r2 | decision | 23.695 | 51.886 | 48.191 | 24,502 | 7,103 |
| cedar-t3 / r3 | review | 18.220 | 48.185 | 29.965 | 22,854 | 6,663 |
| harbor-t1 / r1 | decision | 13.837 | 54.987 | 61.150 | 10,183 | 1,859 |
| harbor-t1 / r2 | decision | 23.510 | 41.141 | 37.631 | 19,118 | 5,284 |
| harbor-t1 / r3 | review | 34.661 | 37.627 | 2.965 | 18,064 | 4,959 |
| harbor-t2 / r1 | decision | 5.599 | 54.985 | 69.386 | 12,946 | 2,742 |
| harbor-t2 / r2 | decision | 27.834 | 49.378 | 41.544 | 21,879 | 5,953 |
| harbor-t2 / r3 | review | 24.760 | 41.540 | 16.780 | 20,017 | 5,478 |
| harbor-t3 / r1 | decision | 5.196 | 54.986 | 69.791 | 14,927 | 3,389 |

全部 **16 次**请求均返回明确 usage：prompt **69,535**，completion **4,608**，total **74,143**。本次未知用量请求为0，未用0替代缺失；全部 stage/request ID成对闭合。没有供应商账单核对，不能将token计数直接当作已支付费用。第四轮仅有两次任务、两次调用用量未知，不能把其已知部分token与本次六轮完整用量比较为成本增减。

Cedar t1 worker记录64.430秒；Harbor t1为72.044秒，仍接近75秒边界。这里只记录单次结果，不给出总体p95、并发吞吐、TTFT或生产容量结论。

## 已确认的质量缺口

[失败诊断](grading/failure-diagnostics.json)保存同一冻结任务的草稿、最终报告、实际guard finding和answer contract；未改原始结果。

1. **有来源的链式计算被拦截。** Cedar t1/t3 中240、208、公式及阈值在被引资料里，约13.3%可由前提推出。实际finding为 `quantity_not_anchored_in_selected_quotes`；[算术规则](../../../../lablens/answer_quality.py)只接受有限简单二元表达式，明确不支持链式表达式。这里确认的是规则覆盖不足及错误拒绝发布，不是资料缺失。Reed的12.5%同时出现在阈值原文中而通过，也不能因此证明数值与主体语义检查完备。
2. **复核没有补全草稿漏项。** Cedar/Harbor t1草稿已经缺少整体结论；Harbor还漏Sable成功率。固定命题复核要求保持原statement集合，因此本轮没有补全这些缺口。应在进入固定复核前增加覆盖检查，而不能把来源里的数字当答案已写完整。
3. **同轮重新引入两个对象时，澄清没有发生。** Cedar t3实际 `answer_contract` 是 `no_narrow_ambiguity_rule_matched`，模型选择报告。当前窄词法规则和模型行为未覆盖此提问；Harbor t3则正确澄清。这不是通用指代能力已通过。
4. **资料缺失被升级成历史未测量。** Harbor t2草稿原本说证据包“未提供”最大时间；最终复核改成“未测量断电后的恢复时间”。被引原文不蕴含后者，所以严格支撑失败。该复核也纠正了同任务草稿对成功率的错误supported标签，并纠正Harbor t1一条错误contradicted标签；应同时保留它纠正和引入错误的证据，不能笼统宣称复核无效或已可靠。

这四项留作后续可复现失败案例；本次冻结后没有修复再重跑。重复读取优化和界面精简已验证，但不代表回答质量验收完成。

## 证据完整性

- [源码清单](code-freeze.json) SHA256：`e89701c9dbffcfabb61b68e9637ea5a0fb261253026a172d9a66f09adc2619c8`。
- [输入清单](frozen-manifest.json) SHA256：`1768de901a655a7108bf4300a1153c2af7bd3a95a88791dbe89493c653d99787`；原gold SHA256：`a0949662947407a355d0e62513085e29e497053e147e88b644eb9cb5db5f9fe7`。
- [runner.py](runner.py) SHA256：`837c4d1eda00552b77c46f782b7aa5eae1cc3d25754d8a4a92c40cb4168016da`。
- [raw-output-manifest.json](raw-output-manifest.json) SHA256：`7b901aeb8cb7e47b74a26e3af7037b290620251ce301cdfbc05bb1df37c80552`；292个原始文件在评分前封存。
- [运行前检查](source-precheck.json)、[运行后检查](source-postcheck.json)、[运行日志](runner.log)及 [approval](approval.json)记录本次唯一运行的边界。
- [analyze_trace.py](analyze_trace.py)只离线重算计时和原文完整性；语义评分由AI逐条阅读，不以该脚本的机械通过替代。
- [grading-manifest.json](grading-manifest.json)记录本报告和评分文件的hash；独立复核如有异议单独保留，不修改原始结果。
