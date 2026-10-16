# ` LabLens 证据报告 `

封装数据已重算通过。本报告只说明收据内列出的查询或计划步骤；不证明来源真实、意图正确或需求完整。静态报告可被编辑，请保留原始收据并重新验证。

| 项目 | 值 |
| --- | --- |
| ` 报告版本 ` | ` 0.11.0 ` |
| ` 收据格式 ` | ` lablens-query-receipt/v1 ` |
| ` 重算结果 ` | ` verified ` |
| ` 快照 SHA-256 标识 ` | ` snapshot:660745ddaf4b8694f5ef28957b96af0e3ae46cc4edf0e2e1e57eab4313ea484a ` |
| ` 收据内容 SHA-256 ` | ` feb70efeba5303e43deeebab8f2dbeb7252e4d8d87342d3203d827b13fefa98f ` |
| ` 本次导出的模型调用次数 ` | ` 0 ` |
| ` 包含合成数据 ` | ` 是 ` |

本次导出使用封装数据进行确定性重算，模型调用为 0。收据不记录完整的历史调用与费用，因此本报告不推断过去的模型调用次数或费用。source_path、source 和 source_sha256 仅作为原有来源文本展示，未读取对应文件、访问链接或认证来源。

## ` 查询范围 `

此查询收据没有保存原始自然语言问题。报告仅重算记录的查询参数，不推测原问题，也不进行原文映射、查询意图或任务规则核对。

### ` 查询条件与参数 `

```text
准确率 0 ≤ accuracy ≤ 1
```

```text
正确题数 ≥ 80
```

```text
correct 升序；指标相同按ID排序
```

```text
取前1条，保留边界全部并列
```

### ` 完整查询参数 `

```json
{
  "min_accuracy": 0.0,
  "max_accuracy": 1.0,
  "min_correct": 80,
  "max_length": null,
  "shorter_than_baseline": null,
  "more_accurate_than_baseline": null,
  "order_by": "correct",
  "descending": false,
  "top_k": 1,
  "include_ties": true,
  "pareto_only": false
}
```

| 查询 ID | 截断前匹配记录数 | 最终记录数 |
| --- | --- | --- |
| ` query:f2343ab7fe410594c55e398e6a7acee425292e01f0aed66b72819a9bf50bfa4f ` | ` 5 ` | ` 2 ` |

下表保留全部结果及执行顺序；平均长度使用每行标注的单位。

| 序号 | 记录 ID | 标签 | 准确率（fraction） | 正确题数（count） | 样本数（count） | 平均长度 | 长度单位 | 合成数据 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| ` 1 ` | ` run:sample-a ` | ` Synthetic A A ` | ` 0.8 ` | ` 80 ` | ` 100 ` | ` 200.0 ` | ` whitespace_words ` | ` 是 ` |
| ` 2 ` | ` run:sample-e ` | ` Synthetic A E ` | ` 0.8 ` | ` 80 ` | ` 100 ` | ` 80.0 ` | ` whitespace_words ` | ` 是 ` |

## ` 封装快照的来源与配置 `

此附录列出整个封装快照，不表示每条记录都被查询选中或参与比较。synthetic 为“是”的记录属于合成数据。资料原文不等于经过认证的事实。

### ` run:sample-a · Synthetic A A `

| 字段 | 快照中的值 |
| --- | --- |
| ` project ` | ` lablens-v08-synthetic ` |
| ` dataset ` | ` synthetic-words-v08 ` |
| ` split_id ` | ` synthetic-a ` |
| ` length_unit ` | ` whitespace_words ` |
| ` tokenizer ` | ` 未记录 ` |
| ` used_for_checkpoint_selection ` | ` 否 ` |
| ` synthetic ` | ` 是 ` |
| ` source_path ` | ` evaluation/v0.8/fixtures/source-a.json ` |
| ` source_sha256 ` | ` ca3a1523fb39a075bda0537d19b464d980eaa7047bb0e188a5a33bcb8b83f303 ` |

### ` 记录的模型配置 `

```json
{
  "fixture": "hand-authored",
  "no_model_execution": "true"
}
```

### ` run:sample-b · Synthetic A B `

| 字段 | 快照中的值 |
| --- | --- |
| ` project ` | ` lablens-v08-synthetic ` |
| ` dataset ` | ` synthetic-words-v08 ` |
| ` split_id ` | ` synthetic-a ` |
| ` length_unit ` | ` whitespace_words ` |
| ` tokenizer ` | ` 未记录 ` |
| ` used_for_checkpoint_selection ` | ` 否 ` |
| ` synthetic ` | ` 是 ` |
| ` source_path ` | ` evaluation/v0.8/fixtures/source-a.json ` |
| ` source_sha256 ` | ` ca3a1523fb39a075bda0537d19b464d980eaa7047bb0e188a5a33bcb8b83f303 ` |

### ` 记录的模型配置 `

```json
{
  "fixture": "hand-authored",
  "no_model_execution": "true"
}
```

### ` run:sample-c · Synthetic A C `

| 字段 | 快照中的值 |
| --- | --- |
| ` project ` | ` lablens-v08-synthetic ` |
| ` dataset ` | ` synthetic-words-v08 ` |
| ` split_id ` | ` synthetic-a ` |
| ` length_unit ` | ` whitespace_words ` |
| ` tokenizer ` | ` 未记录 ` |
| ` used_for_checkpoint_selection ` | ` 否 ` |
| ` synthetic ` | ` 是 ` |
| ` source_path ` | ` evaluation/v0.8/fixtures/source-a.json ` |
| ` source_sha256 ` | ` ca3a1523fb39a075bda0537d19b464d980eaa7047bb0e188a5a33bcb8b83f303 ` |

### ` 记录的模型配置 `

```json
{
  "fixture": "hand-authored",
  "no_model_execution": "true"
}
```

### ` run:sample-d · Synthetic A D `

| 字段 | 快照中的值 |
| --- | --- |
| ` project ` | ` lablens-v08-synthetic ` |
| ` dataset ` | ` synthetic-words-v08 ` |
| ` split_id ` | ` synthetic-a ` |
| ` length_unit ` | ` whitespace_words ` |
| ` tokenizer ` | ` 未记录 ` |
| ` used_for_checkpoint_selection ` | ` 否 ` |
| ` synthetic ` | ` 是 ` |
| ` source_path ` | ` evaluation/v0.8/fixtures/source-a.json ` |
| ` source_sha256 ` | ` ca3a1523fb39a075bda0537d19b464d980eaa7047bb0e188a5a33bcb8b83f303 ` |

### ` 记录的模型配置 `

```json
{
  "fixture": "hand-authored",
  "no_model_execution": "true"
}
```

### ` run:sample-e · Synthetic A E `

| 字段 | 快照中的值 |
| --- | --- |
| ` project ` | ` lablens-v08-synthetic ` |
| ` dataset ` | ` synthetic-words-v08 ` |
| ` split_id ` | ` synthetic-a ` |
| ` length_unit ` | ` whitespace_words ` |
| ` tokenizer ` | ` 未记录 ` |
| ` used_for_checkpoint_selection ` | ` 否 ` |
| ` synthetic ` | ` 是 ` |
| ` source_path ` | ` evaluation/v0.8/fixtures/source-a.json ` |
| ` source_sha256 ` | ` ca3a1523fb39a075bda0537d19b464d980eaa7047bb0e188a5a33bcb8b83f303 ` |

### ` 记录的模型配置 `

```json
{
  "fixture": "hand-authored",
  "no_model_execution": "true"
}
```

### ` run:sample-f · Synthetic A F `

| 字段 | 快照中的值 |
| --- | --- |
| ` project ` | ` lablens-v08-synthetic ` |
| ` dataset ` | ` synthetic-words-v08 ` |
| ` split_id ` | ` synthetic-a ` |
| ` length_unit ` | ` whitespace_words ` |
| ` tokenizer ` | ` 未记录 ` |
| ` used_for_checkpoint_selection ` | ` 否 ` |
| ` synthetic ` | ` 是 ` |
| ` source_path ` | ` evaluation/v0.8/fixtures/source-a.json ` |
| ` source_sha256 ` | ` ca3a1523fb39a075bda0537d19b464d980eaa7047bb0e188a5a33bcb8b83f303 ` |

### ` 记录的模型配置 `

```json
{
  "fixture": "hand-authored",
  "no_model_execution": "true"
}
```

## ` 封装快照的全部资料原文 `

### ` note:limits · 证据边界 `

```text
这些工程样例未记录 seed、GPU 型号或数量、训练费用、逐题预测、推理时延、峰值显存；不能据此推断实际收益或统计显著性。
```

| 资料 ID | 来源文本（仅标签，未打开或认证） |
| --- | --- |
| ` note:limits ` | ` evaluation/v0.8/fixtures/source-a.json ` |

### ` note:source · 合成来源 `

```text
快照 A 的所有记录均为人工构造的合成数值，未训练或评测任何模型；来源说明文件为 evaluation/v0.8/fixtures/source-a.json，哈希仅标识该说明文件字节。
```

| 资料 ID | 来源文本（仅标签，未打开或认证） |
| --- | --- |
| ` note:source ` | ` evaluation/v0.8/fixtures/source-a.json ` |

### ` note:units · 长度单位 `

```text
长度单位为 whitespace_words，表示合成的按空格切分词数；不是 tokenizer token 数。
```

| 资料 ID | 来源文本（仅标签，未打开或认证） |
| --- | --- |
| ` note:units ` | ` evaluation/v0.8/fixtures/source-a.json ` |

## ` 原始收据与离线复核 `

复制以下完整 JSON 并保存为 receipt.json，可使用 lablens verify receipt.json 离线重算。内容哈希不是数字签名；同时修改数据和哈希可以形成新的自洽收据，因此重算通过不证明真实性。

### ` 完整原始收据 JSON（可复制） `

```json
{
  "format": "lablens-query-receipt/v1",
  "snapshot_id": "snapshot:660745ddaf4b8694f5ef28957b96af0e3ae46cc4edf0e2e1e57eab4313ea484a",
  "query": {
    "min_accuracy": 0,
    "max_accuracy": 1,
    "min_correct": 80,
    "max_length": null,
    "shorter_than_baseline": null,
    "more_accurate_than_baseline": null,
    "order_by": "correct",
    "descending": false,
    "top_k": 1,
    "include_ties": true,
    "pareto_only": false
  },
  "dataset": {
    "runs": [
      {
        "id": "run:sample-a",
        "label": "Synthetic A A",
        "project": "lablens-v08-synthetic",
        "dataset": "synthetic-words-v08",
        "split_id": "synthetic-a",
        "num_samples": 100,
        "correct": 80,
        "accuracy": 0.8,
        "mean_length": 200.0,
        "length_unit": "whitespace_words",
        "tokenizer": null,
        "model_config_details": {
          "fixture": "hand-authored",
          "no_model_execution": "true"
        },
        "used_for_checkpoint_selection": false,
        "source_path": "evaluation/v0.8/fixtures/source-a.json",
        "source_sha256": "ca3a1523fb39a075bda0537d19b464d980eaa7047bb0e188a5a33bcb8b83f303",
        "synthetic": true
      },
      {
        "id": "run:sample-b",
        "label": "Synthetic A B",
        "project": "lablens-v08-synthetic",
        "dataset": "synthetic-words-v08",
        "split_id": "synthetic-a",
        "num_samples": 100,
        "correct": 95,
        "accuracy": 0.95,
        "mean_length": 220.0,
        "length_unit": "whitespace_words",
        "tokenizer": null,
        "model_config_details": {
          "fixture": "hand-authored",
          "no_model_execution": "true"
        },
        "used_for_checkpoint_selection": false,
        "source_path": "evaluation/v0.8/fixtures/source-a.json",
        "source_sha256": "ca3a1523fb39a075bda0537d19b464d980eaa7047bb0e188a5a33bcb8b83f303",
        "synthetic": true
      },
      {
        "id": "run:sample-c",
        "label": "Synthetic A C",
        "project": "lablens-v08-synthetic",
        "dataset": "synthetic-words-v08",
        "split_id": "synthetic-a",
        "num_samples": 100,
        "correct": 90,
        "accuracy": 0.9,
        "mean_length": 140.0,
        "length_unit": "whitespace_words",
        "tokenizer": null,
        "model_config_details": {
          "fixture": "hand-authored",
          "no_model_execution": "true"
        },
        "used_for_checkpoint_selection": false,
        "source_path": "evaluation/v0.8/fixtures/source-a.json",
        "source_sha256": "ca3a1523fb39a075bda0537d19b464d980eaa7047bb0e188a5a33bcb8b83f303",
        "synthetic": true
      },
      {
        "id": "run:sample-d",
        "label": "Synthetic A D",
        "project": "lablens-v08-synthetic",
        "dataset": "synthetic-words-v08",
        "split_id": "synthetic-a",
        "num_samples": 100,
        "correct": 90,
        "accuracy": 0.9,
        "mean_length": 100.0,
        "length_unit": "whitespace_words",
        "tokenizer": null,
        "model_config_details": {
          "fixture": "hand-authored",
          "no_model_execution": "true"
        },
        "used_for_checkpoint_selection": false,
        "source_path": "evaluation/v0.8/fixtures/source-a.json",
        "source_sha256": "ca3a1523fb39a075bda0537d19b464d980eaa7047bb0e188a5a33bcb8b83f303",
        "synthetic": true
      },
      {
        "id": "run:sample-e",
        "label": "Synthetic A E",
        "project": "lablens-v08-synthetic",
        "dataset": "synthetic-words-v08",
        "split_id": "synthetic-a",
        "num_samples": 100,
        "correct": 80,
        "accuracy": 0.8,
        "mean_length": 80.0,
        "length_unit": "whitespace_words",
        "tokenizer": null,
        "model_config_details": {
          "fixture": "hand-authored",
          "no_model_execution": "true"
        },
        "used_for_checkpoint_selection": false,
        "source_path": "evaluation/v0.8/fixtures/source-a.json",
        "source_sha256": "ca3a1523fb39a075bda0537d19b464d980eaa7047bb0e188a5a33bcb8b83f303",
        "synthetic": true
      },
      {
        "id": "run:sample-f",
        "label": "Synthetic A F",
        "project": "lablens-v08-synthetic",
        "dataset": "synthetic-words-v08",
        "split_id": "synthetic-a",
        "num_samples": 100,
        "correct": 70,
        "accuracy": 0.7,
        "mean_length": 80.0,
        "length_unit": "whitespace_words",
        "tokenizer": null,
        "model_config_details": {
          "fixture": "hand-authored",
          "no_model_execution": "true"
        },
        "used_for_checkpoint_selection": false,
        "source_path": "evaluation/v0.8/fixtures/source-a.json",
        "source_sha256": "ca3a1523fb39a075bda0537d19b464d980eaa7047bb0e188a5a33bcb8b83f303",
        "synthetic": true
      }
    ],
    "notes": [
      {
        "id": "note:limits",
        "title": "证据边界",
        "text": "这些工程样例未记录 seed、GPU 型号或数量、训练费用、逐题预测、推理时延、峰值显存；不能据此推断实际收益或统计显著性。",
        "source": "evaluation/v0.8/fixtures/source-a.json"
      },
      {
        "id": "note:source",
        "title": "合成来源",
        "text": "快照 A 的所有记录均为人工构造的合成数值，未训练或评测任何模型；来源说明文件为 evaluation/v0.8/fixtures/source-a.json，哈希仅标识该说明文件字节。",
        "source": "evaluation/v0.8/fixtures/source-a.json"
      },
      {
        "id": "note:units",
        "title": "长度单位",
        "text": "长度单位为 whitespace_words，表示合成的按空格切分词数；不是 tokenizer token 数。",
        "source": "evaluation/v0.8/fixtures/source-a.json"
      }
    ]
  },
  "result": {
    "query_id": "query:f2343ab7fe410594c55e398e6a7acee425292e01f0aed66b72819a9bf50bfa4f",
    "snapshot_id": "snapshot:660745ddaf4b8694f5ef28957b96af0e3ae46cc4edf0e2e1e57eab4313ea484a",
    "parameters": {
      "min_accuracy": 0.0,
      "max_accuracy": 1.0,
      "min_correct": 80,
      "max_length": null,
      "shorter_than_baseline": null,
      "more_accurate_than_baseline": null,
      "order_by": "correct",
      "descending": false,
      "top_k": 1,
      "include_ties": true,
      "pareto_only": false
    },
    "matched_before_top_k": 5,
    "ordered_run_ids": [
      "run:sample-a",
      "run:sample-e"
    ],
    "runs": [
      {
        "id": "run:sample-a",
        "label": "Synthetic A A",
        "project": "lablens-v08-synthetic",
        "dataset": "synthetic-words-v08",
        "split_id": "synthetic-a",
        "num_samples": 100,
        "correct": 80,
        "accuracy": 0.8,
        "mean_length": 200.0,
        "length_unit": "whitespace_words",
        "tokenizer": null,
        "model_config_details": {
          "fixture": "hand-authored",
          "no_model_execution": "true"
        },
        "used_for_checkpoint_selection": false,
        "source_path": "evaluation/v0.8/fixtures/source-a.json",
        "source_sha256": "ca3a1523fb39a075bda0537d19b464d980eaa7047bb0e188a5a33bcb8b83f303",
        "synthetic": true
      },
      {
        "id": "run:sample-e",
        "label": "Synthetic A E",
        "project": "lablens-v08-synthetic",
        "dataset": "synthetic-words-v08",
        "split_id": "synthetic-a",
        "num_samples": 100,
        "correct": 80,
        "accuracy": 0.8,
        "mean_length": 80.0,
        "length_unit": "whitespace_words",
        "tokenizer": null,
        "model_config_details": {
          "fixture": "hand-authored",
          "no_model_execution": "true"
        },
        "used_for_checkpoint_selection": false,
        "source_path": "evaluation/v0.8/fixtures/source-a.json",
        "source_sha256": "ca3a1523fb39a075bda0537d19b464d980eaa7047bb0e188a5a33bcb8b83f303",
        "synthetic": true
      }
    ]
  },
  "content_sha256": "feb70efeba5303e43deeebab8f2dbeb7252e4d8d87342d3203d827b13fefa98f"
}
```

