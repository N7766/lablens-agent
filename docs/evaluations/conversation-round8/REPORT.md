# Round 8 单次模型回归结果

**质量门未通过。** 候选 `141a9186dbd187a4621d78169f212c67c78eeb87`，`workspace-agent-v9`。本轮完成有限引用绑定与跨范围比较改动，封存后未继续修改源码或追加推理。PR #48 保持草稿，主服务不升级。

## 运行条件与身份

沿用原 10 轮、16 份合成资料与两套原 gold，题目已被开发者阅读。本轮由开发团队 AI 分组评分，再由另一 AI 先独立阅读、后交叉核查；不是盲测、人工独立评测或泛化测试。

DeepInfra `Qwen/Qwen3-235B-A22B-Instruct-2507`，BM25 top5，单任务75秒、6 decision + 1 review、输出1800/2600，隔离工作区只采集一次，无重试/重提/embedding。

- [候选清单](candidate-freeze.json)：68 个源码/配置文件。
- [输入冻结](input-freeze.json)：46 文件，SHA `94d640750e66a8e9d5332da3da64f774b4828834ba2197bc4fafe60578037822`，保留 Round7 原始失败、评分和后续修复的版本区分。
- [原始目录](runs/round8-once/)：583 个封存文件（seal 另计），raw seal SHA `39645ea4e213c7da0c4b43ff42b09157558638413d26a45c33813483064a86f2`。前后源码/输入身份保持，采集完整，fatal=null。
- [标注](assessment/annotations.json)、[分数](assessment/scores.json)、[独立阅读](assessment/independent-review.md)、[交叉核查](assessment/annotation-cross-check.json)、[评分文件清单](assessment/grading-manifest.json)。

## 交付、质量、用量各自报告

| 原题组 | 计划 | 提交 | 交付 | 失败 / 阻断 | 原 gold 严格通过 |
| --- | ---: | ---: | --- | --- | --- |
| Cedar / Harbor | 6 | 4 | 2 报告 + 1 必要澄清 | 1 / 2 | 1/6；已评分1/3，仅cedar-t3 |
| Alder / Kite | 4 | 4 | 4 报告 | 0 / 0 | 0/4 |

两组 rubric 不同，不合并为语义准确率。6 份报告并非6份合格答案，必要澄清也不是事实报告。失败和依赖阻断不从计划分母删除。`kl-t2` 内部标为 refused，正文实际给出不足结论，因此记报告，同时评其错误拒答与范围问题。

22次尝试 = 15 decision + 7 review。21次返回已知用量，已知 token 下限 **100,686 = 93,645 prompt + 7,041 completion**；Harbor review 未返回，provider 接受状态与 token 未知。无任务缓存复用、自动重试或补采。未知不当零，下限不当完整账单。[调用审计](assessment/call-audit.json)与[独立重算](assessment/independent-call-audit.json)。

| 轮次 | 确认的结果与缺口 |
| --- | --- |
| cedar-t1 | 八行报告已交付、原coverage溢出未出现；计算数值正确，但整体结论被旧数量/因果guard误降级，温度判断仅引测量而未引门槛。 |
| cedar-t2 | 两个核心结论正确：全部通过被反驳、月运行费不足。原gold要求的“节电虽通过”细节未明说，严格不通过；独立阅读对此门槛有分歧，保留为gold_specific_detail。 |
| cedar-t3 | 先澄清Dune/Reed范围，没有代选签字对象，严格通过。 |
| harbor-t1 | 第二次decision耗时39.189秒；review开始约余30.752秒，最终超时，无报告、用量未知。 |
| harbor-t2/t3 | 前轮失败，均依赖阻断、未提交。 |
| ar-t1 | 2总/1重试、4xx与培训冲突方向正确；默认值与部署覆盖关系、上限措辞及逐行定义引用不完整。 |
| ar-t2 | 三项比较已明确2→3、1→2、4xx保持；原错误拒答消失。原gold适用范围说明、上限措辞和逐行引用仍未满分，保留内部评分边界。 |
| kl-t1 | 当前2重试/3尝试、历史未知正确；原gold的transport范围、配置时间与运行快照、最小证据解释未完整。 |
| kl-t2 | 最小证据已具体，历史重试与限流归因保留未知；额外配置不足行仍跨范围解释，未明确最终成功及失败前提尚未成立。 |

[旧六轮详评](assessment/old-review.md)和[原四轮详评](assessment/new-review.md)记录原标准、逐项判定与来源；不把缺一项细节说成核心结论全部错误。新绑定模块最终 findings 为空，不能据此声称已认证语义；Kite错误supported配置由同模型review降为不足，末端规则没有在该最终结果上触发，不能错误归功给规则拦截。

## 对比、原因与限制

| 轮次 | 旧六轮交付 / 严格 | 原四轮交付 / 严格 |
| --- | --- | --- |
| Round 6 | 0/6 / 0/6 | 4/4（3报告1错误澄清） / 1/4 |
| Round 7 | 3/6（2报告1必要澄清） / 2/6 | 4/4（均报告） / 0/4 |
| Round 8 | 3/6（2报告1必要澄清） / 1/6 | 4/4（均报告） / 0/4 |

ar-t2 比较是可定位的局部改善，但总体交付未增加、原严格结果未改善；不能宣称整体质量或延迟提升。八个离线输入副本对照进一步分离Cedar的数值与basis触发条件，属于机械排查，不是修复后的新模型结果。[排查与对照](assessment/failure-review.md)。

Harbor终态worker耗时75.579秒，配置请求时限并非精确硬截止；没有服务端分段遥测，不能确认网络、provider排队、生成速度或payload各自贡献。[超时时序](assessment/timeout-diagnosis.json)。评测规模小、题目暴露、开发者参与评分，均限制结论。

工程层Python1,423、Java69（真实MySQL/Redis集成34）、前端58、受控跨服务64/64单列，见[工程验收](implementation-validation.md)。浏览器刷新回看通过，隔离QA安全停止且数据库内容哈希与主服务身份保持，见[浏览器](browser-check.md)、[清理记录](qa-cleanup.json)。优化过程与下一步见[执行记录](execution-notes.md)和[项目说明](../../conversation-round8.md)。
