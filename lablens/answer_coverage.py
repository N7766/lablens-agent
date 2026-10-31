"""Reserve explicit user-requested overall verdicts before the fixed review.

This is a small lexical coverage aid, not a question parser or correctness judge.
It creates questions for the reviewer, never answers or evidence. Only a single
pair of named alternatives and an explicit overall-acceptance request qualify.
"""
import copy
import re

from .evidence_scope import named_user_pairs

VERSION = 'explicit-overall-slots-v1'
_OVERALL = re.compile(r'验收结论|总(?:体)?(?:结论|判定)|哪些候选.{0,12}通过|'
                      r'\boverall (?:verdict|conclusion|acceptance)|\bwhich .{0,30}\bpass', re.I)
_ROW_OVERALL = re.compile(r'总体|总判定|总的结论|验收结论|\boverall\b', re.I)
_PARTS_ONLY = re.compile(r'只(?:要|需|给|比较|判断).{0,12}分项|不要.{0,8}(?:总体|总判定|验收结论)|'
                         r'\b(?:only (?:individual|per-metric)|no overall|do not give an overall)', re.I)
_NARROWING = re.compile(r'更正|只(?:要|需|给|看|关注|针对)|仅(?:需|给|关注|针对)|改为|'
                        r'\b(?:correction|instead|only|rather than)\b', re.I)
_DIRECT_OVERALL_ZH = re.compile(
    # A named trial such as R-17 is a narrow scope prefix. Arbitrary "在...中"
    # also matches per-metric scope, so it must not suppress an overall slot.
    r'^\s*(?:在\s*[A-Za-z][A-Za-z_-]{0,20}\d[A-Za-z0-9_-]{0,20}\s*试验中\s*)?'
    r'(?:因[^，。；;\n]{1,120}而\s*)?'
    r'(?:(?:已经|已|未|不|无法|未能|可以|能够|不能|不会|会)\s*)?'
    # Whitespace alone is not a boundary: "验收条件 1" is a single condition.
    r'(?:通过|满足)(?:本次|整个|全部)?验收(?:条件|要求)?(?=\s*(?:$|[。！？，；,;.!?]))|'
    r'^\s*(?:本次)?验收\s*(?:未|不|无法|已)?通过(?=\s*(?:$|[。！？，；,;.!?]))')
_DIRECT_OVERALL_EN = re.compile(
    r'^\s+(?:(?:does|did|will|can)\s+not\s+|cannot\s+)?'
    r'(?:(?:pass(?:es|ed)?|fail(?:s|ed)?)(?:\s+the)?\s+(?:acceptance(?:\s+(?:criteria|test))?|trial)|'
    r'(?:meet(?:s)?|satisf(?:y|ies))\s+all\s+(?:acceptance\s+)?(?:criteria|conditions))'
    r'(?=\s*(?:[.;,!?]|$)|\s+(?:because|as|since)\b)', re.I)


class CoverageContractError(ValueError):
    """Missing required slots cannot fit; the caller must stop, not replan."""

    def __init__(self, assessment_count, missing_overall_slots):
        self.assessment_count = assessment_count
        self.missing_overall_slots = list(missing_overall_slots)
        super().__init__('Required overall-verdict slots exceed the eight-assessment capacity; report withheld.')


def _covers_overall(statement, subject):
    name = re.search(rf'(?<![A-Za-z0-9_-]){re.escape(subject)}(?![A-Za-z0-9_-])', statement, re.I)
    if name is None:
        return False
    if _ROW_OVERALL.search(statement):
        return True
    # A whole-candidate predicate is already an overall proposition, regardless
    # of whether its verdict is true. Keep it for review rather than duplicating
    # it. A per-metric subject ("Quill latency passes ...") is not this pattern.
    predicate = statement[name.end():]
    return bool(_DIRECT_OVERALL_ZH.search(predicate) or _DIRECT_OVERALL_EN.search(predicate))


def overall_requirements(question):
    if not _OVERALL.search(question) or _PARTS_ONLY.search(question):
        return []
    pairs = named_user_pairs(question)
    if len(pairs) != 1:
        return []
    _, end, names = pairs[0]
    for clause in re.split(r'[，,。！？?!；;\n]', question[end:]):
        mentions = [name for name in names if re.search(
            rf'(?<![A-Za-z0-9_-]){re.escape(name)}(?![A-Za-z0-9_-])', clause, re.I)]
        if _NARROWING.search(clause) and len(mentions) == 1:
            return []
    return [dict(subject=name, statement=f'{name} 的本次验收总判定。') for name in names]


def prepare_review_draft(question, draft):
    """Extend a draft only with bounded, explicit overall-verdict question slots.

    Existing statements stay immutable. The normal fixed-statement review then
    supplies each verdict and its evidence. An overfull draft is rejected, never
    silently truncated. Other missing metrics still need semantic evaluation.
    """
    prepared = copy.deepcopy(draft)
    required = overall_requirements(question)
    added = []
    for requirement in required:
        subject = requirement['subject']
        covered = any(_covers_overall(item['statement'], subject) for item in prepared['assessments'])
        if covered:
            continue
        prepared['assessments'].append(dict(statement=requirement['statement'], verdict='insufficient',
            explanation='待复核本轮要求的总体结论；这不是资料缺失判定。', basis='documented_fact',
            citation_ids=[], quotes=[]))
        added.append(requirement['statement'])
    if len(prepared['assessments']) > 8:
        raise CoverageContractError(len(draft['assessments']), added)
    return prepared, dict(version=VERSION, requested_overall=requirements_as_statements(required),
        added_review_slots=added, scope='Explicit named-pair overall requests only; presence is not semantic coverage or entailment',
        semantic_coverage_verified=False)


def requirements_as_statements(requirements):
    return [item['statement'] for item in requirements]
