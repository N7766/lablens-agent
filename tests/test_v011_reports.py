"""Report boundaries: recompute first, literal evidence, no model/file dereference."""
from copy import deepcopy
from html.parser import HTMLParser
import json
import os
from pathlib import Path
import re
import sys

import pytest
from fastapi.testclient import TestClient

from lablens.api import app
from lablens.cli import main
from lablens.datasets import DatasetBundle
from lablens.planning import PlanRequest, execute_plan, preview_plan
from lablens.reporting import export_report
from lablens.schemas import QueryRuns
from lablens.workbench import QueryRequest, execute, preview, receipt_digest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def bundle():
    return json.loads((ROOT / 'lablens/data/example-bundle.json').read_text())


def query_receipt(bundle, **kwargs):
    request = QueryRequest(query=QueryRuns(**kwargs), dataset=DatasetBundle.model_validate(bundle))
    request.preview_id = preview(request)['preview_id']
    return execute(request)['receipt']


def plan_receipt(bundle, reverse=False):
    question = '列出所有记录；以run:sample-b为基线比较run:sample-e的正确题数差；说明长度单位与数据来源；重新训练模型。'
    parts = question.rstrip('。').split('；')
    request = PlanRequest(question=question, dataset=bundle, plan={'steps': [
        {'id': 'q', 'kind': 'query', 'request_excerpt': parts[0], 'query': {}},
        {'id': 'cmp', 'kind': 'compare', 'request_excerpt': parts[1],
         'baseline': {'run_id': 'run:sample-e' if reverse else 'run:sample-b'},
         'candidate': {'run_id': 'run:sample-b' if reverse else 'run:sample-e'}},
        {'id': 'notes', 'kind': 'describe', 'request_excerpt': parts[2]},
    ], 'unhandled': [{'request_excerpt': parts[3], 'reason': 'unsupported_analysis'}]})
    request.preview_id = preview_plan(request)['preview_id']
    return execute_plan(request)['receipt']


class DocumentParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.tags, self.attrs, self.codes, self.text = [], [], [], []
        self.current = None

    def handle_starttag(self, tag, attrs):
        self.tags.append(tag)
        self.attrs.extend(attrs)
        if tag == 'code':
            self.current = ''

    def handle_endtag(self, tag):
        if tag == 'code':
            self.codes.append(self.current)
            self.current = None

    def handle_data(self, data):
        self.text.append(data)
        if self.current is not None:
            self.current += data


def json_blocks(report, format):
    if format == 'html':
        parser = DocumentParser(); parser.feed(report)
        return [json.loads(value) for value in parser.codes]
    return [json.loads(m[2]) for m in re.finditer(r'(?m)^(`{3,})json\n(.*?)\n\1\n', report, re.S)]


@pytest.mark.parametrize('format', ['html', 'markdown'])
def test_report_recomputes_complete_query_and_preserves_receipt(bundle, format):
    receipt = query_receipt(bundle)
    original = deepcopy(receipt)
    report = export_report(receipt, format)
    assert report['verification']['status'] == 'verified'
    assert report['report_version'] == '0.11.0'
    assert report['model_calls'] == 0
    assert report['format'] == format
    assert report['filename'].endswith('.html' if format == 'html' else '.md')
    assert receipt in json_blocks(report['content'], format)
    assert receipt == original
    if format == 'html':
        content = report['content']
        marker = '<details><summary>文件与复核信息</summary>'
        start = content.index(marker)
        end = content.index('</details>', start)
        assert '不证明来源真实、意图正确或需求完整' in content[:start]
        assert receipt['snapshot_id'] in content[start:end]
        assert '不推断过去的模型调用次数或费用' in content[start:end]
        assert '查询范围' in content[end:]
    assert receipt['snapshot_id'] in report['content']
    assert receipt['content_sha256'] in report['content']
    assert '没有保存原始自然语言问题' in report['content']
    assert '不推断过去的模型调用次数或费用' in report['content']
    assert 'tokenizer' in report['content'] and 'source_sha256' in report['content']
    for row in receipt['result']['runs']:
        assert row['id'] in report['content'] and str(row['mean_length']) in report['content']
    content = report['content']
    positions = [content.index(row['id'], content.index('下表保留全部结果')) for row in receipt['result']['runs']]
    assert positions == sorted(positions)


@pytest.mark.parametrize('format', ['html', 'markdown'])
@pytest.mark.parametrize('reverse', [True, False])
def test_plan_report_current_checks_direction_units_and_unhandled(bundle, format, reverse):
    receipt = plan_receipt(bundle, reverse)
    content = export_report(receipt, format)['content']
    assert '按 v0.11.0 规则重新核对' in content
    assert '不是执行当时的检查记录' in content
    assert '仅列出的步骤：q, cmp, notes' in content
    assert '重新训练模型' in content and 'unsupported_analysis' in content
    assert ('run:sample-e → run:sample-b' if reverse else 'run:sample-b → run:sample-e') in content
    assert 'percentage_points' in content and 'percent' in content and 'count' in content
    assert 'conflict' in content if reverse else 'matched' in content
    assert receipt in json_blocks(content, format)


@pytest.mark.parametrize('format', ['html', 'markdown'])
def test_empty_query_keeps_empty_result_scope(bundle, format):
    receipt = query_receipt(bundle, min_correct=10000)
    content = export_report(receipt, format)['content']
    assert '结果为空' in content
    assert receipt['result']['runs'] == []
    assert receipt in json_blocks(content, format)


@pytest.mark.parametrize('format', ['html', 'markdown'])
def test_untrusted_markup_is_literal_and_fenced_receipt_survives(bundle, format):
    attack = '</pre><script>alert(1)</script><img src="https://evil.test/x" onerror="alert(1)">'
    multiline = '原文 | 保留\n```\n[点我](javascript:alert(1))\n``````\n' + attack
    bundle['runs'][0]['label'] = attack
    bundle['runs'][0]['source_path'] = 'data/' + attack
    bundle['runs'][0]['model_config_details'] = {'<iframe src=x>': '[x](https://evil.test/)'}
    bundle['notes'][0].update(title=attack, text=multiline, source='javascript:alert(1) | source')
    receipt = query_receipt(bundle)
    content = export_report(receipt, format)['content']
    assert receipt in json_blocks(content, format)
    if format == 'html':
        parser = DocumentParser(); parser.feed(content)
        assert not {'script', 'iframe', 'img', 'a', 'link'} & set(parser.tags)
        assert not any(name in {'src', 'href', 'onerror', 'onclick'} for name, value in parser.attrs)
        assert multiline in ''.join(parser.text)
        assert "default-src 'none'" in content
    else:
        assert '```````text\n' + multiline + '\n```````' in content
        assert '｜' not in content
        assert 'javascript:alert(1) \\| source' in content


@pytest.mark.parametrize('format', ['html', 'markdown'])
@pytest.mark.parametrize('mutation', ['bad_hash', 'rehash_result', 'snapshot', 'extra_field'])
def test_refuses_tampering_even_after_rehash(bundle, format, mutation):
    receipt = query_receipt(bundle)
    if mutation == 'bad_hash':
        receipt['content_sha256'] = '0' * 64
    elif mutation == 'rehash_result':
        receipt['result']['runs'][0]['mean_length'] += 1
    elif mutation == 'snapshot':
        receipt['snapshot_id'] = 'snapshot:' + '0' * 64
    else:
        receipt['question'] = 'invented'
    if mutation != 'bad_hash':
        receipt['content_sha256'] = receipt_digest({k: v for k, v in receipt.items() if k != 'content_sha256'})
    with pytest.raises(ValueError):
        export_report(receipt, format)


@pytest.mark.parametrize('format', ['html', 'markdown'])
def test_export_never_constructs_model_or_reads_source_file(bundle, monkeypatch, format):
    receipt = plan_receipt(bundle)
    def forbidden(*args, **kwargs):
        raise AssertionError('Report must use enclosed data only')
    monkeypatch.setattr('lablens.model.HTTPModel.__init__', forbidden)
    monkeypatch.setattr(Path, 'read_text', forbidden)
    assert export_report(receipt, format)['model_calls'] == 0


def test_api_strict_contract_and_tampered_rejection(bundle, monkeypatch):
    receipt = query_receipt(bundle)
    monkeypatch.setattr('lablens.api.HTTPModel', lambda: pytest.fail('Model constructed'))
    client = TestClient(app)
    result = client.post('/api/report/export', json={'receipt': receipt})
    assert result.status_code == 200 and result.json()['format'] == 'html'
    result = client.post('/api/report/export', json={'receipt': receipt, 'format': 'markdown'})
    assert result.status_code == 200 and result.json()['model_calls'] == 0
    for payload in ({'receipt': receipt, 'extra': 1}, {'receipt': receipt, 'format': 'pdf'},
                    {'receipt': []}, {'receipt': receipt, 'format': 1}):
        assert client.post('/api/report/export', json=payload).status_code == 422
    receipt['result']['runs'].clear()
    assert client.post('/api/report/export', json={'receipt': receipt}).status_code == 422


@pytest.mark.parametrize('format', ['html', 'markdown'])
def test_cli_writes_report_without_large_stdout(bundle, tmp_path, monkeypatch, capsys, format):
    receipt = query_receipt(bundle)
    source = tmp_path / 'receipt.json'; source.write_text(json.dumps(receipt))
    output = tmp_path / 'nested/report.txt'
    monkeypatch.setattr(sys, 'argv', ['lablens', 'report', str(source), '--format', format, '--output', str(output)])
    main()
    assert output.read_text() == export_report(receipt, format)['content']
    assert len(capsys.readouterr().out) < 500
    assert json.loads(source.read_text()) == receipt


@pytest.mark.parametrize('failure', ['missing_output', 'same_path', 'tampered', 'format', 'extra_data'])
def test_cli_failure_preserves_destination(bundle, tmp_path, monkeypatch, failure):
    receipt = query_receipt(bundle)
    source = tmp_path / 'receipt.json'; source.write_text(json.dumps(receipt))
    output = tmp_path / 'report.html'; output.write_text('KEEP')
    args = ['lablens', 'report', str(source)]
    if failure != 'missing_output':
        args += ['--output', str(source if failure == 'same_path' else output)]
    if failure == 'tampered':
        receipt['content_sha256'] = '0' * 64; source.write_text(json.dumps(receipt))
    if failure == 'format': args += ['--format', 'pdf']
    if failure == 'extra_data': args += ['--data-dir', 'do-not-read']
    before = source.read_bytes()
    monkeypatch.setattr(sys, 'argv', args)
    with pytest.raises(SystemExit) as exc:
        main()
    assert exc.value.code == 2
    assert output.read_text() == 'KEEP' and source.read_bytes() == before


def test_old_plan_receipt_exports_without_schema_upgrade():
    payload = json.loads((ROOT / 'docs/evaluations/v0.5/holdout/planner-ceiling-shortest-compare-r1.json').read_text())
    receipt = payload.get('receipt', payload)
    if 'format' not in receipt:
        receipt = payload['execution']['receipt']
    assert receipt['format'] == 'lablens-plan-receipt/v1'
    report = export_report(receipt)
    assert report['verification']['status'] == 'verified'
    assert receipt in json_blocks(report['content'], 'html')


def test_cli_rejects_existing_file_alias_and_preserves_receipt(bundle, tmp_path, monkeypatch):
    receipt = query_receipt(bundle)
    source = tmp_path / 'receipt.json'; source.write_text(json.dumps(receipt))
    alias = tmp_path / 'another-name.json'
    os.link(source, alias)
    assert source.resolve() != alias.resolve() and source.samefile(alias)
    before = source.read_bytes()
    monkeypatch.setattr(sys, 'argv', ['lablens', 'report', str(source), '--output', str(alias)])
    with pytest.raises(SystemExit) as exc:
        main()
    assert exc.value.code == 2
    assert source.read_bytes() == alias.read_bytes() == before
