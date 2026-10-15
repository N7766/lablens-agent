import copy
import json
from lablens.policy import BOUNDARY
from pathlib import Path
import pytest
from lablens.schemas import Answer
from lablens.evidence import validate_answer
from lablens.importing import validate_runs
from lablens.tools import ToolBox


def fixture_answer(**overrides):
    measurement = dict(metric='accuracy_delta_percentage_points', baseline_id='run:sft', candidate_id='run:dpo-best', value=0.65, unit='percentage_points', direction='candidate_minus_baseline')
    measurement.update(overrides)
    return Answer.model_validate(dict(status='answered', claims=[dict(text='准确率增加约 0.65 个百分点', source_ids=['run:sft', 'run:dpo-best'], measurements=[measurement])], limitations=[BOUNDARY]))


def evidence():
    return [ToolBox().execute('compare_runs', dict(baseline_id='run:sft', candidate_id='run:dpo-best'))]


def test_rounding_is_accepted():
    validate_answer(fixture_answer(), evidence())


@pytest.mark.parametrize('overrides', [dict(value=6.5), dict(unit='percent'), dict(baseline_id='run:dpo-best',candidate_id='run:sft'), dict(direction='absolute'), dict(value=float('nan')), dict(value=float('inf'))])
def test_invalid_numeric_evidence(overrides):
    with pytest.raises(ValueError):
        validate_answer(fixture_answer(**overrides), evidence())


def test_unread_evidence():
    with pytest.raises(ValueError, match='unread'):
        validate_answer(fixture_answer(), [])


def test_numerical_text_cannot_skip_structured_fields():
    answer=fixture_answer(); answer.claims[0].measurements=[]
    with pytest.raises(ValueError, match='measurements'):
        validate_answer(answer, evidence())


def test_correct_count_is_exact():
    with pytest.raises(ValueError, match='observed value'):
        validate_answer(fixture_answer(metric='correct',candidate_id=None,value=439.001,unit='count',direction='absolute'), evidence())


def test_length_unit_is_not_tokens():
    with pytest.raises(ValueError, match='unit=whitespace_words'):
        validate_answer(fixture_answer(metric='mean_length',candidate_id=None,value=32.42,unit='tokenizer_tokens',direction='absolute'), evidence())


def raw():
    return json.loads(Path('evaluation/fixtures/synthetic-runs.json').read_text())


@pytest.mark.parametrize('field,value', [('accuracy',float('nan')),('mean_length',float('inf')),('correct',11),('accuracy',0.9),('source_sha256','bad'),('num_samples',0),('model_config_details',{}),('source_path','../private'),('length_unit','tokens')])
def test_bad_import(field,value):
    rows=raw();rows[0][field]=value
    with pytest.raises(ValueError): validate_runs(rows)


def test_missing_units_and_duplicates():
    rows=raw();del rows[0]['length_unit']
    with pytest.raises(ValueError): validate_runs(rows)
    rows=raw();rows[1]['id']=rows[0]['id']
    with pytest.raises(ValueError,match='Duplicate'): validate_runs(rows)


@pytest.mark.parametrize('field,value', [('split_id','other'),('dataset','other'),('project','other'),('length_unit','tokenizer_tokens'),('synthetic',True),('tokenizer','other')])
def test_incompatible_comparisons(field,value):
    tools=ToolBox();tools.index['run:sft'][field]=value
    with pytest.raises(ValueError,match='Incompatible'): tools.execute('compare_runs',dict(baseline_id='run:sft',candidate_id='run:dpo-best'))


def test_import_can_be_used_by_tools(tmp_path):
    (tmp_path/'runs.json').write_text(json.dumps(validate_runs(raw())))
    (tmp_path/'notes.json').write_text('[]')
    tools=ToolBox(tmp_path)
    assert len(tools.execute('list_runs',{})['runs'])==2
    assert tools.execute('compare_runs',dict(baseline_id='run:synthetic-0',candidate_id='run:synthetic-1'))['additional_correct']==1


def test_synthetic_source_hashes_match():
    import hashlib
    for r in raw():
        assert hashlib.sha256((Path('evaluation/fixtures')/r['source_path']).read_bytes()).hexdigest()==r['source_sha256']


def test_numeric_repair_runs_through_agent():
    from lablens.agent import Agent
    from test_agent import SequenceModel, call
    bad=fixture_answer(value=6.5);good=fixture_answer()
    messages=[call('compare_runs',dict(baseline_id='run:sft',candidate_id='run:dpo-best')), {'content':bad.model_dump_json()}, {'content':good.model_dump_json()}]
    result=Agent(SequenceModel(messages)).run('compare')
    assert result['status']=='completed'
    assert result['model_calls']==3
    assert any(e['type']=='validation_error' and 'observed value' in e['error'] for e in result['events'])


def test_repeated_invalid_output_stops_before_budget():
    from lablens.agent import Agent
    from test_agent import SequenceModel
    model=SequenceModel([{'content':'invalid'}]*8)
    result=Agent(model).run('q')
    assert result['status']=='validation_stalled'
    assert result['model_calls']==2


def test_tokenizer_import_retains_explicit_unit_and_identity(tmp_path):
    rows=raw()
    for row in rows: row.update(length_unit='tokenizer_tokens',tokenizer='synthetic/tokenizer-v1')
    (tmp_path/'runs.json').write_text(json.dumps(validate_runs(rows)))
    (tmp_path/'notes.json').write_text('[]')
    tools=ToolBox(tmp_path)
    result=tools.execute('compare_runs',dict(baseline_id='run:synthetic-0',candidate_id='run:synthetic-1'))
    assert result['baseline']['length_unit']=='tokenizer_tokens'
    assert result['candidate']['tokenizer']=='synthetic/tokenizer-v1'
    assert 'length_unit' in next(s['function']['description'] for s in tools.schemas() if s['function']['name']=='list_runs')
