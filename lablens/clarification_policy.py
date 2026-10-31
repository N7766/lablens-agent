"""Bound clarification only for an explicit answer-and-evidence-gap request.

This policy never infers that a question is semantically unambiguous, that the
retrieved evidence is sufficient, or that an absent record proves an event did
not occur. It controls an offered action before a model decision; it does not
rewrite a selected clarification into an answer or add another model request.
"""
import hashlib
from pathlib import Path
import re

VERSION = 'bounded-evidence-answer-v1'
IMPLEMENTATION_SHA256 = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()

# Both parts must be explicit in the current user request. Source passages,
# previous assistant answers, and pinned scope are deliberately not inputs.
_CONCLUSION_REQUEST = re.compile(
    r'(?:请|能否|可以)?(?:逐项|分别|直接|明确|先)?(?:给出?|提供|说明|列出|更新|回答)'
    r'[^。！？!?\n]{0,30}(?:结论|判断)|'
    r'(?:什么|哪些|哪些内容)(?:是)?(?:可以|能够|能)(?:确定|确认)|'
    r'\b(?:give|provide|state|report|list|update)\b[^.!?\n]{0,50}'
    r'\b(?:conclusions?|verdicts?|assessments?)\b|'
    r'\bwhat\s+can\s+(?:we|you|I)\s+(?:conclude|determine|establish)\b', re.I)
_EVIDENCE_GAP_REQUEST = re.compile(
    r'(?:还|仍|所)?(?:缺少?|缺失|需要|所需)(?:的)?(?:哪些|什么|怎样的|最小|必要|额外|更多)?'
    r'(?:的)?(?:证据|资料|信息|记录)|'
    r'(?:哪些|什么)(?:证据|资料|信息|记录)(?:还|仍)?(?:缺少|缺失|需要补充)|'
    r'\b(?:missing|needed|required|additional)\s+(?:minimum\s+)?'
    r'(?:evidence|information|records|data)\b|'
    r'\b(?:evidence|information|records|data)\s+(?:(?:is|are|still|that)\s+)*'
    r'(?:missing|needed|required)\b', re.I)
# An explicit user preference to clarify first takes priority. Keeping this
# conservative can miss optimization opportunities, but avoids forcing a report
# against a request to identify the target or interpretation first.
_CLARIFY_FIRST = re.compile(
    r'(?:先|首先|优先)(?:向我)?(?:提问|问(?:我|清楚|清|一下)|澄清|确认(?:对象|目标|范围|含义))|'
    r'\bbefore\s+(?:giving|providing|stating|reporting|you\s+(?:give|provide|state|report))\b'
    r'[^.!?\n]{0,100}\b(?:ask|clarify)\b|'
    r'\b(?:first\s+(?:ask|clarify)|(?:ask|clarify)\b[^.!?\n]{0,45}'
    r'\b(?:first|before)\b)', re.I)
_CLARIFY_SCOPE_REQUEST = re.compile(
    r'(?:向我|跟我)(?:提问|澄清|确认(?:对象|目标|范围|含义|版本))|'
    r'问我(?:哪|所指|指的)|'
    r'\bask\s+me\b|\b(?:ask|clarify|confirm)\s+(?:(?:the|which|what)\s+)?'
    r'(?:target|scope|deployment|version|referent|interpretation)\b', re.I)
_NEGATED_CONCLUSION = re.compile(
    r'(?:不要|不必|无需|别|不需要)(?:再)?[^。！？!?\n]{0,20}(?:给|写|做|提供|回答|说明)'
    r'[^。！？!?\n]{0,15}(?:结论|判断)|'
    r'\b(?:do\s+not|don[\'’]t|no\s+need\s+to)\s+'
    r'(?:yet\s+)?(?:give|provide|state|report|list|update)\b[^.!?\n]{0,40}'
    r'\b(?:conclusions?|verdicts?|assessments?)\b', re.I)

POLICY_INSTRUCTIONS = (
    'Clarification resolves a missing user target or incompatible interpretations; '
    'missing supporting evidence by itself is not an ambiguous question. '
    'If the current user explicitly requests conclusions and the evidence still '
    'needed, answer the supported parts and bounded uncertainty after retrieval. '
    'Name the missing observation within the report, without treating an absent '
    'record as proof an event never happened. Resolve genuinely missing user '
    'scope before searching. An applicable server clarification obligation takes '
    'priority over this reporting policy.'
)


def clarification_policy(question, *, lookup_count, requires_clarification=False):
    """Return auditable action permission for one existing decision.

    A successful search, including ``no_hits``, counts as one lookup. A failed
    search must not increment the count. The caller must both filter the offered
    tool schema and reject a response selecting a disallowed tool without a
    repair call. This function neither submits a report nor selects evidence.
    """
    if not isinstance(question, str):
        raise TypeError('The current user question must be text.')
    if type(lookup_count) is not int or lookup_count < 0:
        raise ValueError('lookup_count must be a nonnegative integer.')
    if type(requires_clarification) is not bool:
        raise TypeError('requires_clarification must be a boolean.')
    requested = bool(_CONCLUSION_REQUEST.search(question) and _EVIDENCE_GAP_REQUEST.search(question))
    if requires_clarification:
        allow, rule = True, 'unresolved_user_reference_takes_priority'
    elif lookup_count == 0:
        allow, rule = True, 'initial_user_scope_clarification_available'
    elif _CLARIFY_FIRST.search(question):
        allow, rule = True, 'explicit_clarify_first_request'
    elif _CLARIFY_SCOPE_REQUEST.search(question):
        allow, rule = True, 'explicit_scope_clarification_request'
    elif _NEGATED_CONCLUSION.search(question):
        allow, rule = True, 'conclusion_request_negated'
    elif requested:
        allow, rule = False, 'explicit_conclusion_and_evidence_gap_after_retrieval'
    else:
        allow, rule = True, 'no_explicit_bounded_answer_request'
    return dict(version=VERSION, implementation_sha256=IMPLEMENTATION_SHA256,
                allow_clarification=allow, rule=rule,
                explicit_bounded_answer_request=requested, lookup_count=lookup_count,
                scope='Current user request and completed retrieval only; no semantic ambiguity or evidence sufficiency certification',
                semantic_ambiguity_verified=False, evidence_sufficiency_verified=False)
