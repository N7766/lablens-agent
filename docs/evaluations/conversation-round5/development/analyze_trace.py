#!/usr/bin/env python3
"""Mechanical audit of frozen Round5 exposed-regression traces; no model calls."""
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
RAW = ROOT/'run'


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def audit():
    frozen = json.loads((ROOT/'raw-output-manifest.json').read_text())
    for relative, expected in frozen['files'].items():
        assert sha(RAW/relative) == expected, relative
    summary = json.loads((RAW/'run-summary.json').read_text())
    tasks = []
    for row in summary['turns']:
        path = RAW/row['scenario']/row['turn']/'task-final.json'
        if not path.exists():
            tasks.append(dict(turn=row['turn'], status=row['status'], task_submitted=False,
                              blocked_by=row.get('blocked_by'), requests=[], stages=[]))
            continue
        task = json.loads(path.read_text())
        result = task.get('result') or task.get('error_result') or {}
        trace = result.get('agent',{}).get('trace',[])
        ends = [event for event in trace if event['event']=='request_finished']
        stages = [event for event in trace if event['event']=='stage_finished']
        pairs = {}
        for prefix, key in [('stage','stage_id'),('request','request_id')]:
            starts = {item[key]:item for item in trace if item['event']==prefix+'_started'}
            finishes = {item[key]:item for item in trace if item['event']==prefix+'_finished'}
            pairs[prefix+'_ids_match'] = set(starts)==set(finishes)
            pairs[prefix+'_monotonic'] = all(item['elapsed_ms']>=starts[identity]['elapsed_ms'] and
                0<=item['remaining_ms']<=starts[identity]['remaining_ms']
                for identity,item in finishes.items() if identity in starts)
        usage = task.get('execution_usage') or result.get('usage') or {}
        reported = {key: sum(item['usage'][key] for item in ends
            if type(item.get('usage',{}).get(key)) is int) for key in ('prompt_tokens','completion_tokens','total_tokens')}
        unknown = sum(any(value is None for value in item.get('usage',{}).values()) for item in ends)
        model_attempts = usage.get('model_calls')
        tasks.append(dict(turn=row['turn'],status=row['status'],task_submitted=True,
            task_id=task['id'],task_file=str(path.relative_to(ROOT)),worker_ms=task.get('worker_ms'),
            agent_version=result.get('agent',{}).get('version'),
            implementation_sha256=result.get('agent',{}).get('implementation_sha256'),
            telemetry_version=result.get('agent',{}).get('telemetry_version'),
            result_status=result.get('status'),error_code=task.get('error_code'),
            model_attempts=model_attempts,request_events=len(ends),unknown_usage_requests=unknown,
            reported_generation_tokens=reported,overall_usage_unknown=bool(unknown or model_attempts!=len(ends)),
            trace_event_count=len(trace),pair_checks=pairs,requests=ends,stages=stages,
            decision_context=result.get('context',{}).get('context_budget',{}).get('calls',[]),
            model_tool_choices=[dict(step=e['step'],tools=e['tools']) for e in trace if e['event']=='model_decision'],
            read_actions=[e for e in trace if e['event']=='tool' and e.get('name')=='read_source_passages']))
    total_attempts = sum(row.get('model_attempts') or 0 for row in tasks)
    unknown_requests = sum(row.get('unknown_usage_requests',0) for row in tasks)
    return dict(scope='Mechanical audit only. Durations are client wall times; nested stages overlap and must not be added. HTTPX timeouts are network-phase inactivity settings, not hard end-to-end deadlines. Body bytes are JSON bytes, not tokens or packet sizes. Unknown usage is not zero cost. No TTFT, server inference breakdown, or causal model-speed conclusion is available.',
        raw_output_manifest_sha256=sha(ROOT/'raw-output-manifest.json'),
        submitted_tasks=sum(row['task_submitted'] for row in tasks),total_turns=len(tasks),
        completed_tasks=sum(row['status']=='SUCCEEDED' for row in tasks),failed_tasks=sum(row['status']=='FAILED' for row in tasks),
        blocked_turns=sum(row['status']=='BLOCKED' for row in tasks),
        model_attempts=total_attempts,unknown_usage_requests=unknown_requests,
        observed_generation_tokens={key:sum(row.get('reported_generation_tokens',{}).get(key,0) for row in tasks)
            for key in ('prompt_tokens','completion_tokens','total_tokens')},
        overall_usage_unknown=any(row.get('overall_usage_unknown',False) for row in tasks),
        tasks=tasks)


if __name__=='__main__':
    output=ROOT/'grading'/'runtime-audit.json'
    output.parent.mkdir(exist_ok=True)
    output.write_text(json.dumps(audit(),ensure_ascii=False,indent=2)+'\n')
    print(output)

# This audit is intentionally separate from model scoring: source membership and
# repeat-read identity do not prove final-answer entailment or correctness.
if __name__=='__main__':
    for scenario,turn in [(scenario,f'{scenario}-t{n}') for scenario in ('cedar','harbor') for n in (1,2,3)]:
        task_file=RAW/scenario/turn/'task-final.json'
        if not task_file.exists():
            continue
        task=json.loads(task_file.read_text())
        result=task.get('result') or task.get('error_result') or {}
        trace=result.get('agent',{}).get('trace',[])
        hits={hit['chunk_id']:hit for event in trace if event['event']=='tool'
              for hit in event.get('output',{}).get('hits',[])}
        checked=[]
        for identity,hit in hits.items():
            relative=hit['source'].split('/',1)[1]
            original=(ROOT/'inputs'/'sources'/scenario/relative).read_text()
            exact=original[hit['start']:hit['end']]==hit['text']
            digest=hashlib.sha256(hit['text'].encode()).hexdigest()==hit['content_sha256']
            checked.append(dict(chunk_id=identity,source=hit['source'],exact_original_offsets=exact,
                                chunk_sha256_matches=digest))
            assert exact and digest
        searches=[event for event in trace if event['event']=='tool' and event.get('name')=='search_workspace']
        reads=[event for event in trace if event['event']=='tool' and event.get('name')=='read_source_passages']
        initial=searches[0]['output']['passages'] if searches else {}
        repeated=[dict(chunk_ids=event['output']['chunk_ids'],passage_count=len(event['output']['passages']),
            every_passage_already_present_and_unchanged=all(initial.get(eid)==value
                for eid,value in event['output']['passages'].items())) for event in reads]
        artifact=dict(case=turn,task_file=str(task_file.relative_to(ROOT)),checked_chunks=checked,
            repeated_reads=repeated,all_original_text_and_hashes_match=True)
        (ROOT/'grading'/f'{turn}-retrieval-integrity.json').write_text(
            json.dumps(artifact,ensure_ascii=False,indent=2)+'\n')
