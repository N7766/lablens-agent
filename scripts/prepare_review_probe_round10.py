#!/usr/bin/env python3
"""Reconstruct two exposed Round 9 review inputs offline; never call a provider.

Historical Python files are read from their sealed Git commit, hash checked, and
executed in an isolated temporary directory. This is a diagnostic input bundle,
not a new evaluation freeze or evidence of successful model review.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile

REPO = Path(__file__).resolve().parents[1]
RUN = REPO / 'docs/evaluations/conversation-round9/runs/round9-once'
TURN_IDS = ('cedar-t1', 'harbor-t1')


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def canonical_sha(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(',', ':')).encode('utf-8')).hexdigest()


def verify_raw(run=RUN):
    """Validate the complete recorded seal, including all inputs used below."""
    run = Path(run).resolve()
    seal_path = run / 'raw-seal.json'
    seal = read(seal_path)
    for name, expected in seal['files'].items():
        path = (run / name).resolve()
        if not path.is_relative_to(run) or not path.is_file() or sha(path) != expected:
            raise ValueError('Sealed historical raw changed: ' + name)
    return seal, sha(seal_path)


def sealed_read(run, seal, name):
    if name not in seal['files']:
        raise ValueError('Required input is absent from raw seal: ' + name)
    path = (run / name).resolve()
    if not path.is_relative_to(run.resolve()) or sha(path) != seal['files'][name]:
        raise ValueError('Required input no longer matches seal: ' + name)
    return read(path)


def _one(events, predicate, description):
    found = [(i, event) for i, event in enumerate(events) if predicate(event)]
    if len(found) != 1:
        raise ValueError('Expected exactly one ' + description)
    return found[0]


def reconstruct(inputs):
    """Called only inside the isolated historical-source child process.

    The scope deliberately includes only these initial turns. General history
    reconstruction needs the exact context selection, not inferred prior answers.
    """
    from lablens import rag
    from lablens.answer_coverage import prepare_review_draft
    from lablens.answer_quality import make_review_contract
    from lablens.assessment import evidence_catalog, resolve_selections, validate_assessments
    from lablens.runtime_telemetry import request_body_bytes

    def no_network(event, args):
        if event in {'socket.connect', 'socket.connect_ex', 'socket.getaddrinfo'}:
            raise RuntimeError('Offline reconstruction forbids network access')
    sys.addaudithook(no_network)
    task, conversation, created, documents = (inputs[key] for key in
        ('task', 'conversation', 'created', 'documents'))
    result = task.get('result') or task['error_result']
    trace, metadata = result['agent']['trace'], result['context']
    if (inputs['turn_id'] not in TURN_IDS or task['conversation_seq'] != 1
            or metadata['input_turns'] != 0 or metadata['omitted_turns'] != 0
            or created['turns'] or created['omitted_turns'] != 0
            or conversation['id'] != task['conversation_id']
            or created['id'] != task['conversation_id']
            or conversation['state'] != created['state']
            or metadata['pinned_state'] != created['state']
            or any(turn['seq'] < task['conversation_seq'] for turn in conversation['turns'])):
        raise ValueError('This probe only reconstructs proven first-turn empty histories')
    original_documents = [rag.RAGDocument(**{key: doc[key] for key in ('id', 'title', 'text', 'source')})
                          for doc in documents['documents']]
    doc_hash = rag.documents_sha256(original_documents)
    if doc_hash != result['agent']['documents_sha256']:
        raise ValueError('Sealed documents do not match the original Agent snapshot')
    chunks = {chunk['chunk_id']: chunk for chunk in rag.chunk_documents(original_documents)}
    submission_index, submission = _one(trace, lambda e: e.get('event') == 'tool_started'
        and e.get('name') in {'verify_and_submit_report', 'submit_report'}, 'original report submission')
    request_index, request = _one(trace, lambda e: e.get('event') == 'request_started'
        and e.get('phase') == 'review', 'original review request')
    coverage_index, original_coverage = _one(trace, lambda e: e.get('event') == 'review_coverage_slots',
                                          'original coverage event')
    if not submission_index < coverage_index < request_index:
        raise ValueError('Review events are not in causal order')
    observed, search_indexes = {}, []
    for index, event in enumerate(trace[:submission_index]):
        if event.get('event') != 'tool' or event.get('name') != 'search_workspace':
            continue
        search_indexes.append(index)
        output = event['output']
        if (output['documents_sha256'] != doc_hash
                or output['snapshot_id'] != result['agent']['snapshot_id']
                or output['passages'] != evidence_catalog(output['hits'])):
            raise ValueError('Search source snapshot or passage catalog mismatch')
        for hit in output['hits']:
            known = chunks.get(hit['chunk_id'])
            if known is None or any(hit.get(key) != value for key, value in known.items()):
                raise ValueError('Retrieved source text, offsets or hash differs from original documents')
            observed[hit['chunk_id']] = hit
    if not search_indexes:
        raise ValueError('No original retrieval precedes submission')
    hits = list(observed.values())
    question = task['request']['question']
    resolved = resolve_selections(submission['arguments'], hits)
    # The original Agent called this validator but did not replace the draft
    # with its return value. Preserve exactly that order and behavior here.
    validate_assessments(question, resolved, hits)
    draft, coverage = prepare_review_draft(question, resolved)
    if coverage != original_coverage['output']:
        raise ValueError('Coverage reconstruction differs from the sealed original event')
    context = dict(current_question=question, pinned_state=metadata['pinned_state'],
        state_authority=metadata['state_authority'], recent_user_messages=[], untrusted_recent_answers=[],
        omitted_turns=0, previous_answers_are_evidence=False)
    context['scoped_review_contract'] = make_review_contract(question, draft)
    context['scoped_review_contract']['explicit_coverage_slots'] = coverage
    payload = rag._generation_payload(question, hits, {'model': inputs['model']}, draft=draft,
                                      context_data=context)
    reconstructed_bytes = request_body_bytes(payload)
    recorded_bytes = request['request_body_bytes']
    return dict(turn_id=inputs['turn_id'], question=question, draft=draft, hits=hits, context_data=context,
        original_payload=payload, original_review_timeout_ms=request['timeout_ms'],
        tool_arguments=submission['arguments'], resolved_before_coverage=resolved,
        byte_validation=dict(recorded_request_body_bytes=recorded_bytes,
            reconstructed_request_body_bytes=reconstructed_bytes, difference_bytes=reconstructed_bytes-recorded_bytes,
            matches_recorded_length=reconstructed_bytes == recorded_bytes,
            metric='Installed HTTPX json= encoder; body only, no headers or authentication',
            reconstructed_payload_canonical_sha256=canonical_sha(payload),
            original_wire_content_hash_available=False,
            exact_original_wire_identity_proven=False),
        trace_pointers=dict(search_events=search_indexes, tool_arguments=submission_index,
                            coverage=coverage_index, review_request=request_index),
        historical_documents_sha256=doc_hash)


def historical_python_sources(repo, source_record, target):
    """Read immutable Git objects without checkout, reset, worktree or writes to Git."""
    candidate = source_record['candidate_sha']
    if not re.fullmatch(r'[0-9a-f]{40}', candidate):
        raise ValueError('Historical candidate must be a full commit SHA')
    actual = subprocess.run(['git', 'rev-parse', candidate + '^{commit}'], cwd=repo,
                            check=True, capture_output=True, text=True).stdout.strip()
    if actual != candidate:
        raise ValueError('Candidate did not resolve to the recorded commit')
    restored = {}
    for name, expected in source_record['source_files'].items():
        if not name.startswith('lablens/') or not name.endswith('.py'):
            continue
        path = (target / name).resolve()
        if not path.is_relative_to(target.resolve()):
            raise ValueError('Historical source path escapes temporary package')
        content = subprocess.run(['git', 'show', candidate + ':' + name], cwd=repo,
                                 check=True, capture_output=True).stdout
        if hashlib.sha256(content).hexdigest() != expected:
            raise ValueError('Git source differs from sealed source hash: ' + name)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        restored[name] = expected
    if 'lablens/rag.py' not in restored or 'lablens/__init__.py' not in restored:
        raise ValueError('Incomplete historical Python source manifest')
    return restored


def prepare(*, run=RUN, repo=REPO):
    run, repo = Path(run).resolve(), Path(repo).resolve()
    seal, before_sha = verify_raw(run)
    approval = sealed_read(run, seal, 'approval.json')
    cases, source_manifests = [], {}
    for turn_id in TURN_IDS:
        scenario = turn_id.split('-')[0]
        names = dict(task=f'{turn_id}-final-task.json', conversation=f'{turn_id}-conversation.json',
            created=f'{scenario}-conversation-created.json', documents=f'{scenario}-documents.json',
            source=f'{turn_id}-source-before.json')
        loaded = {key: sealed_read(run, seal, name) for key, name in names.items()}
        source = loaded.pop('source')
        if (source['candidate_sha'] != approval['candidate_sha'] or not source['match']
                or source['source_files'] != approval['source_files']):
            raise ValueError('Historical source attestations disagree')
        with tempfile.TemporaryDirectory(prefix='lablens-r10-original-') as temporary:
            target = Path(temporary)
            restored = historical_python_sources(repo, source, target)
            child = ('import json, runpy, sys; '
                     'module = runpy.run_path(sys.argv[1]); '
                     'print(json.dumps(module["reconstruct"](json.load(sys.stdin)), ensure_ascii=False))')
            # -I removes PYTHONPATH/user site/current-directory import ambiguities;
            # insert only the hash-checked package explicitly before loading code.
            child = 'import sys; sys.path.insert(0, sys.argv[2]); ' + child
            completed = subprocess.run([sys.executable, '-I', '-c', child, str(Path(__file__).resolve()), str(target)],
                cwd=target, input=json.dumps(dict(turn_id=turn_id, model=approval['model'], **loaded), ensure_ascii=False),
                capture_output=True, text=True, check=False,
                env={key: value for key, value in os.environ.items()
                     if not any(token in key.upper() for token in ('KEY', 'TOKEN', 'SECRET', 'PASSWORD'))})
            if completed.returncode:
                detail = completed.stderr.strip().splitlines()[-1] if completed.stderr.strip() else 'No child diagnostic'
                raise ValueError('Historical reconstruction failed: ' + detail)
            case = json.loads(completed.stdout)
        source_manifests[turn_id] = restored
        prefix = str(run.relative_to(repo)) if run.is_relative_to(repo) else str(run)
        task_prefix = '/error_result' if loaded['task'].get('result') is None else '/result'
        provenance = dict(candidate_sha=source['candidate_sha'], raw_seal_sha256=before_sha,
            files={key: dict(path=prefix + '/' + name, sha256=seal['files'][name]) for key, name in names.items()},
            approval=dict(path=prefix + '/approval.json', sha256=seal['files']['approval.json']),
            question_pointer='/request/question',
            original_tool_arguments_pointer=task_prefix + '/agent/trace/' + str(case['trace_pointers']['tool_arguments']) + '/arguments',
            context_metadata_pointer=task_prefix + '/context',
            review_request_pointer=task_prefix + '/agent/trace/' + str(case['trace_pointers']['review_request']),
            source_method='Hash-checked Git source from sealed candidate, isolated child process; no provider call',
            final_report_used_as_input=False, model_output_rewritten=False)
        case['provenance'] = provenance
        cases.append(case)
    _, after_sha = verify_raw(run)
    if before_sha != after_sha:
        raise ValueError('Historical seal changed during reconstruction')
    return dict(schema_version=1, scope='Offline diagnostic reconstruction of two exposed failed Round 9 review requests',
        formal_evaluation_freeze=False, external_model_calls=0, raw_seal_sha256=before_sha,
        script_sha256=sha(Path(__file__)), historical_python_sources=source_manifests,
        runtime_versions={name: importlib.metadata.version(name) for name in ('httpx', 'pydantic')},
        limitations=['The original HTTP request content/hash was not saved. Matching recorded byte lengths does not prove byte-for-byte original wire identity.',
            'These are previously exposed AI synthetic examples, not independent questions, new answers or evidence of improved quality.',
            'Only first-turn empty histories are reconstructed. Later-turn context selection is deliberately unsupported.',
            'No provider, credential loading, model execution, retry or formal evaluation freeze occurs.'], cases=cases)


def write_bundle(output, bundle):
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open('x', encoding='utf-8') as handle:
        json.dump(bundle, handle, ensure_ascii=False, indent=2)
        handle.write('\n')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError('Refuse to overwrite an existing diagnostic input bundle')
    bundle = prepare()
    write_bundle(args.output, bundle)
    print(json.dumps(dict(output=str(args.output), external_model_calls=0,
        cases=[dict(turn_id=case['turn_id'], **case['byte_validation']) for case in bundle['cases']]), ensure_ascii=False))


if __name__ == '__main__':
    main()
