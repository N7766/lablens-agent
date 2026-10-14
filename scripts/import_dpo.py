"""Import only numerical summaries, never prompts, credentials or model weights."""
import argparse
import hashlib
import json
from pathlib import Path
from lablens.importing import validate_runs

parser = argparse.ArgumentParser()
parser.add_argument("root", type=Path)
parser.add_argument("--output", type=Path, default=Path(__file__).resolve().parents[1] / "lablens/data")
args = parser.parse_args()
base = args.root.resolve()
args.output.mkdir(parents=True, exist_ok=True)
comparison = "outputs/results/stage5_model_comparison.json"
sft_path = "outputs/results/stage3_lora_sft/val_eval_full_b32.summary.json"
sources = []


def read(relative):
    raw = (base / relative).read_bytes()
    sources.append({"path": relative, "sha256": hashlib.sha256(raw).hexdigest()})
    return json.loads(raw)


sft = read(sft_path)
comparison_data = read(comparison)
rows = [("run:sft", "LoRA-SFT", sft, sft_path)]
for i, run in enumerate(comparison_data["runs"]):
    if run["label"].endswith("/final"):
        continue  # aliases of the final numbered checkpoint, not independent experiments
    raw = read(run["summary_file"])
    for key in ("correct", "num_samples", "accuracy", "avg_output_tokens"):
        if raw[key] != run[key]:
            raise ValueError(f"Summary mismatch in {run['summary_file']}: {key}")
    rows.append(("run:dpo-best" if i == 0 else f"run:dpo-{i:02d}", run["label"], raw, run["summary_file"]))
normalized = []
for rid, label, raw, path in rows:
    n, correct = raw["num_samples"], raw["correct"]
    if not 0 <= correct <= n or n <= 0 or abs(raw["accuracy"] - correct / n) > 1e-9:
        raise ValueError(f"Invalid counts in {path}")
    normalized.append({"id": rid, "label": label, "correct": correct, "num_samples": n,
        "accuracy": raw["accuracy"], "mean_length": raw["avg_output_tokens"],
        "length_unit": "whitespace_words", "split_id": "gsm8k_preference_validation_462",
        "used_for_checkpoint_selection": True, "source_path": path})
unit_source = "scripts/10_eval_stage2_model.py"
raw = (base / unit_source).read_bytes()
if "output_tokens = len(prediction.split())" not in raw.decode():
    raise ValueError("Recheck the evaluation length-unit implementation")
sources.append({"path": unit_source, "sha256": hashlib.sha256(raw).hexdigest(),
                "verified_expression": "output_tokens = len(prediction.split())"})
for run in normalized:
    run.update(project="dpo-cot-compression", dataset="openai/gsm8k", synthetic=False, tokenizer=None,
        model_config_details={"base_model": "Qwen/Qwen3-8B", "checkpoint_label": run["label"]},
        source_sha256=next(s["sha256"] for s in sources if s["path"] == run["source_path"]))
normalized = validate_runs(normalized)
(args.output / "runs.json").write_text(json.dumps(normalized, ensure_ascii=False, indent=2) + "\n")
(args.output / "provenance.json").write_text(json.dumps({"source_project": "dpo-cot-compression",
    "normalization": "avg_output_tokens renamed to mean_length with explicit whitespace_words unit; final aliases omitted",
    "sources": sources}, indent=2) + "\n")
print(f"Imported {len(normalized)} summaries; recorded {len(sources)} source hashes")
