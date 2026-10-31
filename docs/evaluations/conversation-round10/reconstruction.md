# Round 10：失败复核输入的离线重建

本步骤只重建 Round 9 的 `cedar-t1`、`harbor-t1` 两次失败复核请求，供后续有界协议诊断使用。没有调用模型、没有重新检索、没有使用最终答案补造初稿，也没有生成新的正式评测冻结。

## 取证入口与实现

- 工具：[prepare_review_probe_round10.py](../../../scripts/prepare_review_probe_round10.py)
- 测试：[test_round10_probe_inputs.py](../../../tests/test_round10_probe_inputs.py)
- 历史原始记录：[Round 9 raw seal](../conversation-round9/runs/round9-once/raw-seal.json)
- 两份原始任务：[Cedar](../conversation-round9/runs/round9-once/cedar-t1-final-task.json)、[Harbor](../conversation-round9/runs/round9-once/harbor-t1-final-task.json)
- 两份运行前源码证明：[Cedar](../conversation-round9/runs/round9-once/cedar-t1-source-before.json)、[Harbor](../conversation-round9/runs/round9-once/harbor-t1-source-before.json)

先完整核对历史 raw seal，再从固定 Git commit `77b7a550e6a3390a2fdac6c5136a792a126d5631` 读取记录中的 Python 源码。每个源码文件都与原运行前 `source_files` 的 SHA-256 对照，在临时目录中的隔离 Python 子进程加载。该流程不 checkout、不 reset、不修改原工作树，并禁止子进程建立网络连接。当前实现即使已修改，也不会被拿来冒充原请求构建器。

原始 raw seal SHA-256：`574711014f477b414424cd50a56a6554f1c765b3bac469e547d144c5e16b2e0c`。

重建顺序与原 Agent 一致：

1. 读取原任务 `request.question`、`agent.trace[16].arguments`，保留模型实际提交的八行，不从最终报告反推。
2. 按搜索事件顺序恢复已观察的 hits；用 sealed 文档重算 chunk ID、文本、偏移、内容哈希和文档快照哈希，并对照原 passage catalog。
3. 原源码执行 `resolve_selections`、原始校验、`prepare_review_draft`；覆盖槽输出必须与 `agent.trace[18].output` 完全一致。原 Agent 没有把初次校验返回值覆盖到初稿，重建同样保留该行为。
4. 核对两例确实都是第一轮、输入历史为零、创建时 pinned state 与原任务 context 一致，然后重建原 `context_data` 和 scoped contract。
5. 原源码 `_generation_payload` 构建请求，使用记录中的模型及原始 max_tokens，再以 HTTPX 实际 JSON 编码器比较 body 字节数。

只支持这两个已证明为空历史的首轮案例。后续轮次涉及历史选择，不允许以“找最近成功答案”的近似方式自动重建。

## 已验证的重建结果

| 案例 | 原记录 request bytes | 重建 request bytes | 差值 | 原 review timeout | 原始/补槽后行数 | hits |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| cedar-t1 | 29,533 | 29,533 | 0 | 39,968.432 ms | 8 / 8 | 5 |
| harbor-t1 | 28,064 | 28,064 | 0 | 35,098.544 ms | 8 / 8 | 4 |

重建对象的 canonical JSON SHA-256：

- Cedar：`590df9db97d9840378579b1accbde142b474aa1f7f219bf4479865dd3e952b42`
- Harbor：`7c3682faad03489a27319c1fb3125dbef56fe5d6ec9605c2406e6124d7fdb4d6`

这里的 SHA-256 是**本次重建对象**的哈希。Round 9 没有保留原始 HTTP 请求全文或其哈希，因此字节数相等不能单独证明原始 wire 内容逐字相同。输出明确记录 `original_wire_content_hash_available=false`、`exact_original_wire_identity_proven=false`。若将来重建长度不符，工具保留具体差值，不能称为完全重现。

开发时另核对当前工作树 `_generation_payload` 与两份历史重建对象相等。诊断 runner 仍应在实际发送前重新检查对象相等，不能复用这次检查结论。

## 使用和输出契约

```sh
.venv/bin/python scripts/prepare_review_probe_round10.py --output /tmp/round10-review-probe-inputs.json
.venv/bin/python -m pytest -q tests/test_round10_probe_inputs.py
```

输出已存在时拒绝覆盖。程序入口也可用 `prepare()` 获取同一结构：顶层 `cases` 按 Cedar、Harbor 顺序；每项含 `question`、`draft`（已完成覆盖槽）、`hits`、`context_data`、`original_payload`、`original_review_timeout_ms`、`provenance`，另保留原 `tool_arguments`、`resolved_before_coverage` 和字节校验结果。`original_payload` 这个接口字段表示“由原版源码重建的请求对象”，不表示发现了旧 wire 原文。

每个输入文件均保留相对路径及 SHA-256，trace 的参数、覆盖槽、review 请求位置也随产物记录。原模型问题、资料和答案均已暴露；本工具不提供新题、盲测、质量改进或原失败根因的证明。

## 测试与已处理难点

`16 passed`。回归覆盖两例真实字节计数、原 timeout、来源文件哈希、历史源代码与 Git 对照、资料/命中替换、遗漏密封输入、歧义 review 事件、覆盖槽变动、非首轮历史、伪造最终答案不能影响重建、字节不符的诚实报告、路径逃逸和输出拒绝覆盖。

本轮主要难点是“在新版代码正在开发时证明旧输入来自哪里”。处理方法是隔离历史源码执行，所有内容来自原始工具参数和有哈希的资料；长度核验只是其中一层检查，不扩大成未留存原 wire 的证据。

## 附录：诊断 runner 的只读数据核对与非作用域提示基线

在生产提示仍未修改时，额外检查了 [run_review_probe_round10.py](../../../scripts/run_review_probe_round10.py) 的输入和边界。本次检查属于 AI 开发审阅，没有发起供应商请求。

- `inputs.json` SHA-256 为 `a228c9a4766d7adbfed6918d4068c4502ac75cf8860aca0a2f9571722c42cc39`。两例模型均为 `Qwen/Qwen3-235B-A22B-Instruct-2507`，timeout 与原 trace 精确一致，body 长度与上表一致，baseline 当前构建对象与 `original_payload` 相等。
- runner 的原预算取自每例 `original_review_timeout_ms`，转为秒后限制单次 HTTP timeout 和绝对 deadline；准备/冻结检查时间明确排除在这段**直接复核**预算之外。因此这里只能比较直接复核，不可推断完整 Agent 75 秒链路已通过。
- baseline 与 repaired 共享固定 inputs、protocol、semantic checklist；baseline 要求请求对象完全相等，repaired 引用已密封的 baseline。两阶段各有独占持久 claim，每例禁止第二次请求，超时与中断不能自动恢复。
- runner 写入仅位于 Round 10 的 plan 和 `runs/<phase>`；没有修改 Round 9 raw/gold/评分的路径。检查时 Round 9 历史密封文件的全部哈希及文件清单仍一致；raw seal 仍为上文的 `57471101…`。
- 初始 runner 的 `inputs()` 仅对 timeout 作 `1..40000 ms` 范围检查，`verify()` 只绑定新输入文件哈希，未在实际运行时把预算重新绑定到原 trace 或核对旧 raw seal。已向主任务提出补强：每次核对历史 seal，再按来源记录比较原 timeout、recorded bytes、重建 canonical SHA；输入长度匹配标志也应与实际计算一致。当前输入本身正确；本轮以已核对并提交的输入哈希、源码哈希和阶段绑定保持接口稳定，将这项列为后续增强。这条建议针对后续误换输入仍被重新冻结的风险，不能写成发现原运行已经超预算。

为后续“稀疏复核提示单源化”保留以下基线。分别在历史 `77b7a550` 源码隔离进程和检查时的 `11bf4130` 工作树构建同一 Cedar 输入，结果相同。哈希仅覆盖 `messages[0].content` 原样 UTF-8 字节，不做 strip、换行替换或 JSON 包裹；`context_data=None`，普通生成另设 `draft=None`，非作用域复核传原 draft 但不传 scoped contract。

| 路径 | system UTF-8 bytes | system SHA-256 |
| --- | ---: | --- |
| 普通生成 `draft=None, context_data=None` | 7,101 | `e8d6f85a0120c3edf481d8a55222fbc07fe3d84f40b8b8f3f5581f3f4483b9ca` |
| 非作用域复核 `draft=original, context_data=None` | 8,173 | `61b3db72fa19081a06a401a78ccb97f2862dbb39b7b130e40a0bf0c5f2c7a7bf` |

修复只针对 scoped delta 协议时，上面两个提示应逐字保持。若必须变更其中一路，应明确扩大变更范围并重做对应验收，不能仅凭稀疏路径测试就称非作用域行为未变。
