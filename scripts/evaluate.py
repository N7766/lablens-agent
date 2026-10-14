"""Live model evaluation. Automated checks are diagnostics, not task success scores."""
import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from lablens.agent import Agent
from lablens.model import HTTPModel

parser = argparse.ArgumentParser()
parser.add_argument("--limit", type=int, default=3)
parser.add_argument("--output", type=Path, default=Path("reports"))
args = parser.parse_args()
cases = json.loads((Path(__file__).resolve().parents[1] / "evaluation/cases.json").read_text())
if not 1 <= args.limit <= len(cases):
    parser.error(f"--limit must be between 1 and {len(cases)}")
model = HTTPModel()  # fail before generating results when not configured
out = args.output / datetime.now(timezone.utc).strftime("eval-%Y%m%dT%H%M%S%fZ")
out.mkdir(parents=True, exist_ok=False)
results = []
for case in cases[:args.limit]:
    trace = Agent(model).run(case["question"])
    (out / (case["id"] + ".json")).write_text(json.dumps(trace, ensure_ascii=False, indent=2))
    called = {e["name"] for e in trace["events"] if e["type"] == "tool" and "error" not in e["output"]}
    cited = {s for claim in (trace.get("answer") or {}).get("claims", []) for s in claim["source_ids"]}
    results.append({"case_id": case["id"], "status": trace["status"],
        "required_tools_called": set(case["required_tools"]) <= called,
        "expected_sources_cited": set(case["expected_sources"]) <= cited,
        "latency_seconds": trace["latency_seconds"], "model_calls": trace["model_calls"],
        "tool_calls": trace["tool_calls"], "rubric": case["rubric"],
        "manual_factuality_pass": None, "manual_citation_support_pass": None,
        "manual_task_success": None, "review_notes": ""})
report = {"mode": "live", "model": model.model, "cases": results,
          "warning": "Tool and source checks do not establish answer correctness. Complete manual review before reporting task success."}
(out / "summary.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
print(out)
