# 会话第三轮证据索引

完整结论与边界见 [第三轮验收](../../conversation-round3.md)。本目录同时保留失败尝试、最终开发回归和冻结新题，不合并为一个准确率。

| 证据 | 内容与适用范围 |
|---|---|
| [candidate-first-freeze.json](candidate-first-freeze.json) | 首版源码冻结；实现提交 bd7e165 |
| [candidate-freeze.json](candidate-freeze.json) | 最终生产源码冻结；实现5a96b33、补充测试fed3bd6；开始新题后未调整 |
| [development-first](development-first/README.md) | 首版六个已暴露多轮题；5/6执行完成、1/6严格通过，原始失败全部保留 |
| [development-final](development-final/README.md) | 同六题最终版；6/6严格通过，AI开发侧回归，不是新题 |
| [二次AI复核](development-secondary-review.md) | 对最终开发回归全文、11条判断及25段引文复核；不是独立人工/盲审 |
| [targeted-generation](targeted-generation/README.md) | 首版两个真实DPO旧错误检查；主要错误2/2修正、完整严格1/2；不是最终Agent全链路 |
| [new-scenarios](new-scenarios/REPORT.md) | 隔离出题、提前冻结的AI合成新题；0/6完整交付（2次超时、4轮阻塞），仅一次运行；不是人工独立评测 |
| [regressions.json](regressions.json) / [JVM分组](jvm-final-tests.json) | 本地730 Python、61 JVM、39 UI；含34项真实MySQL/Redis集成 |
| [源码CI](implementation-ci.json) | fed3bd6的GitHub Actions结果 |
| [cancellation-fault-final](cancellation-fault-final/README.md) | 最终版实际本地故障场景4/4、检查35/35；脚本provider，外部调用0 |
| [browser](browser/summary.json) | 最终版真实Chrome界面、澄清手动提交、刷新历史与有限布局验证 |
| [main-upgrade.json](main-upgrade.json) | 主服务升级及182条旧任务等数据摘要不变；本机部署，未演练备份恢复 |

首版工程检查、故障及界面证据另存于 `regressions-first.json`、`implementation-first-ci.json`、`cancellation-fault/`、`browser-first/`，不能标作最终源码测试。

模型评测包包含输入与gold冻结、原始HTTP/任务/报告/用量、评分及整包SHA-256清单。评分与原文存在核验分开；真实引用不自动证明结论正确。公开后的题目已暴露，未来再跑应归为开发回归，不再称为未见新题。

模型调用计数、已知token和未知用量分别记录；这些不是供应商账单对账。QA工作区、数据库、脚本provider只用于本机评测，没有线上用户或生产规模含义。
