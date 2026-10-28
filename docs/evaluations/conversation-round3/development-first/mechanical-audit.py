#!/usr/bin/env python3
"""Audit frozen exposed results mechanically; never assign semantic correctness."""
import hashlib
import json
from collections import Counter
from pathlib import Path
import statistics

ROOT=Path(__file__).resolve().parent

def read(p):return json.loads(p.read_text())
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def save(p,x):
    p.parent.mkdir(parents=True,exist_ok=True)
    p.write_text(json.dumps(x,ensure_ascii=False,indent=2)+'\n')
def collect(value):
    if isinstance(value,dict):
        yield value
        for item in value.values():yield from collect(item)
    elif isinstance(value,list):
        for item in value:yield from collect(item)

checks={}
for label in ['frozen','raw-output']:
    m=read(ROOT/f'{label}-manifest.json')
    assert sha(ROOT/f'{label}-manifest.json')==(ROOT/f'{label}-manifest.sha256').read_text().split()[0]
    base=ROOT if label=='frozen' else ROOT/'run'
    for name,expected in m['files'].items():assert sha(base/name)==expected,(label,name)
    checks[label+'_manifest_files_verified']=len(m['files'])
summary=read(ROOT/'run/run-summary.json')
gold=read(ROOT/'gold/rubric.json')
out=[]
for turn in summary['turns']:
    sid,tid=turn['scenario'],turn['turn']
    p=ROOT/'run'/sid/tid/'task-final.json'
    if not p.exists():
        out.append(dict(turn=tid,runtime=turn['status'],observed=False));continue
    t=read(p);r=t.get('result') or t.get('error_result') or {};u=t.get('execution_usage') or r.get('usage') or {}
    corpus={f.name:f.read_text() for f in (ROOT/'corpora'/sid).glob('*.md')}
    citations=r.get('citations',[]);quotes=[q for a in r.get('assessments',[]) for q in a.get('quotes',[])]
    by_id={x['chunk_id']:x for x in citations}
    for d in collect(r.get('retrieval',{})):
        if all(k in d for k in ['chunk_id','source','text']):by_id.setdefault(d['chunk_id'],d)
    def source_for(c):return corpus.get(c.get('source','').rsplit('/',1)[-1],'')
    exact_c=[bool(source_for(c)) and source_for(c)[c['start']:c['end']]==c['text'] and hashlib.sha256(c['text'].encode()).hexdigest()==c['content_sha256'] for c in citations]
    exact_q=[q['chunk_id'] in by_id and q['quote'] in by_id[q['chunk_id']]['text'] and q['quote'] in source_for(by_id[q['chunk_id']]) for q in quotes]
    retrieved={d['source'].rsplit('/',1)[-1] for d in collect(r.get('agent',{}).get('trace',[])) if isinstance(d.get('source'),str) and 'synthetic:' in d['source']}
    need=gold['turns'][tid]['minimal_sources']
    events=Counter(e.get('event') for e in r.get('agent',{}).get('trace',[]))
    body='\n'.join(a.get('statement','')+'\n'+a.get('explanation','') for a in r.get('assessments',[]))
    out.append(dict(turn=tid,runtime=t['status'],result_status=r.get('status'),agent_state=r.get('agent',{}).get('state'),observed=True,
       usage=u,usage_unknown=t.get('usage_unknown'),latency_ms=r.get('latency_ms'),
       source_fingerprint=r.get('agent',{}).get('implementation_sha256'),trace_event_counts=dict(events),
       citation_existence={'matched':sum(exact_c),'total':len(exact_c)},quote_existence={'matched':sum(exact_q),'total':len(exact_q)},
       retrieved_sources=sorted(retrieved),required_sources=need,required_source_document_coverage=all(x in retrieved for x in need) if need else 'NOT_APPLICABLE',
       explicit_scope_word_in_assessment_body=any(w in body.lower() for w in ['本地','合成','synthetic','local']),
       answer=r.get('answer','')))
checks['registered_docs_unchanged']=[]
for s in summary['scenarios']:
    before=read(ROOT/'run'/s['id']/'registered-documents.json');after=read(ROOT/'run'/s['id']/'documents-after.json')
    checks['registered_docs_unchanged'].append({'scenario':s['id'],'unchanged':before==after})
known=[x for x in out if x.get('observed')]
aggregate={'scope':'Mechanical evidence integrity audit only; not semantic correctness or independent review',
 'checks':checks,'submissions':summary['task_submission_attempts'],'runtime':dict(Counter(x['runtime'] for x in out)),
 'model_calls':sum(x['usage'].get('model_calls',0) for x in known),
 'provider_usage_unknown_tasks':sum(bool(x.get('usage_unknown') or x['usage'].get('provider_usage_unknown')) for x in known),
 'known_generation_tokens':{key:sum((x['usage'].get('generation_tokens') or {}).get(key) or 0 for x in known) for key in ['prompt_tokens','completion_tokens','total_tokens']},
 'citation_existence':{key:sum(x['citation_existence'][key] for x in known) for key in ['matched','total']},
 'quote_existence':{key:sum(x['quote_existence'][key] for x in known) for key in ['matched','total']},
 'latency_ms':{'median':statistics.median([x['latency_ms'] for x in known if x.get('latency_ms') is not None]),'max':max(x.get('latency_ms') or 0 for x in known)},
 'turns':out}
assert aggregate['submissions']<=6
assert aggregate['model_calls']<=42
save(ROOT/'grading/mechanical-audit.json',aggregate)
print(json.dumps({k:v for k,v in aggregate.items() if k!='turns'},ensure_ascii=False,indent=2))
