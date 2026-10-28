# Stage 3 Report: LoRA SFT Warm-Up

This note records the Stage 3 supervised fine-tuning warm-up run for later dissertation writing.

## Motivation

Stage 2 trained a LoRA adapter directly with DPO from a newly initialized adapter. Although the DPO validation objective looked strong, free-form GSM8K evaluation was poor. This suggested that the model learned the pairwise preference signal without reliably preserving the expected answer format and problem-solving behavior.

Stage 3 therefore changes the route:

```text
Qwen/Qwen3-8B
  -> LoRA SFT on concise GLM-5.2 chosen responses
  -> evaluate free-form generation
  -> use this SFT adapter as the initialization for later DPO
```

The Stage 3 result reported here is the SFT warm-up only. Stage 3 DPO has not been run yet.

## Data

Stage 3 data is derived from the Stage 1 preference dataset.

The SFT target is the GLM-5.2 chosen response. The stricter DPO data keeps only examples where the chosen response is short and the rejected response is sufficiently longer.

| Split | SFT examples | Strict DPO examples |
| --- | ---: | ---: |
| Train | 4048 | 2894 |
| Validation | 451 | 320 |

Filtering rules used by `configs/stage3_build_data.yaml`:

| Rule | Value |
| --- | ---: |
| Require `Answer:` tag | true |
| Maximum chosen tokens | 80 |
| Minimum rejected tokens | 80 |
| Minimum compression ratio | 0.30 |

Average data statistics:

| Split | Avg SFT response tokens | Avg strict DPO rejected tokens | Avg strict DPO compression ratio |
| --- | ---: | ---: | ---: |
| Train | 38.69 | 160.69 | 71.68% |
| Validation | 38.18 | 171.47 | 73.22% |

Generated JSONL data files are ignored by git and are not uploaded to GitHub.

## Training Configuration

| Group | Hyperparameter | Value |
| --- | --- | ---: |
| Base model | Model | `Qwen/Qwen3-8B` |
| Base model | Trainable weights | LoRA adapter only |
| Base model | dtype | `bfloat16` |
| Base model | attention | `sdpa` |
| Data | max length | 1024 |
| LoRA | rank `r` | 8 |
| LoRA | alpha | 16 |
| LoRA | dropout | 0.05 |
| LoRA | target modules | q/k/v/o + gate/up/down projections |
| Training | epochs | 1 |
| Training | per-device train batch size | 1 |
| Training | gradient accumulation | 8 |
| Training | number of GPUs | 2 |
| Training | effective batch size | 16 |
| Training | learning rate | 1e-5 |
| Training | warmup ratio | 0.03 |
| Training | scheduler | cosine |
| Training | optimizer | `adamw_torch` |
| Training | gradient checkpointing | true |
| Training | eval steps | 100 |
| Training | save steps | 100 |
| Training | logging steps | 10 |

Training command:

```bash
torchrun --nproc_per_node=2 scripts/17_train_lora_sft.py --config configs/stage3_lora_sft.yaml
```

## Hardware and Runtime

| Item | Value |
| --- | ---: |
| Provider | Vast.ai |
| GPU | 2 x RTX 5090 |
| GPU memory per card | 32607 MiB |
| PyTorch | 2.11.0+cu128 |
| CUDA driver report | 13.2 |
| Training runtime | 1005 s / 16 min 45 s |
| Total optimization steps | 253 |
| Peak allocated memory per GPU | about 16.78 GB |
| Peak reserved memory per GPU | about 17.67 GB |

## Training Result

| Metric | Value |
| --- | ---: |
| Final train loss | 0.3577 |
| Final eval loss | 0.2007 |

The final adapter was saved locally under:

```text
outputs/checkpoints/stage3_lora_sft/qwen3_8b_gsm8k_cot_compression_lora_sft
```

This checkpoint directory is ignored by git.

## Free-Form Validation Evaluation

Evaluation used the held-out Stage 1 preference validation questions.

| Run | Samples | Batch size | Accuracy | Avg output tokens |
| --- | ---: | ---: | ---: | ---: |
| Quick check | 80 | 16 | 96.25% | 33.73 |
| Batch check | 80 | 32 | 96.25% | 33.63 |
| Full validation | 462 | 32 | 95.02% | 32.42 |

The full validation result is the main Stage 3 SFT result:

```text
accuracy = 95.02%
average output tokens = 32.42
```

Batch size 32 was stable during evaluation, using about 19 GB on one RTX 5090.

## Interpretation

The Stage 3 SFT warm-up substantially improves free-form answer behavior compared with Stage 2 direct DPO. The direct DPO model had poor free-form validation accuracy, while the SFT warm-up reaches about 95% on the held-out preference validation set with short outputs.

This supports the revised training plan:

```text
SFT first to learn concise correct answer style
then DPO to further prefer compressed reasoning over verbose correct reasoning
```

The next experiment should run Stage 3 DPO initialized from this SFT adapter, using `configs/stage3_lora_dpo.yaml`.

## Hugging Face Checkpoint

The Stage 3 LoRA SFT adapter has been uploaded to:

```text
N7766/qwen3-8b-gsm8k-cot-compression-lora-sft-stage3
```

URL:

```text
https://huggingface.co/N7766/qwen3-8b-gsm8k-cot-compression-lora-sft-stage3
```

Only the LoRA adapter and tokenizer files should be uploaded. Optimizer states, intermediate checkpoints, local logs, generated JSONL files, and API tokens should not be uploaded.
