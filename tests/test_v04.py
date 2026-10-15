import copy
import json
import pytest
from lablens.agent import Agent
from lablens.tools import ToolBox
from test_agent import SequenceModel, call


def test_query_ids_bind_data_and_notes():
    t=ToolBox();bundle={'runs':copy.deepcopy(t.runs),'notes':copy.deepcopy(t.notes)}
    old=t.execute('query_runs',{})['query_id']
    bundle['runs'][0]['mean_length']+=1
    assert ToolBox(bundle=bundle).execute('query_runs',{})['query_id']!=old
    bundle['runs']=t.runs;bundle['notes'][0]['text']+=' Changed.'
    assert ToolBox(bundle=bundle).execute('query_runs',{})['query_id']!=old


def test_canonical_tables_replace_incorrect_model_claim_and_keep_raw():
    t=ToolBox();args=dict(order_by='correct',descending=False,top_k=1)
    out=t.execute('query_runs',args)
    body=dict(status='answered',claims=[dict(text='438题的09最少',source_ids=[out['query_id']])],selection=dict(query_id=out['query_id'],run_ids=out['ordered_run_ids']))
    trace=Agent(SequenceModel([call('query_runs',args),call('submit_answer',body,'c2')]),tools=t).run('最少正确题数的并列记录')
    assert trace['status']=='completed'
    assert trace['query_views'][0]['ordered_run_ids']==['run:dpo-10','run:dpo-11','run:dpo-12']
    assert all(r['correct']==437 for r in trace['query_views'][0]['runs'])
    assert '438' not in json.dumps(trace['answer'],ensure_ascii=False)
    assert '438' in json.dumps(trace['events'],ensure_ascii=False)
    assert trace['intent_validation']=='model_selected_parameters_unconfirmed'


def test_all_query_views_preserved_including_empty():
    t=ToolBox();q1=t.execute('query_runs',{'min_correct':450});q2=t.execute('query_runs',{'pareto_only':True})
    body=dict(status='answered',claims=[],selection=dict(query_id=q2['query_id'],run_ids=q2['ordered_run_ids']))
    trace=Agent(SequenceModel([call('query_runs',{'min_correct':450}),call('query_runs',{'pareto_only':True},'c2'),call('submit_answer',body,'c3')]),tools=t).run('两个查询')
    assert trace['status']=='completed'
    assert len(trace['query_views'])==2 and trace['query_views'][0]['runs']==[]
    assert trace['query_views'][1]['ordered_run_ids']==['run:dpo-best','run:dpo-02']


from lablens.workbench import QueryRequest, preview, execute, verify
from lablens.datasets import DatasetBundle, digest
from fastapi.testclient import TestClient
from lablens.api import app


def test_receipt_recomputes_without_model():
    request=QueryRequest(query={'pareto_only':True})
    request.preview_id=preview(request)['preview_id']
    out=execute(request)
    assert out['model_calls']==0
    assert verify(out['receipt'])['row_count']==2


@pytest.mark.parametrize('change',['parameters','data','notes'])
def test_preview_invalidates_when_inputs_change(change):
    t=ToolBox();request=QueryRequest(dataset=DatasetBundle(runs=t.runs,notes=t.notes))
    request.preview_id=preview(request)['preview_id']
    if change=='parameters':request.query.min_correct=440
    elif change=='data':request.dataset.runs[0].mean_length+=1
    else:request.dataset.notes[0].text+=' Changed.'
    with pytest.raises(ValueError,match='stale'):execute(request)


def test_receipt_detects_tampering_even_if_hash_is_recomputed():
    request=QueryRequest();request.preview_id=preview(request)['preview_id'];r=execute(request)['receipt']
    r['result']['runs'][0]['correct']=-1
    with pytest.raises(ValueError,match='hash'):verify(r)
    r['content_sha256']=digest({k:v for k,v in r.items() if k!='content_sha256'})
    with pytest.raises(ValueError,match='Recomputed'):verify(r)


def test_custom_bundle_is_request_local_and_preserves_units():
    rows=json.load(open('evaluation/fixtures/synthetic-runs.json'))
    for row in rows:row.update(length_unit='tokenizer_tokens',tokenizer='synthetic-tokenizer')
    client=TestClient(app);before=client.get('/api/dataset').json()
    payload={'dataset':{'runs':rows,'notes':[]},'query':{'order_by':'mean_length','descending':False}}
    p=client.post('/api/query/preview',json=payload);assert p.status_code==200
    payload['preview_id']=p.json()['preview_id']
    result=client.post('/api/query/execute',json=payload).json()
    assert result['model_calls']==0 and all(r['synthetic'] for r in result['result']['runs'])
    assert result['result']['runs'][0]['length_unit']=='tokenizer_tokens'
    assert client.get('/api/dataset').json()==before
    assert client.post('/api/receipt/verify',json=result['receipt']).json()['status']=='verified'


def test_invalid_bundles_and_receipts_return_422():
    client=TestClient(app);rows=ToolBox().runs
    assert client.post('/api/dataset/inspect',json={'runs':[rows[0],rows[0]]}).status_code==422
    assert client.post('/api/query/execute',json={'query':{}}).status_code==422
    assert client.post('/api/receipt/verify',json={'format':'bogus'}).status_code==422
    assert client.post('/api/query/preview',json={'query':{'top_k':True}}).status_code==422


def test_cli_query_verify_and_bad_receipt(tmp_path):
    import subprocess,sys
    q=tmp_path/'query.json';q.write_text('{"pareto_only":true}')
    out=tmp_path/'result.json'
    cmd=[sys.executable,'-m','lablens.cli']
    subprocess.run(cmd+['query','--query-file',str(q),'--output',str(out)],check=True,capture_output=True)
    checked=subprocess.run(cmd+['verify',str(out)],check=True,capture_output=True,text=True)
    assert json.loads(checked.stdout)['status']=='verified'
    out.write_text('{}')
    assert subprocess.run(cmd+['verify',str(out)],capture_output=True).returncode==2
