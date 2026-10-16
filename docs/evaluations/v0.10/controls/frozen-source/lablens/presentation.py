"""Render exact query results without asking the model to rewrite membership or metrics."""
from .policy import BOUNDARY
from copy import deepcopy


def query_view(output):
    return deepcopy({k: output[k] for k in ('query_id', 'snapshot_id', 'parameters', 'matched_before_top_k', 'ordered_run_ids', 'runs')})


def render_queries(outputs):
    queries = {o['query_id']: o for o in outputs if 'query_id' in o}
    notes = {n['id']: n for o in outputs for n in o.get('notes', []) + o.get('limitations', []) if isinstance(n, dict)}
    last = list(queries.values())[-1]
    # All query rows remain in query_views, including empty results and multiple queries.
    answer = dict(status='answered', claims=[dict(text='查询结果由代码生成。请核对每张表上方的实际条件；完整记录及指标见表格。', source_ids=[last['query_id']], measurements=[])],
                  limitations=[n['text'] for n in notes.values()] or [BOUNDARY],
                  limitation_ids=list(notes), selection=dict(query_id=last['query_id'], run_ids=last['ordered_run_ids']))
    return answer, [query_view(o) for o in queries.values()]


def comparison_view(output):
    return deepcopy({k: output[k] for k in (
        'baseline', 'candidate', 'accuracy_delta_percentage_points',
        'length_reduction_percent', 'additional_correct', 'limitations', 'source_ids')})


def coverage_view(output):
    return deepcopy({k: output[k] for k in ('fields', 'missing_fields', 'scope', 'notes', 'source_ids')})


def evidence_views(outputs):
    """Only computed values and verbatim evidence; no model-authored interpretation."""
    views = []
    for out in outputs:
        if 'baseline' in out and 'candidate' in out:
            view = dict(kind='compare', result=comparison_view(out))
        elif 'missing_fields' in out:
            view = dict(kind='describe', result=coverage_view(out))
        elif 'query_id' not in out and out.get('notes'):
            view = dict(kind='notes', result=deepcopy({'notes': out['notes'], 'source_ids': out['source_ids']}))
        else:
            continue
        if view not in views:
            views.append(view)
    return views


def render_analysis(outputs):
    answer, queries = render_queries(outputs)
    extra = evidence_views(outputs)
    if extra:
        answer['claims'][0]['text'] = '查询、比较及已读说明由工具证据生成。请核对实际条件、比较方向及资料范围；列出的证据不保证问题中的全部要求已被覆盖。'
    return answer, queries, extra
