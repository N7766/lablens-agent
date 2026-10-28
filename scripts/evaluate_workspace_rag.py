#!/usr/bin/env python3
"""Evaluate frozen RAG questions against the actual HTTP endpoint, preserving failures.

No synthetic embeddings, fallback ranks, answer judging model, retries, or hidden
warm-up requests are used. Dense/hybrid and --live-generation can incur charges.
"""
from __future__ import annotations
import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import statistics
import sys
import time

import httpx

ROOT = Path(__file__).resolve().parents[1]
UNAVAILABLE = {'unavailable', 'provider_error', 'http_error', 'transport_error', 'invalid_response'}
ABSTENTIONS = {'refused', 'no_hits'}


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def load_frozen(suite: Path) -> tuple[dict, dict, dict]:
    manifest = json.loads((suite / 'manifest.json').read_text())
    for name, expected in manifest['files'].items():
        if sha((suite / name).read_bytes()) != expected:
            raise ValueError(f'Frozen input hash mismatch: {name}')
    corpus = json.loads((suite / 'corpus.json').read_text())
    queries = json.loads((suite / 'queries.json').read_text())
    documents = {document['id']: document for document in corpus['documents']}
    if len(documents) != len(corpus['documents']):
        raise ValueError('Duplicate corpus document IDs')
    for document in documents.values():
        if sha(document['text'].encode()) != document['provenance']['content_sha256']:
            raise ValueError(f'Document content hash mismatch: {document["id"]}')
    seen = set()
    for query in queries['queries']:
        if query['id'] in seen or not set(query['relevant_document_ids']) <= documents.keys():
            raise ValueError('Duplicate query or unknown relevance label')
        if bool(query['relevant_document_ids']) != query['answerable']:
            raise ValueError('Answerability/relevance labels conflict')
        seen.add(query['id'])
    return manifest, corpus, queries


def citation_checks(citations: list, hits: list, documents: dict) -> dict:
    known = {hit.get('chunk_id'): hit for hit in hits if isinstance(hit, dict)}
    failures = []
    checked = 0
    for index, citation in enumerate(citations):
        checked += 1
        problems = []
        if not isinstance(citation, dict):
            failures.append({'index': index, 'reasons': ['citation is not an object']})
            continue
        document = documents.get(citation.get('document_id'))
        hit = known.get(citation.get('chunk_id'))
        if document is None:
            problems.append('unknown document ID')
        if hit is None:
            problems.append('citation chunk was not returned by retrieval')
        elif any(citation.get(key) != hit.get(key) for key in
                 ('document_id', 'title', 'source', 'text', 'start', 'end', 'content_sha256')):
            problems.append('citation fields differ from retrieved chunk')
        if document is not None:
            start, end = citation.get('start'), citation.get('end')
            if type(start) is not int or type(end) is not int or not 0 <= start < end <= len(document['text']):
                problems.append('invalid document character offsets')
            elif citation.get('text') != document['text'][start:end]:
                problems.append('excerpt differs from original document substring')
            if citation.get('source') != document['source'] or citation.get('title') != document['title']:
                problems.append('source/title mismatch')
            text = citation.get('text')
            if not isinstance(text, str) or sha(text.encode()) != citation.get('content_sha256'):
                problems.append('excerpt content hash mismatch')
        if problems:
            failures.append({'index': index, 'chunk_id': citation.get('chunk_id'), 'reasons': problems})
    return {'checked': checked, 'valid': checked - len(failures), 'failures': failures,
            'scope': 'retrieved membership, source metadata, literal substring and hash; not semantic claim faithfulness'}


def score(query: dict, response: dict, documents: dict) -> dict:
    retrieval = response.get('retrieval') if isinstance(response.get('retrieval'), dict) else {}
    hits = retrieval.get('hits') if isinstance(retrieval.get('hits'), list) else []
    hits = [hit for hit in hits if isinstance(hit, dict)][:5]
    expected = set(query['relevant_document_ids'])
    actual = {hit.get('document_id') for hit in hits}
    first_rank = next((rank for rank, hit in enumerate(hits, 1) if hit.get('document_id') in expected), None)
    status = response.get('status', 'invalid_response')
    available = status not in UNAVAILABLE
    matched = expected & actual if available else set()
    citations = response.get('citations') if isinstance(response.get('citations'), list) else []
    checks = citation_checks(citations, hits, documents)
    recall = len(matched) / len(expected) if expected else None
    reciprocal = (1 / first_rank if first_rank and available else 0.0) if expected else None
    failure_reasons = []
    if not available:
        failure_reasons.append('request_unavailable_or_failed')
    if expected and recall < 1:
        failure_reasons.append('missing_required_document')
    if expected and status in ABSTENTIONS:
        failure_reasons.append('abstained_on_answerable_question')
    if not expected and available and status not in ABSTENTIONS:
        failure_reasons.append('returned_output_on_unanswerable_question')
    if checks['failures']:
        failure_reasons.append('citation_source_inconsistency')
    if status == 'invalid_citations':
        failure_reasons.append('generation_rejected_by_citation_or_structure_checks')
    return {'status': status, 'available': available, 'recalled_document_ids': sorted(matched),
            'missing_document_ids': sorted(expected - matched), 'document_recall_at_5': recall,
            'mrr_at_5': reciprocal, 'all_required_documents_retrieved': recall == 1 if expected else None,
            'abstained': status in ABSTENTIONS, 'citations': checks,
            'failure_reasons': failure_reasons,
            'semantic_answer_correctness': 'not_assessed',
            'note': 'Top 5 retrieved chunks, deduplicated into document IDs without backfill; unanswerable excerpt output is not itself a false generated factual claim.'}


def percentile(values: list[float], percent: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * percent / 100
    low = math.floor(position)
    high = math.ceil(position)
    return round(ordered[low] + (ordered[high] - ordered[low]) * (position - low), 3)


def latency(values: list[float]) -> dict:
    return {'n': len(values), 'p50_ms': percentile(values, 50), 'p95_ms': percentile(values, 95),
            'max_ms': round(max(values), 3) if values else None}


def aggregate(records: list[dict]) -> list[dict]:
    groups = defaultdict(list)
    for record in records:
        groups[(record['split'], record['strategy'], record['mode'])].append(record)
    output = []
    for (split, strategy, mode), rows in sorted(groups.items()):
        answerable = [row for row in rows if row['answerable']]
        unknown = [row for row in rows if not row['answerable']]
        sources_checked = sum(row['score']['citations']['checked'] for row in rows)
        sources_valid = sum(row['score']['citations']['valid'] for row in rows)
        counters = {key: 0 for key in ('model_calls', 'embedding_requests', 'embedded_inputs', 'embedding_cache_hits', 'embedding_tokens')}
        token_totals = {key: 0 for key in ('prompt_tokens', 'completion_tokens', 'total_tokens')}
        unknown_usage = 0
        cache_conditions = defaultdict(list)
        for row in rows:
            usage = row['response'].get('usage') or {}
            if usage.get('provider_usage_unknown') or row['score']['status'] in {'transport_error', 'invalid_response', 'http_error'}:
                unknown_usage += 1
            for key in counters:
                value = usage.get(key)
                if type(value) is int:
                    counters[key] += value
            for key in token_totals:
                value = (usage.get('generation_tokens') or {}).get(key)
                if type(value) is int:
                    token_totals[key] += value
            cache_conditions[row['embedding_cache_condition']].append(row['client_latency_ms'])
        output.append({'split': split, 'strategy': strategy, 'mode': mode, 'requests': len(rows),
            'completed_requests': sum(row['score']['available'] for row in rows),
            'status_counts': dict(Counter(row['score']['status'] for row in rows)),
            'answerable_questions': len(answerable),
            'document_recall_at_5': statistics.mean(row['score']['document_recall_at_5'] for row in answerable) if answerable else None,
            'mrr_at_5': statistics.mean(row['score']['mrr_at_5'] for row in answerable) if answerable else None,
            'all_required_documents_retrieved': sum(row['score']['all_required_documents_retrieved'] for row in answerable),
            'answerable_abstentions': sum(row['score']['abstained'] for row in answerable),
            'unanswerable_questions': len(unknown),
            'unanswerable_abstentions': sum(row['score']['abstained'] for row in unknown),
            'unanswerable_failed_requests': sum(not row['score']['available'] for row in unknown),
            'citation_source_checks': sources_checked, 'citation_source_checks_valid': sources_valid,
            'citation_source_consistency_rate': sources_valid / sources_checked if sources_checked else None,
            'client_latency': latency([row['client_latency_ms'] for row in rows]),
            'client_latency_by_embedding_cache_condition': {key: latency(value) for key, value in cache_conditions.items()},
            'retrieval_latency': latency([row['response']['retrieval']['latency_ms'] for row in rows if isinstance((row['response'].get('retrieval') or {}).get('latency_ms'), (int, float))]),
            'reported_usage_known_subtotals': {**counters, 'generation_tokens': token_totals},
            'requests_with_unknown_usage': unknown_usage,
            'failure_case_ids': [row['query_id'] for row in rows if row['score']['failure_reasons']],
            'semantic_answer_correctness': 'not_assessed'})
    return output


def cache_condition(response: dict, strategy: str) -> str:
    if strategy == 'bm25':
        return 'no_embedding'
    usage = response.get('usage') or {}
    embedded = usage.get('embedded_inputs', 0)
    hits = usage.get('embedding_cache_hits', 0)
    if embedded > 1:
        return 'document_cold_or_partial_query_uncached'
    if hits and embedded == 1:
        return 'documents_cached_query_uncached'
    return 'unavailable_or_not_observed'


def markdown(summary: dict) -> str:
    lines = ['# Workspace RAG evaluation', '',
        'Same-assistant-authored project corpus and questions. These results are not an independent benchmark, production load test, or semantic faithfulness score.', '',
        '| Split | Strategy | Mode | Completed | Recall@5 | MRR@5 | Unanswerable abstentions | p50 / p95 ms |',
        '| --- | --- | --- | ---: | ---: | ---: | ---: | ---: |']
    def fmt(value):
        return 'n/a' if value is None else f'{value:.4f}'
    for group in summary['groups']:
        time_values = group['client_latency']
        lines.append(f"| {group['split']} | {group['strategy']} | {group['mode']} | {group['completed_requests']}/{group['requests']} | {fmt(group['document_recall_at_5'])} | {fmt(group['mrr_at_5'])} | {group['unanswerable_abstentions']}/{group['unanswerable_questions']} | {time_values['p50_ms']} / {time_values['p95_ms']} |")
    lines.extend(['', 'Recall denominator includes every answerable request, with failed/unavailable requests scored zero. Top 5 means retrieved chunks; duplicate chunks from one document do not create extra document credit.', '',
        'Dense document warm-up and query-provider latency are recorded separately by cache condition in summary.json. Query vectors are not assumed cached. Fixed sequential strategy order and shared document cache prevent an apples-to-apples speed claim.', '',
        'Source consistency checks validate cited IDs, exact source substrings, metadata and SHA-256 only. They do not judge whether a generated claim follows from the citation. Excerpts on an unanswerable question are counted as retrieval non-abstention, not fabricated factual answers.', '',
        '## Recorded failures', ''])
    for row in summary['records']:
        if row['score']['failure_reasons']:
            lines.append(f"- {row['split']}/{row['strategy']}/{row['mode']}/{row['query_id']}: {', '.join(row['score']['failure_reasons'])}; missing={row['score']['missing_document_ids']}; status={row['score']['status']}")
    return '\n'.join(lines) + '\n'


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--endpoint', default='http://127.0.0.1:8765/api/rag/answer')
    parser.add_argument('--suite', type=Path, default=ROOT / 'evaluation/workspace')
    parser.add_argument('--output', type=Path, default=ROOT / 'reports/workspace-rag')
    parser.add_argument('--strategies', default='bm25,dense,hybrid')
    parser.add_argument('--split', choices=['all', 'dev', 'heldout'], default='all')
    parser.add_argument('--live-generation', action='store_true', help='Use only the frozen live subset; incurs up to four generation calls for each selected strategy')
    parser.add_argument('--live-limit', type=int, default=4)
    parser.add_argument('--timeout', type=float, default=180)
    args = parser.parse_args()
    strategies = args.strategies.split(',')
    if not strategies or len(set(strategies)) != len(strategies) or set(strategies) - {'bm25', 'dense', 'hybrid'}:
        parser.error('strategies must be a comma-separated subset of bm25,dense,hybrid')
    if not 1 <= args.live_limit <= 4:
        parser.error('live-limit must be 1..4')
    manifest, corpus, suite = load_frozen(args.suite)
    queries = [query for query in suite['queries'] if args.split == 'all' or query['split'] == args.split]
    if args.live_generation:
        selected = suite['live_subset_order'][:args.live_limit]
        queries = sorted((query for query in queries if query['id'] in selected), key=lambda query: selected.index(query['id']))
    if not queries:
        parser.error('No questions selected')
    args.output.mkdir(parents=True, exist_ok=True)
    summary_path = args.output / 'summary.json'
    if summary_path.exists() or (args.output / 'results.jsonl').exists():
        parser.error('Output already has results; use a new directory to preserve prior runs')
    documents = {document['id']: document for document in corpus['documents']}
    request_documents = [{key: document[key] for key in ('id', 'title', 'text', 'source')} for document in corpus['documents']]
    mode = 'live' if args.live_generation else 'extractive'
    summary = {'schema_version': 1, 'started_at_utc': datetime.now(timezone.utc).isoformat(),
        'endpoint': args.endpoint, 'mode': mode, 'strategy_order': strategies,
        'question_order': [query['id'] for query in queries], 'workers': 1,
        'input_manifest': manifest, 'manifest_sha256': sha((args.suite / 'manifest.json').read_bytes()),
        'runtime_file_hashes_before': {name: sha((ROOT / name).read_bytes()) for name in
            ['scripts/evaluate_workspace_rag.py', 'lablens/rag.py', 'lablens/rag_api.py'] if (ROOT / name).exists()},
        'limits': ['No retry, replacement draws or hidden warm-up.',
            'Fixed sequential strategy order; shared server document cache; not a controlled latency comparison.',
            'Known reported token subtotals only; unknown usage is separately counted; no price or billing claims.',
            'Same-assistant-authored labels; no independent human semantic review.',
            'One DPO experiment project and its LabLens documentation, not independent external datasets.'],
        'records': [], 'groups': []}
    log = (args.output / 'results.jsonl').open('w', encoding='utf-8')
    try:
        with httpx.Client(trust_env=False, timeout=args.timeout, follow_redirects=False) as client:
            # Read configuration metadata only; the status endpoint sends no model requests.
            try:
                metadata = client.get(args.endpoint.rsplit('/', 1)[0] + '/status')
                summary['provider_configuration'] = metadata.json() if metadata.is_success else {'status': 'unavailable', 'http_status': metadata.status_code}
            except (httpx.HTTPError, ValueError):
                summary['provider_configuration'] = {'status': 'unavailable'}
            for strategy in strategies:
                for query in queries:
                    request = {'question': query['question'], 'strategy': strategy, 'mode': mode,
                        'top_k': 5, 'documents': request_documents,
                        'snapshot_id': 'workspace-rag-public-v1:' + manifest['files']['corpus.json']}
                    started = time.perf_counter()
                    http_status = None
                    try:
                        response = client.post(args.endpoint, json=request)
                        http_status = response.status_code
                        try:
                            body = response.json()
                        except ValueError:
                            body = {'status': 'invalid_response', 'error': 'Endpoint did not return JSON'}
                        if isinstance(body, dict) and isinstance(body.get('detail'), dict):
                            body = body['detail']
                        if not isinstance(body, dict):
                            body = {'status': 'invalid_response', 'error': 'Endpoint response was not an object'}
                        if response.status_code >= 400 and 'status' not in body:
                            body = {'status': 'http_error', 'error': f'HTTP {response.status_code}'}
                        if 'status' not in body:
                            body = {'status': 'invalid_response', 'error': 'Missing status'}
                    except httpx.HTTPError as error:
                        body = {'status': 'transport_error', 'error_type': type(error).__name__,
                                'error': 'HTTP transport failed; provider completion and billing may be unknown'}
                    elapsed = round((time.perf_counter() - started) * 1000, 3)
                    record = {'query_id': query['id'], 'split': query['split'], 'category': query['category'],
                        'question': query['question'], 'answerable': query['answerable'],
                        'relevant_document_ids': query['relevant_document_ids'],
                        'expected_evidence': query['expected_evidence'], 'strategy': strategy, 'mode': mode,
                        'http_status': http_status, 'client_latency_ms': elapsed,
                        'embedding_cache_condition': cache_condition(body, strategy),
                        'response': body, 'score': score(query, body, documents)}
                    summary['records'].append(record)
                    log.write(json.dumps(record, ensure_ascii=False) + '\n')
                    log.flush()
                    print(json.dumps({'query': query['id'], 'split': query['split'], 'strategy': strategy,
                        'status': body['status'], 'recall': record['score']['document_recall_at_5'],
                        'elapsed_ms': elapsed}), flush=True)
    finally:
        log.close()
        summary['completed_at_utc'] = datetime.now(timezone.utc).isoformat()
        summary['groups'] = aggregate(summary['records'])
        summary['runtime_file_hashes_after'] = {name: sha((ROOT / name).read_bytes()) for name in summary['runtime_file_hashes_before'] if (ROOT / name).exists()}
        summary['runtime_files_unchanged'] = summary['runtime_file_hashes_before'] == summary['runtime_file_hashes_after']
        summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + '\n')
        (args.output / 'report.md').write_text(markdown(summary))
    incomplete = any(not row['score']['available'] or row['score']['status'] == 'invalid_citations' for row in summary['records'])
    return 2 if incomplete else 0


if __name__ == '__main__':
    sys.exit(main())
