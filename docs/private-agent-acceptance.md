# 私有模型完整 Agent：候选验收步骤

本页是Round19b新候选流程。原三次GPU请求已经结束；下面的prepare/freeze不授权新租机或模型请求。新的付费运行须先确认具体上限，再按冻结计划执行一次。使用新的独占目录，不覆盖Round19原件。

## 先做本地工程验证

`scripts/verify_private_agent.py` 的fixture是固定响应HTTP服务，不是模型；它的prepare仅创建合成工作区、BM25索引和会话，run才提交一个Agent任务。候选服务固定为8086，拒绝使用主8081。

准备一套独立JVM8086、worker8770、Redis6383、新MySQL schema和仅该schema权限的账号。主服务不重启。JVM设MQ=false、checkpoint=false、worker数1、DB池4/min1；worker设 `PYTHON_DOTENV_DISABLED=1`、v4、BM25、独立SQLite索引。只显式传必要环境，不继承主`.env`。模板见 [worker.env.example](../deploy/private-model/worker.env.example)；本地固定响应时将模型地址换为8771，并使用合成key。

```bash
.venv/bin/python scripts/verify_private_agent.py fixture \
  --output .local/round19b/local-report-requests.jsonl --case report
# 另一终端，先将隔离管理员凭据导入进程环境，不在命令行中写密码：
.venv/bin/python scripts/verify_private_agent.py prepare \
  --output .local/round19b/local-report --case report
.venv/bin/python scripts/verify_private_agent.py run \
  --output .local/round19b/local-report --evidence-kind local_scripted
```

insufficient场景切换fixture和prepare的 `--case insufficient`；timeout场景切换为timeout，并在独立worker设Agent总预算1秒、LLM timeout5秒，fixture延迟3秒。各场景使用新的目录/工作区；停止自己启动的进程后再切换，不碰主服务。超时晚到回读由driver执行，不能把服务端worker_timeout或连接拒绝当agent_timeout通过。

driver保存冻结计划、提交意图、终态任务、新认证会话回读与检查结果。未知提交只做只读对账，不再次POST。MySQL行、运行环境、进程清理和浏览器证据由实验记录另外提供，driver自身不能认证任意远端服务的实际配置。

## 冻结新兼容探针和发送网关（零模型调用）

```bash
.venv/bin/python scripts/verify_private_model.py freeze \
  --output .local/round19b/live-compat-01 \
  --base-url http://127.0.0.1:18081/v1 --model lablens-private \
  --review-protocol scoped-review-decisions-v4
.venv/bin/python scripts/private_model_budget_gateway.py prepare \
  --directory .local/round19b/live-gateway-01 \
  --compatibility-plan .local/round19b/live-compat-01/plan.json \
  --upstream http://127.0.0.1:18080/v1 --listen-port 18081 --upstream-mode ssh_tunnel
```

此时仍未发送HTTP。代码、协议或运行库变化需要新目录重新冻结。先创建两个不同随机key：`LABLENS_PRIVATE_GATEWAY_KEY`只给probe/worker，`LABLENS_PRIVATE_MODEL_API_KEY`只给网关及远端服务。API key不放命令参数、日志或GitHub。

## 获准且远端就绪后才执行

沿用[固定模型与显式CPU codec补丁部署手册](private-model-deployment.md)的镜像/文件核验，使用新专用SSH key和新的私有目录；旧实例已删除。SSH tunnel为18080，上游仍仅loopback。worker的LLM_BASE_URL为18081，LLM_API_KEY为本地gateway key，v4显式启用，绝不把上游key给worker。

1. 启动 `private_model_budget_gateway.py serve --directory ... --phase compat`，只在该网关进程同时导入两个key。
2. probe进程把它所需的 `LABLENS_PRIVATE_MODEL_API_KEY` 设置为**本地gateway key**，执行 `verify_private_model.py run --output ...`。这是该工具原环境变量名，不代表给它上游key。
3. 任一失败就停止整个实验。全部3项通过后，正常停止compat网关，保留closed/ledger；以 `serve --phase agent --compatibility-summary <compat-dir>/run/summary.json` 启动唯一Agent阶段。网关比对完整成功记录和响应哈希。
4. 使用 `verify_private_agent.py prepare --output <new-agent-dir> --case report --gateway-plan-sha256 <网关输出的固定SHA256>`，绑定原定两份合成资料和问题。此步骤在隔离JVM上完成，不发模型请求；phase默认300秒，准备应预先完成并在执行前核对。
5. `verify_private_agent.py run --output <new-agent-dir> --evidence-kind real_private_model` 只提交一次。driver先检查当前网关是同一SHA、agent阶段、配额未用，再提交。上限两次动作决策和一次复核；重复搜索导致第三次仍为决策则被拒绝，不能替模型改写动作。
6. 保存结果后另做报告语义审阅、MySQL行核对和只读浏览器回看。HTTP200、任务成功和43项本地检查都不能替代真实结论判分。原题属于已知失败回归，不是独立新题。
7. 在2小时/10USD边界内预留导出清理时间，停止网关/隧道，平台删除实例并独立回读实例和存储列表，删除专用SSH key，核对费用。网关计数不控制GPU租赁账单。

实例创建前重新核报价及存储/传输成本；任何配置、模型或请求次数的扩大需重新确定范围。原profile中的准备快照字段不用于累计运行计数，新实跑另存收据。
