"""Render exact query results without asking the model to rewrite membership or metrics."""
from .policy import BOUNDARY


def query_view(output):
    return {k: output[k] for k in ('query_id', 'snapshot_id', 'parameters', 'matched_before_top_k', 'ordered_run_ids', 'runs')}


def render_queries(outputs):
    queries = {o['query_id']: o for o in outputs if 'query_id' in o}
    notes = {n['id']: n for o in outputs for n in o.get('notes', []) + o.get('limitations', []) if isinstance(n, dict)}
    last = list(queries.values())[-1]
    # All query rows remain in query_views, including empty results and multiple queries.
    answer = dict(status='answered', claims=[dict(text='查询结果由代码生成。请核对每张表上方的实际条件；完整记录及指标见表格。', source_ids=[last['query_id']], measurements=[])],
                  limitations=[n['text'] for n in notes.values()] or [BOUNDARY],
                  limitation_ids=list(notes), selection=dict(query_id=last['query_id'], run_ids=last['ordered_run_ids']))
    return answer, [query_view(o) for o in queries.values()]
