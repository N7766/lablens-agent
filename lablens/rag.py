"""Bounded document retrieval with source-preserving excerpts and optional generation.

BM25/extractive is fully local. Dense vectors are obtained from a real configured
embedding provider; there is deliberately no synthetic embedding fallback.
"""
from collections import Counter, OrderedDict
import hashlib
import json
import math
import os
import re
import threading
import time
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse

from dotenv import load_dotenv
import httpx
from pydantic import Field, model_validator

from .schemas import StrictModel
from .runtime_telemetry import observe_request, request_body_bytes
from .assessment import (SELECTION_INSTRUCTIONS, validate_assessments, evidence_catalog,
                         selection_schema, resolve_selections)
from .comparison_context import build_review_context, review_instructions


CHUNK_SIZE = 800
CHUNK_OVERLAP = 120
CACHE_CAPACITY = 512
# Capture once at import so evaluation can distinguish loaded code from files edited later.
LOADED_IMPLEMENTATION_SHA256 = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
LOADED_ASSESSMENT_SHA256 = hashlib.sha256((Path(__file__).parent / 'assessment.py').read_bytes()).hexdigest()
_VECTOR_CACHE: OrderedDict = OrderedDict()
_CACHE_LOCK = threading.Lock()


class RAGDocument(StrictModel):
    id: str = Field(min_length=1, max_length=160)
    title: str = Field(min_length=1, max_length=300)
    text: str = Field(min_length=1, max_length=200_000)
    source: str = Field(min_length=1, max_length=1000)

    @model_validator(mode='after')
    def nonblank(self):
        if any(not getattr(self, key).strip() for key in ('id', 'title', 'text', 'source')):
            raise ValueError('Document fields must not be blank')
        for key in ('id', 'title', 'text', 'source'):
            try:
                getattr(self, key).encode('utf-8')
            except UnicodeEncodeError:
                raise ValueError('Document fields must contain valid Unicode scalar values') from None
        return self


class RAGRequest(StrictModel):
    question: str = Field(min_length=1, max_length=2000)
    strategy: Literal['bm25', 'dense', 'hybrid'] = 'bm25'
    mode: Literal['extractive', 'live'] = 'extractive'
    top_k: int = Field(default=5, ge=1, le=10)
    documents: list[RAGDocument] = Field(max_length=100)
    snapshot_id: str = Field(min_length=1, max_length=200)

    @model_validator(mode='after')
    def bounded_corpus(self):
        if not self.question.strip() or not self.snapshot_id.strip():
            raise ValueError('Question and snapshot_id must not be blank')
        try:
            (self.question + self.snapshot_id).encode('utf-8')
        except UnicodeEncodeError:
            raise ValueError('Question and snapshot_id must contain valid Unicode scalar values') from None
        if len({document.id for document in self.documents}) != len(self.documents):
            raise ValueError('Document IDs must be unique within a snapshot')
        if sum(len(document.text) for document in self.documents) > 200_000:
            raise ValueError('Total document text must not exceed 200000 characters')
        return self


class RAGError(RuntimeError):
    def __init__(self, message, status='provider_error'):
        super().__init__(message)
        self.status = status


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


def documents_sha256(documents: list[RAGDocument]) -> str:
    # Order is part of the snapshot, including tie-breaking order.
    return _sha(json.dumps([document.model_dump() for document in documents],
                           ensure_ascii=False, sort_keys=True, separators=(',', ':')))


def chunk_documents(documents: list[RAGDocument]) -> list[dict]:
    chunks = []
    for document in documents:
        start = 0
        while start < len(document.text):
            end = min(start + CHUNK_SIZE, len(document.text))
            raw = document.text[start:end]
            left = len(raw) - len(raw.lstrip())
            right = len(raw.rstrip())
            if right > left:
                actual_start, actual_end = start + left, start + right
                text = document.text[actual_start:actual_end]
                identity = json.dumps([document.id, document.title, document.source,
                                       actual_start, actual_end, text], ensure_ascii=False)
                chunks.append(dict(chunk_id='chunk:' + _sha(identity)[:24],
                                   document_id=document.id, title=document.title,
                                   source=document.source, text=text,
                                   start=actual_start, end=actual_end,
                                   content_sha256=_sha(text)))
            if end == len(document.text):
                break
            start = end - CHUNK_OVERLAP
    return chunks


def tokenize(text: str) -> list[str]:
    """Bilingual domain normalization; source text/offsets remain unchanged."""
    from .lexical import tokens
    return tokens(text)


def bm25_scores(question: str, chunks: list[dict]) -> list[float]:
    terms = set(tokenize(question))
    documents = [Counter(tokenize(chunk['title'] + '\n' + chunk['text'])) for chunk in chunks]
    lengths = [sum(document.values()) for document in documents]
    average = sum(lengths) / len(lengths) if lengths else 0
    if not average or not terms:
        return [0.0] * len(chunks)
    frequencies = {term: sum(term in document for document in documents) for term in terms}
    result = []
    for document, length in zip(documents, lengths):
        score = 0.0
        for term in terms:
            frequency = document[term]
            if frequency:
                inverse = math.log(1 + (len(chunks) - frequencies[term] + .5) / (frequencies[term] + .5))
                score += inverse * frequency * 2.5 / (frequency + 1.5 * (.25 + .75 * length / average))
        result.append(score)
    return result


def _endpoint(base: str) -> str:
    try:
        parsed = urlparse(base)
    except ValueError:
        raise RAGError('Provider URL is invalid', 'unavailable') from None
    if (not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment
            or (parsed.scheme != 'https' and not (parsed.scheme == 'http' and parsed.hostname in {'localhost', '127.0.0.1', '::1'}))):
        raise RAGError('Provider URL must use HTTPS (or loopback HTTP) without credentials, query or fragment', 'unavailable')
    try:
        parsed.port
    except ValueError:
        raise RAGError('Provider URL has an invalid port', 'unavailable') from None
    return base.rstrip('/')


def _origin(base: str):
    parsed = urlparse(base)
    return parsed.scheme, parsed.hostname, parsed.port or (443 if parsed.scheme == 'https' else 80)


def provider_config(kind: str, *, deadline_started: float | None = None) -> dict:
    load_dotenv()
    prefix = 'EMBEDDING' if kind == 'embedding' else 'LLM'
    base = os.getenv(prefix + '_BASE_URL', '')
    model = os.getenv(prefix + '_MODEL', '')
    key = os.getenv(prefix + '_API_KEY', '')
    if not base or not model:
        raise RAGError(f'Set {prefix}_BASE_URL and {prefix}_MODEL; no provider request was sent', 'unavailable')
    base = _endpoint(base)
    if kind == 'embedding' and not key:
        llm_base = os.getenv('LLM_BASE_URL', '')
        if llm_base and _origin(base) == _origin(_endpoint(llm_base)):
            key = os.getenv('LLM_API_KEY', '')
    if not key:
        raise RAGError(f'Set {prefix}_API_KEY; no provider request was sent', 'unavailable')
    try:
        timeout = float(os.getenv('RAG_TIMEOUT_SECONDS', '45'))
        total_seconds = float(os.getenv('RAG_TOTAL_SECONDS', '80'))
        batch_size = int(os.getenv('EMBEDDING_BATCH_SIZE', '32'))
        if (not math.isfinite(timeout) or not 1 <= timeout <= 180
                or not math.isfinite(total_seconds) or not 1 <= total_seconds <= 180
                or not 1 <= batch_size <= 64):
            raise ValueError
    except ValueError:
        raise RAGError('RAG per-call and total timeouts must be 1..180 seconds and embedding batch size 1..64', 'unavailable') from None
    deadline = (time.monotonic() if deadline_started is None else deadline_started) + total_seconds
    return dict(base=base, model=model, key=key, timeout=timeout, batch_size=batch_size,
                revision=os.getenv('EMBEDDING_MODEL_REVISION', ''),
                deadline=deadline, total_seconds=total_seconds)


def rag_status() -> dict:
    result = {'bm25': {'available': True}, 'extractive': {'available': True},
              'embedding_cache': {'capacity': CACHE_CAPACITY, 'scope': 'process-memory; vectors only'},
              'limits': {'documents': 100, 'text_characters': 200000, 'top_k': 10},
              'semantic_fact_check': False,
              'assessment_contract': 'claim-verdict-v1',
              'loaded_assessment_sha256': LOADED_ASSESSMENT_SHA256,
              'loaded_implementation_sha256': LOADED_IMPLEMENTATION_SHA256}
    for name, kind in [('dense', 'embedding'), ('live', 'generation')]:
        try:
            config = provider_config(kind)
            result[name] = dict(available=True, model=config['model'],
                                total_budget_seconds=config['total_seconds'],
                                availability_scope='configuration only; provider not contacted')
        except RAGError as exc:
            result[name] = dict(available=False, reason=str(exc))
    result['hybrid'] = dict(result['dense'])
    return result


def clear_embedding_cache():
    with _CACHE_LOCK:
        _VECTOR_CACHE.clear()


def _new_usage():
    return dict(model_calls=0, embedding_requests=0, embedded_inputs=0,
                embedding_cache_hits=0, embedding_tokens=0,
                generation_tokens={'prompt_tokens': 0, 'completion_tokens': 0, 'total_tokens': 0},
                provider_usage_unknown=False, budget_exhausted=False)


def _record_usage(body: dict, usage: dict, kind: str):
    reported = body.get('usage')
    reported = reported if isinstance(reported, dict) else {}
    if kind == 'embedding':
        tokens = reported.get('total_tokens')
        if type(tokens) is not int or tokens < 0:
            usage['embedding_tokens'] = None
            usage['provider_usage_unknown'] = True
        elif usage['embedding_tokens'] is not None:
            usage['embedding_tokens'] += tokens
    else:
        for key in usage['generation_tokens']:
            value = reported.get(key)
            previous = usage['generation_tokens'][key]
            usage['generation_tokens'][key] = previous + value if previous is not None and type(value) is int and value >= 0 else None
        if any(value is None for value in usage['generation_tokens'].values()):
            usage['provider_usage_unknown'] = True


def _post(config, path, payload, usage, kind):
    from .cancellation import check_cancelled
    check_cancelled()
    remaining = config['deadline'] - time.monotonic()
    if remaining <= 0:
        usage['budget_exhausted'] = True
        raise RAGError('Total RAG request budget exhausted before the next provider call; no further request was sent')
    if kind == 'embedding':
        usage['embedding_requests'] += 1
        usage['embedded_inputs'] += len(payload['input'])
    else:
        usage['model_calls'] += 1
    try:
        with observe_request(payload, min(config['timeout'], remaining)) as observation:
            response = httpx.post(config['base'] + path, json=payload,
                                  headers={'Authorization': 'Bearer ' + config['key']},
                                  timeout=min(config['timeout'], remaining), follow_redirects=False)
            observation.received(response)
            response.raise_for_status()
            body = response.json()
            if not isinstance(body, dict):
                raise ValueError
            observation.received(response, body)
            _record_usage(body, usage, kind)
            check_cancelled()
            if time.monotonic() >= config['deadline']:
                usage['budget_exhausted'] = True
                raise RAGError('Total RAG request budget exhausted after the provider response; no further request was sent')
            return body
    except httpx.TimeoutException:
        _unknown_usage(usage, kind)
        check_cancelled()
        usage['budget_exhausted'] = time.monotonic() >= config['deadline']
        raise RAGError('Provider request timed out; billing is unknown. No automatic retry was sent') from None
    except httpx.HTTPStatusError as exc:
        _unknown_usage(usage, kind)
        check_cancelled()
        raise RAGError(f'Provider returned HTTP {exc.response.status_code}; no automatic retry was sent') from None
    except (httpx.HTTPError, ValueError, TypeError):
        _unknown_usage(usage, kind)
        check_cancelled()
        raise RAGError('Provider request failed or returned invalid JSON; no automatic retry was sent') from None


def _unknown_usage(usage, kind):
    usage['provider_usage_unknown'] = True
    if kind == 'embedding':
        usage['embedding_tokens'] = None
    else:
        usage['generation_tokens'] = {key: None for key in usage['generation_tokens']}


def _vectors(body: dict, expected: int) -> list[list[float]]:
    data = body.get('data')
    if not isinstance(data, list) or len(data) != expected:
        raise RAGError('Embedding response count does not match the requested inputs')
    vectors = [None] * expected
    dimension = None
    for item in data:
        if not isinstance(item, dict):
            raise RAGError('Embedding response item must be an object')
        index, values = item.get('index'), item.get('embedding')
        if type(index) is not int or not 0 <= index < expected or vectors[index] is not None:
            raise RAGError('Embedding response indices must be unique and cover every input')
        if not isinstance(values, list) or not 1 <= len(values) <= 4096:
            raise RAGError('Embedding dimensions must be between 1 and 4096')
        try:
            finite = all(type(value) in (float, int) and math.isfinite(value) for value in values)
        except OverflowError:
            finite = False
        if not finite:
            raise RAGError('Embedding values must be finite numbers')
        if dimension is not None and len(values) != dimension:
            raise RAGError('Embedding response dimensions are inconsistent')
        dimension = len(values)
        norm = math.hypot(*values)
        if not norm or not math.isfinite(norm):
            raise RAGError('Embedding vector must have a finite, nonzero norm')
        vectors[index] = [value / norm for value in values]
    return vectors


def dense_scores(question: str, chunks: list[dict], usage: dict, config: dict) -> list[float]:
    fingerprint = (config['base'], config['model'], config['revision'], _sha(config['key']))
    texts = [chunk['title'] + '\n' + chunk['text'] for chunk in chunks]
    keys = [(*fingerprint, _sha(text)) for text in texts]
    vectors = [None] * len(chunks)
    missing = []
    with _CACHE_LOCK:
        for index, key in enumerate(keys):
            if key in _VECTOR_CACHE:
                vectors[index] = _VECTOR_CACHE[key]
                _VECTOR_CACHE.move_to_end(key)
                usage['embedding_cache_hits'] += 1
            else:
                missing.append(index)
    for offset in range(0, len(missing), config['batch_size']):
        indices = missing[offset:offset + config['batch_size']]
        body = _post(config, '/embeddings',
                     {'model': config['model'], 'input': [texts[index] for index in indices], 'encoding_format': 'float'},
                     usage, 'embedding')
        received = _vectors(body, len(indices))
        with _CACHE_LOCK:
            for index, vector in zip(indices, received):
                vectors[index] = vector
                _VECTOR_CACHE[keys[index]] = vector
                _VECTOR_CACHE.move_to_end(keys[index])
            while len(_VECTOR_CACHE) > CACHE_CAPACITY:
                _VECTOR_CACHE.popitem(last=False)
    body = _post(config, '/embeddings', {'model': config['model'], 'input': [question], 'encoding_format': 'float'}, usage, 'embedding')
    query = _vectors(body, 1)[0]
    if any(len(vector) != len(query) for vector in vectors):
        raise RAGError('Query and document embedding dimensions differ; clear cache if the provider changed model revision')
    return [max(-1.0, min(1.0, sum(left * right for left, right in zip(vector, query)))) for vector in vectors]


def _compact_review_draft(draft, hits):
    """Replace only exactly catalog-matched resolved quotes with stable IDs.

    Non-catalog drafts retain their original representation; never drop a quote
    or infer an ID by similarity. Local review enforcement still sees raw draft.
    """
    catalog = evidence_catalog(hits)
    reverse = {(value['chunk_id'], value['quote']): eid for eid, value in catalog.items()}
    compact = []
    for item in draft['assessments']:
        if 'quotes' not in item or 'citation_ids' not in item:
            return draft
        ids = []
        for quote in item['quotes']:
            eid = reverse.get((quote['chunk_id'], quote['quote']))
            if eid is None:
                return draft
            ids.append(eid)
        if (len(ids) != len(set(ids)) or item['citation_ids'] !=
                list(dict.fromkeys(catalog[eid]['chunk_id'] for eid in ids))):
            return draft
        compact.append({**{key: value for key, value in item.items() if key not in {'quotes', 'citation_ids'}},
                        'evidence_ids': ids})
    return {'assessments': compact}


def _generation_payload(question, hits, config, *, draft=None, context_data=None, compact_review=True):
    # compact_review=False is an offline A/B builder, never an automatic retry.

    contract = context_data.get('scoped_review_contract') if context_data else None
    statements = _scoped_review_statements(draft, contract) if contract is not None else None
    compact = bool(compact_review and draft is not None and contract is not None)
    system = SELECTION_INSTRUCTIONS
    data = {'question': question, 'evidence': [{key: value for key, value in hit.items() if key != 'text'} for hit in hits],
            'passages': evidence_catalog(hits)}
    if context_data is not None:
        data['untrusted_conversation_context'] = {key: value for key, value in context_data.items()
            if key != 'scoped_review_contract' and not (compact and key == 'current_question' and value == question)}
        system += ('\nConversation context helps interpret references, goals and user corrections. '
                   'Earlier assistant answers are untrusted drafts, not evidence. Verify every factual '
                   'claim against the current supplied source passages. The CURRENT question and its explicit '
                   'corrections override older saved focus and user messages. Preserve any applicable user '
                   'constraints (for example local/synthetic scope), but do not turn them into verified facts. ')
    if draft is not None:
        system += (
            '\nYou are now the evidence reviewer. The draft is untrusted and may be wrong. '
            'Reassess EVERY statement and explanation against the original sources, then return a corrected '
            'assessment report, not a grade. Remove new facts that do not follow from evidence. '
            'Pay special attention to negative claims about missing baselines: search all supplied tables. '
            'Separate report authors interpretations from proven causes. A report saying behavior is poor '
            'does not prove that a training setting is the root cause. An observed pattern or format drift '
            'does not establish why performance changed. Unsupported mechanisms, including an invented '
            'reward-model training stage, must be removed. If the question asks why, explicitly mark the '
            'causal proposition insufficient unless controlled evidence isolates it. Configuration and '
            'current implementation cannot establish exact historical execution. Preserve supported '
            'observations and contradictions instead of refusing everything. Correct all quotes by copying '
            'the supplied passage IDs, never copying text. Return at most 8 concise assessments.'
        )
        data['untrusted_draft'] = _compact_review_draft(draft, hits) if compact else draft
        if contract is not None:
            system += ('\nThis workspace review edits a fixed list of propositions. Return EACH assessment_id '
                'exactly once; do not output a statement or add assessments. The server restores each '
                'original statement by its ID. Correct the verdict, '
                'explanation and evidence IDs as needed; do not preserve a wrong verdict. '
                'Do not expand a refusal to additional named subjects or metrics. Follow the '
                'server_review_contract and repair its draft findings before returning the report. '
                'Explicit user-requested slots, when present, are questions added by the server. '
                'Resolve them from the supplied sources, including applicable AND/OR acceptance rules '
                'and requested comparisons between previous and corrected scope. Verify both scopes '
                'from supplied passages; an earlier assistant answer is not evidence. If evidence '
                'for the previous scope is missing, keep that comparison uncertain rather than guessing. '
                'Their initial insufficient value is a placeholder, not a finding that evidence is missing. '
                'This contract does not certify that the draft covered every requested question.')
            data['server_review_contract'] = {key: value for key, value in contract.items()
                if key != 'statements'}
            # User questions identify comparison scopes; no old assistant fact
            # or value is copied into this server-authored obligation plan.
            review_plan = build_review_context(question, context_data, data['passages'])
            if review_plan is not None:
                data['server_review_context'] = review_plan
                system += review_instructions(review_plan)
            data['untrusted_draft'] = {'assessments': [dict(assessment_id=f'a{index + 1}', **item)
                for index, item in enumerate(data['untrusted_draft']['assessments'])]}
            if compact:
                system += (' The exact ID-to-statement mapping appears only in untrusted_draft; '
                    'catalog IDs in the draft resolve to the complete original passages supplied above.')
            system += (' In this scoped response, replace the statement field described earlier with '
                'assessment_id. Return only assessment_id, verdict, explanation, basis and evidence_ids '
                'for each row; do not repeat the statement text.')
    return {'model': config['model'], 'temperature': 0, 'max_tokens': 2600,
            'response_format': {'type': 'json_schema', 'json_schema': {
                'name': 'evidence_review_by_id' if statements is not None else 'evidence_assessments', 'strict': True,
                'schema': _scoped_selection_schema(hits, statements) if statements is not None else selection_schema(hits)}},
            'messages': [{'role': 'system', 'content': system},
                         {'role': 'user', 'content': json.dumps(data, ensure_ascii=False)}]}


def _scoped_review_statements(draft, contract):
    """Bind short IDs to one validated immutable draft before requesting review."""
    if not isinstance(draft, dict) or not isinstance(contract, dict):
        raise ValueError('Scoped review requires an original draft and contract.')
    rows = draft.get('assessments')
    statements = contract.get('statements')
    if (not isinstance(rows, list) or not isinstance(statements, list)
            or not 1 <= len(rows) <= 8 or len(rows) != len(statements)
            or any(not isinstance(s, str) or not s.strip() for s in statements)
            or len(set(statements)) != len(statements)
            or any(not isinstance(row, dict) or row.get('statement') != statement
                   for row, statement in zip(rows, statements))):
        raise ValueError('Scoped review contract must match every original statement in order.')
    return list(statements)


def _scoped_selection_schema(hits, statements):
    schema = selection_schema(hits)
    row = schema['$defs']['AssessmentSelection']
    del row['properties']['statement']
    row['properties']['assessment_id'] = {'type': 'string',
        'enum': [f'a{index + 1}' for index in range(len(statements))]}
    row['required'] = ['assessment_id' if name == 'statement' else name for name in row['required']]
    schema['properties']['assessments'].update(minItems=len(statements), maxItems=len(statements))
    return schema


def _restore_review_statements(reviewed, statements):
    """Reject incomplete/expanded IDs; restore exact originals before old checks.

    A string ID is deliberately not an integer index: booleans, floats and
    numeric strings cannot address a row through Python's numeric equality.
    Order may differ in the response, but the published report uses draft order.
    """
    if not isinstance(reviewed, dict) or set(reviewed) != {'assessments'}:
        raise ValueError('Scoped review must contain only assessments.')
    rows = reviewed['assessments']
    if not isinstance(rows, list) or len(rows) != len(statements):
        raise ValueError('Scoped review must return every assessment ID exactly once.')
    original = {f'a{index + 1}': statement for index, statement in enumerate(statements)}
    by_id = {}
    expected_keys = {'assessment_id', 'verdict', 'explanation', 'basis', 'evidence_ids'}
    for row in rows:
        if not isinstance(row, dict) or set(row) != expected_keys:
            raise ValueError('Scoped review rows cannot add or replace statement fields.')
        identity = row['assessment_id']
        if type(identity) is not str or identity not in original or identity in by_id:
            raise ValueError('Scoped review returned an unknown or duplicate assessment ID.')
        by_id[identity] = dict(statement=original[identity],
            **{key: value for key, value in row.items() if key != 'assessment_id'})
    if set(by_id) != set(original):
        raise ValueError('Scoped review omitted an assessment ID.')
    return {'assessments': [by_id[identity] for identity in original]}


def _generation_json(body, *, observer=None, phase='draft'):
    try:
        choice = body['choices'][0]
        if observer is not None:
            observer({'phase': phase, 'finish_reason': choice.get('finish_reason'),
                      'content': choice['message'].get('content')})
        if choice.get('finish_reason') != 'stop':
            raise RAGError('Generation was incomplete; answer withheld', 'invalid_citations')
        return json.loads(choice['message']['content'])
    except (KeyError, IndexError, TypeError, ValueError):
        raise RAGError('Generation returned invalid JSON; answer withheld', 'invalid_citations') from None


def review_assessments(question, raw, hits, usage, config, *, observer=None, context_guard=None, context_data=None):
    """One planned review call, sharing budget/usage with the draft. No retry."""
    payload = _generation_payload(question, hits, config, draft=raw, context_data=context_data)
    if context_guard is not None:
        context_guard(payload)
    body = _post(config, '/chat/completions', payload, usage, 'generation')
    reviewed = _generation_json(body, observer=observer, phase='review')
    try:
        contract = context_data.get('scoped_review_contract') if context_data else None
        selected = (_restore_review_statements(reviewed, _scoped_review_statements(raw, contract))
                    if contract is not None else reviewed)
        resolved = resolve_selections(selected, hits)
        quality = None
        if context_data and context_data.get('scoped_review_contract'):
            from .answer_quality import enforce_review_checks
            resolved, quality = enforce_review_checks(question, raw, resolved)
        result = validate_assessments(question, resolved, hits)
        if quality is not None:
            result['assessment_validation']['selected_evidence_checks'] = quality
    except (TypeError, ValueError):
        raise RAGError('Reviewed assessments failed schema or exact quotation checks; answer withheld', 'invalid_citations') from None
    result['generation_review'] = dict(calls=1, model=config['model'],
        scope='Same-provider model review followed by mechanical checks; not independent human review or proof of entailment',
        payload_format='scoped-review-ids-v2' if context_data and context_data.get('scoped_review_contract') else 'full-review-v1',
        request_body_bytes=request_body_bytes(payload),
        draft_sha256=_sha(json.dumps(raw, ensure_ascii=False, sort_keys=True)),
        reviewed_sha256=_sha(json.dumps(reviewed, ensure_ascii=False, sort_keys=True)))
    review_plan = json.loads(payload['messages'][1]['content']).get('server_review_context')
    if review_plan is not None:
        result['generation_review']['review_context'] = {
            key: review_plan[key] for key in ('version', 'implementation_sha256',
                'available_passage_count', 'available_passage_ids', 'minimum_evidence_requested',
                'scope_resolution_verified', 'evidence_coverage_verified', 'semantic_correctness_verified')}
        if 'comparison' in review_plan:
            result['generation_review']['review_context'].update(
                requested_comparison_items=review_plan['comparison']['requested_items'],
                previous_scope_status=review_plan['comparison']['previous_scope_status'])
    return result


def _generate(question, hits, usage, config, *, observer=None):
    body = _post(config, '/chat/completions', _generation_payload(question, hits, config), usage, 'generation')
    raw = _generation_json(body, observer=observer)
    return review_assessments(question, raw, hits, usage, config, observer=observer)


def _citation(hit):
    return {key: hit[key] for key in ('chunk_id', 'document_id', 'title', 'source', 'text', 'start', 'end', 'content_sha256')}


def answer_rag(request: RAGRequest, *, budget_seconds: float | None = None) -> dict:
    from .cancellation import check_cancelled, ExecutionCancelled
    started = time.perf_counter()
    deadline_started = time.monotonic()
    if budget_seconds is not None and (not math.isfinite(budget_seconds) or not 0 < budget_seconds <= 180):
        raise ValueError('Remaining budget must be finite and in (0, 180] seconds')
    def bounded_config(kind):
        config = provider_config(kind, deadline_started=deadline_started)
        if budget_seconds is not None:
            config['deadline'] = min(config['deadline'], deadline_started + budget_seconds)
        return config
    usage = _new_usage()
    from .lexical import expansion_metadata
    chunks = chunk_documents(request.documents)
    result = dict(status='no_hits', answer='', citations=[], claims=[], usage=usage,
                  mode=request.mode,
                  retrieval=dict(strategy=request.strategy, snapshot_id=request.snapshot_id,
                                 documents_sha256=documents_sha256(request.documents), hits=[],
                                 document_count=len(request.documents), chunk_count=len(chunks),
                                 chunk_size=CHUNK_SIZE, chunk_overlap=CHUNK_OVERLAP,
                                 index_kind='request-local exact scan', latency_ms=0.0),
                  citation_validation={'scope': 'IDs and exact source excerpts only', 'semantic_fact_check': False},
                  limitations=['Source labels are supplied metadata, not authenticated sources.',
                               'Citation validation checks membership, not whether a generated claim is true.',
                               'Dense similarity and RRF scores are rankings, not calibrated answerability probabilities.'])
    if request.strategy in {'bm25', 'hybrid'}:
        result['retrieval']['lexical_normalization'] = expansion_metadata(request.question)
    try:
        check_cancelled()
        if not chunks:
            result['answer'] = 'No documents are available for retrieval.'
            return result
        generation = bounded_config('generation') if request.mode == 'live' else None
        lexical = bm25_scores(request.question, chunks) if request.strategy in {'bm25', 'hybrid'} else [0.0] * len(chunks)
        semantic = dense_scores(request.question, chunks, usage, bounded_config('embedding')) if request.strategy in {'dense', 'hybrid'} else None
        if request.strategy == 'bm25':
            scores = lexical
            indices = [index for index in range(len(chunks)) if lexical[index] > 0]
        elif request.strategy == 'dense':
            scores = semantic
            indices = list(range(len(chunks)))
        else:
            scores = [0.0] * len(chunks)
            # RRF treats both rankings symmetrically; zero-match lexical entries do not contribute.
            for values, eligible in [(lexical, [i for i, value in enumerate(lexical) if value > 0]),
                                     (semantic, list(range(len(chunks))))]:
                for rank, index in enumerate(sorted(eligible, key=lambda i: (-values[i], i)), 1):
                    scores[index] += 1 / (60 + rank)
            indices = list(range(len(chunks)))
            result['retrieval']['rrf_k'] = 60
        selected = sorted(indices, key=lambda index: (-scores[index], index))[:request.top_k]
        hits = [dict(chunks[index], rank=rank, score=round(scores[index], 10),
                     bm25_score=round(lexical[index], 10) if request.strategy != 'dense' else None,
                     dense_score=round(semantic[index], 10) if semantic is not None else None)
                for rank, index in enumerate(selected, 1)]
        result['retrieval']['hits'] = hits
        result['retrieval']['latency_ms'] = round((time.perf_counter() - started) * 1000, 3)
        if not hits:
            result['answer'] = 'No lexical matches were found. This does not prove the documents lack an answer.'
        elif request.mode == 'extractive':
            result.update(status='excerpts', answer='Retrieved excerpts (no generated conclusion):\n\n' + '\n\n'.join('[' + hit['chunk_id'] + '] ' + hit['text'] for hit in hits),
                          citations=[_citation(hit) for hit in hits])
        else:
            result.update(_generate(request.question, hits, usage, generation))
        check_cancelled()
    except ExecutionCancelled:
        result.update(status='agent_cancelled', answer='', citations=[], claims=[], assessments=[],
                      error='Execution cancelled at a safe boundary; completed usage is preserved and no further provider request was sent.')
    except RAGError as exc:
        result.update(status=exc.status, answer='', error=str(exc), citations=[], claims=[])
    finally:
        result['latency_ms'] = round((time.perf_counter() - started) * 1000, 3)
        if not result['retrieval']['latency_ms']:
            result['retrieval']['latency_ms'] = result['latency_ms']
    return result
