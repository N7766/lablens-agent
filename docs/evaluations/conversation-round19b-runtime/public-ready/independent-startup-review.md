# 独立启动故障审查

审查时间：2026-11-11T04:48:04.830040+00:00。结论：**服务启动失败，兼容性与 Agent 语义验收均未进入**。本审查只读取本地冻结证据，没有请求模型、平台或 UI，也没有修改实现。这是独立 AI 审查，非人工盲测。

## 已确认

- 服务退出码 `1`，运行 `44.873 s`，健康观察记录为 `server_exited`。
- 服务日志第 28 行：`FATAL: UCX library was compiled with avx but CPU does not support it.`；第 29 行出现 native segfault，第 187 行为 engine-core 初始化失败。
- CPU 观测（2026-11-11T04:44:52.647424+00:00）识别 20 个 QEMU 虚拟 CPU；全部没有 `avx` 标志，记录的特性集合也没有 `avx2`、`xsave`。
- 本次冻结主机预检通过了平台、固定包版本、GPU/驱动、RAM、磁盘检查，却没有 CPU 指令集检查。检查时读取的 launcher 源码与运行前 freeze 一致：`2a38b5b9b60c98103558f895be32550aa6c9f80ef3617345fb02da4d43738d0e`。本结论针对该冻结版本，不描述随后可能进行的修复。
- `torch`/`torchcodec` 导入成功并不覆盖引擎随后加载的全部本地库。
- 预清理观测显示 `tasks=0`，两个 gateway phase 都未启动、没有账本与 listener，compat/Agent run 目录均不存在。因此本轮 **0 次 compatibility completion、0 次 Agent completion**，无真实报告可判语义。
- launcher 记录其进程组已清理；后续本地观测显示隔离 JVM/worker/Redis 进程与端口均不存在，主服务 PID 与健康未变。此处不据此推断计费停止。

## 证据支持的解释与边界

当前 guest 暴露的 CPU 特性不满足当前 UCX 二进制在启动中明确检查的 AVX 条件。首个明确 fatal 诊断与 CPU flags 相互印证，支持将本轮归类为**主机与本地依赖不兼容导致的启动阻断**。

尚不能确认物理 host 缺少 AVX，还是虚拟化层隐藏了特性，也不能解释供应商为何采用该虚拟 CPU 型号。没有依据宣称仅补 AVX 就保证整个固定栈成功启动，或把 AVX2 提升为本次故障已证明的硬要求。未完成权重完整性/推理验收，没有模型质量、review v4 成功率或 Agent 语义结果。

## 最小离线改进建议

未来 `host-check` 读取 `/proc/cpuinfo`，要求非空处理器记录中每个处理器的 flags 都包含精确 `avx` token；缺失、不可读、空白或异常格式关闭准入。在 `Popen` 之前输出明确失败诊断。覆盖全部有 AVX、QEMU 无 AVX、混合处理器、只有 AVX2/近似 token、文件异常几类回归。

此检查只防止已观察到的启动缺口，不能替代完整引擎启动与推理验收。本审查不修改实现、不追改本次冻结资料。

## 清理、费用与来源

本审查未核对平台销毁、临时 SSH key 移除或实际账单；这些需要根线程单独凭据。进程退出不代表计费停止。

完整结论与源路径/SHA256 见同目录 `independent-startup-review.json`。主要来源：

- server log：`63f4eb1996c5f6ed143676e8c6fdc867a606aae35e06d217678fcb3a5144c65d`，仅引用第 28、29、187 行短诊断。
- CPU feature observation：`5904be82727c37a6f55fb67321d4b33ef46d63324f373f381391e85e1c0aef69`。
- server exit：`9e99405082f54715fb8db53b58757651e33430b3caa49dac23a6b2e42e89f091`。
- host check：`f1410d5211e3667ce4d707a357c6e1a11e742db11591f8714096b8bbe2b5edc4`。
- zero-call observation：`9d5358ea0a339acc2c540603f03ec90ad2ba410ea107d09b4a2df85afda8214a`。

公开候选文件未复制 IP、主机/容器名、账号/SSH 标识、密钥值、环境变量转储或完整原始日志。
