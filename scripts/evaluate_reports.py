#!/usr/bin/env python3
"""Deterministic, offline acceptance of portable report delivery.

This is an engineering regression matrix, not a model-effectiveness benchmark.
The author is an assistant participating in this release's development.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
from html import unescape
from html.parser import HTMLParser
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from lablens.datasets import DatasetBundle
from lablens.planning import PlanRequest, execute_plan, preview_plan
from lablens.schemas import QueryRuns
from lablens.workbench import QueryRequest, execute, preview, receipt_digest

FIXTURE = ROOT / 'evaluation/v0.11/fixtures.json'
FIXTURE_SHA256 = 'f3b6e4102a8bc8512c76a86754b95928351c0ed2c52baff15dfe3a0f6785ea7b'
FORMATS = ('html', 'markdown')
HOSTILE = '</pre><img src="https://external.invalid/pixel" onerror="alert(1)"> [bad](javascript:alert(2)) | column\n```json\n{"fake":true}\n``` & <script>alert(1)</script>'


def sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def fixture() -> dict:
    raw = FIXTURE.read_bytes()
    if sha256(raw) != FIXTURE_SHA256:
        raise ValueError('The predeclared report matrix changed; review and disclose a new freeze')
    return json.loads(raw)


def bundle(kind='normal') -> dict:
    """Small authored summaries; all stated expected deltas are literal in fixtures."""
    runs = []
    for name, correct, length in [('a', 80, 200), ('b', 90, 100), ('c', 90, 100)]:
        runs.append(dict(id='run:' + name, label='Fixture ' + name, project='report-controls',
            dataset='authored-100', split_id='fixture-only', num_samples=100, correct=correct,
            accuracy=correct / 100, mean_length=length, length_unit='whitespace_words',
            tokenizer=None, model_config_details={'model': 'no-model-was-run'},
            used_for_checkpoint_selection=False, source_path='fixture/' + name + '.json',
            source_sha256='0' * 64, synthetic=True))
    notes = [dict(id='note:fixture', title='Authored fixture, not observed experiments',
                  text='这三条记录是人工构造的测试夹具，不是真实模型实验。', source='fixture/notes.txt')]
    if kind == 'hundred':
        runs = [dict(copy.deepcopy(runs[1]), id=f'run:row-{i:03}', label=f'row {i:03}',
                     source_path=f'fixture/row-{i:03}.json') for i in range(100)]
    elif kind == 'tokens':
        for run in runs:
            run.update(length_unit='tokenizer_tokens', tokenizer='fixture-tokenizer/v1')
    elif kind == 'hostile':
        runs[0]['label'] = HOSTILE
        runs[0]['model_config_details'] = {'untrusted key': HOSTILE}
        runs[0]['source_path'] = 'fixture/<svg onload="alert(1)">.json'
        notes[0].update(title=HOSTILE, text=HOSTILE, source='javascript:alert(2)')
    return DatasetBundle.model_validate(dict(runs=runs, notes=notes)).model_dump()


def query_receipt(query=None, dataset_kind='normal') -> dict:
    request = QueryRequest(query=QueryRuns.model_validate(query or {}),
                           dataset=DatasetBundle.model_validate(bundle(dataset_kind)))
    request.preview_id = preview(request)['preview_id']
    return execute(request)['receipt']


def plan_receipt(kind='comparison', dataset_kind='normal') -> dict:
    comparison = '以run:a为基线比较run:b的正确题数差'
    steps = [dict(id='compare', kind='compare', request_excerpt=comparison,
                  baseline={'run_id': 'run:a'}, candidate={'run_id': 'run:b'})]
    question, unhandled = comparison, []
    if kind == 'reverse':
        steps[0].update(baseline={'run_id': 'run:b'}, candidate={'run_id': 'run:a'})
    elif kind == 'self':
        question = '以run:a为基线比较run:a的正确题数差'
        steps[0].update(request_excerpt=question, candidate={'run_id': 'run:a'})
    elif kind == 'reference':
        query_question = '找出准确率最高的一条记录'
        steps.insert(0, dict(id='select', kind='query', request_excerpt=query_question,
                             query={'top_k': 1, 'include_ties': False}))
        steps[1]['candidate'] = {'query_step': 'select'}
        question = query_question + '；' + comparison
    elif kind in ('unhandled', 'partial'):
        unhandled = [dict(request_excerpt='计算训练费用', reason='missing_data')]
        question = '计算训练费用' if kind == 'unhandled' else comparison + '；计算训练费用'
        if kind == 'unhandled':
            steps = []
    elif kind in ('unsupported', 'hostile'):
        question = '解释这些实验是否真正更好' if kind == 'unsupported' else HOSTILE
        steps = [dict(id='describe', kind='describe', request_excerpt=question)]
    request = PlanRequest(question=question, dataset=DatasetBundle.model_validate(bundle(dataset_kind)),
                          plan={'steps': steps, 'unhandled': unhandled})
    request.preview_id = preview_plan(request)['preview_id']
    return execute_plan(request)['receipt']


def control_receipt(case: dict) -> dict:
    values = case['inputs']
    if case['kind'] == 'query':
        return query_receipt(values.get('query'), values.get('dataset_kind', 'normal'))
    if case['kind'] == 'plan':
        return plan_receipt(values['plan_kind'], values.get('dataset_kind', 'normal'))
    mutation = values['mutation']
    receipt = plan_receipt() if mutation == 'plan-delta' else query_receipt()
    if mutation == 'checksum':
        receipt['content_sha256'] = '0' * 64
        return receipt
    if mutation == 'query-result':
        receipt['result']['runs'][0]['correct'] = 81
    elif mutation == 'plan-delta':
        receipt['results'][0]['result']['additional_correct'] = 999
    elif mutation == 'snapshot':
        receipt['snapshot_id'] = 'snapshot:' + '0' * 64
    elif mutation == 'version':
        receipt['format'] = 'lablens-query-receipt/v999'
    elif mutation == 'missing':
        receipt.pop('query')
    elif mutation == 'extra':
        receipt['approved'] = True
    elif mutation == 'dataset':
        receipt['dataset']['runs'][0]['mean_length'] = 999
    else:
        raise ValueError('Unknown declared mutation')
    receipt['content_sha256'] = receipt_digest({k: v for k, v in receipt.items() if k != 'content_sha256'})
    return receipt


class ReportHTML(HTMLParser):
    """Read visible document evidence independently of renderer classes/IDs."""
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts, self.pre_blocks, self.tables, self.styles, self.unsafe = [], [], [], [], []
        self.pre_index = self.table_index = None
        self.in_style = False

    def handle_starttag(self, tag, attrs):
        if tag in {'script', 'iframe', 'object', 'embed', 'img', 'link', 'base', 'form', 'input'}:
            self.unsafe.append('active element: ' + tag)
        for name, value in attrs:
            if name.lower().startswith('on') or name.lower() in {'src', 'srcset', 'action', 'formaction'}:
                self.unsafe.append('active attribute: ' + name)
            if name.lower() == 'href' and value and not value.startswith('#'):
                self.unsafe.append('external or executable href')
            if name.lower() == 'style' and re.search(r'url\s*\(|@import', value or '', re.I):
                self.unsafe.append('external CSS')
        if tag == 'meta' and dict(attrs).get('http-equiv', '').lower() == 'refresh':
            self.unsafe.append('meta refresh')
        if tag == 'pre':
            self.pre_index = len(self.pre_blocks)
            self.pre_blocks.append([])
        if tag == 'table':
            self.table_index = len(self.tables)
            self.tables.append([])
        if tag == 'style':
            self.in_style = True

    def handle_endtag(self, tag):
        if tag == 'pre':
            self.pre_index = None
        if tag == 'table':
            self.table_index = None
        if tag == 'style':
            self.in_style = False

    def handle_data(self, data):
        if self.in_style:
            self.styles.append(data)
            return
        self.parts.append((self.pre_index, data))
        if self.pre_index is not None:
            self.pre_blocks[self.pre_index].append(data)
        if self.table_index is not None:
            self.tables[self.table_index].append(data)


def json_receipt(text):
    try:
        data = json.loads(text)
    except (ValueError, TypeError):
        return None
    return data if isinstance(data, dict) and data.get('format') in {
        'lablens-query-receipt/v1', 'lablens-plan-receipt/v1'} else None


def rules_json(text):
    try:
        data = json.loads(text)
    except (ValueError, TypeError):
        return None
    return data if isinstance(data, dict) and {'coverage', 'request_checks', 'task_checks'} <= data.keys() else None


def readable_report(content: str, format: str) -> tuple[str, list[str], list[dict], list[dict]]:
    if format == 'html':
        document = ReportHTML()
        document.feed(content)
        assert not document.unsafe, document.unsafe
        assert not re.search(r'url\s*\(|@import', ''.join(document.styles), re.I), 'External CSS'
        receipts = [(index, json_receipt(''.join(parts))) for index, parts in enumerate(document.pre_blocks)]
        receipts = [(index, value) for index, value in receipts if value is not None]
        raw_indexes = {index for index, _ in receipts}
        text = '\n'.join(text for index, text in document.parts if index not in raw_indexes)
        rules = [rules_json(''.join(parts)) for parts in document.pre_blocks]
        return text, ['\n'.join(table) for table in document.tables], [value for _, value in receipts], [value for value in rules if value]
    # A fenced JSON block is inert Markdown. Remove it before checking displayed evidence.
    lines, body, receipts, active_lines, rules = content.splitlines(), [], [], [], []
    index = 0
    while index < len(lines):
        opening = re.fullmatch(r'(`{3,}|~{3,})([^\r\n]*)', lines[index])
        if not opening:
            body.append(lines[index])
            active_lines.append(lines[index])
            index += 1
            continue
        fence = opening[1]
        end = index + 1
        while end < len(lines) and not re.fullmatch(re.escape(fence[0]) + '{' + str(len(fence)) + r',}\s*', lines[end]):
            end += 1
        assert end < len(lines), 'Unclosed Markdown fence'
        block = '\n'.join(lines[index + 1:end])
        value = json_receipt(block)
        rule = rules_json(block)
        if rule is not None:
            rules.append(rule)
        if value is not None:
            receipts.append(value)
        else:
            body.extend(lines[index:end + 1])
        index = end + 1
    text = '\n'.join(body)
    # Code spans are inert too. Match whole delimiter runs; a shorter run inside
    # a span cannot terminate it. Keep their content in the visible text audit.
    active = '\n'.join(active_lines)
    cursor, pieces = 0, []
    while cursor < len(active):
        opening = re.search(r'`+', active[cursor:])
        if not opening:
            pieces.append(active[cursor:])
            break
        start, end = cursor + opening.start(), cursor + opening.end()
        closing = re.search(r'(?<!`)' + re.escape(opening[0]) + r'(?!`)', active[end:])
        if not closing:
            pieces.append(active[cursor:end])
            cursor = end
            continue
        pieces.append(active[cursor:start])
        cursor = end + closing.end()
    active = ''.join(pieces)
    assert not re.search(r'<\s*/?\s*(?:script|img|iframe|svg|object|embed)\b', active, re.I), 'Active Markdown HTML'
    assert not re.search(r'(?<!\\)!?\[[^\n]*(?<!\\)\]\(\s*(?:javascript:|https?://)', active, re.I), 'Active Markdown link'
    tables = [line for line in text.splitlines() if line.startswith('|')]
    # Interpret escaped punctuation as text, without interpreting it as markup.
    visible = lambda value: re.sub(r'\\([\\`*{}\[\]()#+\-.!_|>])', r'\1', unescape(value))
    return visible(text), [visible(table) for table in tables], receipts, rules


def normalized(value: str) -> str:
    return re.sub(r'\s+', ' ', value).strip()


def require_number(text: str, value: float):
    matches = re.findall(r'(?<![\w:.])[-−]?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?', text)
    assert any(abs(float(token.replace('−', '-')) - value) <= 0.00001 for token in matches), ('Missing displayed value', value)


def check_export(receipt: dict, result: dict, format: str, expected=None) -> dict:
    assert result['format'] == format
    assert result['report_version'] == '0.11.0'
    assert result['model_calls'] == 0
    assert result['verification']['status'] == 'verified'
    assert result['media_type'].split(';')[0] == ('text/html' if format == 'html' else 'text/markdown')
    text, tables, copied, rules = readable_report(result['content'], format)
    assert copied == [receipt], 'Exactly one complete, copyable original receipt is required'
    visible, table_text = normalized(text), normalized('\n'.join(tables))
    assert receipt['snapshot_id'] in visible
    assert receipt['content_sha256'] in visible
    expected = expected or {}
    is_plan = receipt['format'] == 'lablens-plan-receipt/v1'
    if is_plan:
        assert normalized(receipt['question']) in visible
        assert '0.11.0' in visible and '重新核对' in visible
        assert len(rules) == 1, 'Plan report must include the full current rule-check record'
        rows = receipt['results']
        for item in receipt['plan']['unhandled']:
            assert normalized(item['request_excerpt']) in visible
            # The machine reason can be translated, but missing_data must remain explicit.
            assert item['reason'] in visible or {'missing_data': '资料缺失', 'unsupported_analysis': '不支持',
                'ambiguous_request': '明确'}[item['reason']] in visible
    else:
        rows = [dict(kind='query', result=receipt['result'])]
    for row in rows:
        out = row['result']
        if row['kind'] == 'query':
            for run_id in out['ordered_run_ids']:
                assert run_id in table_text, ('Missing visible query row', run_id)
            if not out['runs']:
                assert any(label in visible for label in ['没有符合', '0 条', '0条', '无匹配', '空结果'])
        elif row['kind'] == 'compare':
            assert out['baseline']['id'] in visible and out['candidate']['id'] in visible
            for key in ['accuracy_delta_percentage_points', 'additional_correct', 'length_reduction_percent']:
                require_number(table_text, out[key])
        elif row['kind'] == 'describe':
            for note in out['notes']:
                assert normalized(note['text']) in visible, ('Missing visible note', note['id'])
    for item in expected.get('visible', []):
        assert normalized(item) in visible, ('Missing literal expected text', item)
    for run_id in expected.get('row_ids', []):
        assert run_id in table_text
    if 'row_count' in expected:
        actual = receipt['result']['runs']
        assert len(actual) == expected['row_count']
        assert receipt['result']['ordered_run_ids'] == expected['row_ids']
    if 'additional_correct' in expected:
        comparison = next(row['result'] for row in rows if row['kind'] == 'compare')
        assert comparison['additional_correct'] == expected['additional_correct']
        assert abs(comparison['accuracy_delta_percentage_points'] - expected['accuracy_delta']) < 1e-9
        assert abs(comparison['length_reduction_percent'] - expected['length_reduction']) < 1e-9
        for key in ['additional_correct', 'accuracy_delta', 'length_reduction']:
            require_number(table_text, expected[key])
    if expected.get('task_conflict'):
        assert '冲突' in visible
        assert rules[0]['task_checks']['has_conflicts'] is True
    if expected.get('unassessed'):
        assert any(word in visible for word in ['未核对', '未覆盖', '未按', '不在', 'not_assessed'])
    if 'step_count' in expected:
        assert len(rows) == expected['step_count']
        assert len(receipt['plan']['unhandled']) == expected['unhandled_count']
    current_rules = None
    if rules:
        current_rules = {group: dict(status=rules[0][group]['status'],
            has_conflicts=rules[0][group]['has_conflicts'],
            assessed_units=len(rules[0][group]['units']),
            unassessed_clauses=len(rules[0][group]['unassessed']),
            scope=rules[0][group]['scope']) for group in ['request_checks', 'task_checks']}
        current_rules['all_clauses_accounted_for'] = rules[0]['coverage']['all_clauses_accounted_for']
    return dict(content_sha256=sha256(result['content'].encode()), bytes=len(result['content'].encode()),
                copied_receipt_equal=True, visible_evidence_checked=True, current_rule_checks=current_rules)


def historical_receipt(entry):
    path = ROOT / entry['path']
    raw = path.read_bytes()
    assert sha256(raw) == entry['sha256'], ('Historical source changed', entry['path'])
    content = json.loads(raw)
    receipt = content if entry['pointer'] == '' else content['execution']['receipt']
    return copy.deepcopy(receipt)


def run_control(case, format, export_report):
    receipt = control_receipt(case)
    before = copy.deepcopy(receipt)
    if case['expected'].get('reject'):
        try:
            export_report(receipt, format)
        except (ValueError, TypeError) as error:
            assert receipt == before, 'Rejected input was modified'
            return dict(status='passed', rejected=True, error_type=type(error).__name__)
        raise AssertionError('Invalid receipt was exported')
    report = export_report(receipt, format)
    audit = check_export(receipt, report, format, case['expected'])
    assert receipt == before, 'Input receipt was modified'
    assert export_report(receipt, format) == report, 'Export is not deterministic'
    return dict(status='passed', **audit)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT / 'reports/v0.11-report-controls')
    args = parser.parse_args()
    from lablens.reporting import export_report
    matrix = fixture()
    args.output.mkdir(parents=True, exist_ok=True)
    exports = args.output / 'exports'
    exports.mkdir(exist_ok=True)
    summary = dict(format='lablens-report-acceptance/v1', fixture_sha256=FIXTURE_SHA256,
        evaluation_kind='offline_engineering_controls_not_model_effectiveness', model_calls=0,
        report_generation_cost_usd=0, historical_model_cost_usd=None,
        reviewer='Assistant participating in development; not independent human review',
        source_manifest=matrix['historical_receipts'], historical=[], controls=[],
        implementation_hashes={path: sha256((ROOT / path).read_bytes()) for path in [
            'lablens/reporting.py', 'lablens/workbench.py', 'lablens/planning.py',
            'lablens/datasets.py', 'lablens/tools.py', 'lablens/request_checks.py',
            'lablens/task_checks.py', 'lablens/plan_checks.py',
            'scripts/evaluate_reports.py', 'tests/test_v011_report_controls.py']})
    for index, entry in enumerate(matrix['historical_receipts']):
        receipt = historical_receipt(entry)
        before = copy.deepcopy(receipt)
        for format in FORMATS:
            row = dict(path=entry['path'], pointer=entry['pointer'], format=format,
                       historical_contract_pass=entry['historical_contract_pass'])
            try:
                report = export_report(receipt, format)
                row.update(status='passed', **check_export(receipt, report, format))
                assert receipt == before, 'Input receipt was modified'
                extension = 'html' if format == 'html' else 'md'
                filename = f'{index:03}-{Path(entry["path"]).stem}.{extension}'
                (exports / filename).write_text(report['content'])
                row['export_path'] = 'exports/' + filename
            except Exception as error:
                row.update(status='failed', error=f'{type(error).__name__}: {error}')
            summary['historical'].append(row)
    for case in matrix['cases']:
        for format in FORMATS:
            row = dict(id=case['id'], format=format)
            try:
                row.update(run_control(case, format, export_report))
            except Exception as error:
                row.update(status='failed', error=f'{type(error).__name__}: {error}')
            summary['controls'].append(row)
    all_rows = summary['historical'] + summary['controls']
    summary['totals'] = dict(historical_receipts=len(matrix['historical_receipts']),
        historical_contract_failures=sum(entry['historical_contract_pass'] is False for entry in matrix['historical_receipts']),
        historical_exports=len(summary['historical']), control_cases=len(matrix['cases']),
        control_format_checks=len(summary['controls']), passed=sum(row['status'] == 'passed' for row in all_rows),
        failed=sum(row['status'] == 'failed' for row in all_rows))
    (args.output / 'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps(summary['totals'], ensure_ascii=False))
    for row in all_rows:
        if row['status'] == 'failed':
            print(json.dumps(row, ensure_ascii=False))
    raise SystemExit(1 if summary['totals']['failed'] else 0)


if __name__ == '__main__':
    main()
