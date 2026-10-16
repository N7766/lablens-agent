"""Bounded live runs. Freeze manifest before requests; never equate execution with correctness."""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from lablens.agent import Agent, SYSTEM
from lablens.baseline import run_direct, DIRECT_SYSTEM
from lablens.model import HTTPModel
from lablens.planning import PLAN_SYSTEM, PlanRequest, draft_plan, execute_plan
from lablens.guided_planning import GUIDED_SYSTEM, draft_guided

ROOT=Path(__file__).resolve().parents[1]


def summarize(case, trace, strategy):
    usages=[e.get('usage') or {} for e in trace['events'] if e['type']=='model']
    called={e['name'] for e in trace['events'] if e['type']=='tool' and 'error' not in e['output']}
    cited={s for c in (trace.get('answer') or {}).get('claims',[]) for s in c['source_ids']}
    return dict(case_id=case['id'], strategy=strategy,status=trace['status'],
        required_tools_called=set(case.get('required_tools',[]))<=called if strategy=='agent' else None,
        expected_sources_cited=set(case.get('expected_sources',[]))<=cited,
        latency_seconds=trace['latency_seconds'],model_calls=trace['model_calls'],tool_calls=trace['tool_calls'],rubric=case['rubric'],
        reported_prompt_tokens=sum(u.get('prompt_tokens',0) for u in usages),
        reported_completion_tokens=sum(u.get('completion_tokens',0) for u in usages),
        reported_estimated_cost_usd=0 if trace['model_calls']==0 else sum(u['estimated_cost'] for u in usages) if usages and all(isinstance(u.get('estimated_cost'),(int,float)) for u in usages) else None,
        usage_incomplete=trace.get('usage_incomplete',False),manual_factuality_pass=None,manual_citation_support_pass=None,manual_task_success=None,review_notes='')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cases',type=Path,default=ROOT/'evaluation/cases.json')
    parser.add_argument('--split',choices=['dev','holdout'])
    parser.add_argument('--limit',type=int,default=3)
    parser.add_argument('--strategy',choices=['agent','planner','guided','guided_once','checked','request_rules','planning','matched','direct','direct_repair','both','all'],default='agent')
    parser.add_argument('--workers',type=int,choices=range(1,5),default=2)
    parser.add_argument('--repeats',type=int,choices=range(1,4),default=1)
    parser.add_argument('--output',type=Path,default=Path('reports'))
    args=parser.parse_args()
    cases=json.loads(args.cases.read_text())
    if args.split: cases=[c for c in cases if c.get('split')==args.split]
    if not 1<=args.limit<=len(cases): parser.error(f'--limit must be between 1 and {len(cases)}')
    cases=cases[:args.limit]
    model=HTTPModel()
    strategies=['agent','planner'] if args.strategy=='matched' else (['agent','direct','direct_repair'] if args.strategy=='all' else (['agent','direct'] if args.strategy=='both' else [args.strategy]))
    if args.strategy=='planning': strategies=['planner','guided_once','guided']
    if args.strategy=='request_rules': strategies=['guided','checked']
    out=args.output/datetime.now(timezone.utc).strftime('eval-%Y%m%dT%H%M%S%fZ');out.mkdir(parents=True,exist_ok=False)
    files=list((ROOT/'lablens').glob('*.py'))+list((ROOT/'lablens/data').glob('*.json'))+[Path(__file__).resolve(), args.cases.resolve(),ROOT/'evaluation/rubric.md']
    manifest=dict(model=model.model,model_revision='Provider model slug; backend revision not exposed',temperature=0,max_tokens=2600,request_timeout_seconds=model.timeout_seconds,
        git_head=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
        source_sha256={str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in files},
        prompts={'agent':SYSTEM,'direct':DIRECT_SYSTEM,'planner':PLAN_SYSTEM,'guided':GUIDED_SYSTEM},prompt_sha256={k:hashlib.sha256(v.encode()).hexdigest() for k,v in [('agent',SYSTEM),('direct',DIRECT_SYSTEM),('planner',PLAN_SYSTEM),('guided',GUIDED_SYSTEM)]},
        cases=cases,strategies=strategies,workers=args.workers,repeats=args.repeats,created_at=datetime.now(timezone.utc).isoformat(),
        warning='One small project snapshot, no independent human review. Shared policy refusals are not model inference. Direct/planner/guided_once have one call; guided has up to two with local validation feedback; direct_repair/Agent up to eight. Planners share full data and deterministic rendering, but prompts/schemas/budgets differ. Evaluation executes unreviewed plans, not human approval. Concurrency affects latency. guided vs checked use identical prompts, schemas and max-two-request budgets; checked adds local full-match query-rule feedback only. Unsupported syntax is not assessed; this is not general semantic validation.')
    # Keep source bytes as well as hashes, including development runs on a dirty tree.
    for file in files:
        target=out/'frozen-source'/file.relative_to(ROOT)
        target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(file.read_bytes())
    (out/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2))
    print(out,flush=True)
    def execute(case,strategy,repeat):
        if strategy in {'planner','guided','guided_once','checked'}:
            trace=draft_plan(HTTPModel(),case['question']) if strategy=='planner' else draft_guided(HTTPModel(),case['question'],max_attempts=1 if strategy=='guided_once' else 2,check_request_rules=strategy=='checked')
            trace['review_mode']='unreviewed_evaluation'
            if trace['status']=='needs_review':
                request=PlanRequest(question=case['question'],plan=trace['plan'],preview_id=trace['preview']['preview_id'])
                trace['execution']=execute_plan(request)
                trace['status']=trace['execution']['status']
        else:
            trace=Agent(HTTPModel()).run(case['question']) if strategy=='agent' else run_direct(HTTPModel(),case['question'],max_steps=8 if strategy=='direct_repair' else 1)
        (out/f'{strategy}-{case["id"]}-r{repeat}.json').write_text(json.dumps(trace,ensure_ascii=False,indent=2))
        print(strategy,case['id'],'repeat',repeat,trace['status'],trace['latency_seconds'],flush=True)
        row=summarize(case,trace,strategy)
        row.update(executable_plan='execution' in trace, plan_steps_executed=len(trace.get('execution',{}).get('executed_step_ids',[])))
        row.update(repair_calls=max(0,trace['model_calls']-1),request_conflicts=sum(e['type']=='request_check' and e['report']['has_conflicts'] for e in trace['events']),recognized_units=len(trace.get('request_checks',{}).get('units',[])),unassessed_clauses=len(trace.get('request_checks',{}).get('unassessed',[])))
        row.update(repeat=repeat,policy_refusal=any(e['type']=='policy_refusal' for e in trace['events']))
        return row
    results=[]
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures=[pool.submit(execute,c,s,r) for c in cases for r in range(1,args.repeats+1) for s in strategies]
        for future in as_completed(futures):
            results.append(future.result())
            (out/'summary.json').write_text(json.dumps(dict(mode='live',model=model.model,cases=sorted(results,key=lambda x:(x['case_id'],x['repeat'],x['strategy'])),warning=manifest['warning']),ensure_ascii=False,indent=2))
    print('Saved',len(results),'runs to',out,flush=True)


if __name__=='__main__': main()
