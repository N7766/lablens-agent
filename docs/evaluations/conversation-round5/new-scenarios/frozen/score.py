#!/usr/bin/env python3
"""Verify seals and aggregate human-readable AI semantic annotations; no keyword grading."""
import argparse
import hashlib
import json
from pathlib import Path

DIMS = ('retrieval_coverage', 'conclusion_correctness', 'completeness', 'citation_entailment', 'erroneous_refusal', 'reference_scope')

def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--run', required=True, type=Path)
    parser.add_argument('--annotations', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    root = Path(__file__).resolve().parent.parent
    freeze_path = root / 'freeze-manifest.json'
    freeze = json.loads(freeze_path.read_text())
    for relative, expected in freeze['files'].items():
        assert digest(root / relative) == expected, 'Frozen input changed: ' + relative
    seal_path = args.run / 'raw-seal.json'
    seal = json.loads(seal_path.read_text())
    for relative, expected in seal['files'].items():
        assert digest(args.run / relative) == expected, 'Raw artifact changed: ' + relative
    summary = json.loads((args.run / 'run-summary.json').read_text())
    annotations = json.loads(args.annotations.read_text())
    gold = json.loads((root / 'frozen' / 'gold.json').read_text())
    assert annotations['same_ai_author_and_scorer'] is True
    assert annotations['freeze_manifest_sha256'] == digest(freeze_path)
    assert annotations['raw_seal_sha256'] == digest(seal_path)
    assert annotations['candidate_sha'] == summary['candidate_sha']
    outcomes = {t['turn_id']: t for t in summary['turns']}
    assert len(outcomes) == 4
    assert {t['turn_id'] for t in annotations['turns']} == set(outcomes)
    assert len(annotations['turns']) == 4
    evaluated = 0
    strict_passes = []
    full_counts = {key: 0 for key in DIMS}
    per_turn = []
    for turn in annotations['turns']:
        tid = turn['turn_id']
        outcome = outcomes[tid]
        assert set(turn['dimensions']) == set(DIMS)
        scored = turn['evaluation_status'] == 'evaluated'
        if scored:
            assert outcome['status'] == 'SUCCEEDED' and outcome.get('valid_saved_response') is True
            evaluated += 1
            assert set(turn['dimension_rationale']) == set(DIMS)
            for key, score in turn['dimensions'].items():
                assert score is None or type(score) is int and score in (0, 1, 2)
                assert isinstance(turn['dimension_rationale'][key], str) and turn['dimension_rationale'][key].strip()
                if score == 2:
                    full_counts[key] += 1
            expected_claims = {c['id'] for c in gold['turns'][tid]['required_conclusions']}
            assert {c['gold_claim_id'] for c in turn['claim_checks']} == expected_claims
            for check in turn['claim_checks']:
                assert check['verdict'] in ('satisfied', 'partial', 'missing', 'contradicted')
                assert check['rationale'] and isinstance(check['evidence_pointers'], list)
            assert turn['retrieval_evidence'], 'Need retrieval trace review or explicit unavailable record'
        else:
            assert turn['evaluation_status'] == 'not_evaluable'
            assert all(v is None for v in turn['dimensions'].values())
            assert turn['limitations']
        passed = scored and all(turn['dimensions'][key] == 2 for key in DIMS)
        if passed:
            strict_passes.append(tid)
        per_turn.append({'turn_id': tid, 'execution_status': outcome['status'],
                         'evaluation_status': turn['evaluation_status'], 'strict_pass': passed,
                         'dimensions': turn['dimensions']})
    result = {'schema_version': 1, 'same_ai_author_and_scorer': True,
              'study_type': 'tiny frozen AI-synthetic diagnostic; no independent human scoring',
              'candidate_sha': summary['candidate_sha'], 'freeze_manifest_sha256': digest(freeze_path),
              'raw_seal_sha256': digest(seal_path), 'annotations_sha256': digest(args.annotations),
              'planned_denominator': 4, 'evaluated_denominator': evaluated,
              'strict_pass_count': len(strict_passes), 'strict_pass_turns': strict_passes,
              'strict_pass_fraction_all_planned': f'{len(strict_passes)}/4',
              'strict_pass_fraction_evaluated': f'{len(strict_passes)}/{evaluated}',
              'dimension_full_score_counts': full_counts,
              'known_model_calls': summary['known_model_calls'],
              'unknown_usage_tasks': summary['unknown_usage_tasks'],
              'turns': per_turn}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps(result, ensure_ascii=False, indent=2))

if __name__ == '__main__':
    main()
