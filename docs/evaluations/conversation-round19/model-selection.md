# Round19：私有模型候选与官方元数据核验

核验日期：2026-11-11。状态：**模型、镜像和启动参数已固定；GPU 部署、显存和回答质量均未实测。** 本次只获取官方小型元数据，不下载权重或镜像层，不发送推理请求。

## 候选与项目接口

选择 `Qwen/Qwen2.5-7B-Instruct` BF16，模型及 tokenizer revision 均为 `a09a35458c702b33eeacc393d103063234e8bc28`；HF 官方 API 返回该提交、7,615,616,512 个 BF16 参数、非 gated 和 Apache-2.0 许可证。这个选择用于先跑通现有协议，不表示它优于当前云模型。[固定提交模型页](https://huggingface.co/Qwen/Qwen2.5-7B-Instruct/tree/a09a35458c702b33eeacc393d103063234e8bc28)、[官方 revision API](https://huggingface.co/api/models/Qwen/Qwen2.5-7B-Instruct/revision/a09a35458c702b33eeacc393d103063234e8bc28)

已检查本项目 `lablens/model.py`、`workspace_agent.py`、`rag.py`：真实 HTTPModel 的 WorkspaceAgent 决策使用 `response_format.type=json_schema` 的 `json_schema_action_v1`，返回 `decision.action` 与 `decision.arguments`，由本地校验后选工具；最终报告也走 JSON Schema。vLLM 0.32.0 的 Chat Completions 接口支持该字段。但“支持 JSON Schema”仍不足以证明项目的完整 `anyOf`、枚举及评审 schema 能成功生成，需要部署后原样发请求验证。[vLLM 结构化输出](https://docs.vllm.ai/en/v0.32.0/features/structured_outputs/)

主路径不启用 native tool parser。旧 native tools 路径如果单独验收，可使用 `--enable-auto-tool-choice --tool-call-parser hermes`；Qwen2.5 的固定 tokenizer 配置自带 Hermes 风格模板，不需覆盖模板。不得把 native tool calling 的通过率当作本项目 structured-action 链路通过率。[vLLM Qwen 工具调用说明](https://docs.vllm.ai/en/v0.32.0/features/tool_calling/#qwen-models)

## 镜像固定与实际核验

固定版本 `vllm/vllm-openai:v0.32.0-cu129-ubuntu2404`。官方 release 存在这个 CUDA 12.9 变体；不能把未加后缀的默认 CUDA 13 镜像视为同一候选。[vLLM 0.32.0 release](https://github.com/vllm-project/vllm/releases/tag/v0.32.0)

本次通过 Docker Hub 公共 token，仅访问 `registry-1.docker.io/v2/vllm/vllm-openai/manifests/...` 和 44,887 字节的 image config blob。对接收的原始响应字节计算 SHA-256，index 与 manifest 同时比对 `Docker-Content-Digest`；config 比对 manifest 中的预期 digest，全部一致。没有请求任一 layer blob。

| 对象 | 核验后的 SHA-256 |
| --- | --- |
| 多平台 index | `9fea04bb6f2deb71db19b7cff586cefb56731a26884aa1cec60690d4e6463cf2` |
| linux/amd64 manifest（8,679 bytes） | `f99aa709b3f873aa1e1aadf5c1db35970119e8af9b36fe15d2a667aae0ed005c` |
| image config（44,887 bytes） | `9ac2d674ffef3b40b5462c83e25972ae449b5e7873848df47ad23b30f07714a7` |

部署应使用 `vllm/vllm-openai@sha256:f99aa709b3f873aa1e1aadf5c1db35970119e8af9b36fe15d2a667aae0ed005c`，平台为 `linux/amd64`；版本标签只作说明。registry config 确认 `CUDA_VERSION=12.9.1`、默认 entrypoint 为 `["vllm", "serve"]`、工作目录 `/vllm-workspace`，build commit 为 `5df63a7203cdb9328d824a27b682e77c7b9928be`，与 GitHub `v0.32.0` tag 指向的 commit 一致。manifest 声明 40 个压缩层，总计 11,182,268,295 bytes；这是元数据统计，不是本次下载量。[官方 Docker Hub 镜像记录](https://hub.docker.com/layers/vllm/vllm-openai/v0.32.0-cu129-ubuntu2404/images/sha256-f99aa709b3f873aa1e1aadf5c1db35970119e8af9b36fe15d2a667aae0ed005c)、[GitHub 固定 tag](https://api.github.com/repos/vllm-project/vllm/git/ref/tags/v0.32.0)

## 固定模型元数据

以下为从固定 revision 下载的小文件原始字节 SHA-256；chat template 则为解析 JSON 后模板字符串的 UTF-8 SHA-256。模型 shard 的 expected SHA-256 取自 HF 官方 LFS 元数据，列在 `deploy/private-model/profile.json`；**尚未下载权重核对这些 shard 哈希**。

| 文件 / 内容 | bytes | SHA-256 |
| --- | ---: | --- |
| `config.json` | 663 | `7463bb0ea78315365e6c6b74de4e73bbcc8359dfb0c5a737584e077d42c0b03c` |
| `tokenizer_config.json` | 7,305 | `5b5d4f65d0acd3b2d56a35b56d374a36cbc1c8fa5cf3b3febbbfabf22f359583` |
| `model.safetensors.index.json` | 27,752 | `624bf7c47cd12468fdc16e38a47cf4f19e0415b859a223ba3c027eed2f0e1028` |
| `LICENSE` | 11,343 | `832dd9e00a68dd83b3c3fb9f5588dad7dcf337a0db50f7d9483f310cd292e92e` |
| 解析后的 chat template | — | `cd8e9439f0570856fd70470bf8889ebd8b5d1107207f67a5efb46e342330527f` |

来源：[固定 config](https://huggingface.co/Qwen/Qwen2.5-7B-Instruct/blob/a09a35458c702b33eeacc393d103063234e8bc28/config.json)、[固定 tokenizer config](https://huggingface.co/Qwen/Qwen2.5-7B-Instruct/blob/a09a35458c702b33eeacc393d103063234e8bc28/tokenizer_config.json)、[固定权重索引](https://huggingface.co/Qwen/Qwen2.5-7B-Instruct/blob/a09a35458c702b33eeacc393d103063234e8bc28/model.safetensors.index.json)、[固定许可证](https://huggingface.co/Qwen/Qwen2.5-7B-Instruct/blob/a09a35458c702b33eeacc393d103063234e8bc28/LICENSE)、[固定树/LFS API](https://huggingface.co/api/models/Qwen/Qwen2.5-7B-Instruct/tree/a09a35458c702b33eeacc393d103063234e8bc28)。

## 24GB 单卡的估算与边界

固定 config 为 28 层、28 个 attention heads、4 个 KV heads、hidden size 3584，head dimension = 3584 / 28 = 128；最大位置长度 32768，无需扩展 RoPE。基于这些配置计算：

```text
BF16 权重张量 = 7,615,616,512 × 2 = 15,231,233,024 bytes ≈ 14.185 GiB
每 token 的 BF16 KV = K/V 2 × 28 层 × 4 KV heads × 128 × 2 bytes = 57,344 bytes
单序列 32768 token KV = 1,879,048,192 bytes = 1.75 GiB
权重张量 + 该序列 KV ≈ 15.935 GiB
```

这是配置推算，未包含 activation、临时工作区、CUDA runtime、allocator 碎片和框架其他分配，也不是 vLLM 实际缓存分配量。假设设备实际报告 24 GiB、利用率 0.9，理论剩余约 5.665 GiB；实际租机需读 `nvidia-smi`，23,000 MiB 只是预检下限。以 BF16、单序列、eager、chunked prefill 2048 启动属于可试候选，不能承诺不 OOM、吞吐或端到端时延。若更改精度/长度/并发，另立候选，保留原失败。

本项目 `conversation.estimated_units` 按序列化 UTF-8 字节及 framing 保守估算，并非 tokenizer。应将隔离 worker 的 `LABLENS_CONTEXT_WINDOW_TOKENS=32768` 与本候选对应；不能保留原默认 65536。即使模型支持 32k token，项目也可能因这个更保守的估算提前拒绝，这必须单独记录。

## 启动参数与运行方式

profile 固定 `generation_config=vllm`，避免模型仓库生成默认值改变请求语义；关闭 prefix caching 使首轮基线更容易解释；固定 structured output backend 为 `xgrammar`。上述选项及 chunked prefill、eager、revision/tokenizer revision 均已核官方版本文档，`xgrammar` 也核对了该 tag 源码，未运行 GPU CLI。[vLLM 0.32.0 serve 参数](https://docs.vllm.ai/en/v0.32.0/cli/serve/)、[固定 structured outputs 配置源码](https://github.com/vllm-project/vllm/blob/5df63a7203cdb9328d824a27b682e77c7b9928be/vllm/config/structured_outputs.py)

```bash
vllm serve Qwen/Qwen2.5-7B-Instruct \
  --revision a09a35458c702b33eeacc393d103063234e8bc28 \
  --tokenizer-revision a09a35458c702b33eeacc393d103063234e8bc28 \
  --served-model-name lablens-private \
  --dtype bfloat16 --max-model-len 32768 --max-num-seqs 1 \
  --max-num-batched-tokens 2048 --enable-chunked-prefill \
  --gpu-memory-utilization 0.9 --enforce-eager \
  --generation-config vllm --no-enable-prefix-caching \
  --structured-outputs-config '{"backend":"xgrammar"}' \
  --host 127.0.0.1 --port 8000
```

此段展示模型参数，不构成完整租赁或服务管理脚本。部署时还需生成私有 API key、设置鉴权、进程超时和日志路径。`max_runtime_seconds=7200` 是准备脚本的运行上限，**不会自动停止云端实例计费**；租机、关停和销毁状态另行核验。

Vast 的 SSH 模式会安装 SSH 并替换原 Docker entrypoint，因此无需 Docker-in-Docker：直接在所租的固定镜像容器里通过 SSH/on-start 运行 `vllm serve`，再用 SSH tunnel 将本地 loopback 映射到远程 loopback。环境变量不会自动继承到所有 SSH 会话，私有变量应显式载入权限受限的文件；不要把密钥写入公开模板。官方模板说明主要展示 `image:tag`，digest 输入在实际租赁界面的接受情况尚未验证；如界面不能接受 digest，不得悄悄改用浮动镜像并声称固定成功。[Vast 运行环境](https://docs.vast.ai/guides/instances/docker-environment)、[Vast 模板设置](https://docs.vast.ai/guides/templates/template-settings)

设备预检要求 Linux amd64、BF16 可用、GPU compute capability 至少 8.0、独占 GPU 显存至少 23,000 MiB、实际可用 host RAM 至少 32,000 MiB、容器内可用磁盘至少 40 GiB。CUDA 12.9.1 对应的保守驱动门槛是 **575.57.08**；不使用较低 CUDA minor compatibility 门槛来保证这个组合。若误选默认 CUDA 13 镜像，应重新匹配驱动及 digest，不能复用本配置。[NVIDIA CUDA 12.9 Update 1 驱动表](https://docs.nvidia.com/cuda/archive/12.9.1/cuda-toolkit-release-notes/index.html#cuda-driver)

成本规划中，镜像压缩层 11,182,268,295 bytes 加四个权重文件 15,231,271,888 bytes，首次主要输入约 **26.414 GB**；权重文件包含 header，因此略大于张量总字节数。30 GB 下载预算余量较小，比较 offer 时可用 **50 GB 入站传输、初始磁盘 100 GB** 的规划基准，仍以具体供应商计费项为准。镜像解压层和 overlay 实际占用未知，profile 的 `image_uncompressed_bytes=null`；40 GiB free 是容器内启动前门槛，不能误写成租赁磁盘总容量或下载量。

## 尚未完成的验收

- 未租机、未下载镜像层/权重、未发送推理请求；GPU、实际驱动、峰值显存、tokens/s 和语义通过率在 profile 中为 `null`。
- 部署后需核固定文件/镜像、CLI 版本、GPU/驱动和完整启动日志，先通过真实项目 JSON Schema 请求，再演示完整检索/报告/历史链路。
- timeout、客户端取消与服务端生成结束分别观测；HTTP 200 或合法 JSON 不等于证据结论正确。
- 本记录不能支持“已完成模型私有化部署”“已抗住真实流量”或“7B 模型质量达标”的简历表述。
