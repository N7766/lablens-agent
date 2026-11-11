# 第十九轮：真实私有部署、兼容失败与故障复盘

2026-11-11，收尾第8/12轮**部分完成**。GPU 私有部署已经落地，但三次兼容请求整体未通过，完整 Agent 未接通；当前仍为 **7/12轮完整交付**。本轮没有因失败停止后便将第8轮记作完成，也没有合并候选或升级 8081/8765 主服务。

[实际运行与费用记录](evaluations/conversation-round19/runtime-run.md) · [部署事故与修复](evaluations/conversation-round19/runtime-incident.md) · [第三项失败分析](evaluations/conversation-round19/scoped-review-failure-analysis.md) · [可复现部署手册](private-model-deployment.md)

## 已落地的内容

- 在用户明确批准的 Vast A6000 单卡、100 GB 磁盘上部署 Qwen2.5-7B-Instruct BF16，固定 model/tokenizer revision、vLLM/CUDA 和基础镜像 digest；使用 32768 上下文、单序列、loopback 服务、独立 key 与 SSH tunnel。
- 实际修复发行版本 `0.32.0` 与安装包 `0.32.0+cu129` 混淆的预检问题，保留首份失败后使用精确发行包约束。
- 原镜像在 TorchCodec 导入时缺少 CUDA 13 动态库，实际退出；按官方兼容矩阵安装固定 `0.17.0+cpu` wheel，校验大小和 SHA256，禁止依赖解析更改 CUDA PyTorch。该环境是**固定基础镜像加显式补丁**，不是原镜像无需修改即可运行。
- 完成实际服务就绪检查，并对四个权重、config、tokenizer_config、index、LICENSE 与 chat template 核对哈希；保留首次 shared-cache 路径拒绝，新增严格两跳路径支持后重新核验。该结果仅认证固定本地文件字节，不认证运行中镜像或未锚定的 tokenizer 数据文件。
- 依次保留未执行的 `probe-01`、`probe-02` 与对应失败，补丁后重新冻结 `probe-03`。真实请求只执行一次三请求计划，不自动重试、补发或改写旧结果。

模型依据见[选型记录](evaluations/conversation-round19/model-selection.md)，官方发行包与依赖依据见[版本证据](evaluations/conversation-round19/vllm-distribution-evidence.json)和[TorchCodec 证据](evaluations/conversation-round19/torchcodec-overlay-evidence.json)，请求边界见[冻结协议](evaluations/conversation-round19/protocol.md)。

## 实际请求结果

| 请求 | 真实结果 | 不能据此宣称 |
| --- | --- | --- |
| 中文连通 | passed；HTTP 200、完整输出 | 模型语义质量通过 |
| workspace_action | passed；模型选出合法 `search_workspace` | 搜索工具已执行、历史已入 MySQL |
| scoped_review | structured_contract_failed；HTTP 200、完整 JSON | JSON schema 不生效、完整报告验收通过 |

三次合计 17.275529 秒，服务端自报 prompt 3357、completion 236、total 3593 tokens；总体状态保持 `blocked`。这是一次串行兼容检查，不是吞吐、TTFT、并发容量、回答准确率或真实流量验收。

离线重放确认第三项的直接失败原因：模型把 `a1` 同时放入保留列表与替换列表，生产 `restore_review_delta` 拒绝相互重叠的决策。原响应可严格解析，冻结 schema 的字段约束检查通过，但该 schema 没有表达跨列表互斥关系；因此不能把问题归为 JSON 生成失败或 XGrammar 违反 schema。生产报告恢复后的检查没有执行到，也没有独立语义判分。删除重叠项的内存反事实仅用于定位，没有回写原响应或把失败改判通过；详见[逐层失败分析](evaluations/conversation-round19/scoped-review-failure-analysis.md)。

## 工程验证与保留的历史

最终本地 Python 全量为 **2292 passed、1项上游弃用警告、73.77秒**，见[完整输出（路径脱敏）](evaluations/conversation-round19/python-tests-final.txt)。公开日志将本机绝对路径替换为 `<CHECKOUT>`，测试结果未改动。准备阶段2240项、版本约束修复后2258项、运行依赖约束修复后2280项的输出均保留；随后补充11项身份文件系统检查和1项真实失败样本回归，再统一运行最终全量。工程回归不改变本轮模型判定。

检查覆盖所属进程组清理、SIGTERM/EPERM 退出证据、固定缓存与隐式凭据隔离、原始 plan/profile 绑定、精确依赖版本及受限 symlink 路径。Java、前端与业务主服务本轮未升级，未把旧轮测试数充作本轮重新验证。

开发中的首次失败、缓存隔离缺口、EPERM 和输入冻结问题保留在[准备阶段开发记录](evaluations/conversation-round19/development-notes.md)；真实预检、动态库、缓存路径和报告互斥问题保留在[运行事故记录](evaluations/conversation-round19/runtime-incident.md)。旧真实 Agent 超时、Round13 漏发及独立人工评估缺口没有被本轮覆盖。

## 释放与费用

授权为本次最多2小时／10 USD，项目总预算500 CNY。实例 `60154954` 的 DELETE 在 `03:22:16 UTC` 获得 HTTP 200 确认；随后 Safari 回读为0实例、0存储，`03:23 UTC` 移除本轮临时 SSH key 后列表为空，SSH tunnel 随远端关闭。这里分别记录请求确认与平台最终状态，没有把 launcher 退出当成计费停止。

平台当前该实例记录约 **$0.38**，当前 spending rate 为0；页面金额经过舍入，近期账单可能延迟，不能标为最终结算。费用分项、观察时间和清理凭据以[运行报告](evaluations/conversation-round19/runtime-run.md)为准，准备阶段报价估算不替代账单。

## 简历价值与下一步

可以如实写：在 A6000 上完成固定模型/vLLM 的私有部署；通过包版本、动态链接、缓存路径及冻结输出排查真实问题；采用固定 wheel 哈希、有限调用和失败保留完成可复现验证。这里是实际部署排障经历，不是商业生产事故经验。

不能写“完整 Agent 已验收”“模型容量达标”“抗住真实业务流量”或“回答准确率提升”。下一步先离线修正更容易表达互斥关系的报告协议并覆盖旧失败及反例，保留本次原判定；再冻结新的有界评测。通过后另行验证工具执行、报告持久化与刷新历史，最后才推进容量/费用压测。新增租用与调用沿用独立的明确授权和预算边界，不自动延长本次实验。
