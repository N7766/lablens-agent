#!/usr/bin/env python
"""Build DPO preference pairs from rejected and chosen generation files."""

from __future__ import annotations

import argparse
import random
import re
import sys
from pathlib import Path
from statistics import mean

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from src.utils.io import load_yaml, read_jsonl, write_json, write_jsonl


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="configs/stage1_build_dpo.yaml")
    return parser.parse_args()


def prompt_for_question(question: str) -> str:
    return (
        "Solve the following math problem.\n\n"
        "Think carefully and explain the reasoning before giving the final answer.\n\n"
        "End with the final answer exactly in this format:\n\n"
        "Answer: <number>\n\n"
        f"Question:\n{question}"
    )


def build_by_id(rows: list[dict]) -> dict[str, dict]:
    by_id = {}
    duplicates = 0
    for row in rows:
        row_id = row.get("id")
        if not row_id:
            continue
        if row_id in by_id:
            duplicates += 1
            continue
        by_id[row_id] = row
    if duplicates:
        print(f"Warning: ignored {duplicates} duplicate ids.")
    return by_id


def id_sort_key(row_id: str) -> tuple[str, int]:
    match = re.match(r"(.+?)_(\d+)$", row_id)
    if not match:
        return row_id, -1
    return match.group(1), int(match.group(2))


def passes_filters(rejected: dict, chosen: dict, filters: dict) -> tuple[bool, str | None]:
    rejected_tokens = int(rejected.get("output_tokens", 0))
    chosen_tokens = int(chosen.get("output_tokens", 0))

    if filters.get("require_rejected_correct", True) and not bool(rejected.get("is_correct")):
        return False, "rejected_incorrect"
    if filters.get("require_chosen_correct", True) and not bool(chosen.get("is_correct")):
        return False, "chosen_incorrect"
    if filters.get("require_chosen_nonempty", True) and not chosen.get("prediction"):
        return False, "chosen_empty"
    if rejected_tokens < int(filters.get("min_rejected_tokens", 21)):
        return False, "rejected_too_short"
    if chosen_tokens < int(filters.get("min_chosen_tokens", 1)):
        return False, "chosen_too_short"
    if filters.get("require_chosen_shorter", True) and chosen_tokens >= rejected_tokens:
        return False, "chosen_not_shorter"
    return True, None


def make_pair(rejected: dict, chosen: dict) -> dict:
    rejected_tokens = int(rejected.get("output_tokens", 0))
    chosen_tokens = int(chosen.get("output_tokens", 0))
    compression_ratio = 1 - (chosen_tokens / rejected_tokens) if rejected_tokens > 0 else None

    return {
        "id": rejected["id"],
        "question": rejected["question"],
        "gold_answer": rejected.get("gold_answer"),
        "prompt": prompt_for_question(rejected["question"]),
        "chosen": chosen.get("prediction", ""),
        "rejected": rejected.get("prediction", ""),
        "chosen_answer": chosen.get("pred_answer"),
        "rejected_answer": rejected.get("pred_answer"),
        "chosen_tokens": chosen_tokens,
        "rejected_tokens": rejected_tokens,
        "compression_ratio": compression_ratio,
        "chosen_model": chosen.get("teacher_model"),
        "rejected_model": "Qwen/Qwen3-8B",
    }


def split_pairs(pairs: list[dict], split_cfg: dict) -> tuple[list[dict], list[dict]]:
    val_ratio = float(split_cfg.get("val_ratio", 0.0))
    if val_ratio <= 0:
        return pairs, []
    if val_ratio >= 1:
        raise ValueError("split.val_ratio must be between 0 and 1.")

    seed = int(split_cfg.get("seed", 42))
    shuffled = pairs[:]
    random.Random(seed).shuffle(shuffled)
    val_count = max(1, round(len(shuffled) * val_ratio)) if shuffled else 0
    val_ids = {row["id"] for row in shuffled[:val_count]}
    train_pairs = [row for row in pairs if row["id"] not in val_ids]
    val_pairs = [row for row in pairs if row["id"] in val_ids]
    return train_pairs, val_pairs


def main() -> None:
    args = parse_args()
    cfg = load_yaml(args.config)
    paths = cfg["paths"]
    filters = cfg.get("filters", {})
    split_cfg = cfg.get("split", {})

    rejected_rows = read_jsonl(paths["rejected_file"])
    chosen_rows = read_jsonl(paths["chosen_file"])
    rejected_by_id = build_by_id(rejected_rows)
    chosen_by_id = build_by_id(chosen_rows)

    pairs = []
    rejection_reasons: dict[str, int] = {}
    matched_ids = sorted(set(rejected_by_id) & set(chosen_by_id), key=id_sort_key)

    for row_id in matched_ids:
        rejected = rejected_by_id[row_id]
        chosen = chosen_by_id[row_id]
        keep, reason = passes_filters(rejected, chosen, filters)
        if keep:
            pairs.append(make_pair(rejected, chosen))
        else:
            rejection_reasons[reason or "unknown"] = rejection_reasons.get(reason or "unknown", 0) + 1

    train_pairs, val_pairs = split_pairs(pairs, split_cfg)

    train_output = paths.get("train_output_file") or paths.get("output_file")
    val_output = paths.get("val_output_file")
    if not train_output:
        raise ValueError("Config must define paths.train_output_file or paths.output_file.")
    write_jsonl(train_output, train_pairs)
    if val_output:
        write_jsonl(val_output, val_pairs)

    chosen_tokens = [row["chosen_tokens"] for row in pairs]
    rejected_tokens = [row["rejected_tokens"] for row in pairs]
    compression = [row["compression_ratio"] for row in pairs if row["compression_ratio"] is not None]
    summary = {
        "rejected_input_count": len(rejected_rows),
        "chosen_input_count": len(chosen_rows),
        "matched_count": len(matched_ids),
        "dpo_pair_count": len(pairs),
        "train_pair_count": len(train_pairs),
        "val_pair_count": len(val_pairs),
        "split": {
            "val_ratio": float(split_cfg.get("val_ratio", 0.0)),
            "seed": int(split_cfg.get("seed", 42)),
        },
        "unmatched_rejected_count": len(set(rejected_by_id) - set(chosen_by_id)),
        "unmatched_chosen_count": len(set(chosen_by_id) - set(rejected_by_id)),
        "filtered_counts": rejection_reasons,
        "avg_chosen_tokens": mean(chosen_tokens) if chosen_tokens else 0.0,
        "avg_rejected_tokens": mean(rejected_tokens) if rejected_tokens else 0.0,
        "avg_compression_ratio": mean(compression) if compression else 0.0,
        "train_output_file": train_output,
        "val_output_file": val_output,
    }
    write_json(paths["summary_file"], summary)

    print("DPO preference build summary")
    print("=" * 34)
    print(f"Rejected input rows: {summary['rejected_input_count']}")
    print(f"Chosen input rows: {summary['chosen_input_count']}")
    print(f"Matched ids: {summary['matched_count']}")
    print(f"DPO pairs written: {summary['dpo_pair_count']}")
    print(f"Train pairs: {summary['train_pair_count']}")
    print(f"Val pairs: {summary['val_pair_count']}")
    print(f"Avg chosen tokens: {summary['avg_chosen_tokens']}")
    print(f"Avg rejected tokens: {summary['avg_rejected_tokens']}")
    print(f"Avg compression ratio: {summary['avg_compression_ratio']}")
    print(f"Filtered counts: {summary['filtered_counts']}")
    print(f"Train output file: {train_output}")
    if val_output:
        print(f"Val output file: {val_output}")
    print(f"Summary file: {paths['summary_file']}")


if __name__ == "__main__":
    main()
