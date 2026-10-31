#!/usr/bin/env python3
"""Offline protocol-byte scenarios from sealed Round 8 drafts; never calls a model."""
from __future__ import annotations
import argparse
import copy
import hashlib
import json
from pathlib import Path
import sys

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from lablens import rag
from lablens.answer_coverage import prepare_review_draft
from lablens.answer_quality import make_review_contract
from lablens.assessment import evidence_catalog, resolve_selections
from lablens.review_delta import INSTRUCTIONS, restore_review_delta
from lablens.runtime_telemetry import request_body_bytes

RUN = REPO / 'docs/evaluations/conversation-round8/runs/round8-once'


def read(path):
    return json.loads(path.read_text())


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify_raw():
    seal = read(RUN / 'raw-seal.json')
    for name, expected in seal['files'].items():
        path = (RUN / name).resolve()
        if not path.is_relative_to(RUN.resolve()) or sha(path) != expected:
            raise ValueError('Historical raw changed: ' + name)
    return sha(RUN / 'raw-seal.json')


def response_bytes(value):
    # Match generation_review.delta.response_json_bytes, not HTTP framing.
    return len(json.dumps(value, ensure_ascii=False).encode('utf-8'))


def rebuild(task, conversation):
    result = task.get('result') or task['error_result']
    trace = result['agent']['trace']
    observed = {}
    for event in trace:
        if event.get('event') == 'tool' and event.get('name') == 'search_workspace':
            observed.update((hit['chunk_id'], hit) for hit in event['output']['hits'])
    submission = next((event for event in trace if event.get('event') == 'tool_started'
                       and event.get('name') in {'verify_and_submit_report', 'submit_report'}), None)
    if submission is None:
        return None
    hits = list(observed.values())
    question = task['request']['question']
    draft, coverage = prepare_review_draft(question, resolve_selections(submission['arguments'], hits))
    original_coverage = next(e['output'] for e in trace if e.get('event') == 'review_coverage_slots')
    if coverage != original_coverage:
        raise ValueError('Coverage reconstruction differs from the frozen draft')
    metadata = result['context']
    previous = [t for t in conversation['turns'] if t['seq'] < task['conversation_seq']
                and t['status'] == 'SUCCEEDED' and t.get('answer')][-2:]
    context = dict(current_question=question, pinned_state=metadata['pinned_state'],
        state_authority=metadata['state_authority'],
        recent_user_messages=[{k: t[k] for k in ('seq', 'question', 'snapshot_id')} for t in previous],
        untrusted_recent_answers=[{k: t[k] for k in ('seq', 'answer', 'status', 'snapshot_id')} for t in previous],
        omitted_turns=metadata['omitted_turns'] + metadata['input_turns'] - len(previous),
        previous_answers_are_evidence=False)
    context['scoped_review_contract'] = make_review_contract(question, draft)
    context['scoped_review_contract']['explicit_coverage_slots'] = coverage
    payload = rag._generation_payload(question, hits, {'model': 'Qwen/Qwen3-235B-A22B-Instruct-2507'},
                                      draft=draft, context_data=context)
    return draft, hits, payload, trace


def compare():
    raw_sha = verify_raw()
    rows = []
    for outcome in read(RUN / 'run-summary.json')['turns']:
        tid = outcome['turn_id']
        if not outcome.get('task_file'):
            continue
        task_path = RUN / outcome['task_file']
        rebuilt = rebuild(read(task_path), read(RUN / f'{tid}-conversation.json'))
        if rebuilt is None:
            continue
        draft, hits, delta_payload, trace = rebuilt
        data = json.loads(delta_payload['messages'][1]['content'])
        policy = data['server_review_delta']
        baseline = copy.deepcopy(delta_payload)
        baseline_data = json.loads(baseline['messages'][1]['content'])
        del baseline_data['server_review_delta']
        baseline['messages'][1]['content'] = json.dumps(baseline_data, ensure_ascii=False)
        suffix = '\n' + INSTRUCTIONS
        if not baseline['messages'][0]['content'].endswith(suffix):
            raise ValueError('Delta instruction suffix changed; review the controlled baseline')
        baseline['messages'][0]['content'] = baseline['messages'][0]['content'][:-len(suffix)]
        baseline['response_format']['json_schema']['schema'] = rag._scoped_selection_schema(hits,
            [r['statement'] for r in draft['assessments']])
        catalog = evidence_catalog(hits)
        reverse = {(v['chunk_id'], v['quote']): k for k, v in catalog.items()}
        full_rows = [dict(assessment_id=f'a{i+1}', **{k: row[k] for k in ('verdict', 'explanation', 'basis')},
                         evidence_ids=[reverse[(q['chunk_id'], q['quote'])] for q in row['quotes']])
                     for i, row in enumerate(draft['assessments'])]
        full_response = {'assessments': full_rows}
        scenarios = {}
        for name, retained in [('zero_retention', []), ('all_eligible_retention', policy['retainable_assessment_ids'])]:
            response = dict(retained_assessment_ids=list(retained),
                            assessments=[r for r in full_rows if r['assessment_id'] not in retained])
            if restore_review_delta(response, draft, hits, policy) != draft:
                raise ValueError('Protocol reconstruction was not identical to the original draft')
            scenarios[name] = dict(retained_ids=list(retained), replacement_count=len(response['assessments']),
                response_json_bytes=response_bytes(response),
                response_delta_bytes=response_bytes(response) - response_bytes(full_response),
                restores_same_draft=True,
                quality_validated=False)
        actual_request = next(e['request_body_bytes'] for e in trace
            if e.get('event') == 'request_started' and e.get('phase') == 'review')
        rows.append(dict(turn_id=tid, task_sha256=sha(task_path), task_status=outcome['status'],
            original_draft_rows=len(full_rows), retainable_ids=policy['retainable_assessment_ids'],
            original_round8_recorded_request_bytes=actual_request,
            controlled_full_replacement_request_bytes=request_body_bytes(baseline),
            candidate_delta_request_bytes=request_body_bytes(delta_payload),
            controlled_request_delta_bytes=request_body_bytes(delta_payload) - request_body_bytes(baseline),
            controlled_full_replacement_response_json_bytes=response_bytes(full_response), scenarios=scenarios))
    if verify_raw() != raw_sha:
        raise ValueError('Historical raw seal changed during reconstruction')
    return dict(schema_version=1, scope='Offline synthetic protocol-byte scenarios from exposed sealed Round 8 drafts; no model calls.',
        raw_seal_sha256=raw_sha,
        method='Same current prompt/catalog/context in both request builders; remove only delta instructions, policy and schema for the full-row baseline. Each synthetic replacement repeats the original draft unchanged; all-eligible retention is a size bound, not a corrected answer.',
        source_hashes={p: sha(REPO / p) for p in ('scripts/compare_review_delta_round9.py', 'lablens/review_delta.py',
            'lablens/rag.py', 'lablens/assessment.py', 'lablens/answer_quality.py', 'lablens/answer_coverage.py')},
        limitations=['Not the exact full Round8-versus-Round9 request comparison; recorded Round8 bytes are reference only.',
            'No actual model selection, generated response, token, latency, cost or quality improvement is measured.',
            'All-eligible retention can preserve semantic errors outside finite guards; known blocked rows still use unchanged synthetic replacements here.',
            'Response metric is parsed JSON serialized with ensure_ascii=False/default separators, excluding HTTP framing.'],
        rows=rows)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError('Refuse to overwrite a previous contrast; use a separate output path')
    data = compare()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({'output': str(args.output), 'review_drafts': len(data['rows']),
        'request_added_bytes': [r['controlled_request_delta_bytes'] for r in data['rows']],
        'eligible_response_delta_bytes': [r['scenarios']['all_eligible_retention']['response_delta_bytes'] for r in data['rows']]}, ensure_ascii=False))


if __name__ == '__main__':
    main()
