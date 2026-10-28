"""Scoring guards use tiny authored fixtures, never the frozen benchmark queries."""
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

spec = spec_from_file_location('workspace_evaluator', Path(__file__).resolve().parents[2] / 'scripts/evaluate_workspace_rag.py')
evaluator = module_from_spec(spec)
spec.loader.exec_module(evaluator)


def fixtures():
    document = {'id': 'doc:a', 'title': 'Example', 'source': 'fixture/source', 'text': 'alpha beta'}
    hit = {'chunk_id': 'chunk:a', 'document_id': 'doc:a', 'title': 'Example',
           'source': 'fixture/source', 'text': 'alpha', 'start': 0, 'end': 5,
           'content_sha256': evaluator.sha(b'alpha'), 'rank': 1}
    query = {'answerable': True, 'relevant_document_ids': ['doc:a', 'doc:b']}
    response = {'status': 'excerpts', 'retrieval': {'hits': [hit]}, 'citations': [hit]}
    return document, hit, query, response


def test_multiple_chunks_do_not_inflate_document_recall():
    document, hit, query, response = fixtures()
    response['retrieval']['hits'].append({**hit, 'chunk_id': 'chunk:other', 'rank': 2})
    result = evaluator.score(query, response, {document['id']: document})
    assert result['document_recall_at_5'] == .5
    assert result['mrr_at_5'] == 1
    assert result['missing_document_ids'] == ['doc:b']


def test_unavailable_is_zero_not_perfect_or_removed():
    document, hit, query, response = fixtures()
    response['status'] = 'unavailable'
    result = evaluator.score(query, response, {document['id']: document})
    assert result['document_recall_at_5'] == 0
    assert result['mrr_at_5'] == 0
    assert not result['available']
    assert not result['abstained']


def test_only_top_five_chunks_count_even_if_sixth_is_relevant():
    document, hit, query, response = fixtures()
    response['retrieval']['hits'] = [{'document_id': 'irrelevant'}] * 5 + [hit]
    response['citations'] = []
    result = evaluator.score(query, response, {document['id']: document})
    assert result['document_recall_at_5'] == 0
    assert result['mrr_at_5'] == 0


def test_literal_source_check_is_not_semantic_faithfulness():
    document, hit, query, response = fixtures()
    response['answer'] = 'An unsupported claim would not be judged by this metric.'
    result = evaluator.score(query, response, {document['id']: document})
    assert result['citations']['valid'] == 1
    assert result['semantic_answer_correctness'] == 'not_assessed'


def test_fabricated_source_rejected_even_with_valid_chunk_id():
    document, hit, query, response = fixtures()
    response['citations'] = [{**hit, 'source': 'fabricated/source'}]
    result = evaluator.score(query, response, {document['id']: document})
    assert result['citations']['valid'] == 0
    assert 'citation_source_inconsistency' in result['failure_reasons']


def test_rehashed_wrong_excerpt_rejected():
    document, hit, query, response = fixtures()
    bad = {**hit, 'text': 'wrong', 'content_sha256': evaluator.sha(b'wrong')}
    response['retrieval']['hits'] = [bad]
    response['citations'] = [bad]
    result = evaluator.score(query, response, {document['id']: document})
    assert result['citations']['valid'] == 0


def test_refusal_scored_separately_from_extracts_on_unanswerable():
    document, hit, query, response = fixtures()
    query = {'answerable': False, 'relevant_document_ids': []}
    result = evaluator.score(query, response, {document['id']: document})
    assert result['document_recall_at_5'] is None
    assert not result['abstained']
    assert 'returned_output_on_unanswerable_question' in result['failure_reasons']
    response.update(status='refused', citations=[])
    result = evaluator.score(query, response, {document['id']: document})
    assert result['abstained']
    assert not result['failure_reasons']


def test_unknown_offsets_rejected_without_exception():
    document, hit, query, response = fixtures()
    response['citations'] = [{**hit, 'start': True}]
    result = evaluator.score(query, response, {document['id']: document})
    assert result['citations']['valid'] == 0
