# 封存后最小补丁审查与增量回归

这是另一 AI 的开发审查，范围是相对真实评测候选 `dd225edf2c286619eae0d299228975c6fecee9f0` 的覆盖守卫和提前终止补丁。未调用外部模型；不修改本轮 raw、输入、gold 或已封存评分。后置代码没有真实模型重测，不能把离线修复写成原候选的推理成绩。

## 发现的问题及最小修复

初稿已能识别原始 Harbor 八行草稿中的“Quill 因 latency 不达标而无法通过验收”和“Sable 因成功率未达标而无法通过验收”，避免重复增加两个总判定槽。但只读交叉审查进一步发现三个确定性反例：

| 仅描述单项的输入 | 初稿行为 | 修复后行为 |
| --- | --- | --- |
| `Quill 在成功率指标中通过验收。` | 错当成 Quill 整体结论，只给 Sable 加槽 | 为 Quill 和 Sable 都保留总体结论槽 |
| `Quill 在 latency 指标中通过验收。` | 同上 | 同上 |
| `Quill 满足验收条件 1（延迟）。` | 同上 | 同上 |

根因是 `在...中` 接受任意单项范围，且“验收条件”后任意空白被当作句末，即使后面还有单项编号。经 root 指示，仅改 `answer_coverage.py` 和对应测试：前缀收窄到形如 `R-17`、`C21` 的有界试验标识加“试验中”；末尾空白必须紧接句末或标点，不允许借空白截掉后面的单项限定。未扩展到通用语义解析；未知范围仍不能替代总判定槽。

增加 9 个单项/编号/换行限定负例与 3 个有效句末/试验标识正例。原始 Harbor 八行草稿仍完整保留，新增槽为零。虚假的整体判断仍交由原有复核判断真假，覆盖守卫只识别是否存在这一命题。

## 容量错误终止与 Java 兼容性

`CoverageContractError` 在普通 `ValueError` 之前捕获，记录缺失槽和原始行数后直接返回 `agent_protocol_error` / `coverage_contract_error`，不会进入下一轮模型决策或最终复核。它发生在复核调用之前；退出工具 span 仍记录 `outcome=rejected`。普通不合法工具参数的行为没有因此改变。

两条脚本化 Agent 路径的测试确认：native 路径在 3 次脚本决策后停止，structured 路径在 2 次脚本决策后停止；每条仅 1 次本地检索，均没有复核调用或额外决策。测试使用受控模型对象和 loopback 检索 fixture，provider 用量为零，不是 DeepInfra 实测。

静态核对 Java：`WorkerResults.safeError` 已接受 `agent_protocol_error`，`safeAgentTrace` 保留 `state` 及 `tool_rejected.output`，`safeTiming` 已允许 `rejected`，因此不需要放宽 Java 生产校验。新增 Java 单测覆盖缺失槽与停止状态保存；其执行结果由 root 的全套 JVM 检查记录，本审查没有冒充已执行 Java 或真实跨服务容量错误测试。

## 实际验证与范围

```bash
.venv/bin/python -m pytest tests/test_answer_coverage.py tests/test_coverage_capacity_stop.py -q
git diff --check
```

结果：**59 passed in 1.80s**，另有既存的 Starlette/httpx 弃用提示；diff check 通过。此前初稿的 47 项虽然全通过，额外反例仍暴露了误覆盖，说明测试数量不能替代针对边界的检查。

审查/增量回归时文件 SHA-256：

| 文件 | SHA-256 |
| --- | --- |
| `lablens/answer_coverage.py` | `48ab359ab237cf8d954725d1b0e7f0e92971998b1e2c859776e3449624e089bd` |
| `lablens/workspace_agent.py` | `bc94eb274bfae12d89a66e2832dc70a70959e68902c208d62031fd92bd7a94b5` |
| `tests/test_answer_coverage.py` | `ce1f3f081c8511a4e21a1aae9e32925bbaf585f14a76a7abc6793c0f697d05f3` |
| `tests/test_coverage_capacity_stop.py` | `5e41284c43a52afc2ae836ae42907653f9f08548a622b6e5690041f51d2bb45e` |
| `services/workspace/src/test/java/io/lablens/workspace/WorkerResultsTest.java` | `5c06cbb45ce649d8f5f8bdfe87024d7228651dacd598f7186003ebb43ddc221c` |

已封存的 `independent-review.md` 当前 SHA-256 仍为 `f51eb941a88b2f47b6b14f5f083fce12c5c13124f3fa534b6bb6e933ba7b6e1b`，与 grading manifest 相符。此前“另一 AI 复核”的标题修改已经被评分封存收录，本次未再次改写该文件。

本补丁解决明确的误判和额外调用路径，不证明模型报告更完整，也不解决 kl-t2 错误澄清、通用整体语义识别或其他超时因素。主服务升级仍须遵守本轮未通过真实质量验收的结论。
