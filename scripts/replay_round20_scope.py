#!/usr/bin/env python3
"""Replay the known Round 19c scope failure with scripted reviews, fully offline.

No model, provider, loopback service, or browser is contacted. The two controlled
variants are AI-designed transformations of an exposed regression, not new model
samples, a blind evaluation, or a semantic acceptance pass. Existing evidence is
read-only; the selected output must not already exist.
"""
import argparse
from contextlib import ExitStack
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from lablens import answer_quality, assessment, proposition_scope, rag, review_decisions, review_delta
from lablens.answer_quality import make_review_contract
from lablens.assessment import evidence_catalog, resolve_selections

SPEC = importlib.util.spec_from_file_location('offline_private_agent_acceptance',
    ROOT / 'scripts/verify_private_agent.py')
acceptance = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(acceptance)

VERSION = 'round20-known-scope-offline-replay-v1'
SEALED = ROOT / 'docs/evaluations/conversation-round19c-runtime'
ORIGINAL_TASK_SHA256 = '8a91f35857ebe3b154db1985b267678e69b70ac2a30f6855727a88521e8a9334'


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def source_hashes():
    paths = [*sorted((ROOT / 'lablens').rglob('*.py')),
             ROOT / 'scripts/verify_private_agent.py', Path(__file__).resolve()]
    return {str(path.relative_to(ROOT)): digest(path.read_bytes()) for path in paths}


def load_original():
    files = {
        'task': SEALED / 'report/run/task-final.json',
        'snapshot': SEALED / 'report/ordered-snapshot.json',
        'summary': SEALED / 'report/run/summary.json',
        'semantic_review': SEALED / 'public-ready/independent-agent-review.json',
    }
    blobs = {name: path.read_bytes() for name, path in files.items()}
    if digest(blobs['task']) != ORIGINAL_TASK_SHA256:
        raise ValueError('Sealed original task identity changed; refuse a different regression input.')
    data = {name: json.loads(raw) for name, raw in blobs.items()}
    original_checks = data['summary']['checks']
    if not (len(original_checks) == 17 and all(original_checks.values())
            and data['summary']['passed'] is True
            and data['semantic_review']['semantic_acceptance'] == 'failed'
            and data['semantic_review']['failed_criteria'] == ['S5']):
        raise ValueError('Original mechanical/semantic outcome does not match the sealed failure.')
    result = data['task']['result']
    selected = next(event['arguments'] for event in result['agent']['trace']
                    if event.get('name') == 'verify_and_submit_report' and event['event'] == 'tool')
    hits = deepcopy(result['retrieval']['hits'])
    draft = resolve_selections(deepcopy(selected), hits)
    # Match the original runtime's JSON representation, not our compact encoding.
    original_draft_digest = digest(json.dumps(draft, ensure_ascii=False, sort_keys=True).encode())
    if original_draft_digest != result['generation_review']['draft_sha256']:
        raise ValueError('Trace reconstruction does not match the original normalized draft digest.')
    return data, files, blobs, draft, hits


def replay_case(case_id, description, draft, reviewed, hits, question, snapshot):
    calls, observed, usage = [], [], {}

    def scripted_post(config, path, payload, received_usage, kind):
        if calls:
            raise AssertionError('Exactly one scripted review invocation is permitted per case; no retry.')
        if path != '/chat/completions' or kind != 'generation':
            raise AssertionError('The replay may only enter the one review transport stub.')
        request = json.loads(payload['messages'][1]['content'])
        calls.append(dict(path=path, kind=kind, request_sha256=digest(canonical(payload)),
                          request_bytes=len(canonical(payload)),
                          schema_name=payload['response_format']['json_schema']['name'],
                          server_review_decisions=request.get('server_review_decisions')))
        # No fabricated provider tokens or provider/model call accounting.
        received_usage['scripted_review_invocations'] = 1
        return {'choices': [{'finish_reason': 'stop', 'message': {
            'role': 'assistant', 'content': json.dumps(reviewed, ensure_ascii=False)}}]}

    context = {'scoped_review_contract': make_review_contract(question, draft)}
    original_inputs = canonical(dict(draft=draft, reviewed=reviewed, hits=hits))
    output = dict(id=case_id, description=description, input_kind=(
        'saved_normalized_model_output' if case_id == 'original_review' else 'ai_designed_controlled_transformation'),
        question=question, normalized_draft=deepcopy(draft), normalized_review=deepcopy(reviewed),
        scoped_review_contract=context['scoped_review_contract'])
    with patch.object(rag, '_post', side_effect=scripted_post):
        try:
            final = rag.review_assessments(question, draft, hits, usage,
                {'model': 'scripted-offline-review-not-a-model', 'review_protocol': 'scoped-review-decisions-v4'},
                observer=observed.append, context_data=context)
        except rag.RAGError as error:
            output.update(terminal_status=error.status, report_published=False,
                          error=str(error), diagnostic=getattr(error, 'review_diagnostic', None),
                          mechanical_report_case_checks=None)
        else:
            output.update(terminal_status=final['status'], report_published=True,
                          final=final, mechanical_report_case_checks=acceptance.report_case_checks(final, snapshot))
    if canonical(dict(draft=draft, reviewed=reviewed, hits=hits)) != original_inputs:
        raise AssertionError('Replay mutated a source input.')
    output.update(scripted_review_invocations=len(calls), external_model_calls=0,
                  usage=usage, scripted_requests=calls, observed_review=observed,
                  provider_tokens_measured=False, retries=0)
    return output


def run():
    before = source_hashes()
    loaded = {f'lablens/{module.__name__.rsplit(".", 1)[-1]}.py': module.IMPLEMENTATION_SHA256
              for module in (answer_quality, assessment, proposition_scope, review_decisions, review_delta)}
    loaded['lablens/rag.py'] = rag.LOADED_IMPLEMENTATION_SHA256
    data, files, blobs, draft, hits = load_original()
    question = data['task']['request']['question']
    original_review = deepcopy(data['task']['result']['generation_review']['decisions']['reviewed_response'])
    repaired_review = deepcopy(original_review)
    # Retain the original configuration passage and historical caveat. This
    # changes only the incorrect verdict in the controlled review, not a model.
    repaired_review['decisions']['a2']['verdict'] = 'supported'

    catalog = evidence_catalog(hits)
    passage_id = lambda text: next(key for key, value in catalog.items() if value['quote'] == text)
    cache_id = passage_id('cache_enabled: true')
    missing_id = passage_id('# 没有提供对应历史运行日志。')
    split_selections = {'assessments': [
        dict(statement=draft['assessments'][0]['statement'], verdict='supported', basis='historical_observation',
             explanation=draft['assessments'][0]['explanation'],
             evidence_ids=[passage_id('SFT 基线：准确率 86%。')]),
        dict(statement='配置启用了缓存。', verdict='supported', basis='current_configuration',
             explanation=repaired_review['decisions']['a2']['explanation'], evidence_ids=[cache_id]),
        dict(statement='历史运行启用了缓存。', verdict='insufficient', basis='historical_observation',
             explanation='资料只记录 cache_enabled: true，没有提供对应历史运行日志，不能确认历史运行启用了缓存。',
             evidence_ids=[cache_id, missing_id]),
    ]}
    split_draft = resolve_selections(split_selections, hits)
    split_review = {'decisions': {
        f'a{i + 1}': dict(action='replace', **{key: value for key, value in row.items() if key != 'statement'})
        for i, row in enumerate(split_selections['assessments'])
    }}

    network_attempts = []
    def block_network(*args, **kwargs):
        network_attempts.append('blocked_network_path')
        raise AssertionError('Network and local service requests are forbidden in this offline replay.')

    # The application transport is stubbed separately in each case. These guards
    # make an accidental fallback fail instead of contacting any service.
    with ExitStack() as stack:
        for target in ('socket.create_connection', 'socket.socket.connect', 'socket.socket.connect_ex',
                       'httpx.Client.request', 'httpx.AsyncClient.request', 'urllib.request.urlopen'):
            stack.enter_context(patch(target, side_effect=block_network))
        cases = [
            replay_case('original_review', 'Known actual failure; original normalized draft and parsed review unchanged.',
                deepcopy(draft), original_review, deepcopy(hits), question, data['snapshot']),
            replay_case('configuration_only_repair',
                'Controlled review changes only a2 verdict to supported. Fixed statements still omit a separate historical proposition.',
                deepcopy(draft), repaired_review, deepcopy(hits), question, data['snapshot']),
            replay_case('explicit_split_propositions',
                'Controlled draft has separate configuration and historical rows; all review selections use existing frozen passages.',
                split_draft, split_review, deepcopy(hits), question, data['snapshot']),
        ]

    first, second, third = cases
    second_final, third_final = second.get('final', {}), third.get('final', {})
    second_checks = second.get('mechanical_report_case_checks') or {}
    third_checks = third.get('mechanical_report_case_checks') or {}
    expectations = {
        'original_review_withheld_for_scope': first['terminal_status'] == 'invalid_proposition_scope' and not first['report_published'],
        'only_verdict_changed_in_controlled_review': repaired_review['decisions']['a2'] == dict(
            original_review['decisions']['a2'], verdict='supported'),
        'configuration_repair_publishes_supported_configuration': second['report_published'] and any(
            row['statement'] == '配置启用了缓存。' and row['verdict'] == 'supported'
            and row['basis'] == 'current_configuration'
            and any(q['quote'] == 'cache_enabled: true' for q in row['quotes'])
            and row['explanation'] == repaired_review['decisions']['a2']['explanation']
            for row in second_final.get('assessments', [])),
        'configuration_repair_still_fails_missing_historical_item': bool(second_checks)
            and second_checks.get('historical_cache_insufficient') is False
            and all(value for key, value in second_checks.items() if key != 'historical_cache_insufficient'),
        'split_report_passes_conservative_known_case_checks': bool(third_checks) and all(third_checks.values()),
        'split_rendered_labels_are_distinct': '【证据支持】配置启用了缓存。' in third_final.get('answer', '')
            and '【证据不足】历史运行启用了缓存。' in third_final.get('answer', '')
            and len(third_final.get('assessments', [])) == 3,
        'exactly_one_scripted_review_per_case': all(case['scripted_review_invocations'] == 1 for case in cases),
        'no_network_attempts': not network_attempts,
        'sealed_inputs_unchanged': all(path.read_bytes() == blobs[name] for name, path in files.items()),
        'sources_unchanged_during_replay': source_hashes() == before,
        'loaded_replay_modules_match_frozen_sources': all(before[path] == value for path, value in loaded.items()),
    }
    return dict(version=VERSION, generated_at=datetime.now(timezone.utc).isoformat(),
        evidence_kind='offline_known_regression_and_ai_designed_controlled_variants',
        scope='One exposed synthetic regression; scripted review responses, not live model samples or independent semantic evaluation.',
        semantic_acceptance='not_evaluated', general_model_quality='not_measured',
        model_calls=0, external_model_calls=0, scripted_review_invocations=sum(case['scripted_review_invocations'] for case in cases),
        provider_tokens_measured=False, external_cost=0, network_attempts=network_attempts,
        historical_outcome=dict(task_sha256=ORIGINAL_TASK_SHA256, mechanical_checks_passed=17,
            mechanical_checks_total=17, semantic_acceptance='failed', failed_criteria=['S5'],
            original_score_rewritten=False, new_model_sample=False),
        inputs={name: dict(path=str(path.relative_to(ROOT)), sha256=digest(blobs[name])) for name, path in files.items()},
        source_freeze=dict(script_sha256=before[str(Path(__file__).resolve().relative_to(ROOT))],
            all_runtime_python_and_driver_files=before, manifest_sha256=digest(canonical(before)),
            loaded_replay_modules=loaded,
            acceptance_version=acceptance.VERSION),
        retrieved_hits=hits, frozen_passage_catalog=catalog, cases=cases,
        regression_expectations=expectations, offline_regression_passed=all(expectations.values()),
        interpretation='A passing controlled replay demonstrates the intended rejection/repair paths for these fixed inputs only. '
            'It does not repair the historical model output or certify future model correctness. '
            'generation_review.calls=1 in a final object counts the scripted stub invocation here, not a provider request.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT / 'docs/evaluations/conversation-round20/offline-replay.json')
    args = parser.parse_args()
    if args.output.exists():
        parser.error('Output already exists; never overwrite a sealed replay artifact.')
    report = run()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('x') as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write('\n')
    print(json.dumps(dict(output=str(args.output), offline_regression_passed=report['offline_regression_passed'],
        model_calls=report['model_calls'], scripted_review_invocations=report['scripted_review_invocations'],
        regression_expectations=report['regression_expectations']), ensure_ascii=False))
    return 0 if report['offline_regression_passed'] else 1


if __name__ == '__main__':
    sys.exit(main())
