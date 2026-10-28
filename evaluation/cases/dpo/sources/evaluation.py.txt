#!/usr/bin/env python
"""Evaluate a trained Stage 2 model on GSM8K-style JSONL data."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from statistics import mean

from tqdm import tqdm

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from src.evaluation.answer_extraction import answers_match, extract_final_answer
from src.models.stage2_load import load_model_and_tokenizer
from src.training.trl_dpo_utils import load_stage2_config
from src.utils.io import load_yaml
from src.utils.io import ensure_parent_dir, read_jsonl, write_json


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="configs/stage2_eval.yaml")
    parser.add_argument("--model_dir", default=None)
    parser.add_argument("--eval_file", default=None)
    parser.add_argument("--output_file", default=None)
    parser.add_argument("--max_samples", type=int, default=None)
    parser.add_argument("--max_new_tokens", type=int, default=None)
    parser.add_argument("--temperature", type=float, default=None)
    parser.add_argument("--batch_size", type=int, default=None)
    parser.add_argument("--dry_run", action="store_true")
    return parser.parse_args()


def resolve_eval_config(path: str) -> tuple[dict, str, str, str, int, float, int]:
    raw = load_yaml(path)
    if "experiment" in raw:
        cfg = load_stage2_config(path)
        return (
            cfg,
            cfg["paths"]["output_dir"],
            cfg["data"]["val_file"],
            str(Path(cfg["paths"]["metrics_dir"]) / f"{cfg['experiment']['name']}_eval.json"),
            512,
            0.0,
            1,
        )

    model_cfg = raw.get("model", {})
    data_cfg = raw.get("data", {})
    gen_cfg = raw.get("generation", {})
    paths = raw.get("paths", {})
    cfg = {
        "model": {
            "base_model_name_or_path": model_cfg.get("model_dir"),
            "base_model": model_cfg.get("base_model_name_or_path"),
            "trust_remote_code": model_cfg.get("trust_remote_code", True),
            "torch_dtype": model_cfg.get("torch_dtype", "bfloat16"),
        }
    }
    return (
        cfg,
        model_cfg["model_dir"],
        data_cfg["eval_file"],
        paths.get("output_file", "outputs/results/stage2_eval.json"),
        int(gen_cfg.get("max_new_tokens", 512)),
        float(gen_cfg.get("temperature", 0.0)),
        int(gen_cfg.get("batch_size", 1)),
    )


def prompt_from_row(row: dict) -> str:
    if "question" in row:
        question = row["question"]
    elif "prompt" in row:
        return row["prompt"]
    else:
        raise KeyError("Evaluation row must contain question or prompt.")
    return (
        "Solve the following math problem briefly.\n\n"
        "End with the final answer exactly in this format:\n\n"
        "Answer: <number>\n\n"
        f"Question:\n{question}"
    )


def gold_from_row(row: dict) -> str | None:
    return row.get("gold_answer") or row.get("answer")


def main() -> None:
    args = parse_args()
    cfg, config_model_dir, config_eval_file, config_output_file, config_max_new_tokens, config_temperature, config_batch_size = resolve_eval_config(args.config)
    model_dir = args.model_dir or config_model_dir
    eval_file = args.eval_file or config_eval_file
    output_file = args.output_file or config_output_file
    max_new_tokens = args.max_new_tokens if args.max_new_tokens is not None else config_max_new_tokens
    temperature = args.temperature if args.temperature is not None else config_temperature
    batch_size = args.batch_size if args.batch_size is not None else config_batch_size

    rows = read_jsonl(eval_file)
    if args.max_samples:
        rows = rows[: args.max_samples]
    print(f"Model dir: {model_dir}")
    print(f"Eval file: {eval_file}")
    print(f"Rows: {len(rows)}")
    print(f"Max new tokens: {max_new_tokens}")
    print(f"Batch size: {batch_size}")

    if args.dry_run:
        missing_gold = sum(1 for row in rows if gold_from_row(row) is None)
        print(f"Dry run complete. Missing gold answers: {missing_gold}")
        return

    import torch
    model_cfg = cfg["model"]
    model_cfg["base_model_name_or_path"] = model_cfg.get("base_model") or model_cfg.get("base_model_name_or_path")
    model, tokenizer = load_model_and_tokenizer(model_dir, model_cfg)
    tokenizer.padding_side = "left"
    model.eval()

    device = "cuda" if torch.cuda.is_available() else "cpu"
    model.to(device)
    outputs = []
    for start in tqdm(range(0, len(rows), batch_size), desc="evaluating"):
        batch_rows = rows[start : start + batch_size]
        prompts = [prompt_from_row(row) for row in batch_rows]
        golds = [gold_from_row(row) for row in batch_rows]
        inputs = tokenizer(prompts, return_tensors="pt", padding=True).to(device)
        gen_kwargs = {
            "max_new_tokens": max_new_tokens,
            "do_sample": temperature > 0,
            "temperature": temperature if temperature > 0 else None,
            "pad_token_id": tokenizer.eos_token_id,
            "eos_token_id": tokenizer.eos_token_id,
        }
        gen_kwargs = {k: v for k, v in gen_kwargs.items() if v is not None}
        with torch.no_grad():
            generated = model.generate(**inputs, **gen_kwargs)
        prompt_width = inputs["input_ids"].shape[1]
        predictions = tokenizer.batch_decode(generated[:, prompt_width:], skip_special_tokens=True)
        for row, gold, prediction in zip(batch_rows, golds, predictions):
            pred_answer = extract_final_answer(prediction)
            output_tokens = len(prediction.split())
            outputs.append(
                {
                    "id": row.get("id"),
                    "gold_answer": gold,
                    "prediction": prediction,
                    "pred_answer": pred_answer,
                    "is_correct": answers_match(pred_answer, gold) if gold is not None else None,
                    "output_tokens": output_tokens,
                }
            )

    correct = [row for row in outputs if row["is_correct"] is True]
    summary = {
        "num_samples": len(outputs),
        "correct": len(correct),
        "accuracy": len(correct) / len(outputs) if outputs else 0.0,
        "avg_output_tokens": mean([row["output_tokens"] for row in outputs]) if outputs else 0.0,
        "max_new_tokens": max_new_tokens,
        "batch_size": batch_size,
        "model_dir": str(model_dir),
        "eval_file": str(eval_file),
    }
    ensure_parent_dir(output_file)
    Path(output_file).write_text(json.dumps({"summary": summary, "outputs": outputs}, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    write_json(str(Path(output_file).with_suffix(".summary.json")), summary)
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
