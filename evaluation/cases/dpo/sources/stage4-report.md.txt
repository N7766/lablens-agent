# Stage 4 Report: Full-Parameter DPO with FSDP

This note records the Stage 4 full-parameter DPO experiment and compares it with the Stage 3 LoRA SFT result.

## Objective

Stage 4 tests whether full-parameter DPO can improve concise reasoning preferences beyond the Stage 3 LoRA SFT warm-up.

Training route:

```text
Qwen/Qwen3-8B
  -> full-parameter DPO
  -> FSDP on 2 x H200
  -> evaluate free-form GSM8K-style answer generation
```

## Data

Stage 4 uses the strict Stage 3 DPO preference data:

| Split | Examples |
| --- | ---: |
| Train | 2894 |
| Validation | 320 |

The free-form evaluation uses the same 462-question validation set used for Stage 3.

## Training Configuration

| Item | Value |
| --- | ---: |
| Base model | `Qwen/Qwen3-8B` |
| Method | Full-parameter DPO |
| Distributed strategy | FSDP full shard |
| GPUs | 2 x H200 |
| Max sequence length | 2048 |
| Max prompt length | 512 |
| Per-device train batch size | 4 |
| Gradient accumulation | 2 |
| Effective batch size | 16 |
| Learning rate | 5e-7 |
| DPO beta | 0.1 |
| Epochs | 1 |
| Reference log-probs | Precomputed |
| FSDP activation checkpointing | Enabled |
| dtype | bfloat16 |

Training command:

```bash
HF_HOME=/workspace/.hf_home \
PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True \
OMP_NUM_THREADS=8 \
torchrun --standalone --nproc_per_node=2 \
scripts/08_train_full_dpo.py \
--config configs/stage4_full_dpo_h200.yaml
```

## Training Result

| Metric | Value |
| --- | ---: |
| Global step | 181 |
| Train runtime | 1204 s |
| Train loss | 0.0587 |
| Preference eval loss | 1.484e-05 |
| Preference eval rewards accuracy | 1.00 |
| Preference eval rewards margin | 21.67 |
| Peak allocated GPU memory | 94.79 GB |
| Peak reserved GPU memory | 116.60 GB |

The final model was saved on the remote GPU server under:

```text
outputs/checkpoints/stage4_full_dpo_h200/qwen3_8b_gsm8k_cot_compression_full_dpo_h200
```

Generated checkpoints and model weights are ignored by git.

## Free-Form Evaluation

Both Stage 3 and Stage 4 were evaluated on the same 462-question validation set with `max_new_tokens=128`.

| Metric | Stage 3 LoRA SFT | Stage 4 Full DPO FSDP | Delta |
| --- | ---: | ---: | ---: |
| Samples | 462 | 462 | 0 |
| Correct | 439 | 153 | -286 |
| Accuracy | 95.02% | 33.12% | -61.90 pp |
| Avg output tokens | 32.42 | 79.68 | +47.26 |

## Interpretation

Stage 4 successfully trained full-parameter DPO with FSDP and strongly optimized the pairwise preference objective. However, free-form GSM8K answer accuracy dropped sharply compared with Stage 3 LoRA SFT.

The result suggests that direct full-parameter DPO over-optimized the preference objective and damaged general answer-generation behavior. Stage 3 LoRA SFT remains the best deployable model so far.

For the dissertation, Stage 4 is still useful as a negative result:

```text
Preference-objective success does not guarantee free-form reasoning accuracy.
For this dataset, SFT preserved answer behavior better than direct full-parameter DPO.
```

## Saved Local Artifacts

Small logs and metrics were synchronized locally:

```text
outputs/logs/stage4_full_dpo_h200/
outputs/results/stage4_full_dpo_h200/
```

Key local comparison files:

```text
outputs/results/stage4_full_dpo_h200/val_eval_full_b16.summary.json
outputs/results/stage4_full_dpo_h200/stage3_vs_stage4_comparison.json
outputs/results/stage4_full_dpo_h200/stage3_vs_stage4_comparison.md
```
