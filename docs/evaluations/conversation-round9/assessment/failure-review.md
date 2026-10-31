# Round 9 失败排查：区分返回、复核与交付

本次候选 `77b7a55` 的真实回归只提交一次。以下结论来自封存的 [调用审计](call-audit.json)、[复核审计](review-audit.json)、[旧六轮评阅](old-review.md)和[原四轮评阅](new-review.md)。后置诊断修复不恢复已丢失内容，不覆盖原记录。

| 问题 | 已确认 | 排除或尚未确认 |
| --- | --- | --- |
| Cedar / Harbor 首轮未交付 | 各2次decision、1次review；review HTTP200返回且用量已知；分别余15.133/26.234秒进入review_error，无正式answer | 不是这两轮的预算耗尽；worker502不能归因于provider502。失败正文、finish_reason及具体校验原因未保存，无法确认是JSON/截断、ID分区、字段还是引用检查 |
| ar-t2超时 | review开始时余49.311秒，约49.665秒后HTTP200返回；有已知token，worker内时刻75.354秒，预算已耗尽，未发布结果 | 不是“没有收到供应商响应”；不根据这些时序猜测排队、网络或模型处理的各自耗时 |
| kl-t1超时 | review开始时余46.930秒，约48.062秒后传输超时；provider接受状态/该次token未知 | unknown不当0；不能用已知总量推导完整费用。HTTP客户端和收尾有小幅越过75秒，75秒不是精确实时强制中断 |
| 稀疏复核是否提速 | 唯一成功ar-t1的5行全部替换，retained为空；原始响应与review_completed审计一致 | 没有成功真实保留案例；其他失败的短completion不能当优化成功；无法据本轮宣称稳定token或延迟收益 |
| 规则类型/派生量是否修好了Cedar | 有离线回归、资格检查、原输入副本对照及反例；实际ar-t1出现rule_evaluation | Cedar未交付，真实整体判定质量未验收；离线规则正确不代表LLM能稳定选对类型、引文或协议 |

## 为什么旧记录无法补回原因

候选在 `_generation_json` 提供可选observer，但 WorkspaceAgent 未接该观察器；成功构建 `generation_review` 后才保存原始reviewed响应。失败时通用RAGError又被转成统一review_error。因此HTTP生命周期记录能证明有返回，却不足以重建返回正文。审阅旧草稿、估算completion token或重放一个假设响应，都不能证明旧模型实际输出过什么。本轮没有为了补原因重调模型。

## 封存后的有限诊断修复

后置版本接入只用于审计的observer：若正文已进入解析流程，保存finish_reason、UTF-8字节数、全文SHA-256及最多16KiB前缀，明确截断与accepted_report=false。restore/schema/quotation失败另留异常类型和最多1,024字符校验信息；统一错误文案不转发任意provider异常。以既有 `tool_rejected` 事件保存，Java错误结果仍能透传；不当作review_completed、不返回草稿、不作为新模型输入，也不增加重试。

独立审查发现新增observer对`message=null/string/list`的`.get`可能抛未处理AttributeError；增加choice/message对象检查，转成受控invalid JSON，缺失内容明确标记未观测。其他回归发现旧测试替身没有接收observer参数，以及任意RAGError文本不能进入公开结果；分别修正测试签名和固定错误文案。这些都是后置工程修复，原模型strict分数不变。

校验：缺ID列表、重叠/未知ID、非法JSON、finish_reason=length、超长非法行、畸形envelope、取消和超时；保存失败正文不等于接受该正文。若响应在`_post`已因预算到期被拒，或传输从未返回，解析observer仍无正文，必须继续保留未观测状态。[独立代码审查](../post-seal-code-review.md)、[后置检查](../post-seal-checks.json)。

下一次实验先固定小规模协议验收和失败分类，确认真实保留/替换可可靠返回后再决定是否保留该协议；不继续凭同十题成绩堆叠规则，也不借增加超时、放宽gold或补采制造提升。
