"""Offline numeric/source diagnostics. These are not task-success or human ratings."""
import argparse
import json
from pathlib import Path
from lablens.evidence import validate_answer, finalize_answer
from lablens.schemas import Answer
from lablens.tools import ToolBox


def audit(trace):
    if any(e['type']=='policy_refusal' for e in trace.get('events',[])):
        return dict(structured_evidence_pass=None,error=None,policy_refusal=True)
    if trace.get('presentation') == 'deterministic_query_tables':
        from lablens.presentation import render_queries
        outputs=[e['output'] for e in trace['events'] if e['type']=='tool' and 'error' not in e['output']]
        answer, views=render_queries(outputs)
        ok=answer==trace['answer'] and views==trace.get('query_views')
        return dict(structured_evidence_pass=ok,error=None if ok else 'Rendered tables differ from tool evidence',intent_validation='not_performed')
    if trace.get('answer') is None:
        return dict(structured_evidence_pass=False,error='No final answer')
    if trace['mode'].startswith('live_direct'):
        tools=ToolBox()
        outputs=[tools.execute('list_runs',{}),tools.execute('describe_data',{})]
        # Offline oracle only; not in the initial context; repair feedback can expose expected values.
        for a in tools.runs:
            for b in tools.runs:
                outputs.append(tools.execute('compare_runs',dict(baseline_id=a['id'],candidate_id=b['id'])))
    else:
        outputs=[e['output'] for e in trace['events'] if e['type']=='tool' and 'error' not in e['output']]
    try:
        validator=finalize_answer if 'limitation_ids' in trace['answer'] else validate_answer
        validator(Answer.model_validate(trace['answer']),outputs)
    except ValueError as exc:
        return dict(structured_evidence_pass=False,error=str(exc)[:1500])
    return dict(structured_evidence_pass=True,error=None)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory',type=Path)
    args=parser.parse_args()
    results={p.name:audit(json.loads(p.read_text())) for p in sorted(args.directory.glob('*.json')) if p.name.startswith(('agent-','direct-','direct_repair-'))}
    (args.directory/'diagnostics.json').write_text(json.dumps(results,ensure_ascii=False,indent=2))
    print(json.dumps(results,ensure_ascii=False,indent=2))


if __name__=='__main__': main()
