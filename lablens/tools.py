import json
import math
import re
from collections import Counter
from pathlib import Path
from .schemas import ListRuns, CompareRuns, SearchNotes

DATA = Path(__file__).parent / "data"


def tokenize(text: str) -> list[str]:
    words = re.findall(r"[a-z0-9_]+", text.lower())
    for segment in re.findall(r"[\u4e00-\u9fff]+", text):
        words.extend(segment[i:i + 2] for i in range(max(1, len(segment) - 1)))
    return words


class ToolBox:
    """Allowlisted read-only tools. No model-supplied file paths or code execution."""
    definitions = {
        "list_runs": (ListRuns, "List experiment summaries; accuracy is a fraction, length is whitespace words."),
        "compare_runs": (CompareRuns, "Compare two IDs from list_runs; returns computed deltas and evidence limitations."),
        "search_notes": (SearchNotes, "Search local methodology notes with BM25 lexical retrieval (Chinese/English)."),
    }

    def __init__(self, data_dir: Path = DATA):
        self.runs = json.loads((data_dir / "runs.json").read_text())
        self.notes = json.loads((data_dir / "notes.json").read_text())
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

    def compare_runs(self, args):
        if args.baseline_id not in self.index or args.candidate_id not in self.index:
            raise ValueError("Unknown run ID; call list_runs first")
        a, b = self.index[args.baseline_id], self.index[args.candidate_id]
        if (a["split_id"], a["num_samples"], a["length_unit"]) != (b["split_id"], b["num_samples"], b["length_unit"]):
            raise ValueError("Incompatible split, sample count, or length unit")
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
