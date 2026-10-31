# Round 6 原始结果另一 AI 复核

本复核由同一开发团队的另一 AI 执行，先读冻结 gold、最终回答、引用原文与工具轨迹，未读取或改写主评分者的 annotations。复核者参与过本轮算术模块开发及 harness 审查，因此这次另一 AI 复核不代表盲评、独立人工或外部评测。本复核没有发起模型请求，也没有修改冻结候选、输入或 raw。

## 语义结论

| 轮次 | 已做到 | 仍存在的问题 | 严格验收意见 |
| --- | --- | --- | --- |
| ar-t1 | Cedar 2.4 的 2 次总尝试 / 1 次重试、仅传输超时可重试、4xx 不重试、默认 1 次与部署覆盖为 2 次、培训资料复制旧版本且未批准均有依据 | 多行重复同一结论，展示不够简洁；其中“最多 2 次”的行标题省略 Cedar，但同条解释及整体回答明确限定 Cedar，不宜误判为把所有 2.4 集群都定为 2 次 | 核心结论及所需内容足以通过；不因重复排版判事实错误 |
| ar-t2 | 正确切换到 Juniper 1.8，答出 3 次总尝试 / 2 次重试、4xx 不重试；所引版本和集群正确 | 用户明确要比较“哪些变更、哪些保留”，回答仅重述新数值，没有显式标出 2→3、1→2 和 4xx 保留；又重复旧培训资料问题。不能把用户提问回显计为已回答比较义务 | 新值正确，但完整性和跨轮对比不足，不能 strict 通过 |
| kl-t1 | 当前允许 2 次重试 / 3 次尝试；历史是否重试未知；badge 3 无图例、最终成功都不能证明重试次数。没有拒答已知配置或断言历史零重试 | 未显式写 transport-only、当前配置晚于运行且不是历史快照，也未说清权威计数器或关联 attempt 记录作为最小证据。前两项属于既有 gold 的细节门槛，不能概括成核心事实全错 | 核心判断正确，冻结 gold 的完整性仍不足，不能 strict 通过 |
| kl-t2 | “您已说明”保留用户线索归属，并正确把“那次运行”识别成 R-731；没有直接捏造历史零重试或上游限流原因 | 实际检索已经得到运行回执、证据清单和告警卡片，却调用 `request_clarification` 结束。没有逐项给重试未知、最终成功/未证明失败、限流原因未知及两项最小证据。用户的问题和对象并不需要先澄清 | 这是不必要澄清造成的实质漏答/错误拒答，不能归因于未检索到资料 |

上述意见支持 ar-t1 可通过、其余三轮不能 strict 通过；正式统计仍由保留的原 gold 与主评分记录产生，本文件不覆盖它们。

## 证据位置与范围判断

- [ar-t1 原始终态](../runs/round6-once/ar-t1-final-task.json)：`/result/answer`、`/result/assessments`；`/result/agent/trace/8/output/hits` 包含 ar-current、ar-deployments、ar-training 和 ar-legacy。不能仅因为引文文本存在就给分，本复核逐项对照了配置上限、版本默认值与培训勘误的实际含义。
- [ar-t2 原始终态](../runs/round6-once/ar-t2-final-task.json)：`/request/question` 要求明确比较变化/保留；`/result/answer` 的四项内容中没有对应的比较措辞。`/result/agent/trace/8/output/hits` 已提供新旧政策和集群注册表，所以这是回答组织/覆盖问题。
- [kl-t1 原始终态](../runs/round6-once/kl-t1-final-task.json)：`/result/assessments/1/explanation` 的“无其他运行时记录可验证”应在当前资料包范围内理解，因为它同时引用明确写着 `in this evidence bundle` 的 inventory。措辞最好限定为“本资料包中无其他记录”，但现有回答没有断言历史从未产生记录或从未重试；不应扩大成历史否定事实。`/result/agent/trace/8/output/hits` 已检索四份必需资料，故漏掉快照时间/最小证据不是检索缺失。
- [kl-t2 原始终态](../runs/round6-once/kl-t2-final-task.json)：`/result/agent/trace/8/output/hits` 是 kl-alert、kl-run、kl-retention；`/result/agent/trace/17/name` 是 `request_clarification`；`/result/answer` 只有追问，`/result/assessments` 和 `/result/citations` 为空。提议寻找更多日志可以放在已有未知结论之后，不能替代本题要求的回答。原 gold 也明确允许“先给实质结论，再邀请补充”。
- [冻结原 gold](../../conversation-round5/new-scenarios/frozen/gold.json)：以上完整性要求取自 ar2-c3/c4、kl1-c1/c4/c5、kl2-c2/c3/c4/c5；保留既有问题与 gold 细节不完全对齐的限制，不追溯改变旧分数。

## 冻结完整性与升级意见

[独立 hash 核对实物](independent-review-hashes.json) 确认：候选 `dd225edf2c286619eae0d299228975c6fecee9f0` 的 **64 个文件按 Git 对象字节**全部与 candidate 清单相同；输入 **31 个文件**、原始产物 **496 个文件**全部匹配。raw-seal SHA-256 为 `e744c169e6b7cefd343761718dc3d469fce743dfea3dbd81ef2675f487883cff`；summary 的 candidate 和 input hash 也一致。该核对时工作树仍与候选相同；之后的明确后置补丁不改变已验证的 Git 对象或本次原始结果。

本次十轮是 **4 个 SUCCEEDED、2 个超时、4 个依赖阻断**。四个成功终态包含 kl-t2 的澄清，只有三个事实报告；“任务成功/可在历史中查看”不能等同于“已给出所问答案”。旧六轮本次均没有最终答案，不能用算术或覆盖规则的离线通过替代这六轮的真实质量结果。

建议保留本轮代码和过程证据供审阅，但**暂不据此升级主服务或宣布答案质量验收通过**。已发现的 Harbor 覆盖守卫误识别若在封存后做最小补丁，只能作为后置离线回放结果单独记录；不得把补丁效果写入 `dd225edf` 的真实推理成绩，也不能据两次超时就认定全部由该单一缺陷造成。本复核没有重新运行模型。
