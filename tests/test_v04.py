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
