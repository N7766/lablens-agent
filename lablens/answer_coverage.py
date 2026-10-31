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
        name = re.compile(rf'(?<![A-Za-z0-9_-]){re.escape(subject)}(?![A-Za-z0-9_-])', re.I)
        covered = any(name.search(item['statement']) and _ROW_OVERALL.search(item['statement'])
                      for item in prepared['assessments'])
        if covered:
            continue
        prepared['assessments'].append(dict(statement=requirement['statement'], verdict='insufficient',
            explanation='待复核本轮要求的总体结论；这不是资料缺失判定。', basis='documented_fact',
            citation_ids=[], quotes=[]))
        added.append(requirement['statement'])
    if len(prepared['assessments']) > 8:
        raise ValueError('Explicit overall-verdict slots exceed the eight-assessment limit; combine related draft facts without omitting requested conclusions')
    return prepared, dict(version=VERSION, requested_overall=requirements_as_statements(required),
        added_review_slots=added, scope='Explicit named-pair overall requests only; presence is not semantic coverage or entailment',
        semantic_coverage_verified=False)


def requirements_as_statements(requirements):
    return [item['statement'] for item in requirements]
