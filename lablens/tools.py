import json
import hashlib
import math
import re
from collections import Counter
from pathlib import Path
from .schemas import ListRuns, CompareRuns, SearchNotes, QueryRuns, DescribeData
from .importing import validate_runs, comparison_key
from .datasets import DatasetBundle, snapshot_id, digest

DATA = Path(__file__).parent / "data"


def tokenize(text: str) -> list[str]:
    words = re.findall(r"[a-z0-9_]+", text.lower())
    for segment in re.findall(r"[\u4e00-\u9fff]+", text):
        words.extend(segment[i:i + 2] for i in range(max(1, len(segment) - 1)))
    return words


class ToolBox:
    """Allowlisted read-only tools. No model-supplied file paths or code execution."""
    definitions = {
        "query_runs": (QueryRuns, "Filter ALL matching runs, sort with ties, or find Pareto frontier (maximize accuracy, minimize length). max_length is strict <. Baseline filters are strict improvements. Copy query_id and ALL ordered run IDs to final selection."),
        "describe_data": (DescribeData, "Describe available fields, missing metadata and read-only capabilities; includes all methodology notes. Use for missing evidence or methodology questions."),
        "list_runs": (ListRuns, "List experiment summaries; accuracy is a fraction; inspect each run length_unit and tokenizer for length units."),
        "compare_runs": (CompareRuns, "Compare two IDs from list_runs; returns computed deltas and evidence limitations."),
        "search_notes": (SearchNotes, "Search local methodology notes with BM25 lexical retrieval (Chinese/English)."),
    }

    def __init__(self, data_dir: Path = DATA, bundle=None):
        if bundle is None:
            bundle = {"runs": json.loads((data_dir / "runs.json").read_text()), "notes": json.loads((data_dir / "notes.json").read_text())}
        normalized = DatasetBundle.model_validate(bundle).normalized()
        self.runs, self.notes = normalized["runs"], normalized["notes"]
        self.snapshot_id = snapshot_id(self.runs, self.notes)
        self.index = {r["id"]: r for r in self.runs}
        self.corpus = [Counter(tokenize(n["title"] + " " + n["text"])) for n in self.notes]

    def schemas(self):
        return [{"type": "function", "function": {"name": name, "description": desc,
                 "parameters": schema.model_json_schema()}}
                for name, (schema, desc) in self.definitions.items()]

    def execute(self, name, arguments):
        if name not in self.definitions:
            raise ValueError("Unknown tool; use the supplied tool definitions")
        args = self.definitions[name][0].model_validate(arguments)
        return getattr(self, name)(args)

    def list_runs(self, args):
        rows = [r for r in self.runs if r["accuracy"] >= args.min_accuracy]
        return {"runs": rows, "source_ids": [r["id"] for r in rows]}

    def describe_data(self, args):
        fields = sorted(set.intersection(*(set(r) for r in self.runs)))
        return {"fields": fields, "scope": "Only imported summaries and notes; no training, deletion, uploads, internet access or file writes",
                "missing_fields": [k for k in ("seed", "gpu_model", "gpu_count", "training_cost", "paired_predictions", "latency", "peak_vram") if k not in fields],
                "notes": self.notes, "source_ids": ["snapshot:coverage"] + [n["id"] for n in self.notes]}

    def query_runs(self, args):
        if args.min_accuracy > args.max_accuracy:
            raise ValueError("min_accuracy exceeds max_accuracy")
        if len({comparison_key(r) for r in self.runs}) != 1:
            raise ValueError("Incompatible records: query one compatible dataset at a time")
        for rid in (args.shorter_than_baseline, args.more_accurate_than_baseline):
            if rid is not None and rid not in self.index:
                raise ValueError(f"Unknown baseline run ID: {rid}. Query filters require an actual run:ID from this snapshot, not a query/action ID. Available IDs: " + ', '.join(sorted(self.index)))
        rows = [r for r in self.runs if args.min_accuracy <= r["accuracy"] <= args.max_accuracy
                and r["correct"] >= args.min_correct and (args.max_length is None or r["mean_length"] < args.max_length)
                and (args.shorter_than_baseline is None or r["mean_length"] < self.index[args.shorter_than_baseline]["mean_length"])
                and (args.more_accurate_than_baseline is None or r["accuracy"] > self.index[args.more_accurate_than_baseline]["accuracy"])]
        if args.pareto_only:
            rows = [r for r in rows if not any(s["accuracy"] >= r["accuracy"] and s["mean_length"] <= r["mean_length"]
                    and (s["accuracy"] > r["accuracy"] or s["mean_length"] < r["mean_length"]) for s in rows)]
        rows.sort(key=lambda r: ((-1 if args.descending else 1) * r[args.order_by], r["id"]))
        matched = len(rows)
        if args.top_k and len(rows) > args.top_k:
            boundary = rows[args.top_k - 1][args.order_by]
            rows = [r for i, r in enumerate(rows) if i < args.top_k or (args.include_ties and r[args.order_by] == boundary)]
        query_id = "query:" + digest({"snapshot_id": self.snapshot_id, "parameters": args.model_dump()})
        notes = [n for n in self.notes if n["id"] in {"note:units", "note:validation", "note:selection"}]
        return {"query_id": query_id, "snapshot_id": self.snapshot_id, "parameters": args.model_dump(), "matched_before_top_k": matched,
                "runs": rows, "ordered_run_ids": [r["id"] for r in rows], "notes": notes,
                "source_ids": [query_id] + [r["id"] for r in rows] + [n["id"] for n in notes]}

    def compare_runs(self, args):
        if args.baseline_id not in self.index or args.candidate_id not in self.index:
            raise ValueError("Unknown run ID; call list_runs first")
        a, b = self.index[args.baseline_id], self.index[args.candidate_id]
        if comparison_key(a) != comparison_key(b):
            raise ValueError("Incompatible project, dataset, split, sample count, unit, tokenizer, or synthetic flag")
        limitations = [n for n in self.notes if n["id"] in {"note:validation", "note:units", "note:significance", "note:latency"}]
        return {
            "baseline": a, "candidate": b,
            "accuracy_delta_percentage_points": 100 * (b["accuracy"] - a["accuracy"]),
            "length_reduction_percent": 100 * (a["mean_length"] - b["mean_length"]) / a["mean_length"],
            "additional_correct": b["correct"] - a["correct"],
            "limitations": limitations,
            "source_ids": [a["id"], b["id"]] + [n["id"] for n in limitations],
        }

    def search_notes(self, args):
        query = set(tokenize(args.query))
        size = len(self.corpus)
        avgdl = sum(sum(c.values()) for c in self.corpus) / max(size, 1)
        scored = []
        for note, counts in zip(self.notes, self.corpus):
            score = 0.0
            for word in query:
                freq = counts[word]
                if not freq:
                    continue
                df = sum(word in c for c in self.corpus)
                idf = math.log(1 + (size - df + .5) / (df + .5))
                score += idf * freq * 2.5 / (freq + 1.5 * (.25 + .75 * sum(counts.values()) / avgdl))
            if score > 0:
                scored.append({**note, "score": round(score, 6)})
        rows = sorted(scored, key=lambda n: (-n["score"], n["id"]))[:args.limit]
        return {"notes": rows, "source_ids": [n["id"] for n in rows]}
