# Round19b review-decisions-v4 设计与 XGrammar 核验

核验日期：2026-11-11。范围：官方文档/固定版本源码与本地依赖检查，未调用模型或平台、未安装依赖、未执行 grammar compile。生产默认保留 v3；v4 是必须显式选择、独立冻结的新候选，不能将本轮 v3 的真实失败改写为通过。

## 官方依据与推断边界

1. 官方 JSON Generation 文档给出了 `GrammarCompiler.compile_json_schema` 及含 `required` 对象字段的调用路径。[官方教程](https://xgrammar.mlc.ai/docs/latest/defining_structures/json_generation.html)
2. 官方 0.2.8 Grammar API 文档说明 `any_order=False` 默认要求声明顺序、必填键与唯一性；`any_order=True` 会放松键存在性和唯一性，影响嵌套对象。不要为了接受任意键顺序打开该选项。服务端仍须独立拒绝重复键和缺失 ID。[API 说明](https://xgrammar.mlc.ai/docs/latest/api/python/grammar.html#xgrammar.Grammar.from_json_schema)
3. 官方 Structural Tag 文档说明 `additionalProperties:false` 即使在任意顺序模式仍限制未声明字段；但它不能补回该模式丢失的必填/唯一性约束。[属性顺序说明](https://xgrammar.mlc.ai/docs/latest/structural_tag/structural_tag.html#property-ordering-with-any-order)
4. 固定 v0.2.8 源码的 `SchemaParser::ParseObject` 对属性子 schema 递归调用 Parse，记录 required；`ParseAnyOf` 对每个分支递归解析。这支持“对象属性内部包含 anyOf”的源码级判断。将 action 的枚举、properties、required、additionalProperties:false 全部放在各自分支内部，anyOf 包装层不再放公共约束；其解析分派优先处理 anyOf，不应依赖旁边的 sibling constraints 自动合取。[固定源码](https://github.com/mlc-ai/xgrammar/blob/v0.2.8/cpp/json_schema_converter.cc)
5. 固定 v0.2.8 官方测试 `test_anyof_oneof` 覆盖属性里的 anyOf，并包含合法值接受与错误对象值拒绝；`test_union` 覆盖对象联合，另有 required 和 additionalProperties 的对象测试。这是上游测试的存在与内容核对，并非本机重新执行。[固定测试](https://github.com/mlc-ai/xgrammar/blob/v0.2.8/tests/python/test_json_schema_converter.py)

结论：所提“required 固定 ID 对象 + 分支内完整约束”的结构有上游实现与测试支持。没有对 LabLens 最终 v4 schema 实际编译，不能声称该候选已通过 vLLM/XGrammar 集成或 GPU 兼容验收。官方 `/latest/` 文档会变化；上述 v0.2.8 源码/测试链接限定了本次审查版本。

## 本机可用性

仓库 `.venv` 为 Python 3.11.13。`find_spec` 与 distribution metadata 检查结果：xgrammar、torch、tvm_ffi、jsonschema 均不存在。未搜索其他项目虚拟环境，未安装或下载任何 wheel/模型。因此本次没有可立即使用的仓库内 XGrammar 编译器。

后续若在已具备相同 runtime 的环境离线验证，可先 `Grammar.from_json_schema(schema, any_order=False)`，然后用 matcher 检查合法/恶意序列；这种离线语法接受检查也不等于模型生成成功。不要因这个步骤创建新付费实例。

## 集成要求

- `LABLENS_REVIEW_PROTOCOL` / config 仅接纳约定的精确 v3/v4 标识；默认 v3，未知值失败。私有 worker 的 v4 必须显式记录。
- probe 的 freeze 参数、计划协议字段、实际生产 payload 与 decoder 必须一致；run 不允许覆盖。按被冻结 payload 的明确协议分流，不能按响应形状猜测或回退。
- 原始 v3 `review_delta.py` 和真实失败材料保留。旧计划继续受到源码漂移保护；不得刷新旧 SHA 冒充原始运行环境。
- v4 每个原始 ID required；额外 ID 拒绝；不允许 retain 的行只给 replace 分支。retain 只能有 action；replace 不携带 statement/assessment_id，原命题从 draft 恢复。
- 全 retain 仍必须经过 evidence、scope、causal 和配置/历史检查。审计版本、实现哈希、响应原件与最终改写分别记录，不能把 schema 合法当语义正确。
- JSON 解析在构造 dict 之前递归拒绝重复键，包括转义后相同的键及嵌套 action；拒绝 NaN/Infinity。当前旧解析曾离线确认 `a1` 与 `\u00611` 会 last-key-win，新严格解析须消除该行为。

## 恶意输入与边界测试建议

1. 顶层 decisions 重复、a1 重复、`a1`/`\u00611` 重复、嵌套 action 重复；均使用原始 JSON 字符串，不能先用 Python dict 构造。
2. 缺少 a2、增加 a99、数字形状键、null/list/boolean 决策、空 decisions；禁止静默补齐或默认 retain。
3. 在不可 retain 的 placeholder/已知 finding 行选择 retain；replace 缺字段，retain 附带 evidence/verdict，非法 action，附加 statement/assessment_id。
4. 未知或重复 evidence ID、错误字段类型、空 explanation、过长字符串；生产解析必须拒绝或执行既有保护。
5. 合法 mixed/all-replace/all-retain；不同对象键顺序应在应用解析后恢复原 draft 顺序，且不修改输入对象。注意应用接受乱序不代表 XGrammar 固定顺序也接受。
6. 保留内容仍触发配置推断历史、因果或数值保护；语义盲区继续标为未认证。
7. v4 下返回旧 v3 双列表必须失败；v3 下返回 v4 必须失败；不得隐式格式猜测。
8. 协议字段篡改、payload 协议不一致、重新计算 plan SHA 试图绕过重建校验、run 参数覆盖；均在 HTTP 前拒绝。
9. 错误响应只产生既定一次请求，保留 intent/raw/usage/失败状态，无重试或自动 fallback；长度截断、超时路径保持原有限额。

## 尚未验证

最终 v4 schema 的实际 grammar compile、Qwen tokenizer 序列接受、vLLM 对 any_order 的集成设置、模型决策质量、真实调用成功率和性能均未验证。源码支持与 CPU 规则测试不能替代这些验收。
