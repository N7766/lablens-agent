import asyncio
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
import httpx
import pytest

SPEC=importlib.util.spec_from_file_location('history_scale',Path(__file__).parents[1]/'scripts/evaluate_history_scale.py')
scale=importlib.util.module_from_spec(SPEC);SPEC.loader.exec_module(scale)

def test_seed_requires_an_empty_isolated_schema(monkeypatch,tmp_path):
    calls=[]
    def sql(args,query):calls.append(query);return '1\n0\n'
    monkeypatch.setattr(scale,'mysql',sql)
    with pytest.raises(AssertionError,match='nonempty'):
        scale.seed(SimpleNamespace(size=1000,database=scale.db_name(1000),output=tmp_path))
    assert len(calls)==1 and calls[0].startswith('SELECT')
    with pytest.raises(AssertionError,match='dedicated'):
        scale.seed(SimpleNamespace(size=1000,database='main',output=tmp_path))
    assert len(calls)==1

def test_seed_boundaries_keep_timestamp_ties_and_tenant_skew():
    assert len(scale.hot_ids(1000))==900
    assert len(set(scale.hot_ids(1000)))==900
    assert scale.timestamp(8)==scale.timestamp(15)
    assert scale.hot_ids(1000)==sorted(scale.hot_ids(1000),reverse=True)
    assert len({scale.workspace_index(i) for i in range(1,1001)})==4

@pytest.mark.parametrize('wrong_ids',[False,True])
def test_measure_reconciles_every_response_and_keeps_bad_results(monkeypatch,tmp_path,wrong_ids):
    ids=scale.hot_ids(1000);original=httpx.AsyncClient
    def handler(request):
        if request.url.path.endswith('/tasks'):
            selected=ids[1:101] if wrong_ids else ids[:100]
            body={'tasks':[{'id':i} for i in selected],'has_more':True}
        elif '/tasks/' in request.url.path:body={'id':ids[0],'result':{'answer':scale.RESULT_PADDING}}
        elif request.url.path.endswith('/status'):body={'database':'available','task_counts':{'SUCCEEDED':1000}}
        else:body={'measurements':[]}
        return httpx.Response(200,json=body)
    monkeypatch.setattr(scale.httpx,'AsyncClient',lambda **kw:original(**kw,transport=httpx.MockTransport(handler)))
    asyncio.run(scale.measure(SimpleNamespace(size=1000,endpoint='http://127.0.0.1:8086',output=tmp_path,variant='candidate',view='summary')))
    result=json.loads((tmp_path/'summary.json').read_text());rows=[json.loads(x) for x in (tmp_path/'requests.jsonl').read_text().splitlines()]
    assert result['passed'] is (not wrong_ids)
    assert len(rows)==result['requests']==155
    assert sum(x['phase']=='concurrent' for x in rows)==80
    assert all(x['valid'] for x in rows if x['scenario']!='list')
    assert result['groups']['list:sequential']['valid']==(0 if wrong_ids else 20)

def test_measure_refuses_nonlocal_endpoints(tmp_path):
    with pytest.raises(ValueError,match='loopback'):
        asyncio.run(scale.measure(SimpleNamespace(size=1000,endpoint='https://external.example',output=tmp_path)))

@pytest.mark.parametrize('failure,error_type',[
    ('timeout','ReadTimeout'),('non_json','JSONDecodeError'),('http_error','HttpStatusError')])
def test_metrics_failure_preserves_completed_workload_and_attempts_later_metrics(monkeypatch,tmp_path,failure,error_type):
    ids=scale.hot_ids(1000);original=httpx.AsyncClient;metrics_seen=[]
    def handler(request):
        if request.url.path.endswith('/tasks'):
            body={'tasks':[{'id':i} for i in ids[:100]],'has_more':True}
        elif '/tasks/' in request.url.path:body={'id':ids[0],'result':{'answer':scale.RESULT_PADDING}}
        elif request.url.path.endswith('/status'):body={'database':'available','task_counts':{'SUCCEEDED':1000}}
        else:
            # Assert durable workload evidence exists before the first diagnostic
            # begins, not only after the exception has been caught.
            assert len((tmp_path/'requests.jsonl').read_text().splitlines())==155
            assert json.loads((tmp_path/'summary.json').read_text())['workload_passed'] is True
            metrics_seen.append(request.url.path.rsplit('/',1)[-1])
            if len(metrics_seen)==1:
                if failure=='timeout':raise httpx.ReadTimeout('synthetic diagnostic timeout',request=request)
                if failure=='non_json':return httpx.Response(200,text='synthetic invalid metrics payload')
                return httpx.Response(503,json={'error':'synthetic_unavailable'})
            body={'measurements':[]}
        return httpx.Response(200,json=body)
    monkeypatch.setattr(scale.httpx,'AsyncClient',lambda **kw:original(**kw,transport=httpx.MockTransport(handler)))
    asyncio.run(scale.measure(SimpleNamespace(size=1000,endpoint='http://127.0.0.1:8086',output=tmp_path,variant='candidate',view='summary')))
    summary=json.loads((tmp_path/'summary.json').read_text())
    rows=[json.loads(x) for x in (tmp_path/'requests.jsonl').read_text().splitlines()]
    metrics=json.loads((tmp_path/'resources-after.json').read_text())
    assert len(rows)==155 and all(x['valid'] for x in rows)
    assert len(metrics_seen)==len(metrics)==5 and len(set(metrics_seen))==5
    assert summary['workload_passed'] is True and summary['passed'] is False
    assert summary['diagnostics_complete'] is False and summary['diagnostics_status']=='failed'
    assert summary['diagnostics_errors']==[{'metric':metrics_seen[0],'error_type':error_type}]
    assert metrics[metrics_seen[0]]['error_type']==error_type
    assert all(metrics[name]['valid'] for name in metrics_seen[1:])
