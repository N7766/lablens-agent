"""One explicit decision per immutable row; no cross-list partition to generate."""
import copy
import hashlib
import json
from pathlib import Path

from .assessment import resolve_selections
from .review_delta import review_policy as legacy_policy

VERSION = 'scoped-review-decisions-v4'
IMPLEMENTATION_SHA256 = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
INSTRUCTIONS = '''Return exactly {"decisions": {"a1": {...}, "a2": {...}}} with EVERY
original assessment ID as a required key and exactly one decision object per ID.
For an entirely correct and explicitly retainable row return only {"action":"retain"}.
For any correction, or any non-retainable row, return {"action":"replace", "verdict":...,
"explanation":..., "basis":..., "evidence_ids":[...]}.
Reassess EVERY row. Retain only IDs allowed by server_review_decisions. An allowed ID
still requires replacement if its conclusion, explanation, basis or premises are wrong.
Answer server placeholders; their initial insufficient verdict is not a finding.
Never output statement, assessment_id, lists of retained IDs, duplicate keys or prose.
Retention is an explicit decision, not proof of correctness; final checks still apply.
'''


def _digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def review_policy(draft, hits, contract):
    # Share established eligibility checks, but bind the candidate to this draft,
    # evidence and contract. A policy from another request cannot authorize retain.
    policy = legacy_policy(draft, hits, contract)
    statements = [row['statement'] for row in draft['assessments']]
    if (contract.get('statements') != statements or len(set(statements)) != len(statements)
            or any(not statement.strip() for statement in statements)):
        raise ValueError('Review decision contract must match immutable statements.')
    return dict(policy, version=VERSION, implementation_sha256=IMPLEMENTATION_SHA256,
                assessment_ids=[f'a{i + 1}' for i in range(len(statements))],
                input_sha256=_digest(dict(draft=draft, hits=hits, contract=contract)))


def decision_schema(full_schema, policy):
    row = copy.deepcopy(full_schema['$defs']['AssessmentSelection'])
    del row['properties']['assessment_id']
    row['required'] = [name for name in row['required'] if name != 'assessment_id']
    row['properties']['action'] = {'type': 'string', 'enum': ['replace']}
    row['required'].insert(0, 'action')
    retain = dict(type='object', properties={'action': {'type': 'string', 'enum': ['retain']}},
                  required=['action'], additionalProperties=False)
    properties = {identity: {'anyOf': [copy.deepcopy(retain), copy.deepcopy(row)]}
                  if identity in policy['retainable_assessment_ids'] else copy.deepcopy(row)
                  for identity in policy['assessment_ids']}
    return dict(type='object', properties={'decisions': dict(type='object', properties=properties,
                    required=list(policy['assessment_ids']), additionalProperties=False)},
                required=['decisions'], additionalProperties=False)


def restore_review_decisions(reviewed, draft, hits, policy, contract):
    """Restore only a complete, explicit and current policy-bound decision map."""
    if policy != review_policy(draft, hits, contract):
        raise ValueError('Review decision policy does not match current inputs.')
    if type(reviewed) is not dict or set(reviewed) != {'decisions'}:
        raise ValueError('Review requires only the decisions object.')
    decisions = reviewed['decisions']
    if type(decisions) is not dict or set(decisions) != set(policy['assessment_ids']):
        raise ValueError('Every immutable ID requires exactly one decision.')
    original = dict(zip(policy['assessment_ids'], draft['assessments']))
    by_id, selected, changed_ids = {}, [], []
    fields = {'action', 'verdict', 'explanation', 'basis', 'evidence_ids'}
    for identity, row in decisions.items():
        if type(row) is not dict:
            raise ValueError('Each review decision must be an object.')
        if row.get('action') == 'retain':
            if set(row) != {'action'} or identity not in policy['retainable_assessment_ids']:
                raise ValueError('Retention contains extra fields or a non-retainable ID.')
            by_id[identity] = copy.deepcopy(original[identity])
        elif row.get('action') == 'replace' and set(row) == fields:
            changed_ids.append(identity)
            selected.append(dict(statement=original[identity]['statement'],
                                 **{key: value for key, value in row.items() if key != 'action'}))
        else:
            raise ValueError('Replacement must contain only the fixed decision fields.')
    if selected:
        resolved = resolve_selections({'assessments': selected}, hits)
        by_id.update(zip(changed_ids, resolved['assessments']))
    return {'assessments': [by_id[identity] for identity in original]}
