# 可直接阅读的报告示例

HTML下载后可在浏览器打开；Markdown可在GitHub阅读。每个示例都附原JSON，支持 `lablens verify 文件.json`。本次报告生成全部为0次模型调用，不代表历史生成免费。

| 示例 | 阅读 | HTML文件 | 可复核JSON | 证据来源 |
| --- | --- | --- | --- | --- |
| 正确方向的计划 | [Markdown](correct-plan.md) | [HTML](correct-plan.html) | [JSON](correct-plan.json) | v0.10实际模型生成后手动确认执行的已有收据；此次只离线导出 |
| 手动反向比较 | [Markdown](manual-reverse.md) | [HTML](manual-reverse.html) | [JSON](manual-reverse.json) | v0.10浏览器中人为反转方向的演示，不是模型自然错误 |
| 合成数据的并列查询 | [Markdown](synthetic-query.md) | [HTML](synthetic-query.html) | [JSON](synthetic-query.json) | 原有合成快照，正确题数至少80，再取最少值并保留a/e两条并列 |

反向比较的数学结果可重算，但报告显示任务规则冲突。当前规则核对在导出时重新运行，不冒充历史检查。所有来源路径仅展示为文本，不访问对应位置；哈希不证明来源真实。

[来源与文件哈希](manifest.json) · [操作说明](../../v0.11-workbench.md) · [验收记录](../../v0.11-evaluation.md)
