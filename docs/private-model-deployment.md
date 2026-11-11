# 私有模型部署：固定配置与复现手册

本页保留原三请求部署复现流程。**完整 Agent 候选须使用[验收步骤](private-agent-acceptance.md)**：独立 gateway key、18081 网关、显式 v4，并重新确认该次请求上限。不要混用旧的直接 probe 与 Agent 配额。

**最新 Round19b 实测：模型服务启动失败，兼容性和 Agent 调用均为 0。** 新实例 `60163927` 的 20 个逻辑 CPU 均未暴露 AVX；固定镜像中的 UCX 报错 `UCX library was compiled with avx but CPU does not support it.`。原主机预检通过，但当时没有 CPU 指令集检查；启动尝试持续约 44.873 秒后以退出码 1 结束。未启动兼容性或 Agent 网关阶段，也未追加推理。实例、存储、临时 SSH key 与本轮隔离服务已核对清理，主服务 8081/8765 保持原进程。见 [Round19b 本轮运行记录](conversation-round19b-runtime.md)。

离线修复已增加 AVX 预检，全量工程回归为 **2485 passed、1 项既有 warning，75.94 秒**；没有在新 GPU 上重新验证修复后的启动或 Agent 链路。原 profile、原冻结计划和失败输出保持不变，原 launcher 已归档；当前 launcher 是下一候选代码。下次运行必须使用新目录、新源文件哈希重新冻结，不能改写旧计划后续跑。

**此前 Round19 实测：GPU 部署启动成功，三次兼容请求的整体验收未通过。** 中文连通和工具决策通过，报告校验为 `structured_contract_failed`；未追加调用，Agent 尚未接通。已按授权使用 Vast A6000 单卡、100 GB 实例 `60154954`，该次最多 2 小时／10 USD，项目总预算 500 CNY。原失败、TorchCodec CPU 补丁与固定文件哈希均已归档；实例、存储及临时 SSH key 已核对清理，平台该次记录约 $0.38，最终结算可能延迟。以下保留复现步骤，历史结果见 [运行报告](evaluations/conversation-round19/runtime-run.md)及[事故记录](evaluations/conversation-round19/runtime-incident.md)。

候选为 Qwen2.5-7B-Instruct BF16、vLLM 发行版本 0.32.0／安装包版本 `0.32.0+cu129`、CUDA 12.9.1、单卡单序列、32768 上下文。模型 revision、tokenizer、镜像 digest、依赖补丁与预检阈值固定在 [profile](../deploy/private-model/profile.json)，官方核验记录在 [选型说明](evaluations/conversation-round19/model-selection.md)。实际选择 A6000；此前 24GB 显存推算不是已验证部署结果。峰值显存、时延、吞吐、语义正确性仍待测量。

**当前环境是固定基础镜像加显式 TorchCodec CPU 补丁，不是原封不动的官方镜像。** 原镜像在导入 TorchCodec 时因 CUDA 13 动态库缺失而退出；仅核对 image digest 无法复现修复后的环境。第 4 节给出固定 wheel、哈希、安装限制与检查步骤。

## 1. 本地准备：不租机、不发 HTTP

以下命令在仓库根目录执行，使用已有项目 `.venv`，不修改主服务配置。`--output` 必须是新的目录，已有目录不能覆盖。

```bash
.venv/bin/python scripts/private_model_deploy.py plan
.venv/bin/python scripts/verify_private_model.py freeze \
  --output .local/round19/private-model-reproduction-01 \
  --base-url http://127.0.0.1:18080/v1 \
  --model lablens-private
```

`plan` 只打印启动计划；`freeze` 保存三个合成请求、生产代码哈希、部署 profile 哈希及本地 Python/httpx/Pydantic 版本。它们都不要求 API key，不下载模型，不产生 GPU 或模型调用费用。修改模型、profile、代码或运行库后应保留旧候选，新建目录重新冻结；不要重写旧 `plan.json` 或修改 profile 中的 `null` 来补运行结果，实际结果另存凭据。

## 2. 付费前固定 offer 与镜像

需把选中的 offer ID、单价、GPU/显存、分配 RAM、驱动、地域、连接方式、磁盘和传输费用写入本轮记录，按预计 2 小时运行、50 GB 入站、100 GB 初始磁盘计算，并留出异常处理余量。50 GB 是预算规划数，非已发生下载量；已核镜像压缩层加权重文件主体约 26.414 GB。Vast 的存储费用在实例停止后仍会持续，删除实例才结束该实例存储计费；导出证据后应核对平台删除状态与费用记录。[Vast 费用说明](https://docs.vast.ai/guides/instances/pricing)

还需核对 CPU 指令集条件：本固定镜像的 UCX 二进制要求 AVX，GPU 型号及显存相同并不代表 CPU 条件相同。offer 的 CPU 型号不能替代容器内 `/proc/cpuinfo` 的实际 flags；租用后必须通过新预检再启动模型。当前 guard 只把本次已确认的 AVX 前提列为必需，没有依据把 AVX2 设为本次门槛，也不声称 AVX 足以覆盖所有 vLLM 或依赖库的硬件要求。

Vast 选择 **SSH 容器模式**，固定镜像为：

```text
vllm/vllm-openai@sha256:f99aa709b3f873aa1e1aadf5c1db35970119e8af9b36fe15d2a667aae0ed005c
```

该 digest 是 `linux/amd64` manifest，说明标签为 `v0.32.0-cu129-ubuntu2404`。Vast 的 SSH 模式会替换镜像原 entrypoint，随后在容器内直接启动 launcher，**不需要嵌套 Docker**。[Vast 运行环境](https://docs.vast.ai/guides/instances/docker-environment)、[模板设置](https://docs.vast.ai/guides/templates/template-settings)

付费前的 Safari 模板检查只确认 CLI preview 保留完整 `image@sha256`，并取消了模板编辑。后续已按具体授权创建实例并完成 SSH 与主机预检；原镜像导入故障仍然真实发生。模板输入、平台创建、包版本检查、服务就绪必须分别留存证据。如果另一次复现的平台拒绝 digest，应先处理固定镜像问题，不能换成浮动标签后继续宣称镜像已固定。

复现时需先确认并注册专用 SSH public key，再连接；private key 留在本机。`host-check` 检查容器内的 CPU flags、GPU、驱动、安装版本和资源下限，不能独立证明平台实际拉取了指定镜像；镜像身份还需平台创建记录或可验证的运行时凭据。

## 3. 确认租赁之后：生成私有运行配置

下面的初始化命令会创建本轮新密钥文件；输出不包含密钥。API key 是自建 vLLM 服务的随机鉴权 token，与 Vast 平台 API key 不同，不需要向模型供应商注册。

```bash
umask 077
.venv/bin/python - <<'PY'
from pathlib import Path
import secrets

root = Path('.local/round19-secrets')
root.mkdir(parents=True, exist_ok=False, mode=0o700)
key = secrets.token_hex(32)
service_key = secrets.token_hex(32)
with (root / 'api.env').open('x') as stream:
    stream.write('LABLENS_PRIVATE_MODEL_API_KEY=' + key + '\n')
worker = Path('deploy/private-model/worker.env.example').read_text()
# This section reproduces the historical direct three-request setup only.
worker = worker.replace('http://127.0.0.1:18081/v1', 'http://127.0.0.1:18080/v1')
worker = worker.replace('scoped-review-decisions-v4', 'scoped-review-delta-v3')
worker = worker.replace('LLM_API_KEY=\n', 'LLM_API_KEY=' + key + '\n')
worker = worker.replace('LABLENS_SERVICE_KEY=\n', 'LABLENS_SERVICE_KEY=' + service_key + '\n')
with (root / 'worker.env').open('x') as stream:
    stream.write(worker)
PY
```

只把 `api.env` 交给远程自建服务；`worker.env` 和 service key 留给本地隔离 worker／未来隔离 JVM。不要把任何私有 env、完整原始 server log 或 SSH private key 上传 GitHub。若目录已存在，核对现有文件和实例状态，不能盲目重建 key。

## 4. 容器内预检与启动

从已确认的 Vast 实例连接面板取得 SSH 主机、端口、用户名；下面三项必须替换为实际值。SSH 首次连接应核对该实例身份，不使用关闭 host key 检查的选项。

```bash
export LABLENS_GPU_SSH_DEST='root@<实例连接主机>'
export LABLENS_GPU_SSH_PORT='<实例SSH端口>'
export LABLENS_GPU_SSH_KEY='<本机专用SSH私钥绝对路径>'

ssh -i "$LABLENS_GPU_SSH_KEY" -p "$LABLENS_GPU_SSH_PORT" "$LABLENS_GPU_SSH_DEST" \
  'umask 077; mkdir -p /workspace/lablens-round19/scripts /workspace/lablens-round19/deploy/private-model /workspace/lablens-round19/secrets'
scp -i "$LABLENS_GPU_SSH_KEY" -P "$LABLENS_GPU_SSH_PORT" \
  scripts/private_model_deploy.py scripts/verify_private_model_identity.py \
  "$LABLENS_GPU_SSH_DEST:/workspace/lablens-round19/scripts/"
scp -i "$LABLENS_GPU_SSH_KEY" -P "$LABLENS_GPU_SSH_PORT" \
  deploy/private-model/profile.json "$LABLENS_GPU_SSH_DEST:/workspace/lablens-round19/deploy/private-model/"
scp -i "$LABLENS_GPU_SSH_KEY" -P "$LABLENS_GPU_SSH_PORT" \
  .local/round19-secrets/api.env "$LABLENS_GPU_SSH_DEST:/workspace/lablens-round19/secrets/"
```

在远程 SSH shell 中先进入工作目录，再执行下方固定依赖补丁。此步骤仅适用于 Python 3.12、linux x86_64 和本 profile 的原始包组合；已有成功的补丁收据时核对实际版本，不重复覆盖收据或重新安装。

```bash
cd /workspace/lablens-round19
python3 - <<'PY'
from datetime import datetime, timezone
from pathlib import Path
import hashlib
import importlib.metadata
import json
import os
import signal
import subprocess
import sys
import urllib.request

os.umask(0o077)
assert sys.version_info[:2] == (3, 12), 'This wheel requires CPython 3.12'
root = Path('evidence/codec-overlay-reproduction-01')
root.mkdir(parents=True, exist_ok=False, mode=0o700)
url = 'https://download.pytorch.org/whl/cpu/torchcodec-0.17.0%2Bcpu-cp312-cp312-manylinux_2_27_x86_64.manylinux_2_28_x86_64.whl'
expected_sha = 'de8e58e5f30692ee7cf92b1b313d0ffdeaac95148525ef2a865067fca0255246'
expected_bytes = 5573051
wheel = root / 'torchcodec-0.17.0+cpu-cp312-cp312-manylinux_2_27_x86_64.manylinux_2_28_x86_64.whl'
names = ('vllm', 'torch', 'torchvision', 'torchaudio', 'torchcodec', 'transformers', 'xgrammar')
def versions():
    return {name: importlib.metadata.version(name) for name in names}
def save(name, value):
    with (root / name).open('x') as stream:
        json.dump(value, stream, indent=2)
        stream.write('\n')
        stream.flush()
        os.fsync(stream.fileno())
before = versions()
assert before['vllm'] == '0.32.0+cu129'
assert before['torch'] == '2.13.0+cu129'
assert before['torchcodec'] == '0.17.0', 'Preserve and inspect an already changed environment'
save('intent.json', dict(started_at=datetime.now(timezone.utc).isoformat(),
     original_versions=before, url=url, expected_sha256=expected_sha,
     expected_bytes=expected_bytes, model_requests_sent=0))
class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None
def expired(*args):
    raise TimeoutError('Wheel download exceeded 45 seconds')
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
signal.signal(signal.SIGALRM, expired)
signal.alarm(45)
try:
    with opener.open(url, timeout=20) as response:
        assert response.status == 200
        raw = response.read(expected_bytes + 1)
finally:
    signal.alarm(0)
actual_sha = hashlib.sha256(raw).hexdigest()
assert len(raw) == expected_bytes and actual_sha == expected_sha, 'Wheel identity mismatch'
with wheel.open('xb') as stream:
    stream.write(raw)
command = [sys.executable, '-m', 'pip', '--isolated', 'install',
           '--no-index', '--no-deps', '--force-reinstall', str(wheel)]
with (root / 'pip.log').open('x') as stream:
    installed = subprocess.run(command, stdout=stream, stderr=subprocess.STDOUT, timeout=90)
after = versions()
unchanged = all(before[name] == after[name] for name in names if name != 'torchcodec')
passed = installed.returncode == 0 and unchanged and after['torchcodec'] == '0.17.0+cpu'
save('install-receipt.json', dict(wheel_sha256=actual_sha, wheel_bytes=len(raw),
     install_return_code=installed.returncode, after_versions=after,
     non_codec_versions_unchanged=unchanged, passed=passed))
assert passed, 'Preserve logs; do not continue after installation or dependency drift'
with (root / 'import.log').open('x') as stream:
    imported = subprocess.run([sys.executable, '-c', 'import torchcodec.decoders'],
                              stdout=stream, stderr=subprocess.STDOUT, timeout=45)
save('import-receipt.json', dict(return_code=imported.returncode,
     passed=imported.returncode == 0, scope='TorchCodec import only; no model request'))
assert imported.returncode == 0, 'Preserve the import failure; model serving remains unverified'
PY
```

该脚本只下载约 5.57 MB 的固定 wheel，先校验完整字节数和 SHA256，再从本地文件安装。`--no-deps --no-index --isolated` 防止安装器改动 PyTorch 栈或切换来源；前后还逐项核对其他包版本。失败时保留该目录，新动作另记；不自动重试，不升级 Torch、不安装 CUDA 13 来绕开原始问题。官方矩阵、索引和 sidecar metadata 依据见 [TorchCodec 补丁出处](evaluations/conversation-round19/torchcodec-overlay-evidence.json)。CPU 构建不提供媒体 GPU 编解码，本轮仅验收纯文本接口。

随后在同一远程 shell 中加载本轮鉴权配置并执行主机预检：

```bash
cd /workspace/lablens-round19
chmod 700 secrets
chmod 600 secrets/api.env
set -a
. ./secrets/api.env
set +a
python3 scripts/private_model_deploy.py host-check
```

必须得到 `passed=true`：Linux amd64，vLLM 安装包精确为 `0.32.0+cu129`，torch 为 `2.13.0+cu129`，torchcodec 为 `0.17.0+cpu`；一张 GPU，显存至少 23000 MiB，compute capability 至少 8.0，驱动至少 575.57.08，容器可见内存检查值至少 32000 MiB，启动目录可用磁盘至少 40 GiB，并具备合法 API key。内存检查在能读到有效 cgroup 上限时取它与物理内存的较小值；该值不代表 offer 分配额或实时空闲内存，offer 的 RAM 条件必须另核。预检失败先保存原因，不现场安装未固定版本来绕过检查。版本一致仍不能代替前面的动态库导入检查。

当前 launcher 还要求 `checks.cpu_required_flags=true`。它最多读取 8 MiB 的 `/proc/cpuinfo`，另读 1 字节判断是否超限；完整、可解析的每个逻辑 CPU 记录都必须包含 `avx`。文件无法读取、空值、坏格式、重复 processor、任一 CPU 缺失 AVX 或超过大小上限，均在创建模型进程之前拒绝。`details.cpu_instruction_set` 只保存状态、逻辑 CPU 数、flags 交集、读取字节数及完整文件 SHA256，不保存 CPU 型号或完整原文；超限时不把截断内容哈希当作完整证据。该检查是本固定镜像 UCX 故障的预防措施，不是所有 vLLM 部署的通用要求或完整兼容性认证。

确认预检后，在这个远程 shell 中启动：

```bash
python3 scripts/private_model_deploy.py serve \
  --output /workspace/lablens-round19/evidence/server-reproduction-01
```

此时才尝试下载固定模型并启动 vLLM。launcher 把下载目录固定到本次 evidence 下的 `model-cache`，保留 `host-check.json`、`launch-intent.json`、私有 `server.log` 和退出时的 `exit.json`。默认最多运行 7200 秒，包含该进程的模型下载／加载时间；超时或 SIGINT/SIGTERM 清理它创建的进程组。macOS 本机不能被这个 CUDA host-check 视为通过。历史 Round19 保留未使用的 `probe-01`／`probe-02` 与失败记录，补丁后实际执行的是 `probe-03`／`server-03`。后续 Round19b 的 CPU 失败及零请求计划也已单独保留，均不得覆盖。

模型文件下载完成后，在另一个远程 SSH shell 中核对固定 revision 的四个权重与资料哈希，结果使用新文件名：

```bash
cd /workspace/lablens-round19
(
  set -C
  python3 scripts/verify_private_model_identity.py \
    --cache-root evidence/server-reproduction-01/model-cache \
    --profile deploy/private-model/profile.json \
    > evidence/server-reproduction-01/identity-01.json
)
```

核验器只读模型缓存，支持受限的 HF snapshot→repo blob→shared blob 两跳路径，不允许任意 cache 内别名。路径拒绝与哈希不匹配是不同结果；缺失或失败时保留原件，不移动权重或覆盖旧结果来“修复”。历史 Round19 的 LICENSE 已自动下载且哈希匹配，无需手动补齐；不能把该次核验套用到后续实例。完整核验仅证明指定本地文件字节，未锚定的 tokenizer 数据文件仍是 observed-only，也不独立证明运行中服务加载身份或镜像未修改。

**7200 秒进程期限只约束模型进程，不会停止 Vast 实例、磁盘或计费。** SSH 断线、launcher 被 SIGKILL、宿主机故障也不能依赖它完成云端释放；必须核对具体实例 ID 并在平台停止／销毁，保存最后状态及费用凭据。停止与销毁含义不同，导出资料前不要销毁。

## 5. 本地 SSH tunnel 与三次兼容性请求

在第 4 节已设置三项 SSH 变量的同一个本地终端建立 tunnel；先确认本地 18080 未被其他进程占用：

```bash
ssh -i "$LABLENS_GPU_SSH_KEY" -p "$LABLENS_GPU_SSH_PORT" \
  -o ExitOnForwardFailure=yes -o ServerAliveInterval=15 -o ServerAliveCountMax=3 \
  -N -L 127.0.0.1:18080:127.0.0.1:8000 "$LABLENS_GPU_SSH_DEST"
```

vLLM 只监听远程 `127.0.0.1:8000`，本机只监听 `127.0.0.1:18080`，无需公开模型服务端口。可以单独 GET `/health` 检查 tunnel／服务就绪；健康接口返回不证明权重身份或生成能力。不要把不断失败的健康检查改成反复生成重试。

在本地仓库根目录的另一个终端，显式加载本轮 key 并运行已冻结候选：

```bash
(
  set -a
  . ./.local/round19-secrets/api.env
  set +a
  .venv/bin/python scripts/verify_private_model.py run \
    --output .local/round19/private-model-reproduction-01
)
```

最多三次生成请求：中文连通、结构化 action、scoped review。每次最多 40 秒、全程最多 120 秒，响应体最多 128 KiB；不跟随重定向、不读取代理环境、不自动重试。任何一项失败会跳过后续请求。结果未知或进程中断时保留目录；再次对相同目录执行会被拒绝，不能通过删掉 `run/` 来“续跑”。需要新实验时先解释旧结果，再冻结新候选。

查看 `run/summary.json`、`run/manifest.json` 和每次请求的 intent、脱敏响应、校验结果。冻结的 profile hash 只是预期身份，需与远程 `launch-intent.json`、平台镜像凭据以及下载后权重核验配对。`compatibility_passed` **不代表完整 Agent 链路、回答质量、模型容量或生产流量通过**。详见 [验收协议](evaluations/conversation-round19/protocol.md)。

## 6. 后续 Agent 联调的隔离 worker

三次兼容性 probe 不启动 worker。以下配置仅用于之后已批准的完整 Agent 验收；不要把直接模型 probe 当作已经完成检索／工具执行／数据库历史。

```bash
export LABLENS_PROJECT_ROOT="$PWD"
env -i PATH="$PATH" LABLENS_PROJECT_ROOT="$LABLENS_PROJECT_ROOT" \
  /bin/bash --noprofile --norc -c '
    set -a
    . "$LABLENS_PROJECT_ROOT/.local/round19-secrets/worker.env"
    set +a
    test -n "$LLM_API_KEY" && test -n "$LABLENS_SERVICE_KEY" || exit 2
    cd "$LABLENS_PROJECT_ROOT"
    exec "$LABLENS_PROJECT_ROOT/.venv/bin/python" -m uvicorn lablens.api:app \
      --host 127.0.0.1 --port 8770
  '
```

`env -i` 去掉旧 shell 的 DeepInfra 等设置，显式导出本轮 env，包括刻意为空的 key。`load_dotenv()` 默认不覆盖已存在环境变量，所以模型请求始终指向本轮 tunnel；embedding 指向另一个 loopback origin 且 key 为空，阻止同源 LLM key 回退。只提交 `strategy=bm25`，不尝试 dense/hybrid，也不复制主项目 `.env`。该配置关闭本轮 checkpoint callback 和 persistent index，避免误接主 JVM。后续如果接入隔离 JVM，需要另行固定数据库、Redis、service key 和 callback；主服务 8081/8765 保持原配置。

Agent 现有预算仍为 80 秒，并给最终 review 保留最多 20 秒；兼容性 probe 的 40 秒单请求通过不能保证这个更紧的 Agent 时序。`LABLENS_CONTEXT_WINDOW_TOKENS=32768` 在当前实现中是 UTF-8 字节/framing 的保守单位，不是 tokenizer 真实 token 数。超时、schema 失败和预算拒绝都应如实保留，不能为得到通过结果临时放宽后覆盖首轮数据。
