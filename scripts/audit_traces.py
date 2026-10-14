"""Offline numeric/source diagnostics. These are not task-success or human ratings."""
import argparse
import json
from pathlib import Path
from lablens.evidence import validate_answer
from lablens.schemas import Answer
from lablens.tools import ToolBox


def audit(trace):
    if trace.get('answer') is None:
        return dict(structured_evidence_pass=False,error='No final answer')
    if trace['mode']=='live_direct':
        tools=ToolBox()
        outputs=[tools.execute('list_runs',{}),dict(notes=tools.notes,source_ids=[n['id'] for n in tools.notes])]
        # Offline oracle only; these calculations were never shown to the direct model.
        for a in tools.runs:
            for b in tools.runs:
                outputs.append(tools.execute('compare_runs',dict(baseline_id=a['id'],candidate_id=b['id'])))
    else:
        outputs=[e['output'] for e in trace['events'] if e['type']=='tool' and 'error' not in e['output']]
    try:
        validate_answer(Answer.model_validate(trace['answer']),outputs)
    except ValueError as exc:
        return dict(structured_evidence_pass=False,error=str(exc)[:1500])
    return dict(structured_evidence_pass=True,error=None)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory',type=Path)
    args=parser.parse_args()
    results={p.name:audit(json.loads(p.read_text())) for p in sorted(args.directory.glob('*.json')) if p.name.startswith(('agent-','direct-'))}
    (args.directory/'diagnostics.json').write_text(json.dumps(results,ensure_ascii=False,indent=2))
    print(json.dumps(results,ensure_ascii=False,indent=2))


if __name__=='__main__': main()
