# Round19：真实 GPU 部署与三次兼容请求

2026-11-11。已在 Vast.ai 单张 RTX A6000 48 GB 上启动固定 Qwen2.5-7B-Instruct + vLLM，完成三次真实推理并释放实例。**部署启动成功，兼容性整体验收未通过**：中文和选工具结构通过，第三次报告审阅包含重复决策，被生产校验拒绝。没有追加调用或把离线修改后的输出计为模型通过。

这是收尾第 8/12 轮的部分交付。完整 Agent 工具执行、MySQL 历史回看、独立语义评测和模型容量仍未验收；不能因租到了 GPU 就把这一轮标为全部完成。

## 环境与身份

| 项目 | 运行证据 |
| --- | --- |
| GPU / 磁盘 | 一张 NVIDIA RTX A6000，49,140 MiB；100 GB 容器盘 |
| 驱动 / 架构 | 580.178.04；compute capability 8.6；Linux amd64 |
| 模型与 tokenizer revision | `Qwen/Qwen2.5-7B-Instruct` / `a09a35458c702b33eeacc393d103063234e8bc28` |
| 基础镜像 | `vllm/vllm-openai@sha256:f99aa709b3f873aa1e1aadf5c1db35970119e8af9b36fe15d2a667aae0ed005c` |
| 运行包 | vLLM `0.32.0+cu129`；torch `2.13.0+cu129`；TorchCodec **显式补丁** `0.17.0+cpu` |
| 运行配置 | BF16、32768 上下文、max-num-seqs=1、eager、XGrammar、独立缓存；API 仅 loopback，经 SSH 隧道访问 |
| 最终 profile SHA256 | `744cbf609fcc61b0d2704a9db6e9cfaaddcc410f060eacfd26172864c45f3e1c` |
| 已执行 plan SHA256 | `90c06e1a9ff049a4a5bd51a801e6dc7d184bd55cd5203c1557b023d04962d99e` |

[实际主机检查](runtime/server-03/host-check.json)、[启动参数](runtime/server-03/launch-intent.json)和[补丁安装回执](runtime/codec-overlay-01/install-receipt.json)分别记录资源、参数及运行环境变化。基础镜像不是未修改状态；CPU 补丁只用于本轮纯文本，不验收 GPU 音视频解码。容器检测到的 113,625 MiB RAM 不是 offer 的约 48 GB 保证分配量。

四个权重分片合计 **15,231,271,888 bytes** 的实际 SHA256 全部匹配固定 HF 元数据；config、tokenizer config、权重索引、LICENSE 与 chat template 也匹配，见[离线文件身份校验](runtime/identity-03-shared-cache.json)。该结果证明缓存文件字节，不是运行进程已加载字节的远程证明；tokenizer.json/vocab/merges 只记录实算值，原 profile 没有预期哈希，不宣称其独立身份认证。平台镜像引用与固定 digest 一致，不等于整个运行根文件系统的独立 attestation。

## 三次请求的实际结果

| 顺序 | 请求 | 结果 | 输入 / 输出 tokens |
| --- | --- | --- | ---: |
| 1 | 指定中文文本 | passed，精确输出“本地模型就绪” | 39 / 5 |
| 2 | 生产 action schema 的合成小请求 | passed，选择 `search_workspace`，BM25、top_k=3；未执行搜索工具 | 69 / 53 |
| 3 | 生产 scoped-review delta 契约 | **structured_contract_failed** | 3249 / 178 |

三次均为 HTTP 200、完整 body、`finish_reason=stop`、可识别 usage；总计供应端自报 **3357 输入 + 236 输出 = 3593 tokens**，整个串行运行 **17.275529 秒**。这是单次兼容实验总耗时，不是 TTFT、吞吐或压测。没有重试，原始判定保持 `blocked`；输入计划未漂移。[完整冻结请求、响应与汇总](runtime/probe-03/run/summary.json)和[原始 manifest](runtime/probe-03/run/manifest.json)可复核。

第三次把 `a1` 同时列入 `retained_assessment_ids` 与替换 assessments。生产 `restore_review_delta` 因交叉重复 ID 抛出 `Unknown, duplicate or overlapping replacement ID.`。JSON 和字段结构正常，但当前 schema 没有表达两个列表间的互斥约束，因此不能据此说 XGrammar 忽略了 schema。恢复阶段已经失败，原始输出未进入后续语义验收。见[离线逐层排查](scoped-review-failure-analysis.md)及[结构化证据](scoped-review-failure-analysis.json)。

离线删除任一冲突决策后能通过机械校验，仅用于定位失败边界；没有改写实际响应、重算成绩或证明新提示已修复模型。已新增一条来自原始响应的回归，确认生产端继续拒绝此类重复决策。

## 真实排障过程

| 异常 | 已确认与处理 | 保留的证据 |
| --- | --- | --- |
| 首次版本预检拒绝 | release `0.32.0` 与安装包 `0.32.0+cu129` 混淆；根据官方构建源码改为精确映射，未使用前缀放行 | [原始 host-check](runtime/host-check-original.json)、原 profile、未执行的旧冻结候选 |
| 第二候选导入退出 | CUDA 12.9 镜像中的 TorchCodec 依赖 CUDA 13 动态库；只替换哈希固定的官方 CPU wheel，其他已列关键包版本不变 | [第二候选退出](runtime/server-02/exit.json)、[补丁出处](torchcodec-overlay-evidence.json)、导入 smoke |
| 首次权重校验拒绝 | HF 新缓存为两层链接，原校验器在读取前拒绝，并非权重哈希不符；最小支持严格限定的两跳布局，保留内容 SHA/大小/边界检查 | [原拒绝](runtime/identity-03-original.json)、修复前脚本、11 项真实文件系统测试、最终哈希结果 |
| 第三次推理合同失败 | 同一 assessment ID 被同时保留和替换；生产拒绝正确，本轮不静默去重 | 原始响应、离线重放、真实失败样本回归 |

详细版本、日志边界与复现步骤见[运行事故记录](runtime-incident.md)和[部署手册](../../private-model-deployment.md)。原候选 01/02 没有发出模型请求；candidate03 才执行三次冻结请求。平台 API 首次无尾斜杠返回 301 是实例管理路由问题，另行修正为明确 URL；这不属于模型请求重试。

## 费用与释放

授权为单实例、最多 2 小时、10 USD，项目总预算 500 元。计时从创建 intent **02:40:30 UTC** 开始，原绝对截止 **04:40:30 UTC**，没有因修复重置。模型进程退出与云账单分别核对：

1. 导出 21 个明确列出的远端小文件，校验本地归档；没有导出模型权重或凭据文件。私有归档 SHA256 为 `376c050ccf8315f57d148f43c59f9d65fabfc5c937e192f82a5c9201f055c9b6`，原始完整日志不公开。
2. 对本轮 launcher 发送 SIGTERM，[退出回执](runtime/server-03/exit.json)确认所属进程组清理；该回执明确不证明云端计费停止。
3. 本轮实例 `60154954` 的 DELETE 在 **03:22:16 UTC** 返回 200；随后已登录的平台页面显示 **0 实例、0 存储**。临时 SSH 公钥移除后列表为空，本地 SSH 隧道被远端关闭。见[清理记录](runtime/cleanup-observation.json)和[销毁请求回执](runtime/provider-destroy-01.jsonl)。创建至释放约 42 分钟。
4. 平台对应实例的账单行当前显示约 **$0.38**，当前账户 spending rate 为 **$0.00/h**。GPU/存储/下载显示约 $0.27/$0.03/$0.07，上传 <$0.01；显示值有舍入。平台注明近期活动可能延迟，**不是最终结算发票**。见[独立实例费用观察](runtime/billing-observation.json)。未公开余额、支付卡或账户资料。

原 $1.3859 是 2 小时与假定流量的规划估算，不是实际支出。tokens 计数未被换算成租机费用。UI 清理与账单记录是白名单字段转录，保留观察时间和来源，不称签名审计。

## 工程验证、材料与下一步

最终工程结果见 [engineering-checks.json](engineering-checks.json) 与 [python-tests-final.txt](python-tests-final.txt)。全部测试为本地工程验证，不计入三个真实模型请求。原 2240、2258、2280 阶段结果保留；最终新增文件身份检查与失败样本回归。Java、前端和主服务代码没有在此次部署实验中修改。

准备阶段以 Git commit `505b78b34be98c013505559dcf927c1376a156e5` 为历史快照；[准备 manifest](preparation-manifest.json)和[准备工程记录](preparation-engineering-checks.json)保留原阶段。部分说明页后来增加当前状态注记，其现行字节不再对应准备 manifest；新的 [manifest](manifest.json)覆盖当前公开材料，两个阶段不混算。原 frozen-probe 保持未执行，实际运行使用 runtime/probe-03。冻结 profile 中 `model_requests_sent=0` 等字段是冻结时的计划状态，不能当实时计数；真实执行次数与身份观察以 runtime 回执为准，未为更新展示数字而改写冻结配置。

下一步先在本地设计每个 ID 仅有一个决策位置的结构或同等强约束，并用本次失败样本及缺失/矛盾资料回归。之后新建冻结候选，按另一次明确预算重新评估；不能复用本次结果宣称已修复。通过后再接隔离 Agent 工具、持久化报告与刷新历史，再做流式指标和受控容量实验。PR 保持草稿，8081/8765 主服务未升级。

简历可据此展示：固定版本模型的实际 GPU 私有部署、SSH 隔离接入、精确依赖与文件身份核验、真实动态库/协议错误排查、有界评测与资源释放。暂不能写完整私有模型 Agent 验收通过、高并发容量、语义准确率或真实业务流量指标。
