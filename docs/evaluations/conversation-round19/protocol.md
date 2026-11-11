# Round19 私有模型准备与后续兼容性验收协议

日期：2026-11-11。本轮当前阶段为 **prepare only**：Safari 已登录 Vast，但尚未租用 GPU、尚未注册本轮 SSH key，未下载镜像层／权重，真实模型请求数为 0。单测中的固定 HTTP 响应不计为推理，也不计为部署成功。

## 冻结对象

候选配置为 `deploy/private-model/profile.json`：Qwen2.5-7B-Instruct 固定 revision `a09a35458c702b33eeacc393d103063234e8bc28`；vLLM 0.32.0、linux/amd64、CUDA 12.9.1，image manifest `sha256:f99aa709b3f873aa1e1aadf5c1db35970119e8af9b36fe15d2a667aae0ed005c`。选择原因、元数据实算哈希、显存推算和未知值见 [model-selection.md](model-selection.md)。

Probe 的 `freeze` 默认不发 HTTP。保存当前生产 Python 文件／probe 哈希、完整请求、请求 hash、profile 原文件 hash、预期部署身份、本地运行库版本和硬预算。冻结后改变这些对象应新建候选。模型服务上的实际权重身份仍需另行核验，model alias 和合法 JSON 不能替代该证明。

首次执行原子创建 `run/`，并在发送每个请求前写入 intent；intent 表示准备发送，不证明远端收到或产生费用。运行时使用一次读取并核验的 plan bytes/hash，收尾绑定同一 hash；磁盘 plan 后续变化单独报告。相同目录不得重放；中断留下的 unknown 不能当作零调用。

## 先后门槛

| 阶段 | 通过依据 | 不能据此宣称 |
| --- | --- | --- |
| 元数据准备 | 官方 registry / HF 小型元数据、固定 revision/digest、可解析计划 | GPU 已租用、镜像已启动 |
| 主机预检 | 实例记录与实际 GPU/driver/vLLM/RAM/disk 检查 | 推理成功、权重身份已认证 |
| 模型就绪 | 启动日志、权重及 tokenizer 核验、健康接口 | Agent 可用、回答正确 |
| 三次兼容请求 | 本协议下全部完成且 usage 可识别 | 端到端工具链、检索质量、语义质量、容量 |
| 完整 Agent 验收 | 后续独立冻结的工具执行、报告、历史刷新及失败终态证据 | 真实生产流量、高并发容量 |
| 模型压测／质量评估 | 后续另设数据集、负载、阈值和统计 | 本轮尚未执行的指标 |

## 三次请求：固定顺序，无失败重试

| 序号 | 请求 | 输出预算 | 机械验收 |
| --- | --- | ---: | --- |
| 1 | 中文连通性 | 64 tokens | `finish_reason=stop`，单个 assistant 文本，内容为指定中文 |
| 2 | `json_schema_action_v1` | 1800 tokens | 严格 JSON；action 为 `search_workspace`；参数经生产 Pydantic 校验，BM25、top_k=3、非空 query |
| 3 | 生产 scoped review / delta schema | 2600 tokens | 完整分配原 assessment IDs，恢复 delta，证据 ID 解析，调用生产 review 与 assessment 检查 |

总计最多 3 次；每次墙钟最多 40 秒、运行总预算最多 120 秒，响应原始 body 上限 128 KiB。不跟随 redirect、不自动 fallback、不读取 HTTP proxy 环境。计划 endpoint 必须为精确的 `http://127.0.0.1:<port>/v1`；run 不接受 endpoint/model/budget 覆盖。

数据仅为两份明确标注的合成资料：SFT=86% 的小表和缓存配置，无用户工作区资料。第二项采用生产 schema builder 的缩小合成请求，并未真的执行搜索；与生产首轮 action 的工具过滤、提示、400-token 输出预算不同。第三项使用固定 draft 与生产校验器，有限规则可能拦截/调整结论，但不执行普遍的语义蕴含评审。即使三项全通过，`semantic_acceptance=not_evaluated`。

## 结果分类与保留

- 全部三项完成、机械合同通过、usage 字段合法且计数相符，才可写 `compatibility_passed`。保存逐次 token 统计及总计，不把自报 token 转换成未经测量的 GPU 成本。
- 401、4xx/5xx、schema 不支持、非 JSON、length 截断、非法 action、引用/ID 错误、usage 缺失或不一致均停止后续请求，保持失败原件。
- 超时、传输中断或未收到完整 body，记录 outcome/usage unknown，不能推断远端已经停止生成，也不能自动重试。客户端取消与 GPU 端终止是两项独立事实。
- plan/source/profile 漂移在发送前拒绝。运行过程中 plan 被改动时保留原执行计划/hash，另报 drift，不能把新文件哈希当作已执行的冻结对象。
- `response.sanitized.txt`、`validation.json` 与 manifest 只提供内容完整性和有限脱敏，不是签名审计或完整隐私清洗。发布前扫描本轮实际密钥；远程完整 server.log 默认留在私有目录。

## 执行与预算边界

完整命令见 [部署手册](../../private-model-deployment.md)。先在付费前确认具体 Vast offer、预估费用、2 小时窗口和释放流程；不把 500 元预算或已登录账户视为已租机。使用 Vast SSH 容器直接启动固定 image 内的 launcher，不嵌套 Docker。Safari 模板编辑的 CLI preview 已保留完整 `image@sha256` 输入，随后取消且未保存；这不是平台实际拉取或运行验收，实际镜像身份仍需确认。

Launcher 的 7200 秒 watchdog 管理它启动的模型进程组，不管理云账单。结束后导出证据，核对同一实例 ID 的停止/销毁状态和存储计费。SIGKILL、断线和宿主机异常时更需要从平台回查，不能因为本地命令退出便宣布费用停止。

本轮配套 worker 使用独立 env、独立 8770 端口与 SSH tunnel；显式封闭 embedding 配置，防止回退到原云服务。三次 probe 不需要启动 worker。后续完整 Agent 需另建隔离 JVM/数据库资料快照，验证真实“提问→选工具→检索/核验→报告→刷新历史”，并分别记录超时和证据不足终态。未经这些观察，本轮不能标记为完成 Agent 端到端接通。

## 必备凭据与当前空缺

后续执行需要保存：offer/租赁与释放记录、冻结 profile/plan、远程 GPU/driver/runtime 信息、镜像身份依据、实际权重/模板 hash 核验、启动/退出记录、三次请求的 intent/脱敏响应/usage/判定/manifest。实际 GPU、峰值显存、TTFT、tokens/s、并发容量、语义通过率和最终费用当前均未测量，保持 `null` 或“未执行”，不使用模拟响应补齐。
