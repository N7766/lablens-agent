"""Portable, deterministic reports from verified enclosed data; never read source labels."""
import html
import json
import re

from .datasets import DatasetBundle
from .planning import PlanRequest, prepare
from .presentation import query_view
from .schemas import QueryRuns
from .workbench import conditions, tools_for, verify

REPORT_VERSION = '0.11.0'


def _text(value):
    if isinstance(value, bool):
        return '是' if value else '否'
    if value is None:
        return '未记录'
    return str(value)


def _json(value):
    return json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)


def _fence(text):
    return '`' * max(3, 1 + max((len(m[0]) for m in re.finditer(r'`+', text)), default=0))


def _inline(value, *, table=False):
    # A code span disables Markdown links, autolinks and embedded HTML. Preserve
    # line breaks as visible escapes so a cell cannot create a new table/block.
    text = _text(value).replace('\r', '\\r').replace('\n', '\\n')
    if table:
        text = text.replace('|', '\\|')
    fence = '`' * max(1, 1 + max((len(m[0]) for m in re.finditer(r'`+', text)), default=0))
    return fence + ' ' + text + ' ' + fence


class _Document:
    def __init__(self, format):
        self.format = format
        self.parts = []

    def heading(self, title, level=2):
        if self.format == 'html':
            self.parts.append(f'<h{level}>{html.escape(title)}</h{level}>')
        else:
            self.parts.append('#' * level + ' ' + _inline(title) + '\n')

    def paragraph(self, text, *, trusted=False):
        if self.format == 'html':
            self.parts.append('<p>' + html.escape(text) + '</p>')
        else:
            if trusted:
                self.parts.append(text + '\n')
            else:
                fence = _fence(text)
                self.parts.append(fence + 'text\n' + text + '\n' + fence + '\n')

    def table(self, headings, rows):
        if self.format == 'html':
            header = ''.join('<th scope="col">' + html.escape(h) + '</th>' for h in headings)
            body = ''.join('<tr>' + ''.join('<td>' + html.escape(_text(v)) + '</td>' for v in row) + '</tr>' for row in rows)
            self.parts.append('<div class="table-scroll"><table><thead><tr>' + header + '</tr></thead><tbody>' + body + '</tbody></table></div>')
        else:
            self.parts.append('| ' + ' | '.join(headings) + ' |\n| ' + ' | '.join('---' for _ in headings) + ' |\n' + '\n'.join('| ' + ' | '.join(_inline(v, table=True) for v in row) + ' |' for row in rows) + '\n')

    def code(self, value, *, title=None):
        text = _json(value)
        if self.format == 'html':
            content = '<pre><code>' + html.escape(text) + '</code></pre>'
            if title:
                content = '<details><summary>' + html.escape(title) + '</summary>' + content + '</details>'
            self.parts.append(content)
        else:
            if title:
                self.heading(title, 3)
            fence = _fence(text)
            self.parts.append(fence + 'json\n' + text + '\n' + fence + '\n')

    def finish(self):
        body = '\n'.join(self.parts)
        if self.format == 'markdown':
            return body + '\n'
        return '''<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'">
<title>LabLens 证据报告</title><style>
:root{color-scheme:light}*{box-sizing:border-box}body{margin:0;background:#f2f5f7;color:#172b3a;font:15px/1.65 system-ui,-apple-system,"PingFang SC",sans-serif}main{max-width:1200px;margin:32px auto;padding:40px;background:white;border-top:6px solid #11756c}h1{font-size:30px;margin-top:0}h2{margin-top:36px;padding-bottom:8px;border-bottom:1px solid #ccd9df}h3{margin-top:24px}p{white-space:pre-wrap;overflow-wrap:anywhere}.table-scroll{overflow-x:auto;margin:16px 0}table{width:100%;border-collapse:collapse;font-size:13px}th,td{padding:9px 11px;border:1px solid #d9e2e7;text-align:left;vertical-align:top;white-space:pre-wrap;overflow-wrap:anywhere}th{background:#eaf2f1;font-weight:650}tr:nth-child(even) td{background:#f8fafb}pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#f3f6f8;padding:16px;border:1px solid #d9e2e7;font-size:12px}summary{cursor:pointer;font-weight:600}details{margin:16px 0}code{font-family:ui-monospace,SFMono-Regular,monospace}footer{margin-top:36px;border-top:1px solid #ccd9df;padding-top:12px;color:#536874}@media(max-width:640px){main{margin:0;padding:20px}h1{font-size:24px}}@media print{body{background:white;font-size:10pt}main{max-width:none;margin:0;padding:0;border-top:3px solid #11756c}.table-scroll{overflow:visible}table{font-size:8pt;table-layout:fixed}thead{display:table-header-group}tr{break-inside:avoid}h2,h3{break-after:avoid}pre{font-size:8pt}details:not([open]) pre{display:block}summary{list-style:none}a{color:inherit}}
</style></head><body><main>''' + body + '<footer>LabLens · 静态证据报告 · v' + REPORT_VERSION + '</footer></main></body></html>\n'


def _run_table(doc, rows):
    if not rows:
        doc.paragraph('结果为空；没有符合这些条件的记录。', trusted=True)
    doc.table(['序号', '记录 ID', '标签', '准确率（fraction）', '正确题数（count）', '样本数（count）', '平均长度', '长度单位', '合成数据'],
              [[i, r['id'], r['label'], r['accuracy'], r['correct'], r['num_samples'], r['mean_length'], r['length_unit'], r['synthetic']] for i, r in enumerate(rows, 1)])


def _notes(doc, notes):
    if not notes:
        doc.paragraph('快照没有包含资料说明。', trusted=True)
    for note in notes:
        doc.heading(note['id'] + ' · ' + note['title'], 3)
        doc.paragraph(note['text'])
        doc.table(['资料 ID', '来源文本（仅标签，未打开或认证）'], [[note['id'], note['source'] or '未记录']])


def _result(doc, item, step=None):
    result = item['result']
    if step:
        doc.heading('步骤 ' + step.id + ' · ' + step.kind)
        doc.paragraph('请求原文：' + step.request_excerpt)
    if item['kind'] == 'query':
        doc.heading('查询条件与参数', 3)
        for condition in conditions(QueryRuns.model_validate(result['parameters'])):
            doc.paragraph(condition)
        doc.code(result['parameters'], title='完整查询参数')
        doc.table(['查询 ID', '截断前匹配记录数', '最终记录数'], [[result['query_id'], result['matched_before_top_k'], len(result['runs'])]])
        doc.paragraph('下表保留全部结果及执行顺序；平均长度使用每行标注的单位。', trusted=True)
        _run_table(doc, result['runs'])
    elif item['kind'] == 'compare':
        doc.paragraph('比较方向：' + result['baseline']['id'] + ' → ' + result['candidate']['id'])
        _run_table(doc, [result['baseline'], result['candidate']])
        doc.table(['指标', '计算值', '单位', '方向 / 公式'], [
            ['准确率差', result['accuracy_delta_percentage_points'], 'percentage_points', '候选 − 基线'],
            ['正确题数差', result['additional_correct'], 'count', '候选 − 基线'],
            ['平均长度减少比例', result['length_reduction_percent'], 'percent', '(基线 − 候选) / 基线 × 100'],
        ])
        doc.heading('比较所附的资料说明', 3)
        _notes(doc, result['limitations'])
        doc.code(result['source_ids'], title='比较来源 ID')
    else:
        doc.heading('资料范围', 3)
        doc.paragraph(result['scope'])
        doc.table(['已有字段', '缺失字段'], [[', '.join(result['fields']), ', '.join(result['missing_fields'])]])
        _notes(doc, result['notes'])
        doc.code(result['source_ids'], title='资料来源 ID')


def _checks(doc, preview, request):
    doc.heading('按 v0.11.0 规则重新核对')
    doc.paragraph('以下结果在本次导出时重新计算，不是执行当时的检查记录。只检查已支持的明确句式和原文映射；未识别表达仍须人工核对，不能据此证明自然语言意图正确或需求完整。', trusted=True)
    doc.heading('未处理要求', 3)
    doc.table(['原文', '原因'], [[u.request_excerpt, u.reason] for u in request.plan.unhandled])
    if not request.plan.unhandled:
        doc.paragraph('计划没有声明未处理项；这不证明全部要求已覆盖。', trusted=True)
    mapping = preview['coverage']
    doc.heading('原文逐句映射', 3)
    doc.table(['原文', '映射状态', '步骤', '未处理项序号（从 0 开始）', '仅部分引用的步骤'],
              [[c['text'], c['state'], ', '.join(c['step_ids']), ', '.join(map(str, c['unhandled_indexes'])), ', '.join(c['partial_quote_step_ids'])] for c in mapping['clauses']])
    for key, title in [('request_checks', '查询规则'), ('task_checks', '比较与资料规则')]:
        check = preview[key]
        doc.heading(title, 3)
        doc.table(['当前状态', '存在冲突', '额外 / 未关联步骤'], [[check['status'], check['has_conflicts'], ', '.join(check['unexpected_step_ids'])]])
        for unit in check['units']:
            doc.table(['要求原文', '状态', '关联步骤', '差异 / 冲突'], [[unit['text'], unit['state'], ', '.join(unit['step_ids']), '\n'.join(unit['differences']) or '无']])
            doc.code({'expected': unit['expected'], 'actual': unit['actual']}, title='预期与实际参数')
        doc.table(['未按本组规则核对的原文', '原因'], [[u['text'], u['reason']] for u in check['unassessed']])
        doc.paragraph(check['scope'])
    doc.code({key: preview[key] for key in ('coverage', 'request_checks', 'task_checks')}, title='本次完整规则复核记录')


def export_report(receipt: dict, format: str = 'html') -> dict:
    """Verify, recompute and render. No network, model client, or source-file reads."""
    if format not in {'html', 'markdown'}:
        raise ValueError('Report format must be html or markdown')
    verification = verify(receipt)
    dataset = DatasetBundle.model_validate(receipt['dataset'])
    tools = tools_for(dataset)
    doc = _Document(format)
    doc.heading('LabLens 证据报告', 1)
    doc.paragraph('封装数据已重算通过。本报告只说明收据内列出的查询或计划步骤；不证明来源真实、意图正确或需求完整。静态报告可被编辑，请保留原始收据并重新验证。', trusted=True)
    if format == 'html':
        doc.parts.append('<details><summary>文件与复核信息</summary>')
    doc.table(['项目', '值'], [
        ['报告版本', REPORT_VERSION], ['收据格式', receipt['format']], ['重算结果', verification['status']],
        ['快照 SHA-256 标识', tools.snapshot_id], ['收据内容 SHA-256', receipt['content_sha256']],
        ['本次导出的模型调用次数', 0], ['包含合成数据', any(r['synthetic'] for r in tools.runs)],
    ])
    doc.paragraph('本次导出使用封装数据进行确定性重算，模型调用为 0。收据不记录完整的历史调用与费用，因此本报告不推断过去的模型调用次数或费用。source_path、source 和 source_sha256 仅作为原有来源文本展示，未读取对应文件、访问链接或认证来源。', trusted=True)
    if format == 'html':
        doc.parts.append('</details>')
    if receipt['format'] == 'lablens-plan-receipt/v1':
        request = PlanRequest(question=receipt['question'], plan=receipt['plan'], dataset=dataset)
        request, tools, results, preview = prepare(request)
        doc.heading('原始问题与执行范围')
        doc.paragraph(request.question)
        doc.paragraph('仅列出的步骤：' + (', '.join(s.id for s in request.plan.steps) or '无执行步骤'))
        doc.heading('本次复核摘要')
        doc.paragraph('按 v0.11.0 规则重新核对；以下摘要不是执行当时的检查记录。详细规则结果位于结果表之后。', trusted=True)
        doc.table(['项目', '本次结果'], [
            ['列出步骤数', len(request.plan.steps)], ['声明未处理项数', len(request.plan.unhandled)],
            ['查询规则存在冲突', preview['request_checks']['has_conflicts']],
            ['比较与资料规则存在冲突', preview['task_checks']['has_conflicts']],
            ['全部原句有完整映射或声明未处理', preview['coverage']['all_clauses_accounted_for']],
        ])
        doc.paragraph('规则未覆盖的表达仍须人工核对。重算通过或没有冲突均不代表意图正确、需求完整或来源真实。', trusted=True)
        overview = []
        for item in results:
            result = item['result']
            if item['kind'] == 'query':
                summary = f"完整查询结果：{len(result['runs'])} 行"
            elif item['kind'] == 'compare':
                summary = result['baseline']['id'] + ' → ' + result['candidate']['id'] + f"；正确题数差 {result['additional_correct']} count"
            else:
                summary = f"资料说明：{len(result['notes'])} 条"
            overview.append([item['id'], item['kind'], summary])
        doc.table(['步骤', '操作', '重算结果摘要'], overview)
        doc.heading('按计划顺序重算的结果')
        for step, item in zip(request.plan.steps, results):
            _result(doc, item, step)
        _checks(doc, preview, request)
    else:
        query = QueryRuns.model_validate(receipt['query'])
        result = query_view(tools.execute('query_runs', query.model_dump()))
        doc.heading('查询范围')
        doc.paragraph('此查询收据没有保存原始自然语言问题。报告仅重算记录的查询参数，不推测原问题，也不进行原文映射、查询意图或任务规则核对。', trusted=True)
        _result(doc, {'kind': 'query', 'result': result})
    doc.heading('封装快照的来源与配置')
    doc.paragraph('此附录列出整个封装快照，不表示每条记录都被查询选中或参与比较。synthetic 为“是”的记录属于合成数据。资料原文不等于经过认证的事实。', trusted=True)
    for run in tools.runs:
        doc.heading(run['id'] + ' · ' + run['label'], 3)
        doc.table(['字段', '快照中的值'], [[name, run[name]] for name in (
            'project', 'dataset', 'split_id', 'length_unit', 'tokenizer', 'used_for_checkpoint_selection',
            'synthetic', 'source_path', 'source_sha256')])
        doc.code(run['model_config_details'], title='记录的模型配置')
    doc.heading('封装快照的全部资料原文')
    _notes(doc, tools.notes)
    doc.heading('原始收据与离线复核')
    doc.paragraph('复制以下完整 JSON 并保存为 receipt.json，可使用 lablens verify receipt.json 离线重算。内容哈希不是数字签名；同时修改数据和哈希可以形成新的自洽收据，因此重算通过不证明真实性。', trusted=True)
    doc.code(receipt, title='完整原始收据 JSON（可复制）')
    suffix = 'html' if format == 'html' else 'md'
    return {'format': format, 'filename': f"lablens-report-{receipt['content_sha256'][:12]}.{suffix}",
            'media_type': 'text/html; charset=utf-8' if format == 'html' else 'text/markdown; charset=utf-8',
            'content': doc.finish(), 'verification': verification, 'report_version': REPORT_VERSION, 'model_calls': 0}
