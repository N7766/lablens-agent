# 私有模型部署：准备状态与执行手册

**2026-11-11 状态：prepare only。** Safari 中已登录 Vast；本轮尚未租用 GPU，尚未注册本轮 SSH key，未下载模型权重或镜像层，未发出真实推理请求。500 元是用户给出的总预算；具体 offer、费用和释放方案仍需在付费前确认。以下远程启动与 `run` 命令是后续执行步骤，不表示已经运行。

候选为 Qwen2.5-7B-Instruct BF16、vLLM 0.32.0 / CUDA 12.9.1、单卡单序列、32768 上下文。模型 revision、tokenizer、镜像平台 digest 与预检阈值固定在 [profile](../deploy/private-model/profile.json)，官方核验记录在 [选型说明](evaluations/conversation-round19/model-selection.md)。24GB 是待实测起点；显存、时延、吞吐、语义正确性目前均未知。

## 1. 本地准备：不租机、不发 HTTP

以下命令在仓库根目录执行，使用已有项目 `.venv`，不修改主服务配置。`--output` 必须是新的目录，已有目录不能覆盖。

```bash
.venv/bin/python scripts/private_model_deploy.py plan
.venv/bin/python scripts/verify_private_model.py freeze \
  --output .local/round19/private-model-candidate-01 \
  --base-url http://127.0.0.1:18080/v1 \
  --model lablens-private
```

`plan` 只打印启动计划；`freeze` 保存三个合成请求、生产代码哈希、部署 profile 哈希及本地 Python/httpx/Pydantic 版本。它们都不要求 API key，不下载模型，不产生 GPU 或模型调用费用。修改模型、profile、代码或运行库后应保留旧候选，新建目录重新冻结；不要重写旧 `plan.json` 或修改 profile 中的 `null` 来补运行结果，实际结果另存凭据。

## 2. 付费前固定 offer 与镜像

需把选中的 offer ID、单价、GPU/显存、分配 RAM、驱动、地域、连接方式、磁盘和传输费用写入本轮记录，按预计 2 小时运行、50 GB 入站、100 GB 初始磁盘计算，并留出异常处理余量。50 GB 是预算规划数，非已发生下载量；已核镜像压缩层加权重文件主体约 26.414 GB。Vast 的存储费用在实例停止后仍会持续，删除实例才结束该实例存储计费；导出证据后应核对平台删除状态与费用记录。[Vast 费用说明](https://docs.vast.ai/guides/instances/pricing)

Vast 选择 **SSH 容器模式**，固定镜像为：

```text
vllm/vllm-openai@sha256:f99aa709b3f873aa1e1aadf5c1db35970119e8af9b36fe15d2a667aae0ed005c
```

该 digest 是 `linux/amd64` manifest，说明标签为 `v0.32.0-cu129-ubuntu2404`。Vast 的 SSH 模式会替换镜像原 entrypoint，随后在容器内直接启动 launcher，**不需要嵌套 Docker**。[Vast 运行环境](https://docs.vast.ai/guides/instances/docker-environment)、[模板设置](https://docs.vast.ai/guides/templates/template-settings)

2026-11-11 在 Safari 中观察到账户为 0 实例、0 存储，SSH key 列表为空。模板编辑框输入完整 `image@sha256` 后，CLI preview 保留了同一 digest；随后已 Cancel，未保存模板或创建实例。这仅证明界面编码保留输入，不能证明平台能拉取、启动该镜像。如果实际平台拒绝，应停在准备状态处理固定镜像问题，不能换成浮动标签后继续宣称镜像已固定。

需要先确认并注册专用于本轮的 SSH public key，再执行后续连接；private key 留在本机。本轮还没有完成这一步。`host-check` 只能检查容器内的 GPU、驱动及安装版本，不能独立证明平台实际拉取了指定镜像；镜像身份还需平台创建记录或可验证的运行时凭据。

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
  scripts/private_model_deploy.py "$LABLENS_GPU_SSH_DEST:/workspace/lablens-round19/scripts/"
scp -i "$LABLENS_GPU_SSH_KEY" -P "$LABLENS_GPU_SSH_PORT" \
  deploy/private-model/profile.json "$LABLENS_GPU_SSH_DEST:/workspace/lablens-round19/deploy/private-model/"
scp -i "$LABLENS_GPU_SSH_KEY" -P "$LABLENS_GPU_SSH_PORT" \
  .local/round19-secrets/api.env "$LABLENS_GPU_SSH_DEST:/workspace/lablens-round19/secrets/"
```

在远程 SSH shell 中执行：

```bash
cd /workspace/lablens-round19
chmod 700 secrets
chmod 600 secrets/api.env
set -a
. ./secrets/api.env
set +a
python3 scripts/private_model_deploy.py host-check
```

必须得到 `passed=true`：Linux amd64，vLLM 0.32.0，一张 GPU，显存至少 23000 MiB，compute capability 至少 8.0，驱动至少 575.57.08，RAM 配额至少 32000 MiB，启动目录可用磁盘至少 40 GiB，并具备合法 API key。内存检查取物理 RAM 与 cgroup 上限的较小值，不代表当前实时空闲内存。预检失败先保存原因，不现场安装未固定版本来绕过检查。

确认预检后，在这个远程 shell 中启动：

```bash
python3 scripts/private_model_deploy.py serve \
  --output /workspace/lablens-round19/evidence/server-01
```

此时才会下载固定模型并启动 vLLM。launcher 把下载目录固定到本次 evidence 下的 `model-cache`，保留 `host-check.json`、`launch-intent.json`、私有 `server.log` 和退出时的 `exit.json`。默认最多运行 7200 秒，包含该进程的模型下载／加载时间；超时或 SIGINT/SIGTERM 清理它创建的进程组。macOS 本机不能被这个 CUDA host-check 视为通过。

**7200 秒进程期限只约束模型进程，不会停止 Vast 实例、磁盘或计费。** SSH 断线、launcher 被 SIGKILL、宿主机故障也不能依赖它完成云端释放；必须核对具体实例 ID 并在平台停止／销毁，保存最后状态及费用凭据。停止与销毁含义不同，导出资料前不要销毁。

## 5. 本地 SSH tunnel 与三次兼容性请求

在本地新的终端建立 tunnel；先确认本地 18080 未被其他进程占用：

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
    --output .local/round19/private-model-candidate-01
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
