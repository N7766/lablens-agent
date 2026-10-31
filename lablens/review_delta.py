"""Explicit per-row review decisions; retention never bypasses final checks."""
import copy
import hashlib
from pathlib import Path

from .assessment import AssessmentReport, evidence_catalog, resolve_selections

VERSION = 'scoped-review-delta-v3'
IMPLEMENTATION_SHA256 = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
INSTRUCTIONS = '''Return exactly {"retained_assessment_ids": [...], "assessments": [...]}.
Reassess EVERY original row. For an entirely correct row, explicitly put its ID in
retained_assessment_ids; the server retains its exact original contents. For any change,
return a replacement in assessments with assessment_id, verdict, explanation, basis,
and evidence_ids. Each original ID must occur exactly ONCE across the two lists.
Retain only IDs allowed by server_review_delta. All other IDs require replacements.
Even an allowed ID must be replaced if its basis, conclusion, explanation or selected
premises are wrong or incomplete. Server placeholder rows must be answered. Correct a
rule_evaluation mislabeled as a causal_claim. Retention is your explicit review decision,
not evidence of correctness; every restored row still undergoes the same final checks.
Do not output statements, new rows, prose, partial reports, or empty lists for both fields.
'''


def review_policy(draft, hits, contract):
    AssessmentReport.model_validate(draft)
    catalog_pairs = {(v['chunk_id'], v['quote']) for v in evidence_catalog(hits).values()}
    findings = list(contract.get('draft_findings', [])) + list(contract.get('evidence_binding', {}).get('findings', []))
    blocked = {f['assessment_index'] for f in findings}
    allowed = []
    for index, row in enumerate(draft['assessments']):
        pairs = [(q['chunk_id'], q['quote']) for q in row['quotes']]
        # Empty evidence includes server placeholders. Non-catalog excerpts must
        # be explicitly selected again; never silently normalize the draft.
        if (index not in blocked and not (row['verdict'] == 'supported' and row['basis'] == 'causal_claim')
                and pairs and len(pairs) == len(set(pairs))
                and all(pair in catalog_pairs for pair in pairs)
                and row['citation_ids'] == list(dict.fromkeys(pair[0] for pair in pairs))):
            allowed.append(f'a{index + 1}')
    return dict(version=VERSION, implementation_sha256=IMPLEMENTATION_SHA256,
                retainable_assessment_ids=allowed, semantic_correctness_verified=False)


def delta_schema(full_schema, policy):
    schema = copy.deepcopy(full_schema)
    count = schema['properties']['assessments']['maxItems']
    schema['properties']['assessments'].update(minItems=0, maxItems=count)
    ids = list(policy['retainable_assessment_ids'])
    item = {'type': 'string'}
    if ids:
        item['enum'] = ids
    schema['properties']['retained_assessment_ids'] = dict(type='array', items=item, minItems=0, maxItems=len(ids))
    schema['required'] = ['retained_assessment_ids', 'assessments']
    return schema


def restore_review_delta(reviewed, draft, hits, policy):
    """Strict total partition of immutable IDs. No implicit retention on failure."""
    if type(reviewed) is not dict or set(reviewed) != {'retained_assessment_ids', 'assessments'}:
        raise ValueError('Review delta requires both decision lists and no extra fields.')
    retained, replacements = reviewed['retained_assessment_ids'], reviewed['assessments']
    if type(retained) is not list or type(replacements) is not list:
        raise ValueError('Review delta decisions must be lists.')
    original = {f'a{i + 1}': row for i, row in enumerate(draft['assessments'])}
    allowed = policy['retainable_assessment_ids']
    by_id = {}
    for identity in retained:
        if type(identity) is not str or identity not in allowed or identity in by_id:
            raise ValueError('Unknown, duplicate or non-retainable ID.')
        by_id[identity] = copy.deepcopy(original[identity])
    selected, changed_ids = [], []
    fields = {'assessment_id', 'verdict', 'explanation', 'basis', 'evidence_ids'}
    for row in replacements:
        if type(row) is not dict or set(row) != fields:
            raise ValueError('Replacement must contain only the fixed review fields.')
        identity = row['assessment_id']
        if type(identity) is not str or identity not in original or identity in by_id or identity in changed_ids:
            raise ValueError('Unknown, duplicate or overlapping replacement ID.')
        changed_ids.append(identity)
        selected.append(dict(statement=original[identity]['statement'],
                             **{k: v for k, v in row.items() if k != 'assessment_id'}))
    if set(retained) | set(changed_ids) != set(original):
        raise ValueError('Every original ID requires an explicit review decision.')
    if selected:
        resolved = resolve_selections({'assessments': selected}, hits)
        by_id.update(zip(changed_ids, resolved['assessments']))
    return {'assessments': [by_id[identity] for identity in original]}
