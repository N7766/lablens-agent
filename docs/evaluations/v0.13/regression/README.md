# 已知生成失败的开发回归记录

固定输入为`evaluation/regressions/v013/known-failures.json`，保留旧错误当时取得的每题8份资料。此处隔离生成，**没有检索质量分数**。原始问题d01/h01是开放排查问题，targeted题只问具体旧错误，不替代原问题的完整性验收。

| 目录 | 性质与结果 |
| --- | --- |
| first-run | 首次复现，单次生成；引用失败和无依据归因保留。 |
| review-pass | 引入同模型复核，仍有原文复制/结构失败。 |
| final-regression | 开发候选，仍未解决全部引用失败；目录名不代表最终验收通过。 |
| schema-candidate | 临时strict-schema包装试验，仍有原文复制失败和超时；包装代码另存runner.py.txt，不能只用rag.py哈希复现该候选。 |
| passage-selection | 模型改为选择片段ID，4题均返回，但解释仍有语义问题。 |
| boundary-final | 4题新生成；原语义评审边界2/4、完整1/4，保留错句。 |
| guard-replay | 保存raw review的零调用重放；原评审边界3/4、完整1/4。 |
| guard-final-replay | 继续窄规则修复的零调用重放；更严格另一AI审查1 pass、3 partial，两个开放问题仍partial。不同评审口径不画成连续提升曲线。 |
| final-live-targeted | 最终实现2个开发定向题，4次真实调用，无自动重试；语义1 pass、1 fail。配置/历史边界通过，SFT基线已找到，但复核将validation误写test导致整题失败。 |

各目录保留metadata、原始回答、用量；有语义审查的目录同时保留逐条MD/JSON。早期轮次不是独立保留集；候选历史只保留当时输出/哈希，除明确另存源码者外，不声称完整重建每个未提交中间版本。

真实推理runner：

```sh
python scripts/evaluate_generation_regressions.py --env-file /path/to/local.env \
  --only-targeted --output /path/to/new-empty-result-directory
```

命令会调用配置模型，每题初稿/复核2次，不做自动重试；不要把此运行当作新的盲测。未参与本轮开发的评测助手另做的网上取材题组见`evaluation/independent_v013/RESULTS.md`。
