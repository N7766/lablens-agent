# Round 9 冻结真实回归报告

**候选未通过质量与交付验收。** 2026-10-31，10计划轮仅1份报告交付；5次实际提交中2次复核失败、2次超时，另5轮依赖阻断。旧六轮原strict 0/6（无可评分答案），原四轮0/4（可评分0/1）。相比Round8的6报告+1必要澄清，交付明显下降。全部十题已暴露，为开发团队AI评阅的合成回归，不是独立人工评测或通用准确率。

## 身份与执行条件

- 实际模型候选：`77b7a550e6a3390a2fdac6c5136a792a126d5631`；workspace-agent-v10 / scoped-review-delta-v3。
- [输入冻结](input-freeze.json)：652文件，SHA `44fdc6c67b08166e28a02c1cf8fc0769aa0ac65cac12ad5570af6a7be89cef78`，绑定原题/gold及完整Round8历史。
- [raw seal](runs/round9-once/raw-seal.json)：525文件，SHA `574711014f477b414424cd50a56a6554f1c765b3bac469e547d144c5e16b2e0c`。
- [候选与配置](candidate-freeze.json)：70个源码/配置文件。隔离QA8083/8767、独立数据库；4场景、16资料；DeepInfra `Qwen/Qwen3-235B-A22B-Instruct-2507`。
- 单任务75秒、6 decision+1 review、1800/2600输出上限及20秒最大review预留保持；全轮70尝试上限，实际15。无重试、补采、embedding或缓存复用。冻结后运行中无源码/输入改动，raw封存后才读语义。

[完整协议](protocol.md)、[执行汇总](runs/round9-once/run-summary.json)、[评分](assessment/scores.json)、[逐题标注](assessment/annotations.json)。后置诊断修复是另一个代码检查点，不冒充本次模型候选。

## 逐轮结果

| 轮次 | 交付 | 可确认状态 |
| --- | --- | --- |
| cedar-t1 | 无 | review HTTP200返回后仍余15.133秒，review_error；详细原因未留存 |
| cedar-t2 / t3 | 无 | 依赖失败/阻断，未提交 |
| harbor-t1 | 无 | review HTTP200返回后仍余26.234秒，review_error；详细原因未留存 |
| harbor-t2 / t3 | 无 | 依赖失败/阻断，未提交 |
| ar-t1 | 1份事实报告 | 主结论正确；缺默认值与覆盖关系解释、部分逐行规则引用；六维2/2/1/1/2/2 |
| ar-t2 | 无 | review返回且token已知，但已超过总预算；worker75.360秒 |
| kl-t1 | 无 | review传输超时，接受状态/用量未知；worker76.143秒 |
| kl-t2 | 无 | 依赖失败，未提交 |

失败draft不计回答，未提交轮保留计划分母。旧rubric的失败轴是未交付计分约定，不表示已经观察到事实全错或检索失败；实际Cedar/Harbor已检索到关键规则及测量。两套rubric不同，不汇总“准确率”。[旧六轮](assessment/old-review.md)、[原四轮](assessment/new-review.md)、[609个证据指针实例交叉核验](assessment/internal-cross-check.md)。

## 用量与延迟边界

15次模型尝试 = 10 decision + 5 review。14次返回用量完整已知，合计 **71,207 token下限 = 65,219 prompt + 5,988 completion**；kl-t1的一次review未知。费用未完整对账，不推算总费用。总token低于Round8不能作为优化收益：本轮少提交3个任务、少交付大量答案。[逐调用审计](assessment/call-audit.json)。

唯一成功的ar-t1使用5个替换行、0个保留行。保存的parsed review按本地JSON编码为1,416 bytes，实际request为22,749 bytes；前者不是provider原始HTTP字节；这不是相同响应的压缩A/B。[复核审计](assessment/review-audit.json)。7份历史草稿的受控JSON对照显示请求增加1,349–1,427 bytes、假设保留全部允许行时响应减少529–2,204 bytes；全替换增加31 bytes。假设场景不能当真实token/延迟/质量改善。[最终字节对照](review-delta-bytes-final.json)。

## 工程检查与后置修复分开

冻结候选工程检查：Python1,661、Java70（34真实MySQL/Redis集成）、UI58通过；schema enum列表防别名拷贝小修另有88项定向通过。受控跨服务64/64、外部模型0。[候选工程证据](engineering-checks.json)。

真实失败揭示观察缺口：成功路径保存reviewed_response，失败路径却没有原始正文/finish_reason，不能定位具体错误。raw封存后接通有界失败observer和Java错误轨迹透传；追加畸形choice/message、超长正文、错误JSON、ID分区、隐私与无重试反例。后置最终Python **1,672**、Java **71**、受控跨服务 **64/64**通过，外部推理0；UI未改，沿用本轮58项。独立审查促成畸形message的AttributeError修复，另保留任意provider异常文本不输出的旧隐私约束。[后置检查](post-seal-checks.json)、[审查](post-seal-code-review.md)。

这项补丁改善未来失败的可查性，没有恢复这次丢失的正文、确认旧失败根因或证明模型质量改善。解析前因预算拒绝的响应、从未返回的传输请求仍没有正文；明确记录未观测。[失败复核](assessment/failure-review.md)、[全部过程](execution-notes.md)。

## 交付与下一步

浏览器只读233个GET确认报告、引用、工具轨迹与失败历史可回看；刷新重选后正文1,980 bytes及SHA相同，无控制台错误。没有本轮澄清案例，未补造验证。[浏览器验收](browser-check.md)。QA关闭前后五表全内容哈希/数量/状态不变，保留4工作区、16资料、5任务、4会话；主服务进程与健康不变。[清理记录](qa-cleanup.json)。

PR48保持draft，主服务未升级。下一次先用小规模协议验收拿到真实保留/替换路径的可诊断结果，再安排新题检验通用性；不只针对已暴露十题扩规则。MQ、并发开放负载、数据规模、长短期记忆和GPU私有部署仍在独立路线，本轮未完成。项目价值在可审查的后端状态、来源、预算和排障过程；目前不能对外承诺稳定答案质量或生产流量能力。
