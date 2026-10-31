# Round 9 稀疏复核：独立协议检查与字节对照

本轮把固定行复核改为显式“保留原行 / 返回替换行”。模型必须重新核查每一行，每个原 ID 在两个列表中恰好出现一次；缺行、重复、交叉、未知 ID 或非法字段均拒收，不能在失败后默认沿用草稿。协议允许压缩正确原行的重复输出，但没有提高原来的 75 秒、6+1 次模型调用或 1800/2600 输出上限。

## 独立检查与实际问题

新增 `tests/test_round9_review_delta.py` 共 **46 项**离线检查：

- 全替换、混合、全保留恢复为同一完整报告，原顺序和 statement 固定，返回嵌套字段与输入无别名。
- 严格检查缺失/重复/重叠/未知/错误类型 ID，两个列表的形状，额外/缺失字段及非法/重复 evidence ID。
- placeholder、已有数值问题、已知对象绑定问题、非 catalog 引文、重复引文、不一致 citation IDs、supported causal_claim 均不能保留；已表达不足的因果假设可按引用条件保留。
- 真实 `_post → httpx.post` 代码路径使用 delta schema，最多调用一次；截断、超时、缺项失败都不回退发布草稿，也不重试。取消在发送前阻止请求，发送后阻止发布。
- 即使允许保留，历史执行仍受配置范围检查，伪装为 documented_fact 的显式因果谓词仍受因果 guard。无 scope 的旧选择协议保持可用。
- 一个故意把 pending 写成 completed 的受控语义错误仍可能通过有限机械检查；测试明确记录该盲区，所有语义认证字段保持 false。

独立检查发现审计链缺口：原实现只保留 response 哈希和保留/替换 ID；最终 guard 改写解释后，原始替换文本可能无法恢复。root 补入有界 parsed `reviewed_response` 深拷贝，并在 `review_completed.output` 中保存 generation_review。回归确认模型原始 supported/explanation/evidence_ids 与最终 insufficient 结果同时可查，修改审计副本不会改输入或最终引用。完整响应封存仍依赖实际运行的原始结果保存，不把单元测试称为真实调用成功。

独立检查还发现 schema 的 enum 列表与 policy 的允许列表共用引用；root 改为复制列表，现有 schema 测试补入修改返回 schema 不影响 policy 的断言。当前真实路由没有修改该列表，修复针对协议对象不可变边界。

另一审查指出 supported causal_claim 已知会被后续 guard 拒绝，因此 policy 强制其返回替换；这不是给因果结论开例外。替换为 rule_evaluation 后仍需对应规则与测量，不能仅改标签宣称原因成立。

## 可复现离线字节对照

运行：

```sh
.venv/bin/python scripts/compare_review_delta_round9.py \
  --output docs/evaluations/conversation-round9/review-delta-bytes.json
```

输出已存在时拒绝覆盖；后续源码变化应使用另一个输出文件，保留首次对照。脚本只读第八轮 raw，前后核验完整 seal；从封存的 `tool_started.arguments`、实际检索 hits 和会话重建草稿，并核对 coverage 与当时轨迹一致。6 份报告和 Harbor 超时的草稿均保留，共 7 份；必要澄清及依赖阻断没有可用复核草稿，不伪造。

为了隔离协议开销，两个请求 builder 使用**同一当前提示词、catalog、上下文与草稿**；full-row 对照只去掉 delta 指令、policy 和 schema，恢复完整替换 schema。因此这不是第八轮整包与第九轮整包的实际线上比较。原 Round8 请求字节作为参考单列。

两个 response 是手工构造的协议场景：全部原行原样重发，以及所有当前允许的 ID 均选择保留、其余原样重发。二者恢复后必须与原稿相同；原稿已知错误也可能被保留，所以它们不是正确答案或真实模型返回。

| 草稿 | 原行数 / 可保留 | 新请求增加 bytes | 零保留响应增加 bytes | 全可保留响应变化 bytes |
| --- | ---: | ---: | ---: | ---: |
| cedar-t1 | 8 / 6 | +1,401 | +31 | −1,213 |
| cedar-t2 | 2 / 2 | +1,349 | +31 | −529 |
| harbor-t1 | 8 / 8 | +1,427 | +31 | −2,204 |
| ar-t1 | 4 / 4 | +1,375 | +31 | −984 |
| ar-t2 | 7 / 4 | +1,375 | +31 | −914 |
| kl-t1 | 4 / 4 | +1,375 | +31 | −1,040 |
| kl-t2 | 4 / 2 | +1,349 | +31 | −588 |

请求增加是 schema、明确保留指令和 policy 的代价；响应能否减少取决于模型实际选择和修改量。不要相加请求/响应字节来估算模型耗时或成本。该对照没有调用模型、没有计算 tokenizer token、没有测量延迟，不能证明 Harbor 超时会消失或质量提高。实际效果要在候选冻结、单次采集和 raw 封存后单独评估。
