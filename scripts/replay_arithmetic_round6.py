#!/usr/bin/env python3
"""Replay saved Cedar drafts through old/new guards without a provider call."""
import argparse
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile


REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from lablens.answer_quality import selected_evidence_findings
from lablens.evidence_arithmetic import inspect_percentage_derivations

BASELINE = 'b76eb7a4553583aaa8ca3a19bca83a59da67e9e6'


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_baseline(path, filename, module_name):
    # Load trusted repository code at an exact Git commit, never model text.
    path.write_bytes(subprocess.check_output(['git', 'show', BASELINE + ':' + filename], cwd=REPO))
    spec = importlib.util.spec_from_file_location(module_name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    if args.output.exists():
        raise SystemExit('Refuse to overwrite an existing replay artifact')
    source_files = ('lablens/answer_quality.py', 'lablens/evidence_arithmetic.py', 'lablens/evidence_scope.py')
    source_hashes = {name: sha(REPO / name) for name in source_files}
    records, input_hashes = [], {}
    with tempfile.TemporaryDirectory(prefix='lablens-round6-baseline-') as directory:
        temp = Path(directory)
        old_guard = load_baseline(temp / 'answer_quality.py', 'lablens/answer_quality.py', 'round6_baseline_quality')
        old_selection = load_baseline(temp / 'assessment.py', 'lablens/assessment.py', 'lablens.round6_baseline_assessment')
        baseline_hashes = {name: sha(temp / name) for name in ('answer_quality.py', 'assessment.py')}
        for tid in ('cedar-t1', 'cedar-t3'):
            relative = f'docs/evaluations/conversation-round5/development/run/cedar/{tid}/task-final.json'
            path = REPO / relative
            input_hashes[relative] = sha(path)
            task = json.loads(path.read_text())
            trace = task['result']['agent']['trace']
            hits = {}
            raw_draft = None
            for event in trace:
                if event.get('event') == 'tool' and event.get('name') == 'search_workspace':
                    for hit in event['output']['hits']:
                        hits[hit['chunk_id']] = hit
                if event.get('event') == 'tool_started' and event.get('name') == 'verify_and_submit_report':
                    raw_draft = copy.deepcopy(event['arguments'])
                    break
            if raw_draft is None:
                raise RuntimeError('No saved report draft: ' + tid)
            resolved = old_selection.resolve_selections(raw_draft, list(hits.values()))
            before, after = old_guard.selected_evidence_findings(resolved), selected_evidence_findings(resolved)
            if before != [{'assessment_index': 0, 'rule': 'quantity_not_anchored_in_selected_quotes', 'quantities': ['13.3%']}]:
                raise RuntimeError('Saved draft did not reproduce the expected original failure')
            if after:
                raise RuntimeError('Current guard still withholds this saved draft: ' + repr(after))
            item = resolved['assessments'][0]
            arithmetic = inspect_percentage_derivations(item['statement'] + '\n' + item['explanation'],
                                                        '\n'.join(q['quote'] for q in item['quotes']))
            negative = copy.deepcopy(item)
            negative['statement'] = negative['explanation'] = 'Dune节电率=(240-208)/240*100=12.5%。'
            control = {'assessments': [negative]}
            records.append({'turn_id': tid, 'source_task_file': relative,
                'source_task_sha256': input_hashes[relative], 'saved_provider_draft': raw_draft,
                'resolved_with_baseline_catalog': resolved,
                'before_guard_findings': before, 'after_guard_findings': after,
                'new_derivation': {'derived_quantities': [[kind, str(value)] for kind, value in arithmetic.derived_quantities],
                    'accepted': arithmetic.accepted, 'findings': arithmetic.findings},
                'synthetic_negative_control': {'label': 'mutated wrong result equals quoted threshold; not a historical model output',
                    'input': control, 'before_guard_findings': old_guard.selected_evidence_findings(control),
                    'after_guard_findings': selected_evidence_findings(control)}})
    if source_hashes != {name: sha(REPO / name) for name in source_files}:
        raise RuntimeError('Current guard sources changed during replay')
    if input_hashes != {name: sha(REPO / name) for name in input_hashes}:
        raise RuntimeError('Historical raw changed during replay')
    artifact = {'schema_version': 1, 'scope': 'Offline replay of exposed saved provider drafts; no model call and no new answer-quality score',
        'baseline_commit': BASELINE, 'baseline_source_sha256': baseline_hashes,
        'current_source_sha256': source_hashes, 'historical_input_sha256': input_hashes,
        'provider_calls': 0, 'historical_files_unchanged': True,
        'limitations': ['The before/after compares a deterministic guard on identical saved drafts.',
            'Original Round 5 outputs and grades remain unchanged.',
            'Passing numeric checks does not establish complete answers, citation entailment or model generalization.'],
        'records': records}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(artifact, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({'output': str(args.output), 'sha256': sha(args.output),
        'replayed_drafts': len(records), 'provider_calls': 0,
        'old_findings': [len(r['before_guard_findings']) for r in records],
        'new_findings': [len(r['after_guard_findings']) for r in records]}))


if __name__ == '__main__':
    main()
