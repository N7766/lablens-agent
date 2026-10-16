"""Post-hoc controlled replay + one live repair. Never include these in fresh-task scores."""
import hashlib
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from lablens.guided_planning import draft_guided, GUIDED_SYSTEM
from lablens.model import HTTPModel
from lablens.planning import PlanRequest, execute_plan

ROOT = Path(__file__).resolve().parents[1]


def run(source_dir):
    # These two baseline failures were chosen after observing the v0.7 comparison.
    names = ['guided-pareto-and-singleton-r1.json', 'guided-bounded-top-three-r2.json']
    selected = {name: json.loads((source_dir/name).read_text()) for name in names}
    out = ROOT/'reports'/datetime.now(timezone.utc).strftime('controlled-repair-%Y%m%dT%H%M%S%fZ')
    out.mkdir(parents=True, exist_ok=False)
    files = list((ROOT/'lablens').glob('*.py'))+list((ROOT/'lablens/data').glob('*.json'))+[Path(__file__).resolve()]
    hashes = {}
    for f in files:
        rel=f.relative_to(ROOT); hashes[str(rel)]=hashlib.sha256(f.read_bytes()).hexdigest()
        target=out/'frozen-source'/rel; target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(f.read_bytes())
    manifest=dict(design='Post-hoc selected baseline failure replies replayed once, followed by at most one new live model request. Not fresh inference or independent holdout.',
                  git_head=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
                  source_sha256=hashes, prompt=GUIDED_SYSTEM,
                  selected={n:hashlib.sha256((source_dir/n).read_bytes()).hexdigest() for n in names})
    (out/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n')
    print(out,flush=True)
    for name,source in selected.items():
        initial=next(e for e in source['events'] if e['type']=='model')
        class ReplayThenLive:
            mode='controlled_replay_live_repair'
            def __init__(self): self.calls=0; self.live_calls=0; self.http=HTTPModel()
            def complete(self,messages,tools):
                self.calls+=1
                if self.calls==1:
                    return initial['message'],dict(prompt_tokens=0,completion_tokens=0,estimated_cost=0,source='replayed_not_billed')
                self.live_calls+=1
                return self.http.complete(messages,tools)
        model=ReplayThenLive();trace=draft_guided(model,source['question'],check_request_rules=True)
        trace['evaluation_design']=manifest['design']; trace['replayed_model_replies']=1
        trace['live_model_requests']=model.live_calls;trace['initial_source']=name
        trace['initial_source_sha256']=manifest['selected'][name]
        first=True
        for event in trace['events']:
            if event['type']=='model':
                event['response_source']='replayed' if first else 'live'; first=False
        if trace['status']=='needs_review':
            trace['execution']=execute_plan(PlanRequest(question=trace['question'],plan=trace['plan'],preview_id=trace['preview']['preview_id']))
            trace['status']=trace['execution']['status']
        (out/('checked-'+name[7:])).write_text(json.dumps(trace,ensure_ascii=False,indent=2)+'\n')
        print(name,trace['status'],'live_requests',model.live_calls,flush=True)
    return out


if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('source_directory',type=Path)
    run(parser.parse_args().source_directory)
