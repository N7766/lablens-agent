"""Once-only Round15 cross-service evidence using an isolated JVM/Python pair.

prepare is free; retrieval and agent require --go GO. No provider retries.
Each phase creates an exclusive marker and preserves attempted calls on failure.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import statistics
import threading
import time

import httpx

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / 'docs/evaluations/conversation-round15'
FIXTURE_HASHES = {
    'corpus.json': 'd140576b9e1155d039bd509b564183a2631924b10d187da2aa7f0e2526e084ab',
    'questions.json': '1bc4c94ae9eb2e97658ecdbe31c377721524dcc41a4059c4de53f65f5e98dbb9',
}
PRICE_USD_PER_TOKEN = .01 / 1_000_000
STRATEGIES = ('bm25', 'dense', 'hybrid')


def dump(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n')


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(',', ':')).encode()).hexdigest()


def candidate_hashes():
    files = ['lablens/rag.py', 'lablens/rag_api.py', 'lablens/retrieval_index.py',
             'lablens/lexical.py', 'lablens/assessment.py', 'lablens/workspace_agent.py',
             'scripts/evaluate_persistent_index_round15.py',
             'services/workspace/src/main/java/io/lablens/workspace/WorkspaceIndexController.java',
             'docs/evaluations/conversation-round15/protocol.md',
             'docs/evaluations/conversation-round15/agent-protocol.md']
    return {name: hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in files}


def score_retrieval(question, result, strategy):
    # The retrieval API nests hits under retrieval. Only the first three chunks
    # count; duplicate document IDs cannot inflate recall or precision.
    ranked = list(dict.fromkeys(hit['document_id'] for hit in result['retrieval']['hits'][:3]))
    relevant = set(question['relevant_doc_ids'])
    found = set(ranked) & relevant
    return {'strategy': strategy, 'question_id': question['id'], 'status': 'completed',
            'ranked_document_ids': ranked, 'relevant_doc_ids': sorted(relevant),
            'recall_at_3': len(found)/len(relevant), 'precision_at_3': len(found)/3,
            'precision_at_3_ceiling': min(3, len(relevant))/3,
            'all_required_documents_present': relevant <= set(ranked),
            'contradictory_pair_present': set(question['contradictory_pair']) <= set(ranked)
                if question.get('contradictory_pair') else None,
            'expected_evidence_status': question['expected_evidence_status'],
            'semantic_status': 'not_evaluated_extractive_only'}


def summarize_retrieval(rows, questions):
    result = {}
    for strategy in STRATEGIES:
        selected = [row for row in rows if row['strategy'] == strategy]
        completed = [row for row in selected if row['status'] == 'completed']
        failures = [row for row in selected if row['status'] == 'failed']
        result[strategy] = {
            'planned_questions': len(questions), 'completed_questions': len(completed),
            'failed_questions': len(failures), 'not_attempted_questions': len(questions)-len(selected),
            'macro_recall_at_3_all_planned': sum(row['recall_at_3'] for row in completed)/len(questions),
            'macro_precision_at_3_all_planned': sum(row['precision_at_3'] for row in completed)/len(questions),
            'macro_precision_at_3_ceiling': statistics.mean(min(3, len(q['relevant_doc_ids']))/3 for q in questions),
            'all_required_documents_present': sum(row['all_required_documents_present'] for row in completed),
            'contradictory_pairs_present': sum(row['contradictory_pair_present'] is True for row in completed),
            'contradictory_pairs_planned': sum(bool(q.get('contradictory_pair')) for q in questions),
            'all_planned_denominator_note': 'Failed and unattempted cases contribute zero; incomplete runs are not acceptance passes',
        }
    return result


class EmbeddingBudget:
    """Pre-dispatch reservations are never refunded, including on unknown usage.

    BGE-M3's text tokenizer emits pieces from input bytes, plus special tokens.
    UTF-8 bytes + 16 per string conservatively reserves these tiny text inputs;
    returned usage is also checked against the reservation before any next call.
    """
    def __init__(self, started, clock=time.monotonic):
        self.started, self.clock = started, clock
        self.reserved = {'embedding_requests': 0, 'embedded_inputs': 0, 'embedding_tokens': 0}
        self.known = dict(self.reserved)
        self.unknown = False
        self.entries = []

    def reserve(self, label, inputs=(), max_requests=0, minimum_seconds=1):
        tokens = sum(max(1, len(value.encode('utf-8'))) + 16 for value in inputs)
        if any(len(value.encode('utf-8')) + 16 > 8192 for value in inputs):
            raise RuntimeError('Conservative single-input token reservation exceeds model context')
        addition = dict(embedding_requests=max_requests, embedded_inputs=len(inputs), embedding_tokens=tokens)
        limits = dict(embedding_requests=60, embedded_inputs=250, embedding_tokens=50000)
        if any(self.reserved[name] + addition[name] > limits[name] for name in limits):
            raise RuntimeError('Pre-dispatch embedding budget exhausted; no request sent')
        if (self.reserved['embedding_tokens'] + tokens)*PRICE_USD_PER_TOKEN > .05:
            raise RuntimeError('Pre-dispatch embedding cost ceiling reached; no request sent')
        if self.clock()-self.started + minimum_seconds >= 300:
            raise RuntimeError('Insufficient remaining wall budget; no request sent')
        for name in self.reserved:
            self.reserved[name] += addition[name]
        entry = {'label': label, 'reservation': addition, 'status': 'reserved',
                 'reserved_at_elapsed_seconds': self.clock()-self.started,
                 'known_usage': None, 'provider_usage_unknown': False}
        self.entries.append(entry)
        return entry

    def sent(self, entry):
        entry['status'] = 'sent'

    def uncertain(self, entry):
        if entry['status'] == 'sent':
            entry['status'] = 'failed'
            entry['provider_usage_unknown'] = bool(entry['reservation']['embedding_requests'])
            self.unknown |= entry['provider_usage_unknown']

    def account(self, entry, result):
        usage = result.get('usage')
        if not isinstance(usage, dict):
            self.uncertain(entry)
            raise RuntimeError('Missing usage; stop without retry')
        entry['known_usage'] = usage
        unknown = usage.get('provider_usage_unknown', False)
        for name in self.known:
            value = usage.get(name)
            if type(value) is not int or value < 0:
                unknown = True
            else:
                self.known[name] += value
                if value > entry['reservation'][name]:
                    entry['status'] = 'budget_mismatch'
                    raise RuntimeError('Provider usage exceeded pre-call reservation; stop')
        entry['provider_usage_unknown'] = bool(unknown)
        self.unknown |= bool(unknown)
        entry['status'] = 'unknown_usage' if unknown else 'completed'
        if unknown:
            raise RuntimeError('Unknown provider usage; stop without retry')
        if usage.get('model_calls') != 0:
            raise RuntimeError('Generation was not authorized in the retrieval run')
        if self.clock()-self.started > 300:
            raise RuntimeError('Retrieval wall budget exceeded; stop')

    def report(self):
        return {'reserved_upper_bounds': self.reserved, 'known_reported_usage': self.known,
                'provider_usage_unknown': self.unknown,
                'reported_total_embedding_tokens': None if self.unknown else self.known['embedding_tokens'],
                'known_usage_estimate_usd': self.known['embedding_tokens']*PRICE_USD_PER_TOKEN,
                'total_estimate_usd': None if self.unknown else self.known['embedding_tokens']*PRICE_USD_PER_TOKEN,
                'reservation_estimate_upper_bound_usd': self.reserved['embedding_tokens']*PRICE_USD_PER_TOKEN,
                'billing_note': 'Estimate at published standard price, not supplier invoice',
                'entries': self.entries}


def run(args):
    out = Path(args.output)
    out.mkdir(parents=True, exist_ok=True)
    with (out/(args.phase+'-started.json')).open('x') as file:
        json.dump({'phase': args.phase, 'started_at_epoch': time.time()}, file)
    for name, expected in FIXTURE_HASHES.items():
        assert hashlib.sha256((FIXTURE/name).read_bytes()).hexdigest() == expected
    documents = json.loads((FIXTURE/'corpus.json').read_text())['documents']
    questions = json.loads((FIXTURE/'questions.json').read_text())['questions']
    started = time.monotonic()
    rows, calls, failure = [], [], None
    budget = EmbeddingBudget(started)
    calls_lock = threading.Lock()
    with httpx.Client(base_url=args.jvm, trust_env=False, timeout=110) as client:
        def call(label, method, path, *, reservation=None, **kwargs):
            record = {'label': label, 'method': method, 'path': path, 'request': kwargs.get('json'),
                      'status': 'started', 'started_at_elapsed_seconds': time.monotonic()-started}
            start = time.monotonic()
            with calls_lock:
                calls.append(record)
                dump(out/(label+'.json'), record)
            try:
                if reservation is not None:
                    # This is the local bridge timeout. The isolated worker's
                    # provider HTTP timeout is separately frozen at 20 seconds.
                    kwargs['timeout'] = min(100, max(.01, 300-(time.monotonic()-started)))
                    budget.sent(reservation)
                response = client.request(method, path, **kwargs)
                record['http_status'] = response.status_code
                body = None if response.status_code == 204 else response.json()
                record['body'] = body
                if reservation is not None:
                    budget.account(reservation, body if isinstance(body, dict) else {})
                if not response.is_success:
                    raise RuntimeError(f'HTTP {response.status_code} at {label}')
                record['status'] = 'completed'
                return body
            except Exception as exc:
                record.update(status='failed', error_type=type(exc).__name__, error=str(exc))
                if reservation is not None:
                    budget.uncertain(reservation)
                raise
            finally:
                record['elapsed_ms'] = (time.monotonic()-start)*1000
                with calls_lock:
                    dump(out/(label+'.json'), record)
                    dump(out/(args.phase+'-call-ledger.json'), calls)
                if reservation is not None:
                    dump(out/'embedding-call-ledger.json', budget.report())

        def poll_import(prefix, path, batch):
            for i in range(30):
                batch = call(f'{prefix}-status-{i:02}', 'GET', path+'/imports/'+batch['id'])
                if batch['status'] != 'QUEUED':
                    break
                time.sleep(.2)
            assert batch['status'] == 'APPLIED' and batch['applied_revision'] == 1
            return batch

        def source_snapshot(snapshot):
            return [{key: row[key] for key in ('id', 'title', 'text', 'source')} for row in snapshot['documents']]

        try:
            if args.phase == 'prepare':
                workspace = call('workspace', 'POST', '/api/workspaces', json={'name': 'Round15 frozen Kestrel — synthetic'})['id']
                path = f'/api/workspaces/{workspace}'
                body = {'expected_revision': 0, 'documents': documents}
                def submit(i):
                    return call(f'import-submit-{i:02}', 'POST', path+'/imports',
                                headers={'Idempotency-Key': 'round15-frozen-import'}, json=body)
                with ThreadPoolExecutor(max_workers=8) as pool:
                    batches = list(pool.map(submit, range(12)))
                assert len({batch['id'] for batch in batches}) == 1
                poll_import('import', path, batches[0])
                snapshot = call('snapshot', 'GET', path+'/documents')
                source = source_snapshot(snapshot)
                assert snapshot['revision'] == 1
                assert sorted(source, key=lambda doc: doc['id']) == sorted(documents, key=lambda doc: doc['id'])
                status = call('worker-status-before-freeze', 'GET', args.worker+'/api/rag/status')
                hashes = candidate_hashes()
                assert status['loaded_implementation_sha256'] == hashes['lablens/rag.py']
                freeze = {'workspace_id': workspace, 'revision': 1,
                          'snapshot_id': workspace+':1', 'ordered_documents': source,
                          'documents_sha256': digest(source), 'fixture_hashes': FIXTURE_HASHES,
                          'candidate_hashes': hashes, 'worker_status': status,
                          'request_order': [{'strategy': strategy, 'question_id': q['id']} for strategy in STRATEGIES for q in questions],
                          'provider_http_timeout_seconds_required': 20, 'provider_retries': 0,
                          'embedding_model': 'BAAI/bge-m3', 'provider_revision_pinned': False,
                          'local_model_revision_namespace': 'deepinfra-bge-m3-observed-2026-11-08'}
                # Freeze actual MySQL document ordering before any paid work.
                with (out/'ordered-snapshot-freeze.json').open('x') as file:
                    json.dump(freeze, file, ensure_ascii=False, indent=2)
                indexed = call('bm25-build', 'POST', path+'/index', json={'strategy': 'bm25', 'expected_revision': 1})
                assert indexed['status'] == 'ready' and indexed['documents_sha256'] == digest(source)
                repeat = call('bm25-replay', 'POST', path+'/index', json={'strategy': 'bm25', 'expected_revision': 1})
                assert repeat['index_id'] == indexed['index_id'] and repeat['index_reused']
                dump(out/'state.json', {'workspace_id': workspace, 'revision': 1,
                                       'documents_sha256': digest(source), 'bm25_index_id': indexed['index_id']})
                return

            state = json.loads((out/'state.json').read_text())
            freeze = json.loads((out/'ordered-snapshot-freeze.json').read_text())
            assert candidate_hashes() == freeze['candidate_hashes'], 'Candidate changed after freeze'
            workspace = state['workspace_id']
            path = f'/api/workspaces/{workspace}'
            snapshot = call(args.phase+'-snapshot', 'GET', path+'/documents')
            source = source_snapshot(snapshot)
            assert snapshot['revision'] == state['revision']
            assert source == freeze['ordered_documents'] and digest(source) == state['documents_sha256']
            payload = {'snapshot_id': workspace+':'+str(snapshot['revision']), 'documents': source}

            if args.phase == 'retrieval':
                assert args.go == 'GO', 'Paid embeddings require --go GO'
                status = call('retrieval-worker-status', 'GET', args.worker+'/api/rag/status')
                assert status['loaded_implementation_sha256'] == freeze['candidate_hashes']['lablens/rag.py']
                assert status['dense']['available'] and status['dense']['model'] == 'BAAI/bge-m3'
                assert status['persistent_index']['enabled']
                # Each frozen document is one chunk. Reserve one possible HTTP
                # call per input, conservatively allowing a provider batch size of 1.
                assert all(doc['text'].strip() == doc['text'] and len(doc['text']) <= 800 for doc in source)
                inputs = [doc['title']+'\n'+doc['text'] for doc in source]
                reservation = budget.reserve('semantic-build', inputs, len(inputs), minimum_seconds=85)
                dump(out/'embedding-call-ledger.json', budget.report())
                build = call('semantic-build', 'POST', path+'/index', reservation=reservation,
                             json={'strategy': 'dense', 'expected_revision': 1, 'approved_revision': 1})
                assert build['status'] == 'ready' and build['documents_sha256'] == state['documents_sha256']
                # Reserve conservatively even though exact ready-index replay is
                # required to make no provider call; unexpected work cannot escape caps.
                reservation = budget.reserve('semantic-replay', inputs, len(inputs), minimum_seconds=85)
                dump(out/'embedding-call-ledger.json', budget.report())
                replay = call('semantic-replay', 'POST', path+'/index', reservation=reservation,
                              json={'strategy': 'hybrid', 'expected_revision': 1, 'approved_revision': 1})
                assert replay['index_id'] == build['index_id'] and replay['index_reused']
                assert replay['usage']['embedding_requests'] == 0
                state['semantic_index_id'] = build['index_id']
                dump(out/'state.json', state)
                for strategy in STRATEGIES:
                    for question in questions:
                        label = strategy+'-'+question['id']
                        try:
                            paid = strategy != 'bm25'
                            reservation = budget.reserve(label, [question['question']] if paid else [],
                                                         1 if paid else 0, minimum_seconds=25 if paid else 1)
                            dump(out/'embedding-call-ledger.json', budget.report())
                            result = call(label, 'POST', args.worker+'/api/rag/answer', reservation=reservation,
                                          json=payload | {'question': question['question'], 'mode': 'extractive', 'strategy': strategy, 'top_k': 3})
                            assert result['status'] in ('excerpts', 'no_hits')
                            assert result['retrieval']['index_reused']
                            assert result['retrieval']['documents_sha256'] == state['documents_sha256']
                            rows.append(score_retrieval(question, result, strategy))
                        except Exception as exc:
                            rows.append({'strategy': strategy, 'question_id': question['id'],
                                         'status': 'failed', 'error_type': type(exc).__name__,
                                         'semantic_status': 'not_evaluated_extractive_only'})
                            raise
                        finally:
                            dump(out/'retrieval-rows.json', rows)
            elif args.phase == 'restart':
                for strategy in ('bm25', 'dense'):
                    result = call('restart-'+strategy, 'GET', path+'/index?strategy='+strategy)
                    expected = state['bm25_index_id' if strategy == 'bm25' else 'semantic_index_id']
                    assert result['status'] == 'ready' and result['index_id'] == expected
                    assert result['usage']['embedding_requests'] == 0
                result = call('restart-bm25-query', 'POST', args.worker+'/api/rag/answer',
                              json=payload | {'question': questions[1]['question'], 'mode': 'extractive', 'strategy': 'bm25', 'top_k': 3})
                assert result['retrieval']['index_reused'] and result['usage']['embedding_requests'] == 0
                if args.go == 'GO':
                    # Separate predeclared one-query restart probe: prior paid run
                    # must be known and leave room under the same round limits.
                    previous=json.loads((out/'embedding-call-ledger.json').read_text())
                    assert not previous['provider_usage_unknown']
                    reserved=previous['reserved_upper_bounds']
                    assert reserved['embedding_requests']+1<=60 and reserved['embedded_inputs']+1<=250
                    question=questions[1]['question']
                    assert reserved['embedding_tokens']+len(question.encode('utf-8'))+16<=50000
                    reservation=budget.reserve('restart-dense-query', inputs=[question], max_requests=1, minimum_seconds=25)
                    dump(out/'restart-embedding-ledger.json',budget.report())
                    try:
                        result=call('restart-dense-query','POST',args.worker+'/api/rag/answer',reservation=reservation,
                                    json=payload|{'question':question,'mode':'extractive','strategy':'dense','top_k':3})
                        assert result['retrieval']['index_reused'] and result['retrieval']['index_id']==state['semantic_index_id']
                        assert result['usage']['embedding_requests']==1 and result['usage']['embedded_inputs']==1
                    finally:
                        dump(out/'restart-embedding-ledger.json',budget.report())
            elif args.phase == 'agent':
                assert args.go == 'GO', 'Paid model task requires --go GO'
                # This exposed-fixture wiring demonstration requires a separate
                # generation protocol and service-side model budgets, not the embedding protocol.
                assert (FIXTURE/'agent-protocol.md').is_file(), 'Freeze a separate bounded generation protocol first'
                task = call('agent-submitted', 'POST', path+'/tasks', headers={'Idempotency-Key': 'round15-agent-once'},
                            json={'question': questions[1]['question'], 'mode': 'agent', 'strategy': 'bm25', 'top_k': 3, 'approved_revision': 1})
                i = 0
                while task['status'] in ('QUEUED', 'RUNNING', 'CANCEL_REQUESTED'):
                    assert time.monotonic()-started <= 110
                    time.sleep(.5)
                    task = call(f'agent-poll-{i:03}', 'GET', path+'/tasks/'+task['id'])
                    i += 1
                dump(out/'agent-final.json', task)
                refreshed = call('agent-refreshed', 'GET', path+'/tasks/'+task['id'])
                assert refreshed == task
                dump(out/'agent-summary.json', {'status': task['status'], 'task_id': task['id'],
                    'snapshot_revision': task['snapshot_revision'], 'elapsed_seconds': time.monotonic()-started,
                    'history_readback_equal': True, 'semantic_review': 'pending separate manual AI review'})
            elif args.phase == 'invalidate':
                # Engineering mutations belong to a separate workspace. The
                # frozen retrieval/Agent snapshot above is never modified.
                engineering = call('invalidation-workspace', 'POST', '/api/workspaces',
                                   json={'name': 'Round15 revision invalidation — synthetic copy'})['id']
                engineering_path = f'/api/workspaces/{engineering}'
                copied = call('invalidation-import', 'POST', engineering_path+'/imports',
                              headers={'Idempotency-Key': 'round15-invalidation-copy'},
                              json={'expected_revision': 0, 'documents': source})
                poll_import('invalidation-import', engineering_path, copied)
                original = call('invalidation-initial-index', 'POST', engineering_path+'/index',
                                json={'strategy': 'bm25', 'expected_revision': 1})
                assert original['status'] == 'ready'
                first = source[0]
                changed = {key: first[key] for key in ('title', 'text', 'source')}
                changed['text'] += ' 本轮版本失效检查追加标记。'
                call('mutation', 'PUT', engineering_path+'/documents/'+first['id'], json=changed)
                missing = call('after-mutation-index', 'GET', engineering_path+'/index?strategy=bm25')
                assert missing['status'] == 'missing' and missing['revision'] == 2
                rebuilt = call('after-mutation-build', 'POST', engineering_path+'/index', json={'strategy': 'bm25', 'expected_revision': 2})
                assert rebuilt['status'] == 'ready' and rebuilt['index_id'] != original['index_id']
                call('deletion', 'DELETE', engineering_path+'/documents/'+first['id'])
                missing = call('after-deletion-index', 'GET', engineering_path+'/index?strategy=bm25')
                assert missing['status'] == 'missing' and missing['revision'] == 3
                rebuilt = call('after-deletion-build', 'POST', engineering_path+'/index', json={'strategy': 'bm25', 'expected_revision': 3})
                assert rebuilt['status'] == 'ready' and rebuilt['chunk_count'] == 9
                current = call('after-mutations-frozen-snapshot', 'GET', path+'/documents')
                assert current['revision'] == 1 and source_snapshot(current) == freeze['ordered_documents']
                dump(out/'invalidation-summary.json', {'engineering_workspace_id': engineering,
                    'frozen_workspace_id': workspace, 'edit_invalidated': True, 'delete_invalidated': True,
                    'rebuilt_chunks': 9, 'frozen_snapshot_unchanged': True})
        except Exception as exc:
            failure = {'error_type': type(exc).__name__, 'error': str(exc)}
            raise
        finally:
            for name, expected in FIXTURE_HASHES.items():
                assert hashlib.sha256((FIXTURE/name).read_bytes()).hexdigest() == expected
            dump(out/(args.phase+'-completion.json'), {'phase': args.phase,
                'status': 'failed' if failure else 'completed', 'failure': failure,
                'elapsed_seconds': time.monotonic()-started, 'candidate_hashes': candidate_hashes()})
            dump(out/(args.phase+'-call-ledger.json'), calls)
            if args.phase == 'retrieval':
                dump(out/'embedding-call-ledger.json', budget.report())
                dump(out/'retrieval-summary.json', {'status': 'failed' if failure else 'completed',
                    'strategies': summarize_retrieval(rows, questions), 'budget': budget.report(),
                    'elapsed_seconds': time.monotonic()-started,
                    'semantic_conclusion_correctness': 'not_measured',
                    'authorship': 'AI synthetic, not human independent evaluation', 'failure': failure})


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('phase', choices=['prepare', 'retrieval', 'restart', 'agent', 'invalidate'])
    parser.add_argument('--output', required=True)
    parser.add_argument('--jvm', default='http://127.0.0.1:8086')
    parser.add_argument('--worker', default='http://127.0.0.1:8770')
    parser.add_argument('--go', default='NO')
    run(parser.parse_args())
