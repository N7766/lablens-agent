# Round 9：封存后只读浏览器验收

2026-10-31 在 raw 封存并收到 GO 后，用独立 Playwright 会话 `round9-qa` 打开隔离 QA 的 `http://127.0.0.1:8083/`。对应候选为 `77b7a550e6a3390a2fdac6c5136a792a126d5631`，场景来自 [run-summary](runs/round9-once/run-summary.json)。没有操作主服务 8081、勾选模型授权、提交或重试任务，也没有新建澄清案例。

## 实际核查

| 原始任务 | 操作及可见结果 | 证据 |
| --- | --- | --- |
| ar-t1 `0f672944-10ea-4c1e-8702-894420d3bdc3` | 选择 Alder 项目和会话，从最近记录打开唯一成功报告；显示“已完成 · 资料版本 4 · 存在矛盾”，可展开 3 段来源及 Agent 查找、核验、复核、保存轨迹。 | [报告截图](browser/success-report.png)、[来源与轨迹](browser/success-sources-trace.png)、[页面快照](browser/success-sources-trace-snapshot.txt) |
| ar-t1 刷新回读 | 浏览器 reload 后项目与会话仍被选中，结果面板回到待选择状态；重新打开同一历史任务后报告与刷新前完全一致。并非自动恢复当前报告。 | [刷新后截图](browser/success-after-reload.png)、[刷新前原文](browser/answer-before.txt)、[刷新后原文](browser/answer-after.txt)、[相等性记录](browser/reload-equality.json) |
| cedar-t1 `5d11c8c0-f3c7-4b92-af88-7bc66261d103` | 历史显示失败与保存的 worker HTTP 502 信息；轨迹保留搜索、核验、进入复核及“复核失败”停止。没有展示一份成功答案，也没有自动重试。 | [失败截图](browser/cedar-failure-trace.png)、[页面快照](browser/cedar-failure-trace-snapshot.txt) |
| ar-t2 `3ccd4308-2910-4d61-a307-5355fe362ffe` | 原会话第二轮显示失败、执行时限信息及“超时，已停止”；旧成功首轮仍在历史列表。 | [超时截图](browser/ar-timeout-trace.png)、[页面快照](browser/ar-timeout-trace-snapshot.txt) |
| kl-t1 `392d4edc-a2e2-4444-b41b-daf241f4468f` | 显示超时停止，并明确提示“中断前的模型用量可能未知；未自动重试”。搜索、核验、复核和快照/资料摘要仍可见。 | [未知用量超时截图](browser/kite-timeout-trace.png)、[页面快照](browser/kite-timeout-trace-snapshot.txt) |

刷新前后所比较的是完整报告正文（含用户范围说明），长度 **1,980 UTF-8 bytes**，SHA-256 均为 `f263512ff5b5bf7184b6fc9ab84e6b630efce336ec6e1e338c6d217fed681aae`。这证明已保存内容可回读，不证明结论语义正确。

所有截图是本次 QA 的真实页面；已目视检查成功报告、复核失败和用量未知的超时页面，内容清晰且没有面板遮挡。报告里的“生产集群”属于已暴露合成评测材料，不代表该项目承接过真实生产流量。

## 网络与控制台

[完整请求列表](browser/network-final.txt)包含导出时的 **233 条请求**，含静态资源，全部为 `GET http://127.0.0.1:8083/...` 且 HTTP 200；没有 POST 或其他写请求。请求通过工作区任务列表、会话历史 GET 回读，选中任务后由 UI 渲染已返回的记录，并不是每个任务都有单独的 GET `/tasks/{id}`。历史任务显示的 worker 502 是原采集失败信息，不能与此次读取 HTTP 200 混为一谈。[机器核对摘要](browser/network-check.json)

[控制台](browser/console.txt)记录 0 条消息、0 errors、0 warnings。CLI 操作期间出现过轮询替换任务按钮后 ref 失效及定位/脚本命令错误，重新取得快照后改用刚观察到的精确按钮名称完成读取；没有因此提交模型任务。这些自动化命令错误不是应用控制台错误。

本轮没有实际澄清终态，**未验证本轮澄清 UI**。依赖被阻断的 5 个计划轮没有提交任务，未创造替代数据补齐。只读浏览器验证不改变原始失败、评分分母、语义评分或费用审计。

独立浏览器会话已[关闭](browser/closed.txt)；QA 服务由 root 统一清理。原始截图与日志先保存在 `output/playwright/round9/`，再原样复制到本文的 `browser/`，文件指纹见 [manifest](browser/manifest.json)。
