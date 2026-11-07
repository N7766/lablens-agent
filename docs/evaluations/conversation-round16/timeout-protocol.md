# Round16 本地超时诊断协议（运行前固定）

目的：解释 Round15 的计时证据边界，并通过真实 loopback HTTP 的可控延迟检查现有 `HTTPModel`、Agent 总时间预算和协作取消。没有模型供应商调用、训练、语义正确率或生产容量测试。

## 固定场景与判定

| 场景 | 本地服务行为 | 验证内容 |
|---|---|---|
| 等待响应头 | 450 ms 后发响应头；read timeout 200 ms | `ReadTimeout`，客户端没有观察到响应头 |
| 响应体停顿 | 立即发头和首块，暂停 450 ms；read timeout 200 ms | `ReadTimeout`，客户端确实已观察到头和首块 |
| 连续小块 | 8 块、相邻间隔 80 ms；read timeout 200 ms | 完成耗时超过 200 ms；逐次 read timeout 不是总截止时间 |
| 现有 HTTPModel 重放前三种行为 | 同一真实本地服务；合成 JSON action 和合成 usage | 保留真实运行时 telemetry；两类 read timeout 的 `response_received=false` 都表示没有完整 Response，不能推出没有头或字节 |
| Agent 总预算 | 总预算 1 s，16 块间隔 100 ms，每次 read 小于分配预算 | 观察 HTTP 是否超过总截止时间；记录最终状态、请求数量、工具数量和 usage，不能先验标记为已修复 |
| Agent 协作取消 | 正在连续接收时请求取消 | 记录请求取消时 `stopped`、实际返回时间与退出后的 `stopped`；不能把本地取消请求当作供应商终止 |

所有网络地址由脚本创建的 `ThreadingHTTPServer(('127.0.0.1', 0), ...)` 产生，不接受外部地址。通过 `HTTPModel.__new__` 注入本地假模型参数，避免读取 `.env`；NO_PROXY 对本次进程的 loopback 显式设置。凭据为固定本地占位值，输出不含 HTTP 头、请求/响应正文或凭据。延迟为缩小比例的工程复现，不与真实供应商时延作数值比较。

每个场景只发一个请求，无自动重试。脚本记录 Python/HTTPX/HTTPCore 版本、协议和源码 SHA256、事件相对时间、受控服务请求数、客户端头/首块观察、完整响应与 usage 状态。首次应用层 body chunk 观察不是物理网卡首字节、模型首 token 或 provider 队列长度。允许小范围调度抖动；测试判定主要根据状态和事件顺序，不设置毫秒级性能门槛。

脚本必须在空的新输出目录执行；重复运行需要新目录，不能覆盖首次证据。运行命令：

```sh
.venv/bin/python scripts/diagnose_http_timeout_round16.py \
  --output docs/evaluations/conversation-round16/timeout-run
```

## 历史证据与未知项

保留 Round15 `run/agent-final.json`：r1 完整响应 25374.305 ms、已知 2410 tokens；本地检索 14.931 ms；r2 分配 34597.896 ms，实际 35045.431 ms 后 `ReadTimeout`，remaining 19552.480 ms。r2 分配来自约 54.598 s 剩余时间减 20 s 审核预留，不是 80 s 总预算耗尽。r2 没有完整响应，usage 和供应商受理/计费未知。历史 telemetry 没有头/首块观测，因此无法确认是排队、计算、网络还是响应体停顿。

HTTPX 官方说明 read timeout 限制等待下一块数据的时长；HTTPcore trace 可以观察传输阶段，事件接口可能随版本变化。本脚本只保存事件名和时间，不保存 trace 参数。[HTTPX timeouts](https://www.python-httpx.org/advanced/timeouts/)、[HTTPcore trace extension](https://www.encode.io/httpcore/extensions/)。

若发现当前调用不是绝对截止时间或立即取消，须记录为仍存在的运行时限制；本轮诊断不自行更改模型实现、不补跑付费模型。

## 首次运行后的配置补充（v2 运行前固定）

首次 `timeout-run` 保留了 6 个已运行的 HTTP 场景；两个 Agent 场景在发送请求前被新加入的默认开启服务身份检查拒绝。该失败是诊断脚本没有配置合成服务身份，不是模型超时。v2 显式提供固定的合成 `LABLENS_SERVICE_KEY`，仍只使用本地服务、仍不读取 `.env`。第一次的脚本、协议、原始结果和失败说明留在原目录；第二次输出到 `timeout-run-v2`。生产服务身份配置不因本次探针而改变。
