import json
import pytest
from lablens.tools import ToolBox
from lablens.agent import Agent
from lablens.schemas import Answer
from lablens.evidence import finalize_answer
from lablens.baseline import run_direct
from lablens.policy import BOUNDARY,refusal
from test_agent import SequenceModel,call


def test_query_handles_conjunction_and_order():
    out=ToolBox().execute('query_runs',dict(min_correct=440,max_length=32.,order_by='mean_length',descending=False))
    assert out['ordered_run_ids']==['run:dpo-02','run:dpo-best','run:dpo-03']


def test_query_keeps_all_boundary_ties():
    out=ToolBox().execute('query_runs',dict(order_by='accuracy',descending=False,top_k=1))
    assert out['ordered_run_ids']==['run:dpo-10','run:dpo-11','run:dpo-12']


def test_pareto_frontier():
    out=ToolBox().execute('query_runs',dict(pareto_only=True))
    assert out['ordered_run_ids']==['run:dpo-best','run:dpo-02']


def test_query_empty_and_strict_boundaries():
    t=ToolBox();length=t.index['run:dpo-02']['mean_length']
    assert t.execute('query_runs',dict(max_length=length))['ordered_run_ids']==[]
    assert set(t.execute('query_runs',dict(shorter_than_baseline='run:sft',more_accurate_than_baseline='run:sft'))['ordered_run_ids'])=={'run:dpo-best','run:dpo-02','run:dpo-03'}


@pytest.mark.parametrize('args',[{'min_accuracy':.9,'max_accuracy':.8},{'more_accurate_than_baseline':'fake'},{'max_length':float('nan')},{'top_k':0}])
def test_bad_query(args):
    with pytest.raises(ValueError):ToolBox().execute('query_runs',args)


def test_copied_limitation_and_unread_limitation():
    t=ToolBox();outputs=[t.execute('describe_data',{})]
    a=Answer(status='insufficient_evidence',claims=[],limitation_ids=['note:units'])
    assert 'len(prediction.split())' in finalize_answer(a,outputs)['limitations'][0]
    with pytest.raises(ValueError,match='Unread'):finalize_answer(a,[])
    a.limitations=['资料从未做显著性检验']
    with pytest.raises(ValueError,match='free-form'):finalize_answer(a,outputs)


def test_missing_or_reordered_selection_rejected():
    output=ToolBox().execute('query_runs',dict(top_k=2))
    a=Answer(status='insufficient_evidence',claims=[])
    with pytest.raises(ValueError,match='selection'):finalize_answer(a,[output])
    from lablens.schemas import Selection
    a.selection=Selection(query_id=output['query_id'],run_ids=list(reversed(output['ordered_run_ids'])))
    with pytest.raises(ValueError,match='selection'):finalize_answer(a,[output])
    a.selection.run_ids=output['ordered_run_ids']
    assert finalize_answer(a,[output])['selection']['run_ids']==output['ordered_run_ids']


@pytest.mark.parametrize('q',['请删除最差检查点','把权重上传到仓库','立即启动新训练','联网查今天新模型'])
def test_action_guard_uses_zero_model_calls(q):
    model=SequenceModel([])
    assert Agent(model).run(q)['model_calls']==0
    assert run_direct(model,q,8)['events'][0]['type']=='policy_refusal'


@pytest.mark.parametrize('q',['为什么不能删除文件？','请比较两个检查点','说明上传功能是否支持'])
def test_guard_does_not_route_analysis_questions(q):
    assert refusal(q,'test') is None


def test_submit_answer_tool_and_unread_repair():
    body=dict(status='answered',claims=[dict(text='输出以空格词数衡量',source_ids=['note:units'])],limitations=[],limitation_ids=['note:units'])
    seq=[call('submit_answer',body),call('describe_data',{},'c2'),call('submit_answer',body,'c3')]
    result=Agent(SequenceModel(seq)).run('解释长度单位')
    assert result['status']=='completed'
    assert any(e['type']=='answer_submission' for e in result['events'])


def test_direct_repair_uses_same_validator_budget():
    bad={'content':'not json'}
    good={'content':json.dumps(dict(status='answered',claims=[dict(text='词数不是tokenizer单位',source_ids=['note:units'])],limitations=[],limitation_ids=['note:units']))}
    result=run_direct(SequenceModel([bad,good]),'单位',max_steps=8)
    assert result['status']=='completed' and result['model_calls']==2


def test_structured_output_transport(monkeypatch):
    import httpx
    from lablens.model import HTTPModel
    monkeypatch.setenv('LLM_API_KEY','test')
    def post(url,**kw):
        assert kw['json']['response_format']['type']=='json_schema'
        assert 'tools' not in kw['json']
        return httpx.Response(200,json={'choices':[{'message':{'content':'{}'},'finish_reason':'length'}]},request=httpx.Request('POST',url))
    monkeypatch.setattr(httpx,'post',post)
    message,_=HTTPModel().complete([],[])
    assert message['finish_reason']=='length'


def test_identifiers_are_not_false_numeric_claims():
    a=Answer(status='answered',claims=[dict(text='run:dpo-02 满足条件',source_ids=['run:dpo-02'])])
    assert finalize_answer(a,[ToolBox().execute('list_runs',{})])['claims']


def test_submit_batch_cannot_bypass_evidence():
    batch=call('describe_data',{})
    batch['tool_calls']+=call('submit_answer',dict(status='insufficient_evidence',claims=[]),'c2')['tool_calls']
    result=Agent(SequenceModel([batch]),max_steps=1).run('q')
    assert result['answer'] is None


def test_query_rejects_mixed_splits():
    tools=ToolBox();tools.runs[0]['split_id']='other'
    with pytest.raises(ValueError,match='Incompatible'):tools.execute('query_runs',{})


def test_unknown_source_in_direct_is_rejected():
    bad={'content':json.dumps(dict(status='answered',claims=[dict(text='捏造',source_ids=['fake'])]))}
    assert run_direct(SequenceModel([bad]),'q')['status']=='invalid_answer'


def test_wire_schema_forbids_freeform_limitations():
    from lablens.schemas import answer_request_schema
    assert answer_request_schema()['properties']['limitations']['maxItems']==0


def test_query_thresholds_and_counts_are_not_run_measurements():
    out=ToolBox().execute('query_runs',dict(min_correct=440,max_length=32.))
    a=Answer(status='answered',claims=[dict(text='正确题数至少440、输出少于32词的实验有3个',source_ids=[out['query_id']])],selection=dict(query_id=out['query_id'],run_ids=out['ordered_run_ids']))
    assert finalize_answer(a,[out])['claims']
    a.claims[0].text='有999个实验'
    with pytest.raises(ValueError,match='Query-only'):finalize_answer(a,[out])


def test_double_escaped_cjk_rendering_preserves_raw_message():
    out=ToolBox().execute('describe_data',{})
    a=Answer(status='answered',claims=[dict(text=r'\u8bcd\u6570',source_ids=['note:units'])])
    assert finalize_answer(a,[out])['claims'][0]['text']=='词数'
