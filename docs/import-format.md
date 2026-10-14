# 导入其他实验

`evaluation/experiment.schema.json` 定义数组格式；每条实验必须包含项目、数据集、切分标识、样本数、正确题数、准确率、输出长度与单位、模型配置、来源路径及 SHA-256、是否合成、是否参与检查点选择。`tokenizer_tokens` 还必须注明 tokenizer 标识。准确率采用 0–1 比例。

```bash
python scripts/import_experiments.py evaluation/fixtures/synthetic-runs.json --output reports/synthetic-data
```

```python
from pathlib import Path
from lablens.tools import ToolBox
from lablens.agent import Agent
from lablens.model import HTTPModel
agent = Agent(HTTPModel(), tools=ToolBox(Path("reports/synthetic-data")))
result = agent.run("比较 run:synthetic-0 和 run:synthetic-1")
```

合成夹具没有真实模型运行，不能用作简历效果。其两个原始 JSON 也已提供，可复算哈希。真实 DPO 数据保留原适配器：

```bash
python scripts/import_dpo.py /path/to/dpo-cot-compression --output reports/reimported-dpo
```

DPO 适配器重新读取原文件并计算哈希；通用导入器校验哈希格式，并不通过哈希证明来源真实性，也不访问用户填写的 source_path。通用数据目录默认生成空 notes.json，可自行添加对应项目的方法说明；不要拷贝 DPO 的方法说明套用于其他项目。源目录未提供时不能独立验证哈希对应文件。

加载时再次验证每条记录：有限数值、正确题数与准确率一致、非空必填字段、无重复 ID、来源路径为相对标识。不同项目、数据集、切分、样本数、长度单位、tokenizer 或合成标识的记录可以共同列出，但不能互相比较。相同 split_id 仍是提供者的声明，不能证明逐题顺序或样本完全一致；显著性分析需要另行提供配对预测。
