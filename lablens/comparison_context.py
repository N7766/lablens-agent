"""Bounded review obligations from user requests, never source-derived answers.

History identifies the requested scope; only the current passage catalog can
support facts. This plan deliberately leaves scope resolution and evidence
coverage unverified. It neither parses values nor promotes assistant history.
"""
import hashlib
import re
from pathlib import Path

from .answer_coverage import comparison_requirements


VERSION = 'source-scoped-review-context-v1'
IMPLEMENTATION_SHA256 = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
_MINIMUM_EVIDENCE = re.compile(
    r'(?:最小|最少|最低限度(?:的)?|至少还需(?:要)?的)\s*(?:核验)?证据|'
    r'\b(?:minimum|minimal|least) (?:additional |necessary |required )?evidence\b', re.I)
_NEGATED_REQUEST = re.compile(
    r'(?:不要|无需|不用|不必|不需要)[^，,。？！?!；;\n]{0,18}$|'
    r'\b(?:do not|don.t|no need to) [^.;!?\n]{0,32}$', re.I)

COMPARISON_REVIEW_INSTRUCTIONS = (
    'For the server comparison plan, reconstruct the previous USER-requested scope and '
    'the corrected current USER-requested scope independently from the supplied passages. '
    'A requested correction may compare different objects or versions: do not require an '
    'earlier answer about the new object, or refuse solely because the objects differ. '
    'For each requested item, explain old scoped value/rule -> new scoped value/rule, '
    'whether the conclusion changes or stays, and select passages supporting BOTH sides '
    'within that assessment. Naming both requested scopes inside that existing comparison '
    'explanation is allowed; do not add an unrequested proposition or row. '
    'An earlier assistant answer is not evidence. If it was wrong, '
    'distinguish correcting that answer from an actual change in the underlying system. '
    'The plan does not resolve pronouns or certify source coverage. If either scope is '
    'ambiguous, or its applicable source is missing or contradictory, identify that exact '
    'uncertainty without inventing a prior value; preserve independently supported facts. '
    'Keep configured ceilings distinct from observed execution counts.'
)

MINIMUM_EVIDENCE_REVIEW_INSTRUCTIONS = (
    'The current user explicitly asks for minimum evidence. In EACH relevant insufficient '
    'assessment explanation, state what remains unknown and the smallest observable that '
    'could decide it, with its required object/run/attempt or time/version linkage. '
    'Use supplied evidence-inventory requirements when present and cite them; otherwise '
    'label the check as a proposed verification, not an observed or source-proven fact. '
    'Name a decisive record or counter and the required linkage rather than merely saying '
    'logs are missing. Alternatives are alternatives, not a demand for every artifact. '
    'Before attributing a cause, distinguish whether the underlying event/failure itself '
    'is established, then require linked observations for the proposed causal connection. '
    'A final success or configured allowance alone cannot establish prior attempts/failures. '
    'Attribute user-provided clues to the user; do not present them as verified source facts. '
    'Fill the existing relevant explanation; do not add rows or repeat unrelated old conclusions.'
)


def _previous_user_scope(context):
    """Reference the latest valid original user question, with no history fallback.

    This function does not search assistant text, pinned state, source titles or
    omitted turns for a missing referent. A malformed latest entry makes the
    prior scope unavailable rather than silently selecting an older question.
    """
    recent = context.get('recent_user_messages') if isinstance(context, dict) else None
    if not isinstance(recent, list) or not recent:
        return None
    latest = recent[-1]
    if not isinstance(latest, dict):
        return None
    question, seq, snapshot = (latest.get(key) for key in ('question', 'seq', 'snapshot_id'))
    if (not isinstance(question, str) or not question.strip() or len(question) > 2000
            or type(seq) is not int or seq < 1 or not isinstance(snapshot, str)
            or not snapshot.strip() or len(snapshot) > 200):
        return None
    return dict(question_ref='untrusted_conversation_context.recent_user_messages[-1].question',
                seq=seq, snapshot_id=snapshot,
                authority='user_requested_scope_not_verified_facts')


def build_review_context(question, context, catalog):
    """Return an explicit obligation plan or None, without deciding any answer."""
    requirements = comparison_requirements(question)
    minimum_evidence = any(not _NEGATED_REQUEST.search(question[max(0, match.start() - 48):match.start()])
                           for match in _MINIMUM_EVIDENCE.finditer(question))
    if not requirements and not minimum_evidence:
        return None
    plan = dict(version=VERSION, implementation_sha256=IMPLEMENTATION_SHA256,
                source_catalog='passages', available_passage_ids=list(catalog),
                available_passage_count=len(catalog),
                scope_resolution_verified=False, evidence_coverage_verified=False,
                semantic_correctness_verified=False, previous_answers_are_evidence=False,
                minimum_evidence_requested=minimum_evidence)
    if requirements:
        previous = _previous_user_scope(context)
        plan['comparison'] = dict(
            requested_items=[item['subject'] for item in requirements],
            current_user_scope='question', previous_user_scope=previous,
            previous_scope_status=('user_message_available_not_disambiguated' if previous else 'unavailable'),
            comparison_mode='independently_source_both_user_scopes',
            different_objects_or_versions_are_not_by_themselves_missing_evidence=True)
    return plan


def review_instructions(plan):
    if plan is None:
        return ''
    parts = []
    if 'comparison' in plan:
        parts.append(COMPARISON_REVIEW_INSTRUCTIONS)
    if plan['minimum_evidence_requested']:
        parts.append(MINIMUM_EVIDENCE_REVIEW_INSTRUCTIONS)
    return '\n' + '\n'.join(parts)
