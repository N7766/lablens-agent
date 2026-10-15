import pytest
from lablens.agent import Agent
from lablens.tools import ToolBox
from test_agent import SequenceModel, call


def test_mixed_agent_keeps_comparison_and_missing_evidence():
    tools = ToolBox()
    out = tools.execute('query_runs', {'top_k': 1})
    answer = dict(status='answered', claims=[], selection=dict(query_id=out['query_id'], run_ids=out['ordered_run_ids']))
    trace = Agent(SequenceModel([
        call('query_runs', {'top_k': 1}),
        call('compare_runs', {'baseline_id': 'run:sft', 'candidate_id': 'run:dpo-best'}, 'c2'),
        call('describe_data', {}, 'c3'), call('submit_answer', answer, 'c4')
    ])).run('查询最高准确率、比较SFT与最优DPO并说明数据边界')
    assert trace['status'] == 'completed'
    assert [v['kind'] for v in trace['evidence_views']] == ['compare', 'describe']
    comparison, coverage = [v['result'] for v in trace['evidence_views']]
    assert comparison['additional_correct'] == 3
    assert comparison['accuracy_delta_percentage_points'] == pytest.approx(100 * 3 / 462)
    assert 'paired_predictions' in coverage['missing_fields']
    assert 'note:significance' in coverage['source_ids']

