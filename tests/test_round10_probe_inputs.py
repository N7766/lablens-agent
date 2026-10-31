"""Offline reconstruction fidelity and fail-closed input provenance checks."""
import copy
import importlib.util
import json
from pathlib import Path
import shutil

import pytest

REPO = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('round10_probe_inputs', REPO / 'scripts/prepare_review_probe_round10.py')
probe = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(probe)


@pytest.fixture(scope='module')
def bundle():
    return probe.prepare()


@pytest.fixture
def copied_run(tmp_path):
    """Copy only needed sealed inputs; never edit the historical directory."""
    run = tmp_path / 'raw'
    run.mkdir()
    original_seal = probe.read(probe.RUN / 'raw-seal.json')
    names = {'approval.json'}
    for turn_id in probe.TURN_IDS:
        scenario = turn_id.split('-')[0]
        names.update({f'{turn_id}-final-task.json', f'{turn_id}-conversation.json',
                      f'{scenario}-conversation-created.json', f'{scenario}-documents.json',
                      f'{turn_id}-source-before.json'})
    for name in names:
        shutil.copyfile(probe.RUN / name, run / name)
    (run / 'raw-seal.json').write_text(json.dumps(dict(files={name: original_seal['files'][name] for name in names})))
    return run


def change(run, name, edit, reseal=True):
    path = run / name
    data = probe.read(path)
    edit(data)
    path.write_text(json.dumps(data, ensure_ascii=False))
    if reseal:
        seal = probe.read(run / 'raw-seal.json')
        seal['files'][name] = probe.sha(path)
        (run / 'raw-seal.json').write_text(json.dumps(seal))


def test_original_review_bytes_reproduced_with_historical_sources(bundle):
    assert bundle['external_model_calls'] == 0
    assert bundle['formal_evaluation_freeze'] is False
    assert [case['turn_id'] for case in bundle['cases']] == ['cedar-t1', 'harbor-t1']
    for case, expected_bytes, expected_timeout in zip(bundle['cases'], [29533, 28064], [39968.432, 35098.544]):
        audit = case['byte_validation']
        assert audit['recorded_request_body_bytes'] == audit['reconstructed_request_body_bytes'] == expected_bytes
        assert audit['difference_bytes'] == 0
        assert audit['matches_recorded_length'] is True
        assert audit['original_wire_content_hash_available'] is False
        assert audit['exact_original_wire_identity_proven'] is False
        assert case['original_review_timeout_ms'] == expected_timeout
        assert case['provenance']['candidate_sha'] == '77b7a550e6a3390a2fdac6c5136a792a126d5631'
        assert case['provenance']['final_report_used_as_input'] is False
        assert case['provenance']['model_output_rewritten'] is False
        assert case['provenance']['original_tool_arguments_pointer'].endswith('/agent/trace/16/arguments')
        assert case['provenance']['review_request_pointer'].endswith('/agent/trace/21')


def test_payload_retains_exact_question_all_sources_and_pinned_context(bundle):
    for case in bundle['cases']:
        data = json.loads(case['original_payload']['messages'][1]['content'])
        assert data['question'] == case['question']
        assert len(data['evidence']) == len(case['hits'])
        assert data['untrusted_conversation_context']['pinned_state'] == case['context_data']['pinned_state']
        assert case['context_data']['recent_user_messages'] == []
        assert case['context_data']['untrusted_recent_answers'] == []
        assert data['server_review_delta']['version'] == 'scoped-review-delta-v3'
        assert case['original_payload']['max_tokens'] == 2600
        assert case['original_payload']['temperature'] == 0


def test_each_input_file_and_historical_python_file_has_real_hash(bundle):
    for case in bundle['cases']:
        for entry in case['provenance']['files'].values():
            assert probe.sha(REPO / entry['path']) == entry['sha256']
        files = bundle['historical_python_sources'][case['turn_id']]
        source = probe.read(REPO / case['provenance']['files']['source']['path'])
        assert all(value == source['source_files'][name] for name, value in files.items())
        assert 'lablens/rag.py' in files and 'lablens/review_delta.py' in files


def test_unsealed_task_change_rejected(copied_run):
    change(copied_run, 'cedar-t1-final-task.json', lambda d: d.update(error_message='changed'), reseal=False)
    with pytest.raises(ValueError, match='Sealed historical raw changed'):
        probe.prepare(run=copied_run)


def test_required_file_must_be_listed_in_seal(copied_run):
    seal = probe.read(copied_run / 'raw-seal.json')
    del seal['files']['cedar-documents.json']
    (copied_run / 'raw-seal.json').write_text(json.dumps(seal))
    with pytest.raises(ValueError, match='absent from raw seal'):
        probe.prepare(run=copied_run)


def test_historical_source_record_must_match_approval(copied_run):
    change(copied_run, 'cedar-t1-source-before.json', lambda d: d['source_files'].update({'lablens/rag.py': '0'*64}))
    with pytest.raises(ValueError, match='attestations disagree'):
        probe.prepare(run=copied_run)


def test_both_source_attestations_cannot_fake_git_source(copied_run):
    edit = lambda d: d['source_files'].update({'lablens/rag.py': '0'*64})
    for name in ['approval.json', 'cedar-t1-source-before.json', 'harbor-t1-source-before.json']:
        change(copied_run, name, edit)
    with pytest.raises(ValueError, match='Git source differs'):
        probe.prepare(run=copied_run)


def test_document_substitution_rejected(copied_run):
    change(copied_run, 'cedar-documents.json', lambda d: d['documents'][0].update(text='substituted rule'))
    with pytest.raises(ValueError, match='original Agent snapshot'):
        probe.prepare(run=copied_run)


def test_hit_substitution_rejected(copied_run):
    def edit(d):
        d['error_result']['agent']['trace'][8]['output']['hits'][0]['text'] = 'substituted source'
    change(copied_run, 'cedar-t1-final-task.json', edit)
    with pytest.raises(ValueError, match='snapshot or passage catalog mismatch'):
        probe.prepare(run=copied_run)


def test_coverage_substitution_rejected(copied_run):
    def edit(d):
        d['error_result']['agent']['trace'][18]['output']['fake_change'] = True
    change(copied_run, 'cedar-t1-final-task.json', edit)
    with pytest.raises(ValueError, match='Coverage reconstruction differs'):
        probe.prepare(run=copied_run)


def test_later_turns_not_silently_inferred(copied_run):
    change(copied_run, 'cedar-t1-final-task.json', lambda d: d.update(conversation_seq=2))
    with pytest.raises(ValueError, match='proven first-turn empty histories'):
        probe.prepare(run=copied_run)


def test_ambiguous_original_review_request_rejected(copied_run):
    def edit(d):
        trace = d['error_result']['agent']['trace']
        trace.append(copy.deepcopy(trace[21]))
    change(copied_run, 'cedar-t1-final-task.json', edit)
    with pytest.raises(ValueError, match='exactly one original review request'):
        probe.prepare(run=copied_run)


def test_final_answer_injection_is_never_used_as_draft(copied_run, bundle):
    def edit(d):
        d['error_result']['assessments'] = [{'statement': 'fake final report'}]
        d['error_result']['answer'] = 'fake final answer'
    change(copied_run, 'cedar-t1-final-task.json', edit)
    actual = probe.prepare(run=copied_run)['cases'][0]
    expected = bundle['cases'][0]
    assert actual['draft'] == expected['draft']
    assert actual['original_payload'] == expected['original_payload']


def test_byte_difference_reported_without_claiming_exact_reproduction(copied_run):
    def edit(d):
        d['error_result']['agent']['trace'][21]['request_body_bytes'] += 1
    change(copied_run, 'cedar-t1-final-task.json', edit)
    audit = probe.prepare(run=copied_run)['cases'][0]['byte_validation']
    assert audit['difference_bytes'] == -1
    assert audit['matches_recorded_length'] is False
    assert audit['exact_original_wire_identity_proven'] is False


def test_diagnostic_bundle_write_refuses_overwrite(tmp_path):
    path = tmp_path / 'probe.json'
    probe.write_bundle(path, {'offline': True})
    with pytest.raises(FileExistsError):
        probe.write_bundle(path, {'offline': False})
    assert probe.read(path) == {'offline': True}


def test_seal_path_traversal_rejected(copied_run):
    outside = copied_run.parent / 'outside.json'
    outside.write_text('{}')
    seal = probe.read(copied_run / 'raw-seal.json')
    seal['files']['../outside.json'] = probe.sha(outside)
    (copied_run / 'raw-seal.json').write_text(json.dumps(seal))
    with pytest.raises(ValueError, match='Sealed historical raw changed'):
        probe.verify_raw(copied_run)
