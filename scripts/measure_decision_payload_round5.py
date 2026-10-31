#!/usr/bin/env python3
"""Reconstruct exposed Round 4 decisions and compare their packed JSON bodies.

No socket, provider, generation, or latency benchmark is involved. The Cedar
third-step row projects a previously recorded redundant read that the new action
schema would no longer offer; it is not an executed new-model trajectory.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from lablens.assessment import evidence_catalog
from lablens.conversation import ConversationBudget, ConversationContext
from lablens.model import HTTPModel, workspace_action_payload
from lablens.rag import _new_usage, documents_sha256
from lablens.runtime_telemetry import request_body_bytes

BASELINE = 'b737ef7554ed368616f8485b55fed8666f574017'
MODEL = 'Qwen/Qwen3-235B-A22B-Instruct-2507'


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def names(payload):
    return [item['properties']['action']['enum'][0]
        for item in payload['response_format']['json_schema']['schema']['properties']['decision']['anyOf']]


def report_schema(payload):
    return next((item for item in payload['response_format']['json_schema']['schema']['properties']['decision']['anyOf']
        if item['properties']['action']['enum'] == ['verify_and_submit_report']), None)


def passages(payload):
    found = {}
    for message in payload['messages']:
        if message['role'] == 'user':
            value = json.loads(message['content']).get('untrusted_tool_result', {})
            for eid, reference in value.get('visible_passage_references', {}).items():
                assert eid in found and reference['chunk_id'] == found[eid]['chunk_id'], 'Dangling evidence reference'
            found.update(value.get('passages', {}))
    return found


class CaptureComplete(Exception):
    pass


def capture(old, old_conversation, scene):
    source = ROOT / f'docs/evaluations/conversation-round4/development/run/{scene}/{scene}-t1/task-final.json'
    task = json.loads(source.read_text())
    result = task['error_result'] or task['result']
    trace = result['agent']['trace']
    metadata = result['context']
    assert metadata['input_turns'] == metadata['omitted_turns'] == 0
    context = ConversationContext(id=metadata['conversation_id'], revision=metadata['conversation_revision'], state=metadata['pinned_state'])
    doc_path = source.parent.parent / 'registered-documents.json'
    docs = [{key: doc[key] for key in ('id', 'title', 'text', 'source')}
        for doc in json.loads(doc_path.read_text())['documents']]
    request = old.WorkspaceAgentRequest(question=task['request']['question'], mode='agent', strategy='bm25', top_k=5,
        snapshot_id=result['retrieval']['snapshot_id'], documents=docs, conversation_context=context)
    expected = [e for e in trace if e['event'] == 'request_started' and e['phase'] == 'decision']
    actions = {e['step']: e for e in trace if e['event'] == 'tool_started'}
    searches = iter(e['output'] for e in trace if e['event'] == 'tool' and e.get('name') == 'search_workspace')
    captured, payloads = [], []

    class Budget(old_conversation.ConversationBudget):
        def decision_messages(self, system, current, exchanges, tools, **kwargs):
            captured.append((system, copy.deepcopy(current), copy.deepcopy(exchanges), copy.deepcopy(tools),
                {key: copy.deepcopy(value) for key, value in kwargs.items() if key != 'payload_builder'}))
            return super().decision_messages(system, current, exchanges, tools, **kwargs)

    class RecordedModel(HTTPModel):
        def __init__(self): self.model = MODEL; self.timeout_seconds = 60
        def choose_workspace_action(self, payload, *, timeout_seconds):
            payloads.append(copy.deepcopy(payload))
            if len(payloads) == len(expected): raise CaptureComplete
            action = actions[len(payloads)]
            return dict(role='assistant', content=None, tool_calls=[dict(id=f'recorded-{len(payloads)}', type='function',
                function=dict(name=action['name'], arguments=json.dumps(action['arguments'], ensure_ascii=False)))]), {}

    class RecordedRetrieval:
        def post(self, url, *, json, **kwargs):
            output = next(searches)
            return httpx.Response(200, json=dict(mode='extractive', status=output['status'], usage=_new_usage(),
                retrieval=dict(snapshot_id=output['snapshot_id'], documents_sha256=output['documents_sha256'],
                               strategy=json['strategy'], hits=output['hits'])))

    old.ConversationBudget = Budget
    try:
        old.WorkspaceAgent(RecordedModel(), client=RecordedRetrieval()).run(request)
        raise AssertionError('Expected the final captured decision to stop the offline replay')
    except CaptureComplete:
        pass
    rows = []
    for index, ((system, current, exchanges, tools, kwargs), before, recorded) in enumerate(zip(captured, payloads, expected), 1):
        before_bytes = request_body_bytes(before)
        assert before_bytes == recorded['request_body_bytes'], (scene, index, before_bytes, recorded['request_body_bytes'])
        current['documents_sha256'] = documents_sha256(request.documents)
        original = copy.deepcopy(exchanges)
        budget = ConversationBudget(context)
        builder = lambda messages, choices: workspace_action_payload(messages, choices,
            model=before['model'], max_tokens=before['max_tokens'])
        budget.decision_messages(system, current, exchanges, tools, **kwargs, payload_builder=builder, compact_evidence=True)
        after = budget.decision_payload
        canonical = evidence_catalog(list(kwargs['observed'].values()))
        checks = dict(baseline_body_size_matches_recorded_http_request=True,
            full_passages_preserved=passages(before) == passages(after) == canonical,
            report_schema_unchanged=report_schema(before) == report_schema(after),
            raw_tool_history_unchanged=exchanges == original,
            model_and_output_limit_unchanged=before['model'] == after['model'] and before['max_tokens'] == after['max_tokens'],
            exact_guarded_payload_used=budget.calls[-1]['fits'])
        assert all(checks.values()), checks
        after_bytes = request_body_bytes(after)
        rows.append(dict(scene=scene, decision_step=index, before_body_bytes=before_bytes, after_body_bytes=after_bytes,
            bytes_removed=before_bytes-after_bytes, reduction_percent=round(100*(before_bytes-after_bytes)/before_bytes, 3),
            before_tools=names(before), after_tools=names(after), visibility=budget.calls[-1], checks=checks,
            recorded_task=str(source.relative_to(ROOT)), recorded_task_sha256=sha(source),
            registered_documents_sha256=sha(doc_path),
            scope='Counterfactual packing of the exposed recorded tool history; no new model decisions or elapsed-time measurements'))
    return rows


def measure(output):
    if output.exists(): raise RuntimeError('Choose a fresh output path; preserve earlier measurements')
    os.environ.update(LABLENS_AGENT_TOTAL_SECONDS='75', LABLENS_CONTEXT_WINDOW_TOKENS='65536')
    with tempfile.TemporaryDirectory(prefix='lablens-round5-baseline-') as directory:
        sources = {}
        for name in ('workspace_agent', 'conversation'):
            path = Path(directory) / (name + '.py')
            path.write_bytes(subprocess.check_output(['git', 'show', f'{BASELINE}:lablens/{name}.py'], cwd=ROOT))
            sources[name] = sha(path)
        old_conversation = load_module('lablens._round4_conversation', Path(directory) / 'conversation.py')
        old = load_module('lablens._round4_workspace_agent', Path(directory) / 'workspace_agent.py')
        cases = [row for scene in ('cedar', 'harbor') for row in capture(old, old_conversation, scene)]
    result = dict(scope=__doc__, baseline_commit=BASELINE, baseline_source_sha256=sources,
        external_model_calls=0, external_network_requests=0, source_sha256={str(path.relative_to(ROOT)): sha(path)
            for path in [Path(__file__).resolve(), ROOT/'lablens/conversation.py', ROOT/'lablens/workspace_agent.py',
                         ROOT/'lablens/model.py', ROOT/'lablens/assessment.py']}, cases=cases)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2)+'\n')
    print(json.dumps([{key: row[key] for key in ('scene', 'decision_step', 'before_body_bytes', 'after_body_bytes', 'reduction_percent')} for row in cases]))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    measure(parser.parse_args().output)
