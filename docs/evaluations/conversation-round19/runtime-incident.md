# Round19 部署问题记录：版本预检与 TorchCodec 动态库

记录日期：2026-11-11。**GPU 部署启动成功；第三候选的三次兼容请求全部完成，但整体验收未通过，Agent 尚未接通。** 前两次失败、实际补丁、重新冻结和固定文件哈希核验均已记录；实例、存储和临时 SSH key 已核对清理，平台当前记录约 $0.38，最终结算可能延迟。不能用主机预检、导入检查、健康检查或工程单测通过替代推理验收。

本次已批准 Vast 单张 A6000、100 GB 磁盘，最多 2 小时、总费用不超过 10 USD，项目总预算为 500 CNY。Offer 为 `51092051`，instance 为 `60154954`，创建意图记录于 `02:40:30 UTC`，平台 `start_date=1794364838.0060368` 对应创建时间约为 `02:40:38 UTC`。固定模型 revision 与镜像 digest 没有因下面的版本预检修复而改变。

## 1. 发行版本与安装包版本混淆

原 profile 的 `vllm_version=0.32.0` 被直接用于比较 `importlib.metadata.version('vllm')`。实际固定 CUDA 12.9 镜像返回 `0.32.0+cu129`，导致唯一的失败项为 `vllm_version_matches=false`。第一候选因此在启动模型前被拦截，真实推理请求为 0。

原始预检同时确认以下环境；这些值仅证明主机条件，不是容量或性能指标：

| 项目 | 实际预检值 |
| --- | --- |
| GPU | 一张 NVIDIA RTX A6000 |
| GPU 显存 | 49,140 MiB |
| 驱动 / compute capability | 580.178.04 / 8.6 |
| 容器可见内存检查值 | 113,625 MiB；不是 offer 的 48 GB 分配额，也不是实时空闲内存 |
| 启动目录可用磁盘 | 99.978 GiB |
| vLLM 安装包版本 | `0.32.0+cu129` |

官方依据支持该后缀，并非看到现场输出后任意放宽版本检查：

- [官方 v0.32.0 release API](https://api.github.com/repos/vllm-project/vllm/releases/tags/v0.32.0) 包含 `vllm-0.32.0+cu129-cp38-abi3-manylinux_2_28_x86_64.whl`。
- [固定提交的版本生成代码](https://github.com/vllm-project/vllm/blob/5df63a7203cdb9328d824a27b682e77c7b9928be/setup.py#L1347-L1380) 会给非主 CUDA 构建添加相应本地版本后缀；[同一提交的发布流水线](https://github.com/vllm-project/vllm/blob/5df63a7203cdb9328d824a27b682e77c7b9928be/.buildkite/release-pipeline.yaml#L446-L463) 为本次变体指定 CUDA 12.9.1、Ubuntu 24.04。
- 官方 registry manifest 与 config 的原始 body 哈希再次匹配预期。config 中 CUDA 为 12.9.1、构建提交为 `5df63a7203cdb9328d824a27b682e77c7b9928be`；config 本身没有安装包版本字符串，不能单独证明安装包版本。

完整 URL、响应字节数、实算哈希、wheel 的 API 预期哈希和证据边界见 [vllm-distribution-evidence.json](vllm-distribution-evidence.json)。该资料获取仅涉及小型元数据与源码；没有为此下载发行 wheel 或镜像层。JSON 文件 SHA256 为 `b49f9924515ba93a01b2ebe004c7a6d256d065184005acb730841f04f3deb013`。

修复在 [profile](../../../deploy/private-model/profile.json) 中保留发行标签 `vllm_version=0.32.0`，新增 `vllm_distribution_version=0.32.0+cu129`。[launcher](../../../scripts/private_model_deploy.py) 对已核验的 `(release, CUDA)` 使用精确映射，并将实际安装包版本与完整字符串比较。没有去除 `+cu129`，也没有使用版本前缀或范围比较来接纳其他构建。

修正后的远程 host-check 全项通过。版本修复阶段的工程回归为 launcher 50 项通过、项目全量 2258 项通过（73.38 秒，由主线程执行）；这些是工程检查，不是 GPU 推理、模型回答或 Agent 质量指标。

## 2. 保留失败并重新冻结

以下私有原件继续保留在本地忽略目录 `.local/round19/instance-60154954/`；经白名单筛选的副本已经公开到本目录的 `runtime/`，可从[运行报告](runtime-run.md)逐项访问。表内仍使用私有归档路径，哈希用于关联原件；凭据与完整原始服务日志不公开，公开日志仅保留必要脱敏摘录。

| 对象 | 记录／SHA256 |
| --- | --- |
| 首次预检 | `host-check-original.json`；`5e98b4a0481dde48af79205362590de91e253a585fd74d66a68b23d031f501c9` |
| 首次失败分类 | `preflight-failure-01.json`；`aa5878c8f05adb18c440b4eeb649eceee4edfa31b7d32e47db9fecf7b30351ab` |
| 原 profile | `profile-original.json`；`66e9a4c358a0ea9c085c53d03c00443b89c79bacb4c27ff61362623deea5aae9` |
| 旧冻结候选 | `probe-01/plan.json`；`b3896c38a8a031605c00c95326e375b2d7d2a8ba76bc6b77758b46b7af963015`；未执行 |
| 修正后 profile | `441e7469f71fa58075d78e5e08b135bc2eb5935cd9d9b9d5142573b25618c12c` |
| 新冻结候选 | `probe-02/plan.json`；`14f2cef78e8b282a5e1046d56f5741befa5dfdb94ae563af027c34cca33daf84`；截至 server-02 失败仍未执行 |
| 修正后远程预检回执 | `host-check-corrected.receipt.txt`；`f53e24426c8c1bf9e039ce7125bfd5fbf335484599e75f6959354e9c1adbf62f` |

旧候选没有被覆盖或补写成成功；新候选绑定修改后的完整 profile 哈希。模型 revision 与固定基础镜像保持一致。任何后续运行依赖补丁也应明确记为基础镜像上的运行环境变更，并新建候选，不能复用旧计划隐瞒环境变化。

## 3. 通过预检后，服务仍在导入阶段失败

`server-02` 在 11.678 秒后以 code 1 退出；退出回执确认进程组清理完成，`rental_billing_stopped=false`。尚未进入模型就绪状态，该阶段没有下载模型权重、没有发送推理请求。实际报错为 `libnvrtc.so.13` 缺失，随后指出无法加载 `torchcodec/libtorchcodec_image.so`。

现场安装版本为 vLLM `0.32.0+cu129`、torch `2.13.0+cu129`、torchcodec `0.17.0`、transformers `5.19.0`、xgrammar `0.2.8`。该 TorchCodec 动态库的 `ldd` 结果还列出 `libcudart.so.13` 缺失，和 CUDA 12.9 基础镜像不匹配。`ldd` 中其他 libtorch/libc10 搜索结果不能直接当作独立根因，因为 PyTorch 导入时的动态库加载上下文不同。

原件仍保留在同一私有目录：

- `server-02-export/exit.json`：SHA256 `aca8d919994064b7fa475a351b93522863f1a348fdd2fc0deaa07b17803d1cac`。
- `server-02-initial.redacted.log`：SHA256 `162f26080cebe90147c84497efa2c2a2ea4a230e1202ac9a1b316e6d6508f881`；第 7、51 行分别保留缺库和目标库错误。
- `runtime-dependency-original.json`：SHA256 `2cf91285433a0200cd2bd23d7386c76a9fcfd3727833d342d1fa2a9f95684051`，包含安装包版本及动态库核验。

[上游 issue #63121](https://github.com/vllm-project/vllm/issues/63121) 报告了相同 CUDA 12.9 镜像与 CUDA 13 TorchCodec 的启动冲突，并提供了另一环境的 A/B 结果。本次判断同时依据本机 traceback 与动态库检查，不把上游报告本身当作本机修复成功的证据。

公开可复核的[第二候选最小错误摘录](runtime/server-02/error-excerpts.txt)和[退出回执](runtime/server-02/exit.json)分别保留缺库错误与失败终态。

实际修复仅将 TorchCodec 换为官方 `0.17.0+cpu` 构建，保留当前 CUDA PyTorch、vLLM 和模型，用于这轮纯文本请求。[固定提交的官方兼容矩阵](https://github.com/meta-pytorch/torchcodec/blob/742b6b157ca22a15d22ed6db40286b226e340bd4/README.md#L215-L221) 将 TorchCodec 0.17 对应到 torch ≥2.11、Python 3.10–3.14，涵盖本机 torch 2.13 / Python 3.12.3。[官方 CPU 索引](https://download.pytorch.org/whl/cpu/torchcodec/)的 cp312 manylinux x86_64 wheel 预期 SHA256 为 `de8e58e5f30692ee7cf92b1b313d0ffdeaac95148525ef2a865067fca0255246`。官方小型元数据、固定矩阵提交、PEP658 sidecar 实算哈希及另一个 CUDA 12.9 方案的比较保存于 [torchcodec-overlay-evidence.json](torchcodec-overlay-evidence.json)；下述安装回执另外证明了本次实际下载与安装。

`03:09:47.899323 UTC` 的实际安装回执记录 wheel 为 5,573,051 字节，实算 SHA256 与上述预期一致。命令为隔离 pip 从本地已校验 wheel 执行 `--no-index --no-deps --force-reinstall`，返回码为 0。安装后 vLLM `0.32.0+cu129`、torch `2.13.0+cu129`、torchvision `0.28.0+cu129`、torchaudio `2.11.0+cu129`、transformers `5.19.0`、xgrammar `0.2.8` 均未改变，只有 TorchCodec 从 `0.17.0` 变为 `0.17.0+cpu`。

这是**固定基础镜像加显式运行依赖补丁**，不是原始官方镜像直接成功；profile 已明确 `base_image_pristine=false`，固定 wheel URL／SHA256／大小，并对 torch 与 torchcodec 增加精确版本预检。CPU 构建不提供 TorchCodec GPU 编解码能力，本轮不验收音视频或该功能。依赖文件没有指定 torch 版本，不能用“安装解析无报错”替代动态链接和服务启动检查。

主线程随后验证 TorchCodec 导入通过，重新冻结 `probe-03`，并于 `03:11:46.287482 UTC` 启动第三候选（launcher PID 915）。该启动回执本身只证明已发起进程；主线程之后另外观察到 `03:15:11 UTC` 服务 ready、权重加载完成日志及健康 GET 200。截至该观察截面未发送真实推理请求，健康检查不计为三次 probe。

`pre-probe03-readonly-observations.txt` 中的 `03:18:09 UTC` 记录再次确认 TorchCodec `AudioDecoder` 可导入，但没有执行音频解码；同一时刻的 GPU 采样为使用 43,695 / 总计 49,140 MiB、利用率 0%。这是单次整卡采样，不是模型权重大小、峰值显存、吞吐或负载容量测试。

| 第三候选证据 | SHA256／内容 |
| --- | --- |
| 新 profile | `744cbf609fcc61b0d2704a9db6e9cfaaddcc410f060eacfd26172864c45f3e1c` |
| `probe-03/plan.json` | `90c06e1a9ff049a4a5bd51a801e6dc7d184bd55cd5203c1557b023d04962d99e` |
| `early-export/codec-overlay-01/intent.json` | `d889a32e194b7afb860473156cca5911c673cf51325f5c05ee855821d2ed22f2` |
| `early-export/codec-overlay-01/install-receipt.json` | `786ba4b0d3358401bef1655cbb96ea86432c212670c4d7312a955f41c3b2ae2a` |
| `early-export/launcher-03-start.json` | `762a1146a3b2bdeb3b031f141b3c0f6b1351e6945ae4412eb2b8a2aa699e30da` |

表内路径仍相对于 `.local/round19/instance-60154954/`。复现补丁的完整命令见 [部署手册](../../private-model-deployment.md)。候选 01、02 的计划、预检与导入失败都保留，补丁没有额外增加已批准的推理次数，也不会重置实例计费窗口。

首次第三候选身份核验 `early-export/identity-03-original.json`（SHA256 `41ad9610ff30c72897314888caaabdf3a3e6d98e1d093685594997fa615dfdfa`）中，config、tokenizer_config、索引、LICENSE 和 chat template 均匹配；LICENSE 已由模型下载流程提供，无需人工补齐。四个权重文件在读取前被路径防护判为 `unexpected_symlink_target`，因此该次实际权重哈希和字节数均未计算。这是核验路径尚未支持的拒绝结果，不能写成权重哈希不匹配。

排查实际缓存布局后，[核验器](../../../scripts/verify_private_model_identity.py) 增加严格的 HF 两跳 shared-cache 路径支持，保留原拒绝结果，并通过 11 项文件系统测试。二次核验 `identity-03-shared-cache.json`（SHA256 `8eb26e6e24b2ac034c77076ae27637097a69a0cf4eb3e8c13149350eea41dc87`）耗时 39.837 秒：四个权重的字节数／SHA256、四份固定 metadata、chat template 与权重索引引用全部匹配。所用核验器 SHA256 为 `48760ed0f8154ee9d8130ddc1c2aa4b269839d9778f011e1b96dc76893bc6f27`。

该通过范围是固定本地文件字节。`tokenizer_payload_identity_verified`、`live_server_loaded_identity_verified` 和 `image_identity_verified` 仍为 false：tokenizer 数据文件没有预期哈希，运行中服务与基础镜像身份也没有由该离线核验器独立认证。probe summary 不执行文件身份核验，其 `model_weights_identity_verified=false` 也不应被改写；本地文件核验另由上述原件证明。

## 4. 实例连接阶段的两个边界

平台状态接口首次访问不带末尾斜杠的实例路由时返回 301，主线程改为明确的 `https://console.vast.ai/api/v0/instances/60154954/` 后继续核对该实例。此处是平台 API 路由修正，不是推理请求重试，也没有以无条件跟随任意跳转来修改 probe 的 redirect 策略。

SSH shell 没有继承所需的平台容器认证变量时，主线程已验证从 `/proc/1/environ` 仅筛取 `CONTAINER_ID` 与 `CONTAINER_API_KEY` 的实例限定 fallback。记录只保留变量名称，不发布变量值或全量进程环境；这与 vLLM 的自建服务鉴权 key 是不同用途。

## 三次兼容请求结果与剩余边界

唯一执行的 `probe-03` 没有自动重试，完成三次请求后停止；三个响应均为 HTTP 200、完整 body、`finish_reason=stop`，usage 可解析且计数一致。真实结果为：

| 请求 | 结果 | prompt / completion / total tokens | 验收含义 |
| --- | --- | --- | --- |
| 中文连通 | passed | 39 / 5 / 44 | 指定中文输出通过 |
| workspace_action | passed | 69 / 53 / 122 | 选出合法 `search_workspace`，但未真实执行搜索工具 |
| scoped_review | structured_contract_failed | 3249 / 178 / 3427 | a1同时保留和替换，生产报告恢复校验拒绝；完整原失败保留 |

合计 prompt 3357、completion 236、total 3593 tokens，均为服务端自报 usage；三次 probe 墙钟合计 17.275529 秒。这是一次串行兼容检查，不是 TTFT、稳定吞吐、并发容量或语义质量评测。总体状态为 `blocked`，不能标记 `compatibility_passed`，也不能将“2/3”写成回答准确率。

原件为 `probe-03/run/summary.json`（SHA256 `f296697c72762c16ca8c77c58024928ce393aa44af66c42a4ef509dc3082e405`）、同目录 manifest、逐次 intent 和脱敏响应。执行所绑定 plan SHA256 为 `90c06e1a9ff049a4a5bd51a801e6dc7d184bd55cd5203c1557b023d04962d99e`，磁盘计划未漂移；旧候选及第三项失败均未覆盖。

离线重放确认：`a1` 同时出现在 `retained_assessment_ids` 与替换 assessments，生产 `restore_review_delta` 正确拒绝重叠决策。原始 JSON 与字段约束检查通过，冻结 schema 未表达跨列表互斥；不能说 XGrammar 违反 schema。两种内存反事实能继续机械校验，只用于定位，没有回写原响应或重算通过。原始输出未执行到后续复核与语义检查，详见[逐层失败分析](scoped-review-failure-analysis.md)。新增失败样本回归仍要求生产端拒绝该原输出。

最终工程全量为2292项通过、1项上游弃用警告、73.77秒，见[完整输出（路径脱敏）](python-tests-final.txt)。公开日志将本机绝对路径替换为 `<CHECKOUT>`，测试结果未改动。此前2240/2258/2280阶段结果、11项身份文件系统检查与1项失败样本回归均保留；本地工程通过不改变真实模型的blocked判定。

当前已确认固定补丁后的 GPU 服务可启动，固定本地模型文件匹配，但模型报告兼容性整体验收未通过，未追加调用。实例 `60154954` 的 DELETE 于 `03:22:16 UTC` 返回200后，另行回读平台为0实例、0存储；`03:23 UTC` 本轮临时SSH key移除后列表为空，隧道被远端关闭。[清理记录](runtime/cleanup-observation.json)与[销毁回执](runtime/provider-destroy-01.jsonl)分开记录请求确认和平台观察，未用进程退出推断云端释放。

平台当前该实例费用约$0.38，当前spending rate为0。页面金额经舍入且近期账单可能延迟，仍不是最终结算发票；详见[费用观察](runtime/billing-observation.json)与[运行报告](runtime-run.md)。

完整 Agent 工具执行、历史刷新与语义评估没有在本轮接通或验收，`semantic_acceptance=not_evaluated`。模型进程退出与进程组清理不能证明云端计费已经停止。
