# Round 8 评测工具准备与难题

## 范围和继承

`evaluate_conversation_round8.py`、`score_conversation_round8.py` 和 `test_round8_evaluation_harness.py` 独立版本化。Round 5 输入、两套 gold、Round 6 / 7 源码、raw 与分数均不改动。本轮仍是开发者已阅读的十题回归，不能测量泛化。

沿用 Round 7 的 75 项离线保护：函数及 CLI 显式 GO、同协议一次 claim、10 提交 / 70 尝试、每任务 6+1、75 秒、1800/2600 输出上限、失败依赖阻断、unknown 不作零、逐请求 token 与 aggregate 一致、原始字节封存及前后源码/输入身份、原 gold、完整标注和 strict 自洽性。

## 新的处理选择

| 难题 | 选择 | 验证和限制 |
| --- | --- | --- |
| 上轮后续覆盖修复与真实评测候选不同，容易误称已在线修好 | 输入绑定 Round 7 原报告、分数、失败复核及完整 grading manifest；验证原 raw seal 和标注 seal 的关联 | 哈希绑定的是历史版本，不是因果证明。Round 7 的 619c051 是模型采集候选，后续覆盖修复仅离线验证。 |
| 旧 prepare 曾保留未执行 v1→v2 的特殊入口 | Round 8 直接拒绝覆盖现有输入冻结，采集目录存在也拒绝 prepare | 两个离线负例确认失败时原文件不变；审查必须发生在首次冻结前。 |
| 只绑定分数而不核查对应历史失败记录，可能丢失评分边界 | 校验 Round 7 全部已封存评阅文件与 grading manifest，全部加入新输入身份 | 修改 scores、failure review 或 raw 字节均阻止 prepare；grading 指向不同 raw seal 也阻止。 |
| 执行汇总曾由评分器读取，却未被显式列为必需的 sealed 文件 | 把 run-summary.json 加入评分必需封存文件 | 删除 seal 中该条目后，即使文件仍在磁盘也拒绝正式评分。正常 collector 本就封存汇总，本修复防止格式不完整的证据集合被评分。 |
| 引文存在而不蕴含对应对象或结论 | 继续人工式逐条 AI 阅读最终回答、引用正文及检索轨迹；六维 / 六轴与补充诊断保持原标准 | 工具只能拒绝格式不完整、身份变化或自相矛盾的标注，不能自动证明语义正确。 |

## 离线验证

初次准备 `tests/test_round8_evaluation_harness.py`：**83 passed**，其中 75 项继承、7 项历史绑定及冻结保护新增，1 项执行汇总封存检查新增。没有网络或模型调用。归一化检查逐字对照原始问题和资料，并保持依赖、状态及 source 不变。

当前未运行 prepare，未生成 input-freeze，未启动采集。源码版本及隔离服务须由 root 在执行前确认；这份说明不构成模型质量通过。
