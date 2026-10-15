"""Stateless query previews and portable, offline-recomputable result receipts."""
from .datasets import DatasetBundle, digest
from .schemas import QueryRuns, StrictModel
from .tools import ToolBox
from .presentation import query_view
from pydantic import Field


class QueryRequest(StrictModel):
    query: QueryRuns = Field(default_factory=QueryRuns)
    dataset: DatasetBundle | None = None
    preview_id: str | None = None


def tools_for(dataset=None):
    return ToolBox(bundle=dataset.model_dump()) if dataset is not None else ToolBox()


def inspect(dataset=None):
    t=tools_for(dataset)
    from .importing import comparison_key
    return dict(snapshot_id=t.snapshot_id,run_count=len(t.runs),note_count=len(t.notes),
                comparable=len({comparison_key(r) for r in t.runs})==1,
                units=sorted({r['length_unit'] for r in t.runs}),
                synthetic=any(r['synthetic'] for r in t.runs),run_ids=[r['id'] for r in t.runs])


def conditions(query):
    p=query.model_dump()
    parts=[f"准确率 {p['min_accuracy']:.6g} ≤ accuracy ≤ {p['max_accuracy']:.6g}",f"正确题数 ≥ {p['min_correct']}"]
    if p['max_length'] is not None:parts.append(f"平均长度 < {p['max_length']:.6g}（使用数据中的长度单位）")
    for k,label in [('shorter_than_baseline','平均长度严格短于'),('more_accurate_than_baseline','准确率严格高于')]:
        if p[k]:parts.append(label+' '+p[k])
    if p['pareto_only']:parts.append('在筛选后集合中取Pareto前沿：准确率最大、长度最小')
    parts.append(p['order_by']+(' 降序' if p['descending'] else ' 升序')+'；指标相同按ID排序')
    if p['top_k']:parts.append(f"取前{p['top_k']}条"+('，保留边界全部并列' if p['include_ties'] else '，不扩展边界并列'))
    else:parts.append('保留全部结果')
    return parts


def preview(request):
    t=tools_for(request.dataset)
    # Execution validates compatibility and references before offering a plan.
    out=t.execute('query_runs',request.query.model_dump())
    return dict(preview_id='preview:'+digest({'snapshot_id':t.snapshot_id,'query':request.query.model_dump()}),
                snapshot_id=t.snapshot_id,conditions=conditions(request.query),parameters=request.query.model_dump(),
                dataset=inspect(request.dataset))


def execute(request):
    plan=preview(request)
    if request.preview_id!=plan['preview_id']:
        raise ValueError('Preview is missing or stale; preview these exact data and parameters again')
    t=tools_for(request.dataset)
    result=query_view(t.execute('query_runs',request.query.model_dump()))
    receipt=dict(format='lablens-query-receipt/v1',snapshot_id=t.snapshot_id,query=request.query.model_dump(),
                 dataset={'runs':t.runs,'notes':t.notes},result=result)
    receipt['content_sha256']=receipt_digest(receipt)
    return dict(mode='deterministic_query',model_calls=0,conditions=plan['conditions'],result=result,receipt=receipt)


def verify(receipt):
    if not isinstance(receipt,dict) or set(receipt)!={'format','snapshot_id','query','dataset','result','content_sha256'}:
        raise ValueError('Invalid receipt fields')
    if receipt['format']!='lablens-query-receipt/v1':raise ValueError('Unsupported receipt version')
    body={k:v for k,v in receipt.items() if k!='content_sha256'}
    if receipt_digest(body)!=receipt['content_sha256']:raise ValueError('Receipt content hash mismatch')
    dataset=DatasetBundle.model_validate(receipt['dataset']);query=QueryRuns.model_validate(receipt['query'])
    t=tools_for(dataset)
    if t.snapshot_id!=receipt['snapshot_id']:raise ValueError('Dataset fingerprint mismatch')
    expected=query_view(t.execute('query_runs',query.model_dump()))
    if expected!=receipt['result']:raise ValueError('Recomputed result differs from receipt')
    return dict(status='verified',snapshot_id=t.snapshot_id,row_count=len(expected['runs']),
                scope='Recomputed from enclosed data. Not a signature, provenance authentication, or natural-language intent verification.')


def receipt_digest(value):
    """JSON clients erase the distinction between 1.0 and 1; hash them identically."""
    def normalize(item):
        if isinstance(item, float) and item.is_integer():
            return int(item)
        if isinstance(item, list):
            return [normalize(v) for v in item]
        if isinstance(item, dict):
            return {k: normalize(v) for k, v in item.items()}
        return item
    return digest(normalize(value))
