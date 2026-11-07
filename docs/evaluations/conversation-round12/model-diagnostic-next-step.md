# Round 12 模型诊断复查与下一步

日期：2026-11-08。此项只读复查历史证据与当前代码，没有调用模型、修改 Python、重跑旧题或改变主服务。Round 12 的并发准入和数据库验收独立进行；后端测试通过不意味着模型质量通过。

## 四次超时究竟确认了什么

本次重新核对 Round 10 [总汇总](../conversation-round10/summary.json)，并离线校验 baseline、repaired 的 `raw-seal.json`：各 17 个文件的哈希与文件集合全部相符。历史失败保持原样。

| 阶段 / 场景 | 请求正文 bytes | 原复核 allowance ms | 本次 attempt 总耗时 ms | 已保存结果 |
| --- | ---: | ---: | ---: | --- |
| baseline / Cedar | 29,533 | 39,968.432 | 40,538.019 | timeout，无最终报告，用量未知 |
| baseline / Harbor | 28,064 | 35,098.544 | 35,673.607 | timeout，无最终报告，用量未知 |
| repaired / Cedar | 28,759 | 39,968.432 | 40,543.270 | timeout，无最终报告，用量未知 |
| repaired / Harbor | 27,290 | 35,098.544 | 35,963.737 | timeout，无最终报告，用量未知 |

逐次证据位于 [baseline](../conversation-round10/runs/baseline/) 和 [repaired](../conversation-round10/runs/repaired/) 下的 `attempt.json`、`trace.json`、`usage.json`、`failure.json`、`request-intent.json`。上表取 attempt 外层耗时，不把它与 trace 中略短的 request 耗时混写，也不把包含关系的 stage/request 耗时相加。

可以确认：

- 运行器共进入 4 次 provider POST 调用，没有自动重试；没有获得可交付的最终报告。两阶段各只改变 system 提示的比较仍然成立；每个请求少 774 bytes 只是正文大小变化。
- 四次内层事件均为 `request_finished.outcome=timeout`，`http_status=null`、`usage_known=false`、`provider_acceptance=unknown`。三个 generation token 字段为 null，不能记成零消耗或零费用。
- 这 4 次是已暴露合成案例的直接 review，不含新检索、完整 Agent 决策链或真实业务流量；最终语义均为 `not_evaluable`。

当前证据不能确认：

- 超时具体发生在连接、读、写还是连接池等待阶段；不能归因于模型慢、供应商排队、网络、DNS/TLS、代理或本地连接池中的任一项。
- provider 是否收到请求、是否继续执行、产生了多少 token 或计费。
- Round 9 的 HTTP 200 后报告验证失败是否被提示修复解决。Round 10 没有重现该类响应，也没有补回当时未留存的 response body。
- `response_received=false` 精确表示观察器没有获得完整 `httpx.post()` 返回的 Response 对象。观察器是在 POST 返回后才记录 received；它没有保存 headers/首字节事件，因此不能进一步断言对端完全没有返回过任何字节。

## 遥测和执行器的实际能力

| 位置 | 已有能力 | 当前缺口 |
| --- | --- | --- |
| [runtime_telemetry.py](../../../lablens/runtime_telemetry.py) 的 `_outcome` / `observe_request` | 请求与阶段耗时、阶段关联、正文 bytes、timeout 参数、完成结果、HTTP 状态和已知/未知 usage；不保存凭证或正文 | 所有 `httpx.TimeoutException` 都映射为 `timeout`；没有 timeout subclass/phase，也没有传输里程碑 |
| [rag.py](../../../lablens/rag.py) 的 `_post` | 调用前检查剩余总预算；收到响应后检查迟到；超时保留未知用量；禁止自动重试 | 只捕获 TimeoutException 总类并转为通用 RAGError；标量 timeout 不是整段调用的硬实时墙钟保证 |
| [model.py](../../../lablens/model.py) 的 `HTTPModel` | `choose_workspace_action` 使用同一请求观察器，超时转通用 ModelError | `complete` 仍直接 POST，没有该请求观察器；不能把 structured workspace action 路径的遥测覆盖面推广到全部模型入口 |
| [run_review_probe_round10.py](../../../scripts/run_review_probe_round10.py) | 候选/输入/协议冻结、持久 once-only claim、每题最多一次 POST、原始响应在业务校验前留存、finally 保存失败和封存 | 包装器只有 POST 完整返回后才能保存 response bytes；异常子类/headers 时间线未留存；执行的是直接 review，不能替代 Agent 端到端 |
| [prepare_review_probe_round10.py](../../../scripts/prepare_review_probe_round10.py) | 按历史源码、输入、字节数及来源哈希重建请求 | 没有历史 wire 原文；重建吻合不能被改写为找到原始网络报文 |

本机安装的 HTTPX 0.28.1 的 `httpx.post` 签名默认 `trust_env=True`；当前 provider 调用未显式覆盖它。这只能说明环境代理可能参与的配置入口存在。Round 10 没有封存脱敏后的环境代理有效配置或连接路线，不能据此认定当时使用了代理，更不能认定它是根因。`workspace_agent.py` 中用于回环工具 HTTP 的 `Client(trust_env=False)` 是另一条链路，也不能作为 provider 路径的诊断结果。下一轮应记录配置事实，禁止为了碰运气自动切换代理、模型或超时。

已有离线测试曾注入 ReadTimeout，并验证未知用量与不重试；这证明受控失败分支，不证明真实 4 次均为 ReadTimeout。外层 stage 的 `outcome=error` 来自转换后的 RAGError，不能覆盖内层已记录的 timeout。

## 下一轮最小有界方案（尚未执行）

先完成零调用的分类回归，再执行一次固定的小协议。沿用评测轮次的证据边界：冻结候选与输入，失败不覆盖，阶段上限不自动恢复，分别报告工程结果、传输结果、最终语义和是否进入主服务。

1. **离线传输分类。** 给请求遥测增加有限枚举 `timeout_phase=connect/read/write/pool/unknown`，保留原 `outcome=timeout` 兼容。分别注入四个 HTTPX timeout subclass、基类 timeout、HTTP 非成功状态、普通 transport error、取消、响应后越过 deadline 和异常 JSON。只保存白名单类型/阶段，不保存异常原文、请求头、代理凭证。验证每次失败只有一次调用、未知用量未归零、观察器异常不会覆盖主错误。补齐实际执行路径的观察器覆盖，并对未覆盖入口明确标注。
2. **冻结一次诊断协议。** 使用一个新实验目录及 once-only claim；记录候选 commit、运行依赖版本、端点与模型、输入/协议 SHA、正文 bytes、各阶段 timeout 和绝对预算、脱敏的 `trust_env`/代理是否配置等事实。旧 Round 10 目录只读。不要输出代理 URL、Authorization 或环境变量全集。
3. **固定小请求基线。** 先做 1 个预先声明的极小独立问题，最多 1 次 POST、输出上限 64 tokens、请求 timeout 不超过 10 秒。成功只证明这次请求返回；它不能证明大请求、报告 schema 或 Agent 已稳定。若它失败，停止本次 live 协议并保留失败，不继续花费尝试报告。
4. **一条独立新题到真实报告。** 小请求通过后，使用此前未用于修改规则的单份小资料包，包含一个可支持命题、一组相互矛盾证据和一个缺失信息问题。问题与答案标准在候选冻结前由另一名 AI 审阅者封存，并如实标注“AI 开发新题预验收”，不冒充独立人工评测。使用 BM25 和实际生成/复核/最终验证路径，最多 1 次生成加 1 次复核；不调用 embedding。保留原始响应、逐步验证结果、最终报告和来源。生成失败后不运行依赖它的复核。
5. **先封存再审阅。** 分开核对检索是否找到必需证据、结论/对象/阈值是否正确、矛盾与证据不足是否正确表达、是否错误拒答，以及引用是否真正支持结论。真实报告返回不自动等于语义通过。该直接 RAG 小链路也不冒充完整多轮 Agent 验收；后续 Agent 选工具→核验→历史读取需要单独小协议。

建议 live 总上限为 **3 次 POST**，只有一次极小请求和一条新题；不追加第 4 次。报告阶段沿用冻结时实际生产输出预算，不暗中提高预算、降低验证标准或切换便宜模型来追分。新题资料虽小，完整 JSON schema 仍可能较大，必须测量实际请求正文而不是凭资料字数估算。

费用建议上限 **US$1**；这是下一轮协议的建议约束，并非已发生费用。运行前先核对该端点/模型当时的单价，用冻结的输入与最大输出预算估算上界；无法建立可靠上界或预算不符时，不启动 live。全程建议以 **100 秒**为不再开始下一请求的总时间界限，单次生成/复核分别不超过 35/40 秒；HTTPX timeout 仍不宣称硬实时中断正在执行的调用。实际耗时、已知 usage 和未知费用都要原样记录。

停止条件：离线回归失败、来源/候选/协议哈希不一致、预算或凭证预检失败则不调用；第一次极小请求失败则停止 live；达到请求数/时间/费用上限、用户中断、未知用量失败或依赖步骤失败则不自动重试、不删 claim 重跑；保留已执行失败，未执行项标 blocked/not_run。需要新实验时另立协议与身份，不能补调用到“成功”为止。

该切片的目标是取得能够区分超时类别、可核查一次报告交付的证据。即使全部通过，也只是一题的新样本预验收，不能发布稳定成功率、独立人工评测或生产承载结论；Round 10 的 4 次失败和未验收状态继续保留。
