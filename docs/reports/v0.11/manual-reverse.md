# ` LabLens 证据报告 `

封装数据已重算通过。本报告只说明收据内列出的查询或计划步骤；不证明来源真实、意图正确或需求完整。静态报告可被编辑，请保留原始收据并重新验证。

| 项目 | 值 |
| --- | --- |
| ` 报告版本 ` | ` 0.11.0 ` |
| ` 收据格式 ` | ` lablens-plan-receipt/v1 ` |
| ` 重算结果 ` | ` verified ` |
| ` 快照 SHA-256 标识 ` | ` snapshot:76521482a203764b032327edf1e05cb854f5b6179ea4860299883e39aaeb9f3b ` |
| ` 收据内容 SHA-256 ` | ` beba967b57716b55081e43a8d10a2484bf9e373f921f8ee510efe97cca82d1e1 ` |
| ` 本次导出的模型调用次数 ` | ` 0 ` |
| ` 包含合成数据 ` | ` 否 ` |

本次导出使用封装数据进行确定性重算，模型调用为 0。收据不记录完整的历史调用与费用，因此本报告不推断过去的模型调用次数或费用。source_path、source 和 source_sha256 仅作为原有来源文本展示，未读取对应文件、访问链接或认证来源。

## ` 原始问题与执行范围 `

```text
列出准确率最高的全部并列记录；以run:sft为基线比较run:dpo-best的正确题数差；说明验证集和显著性边界。
```

```text
仅列出的步骤：best, delta, limits
```

## ` 本次复核摘要 `

按 v0.11.0 规则重新核对；以下摘要不是执行当时的检查记录。详细规则结果位于结果表之后。

| 项目 | 本次结果 |
| --- | --- |
| ` 列出步骤数 ` | ` 3 ` |
| ` 声明未处理项数 ` | ` 0 ` |
| ` 查询规则存在冲突 ` | ` 否 ` |
| ` 比较与资料规则存在冲突 ` | ` 是 ` |
| ` 全部原句有完整映射或声明未处理 ` | ` 是 ` |

规则未覆盖的表达仍须人工核对。重算通过或没有冲突均不代表意图正确、需求完整或来源真实。

| 步骤 | 操作 | 重算结果摘要 |
| --- | --- | --- |
| ` best ` | ` query ` | ` 完整查询结果：1 行 ` |
| ` delta ` | ` compare ` | ` run:dpo-best → run:sft；正确题数差 -3 count ` |
| ` limits ` | ` describe ` | ` 资料说明：6 条 ` |

## ` 按计划顺序重算的结果 `

## ` 步骤 best · query `

```text
请求原文：列出准确率最高的全部并列记录
```

### ` 查询条件与参数 `

```text
准确率 0 ≤ accuracy ≤ 1
```

```text
正确题数 ≥ 0
```

```text
accuracy 降序；指标相同按ID排序
```

```text
取前1条，保留边界全部并列
```

### ` 完整查询参数 `

```json
{
  "min_accuracy": 0.0,
  "max_accuracy": 1.0,
  "min_correct": 0,
  "max_length": null,
  "shorter_than_baseline": null,
  "more_accurate_than_baseline": null,
  "order_by": "accuracy",
  "descending": true,
  "top_k": 1,
  "include_ties": true,
  "pareto_only": false
}
```

| 查询 ID | 截断前匹配记录数 | 最终记录数 |
| --- | --- | --- |
| ` query:7c3339471f6e3af99370fbd0778ae34f8513fb8c469be71241903608d2872e75 ` | ` 11 ` | ` 1 ` |

下表保留全部结果及执行顺序；平均长度使用每行标注的单位。

| 序号 | 记录 ID | 标签 | 准确率（fraction） | 正确题数（count） | 样本数（count） | 平均长度 | 长度单位 | 合成数据 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| ` 1 ` | ` run:dpo-best ` | ` stage5_lora_dpo_sft_lr1e-6_beta003_steps50/checkpoint-50 ` | ` 0.9567099567099567 ` | ` 442 ` | ` 462 ` | ` 31.528138528138527 ` | ` whitespace_words ` | ` 否 ` |

## ` 步骤 delta · compare `

```text
请求原文：以run:sft为基线比较run:dpo-best的正确题数差
```

```text
比较方向：run:dpo-best → run:sft
```

| 序号 | 记录 ID | 标签 | 准确率（fraction） | 正确题数（count） | 样本数（count） | 平均长度 | 长度单位 | 合成数据 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| ` 1 ` | ` run:dpo-best ` | ` stage5_lora_dpo_sft_lr1e-6_beta003_steps50/checkpoint-50 ` | ` 0.9567099567099567 ` | ` 442 ` | ` 462 ` | ` 31.528138528138527 ` | ` whitespace_words ` | ` 否 ` |
| ` 2 ` | ` run:sft ` | ` LoRA-SFT ` | ` 0.9502164502164502 ` | ` 439 ` | ` 462 ` | ` 32.422077922077925 ` | ` whitespace_words ` | ` 否 ` |

| 指标 | 计算值 | 单位 | 方向 / 公式 |
| --- | --- | --- | --- |
| ` 准确率差 ` | ` -0.6493506493506551 ` | ` percentage_points ` | ` 候选 − 基线 ` |
| ` 正确题数差 ` | ` -3 ` | ` count ` | ` 候选 − 基线 ` |
| ` 平均长度减少比例 ` | ` -2.835370039818769 ` | ` percent ` | ` (基线 − 候选) / 基线 × 100 ` |

### ` 比较所附的资料说明 `

### ` note:latency · 时延 显存 吞吐 latency memory throughput `

```text
导入摘要没有同环境下的推理时延、吞吐或峰值显存测量。输出词数减少不能直接证明推理更快或显存更低，需要固定硬件、批量与生成设置重新测量。
```

| 资料 ID | 来源文本（仅标签，未打开或认证） |
| --- | --- |
| ` note:latency ` | ` Imported summary field coverage ` |

### ` note:significance · 统计显著性 significance `

```text
准确率的点估计差异不等于统计显著。仅凭摘要中的正确题数无法恢复两个模型在每一题上的配对结果，因此当前工具不计算配对显著性检验或宣称显著提升。
```

| 资料 ID | 来源文本（仅标签，未打开或认证） |
| --- | --- |
| ` note:significance ` | ` Analysis policy; no paired predictions included in this snapshot ` |

### ` note:units · 输出长度单位：词数不是 tokenizer tokens `

```text
当前导入实验的 avg_output_tokens 实际来自 len(prediction.split())，是按空格切分的词数 whitespace words。项目将其标准化为 mean_length 和 length_unit=whitespace_words，不能据此计算模型 tokenizer token 节省。
```

| 资料 ID | 来源文本（仅标签，未打开或认证） |
| --- | --- |
| ` note:units ` | ` scripts/10_eval_stage2_model.py; see provenance.json ` |

### ` note:validation · 验证集与检查点选择 validation checkpoint `

```text
这些模型摘要来自同一 462 题验证集，该集合参与检查点选择。最优 DPO 指的是导入的 Stage 5 候选中验证集准确率最高的检查点，不是独立测试集上的泛化结论。
```

| 资料 ID | 来源文本（仅标签，未打开或认证） |
| --- | --- |
| ` note:validation ` | ` DPO Stage 5 comparison selection protocol ` |

### ` 比较来源 ID `

```json
[
  "run:dpo-best",
  "run:sft",
  "note:latency",
  "note:significance",
  "note:units",
  "note:validation"
]
```

## ` 步骤 limits · describe `

```text
请求原文：说明验证集和显著性边界
```

### ` 资料范围 `

```text
Only imported summaries and notes; no training, deletion, uploads, internet access or file writes
```

| 已有字段 | 缺失字段 |
| --- | --- |
| ` accuracy, correct, dataset, id, label, length_unit, mean_length, model_config_details, num_samples, project, source_path, source_sha256, split_id, synthetic, tokenizer, used_for_checkpoint_selection ` | ` seed, gpu_model, gpu_count, training_cost, paired_predictions, latency, peak_vram ` |

### ` note:latency · 时延 显存 吞吐 latency memory throughput `

```text
导入摘要没有同环境下的推理时延、吞吐或峰值显存测量。输出词数减少不能直接证明推理更快或显存更低，需要固定硬件、批量与生成设置重新测量。
```

| 资料 ID | 来源文本（仅标签，未打开或认证） |
| --- | --- |
| ` note:latency ` | ` Imported summary field coverage ` |

### ` note:scope · 工具能力与数据范围 scope `

```text
工具只能读取打包的摘要和说明，不能启动训练、修改文件或访问互联网。当前数据是一个 DPO 项目的小型快照，不能推广为跨数据集、跨任务或生产效果。
```

| 资料 ID | 来源文本（仅标签，未打开或认证） |
| --- | --- |
| ` note:scope ` | ` LabLens v0.1 tool contract ` |

### ` note:selection · 多目标选择 accuracy length Pareto `

```text
模型选择应同时检查准确率和输出长度。最高准确率不一定具有最短输出。阈值与选择标准应在评估前确定，且最终泛化表现需保留独立测试集。
```

| 资料 ID | 来源文本（仅标签，未打开或认证） |
| --- | --- |
| ` note:selection ` | ` Analysis policy ` |

### ` note:significance · 统计显著性 significance `

```text
准确率的点估计差异不等于统计显著。仅凭摘要中的正确题数无法恢复两个模型在每一题上的配对结果，因此当前工具不计算配对显著性检验或宣称显著提升。
```

| 资料 ID | 来源文本（仅标签，未打开或认证） |
| --- | --- |
| ` note:significance ` | ` Analysis policy; no paired predictions included in this snapshot ` |

### ` note:units · 输出长度单位：词数不是 tokenizer tokens `

```text
当前导入实验的 avg_output_tokens 实际来自 len(prediction.split())，是按空格切分的词数 whitespace words。项目将其标准化为 mean_length 和 length_unit=whitespace_words，不能据此计算模型 tokenizer token 节省。
```

| 资料 ID | 来源文本（仅标签，未打开或认证） |
| --- | --- |
| ` note:units ` | ` scripts/10_eval_stage2_model.py; see provenance.json ` |

### ` note:validation · 验证集与检查点选择 validation checkpoint `

```text
这些模型摘要来自同一 462 题验证集，该集合参与检查点选择。最优 DPO 指的是导入的 Stage 5 候选中验证集准确率最高的检查点，不是独立测试集上的泛化结论。
```

| 资料 ID | 来源文本（仅标签，未打开或认证） |
| --- | --- |
| ` note:validation ` | ` DPO Stage 5 comparison selection protocol ` |

### ` 资料来源 ID `

```json
[
  "snapshot:coverage",
  "note:latency",
  "note:scope",
  "note:selection",
  "note:significance",
  "note:units",
  "note:validation"
]
```

## ` 按 v0.11.0 规则重新核对 `

以下结果在本次导出时重新计算，不是执行当时的检查记录。只检查已支持的明确句式和原文映射；未识别表达仍须人工核对，不能据此证明自然语言意图正确或需求完整。

### ` 未处理要求 `

| 原文 | 原因 |
| --- | --- |


计划没有声明未处理项；这不证明全部要求已覆盖。

### ` 原文逐句映射 `

| 原文 | 映射状态 | 步骤 | 未处理项序号（从 0 开始） | 仅部分引用的步骤 |
| --- | --- | --- | --- | --- |
| ` 列出准确率最高的全部并列记录 ` | ` mapped ` | ` best ` | `  ` | `  ` |
| ` 以run:sft为基线比较run:dpo-best的正确题数差 ` | ` mapped ` | ` delta ` | `  ` | `  ` |
| ` 说明验证集和显著性边界 ` | ` mapped ` | ` limits ` | `  ` | `  ` |

### ` 查询规则 `

| 当前状态 | 存在冲突 | 额外 / 未关联步骤 |
| --- | --- | --- |
| ` checked ` | ` 否 ` | `  ` |

| 要求原文 | 状态 | 关联步骤 | 差异 / 冲突 |
| --- | --- | --- | --- |
| ` 列出准确率最高的全部并列记录 ` | ` matched ` | ` best ` | ` 无 ` |

### ` 预期与实际参数 `

```json
{
  "expected": {
    "min_accuracy": 0,
    "max_accuracy": 1,
    "min_correct": 0,
    "max_length": null,
    "shorter_than_baseline": null,
    "more_accurate_than_baseline": null,
    "order_by": "accuracy",
    "descending": true,
    "top_k": 1,
    "include_ties": true,
    "pareto_only": false
  },
  "actual": [
    {
      "step_id": "best",
      "query": {
        "min_accuracy": 0.0,
        "max_accuracy": 1.0,
        "min_correct": 0,
        "max_length": null,
        "shorter_than_baseline": null,
        "more_accurate_than_baseline": null,
        "order_by": "accuracy",
        "descending": true,
        "top_k": 1,
        "include_ties": true,
        "pareto_only": false
      }
    }
  ]
}
```

| 未按本组规则核对的原文 | 原因 |
| --- | --- |
| ` 以run:sft为基线比较run:dpo-best的正确题数差 ` | ` 未完整匹配支持句式；该句需人工核对 ` |
| ` 说明验证集和显著性边界 ` | ` 未完整匹配支持句式；该句需人工核对 ` |

```text
Limited explicit Chinese query rules only; unrecognized clauses and general semantics are not validated
```

### ` 比较与资料规则 `

| 当前状态 | 存在冲突 | 额外 / 未关联步骤 |
| --- | --- | --- |
| ` conflict ` | ` 是 ` | `  ` |

| 要求原文 | 状态 | 关联步骤 | 差异 / 冲突 |
| --- | --- | --- | --- |
| ` 以run:sft为基线比较run:dpo-best的正确题数差 ` | ` conflict ` | ` delta ` | ` delta 的 baseline 应为显式记录 'run:sft'，实际为 {'run_id': 'run:dpo-best', 'query_step': None}\ndelta 的 candidate 应为显式记录 'run:dpo-best'，实际为 {'run_id': 'run:sft', 'query_step': None} ` |

### ` 预期与实际参数 `

```json
{
  "expected": {
    "operation": "compare",
    "baseline": "run:sft",
    "candidate": "run:dpo-best"
  },
  "actual": [
    {
      "step_id": "delta",
      "kind": "compare",
      "baseline": {
        "run_id": "run:dpo-best",
        "query_step": null
      },
      "candidate": {
        "run_id": "run:sft",
        "query_step": null
      }
    }
  ]
}
```

| 要求原文 | 状态 | 关联步骤 | 差异 / 冲突 |
| --- | --- | --- | --- |
| ` 说明验证集和显著性边界 ` | ` matched ` | ` limits ` | ` 无 ` |

### ` 预期与实际参数 `

```json
{
  "expected": {
    "operation": "describe"
  },
  "actual": [
    {
      "step_id": "limits",
      "kind": "describe"
    }
  ]
}
```

| 未按本组规则核对的原文 | 原因 |
| --- | --- |
| ` 列出准确率最高的全部并列记录 ` | ` 未完整匹配支持的比较或说明句式；该句需人工核对 ` |

```text
Limited explicit Chinese named-pair comparison and evidence-description rules only; unrecognized clauses and general semantics are not validated
```

### ` 本次完整规则复核记录 `

```json
{
  "coverage": {
    "scope": "Exact full-clause mapping only; does not establish semantic coverage",
    "clauses": [
      {
        "id": "c1",
        "text": "列出准确率最高的全部并列记录",
        "start": 0,
        "end": 14,
        "state": "mapped",
        "step_ids": [
          "best"
        ],
        "unhandled_indexes": [],
        "partial_quote_step_ids": []
      },
      {
        "id": "c2",
        "text": "以run:sft为基线比较run:dpo-best的正确题数差",
        "start": 15,
        "end": 46,
        "state": "mapped",
        "step_ids": [
          "delta"
        ],
        "unhandled_indexes": [],
        "partial_quote_step_ids": []
      },
      {
        "id": "c3",
        "text": "说明验证集和显著性边界",
        "start": 47,
        "end": 58,
        "state": "mapped",
        "step_ids": [
          "limits"
        ],
        "unhandled_indexes": [],
        "partial_quote_step_ids": []
      }
    ],
    "all_clauses_accounted_for": true,
    "semantic_validation": "not_performed"
  },
  "request_checks": {
    "scope": "Limited explicit Chinese query rules only; unrecognized clauses and general semantics are not validated",
    "units": [
      {
        "clause_id": "c1",
        "text": "列出准确率最高的全部并列记录",
        "start": 0,
        "end": 14,
        "expected": {
          "min_accuracy": 0,
          "max_accuracy": 1,
          "min_correct": 0,
          "max_length": null,
          "shorter_than_baseline": null,
          "more_accurate_than_baseline": null,
          "order_by": "accuracy",
          "descending": true,
          "top_k": 1,
          "include_ties": true,
          "pareto_only": false
        },
        "checked_fields": [
          "min_accuracy",
          "max_accuracy",
          "min_correct",
          "max_length",
          "shorter_than_baseline",
          "more_accurate_than_baseline",
          "pareto_only",
          "top_k",
          "order_by",
          "descending",
          "include_ties"
        ],
        "id": "r1",
        "step_ids": [
          "best"
        ],
        "actual": [
          {
            "step_id": "best",
            "query": {
              "min_accuracy": 0.0,
              "max_accuracy": 1.0,
              "min_correct": 0,
              "max_length": null,
              "shorter_than_baseline": null,
              "more_accurate_than_baseline": null,
              "order_by": "accuracy",
              "descending": true,
              "top_k": 1,
              "include_ties": true,
              "pareto_only": false
            }
          }
        ],
        "differences": [],
        "state": "matched"
      }
    ],
    "unassessed": [
      {
        "clause_id": "c2",
        "text": "以run:sft为基线比较run:dpo-best的正确题数差",
        "reason": "未完整匹配支持句式；该句需人工核对"
      },
      {
        "clause_id": "c3",
        "text": "说明验证集和显著性边界",
        "reason": "未完整匹配支持句式；该句需人工核对"
      }
    ],
    "unexpected_step_ids": [],
    "has_conflicts": false,
    "status": "checked",
    "semantic_validation": "not_performed"
  },
  "task_checks": {
    "scope": "Limited explicit Chinese named-pair comparison and evidence-description rules only; unrecognized clauses and general semantics are not validated",
    "units": [
      {
        "text": "以run:sft为基线比较run:dpo-best的正确题数差",
        "start": 15,
        "end": 46,
        "kind": "compare",
        "expected": {
          "operation": "compare",
          "baseline": "run:sft",
          "candidate": "run:dpo-best"
        },
        "clause_id": "c2",
        "id": "t1",
        "step_ids": [
          "delta"
        ],
        "actual": [
          {
            "step_id": "delta",
            "kind": "compare",
            "baseline": {
              "run_id": "run:dpo-best",
              "query_step": null
            },
            "candidate": {
              "run_id": "run:sft",
              "query_step": null
            }
          }
        ],
        "unhandled_indexes": [],
        "differences": [
          "delta 的 baseline 应为显式记录 'run:sft'，实际为 {'run_id': 'run:dpo-best', 'query_step': None}",
          "delta 的 candidate 应为显式记录 'run:dpo-best'，实际为 {'run_id': 'run:sft', 'query_step': None}"
        ],
        "state": "conflict"
      },
      {
        "text": "说明验证集和显著性边界",
        "start": 47,
        "end": 58,
        "kind": "describe",
        "expected": {
          "operation": "describe"
        },
        "clause_id": "c3",
        "id": "t2",
        "step_ids": [
          "limits"
        ],
        "actual": [
          {
            "step_id": "limits",
            "kind": "describe"
          }
        ],
        "unhandled_indexes": [],
        "differences": [],
        "state": "matched"
      }
    ],
    "unassessed": [
      {
        "clause_id": "c1",
        "text": "列出准确率最高的全部并列记录",
        "reason": "未完整匹配支持的比较或说明句式；该句需人工核对"
      }
    ],
    "unexpected_step_ids": [],
    "has_conflicts": true,
    "status": "conflict",
    "semantic_validation": "not_performed"
  }
}
```

## ` 封装快照的来源与配置 `

此附录列出整个封装快照，不表示每条记录都被查询选中或参与比较。synthetic 为“是”的记录属于合成数据。资料原文不等于经过认证的事实。

### ` run:dpo-02 · stage5_lora_dpo_sft_lr5e-7_beta003_steps100/checkpoint-75 `

| 字段 | 快照中的值 |
| --- | --- |
| ` project ` | ` dpo-cot-compression ` |
| ` dataset ` | ` openai/gsm8k ` |
| ` split_id ` | ` gsm8k_preference_validation_462 ` |
| ` length_unit ` | ` whitespace_words ` |
| ` tokenizer ` | ` 未记录 ` |
| ` used_for_checkpoint_selection ` | ` 是 ` |
| ` synthetic ` | ` 否 ` |
| ` source_path ` | ` outputs/results/stage5_lora_dpo/stage5_lora_dpo_sft_lr5e-7_beta003_steps100/checkpoint_evals/checkpoint-75_eval.summary.json ` |
| ` source_sha256 ` | ` 2e9bd45da59e04c0a98f448aedf7b9fea7a5cc15c735fe78c7f1e5dc940f15a5 ` |

### ` 记录的模型配置 `

```json
{
  "base_model": "Qwen/Qwen3-8B",
  "checkpoint_label": "stage5_lora_dpo_sft_lr5e-7_beta003_steps100/checkpoint-75"
}
```

### ` run:dpo-03 · stage5_lora_dpo_sft_lr1e-6_beta003_steps50/checkpoint-25 `

| 字段 | 快照中的值 |
| --- | --- |
| ` project ` | ` dpo-cot-compression ` |
| ` dataset ` | ` openai/gsm8k ` |
| ` split_id ` | ` gsm8k_preference_validation_462 ` |
| ` length_unit ` | ` whitespace_words ` |
| ` tokenizer ` | ` 未记录 ` |
| ` used_for_checkpoint_selection ` | ` 是 ` |
| ` synthetic ` | ` 否 ` |
| ` source_path ` | ` outputs/results/stage5_lora_dpo/stage5_lora_dpo_sft_lr1e-6_beta003_steps50/checkpoint_evals/checkpoint-25_eval.summary.json ` |
| ` source_sha256 ` | ` 3ff6627daa2ee02dfa38c975a60333e0c1ec6b6d949762af3e483503c8391fe8 ` |

### ` 记录的模型配置 `

```json
{
  "base_model": "Qwen/Qwen3-8B",
  "checkpoint_label": "stage5_lora_dpo_sft_lr1e-6_beta003_steps50/checkpoint-25"
}
```

### ` run:dpo-04 · stage5_lora_dpo_sft_lr5e-7_beta003_steps100/checkpoint-100 `

| 字段 | 快照中的值 |
| --- | --- |
| ` project ` | ` dpo-cot-compression ` |
| ` dataset ` | ` openai/gsm8k ` |
| ` split_id ` | ` gsm8k_preference_validation_462 ` |
| ` length_unit ` | ` whitespace_words ` |
| ` tokenizer ` | ` 未记录 ` |
| ` used_for_checkpoint_selection ` | ` 是 ` |
| ` synthetic ` | ` 否 ` |
| ` source_path ` | ` outputs/results/stage5_lora_dpo/stage5_lora_dpo_sft_lr5e-7_beta003_steps100/checkpoint_evals/checkpoint-100_eval.summary.json ` |
| ` source_sha256 ` | ` 8009e8fcc842893698b42e425a8c7a28ea1b23880a0033755e28a2786f9fd2ed ` |

### ` 记录的模型配置 `

```json
{
  "base_model": "Qwen/Qwen3-8B",
  "checkpoint_label": "stage5_lora_dpo_sft_lr5e-7_beta003_steps100/checkpoint-100"
}
```

### ` run:dpo-06 · stage5_lora_dpo_sft_lr5e-7_beta005_steps100/checkpoint-100 `

| 字段 | 快照中的值 |
| --- | --- |
| ` project ` | ` dpo-cot-compression ` |
| ` dataset ` | ` openai/gsm8k ` |
| ` split_id ` | ` gsm8k_preference_validation_462 ` |
| ` length_unit ` | ` whitespace_words ` |
| ` tokenizer ` | ` 未记录 ` |
| ` used_for_checkpoint_selection ` | ` 是 ` |
| ` synthetic ` | ` 否 ` |
| ` source_path ` | ` outputs/results/stage5_lora_dpo/stage5_lora_dpo_sft_lr5e-7_beta005_steps100/checkpoint_evals/checkpoint-100_eval.summary.json ` |
| ` source_sha256 ` | ` 77223a73ca11cec3a3a3e127fb06a5dc16c1f0f3757c782ea57e011fb8243e34 ` |

### ` 记录的模型配置 `

```json
{
  "base_model": "Qwen/Qwen3-8B",
  "checkpoint_label": "stage5_lora_dpo_sft_lr5e-7_beta005_steps100/checkpoint-100"
}
```

### ` run:dpo-08 · stage5_lora_dpo_sft_lr5e-7_beta003_steps100/checkpoint-50 `

| 字段 | 快照中的值 |
| --- | --- |
| ` project ` | ` dpo-cot-compression ` |
| ` dataset ` | ` openai/gsm8k ` |
| ` split_id ` | ` gsm8k_preference_validation_462 ` |
| ` length_unit ` | ` whitespace_words ` |
| ` tokenizer ` | ` 未记录 ` |
| ` used_for_checkpoint_selection ` | ` 是 ` |
| ` synthetic ` | ` 否 ` |
| ` source_path ` | ` outputs/results/stage5_lora_dpo/stage5_lora_dpo_sft_lr5e-7_beta003_steps100/checkpoint_evals/checkpoint-50_eval.summary.json ` |
| ` source_sha256 ` | ` bfff3de9974a9c72cd6349a03a4c56b1b614123fc417372bcacf7036120668df ` |

### ` 记录的模型配置 `

```json
{
  "base_model": "Qwen/Qwen3-8B",
  "checkpoint_label": "stage5_lora_dpo_sft_lr5e-7_beta003_steps100/checkpoint-50"
}
```

### ` run:dpo-09 · stage5_lora_dpo_sft_lr5e-7_beta005_steps100/checkpoint-50 `

| 字段 | 快照中的值 |
| --- | --- |
| ` project ` | ` dpo-cot-compression ` |
| ` dataset ` | ` openai/gsm8k ` |
| ` split_id ` | ` gsm8k_preference_validation_462 ` |
| ` length_unit ` | ` whitespace_words ` |
| ` tokenizer ` | ` 未记录 ` |
| ` used_for_checkpoint_selection ` | ` 是 ` |
| ` synthetic ` | ` 否 ` |
| ` source_path ` | ` outputs/results/stage5_lora_dpo/stage5_lora_dpo_sft_lr5e-7_beta005_steps100/checkpoint_evals/checkpoint-50_eval.summary.json ` |
| ` source_sha256 ` | ` 2aa0a9933e25b349217b6553fb8a0f91117b50f9cd460655a58a8c0e6e6fcbce ` |

### ` 记录的模型配置 `

```json
{
  "base_model": "Qwen/Qwen3-8B",
  "checkpoint_label": "stage5_lora_dpo_sft_lr5e-7_beta005_steps100/checkpoint-50"
}
```

### ` run:dpo-10 · stage5_lora_dpo_sft_lr5e-7_beta005_steps100/checkpoint-75 `

| 字段 | 快照中的值 |
| --- | --- |
| ` project ` | ` dpo-cot-compression ` |
| ` dataset ` | ` openai/gsm8k ` |
| ` split_id ` | ` gsm8k_preference_validation_462 ` |
| ` length_unit ` | ` whitespace_words ` |
| ` tokenizer ` | ` 未记录 ` |
| ` used_for_checkpoint_selection ` | ` 是 ` |
| ` synthetic ` | ` 否 ` |
| ` source_path ` | ` outputs/results/stage5_lora_dpo/stage5_lora_dpo_sft_lr5e-7_beta005_steps100/checkpoint_evals/checkpoint-75_eval.summary.json ` |
| ` source_sha256 ` | ` ed3e2471145cd521c4978b0add3d90d92dfaea6d0ddf3e1294b038ba208c21ab ` |

### ` 记录的模型配置 `

```json
{
  "base_model": "Qwen/Qwen3-8B",
  "checkpoint_label": "stage5_lora_dpo_sft_lr5e-7_beta005_steps100/checkpoint-75"
}
```

### ` run:dpo-11 · stage5_lora_dpo_sft_lr5e-7_beta005_steps100/checkpoint-25 `

| 字段 | 快照中的值 |
| --- | --- |
| ` project ` | ` dpo-cot-compression ` |
| ` dataset ` | ` openai/gsm8k ` |
| ` split_id ` | ` gsm8k_preference_validation_462 ` |
| ` length_unit ` | ` whitespace_words ` |
| ` tokenizer ` | ` 未记录 ` |
| ` used_for_checkpoint_selection ` | ` 是 ` |
| ` synthetic ` | ` 否 ` |
| ` source_path ` | ` outputs/results/stage5_lora_dpo/stage5_lora_dpo_sft_lr5e-7_beta005_steps100/checkpoint_evals/checkpoint-25_eval.summary.json ` |
| ` source_sha256 ` | ` 1f5223f448978586e770f20a73612da6498415a7b0c56978ceb781f8785f0c3a ` |

### ` 记录的模型配置 `

```json
{
  "base_model": "Qwen/Qwen3-8B",
  "checkpoint_label": "stage5_lora_dpo_sft_lr5e-7_beta005_steps100/checkpoint-25"
}
```

### ` run:dpo-12 · stage5_lora_dpo_sft_lr5e-7_beta003_steps100/checkpoint-25 `

| 字段 | 快照中的值 |
| --- | --- |
| ` project ` | ` dpo-cot-compression ` |
| ` dataset ` | ` openai/gsm8k ` |
| ` split_id ` | ` gsm8k_preference_validation_462 ` |
| ` length_unit ` | ` whitespace_words ` |
| ` tokenizer ` | ` 未记录 ` |
| ` used_for_checkpoint_selection ` | ` 是 ` |
| ` synthetic ` | ` 否 ` |
| ` source_path ` | ` outputs/results/stage5_lora_dpo/stage5_lora_dpo_sft_lr5e-7_beta003_steps100/checkpoint_evals/checkpoint-25_eval.summary.json ` |
| ` source_sha256 ` | ` 1ad67ac1f151570f5c40254f861e8b3507eb3a849853fe4f99ffb0b9193d4a53 ` |

### ` 记录的模型配置 `

```json
{
  "base_model": "Qwen/Qwen3-8B",
  "checkpoint_label": "stage5_lora_dpo_sft_lr5e-7_beta003_steps100/checkpoint-25"
}
```

### ` run:dpo-best · stage5_lora_dpo_sft_lr1e-6_beta003_steps50/checkpoint-50 `

| 字段 | 快照中的值 |
| --- | --- |
| ` project ` | ` dpo-cot-compression ` |
| ` dataset ` | ` openai/gsm8k ` |
| ` split_id ` | ` gsm8k_preference_validation_462 ` |
| ` length_unit ` | ` whitespace_words ` |
| ` tokenizer ` | ` 未记录 ` |
| ` used_for_checkpoint_selection ` | ` 是 ` |
| ` synthetic ` | ` 否 ` |
| ` source_path ` | ` outputs/results/stage5_lora_dpo/stage5_lora_dpo_sft_lr1e-6_beta003_steps50/checkpoint_evals/checkpoint-50_eval.summary.json ` |
| ` source_sha256 ` | ` 1478a70f44ff720b3d9bb455bb33bff7771242aeb8c1543d67fe21192a49cf2b ` |

### ` 记录的模型配置 `

```json
{
  "base_model": "Qwen/Qwen3-8B",
  "checkpoint_label": "stage5_lora_dpo_sft_lr1e-6_beta003_steps50/checkpoint-50"
}
```

### ` run:sft · LoRA-SFT `

| 字段 | 快照中的值 |
| --- | --- |
| ` project ` | ` dpo-cot-compression ` |
| ` dataset ` | ` openai/gsm8k ` |
| ` split_id ` | ` gsm8k_preference_validation_462 ` |
| ` length_unit ` | ` whitespace_words ` |
| ` tokenizer ` | ` 未记录 ` |
| ` used_for_checkpoint_selection ` | ` 是 ` |
| ` synthetic ` | ` 否 ` |
| ` source_path ` | ` outputs/results/stage3_lora_sft/val_eval_full_b32.summary.json ` |
| ` source_sha256 ` | ` 44f222386666e80518d3cd4e2a3f5e98e4cca6d7e35d12b19ba33dc9d2965388 ` |

### ` 记录的模型配置 `

```json
{
  "base_model": "Qwen/Qwen3-8B",
  "checkpoint_label": "LoRA-SFT"
}
```

## ` 封装快照的全部资料原文 `

### ` note:latency · 时延 显存 吞吐 latency memory throughput `

```text
导入摘要没有同环境下的推理时延、吞吐或峰值显存测量。输出词数减少不能直接证明推理更快或显存更低，需要固定硬件、批量与生成设置重新测量。
```

| 资料 ID | 来源文本（仅标签，未打开或认证） |
| --- | --- |
| ` note:latency ` | ` Imported summary field coverage ` |

### ` note:scope · 工具能力与数据范围 scope `

```text
工具只能读取打包的摘要和说明，不能启动训练、修改文件或访问互联网。当前数据是一个 DPO 项目的小型快照，不能推广为跨数据集、跨任务或生产效果。
```

| 资料 ID | 来源文本（仅标签，未打开或认证） |
| --- | --- |
| ` note:scope ` | ` LabLens v0.1 tool contract ` |

### ` note:selection · 多目标选择 accuracy length Pareto `

```text
模型选择应同时检查准确率和输出长度。最高准确率不一定具有最短输出。阈值与选择标准应在评估前确定，且最终泛化表现需保留独立测试集。
```

| 资料 ID | 来源文本（仅标签，未打开或认证） |
| --- | --- |
| ` note:selection ` | ` Analysis policy ` |

### ` note:significance · 统计显著性 significance `

```text
准确率的点估计差异不等于统计显著。仅凭摘要中的正确题数无法恢复两个模型在每一题上的配对结果，因此当前工具不计算配对显著性检验或宣称显著提升。
```

| 资料 ID | 来源文本（仅标签，未打开或认证） |
| --- | --- |
| ` note:significance ` | ` Analysis policy; no paired predictions included in this snapshot ` |

### ` note:units · 输出长度单位：词数不是 tokenizer tokens `

```text
当前导入实验的 avg_output_tokens 实际来自 len(prediction.split())，是按空格切分的词数 whitespace words。项目将其标准化为 mean_length 和 length_unit=whitespace_words，不能据此计算模型 tokenizer token 节省。
```

| 资料 ID | 来源文本（仅标签，未打开或认证） |
| --- | --- |
| ` note:units ` | ` scripts/10_eval_stage2_model.py; see provenance.json ` |

### ` note:validation · 验证集与检查点选择 validation checkpoint `

```text
这些模型摘要来自同一 462 题验证集，该集合参与检查点选择。最优 DPO 指的是导入的 Stage 5 候选中验证集准确率最高的检查点，不是独立测试集上的泛化结论。
```

| 资料 ID | 来源文本（仅标签，未打开或认证） |
| --- | --- |
| ` note:validation ` | ` DPO Stage 5 comparison selection protocol ` |

## ` 原始收据与离线复核 `

复制以下完整 JSON 并保存为 receipt.json，可使用 lablens verify receipt.json 离线重算。内容哈希不是数字签名；同时修改数据和哈希可以形成新的自洽收据，因此重算通过不证明真实性。

### ` 完整原始收据 JSON（可复制） `

```json
{
  "format": "lablens-plan-receipt/v1",
  "question": "列出准确率最高的全部并列记录；以run:sft为基线比较run:dpo-best的正确题数差；说明验证集和显著性边界。",
  "snapshot_id": "snapshot:76521482a203764b032327edf1e05cb854f5b6179ea4860299883e39aaeb9f3b",
  "plan": {
    "steps": [
      {
        "id": "best",
        "request_excerpt": "列出准确率最高的全部并列记录",
        "kind": "query",
        "query": {
          "min_accuracy": 0,
          "max_accuracy": 1,
          "min_correct": 0,
          "max_length": null,
          "shorter_than_baseline": null,
          "more_accurate_than_baseline": null,
          "order_by": "accuracy",
          "descending": true,
          "top_k": 1,
          "include_ties": true,
          "pareto_only": false
        }
      },
      {
        "id": "delta",
        "request_excerpt": "以run:sft为基线比较run:dpo-best的正确题数差",
        "kind": "compare",
        "baseline": {
          "run_id": "run:dpo-best",
          "query_step": null
        },
        "candidate": {
          "run_id": "run:sft",
          "query_step": null
        }
      },
      {
        "id": "limits",
        "request_excerpt": "说明验证集和显著性边界",
        "kind": "describe"
      }
    ],
    "unhandled": []
  },
  "dataset": {
    "runs": [
      {
        "id": "run:dpo-02",
        "label": "stage5_lora_dpo_sft_lr5e-7_beta003_steps100/checkpoint-75",
        "project": "dpo-cot-compression",
        "dataset": "openai/gsm8k",
        "split_id": "gsm8k_preference_validation_462",
        "num_samples": 462,
        "correct": 441,
        "accuracy": 0.9545454545454546,
        "mean_length": 31.350649350649352,
        "length_unit": "whitespace_words",
        "tokenizer": null,
        "model_config_details": {
          "base_model": "Qwen/Qwen3-8B",
          "checkpoint_label": "stage5_lora_dpo_sft_lr5e-7_beta003_steps100/checkpoint-75"
        },
        "used_for_checkpoint_selection": true,
        "source_path": "outputs/results/stage5_lora_dpo/stage5_lora_dpo_sft_lr5e-7_beta003_steps100/checkpoint_evals/checkpoint-75_eval.summary.json",
        "source_sha256": "2e9bd45da59e04c0a98f448aedf7b9fea7a5cc15c735fe78c7f1e5dc940f15a5",
        "synthetic": false
      },
      {
        "id": "run:dpo-03",
        "label": "stage5_lora_dpo_sft_lr1e-6_beta003_steps50/checkpoint-25",
        "project": "dpo-cot-compression",
        "dataset": "openai/gsm8k",
        "split_id": "gsm8k_preference_validation_462",
        "num_samples": 462,
        "correct": 440,
        "accuracy": 0.9523809523809523,
        "mean_length": 31.8982683982684,
        "length_unit": "whitespace_words",
        "tokenizer": null,
        "model_config_details": {
          "base_model": "Qwen/Qwen3-8B",
          "checkpoint_label": "stage5_lora_dpo_sft_lr1e-6_beta003_steps50/checkpoint-25"
        },
        "used_for_checkpoint_selection": true,
        "source_path": "outputs/results/stage5_lora_dpo/stage5_lora_dpo_sft_lr1e-6_beta003_steps50/checkpoint_evals/checkpoint-25_eval.summary.json",
        "source_sha256": "3ff6627daa2ee02dfa38c975a60333e0c1ec6b6d949762af3e483503c8391fe8",
        "synthetic": false
      },
      {
        "id": "run:dpo-04",
        "label": "stage5_lora_dpo_sft_lr5e-7_beta003_steps100/checkpoint-100",
        "project": "dpo-cot-compression",
        "dataset": "openai/gsm8k",
        "split_id": "gsm8k_preference_validation_462",
        "num_samples": 462,
        "correct": 439,
        "accuracy": 0.9502164502164502,
        "mean_length": 31.39177489177489,
        "length_unit": "whitespace_words",
        "tokenizer": null,
        "model_config_details": {
          "base_model": "Qwen/Qwen3-8B",
          "checkpoint_label": "stage5_lora_dpo_sft_lr5e-7_beta003_steps100/checkpoint-100"
        },
        "used_for_checkpoint_selection": true,
        "source_path": "outputs/results/stage5_lora_dpo/stage5_lora_dpo_sft_lr5e-7_beta003_steps100/checkpoint_evals/checkpoint-100_eval.summary.json",
        "source_sha256": "8009e8fcc842893698b42e425a8c7a28ea1b23880a0033755e28a2786f9fd2ed",
        "synthetic": false
      },
      {
        "id": "run:dpo-06",
        "label": "stage5_lora_dpo_sft_lr5e-7_beta005_steps100/checkpoint-100",
        "project": "dpo-cot-compression",
        "dataset": "openai/gsm8k",
        "split_id": "gsm8k_preference_validation_462",
        "num_samples": 462,
        "correct": 439,
        "accuracy": 0.9502164502164502,
        "mean_length": 31.504329004329005,
        "length_unit": "whitespace_words",
        "tokenizer": null,
        "model_config_details": {
          "base_model": "Qwen/Qwen3-8B",
          "checkpoint_label": "stage5_lora_dpo_sft_lr5e-7_beta005_steps100/checkpoint-100"
        },
        "used_for_checkpoint_selection": true,
        "source_path": "outputs/results/stage5_lora_dpo/stage5_lora_dpo_sft_lr5e-7_beta005_steps100/checkpoint_evals/checkpoint-100_eval.summary.json",
        "source_sha256": "77223a73ca11cec3a3a3e127fb06a5dc16c1f0f3757c782ea57e011fb8243e34",
        "synthetic": false
      },
      {
        "id": "run:dpo-08",
        "label": "stage5_lora_dpo_sft_lr5e-7_beta003_steps100/checkpoint-50",
        "project": "dpo-cot-compression",
        "dataset": "openai/gsm8k",
        "split_id": "gsm8k_preference_validation_462",
        "num_samples": 462,
        "correct": 439,
        "accuracy": 0.9502164502164502,
        "mean_length": 31.757575757575758,
        "length_unit": "whitespace_words",
        "tokenizer": null,
        "model_config_details": {
          "base_model": "Qwen/Qwen3-8B",
          "checkpoint_label": "stage5_lora_dpo_sft_lr5e-7_beta003_steps100/checkpoint-50"
        },
        "used_for_checkpoint_selection": true,
        "source_path": "outputs/results/stage5_lora_dpo/stage5_lora_dpo_sft_lr5e-7_beta003_steps100/checkpoint_evals/checkpoint-50_eval.summary.json",
        "source_sha256": "bfff3de9974a9c72cd6349a03a4c56b1b614123fc417372bcacf7036120668df",
        "synthetic": false
      },
      {
        "id": "run:dpo-09",
        "label": "stage5_lora_dpo_sft_lr5e-7_beta005_steps100/checkpoint-50",
        "project": "dpo-cot-compression",
        "dataset": "openai/gsm8k",
        "split_id": "gsm8k_preference_validation_462",
        "num_samples": 462,
        "correct": 438,
        "accuracy": 0.948051948051948,
        "mean_length": 31.748917748917748,
        "length_unit": "whitespace_words",
        "tokenizer": null,
        "model_config_details": {
          "base_model": "Qwen/Qwen3-8B",
          "checkpoint_label": "stage5_lora_dpo_sft_lr5e-7_beta005_steps100/checkpoint-50"
        },
        "used_for_checkpoint_selection": true,
        "source_path": "outputs/results/stage5_lora_dpo/stage5_lora_dpo_sft_lr5e-7_beta005_steps100/checkpoint_evals/checkpoint-50_eval.summary.json",
        "source_sha256": "2aa0a9933e25b349217b6553fb8a0f91117b50f9cd460655a58a8c0e6e6fcbce",
        "synthetic": false
      },
      {
        "id": "run:dpo-10",
        "label": "stage5_lora_dpo_sft_lr5e-7_beta005_steps100/checkpoint-75",
        "project": "dpo-cot-compression",
        "dataset": "openai/gsm8k",
        "split_id": "gsm8k_preference_validation_462",
        "num_samples": 462,
        "correct": 437,
        "accuracy": 0.9458874458874459,
        "mean_length": 31.35930735930736,
        "length_unit": "whitespace_words",
        "tokenizer": null,
        "model_config_details": {
          "base_model": "Qwen/Qwen3-8B",
          "checkpoint_label": "stage5_lora_dpo_sft_lr5e-7_beta005_steps100/checkpoint-75"
        },
        "used_for_checkpoint_selection": true,
        "source_path": "outputs/results/stage5_lora_dpo/stage5_lora_dpo_sft_lr5e-7_beta005_steps100/checkpoint_evals/checkpoint-75_eval.summary.json",
        "source_sha256": "ed3e2471145cd521c4978b0add3d90d92dfaea6d0ddf3e1294b038ba208c21ab",
        "synthetic": false
      },
      {
        "id": "run:dpo-11",
        "label": "stage5_lora_dpo_sft_lr5e-7_beta005_steps100/checkpoint-25",
        "project": "dpo-cot-compression",
        "dataset": "openai/gsm8k",
        "split_id": "gsm8k_preference_validation_462",
        "num_samples": 462,
        "correct": 437,
        "accuracy": 0.9458874458874459,
        "mean_length": 31.848484848484848,
        "length_unit": "whitespace_words",
        "tokenizer": null,
        "model_config_details": {
          "base_model": "Qwen/Qwen3-8B",
          "checkpoint_label": "stage5_lora_dpo_sft_lr5e-7_beta005_steps100/checkpoint-25"
        },
        "used_for_checkpoint_selection": true,
        "source_path": "outputs/results/stage5_lora_dpo/stage5_lora_dpo_sft_lr5e-7_beta005_steps100/checkpoint_evals/checkpoint-25_eval.summary.json",
        "source_sha256": "1f5223f448978586e770f20a73612da6498415a7b0c56978ceb781f8785f0c3a",
        "synthetic": false
      },
      {
        "id": "run:dpo-12",
        "label": "stage5_lora_dpo_sft_lr5e-7_beta003_steps100/checkpoint-25",
        "project": "dpo-cot-compression",
        "dataset": "openai/gsm8k",
        "split_id": "gsm8k_preference_validation_462",
        "num_samples": 462,
        "correct": 437,
        "accuracy": 0.9458874458874459,
        "mean_length": 31.854978354978353,
        "length_unit": "whitespace_words",
        "tokenizer": null,
        "model_config_details": {
          "base_model": "Qwen/Qwen3-8B",
          "checkpoint_label": "stage5_lora_dpo_sft_lr5e-7_beta003_steps100/checkpoint-25"
        },
        "used_for_checkpoint_selection": true,
        "source_path": "outputs/results/stage5_lora_dpo/stage5_lora_dpo_sft_lr5e-7_beta003_steps100/checkpoint_evals/checkpoint-25_eval.summary.json",
        "source_sha256": "1ad67ac1f151570f5c40254f861e8b3507eb3a849853fe4f99ffb0b9193d4a53",
        "synthetic": false
      },
      {
        "id": "run:dpo-best",
        "label": "stage5_lora_dpo_sft_lr1e-6_beta003_steps50/checkpoint-50",
        "project": "dpo-cot-compression",
        "dataset": "openai/gsm8k",
        "split_id": "gsm8k_preference_validation_462",
        "num_samples": 462,
        "correct": 442,
        "accuracy": 0.9567099567099567,
        "mean_length": 31.528138528138527,
        "length_unit": "whitespace_words",
        "tokenizer": null,
        "model_config_details": {
          "base_model": "Qwen/Qwen3-8B",
          "checkpoint_label": "stage5_lora_dpo_sft_lr1e-6_beta003_steps50/checkpoint-50"
        },
        "used_for_checkpoint_selection": true,
        "source_path": "outputs/results/stage5_lora_dpo/stage5_lora_dpo_sft_lr1e-6_beta003_steps50/checkpoint_evals/checkpoint-50_eval.summary.json",
        "source_sha256": "1478a70f44ff720b3d9bb455bb33bff7771242aeb8c1543d67fe21192a49cf2b",
        "synthetic": false
      },
      {
        "id": "run:sft",
        "label": "LoRA-SFT",
        "project": "dpo-cot-compression",
        "dataset": "openai/gsm8k",
        "split_id": "gsm8k_preference_validation_462",
        "num_samples": 462,
        "correct": 439,
        "accuracy": 0.9502164502164502,
        "mean_length": 32.422077922077925,
        "length_unit": "whitespace_words",
        "tokenizer": null,
        "model_config_details": {
          "base_model": "Qwen/Qwen3-8B",
          "checkpoint_label": "LoRA-SFT"
        },
        "used_for_checkpoint_selection": true,
        "source_path": "outputs/results/stage3_lora_sft/val_eval_full_b32.summary.json",
        "source_sha256": "44f222386666e80518d3cd4e2a3f5e98e4cca6d7e35d12b19ba33dc9d2965388",
        "synthetic": false
      }
    ],
    "notes": [
      {
        "id": "note:latency",
        "title": "时延 显存 吞吐 latency memory throughput",
        "text": "导入摘要没有同环境下的推理时延、吞吐或峰值显存测量。输出词数减少不能直接证明推理更快或显存更低，需要固定硬件、批量与生成设置重新测量。",
        "source": "Imported summary field coverage"
      },
      {
        "id": "note:scope",
        "title": "工具能力与数据范围 scope",
        "text": "工具只能读取打包的摘要和说明，不能启动训练、修改文件或访问互联网。当前数据是一个 DPO 项目的小型快照，不能推广为跨数据集、跨任务或生产效果。",
        "source": "LabLens v0.1 tool contract"
      },
      {
        "id": "note:selection",
        "title": "多目标选择 accuracy length Pareto",
        "text": "模型选择应同时检查准确率和输出长度。最高准确率不一定具有最短输出。阈值与选择标准应在评估前确定，且最终泛化表现需保留独立测试集。",
        "source": "Analysis policy"
      },
      {
        "id": "note:significance",
        "title": "统计显著性 significance",
        "text": "准确率的点估计差异不等于统计显著。仅凭摘要中的正确题数无法恢复两个模型在每一题上的配对结果，因此当前工具不计算配对显著性检验或宣称显著提升。",
        "source": "Analysis policy; no paired predictions included in this snapshot"
      },
      {
        "id": "note:units",
        "title": "输出长度单位：词数不是 tokenizer tokens",
        "text": "当前导入实验的 avg_output_tokens 实际来自 len(prediction.split())，是按空格切分的词数 whitespace words。项目将其标准化为 mean_length 和 length_unit=whitespace_words，不能据此计算模型 tokenizer token 节省。",
        "source": "scripts/10_eval_stage2_model.py; see provenance.json"
      },
      {
        "id": "note:validation",
        "title": "验证集与检查点选择 validation checkpoint",
        "text": "这些模型摘要来自同一 462 题验证集，该集合参与检查点选择。最优 DPO 指的是导入的 Stage 5 候选中验证集准确率最高的检查点，不是独立测试集上的泛化结论。",
        "source": "DPO Stage 5 comparison selection protocol"
      }
    ]
  },
  "results": [
    {
      "id": "best",
      "kind": "query",
      "request_excerpt": "列出准确率最高的全部并列记录",
      "result": {
        "query_id": "query:7c3339471f6e3af99370fbd0778ae34f8513fb8c469be71241903608d2872e75",
        "snapshot_id": "snapshot:76521482a203764b032327edf1e05cb854f5b6179ea4860299883e39aaeb9f3b",
        "parameters": {
          "min_accuracy": 0,
          "max_accuracy": 1,
          "min_correct": 0,
          "max_length": null,
          "shorter_than_baseline": null,
          "more_accurate_than_baseline": null,
          "order_by": "accuracy",
          "descending": true,
          "top_k": 1,
          "include_ties": true,
          "pareto_only": false
        },
        "matched_before_top_k": 11,
        "ordered_run_ids": [
          "run:dpo-best"
        ],
        "runs": [
          {
            "id": "run:dpo-best",
            "label": "stage5_lora_dpo_sft_lr1e-6_beta003_steps50/checkpoint-50",
            "project": "dpo-cot-compression",
            "dataset": "openai/gsm8k",
            "split_id": "gsm8k_preference_validation_462",
            "num_samples": 462,
            "correct": 442,
            "accuracy": 0.9567099567099567,
            "mean_length": 31.528138528138527,
            "length_unit": "whitespace_words",
            "tokenizer": null,
            "model_config_details": {
              "base_model": "Qwen/Qwen3-8B",
              "checkpoint_label": "stage5_lora_dpo_sft_lr1e-6_beta003_steps50/checkpoint-50"
            },
            "used_for_checkpoint_selection": true,
            "source_path": "outputs/results/stage5_lora_dpo/stage5_lora_dpo_sft_lr1e-6_beta003_steps50/checkpoint_evals/checkpoint-50_eval.summary.json",
            "source_sha256": "1478a70f44ff720b3d9bb455bb33bff7771242aeb8c1543d67fe21192a49cf2b",
            "synthetic": false
          }
        ]
      }
    },
    {
      "id": "delta",
      "kind": "compare",
      "request_excerpt": "以run:sft为基线比较run:dpo-best的正确题数差",
      "result": {
        "baseline": {
          "id": "run:dpo-best",
          "label": "stage5_lora_dpo_sft_lr1e-6_beta003_steps50/checkpoint-50",
          "project": "dpo-cot-compression",
          "dataset": "openai/gsm8k",
          "split_id": "gsm8k_preference_validation_462",
          "num_samples": 462,
          "correct": 442,
          "accuracy": 0.9567099567099567,
          "mean_length": 31.528138528138527,
          "length_unit": "whitespace_words",
          "tokenizer": null,
          "model_config_details": {
            "base_model": "Qwen/Qwen3-8B",
            "checkpoint_label": "stage5_lora_dpo_sft_lr1e-6_beta003_steps50/checkpoint-50"
          },
          "used_for_checkpoint_selection": true,
          "source_path": "outputs/results/stage5_lora_dpo/stage5_lora_dpo_sft_lr1e-6_beta003_steps50/checkpoint_evals/checkpoint-50_eval.summary.json",
          "source_sha256": "1478a70f44ff720b3d9bb455bb33bff7771242aeb8c1543d67fe21192a49cf2b",
          "synthetic": false
        },
        "candidate": {
          "id": "run:sft",
          "label": "LoRA-SFT",
          "project": "dpo-cot-compression",
          "dataset": "openai/gsm8k",
          "split_id": "gsm8k_preference_validation_462",
          "num_samples": 462,
          "correct": 439,
          "accuracy": 0.9502164502164502,
          "mean_length": 32.422077922077925,
          "length_unit": "whitespace_words",
          "tokenizer": null,
          "model_config_details": {
            "base_model": "Qwen/Qwen3-8B",
            "checkpoint_label": "LoRA-SFT"
          },
          "used_for_checkpoint_selection": true,
          "source_path": "outputs/results/stage3_lora_sft/val_eval_full_b32.summary.json",
          "source_sha256": "44f222386666e80518d3cd4e2a3f5e98e4cca6d7e35d12b19ba33dc9d2965388",
          "synthetic": false
        },
        "accuracy_delta_percentage_points": -0.6493506493506551,
        "length_reduction_percent": -2.835370039818769,
        "additional_correct": -3,
        "limitations": [
          {
            "id": "note:latency",
            "title": "时延 显存 吞吐 latency memory throughput",
            "text": "导入摘要没有同环境下的推理时延、吞吐或峰值显存测量。输出词数减少不能直接证明推理更快或显存更低，需要固定硬件、批量与生成设置重新测量。",
            "source": "Imported summary field coverage"
          },
          {
            "id": "note:significance",
            "title": "统计显著性 significance",
            "text": "准确率的点估计差异不等于统计显著。仅凭摘要中的正确题数无法恢复两个模型在每一题上的配对结果，因此当前工具不计算配对显著性检验或宣称显著提升。",
            "source": "Analysis policy; no paired predictions included in this snapshot"
          },
          {
            "id": "note:units",
            "title": "输出长度单位：词数不是 tokenizer tokens",
            "text": "当前导入实验的 avg_output_tokens 实际来自 len(prediction.split())，是按空格切分的词数 whitespace words。项目将其标准化为 mean_length 和 length_unit=whitespace_words，不能据此计算模型 tokenizer token 节省。",
            "source": "scripts/10_eval_stage2_model.py; see provenance.json"
          },
          {
            "id": "note:validation",
            "title": "验证集与检查点选择 validation checkpoint",
            "text": "这些模型摘要来自同一 462 题验证集，该集合参与检查点选择。最优 DPO 指的是导入的 Stage 5 候选中验证集准确率最高的检查点，不是独立测试集上的泛化结论。",
            "source": "DPO Stage 5 comparison selection protocol"
          }
        ],
        "source_ids": [
          "run:dpo-best",
          "run:sft",
          "note:latency",
          "note:significance",
          "note:units",
          "note:validation"
        ]
      }
    },
    {
      "id": "limits",
      "kind": "describe",
      "request_excerpt": "说明验证集和显著性边界",
      "result": {
        "fields": [
          "accuracy",
          "correct",
          "dataset",
          "id",
          "label",
          "length_unit",
          "mean_length",
          "model_config_details",
          "num_samples",
          "project",
          "source_path",
          "source_sha256",
          "split_id",
          "synthetic",
          "tokenizer",
          "used_for_checkpoint_selection"
        ],
        "missing_fields": [
          "seed",
          "gpu_model",
          "gpu_count",
          "training_cost",
          "paired_predictions",
          "latency",
          "peak_vram"
        ],
        "scope": "Only imported summaries and notes; no training, deletion, uploads, internet access or file writes",
        "notes": [
          {
            "id": "note:latency",
            "title": "时延 显存 吞吐 latency memory throughput",
            "text": "导入摘要没有同环境下的推理时延、吞吐或峰值显存测量。输出词数减少不能直接证明推理更快或显存更低，需要固定硬件、批量与生成设置重新测量。",
            "source": "Imported summary field coverage"
          },
          {
            "id": "note:scope",
            "title": "工具能力与数据范围 scope",
            "text": "工具只能读取打包的摘要和说明，不能启动训练、修改文件或访问互联网。当前数据是一个 DPO 项目的小型快照，不能推广为跨数据集、跨任务或生产效果。",
            "source": "LabLens v0.1 tool contract"
          },
          {
            "id": "note:selection",
            "title": "多目标选择 accuracy length Pareto",
            "text": "模型选择应同时检查准确率和输出长度。最高准确率不一定具有最短输出。阈值与选择标准应在评估前确定，且最终泛化表现需保留独立测试集。",
            "source": "Analysis policy"
          },
          {
            "id": "note:significance",
            "title": "统计显著性 significance",
            "text": "准确率的点估计差异不等于统计显著。仅凭摘要中的正确题数无法恢复两个模型在每一题上的配对结果，因此当前工具不计算配对显著性检验或宣称显著提升。",
            "source": "Analysis policy; no paired predictions included in this snapshot"
          },
          {
            "id": "note:units",
            "title": "输出长度单位：词数不是 tokenizer tokens",
            "text": "当前导入实验的 avg_output_tokens 实际来自 len(prediction.split())，是按空格切分的词数 whitespace words。项目将其标准化为 mean_length 和 length_unit=whitespace_words，不能据此计算模型 tokenizer token 节省。",
            "source": "scripts/10_eval_stage2_model.py; see provenance.json"
          },
          {
            "id": "note:validation",
            "title": "验证集与检查点选择 validation checkpoint",
            "text": "这些模型摘要来自同一 462 题验证集，该集合参与检查点选择。最优 DPO 指的是导入的 Stage 5 候选中验证集准确率最高的检查点，不是独立测试集上的泛化结论。",
            "source": "DPO Stage 5 comparison selection protocol"
          }
        ],
        "source_ids": [
          "snapshot:coverage",
          "note:latency",
          "note:scope",
          "note:selection",
          "note:significance",
          "note:units",
          "note:validation"
        ]
      }
    }
  ],
  "content_sha256": "beba967b57716b55081e43a8d10a2484bf9e373f921f8ee510efe97cca82d1e1"
}
```

