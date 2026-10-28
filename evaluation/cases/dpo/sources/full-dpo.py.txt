#!/usr/bin/env python
"""Train the Stage 2 full-parameter DPO model with TRL.

This route is GPU-heavy. It is designed for distributed training with FSDP,
gradient checkpointing, gradient accumulation, and reference log-prob
precomputation so the reference model does not remain resident during training.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from src.training.trl_dpo_utils import (
    common_dpo_config_kwargs,
    fail_with_dependency_hint,
    filter_config_kwargs,
    gpu_memory_log_path,
    load_preference_datasets,
    load_stage2_config,
    load_tokenizer,
    model_init_kwargs,
    prepare_output_dirs,
    print_training_banner,
    require_training_files,
)
from src.training.callbacks import GpuMemoryCallback


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="configs/stage2_full_dpo.yaml")
    parser.add_argument("--dry_run", action="store_true", help="Load config/data only; do not load the model or train.")
    parser.add_argument("--max_steps", type=int, default=None, help="Override max_steps for smoke testing.")
    parser.add_argument("--max_train_samples", type=int, default=None, help="Use a small train subset for smoke testing.")
    parser.add_argument("--max_eval_samples", type=int, default=None, help="Use a small validation subset for smoke testing.")
    parser.add_argument("--select_longest_samples", action="store_true", help="Use the longest examples before applying sample limits.")
    parser.add_argument("--max_length", type=int, default=None, help="Override DPO max_length for memory testing.")
    parser.add_argument("--max_prompt_length", type=int, default=None, help="Override DPO max_prompt_length for memory testing.")
    parser.add_argument("--per_device_train_batch_size", type=int, default=None, help="Override per-device train batch size.")
    parser.add_argument("--gradient_accumulation_steps", type=int, default=None, help="Override gradient accumulation steps.")
    parser.add_argument(
        "--disable_precompute_ref_log_probs",
        action="store_true",
        help="Disable reference log-prob precomputation for compatibility smoke tests.",
    )
    parser.add_argument("--skip_save", action="store_true", help="Do not save the model after training; useful for smoke tests.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    cfg = load_stage2_config(args.config)

    if cfg["experiment"].get("method") != "full_dpo":
        raise ValueError("This script expects experiment.method: full_dpo")

    if args.max_length is not None:
        cfg["data"]["max_length"] = int(args.max_length)
    if args.max_prompt_length is not None:
        cfg["data"]["max_prompt_length"] = int(args.max_prompt_length)
    if args.per_device_train_batch_size is not None:
        cfg["training"]["per_device_train_batch_size"] = int(args.per_device_train_batch_size)
    if args.gradient_accumulation_steps is not None:
        cfg["training"]["gradient_accumulation_steps"] = int(args.gradient_accumulation_steps)
    if args.disable_precompute_ref_log_probs:
        cfg["dpo"]["precompute_ref_log_probs"] = False

    require_training_files(cfg)
    prepare_output_dirs(cfg)
    print_training_banner(cfg, "full-parameter")

    dataset = load_preference_datasets(cfg)
    if args.select_longest_samples:
        def add_sort_len(row: dict) -> dict:
            return {"_sort_len": len(row["prompt"]) + max(len(row["chosen"]), len(row["rejected"]))}

        dataset = dataset.map(add_sort_len)
        dataset["train"] = dataset["train"].sort("_sort_len", reverse=True).remove_columns("_sort_len")
        dataset["validation"] = dataset["validation"].sort("_sort_len", reverse=True).remove_columns("_sort_len")
    if args.max_train_samples is not None:
        n = min(args.max_train_samples, len(dataset["train"]))
        dataset["train"] = dataset["train"].select(range(n))
    if args.max_eval_samples is not None:
        n = min(args.max_eval_samples, len(dataset["validation"]))
        dataset["validation"] = dataset["validation"].select(range(n))
    print(f"Train rows: {len(dataset['train'])}")
    print(f"Validation rows: {len(dataset['validation'])}")
    print(f"FSDP: {cfg['training'].get('fsdp')}")
    print(f"Gradient checkpointing: {cfg['training'].get('gradient_checkpointing')}")
    print(f"Gradient accumulation steps: {cfg['training'].get('gradient_accumulation_steps')}")
    print(f"Precompute reference log-probs: {cfg['dpo'].get('precompute_ref_log_probs')}")

    if args.dry_run:
        print("Dry run complete. Model was not loaded and training was not started.")
        return

    try:
        from transformers import AutoModelForCausalLM
        from trl import DPOConfig, DPOTrainer
    except Exception as exc:  # pragma: no cover - depends on training env
        fail_with_dependency_hint(exc)

    tokenizer = load_tokenizer(cfg)
    init_kwargs = model_init_kwargs(cfg)
    dpo_kwargs = common_dpo_config_kwargs(cfg, max_steps=args.max_steps)
    dpo_kwargs["fsdp"] = cfg["training"].get("fsdp")
    dpo_kwargs["fsdp_config"] = cfg["training"].get("fsdp_config")
    dpo_kwargs["activation_offloading"] = bool(cfg["dpo"].get("activation_offloading", False))
    if args.skip_save:
        dpo_kwargs["save_strategy"] = "no"
    dpo_args = DPOConfig(**filter_config_kwargs(DPOConfig, dpo_kwargs))

    model = AutoModelForCausalLM.from_pretrained(
        cfg["model"]["base_model_name_or_path"],
        **init_kwargs,
    )
    if cfg["dpo"].get("precompute_ref_log_probs", False):
        # TRL precomputes reference log-probs during DPOTrainer initialization,
        # before Trainer/FSDP moves the model. Move each rank's temporary
        # precompute model to its local GPU to avoid CPU/GPU device mismatches.
        import os
        import torch

        if torch.cuda.is_available():
            local_rank = int(os.environ.get("LOCAL_RANK", "0"))
            model.to(torch.device("cuda", local_rank))

    fsdp_activation_checkpointing = bool(
        (cfg["training"].get("fsdp_config") or {}).get("activation_checkpointing", False)
    )
    if cfg["training"].get("gradient_checkpointing", True) or fsdp_activation_checkpointing:
        model.config.use_cache = False

    trainer = DPOTrainer(
        model=model,
        ref_model=None,
        args=dpo_args,
        train_dataset=dataset["train"],
        eval_dataset=dataset["validation"],
        processing_class=tokenizer,
        callbacks=[GpuMemoryCallback(gpu_memory_log_path(cfg))],
    )
    trainer.train()
    if args.skip_save:
        print("Skipping model save because --skip_save was set.")
    else:
        trainer.save_model(cfg["paths"]["output_dir"])
        tokenizer.save_pretrained(cfg["paths"]["output_dir"])


if __name__ == "__main__":
    main()
