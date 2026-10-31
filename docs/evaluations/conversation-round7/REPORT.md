# Round 7 单次模型回归结果

**质量门未通过。** 候选 `619c05122acc092241596c6f1a2f20b72ce18fcd`，`workspace-agent-v8` / coverage v2。之后的 coverage v3 只做离线验证，不继承本报告的模型结果。

## 条件与封存

沿用原 10 轮、16 份合成资料与两套原 gold；题目已暴露，开发团队 AI 逐条评阅并由另一 AI 复核，不是盲测或人工独立评测。DeepInfra `Qwen/Qwen3-235B-A22B-Instruct-2507`、BM25 top5、单任务75秒、6 decision + 1 review、输出1800/2600。隔离工作区只采集一次。

- [候选清单](candidate-freeze.json)：66 个源码/配置文件绑定候选提交。
- [输入冻结](input-freeze.json)：30 文件，SHA `1c7c2dbf099c7b04d8403a8df366ae47034e29e28ff9e94fc189f33376618d98`。
- [原始目录](runs/round7-once/)：571 个封存文件（seal 文件另计），raw seal SHA `9054319d72f861c077b4184c071e5de6c8f2644689078363b1d607e255cefde1`。
- [评分](assessment/scores.json)、[标注](assessment/annotations.json)、[内部 AI 复核与分歧](assessment/independent-review.md)、[评分文件清单](assessment/grading-manifest.json)。

## 交付、质量与用量分别报告

| 原题组 | 计划 | 提交 | 交付 | 失败/阻断 | 原 gold 严格通过 |
| --- | ---: | ---: | --- | --- | --- |
| Cedar / Harbor | 6 | 4 | 2 报告 + 1 必要澄清 | 1 / 2 | 2/6；已评分2/3 |
| Alder / Kite | 4 | 4 | 4 报告 | 0 / 0 | 0/4 |

两组不汇总成语义准确率。没有回答的三轮不从计划分母删除。6 事实报告与1合理澄清不是7份合格报告。原四轮检索覆盖均满分但严格0/4，说明必要资料命中与完整、正确、引用充分是不同指标。

[调用审计](assessment/call-audit.json)与[独立审计](assessment/independent-call-audit.json)一致：21次=15决策+6复核；83,382 prompt + 6,758 completion = 90,140 provider报告token。21次均有返回用量，无未知接受；无embedding、重试、重提或任务缓存。未核对供应商账单，不换算成本。原始采集完整、身份保持、fatal=null。

| 轮次 | 结论与可定位原因 |
| --- | --- |
| cedar-t1 | coverage漏认原稿的试验ID整体判定，误补槽超过8行；2次决策、0复核后停止，尚余约35.6秒。不是本次超时。 |
| cedar-t2/t3 | 前轮没有有效响应，依赖阻断，未提交。 |
| harbor-t1 | 主要数据与总体判断正确；冗余Sable槽下解释Quill并引用Quill，确定对象错配。 |
| harbor-t2 | 正确区分149秒达标、98.75%反驳达标、停电最大时长未知。严格通过。 |
| harbor-t3 | R-17/R-09指代不明时必要澄清。严格通过。 |
| ar-t1 | 主要2总/1重试与4xx正确；原gold要求的默认值与覆盖关系解释缺失；保留“最多”措辞的评分分歧。 |
| ar-t2 | 新值正确；三项比较以对象不同错误拒答，已有资料足够，非检索缺口。 |
| kl-t1 | 当前配置和历史未知正确；原gold较细的transport范围、时间边界及最小证据不完整。 |
| kl-t2 | 已转为事实报告并保留历史未知；额外配置行引用不支持它的alert-card，最小证据仍漏项。 |

详细证据指针、排除项与假设见[旧六轮](assessment/old-review.md)、[原四轮](assessment/new-review.md)、[失败复核](assessment/failure-review.md)。对 ar-t1 措辞与 harbor-t1 明示“通过”的门槛有内部阅读分歧，原标注不改；这些争议不改变严格通过名单。

## 对比与限制

| 轮次 | 旧六轮交付 / 严格 | 原四轮交付 / 严格 |
| --- | --- | --- |
| Round 5 | 6/6 / 2/6 | 2/4 / 0/4 |
| Round 6 | 0/6 / 0/6 | 4/4（3报告1错误澄清） / 1/4 |
| Round 7 | 3/6（2报告1必要澄清） / 2/6 | 4/4（均报告） / 0/4 |

响应恢复不证明整体质量提升；一批没有超时不证明时延稳定，未做因果消融，也未测生产流量或泛化。原gold部分要求较细、样本小、AI参与开发与评分，是已知限制；不通过删除失败或放宽gold取得更好结果。

[后置修复](../../post-evaluation-round7.md)仅修有限机械识别，原始输出、评分、raw seal保留。PR保持草稿、主服务未升级。封存后完整Python 1,222项、UI 58项通过，源码/日志哈希见[后置验证](postseal-validation.json)；不改写上述模型结果。工程与浏览器验收见[工程验证](implementation-validation.md)、[历史回看](browser-check.md)、[QA关闭记录](qa-cleanup.json)。
