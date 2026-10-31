# Round 6 评测脚本交叉审查

范围是评测执行与汇总脚本的只读内部 AI 交叉审查，未调用外部模型或 HTTP 服务，不是独立人工验收。脚本作者在评测开始前修复了审查中发现的问题；原始 gold、Round 5 分数与 raw 不变。

审查发现的三个问题及处理：

| 问题 | 对评测的影响 | 评测开始前的修复 |
| --- | --- | --- |
| input freeze 原来只在启动时核验，结束汇总重新读取当前清单 hash | 若输入或 gold 清单在中途变化，可能绑定到不同清单 | 每次提交前及结束时核验批准的固定清单 hash 与文件，summary 直接保留批准值 |
| 只检查汇总用量的 flag / 类型，未用 request trace 交叉检查 | 负 token、未结束调用或 provider unknown 可能被当成完整已知 | 负数标为 unknown；未闭合或不确定的 request trace 强制保留 unknown |
| 评分依据只要求 evidence_pointers 是非空 list | `[None]`、不存在文件等也可被视为“有证据指针” | 只接受封存 raw/input 集合中的真实文件，并验证 hash、JSON pointer 或行号范围 |

保存了 [首次离线探针](harness-cross-review-initial.json)。该探针执行时，作者已经并行修复前两项：负 token / trace unknown 正确标为 unknown；当时 scorer 仍接受 `[None]`。这是开发中间状态，不能据此说最终脚本仍有同一漏洞。

[修复后复核](harness-cross-review-fixed.json) 记录了所审两个脚本的实际 SHA-256：合法封存 JSON pointer 能通过；null、缺失文件、越界路径、缺失 JSON 目标均拒绝；批准输入清单能通过、被修改清单拒绝；trace uncertainty 与负 token 保持 unknown。10 个离线检查（含检查时源码 hash 未变）全部通过。第一次手工复核 fixture 使用未解析的 macOS `/var` 临时目录别名而被路径守卫拒绝，随后仅将 fixture root `.resolve()` 后重试；生产解析器本就解析 root，未为通过测试放松路径约束。

另外静态检查了 70 次总尝试上限、单任务 6 decision + 1 review、未知尝试数停止提交、失败前置阻断后续、十题计划分母保留、两套原 gold 分开汇总，以及不将引文存在替代语义评分，未发现这些路径存在明显放宽。最终运行仍须由批准配置、冻结源码、原始任务与评分证据共同证明；离线探针不能证明实际模型调用遵循全部预算，也不能证明人工标注的语义正确。
