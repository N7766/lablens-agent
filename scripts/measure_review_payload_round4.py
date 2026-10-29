#!/usr/bin/env python3
"""Offline payload comparison on two public exposed drafts; no provider call.

Reconstructs the first-turn review context from persisted metadata. Measures
HTTPX-encoded JSON bodies, not tokenizer tokens or inference speed.
"""
from __future__ import annotations
import argparse, hashlib, importlib.util, json, subprocess, sys, tempfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from lablens.assessment import resolve_selections, evidence_catalog
from lablens.answer_quality import make_review_contract
from lablens.conversation import ConversationBudget, ConversationContext
from lablens.rag import _generation_payload
from lablens.runtime_telemetry import request_body_bytes

def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def measure(output:Path):
    if output.exists():raise RuntimeError('Choose a new output path; old measurement must remain immutable')
    baseline_commit='dab449f90ecc7c6a92cd98698fa0f979fb686972'
    baseline_source=subprocess.check_output(['git','show',baseline_commit+':lablens/rag.py'],cwd=ROOT)
    with tempfile.TemporaryDirectory(prefix='lablens-review-baseline-') as tmp:
        source=Path(tmp)/'rag.py';source.write_bytes(baseline_source)
        (Path(tmp)/'assessment.py').write_bytes((ROOT/'lablens/assessment.py').read_bytes())
        spec=importlib.util.spec_from_file_location('lablens._round3_baseline_rag',source)
        old=importlib.util.module_from_spec(spec);spec.loader.exec_module(old)
        rows=[]
        for scene in ['cedar','harbor']:
            path=ROOT/f'docs/evaluations/conversation-round3/new-scenarios/run/{scene}/{scene}-t1/task-final.json'
            task=json.loads(path.read_text());error=task['error_result'];trace=error['agent']['trace']
            hits={}
            for event in trace:
                if event.get('event')=='tool' and event.get('name')=='search_workspace':
                    for hit in event['output']['hits']:hits[hit['chunk_id']]=hit
            hits=list(hits.values())
            action=next(event['arguments'] for event in trace if event.get('event')=='tool_started' and event.get('name')=='verify_and_submit_report')
            draft=resolve_selections(action,hits);question=task['request']['question'];metadata=error['context']
            assert metadata['input_turns']==0 and metadata['omitted_turns']==0
            context=ConversationContext(id=metadata['conversation_id'],revision=metadata['conversation_revision'],state=metadata['pinned_state'])
            review=ConversationBudget(context).review_context(question);review['scoped_review_contract']=make_review_contract(question,draft)
            config={'model':'Qwen/Qwen3-235B-A22B-Instruct-2507'}
            before=old._generation_payload(question,hits,config,draft=draft,context_data=review)
            legacy=_generation_payload(question,hits,config,draft=draft,context_data=review,compact_review=False)
            after=_generation_payload(question,hits,config,draft=draft,context_data=review,compact_review=True)
            assert before==legacy,'Offline legacy flag diverged from actual Round3 implementation'
            a=json.loads(before['messages'][1]['content']);b=json.loads(after['messages'][1]['content'])
            checks={'complete_passage_catalog_unchanged':a['passages']==b['passages']==evidence_catalog(hits),
                'evidence_metadata_unchanged':a['evidence']==b['evidence'],
                'response_schema_and_fixed_propositions_unchanged':before['response_format']==after['response_format'],
                'draft_round_trip_exact':resolve_selections(b['untrusted_draft'],hits)==draft,
                'contract_findings_and_rules_unchanged':{k:v for k,v in a['server_review_contract'].items() if k!='statements'}==b['server_review_contract'],
                'conversation_scope_unchanged':{k:v for k,v in a['untrusted_conversation_context'].items() if k!='current_question'}==b['untrusted_conversation_context'],
                'top_level_question_unchanged':a['question']==b['question']==question,
                'model_and_output_limit_unchanged':before['model']==after['model'] and before['max_tokens']==after['max_tokens']}
            assert all(checks.values()),checks
            before_bytes=request_body_bytes(before);after_bytes=request_body_bytes(after)
            rows.append({'scene':scene,'input_task_relative':str(path.relative_to(ROOT)),'input_task_sha256':sha(path),
                'before_body_bytes':before_bytes,'after_body_bytes':after_bytes,'bytes_removed':before_bytes-after_bytes,
                'reduction_percent':round((before_bytes-after_bytes)/before_bytes*100,3),'checks':checks,
                'before_payload_sha256':hashlib.sha256(json.dumps(before,sort_keys=True,ensure_ascii=False).encode()).hexdigest(),
                'after_payload_sha256':hashlib.sha256(json.dumps(after,sort_keys=True,ensure_ascii=False).encode()).hexdigest()})
    result={'scope':'Offline JSON-body representation comparison on two already exposed synthetic drafts; no inference or speed/quality claim',
        'baseline_commit':baseline_commit,'baseline_rag_sha256':hashlib.sha256(baseline_source).hexdigest(),
        'source_sha256':{str(p.relative_to(ROOT)):sha(p) for p in [Path(__file__).resolve(),ROOT/'lablens/rag.py',ROOT/'lablens/assessment.py',ROOT/'lablens/answer_quality.py',ROOT/'lablens/runtime_telemetry.py']},
        'external_model_calls':0,'context_reconstruction':'First-turn persisted state and zero historical turns; same known model identifier; body comparison not original provider wire capture','cases':rows}
    output.parent.mkdir(parents=True,exist_ok=True);output.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps([{k:r[k] for k in ['scene','before_body_bytes','after_body_bytes','reduction_percent']} for r in rows]))
if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',type=Path,required=True)
    measure(parser.parse_args().output)
