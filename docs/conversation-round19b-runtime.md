# 第十九轮续实跑：GPU 虚机 CPU 指令集排障

2026-11-11。**本次运行在模型引擎启动阶段失败，真实模型请求 0 次；兼容检查和完整 Agent 均未开始。** 实例、存储、临时 SSH key 与本地隔离进程已清理，主服务未升级。收尾仍为 **7/12 轮完整交付、第 8 轮部分完成**。

## 运行范围与结果

用户授权单张 A6000 48GB 级、100GB 磁盘，先做 3 次兼容检查，通过后才做一道已知回归 Agent 题、最多再调用 3 次；不重试，总上限 2 小时 / 10 美元，仍受 500 元总预算约束。实例 `60163927` 的运行源码为 `fb187b9c4a35b33222486d4aba1271e40c97d79f`。

沿用固定 Qwen2.5-7B-Instruct revision `a09a35458c702b33eeacc393d103063234e8bc28`、vLLM 0.32.0 / 包版本 `0.32.0+cu129`，镜像 digest `f99aa709b3f873aa1e1aadf5c1db35970119e8af9b36fe15d2a667aae0ed005c`，以及已固定 SHA 的 TorchCodec `0.17.0+cpu` 覆盖层。基础镜像加覆盖层的身份分别记录；本次未到模型权重和服务身份验收阶段。

| 阶段 | 本次观察 | 证据 |
| --- | --- | --- |
| 准备 | 冻结 3+3 请求计划、新资料快照和源码；首次本地准备失败原件保留，第二次等待认证服务就绪后准备成功 | [冻结协议](evaluations/conversation-round19b-runtime/runtime-protocol.json)、[首次准备失败](evaluations/conversation-round19b-runtime/local-prepare-failure.json) |
| 部署 | 初次上传因 `/workspace` 不存在而失败，创建父目录后上传成功；CPU codec 安装与导入成功 | [两次上传回执](evaluations/conversation-round19b-runtime/remote-evidence/01-upload/receipt.json)、[修复后回执](evaluations/conversation-round19b-runtime/remote-evidence/02-upload-parent-fixed/receipt.json)、[覆盖层](evaluations/conversation-round19b-runtime/public-ready/codec-overlay.json) |
| 原预检 | GPU、驱动、包版本、内存和磁盘等检查通过，但当时未检查 CPU 指令集 | [原始 host check](evaluations/conversation-round19b-runtime/public-ready/hostcheck.json) |
| 服务启动 | 04:41:28 UTC 启动；约 44.873 秒后 exit 1，UCX 报 AVX 不支持并导致 engine 初始化失败 | [启动失败和日志摘录](evaluations/conversation-round19b-runtime/public-ready/remote-startup-failure.json) |
| 推理 | 未 ready；未核验模型身份；兼容 / Agent 请求均为 0，没有报告、质量或吞吐结果 | [运行结论](evaluations/conversation-round19b-runtime/public-ready/run-outcome.json)、[就绪记录](evaluations/conversation-round19b-runtime/public-ready/model-readiness.json) |
| 清理 | 单次 DELETE 获 HTTP 200 后，独立在控制台刷新核对实例 0、卷 0、临时 key 已删除、当前支出率 $0/h | [供应商清理](evaluations/conversation-round19b-runtime/public-ready/provider-cleanup.json)、[本地清理](evaluations/conversation-round19b-runtime/public-ready/local-cleanup.json) |

租用意图 04:26:34 UTC 至删除确认 04:45:31 UTC，约 **18 分 57 秒**。控制台该实例账目暂记 **$0.19**，可能延迟结算；这是实例条目，不是账户余额差。创建时报价约 $0.895/h，不作为最终账单。[费用回执](evaluations/conversation-round19b-runtime/public-ready/billing-observation.json)

## 排查：什么已确认，什么仍未知

原日志明确报告 `UCX library was compiled with avx but CPU does not support it`。随后只读检查发现，该虚机呈现 `QEMU Virtual CPU version 2.5+`，20 个逻辑 CPU 均未暴露 `avx`；本次 loaded UCX 的要求与虚机可见指令集不兼容。原始日志 SHA、行号、CPU 观察与进程退出回执一并保留，公开文件只包含脱敏摘要。

这足以解释本次引擎启动阻断。CPU codec 安装和导入成功，不能因此宣称所有原生库兼容；同型号 GPU、驱动及版本检查通过，也不能保证不同宿主虚机的 CPU 能力一致。尚未确认物理宿主 CPU 是否支持 AVX、虚拟化为何屏蔽该特性，以及换到暴露 AVX 的机器后是否还会遇到其他依赖问题。没有通过关闭 UCX 或修改运行栈绕过错误，也没有重启筛选成功结果。

首次本地准备仅留下通用失败，未捕获精确异常；等待认证就绪后成功只是后续观察，不能倒推确认其根因。上传缺失父目录则有明确异常，可单独确认。独立 AI 复核认可启动阻断与零请求的证据边界；它不是人工盲测，也未独立核对平台账单。[复核记录](evaluations/conversation-round19b-runtime/public-ready/independent-startup-review.md)

## 离线修复与验证

在故障证据封存后，为当前固定镜像补充启动前 CPU 检查：读取有上限的 Linux `/proc/cpuinfo`，要求**每个**逻辑 CPU 都有精确 `avx` 标记。缺失、无法读取、格式异常或超过 8 MiB 时拒绝启动；记录状态、交集和完整文件哈希，不保留原始主机信息。默认打印部署计划不读宿主文件，也不发请求或启动进程。

复现用例使用本次 20 CPU 的特征：旧检查全部通过，新检查单独因 AVX 缺失拒绝，且不会调用 `Popen`。另覆盖混合 CPU 能力、读取失败、畸形 / 超大数据和合法多核信息。AVX 是当前 UCX 已确认的必要条件；该检查不等于完整 ISA 兼容认证。

- 部署脚本专项：92 项通过；加强回归断言后，单例再次通过。
- 最终 Python 全量：**2,485 项通过，1 项既有弃用警告，75.94 秒**。
- 独立代码复核未发现需修复问题；没有再次租 GPU 验证新 guard。

[工程回执](evaluations/conversation-round19b-runtime/public-ready/engineering-checks.json)。本次执行过的旧 launcher 单独封存，后置修复不覆盖原来的预检“通过”记录，也不重判历史失败；下一次真实运行必须重新冻结源码和计划。[已执行 launcher](evaluations/conversation-round19b-runtime/frozen-source/scripts/private_model_deploy.py)

## 简历与后续

可陈述：在预算受控的真实 GPU 部署中，通过原生日志与虚机 CPU 特征交叉定位 UCX / AVX 启动故障；将排查结论转成启动前拒绝和回归测试，保留版本、失败、费用与资源释放证据。它体现部署兼容性诊断和故障闭环；不能称为生产线上事故、模型质量提升或完整私有 Agent 验收。

上一次 GPU 运行的 3 请求 / 2 通过 / 1 协议失败保持原判；[本地 43 项跨服务检查](conversation-round19b.md)仍仅为固定响应工程验证。本次零推理没有提供新的语义样本。下一步最小范围仍是：新冻结候选、核验租用虚机 AVX 与固定环境、3 次兼容通过后再做最多 3 次模型请求的已知 Agent 回归。此次租用已结束，后续新的付费运行需单独明确授权；新题、模型容量和生产流量能力仍待验收，详见[十二轮计划](finalization-12-rounds.md)。

公开材料通过显式文件白名单导出，未包含密钥、SSH 地址、私有环境、完整原始服务日志或其他账户账目。文件哈希只证明内容对应，不代表签名来源或验收成功。[导出清单](evaluations/conversation-round19b-runtime/manifest.json)
