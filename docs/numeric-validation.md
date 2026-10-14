# 数值证据校验

v0.1 仅检查 source_id 是否被本轮工具读取。v0.2 在每条 claim 中增加 measurements：指标、基线、候选、值、单位、方向。校验器只使用本轮成功工具的输出，不允许从未读取的全量数据中补证据。

绝对指标 accuracy 使用 fraction、correct/num_samples 使用 count、mean_length 使用原记录的单位。比较指标的有序基线与候选必须对应实际 compare_runs 返回；accuracy_delta_percentage_points 和 additional_correct 是候选减基线，length_reduction_percent 是（基线−候选）/基线。计数必须精确相等；比例 accuracy 的绝对容差 0.00005，其余长度/百分点/百分比容差 0.005，支持常用小数舍入。

错误值、单位、方向、交换比较顺序、未读来源、NaN/Infinity 均拒绝，并在原 8 次模型 / 12 次工具上限内反馈修正。含阿拉伯数字的 claim 必须附结构化 measurement；这个检查不能把自然语言中的每个数字自动对应到字段，中文数字和 limitations 也不是完整语义验证。即使字段通过，文字仍可能和字段矛盾，仍需逐题审核；不得宣传为全自动事实验证。

页面显示经过校验的结构化数值，用户可以直接核对。完整模型消息、工具输出和修正错误保存为轨迹。原版本与新版本相同开发题的耗时和用量对照见 v0.2 评测报告；协议完成与任务成功分别报告。
