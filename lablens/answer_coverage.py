"""Reserve bounded, explicit user-requested questions before the fixed review.

This is a lexical coverage aid, not a question parser or correctness judge. It
creates questions, never answers or evidence. Supported shapes are named-pair
overall acceptance and enumerated previous-answer change/retain comparisons.
"""
import copy
import re

from .evidence_scope import named_user_pairs

VERSION = 'explicit-request-slots-v3'
_OVERALL = re.compile(r'验收结论|总(?:体)?(?:结论|判定)|哪些候选.{0,12}通过|'
                      r'\boverall (?:verdict|conclusion|acceptance)|\bwhich .{0,30}\bpass', re.I)
_ROW_OVERALL = re.compile(r'总体|总判定|总的结论|验收结论|\boverall\b', re.I)
_PARTS_ONLY = re.compile(r'只(?:要|需|给|比较|判断).{0,12}分项|不要.{0,8}(?:总体|总判定|验收结论)|'
                         r'\b(?:only (?:individual|per-metric)|no overall|do not give an overall)', re.I)
_NARROWING = re.compile(r'更正|只(?:要|需|给|看|关注|针对)|仅(?:需|给|关注|针对)|改为|'
                        r'\b(?:correction|instead|only|rather than)\b', re.I)
_TRIAL_ID = r'(?![Pp]\d+(?![A-Za-z0-9_-]))[A-Z][A-Za-z_-]{0,20}\d[A-Za-z0-9_-]{0,20}'
_CONDITIONAL_OVERALL = re.compile(
    r'如果|假如|假设|倘若|除非|只要|若(?!干)|'
    r'\b(?:if|unless|assuming|suppose|would)\b', re.I)
_QUESTION_OVERALL = re.compile(r'[?？]|吗|是否|能否|可否|\bwhether\b', re.I)
_DIRECT_OVERALL_ZH = re.compile(
    # A named trial such as R-17 is a narrow scope prefix. Arbitrary "在...中"
    # also matches per-metric scope, so it must not suppress an overall slot.
    rf'^\s*(?:在\s*{_TRIAL_ID}\s*试验中\s*)?'
    r'(?:因[^，。；;\n]{1,120}而\s*)?'
    r'(?:(?:也|均|都)\s*)?'
    r'(?:(?:已经|已|未|不|无法|未能|可以|能够|不能|不会|会)\s*)?'
    # Whitespace alone is not a boundary: "验收条件 1" is a single condition.
    rf'(?:通过|满足)\s*(?:{_TRIAL_ID}\s*(?:试验\s*)?)?'
    r'(?:本次|整个|全部)?验收(?:条件|要求)?(?=\s*(?:$|[。！？，；,;.!?]))|'
    r'^\s*(?:本次)?验收\s*(?:未|不|无法|已)?通过(?=\s*(?:$|[。！？，；,;.!?]))')
_DIRECT_OVERALL_EN = re.compile(
    r'^\s+(?:(?:does|did|will|can)\s+not\s+|cannot\s+)?'
    r'(?:(?:pass(?:es|ed)?|fail(?:s|ed)?)(?:\s+the)?\s+(?:acceptance(?:\s+(?:criteria|test))?|trial)|'
    r'(?:meet(?:s)?|satisf(?:y|ies))\s+all\s+(?:acceptance\s+)?(?:criteria|conditions))'
    r'(?=\s*(?:[.;,!?]|$)|\s+(?:because|as|since)\b)', re.I)


class CoverageContractError(ValueError):
    """Missing required slots cannot fit; the caller must stop, not replan."""

    def __init__(self, assessment_count, missing_overall_slots, *, comparison=False):
        self.assessment_count = assessment_count
        self.missing_overall_slots = list(missing_overall_slots)
        self.missing_review_slots = list(missing_overall_slots)
        category = 'review' if comparison else 'overall-verdict'
        super().__init__(f'Required {category} slots exceed the eight-assessment capacity; report withheld.')


def _covers_overall(statement, subject, subjects):
    names = '|'.join(re.escape(name) for name in sorted(subjects, key=len, reverse=True))
    named = re.compile(rf'(?<![A-Za-z0-9_-])(?:{names})(?![A-Za-z0-9_-])', re.I)
    # Keep question punctuation on its own candidate's sentence. Only a named
    # candidate at a comma boundary starts a new local subject; names inside
    # "because ..." must not cut a subject away from its predicate.
    # A newline is whitespace, not a sentence boundary: "passes acceptance\n
    # latency metric" must not become an overall proposition by truncation.
    for sentence in re.split(r'(?<=[。！？!?；;])|(?<=\.)(?=\s|$)', statement):
        mentions = list(named.finditer(sentence))
        anchors = [i for i, name in enumerate(mentions)
                   if i == 0 or re.search(r'[，,]\s*$', sentence[:name.start()])]
        for index, position in enumerate(anchors):
            name = mentions[position]
            next_position = anchors[index + 1] if index + 1 < len(anchors) else len(mentions)
            end = mentions[next_position].start() if next_position < len(mentions) else len(sentence)
            members = [name]
            for other in mentions[position + 1:next_position]:
                # Explicit joint subjects share the same scope and predicate.
                # A causal object, for example "Birch 因 Maple ... 而 ...", is
                # neither a new subject nor a joint member.
                connector = sentence[members[-1].end():other.start()]
                if not re.fullmatch(r'\s*(?:和|与|及|、|&|\band\b)\s*', connector, re.I):
                    break
                members.append(other)
            if subject.casefold() not in {member.group().casefold() for member in members}:
                continue
            owned = sentence[name.start():end]
            shared_prefix = sentence[:mentions[0].start()]
            # Preceding conditions may govern a subsequent candidate, whereas
            # another candidate's question does not. A condition after this
            # candidate stays in owned unless a new named candidate begins.
            if (_CONDITIONAL_OVERALL.search(sentence[:name.start()] + owned)
                    or _QUESTION_OVERALL.search(shared_prefix + owned)):
                continue
            if _ROW_OVERALL.search(owned):
                return True
            # Retain true or false whole-candidate propositions for review;
            # numeric truth and source entailment are not decided here.
            predicate = sentence[members[-1].end():end]
            if _DIRECT_OVERALL_ZH.search(predicate) or _DIRECT_OVERALL_EN.search(predicate):
                return True
    return False


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


_PREVIOUS = re.compile(r'上一轮|上轮|上次|此前|先前|原先|之前|\b(?:previous|prior|earlier) (?:answer|conclusion|turn)\b', re.I)
_CHANGE = re.compile(r'哪些(?:需要|需|应)?(?:变更|改变|更改|修改|调整)|\bwhich (?:\w+ ){0,3}chang(?:e|ed|es)\b', re.I)
_RETAIN = re.compile(r'哪些(?:可以|可|需要|应)?(?:保留|不变|沿用)|\bwhich (?:\w+ ){0,3}(?:remain|stay|retain|keep)\b', re.I)
_NO_COMPARISON = re.compile(r'(?:不要|无需|不必|不用).{0,12}(?:比较|对比|变更|保留)|'
                            r'\b(?:do not|don.t|no need to) (?:compare|contrast|list changes)\b', re.I)
# Deliberately bounded list grammar, rather than trying to parse arbitrary prose.
_ZH_COMPARISON_LIST = re.compile(
    r'(?:指出|说明|列出|回答|判断|比较)\s*(?P<items>[^。！？?!；;\n]{1,150}?)'
    r'(?:这[两二三四五六2-6]项)?中\s*(?=哪些)')
_EN_COMPARISON_LIST = re.compile(
    r'\bfor (?P<items>[^.;!?\n]{1,150}?),\s*(?:state|identify|explain|list)\s+which\b', re.I)
_COMPARE_PREDICATE = re.compile(
    r'(?:从|由)[^。！？?!；;\n]{1,45}(?:改为|变为|变更为|调整为|变成|到|至|→)|'
    r'(?:需要|需|应)(?:变更|修改|调整)|(?:保持不变|维持不变|仍然|仍为|沿用|保留)|'
    r'(?:变更|改变|变化|更改|修改|调整)(?:为|成|了|：|:)|'
    r'\b(?:chang(?:ed|es)?|retain(?:ed|s)?|remain(?:s|ed)?|unchanged|kept|keep)\b', re.I)
_QUESTION_ONLY = re.compile(r'[？?]|吗|是否|哪(?:些|个)|如何|待(?:核实|判断|复核)|请(?:确认|判断)|'
                            r'\b(?:whether|which|what|please|to be reviewed)\b', re.I)
_UNCERTAIN_COMPARISON = re.compile(r'(?:无法|不能|不足以|尚不清楚|尚不能).{0,18}(?:变更|改变|保留|不变)|'
                                    r'\b(?:cannot|unable to|unknown).{0,40}(?:change|retain|unchanged)', re.I)


def comparison_requirements(question):
    """Extract only an explicit bounded list asking what changes and stays.

    Item text comes only from the current question. We do not infer previous
    values, scrape documents for requirements, or decide a comparison's truth.
    Unsupported, negated, or ambiguous list shapes are left to model judgment.
    """
    if (not _PREVIOUS.search(question) or not _CHANGE.search(question)
            or not _RETAIN.search(question) or _NO_COMPARISON.search(question)):
        return []
    matches = list(_ZH_COMPARISON_LIST.finditer(question))
    english = False
    if not matches:
        matches = list(_EN_COMPARISON_LIST.finditer(question))
        english = True
    if len(matches) != 1:
        return []
    listed = matches[0].group('items')
    # The nearest directive is the start of the list, e.g. "修正...，明确指出A、B".
    if not english:
        listed = re.split(r'(?:指出|说明|列出|回答|判断|比较)', listed)[-1]
        splitter = r'、|，|,|\s*(?:和|及|与)\s*'
    else:
        splitter = r',\s*(?:and\s+)?|\s+and\s+'
    subjects = [item.strip(' \t:：') for item in re.split(splitter, listed) if item.strip(' \t:：')]
    if (not 2 <= len(subjects) <= 6 or len({s.casefold() for s in subjects}) != len(subjects)
            or any(not 1 <= len(s) <= 36 or re.search(r'[。！？?!；;：:\n]', s) for s in subjects)):
        return []
    return [dict(subject=subject,
                 statement=f'{subject}：与上一轮相比的变更或保留结论及依据。') for subject in subjects]


def _find_item(text, subject):
    # Whitespace tolerance only; aliases or semantic equivalence need review.
    pattern = r'\s*'.join(re.escape(part) for part in subject.split())
    if subject[0].isascii() and subject[0].isalnum():
        pattern = r'(?<![A-Za-z0-9_-])' + pattern
    if subject[-1].isascii() and subject[-1].isalnum():
        pattern += r'(?![A-Za-z0-9_-])'
    return re.search(pattern, text, re.I)


def _mentions_item(text, subject):
    return _find_item(text, subject) is not None


def _has_comparison_predicate(text):
    return bool(_UNCERTAIN_COMPARISON.search(text) or
                (_COMPARE_PREDICATE.search(text) and not _QUESTION_ONLY.search(text)))


def _covers_comparison(row, requirement, required):
    if row['statement'] == requirement['statement']:
        return True  # Idempotent server question; still explicitly not an answer.
    subject = requirement['subject']
    texts = (row['statement'], row.get('explanation', ''))
    other_items = [r['subject'] for r in required if r['subject'] != subject]
    for text in texts:
        for clause in re.split(r'(?<=[，,。！？?!；;\n])', text):
            mention = _find_item(clause, subject)
            if mention is None:
                continue
            tail = clause[mention.end():]
            # A different requested metric starts a different local predicate,
            # even without punctuation: "timeout=20 and threads changed ...".
            boundaries = [found.start() for item in other_items
                          if (found := _find_item(tail, item)) is not None]
            local = tail[:min(boundaries)] if boundaries else tail
            if _has_comparison_predicate(local):
                return True
    # A short metric heading may own its explanation, unless that explanation
    # explicitly switches to another requested item. Never borrow a neighbour's
    # "changed" marker merely because both occur somewhere in a long report.
    statement, explanation = texts
    return (_mentions_item(statement, subject)
            and not any(_mentions_item(statement, item) for item in other_items)
            and not any(_mentions_item(explanation, item) for item in other_items)
            and _has_comparison_predicate(explanation))


def prepare_review_draft(question, draft):
    """Keep existing statements and append only bounded neutral question slots.

    Fixed-statement review supplies verdicts and selected evidence. Presence of
    a lexical relation is not semantic coverage. Overfull reports fail without
    deleting metrics or requesting another billed model call.
    """
    prepared = copy.deepcopy(draft)
    required = overall_requirements(question)
    comparisons = comparison_requirements(question)
    added = []
    for requirement in required:
        if any(_covers_overall(item['statement'], requirement['subject'], [r['subject'] for r in required])
               for item in prepared['assessments']):
            continue
        prepared['assessments'].append(dict(statement=requirement['statement'], verdict='insufficient',
            explanation='待复核本轮要求的总体结论；这不是资料缺失判定。', basis='documented_fact',
            citation_ids=[], quotes=[]))
        added.append(requirement['statement'])
    for requirement in comparisons:
        if any(_covers_comparison(item, requirement, comparisons) for item in prepared['assessments']):
            continue
        prepared['assessments'].append(dict(statement=requirement['statement'], verdict='insufficient',
            explanation='待核验本轮明确要求的新旧对比；这不是资料缺失判定。依据当前资料判断变更或保留；'
                        '旧回答不是证据，资料不足或矛盾时应明确说明，不猜测旧值。',
            basis='documented_fact', citation_ids=[], quotes=[]))
        added.append(requirement['statement'])
    if len(prepared['assessments']) > 8:
        raise CoverageContractError(len(draft['assessments']), added, comparison=bool(comparisons))
    return prepared, dict(version=VERSION, requested_overall=requirements_as_statements(required),
        requested_comparisons=requirements_as_statements(comparisons), added_review_slots=added,
        scope='Bounded explicit overall and enumerated change/retain requests; presence is not semantic coverage or entailment',
        semantic_coverage_verified=False)


def requirements_as_statements(requirements):
    return [item['statement'] for item in requirements]
