"""Bounded evidence-scope and user-reference checks, not semantic certification.

The measurement check operates only on selected, already validated quotations.
It distinguishes a record not containing a value from a claim that an experiment
never measured it. It deliberately requires local subject/metric anchors for an
explicit nonmeasurement claim; it does not search other documents to rescue one.
The reference check reads user questions only, never answers or pinned memory.
"""
import hashlib
import re
from pathlib import Path

VERSION = 'evidence-scope-v1'
IMPLEMENTATION_SHA256 = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()

_IDENTIFIER = r'[A-Za-z][A-Za-z0-9]*(?:[-_][A-Za-z0-9]+)+'
_QUOTED = r'[“\"][^”\"\n]{1,40}[”\"]'
_PROPER = r'[A-Z][a-z]{1,30}'
_NUMBER_ID = r'[A-Za-z]{1,12}[0-9]{1,12}'
_LOWER_ID_PREFIX = re.compile(r'^(?:gpu|cpu|node|host|run|job|task|server|worker|model|experiment|device|cluster)[-_]', re.I)
_RUN_ID = re.compile(r'^(?:r|run|trial|experiment)[-_]?\d+(?:[-_]\d+)*$', re.I)
_COMPOUND_PROSE = frozenset({'long-term', 'short-term', 'out-of-sample', 'in-sample',
    'a-priori', 'read-only', 'write-only', 'end-to-end', 'well-known', 'real-time'})
_NAME = rf'(?:{_IDENTIFIER}|{_QUOTED}|{_PROPER})'
_JOIN = r'(?:和|与|、|以及|\band\b|\bor\b)'
_PAIR = re.compile(rf'(?<![A-Za-z0-9_-])(?P<left>{_NAME})\s*{_JOIN}\s*(?P<right>{_NAME})(?![A-Za-z0-9_-])')
_ID_PAIR = re.compile(rf'(?<![A-Za-z0-9_-])(?P<left>{_IDENTIFIER})\s*{_JOIN}\s*'
                      rf'(?:[A-Z][a-z]+\s+){{0,3}}(?P<right>{_IDENTIFIER})(?![A-Za-z0-9_-])')
_SINGULAR = re.compile(r'它(?!们)|那(?:台|个|次|份)(?:机器|设备|主机|记录|运行)?|这台(?:机器|设备|主机)|'
                       r'\b(?:it|that (?:machine|device|host|run|record)|this (?:machine|device|host))\b', re.I)
_PLURAL = re.compile(r'两台|两者|二者|它们|分别|各自|\b(?:both|each|respectively)\b', re.I)
_PAIR_CUE = re.compile(r'候选|方案|机器|设备|节点|比较|对比|并列|同时|\b(?:compare|comparison|candidates?|machines?|devices?|hosts?|nodes?)\b', re.I)
# Plain title-cased prose is not a reliable named entity. Unknown lowercase names
# and CJK names without quotes remain outside this deliberately limited grammar.
_ORDINARY = frozenset('a an the this that these those it its they their input output read write '
    'latency throughput accuracy precision recall success failure speed time duration cost energy '
    'maximum minimum average median mean configuration evidence source report result results '
    'candidate candidates machine device node host run record records first second latest '
    'current previous old new local remote required optional supported contradicted insufficient '
    'available unavailable true false yes no pass fail valid invalid ready complete pending '
    'power recovery retry retries training test tests testing measurement measurements metric '
    'before after only both each all final overall compare comparison and or not for of in on at '
    'is are was were has had can cannot did does do please according table package document '
    'english chinese historical unknown missing none limit threshold actual observed transport '
    'but however whether never no measurement ms seconds latency what which why how when where '
    'such also then therefore additionally meanwhile given if else data status note formal interim draft'.split())


def _name_ok(name):
    if name.startswith(('“', '"')):
        return True
    if '-' in name or '_' in name:
        if name.casefold() in _COMPOUND_PROSE:
            return False
        return bool(_LOWER_ID_PREFIX.search(name)) or any(ch.isupper() or ch.isdigit() for ch in name)
    return name.casefold() not in _ORDINARY


def _pairs(text):
    matches = []
    # Prefer the identifier on the far side of a short title (R-17 and Archive
    # Restore R-09), rather than incorrectly using the ordinary word Archive.
    for pattern in (_ID_PAIR, _PAIR):
        for match in pattern.finditer(text):
            if any(match.start() < end and match.end() > start for start, end, _ in matches):
                continue
            names = [match[key].strip('“”"') for key in ('left', 'right')]
            if names[0].casefold() == names[1].casefold() or not all(_name_ok(match[key]) for key in ('left', 'right')):
                continue
            plain = any(re.fullmatch(_PROPER, match[key]) for key in ('left', 'right'))
            if plain and not _PAIR_CUE.search(text):
                continue
            matches.append((match.start(), match.end(), names))
    return sorted(matches)


def named_user_pairs(text):
    """Return bounded user-name pairs as (start, end, [left, right]) tuples.

    This shared lexical primitive does not establish that a pair remains the
    final requested scope; callers must respect subsequent user corrections.
    """
    return _pairs(text)


def _contains_name(text, name):
    return bool(re.search(rf'(?<![A-Za-z0-9_-]){re.escape(name)}(?![A-Za-z0-9_-])', text, re.I))


def _question_of(turn):
    return turn.get('question', '') if isinstance(turn, dict) else getattr(turn, 'question', '')


def _latest_question(context):
    turns = context.get('turns', []) if isinstance(context, dict) else getattr(context, 'turns', [])
    return _question_of(turns[-1]) if turns else ''


def user_reference_contract(question, context=None):
    """Force clarification only for an unresolved singular request after a pair.

    A current-turn pair supersedes a previous single target. Plural context in an
    earlier sentence ("both remain candidates") does not make a later singular
    request plural. Explicit naming/corrections in the request remain authoritative.
    """
    choices, rule = [], 'no_narrow_ambiguity_rule_matched'
    clauses = [part.strip() for part in re.split(r'[。！？?!；;\n]', question) if part.strip()]
    referent_clauses = [part for part in clauses if _SINGULAR.search(part) or _PLURAL.search(part)]
    requested = referent_clauses[-1] if referent_clauses else ''
    if _SINGULAR.search(requested) and not _PLURAL.search(requested):
        current_pairs = _pairs(question)
        previous = _latest_question(context)
        pairs = current_pairs or _pairs(previous)
        antecedent = question if current_pairs else previous
        if len(pairs) == 1:
            _, end, names = pairs[0]
            # A specific subject in the singular request or a later repeated
            # subject after the pair is a correction/narrowing: let the model
            # follow it. The initial "withdraw Dune-only" before a NEW pair is
            # intentionally not a later correction.
            later = antecedent[end:]
            named_request = [name for name in names if _contains_name(requested, name)]
            current_mentions = [name for name in names if _contains_name(question, name)]
            narrowed = (len(named_request) == 1 or any(_contains_name(later, name) for name in names)
                        or (not current_pairs and bool(current_mentions)))
            if not narrowed:
                choices = names
                rule = ('current_user_parallel_targets_with_singular_reference' if current_pairs
                        else 'parallel_user_targets_with_singular_reference')
    return dict(version=VERSION, implementation_sha256=IMPLEMENTATION_SHA256,
                requires_clarification=bool(choices), rule=rule, choices=choices,
                scope='narrow user-message lexical guard; not general referent resolution',
                draft_coverage_verified=False)


# A negative observation is itself a historical assertion, even when the row's
# verdict is insufficient. A missing value/record, by contrast, says nothing about
# whether the underlying experiment performed that measurement.
_NEGATIVE = re.compile(
    r'未(?:曾|实际|进行过|进行)?测(?:量|试)|没有(?:进行过|进行|实际)?测(?:量|试)|从未测(?:量|试)|'
    r'未(?:开展|进行)(?:过)?[^，,。.;；\n]{0,30}(?:测量|测试)|'
    r'\b(?:was|were|is|are)\s+(?:not|never)\s+(?:measured|tested)\b|'
    r'\b(?:has|have|had)\s+(?:not|never)\s+been\s+(?:measured|tested)\b|'
    r'\b(?:did|does|do)\s+not\s+(?:measure|test)\b|'
    r'\bnever\s+(?:measured|tested)\b|\bunmeasured\b|'
    r'\bno\s+(?:measurement|test)\s+(?:was|has been|had been)\s+(?:made|taken|performed|conducted|run)\b', re.I)
_UNCERTAIN = re.compile(r'无法(?:确认|判断|确定)|不能(?:确认|判断|确定|证明|认定)|不(?:代表|意味|等于)|'
                        r'(?:没有|尚无|无)证据(?:表明|说明|证明)|'
                        r'是否|可能|尚不清楚|未知|\b(?:cannot|can\x27t|could not|unable to|whether|unknown|unclear|'
                        r'does not (?:prove|imply|mean|establish|show|say|state)|(?:not|no) evidence that|may|might|possibly)\b', re.I)
_ATTRIBUTED = re.compile(r'(?:资料|报告|记录|表格|文档)[^，,。.;；\n]{0,24}(?:称|说|写明|记载|记为|声称)|'
                        r'\b(?:source|report|record|table|document)\b[^,.;\n]{0,35}\b(?:says|states|claims|reports|records)\b', re.I)
_METRICS = (
    ('recovery', re.compile(r'恢复|\b(?:recovery|restore)\b', re.I)),
    ('power_failure', re.compile(r'停电|断电|掉电|\b(?:power\s+(?:failure|failures|loss|outage)|blackout)\b', re.I)),
    ('latency', re.compile(r'延迟|时延|\blatency\b', re.I)),
    ('duration', re.compile(r'时间|耗时|时长|\b(?:time|duration)\b', re.I)),
    ('success_rate', re.compile(r'成功率|\bsuccess\s+rate\b', re.I)),
    ('accuracy', re.compile(r'准确率|正确率|\baccuracy\b', re.I)),
    ('temperature', re.compile(r'温度|\btemperature\b', re.I)),
    ('energy', re.compile(r'能耗|耗电|节电|\benergy\b', re.I)),
    ('throughput', re.compile(r'吞吐|\bthroughput\b', re.I)),
    ('memory', re.compile(r'内存|显存|\bmemory\b', re.I)),
    ('power', re.compile(r'功耗|\bpower\b(?!\s+(?:failure|failures|loss|outage))', re.I)),
    ('cost', re.compile(r'费用|成本|\bcost\b', re.I)),
    ('median', re.compile(r'中位|\bmedian\b', re.I)),
    ('maximum', re.compile(r'最大|最长|\b(?:maximum|max|longest)\b', re.I)),
    ('minimum', re.compile(r'最小|最短|\b(?:minimum|min|shortest)\b', re.I)),
    ('p95', re.compile(r'\bp95\b|95(?:th)?\s*percentile', re.I)),
    ('p99', re.compile(r'\bp99\b|99(?:th)?\s*percentile', re.I)),
)
_MEASURED = re.compile(r'已(?:经)?测(?:量|得)|实际测(?:量|得)|记录(?:到|为)|测得|'
                       r'\b(?:was|were|is|are)\s+measured\b|\b(?:measured|observed|recorded)\b', re.I)


def _clauses(text):
    # Do not split decimal numbers. Commas and adversative boundaries prevent a
    # later uncertainty disclaimer from licensing an earlier historical claim.
    parts = [part.strip() for part in re.split(r'[，,。;；\n]|(?<!\d)\.(?!\d)|\b(?:but|however|whereas|while)\b|但是|但|而', text, flags=re.I)
             if part.strip()]
    clauses, prefix = [], ''
    for part in parts:
        # Preserve a scoped run prefix across the natural comma in "In R-17,
        # Sable ...". Dropping R-17 here would let a quote from R-09 match.
        if re.fullmatch(rf'(?:in|during|for|under|在)?\s*(?:{_IDENTIFIER}|{_NUMBER_ID})', part, re.I):
            prefix = (prefix + ' ' + part).strip()
            continue
        clauses.append((prefix + ', ' + part) if prefix else part)
        prefix = ''
    if prefix:
        clauses.append(prefix)
    return clauses


def _negative_claims(text):
    for clause in _clauses(text):
        match = _NEGATIVE.search(clause)
        if match and not _UNCERTAIN.search(clause[:match.start()]):
            yield clause


def _subjects(text):
    names = {match.group().casefold() for match in re.finditer(
        rf'(?<![A-Za-z0-9_-])(?:{_IDENTIFIER}|{_NUMBER_ID}|{_PROPER}|[A-Z]{{2,12}})(?![A-Za-z0-9_-])', text)
        if _name_ok(match.group())}
    return names


def _metrics(text):
    return {name for name, pattern in _METRICS if pattern.search(text)}


def _binding_matches(claim, statement, source):
    # Require BOTH the asserted run/object and the proposition's named target.
    # A quote about Quill cannot license Sable, nor R-09 a claim about R-17.
    subjects = _subjects(claim) | _subjects(statement)
    source_subjects = _subjects(source)
    if not subjects or not subjects <= source_subjects:
        return False
    # Do not let another object's negative clause license this object's claim.
    # One extra run label is permissible when the claim did not name a run;
    # extra candidate names or multiple competing runs are not.
    extra = source_subjects - subjects
    if extra and (len(extra) != 1 or not all(_RUN_ID.fullmatch(name) for name in extra)
                  or any(_RUN_ID.fullmatch(name) for name in subjects)):
        return False
    claim_metrics = _metrics(claim)
    if not claim_metrics:
        claim_metrics = _metrics(statement)
    source_metrics = _metrics(source)
    # Unknown metric language requires an exact copied clause, not a generic
    # "not measured" phrase somewhere in a selected passage.
    if not claim_metrics:
        return claim.strip().casefold() == source.strip().casefold()
    return claim_metrics == source_metrics


def measurement_scope_findings(raw):
    """Return narrow unsafe-negative findings without altering source or prose.

    Check each explanation regardless of verdict. Only supported statements are
    positive assertions: an insufficient/contradicted proposition may legitimately
    ask whether a measurement did not happen. Exact quotation validity is the
    caller's responsibility. An empty result is not proof of semantic entailment.
    """
    findings = []
    for index, item in enumerate(raw.get('assessments', [])):
        assertion_text = item.get('explanation', '')
        if item.get('verdict') == 'supported':
            assertion_text += '\n' + item.get('statement', '')
        selected = [clause for quote in item.get('quotes', []) for clause in _clauses(quote.get('quote', ''))]
        for claim in dict.fromkeys(_negative_claims(assertion_text)):
            matching = [clause for clause in selected if _binding_matches(claim, item.get('statement', ''), clause)]
            support = [clause for clause in matching if list(_negative_claims(clause))]
            conflict = [clause for clause in matching if _MEASURED.search(clause) and not _NEGATIVE.search(clause)]
            if not support or (conflict and not _ATTRIBUTED.search(claim)):
                findings.append(dict(assessment_index=index,
                    rule=('selected_measurement_scope_conflict' if support and conflict
                          else 'historical_nonmeasurement_not_supported_by_selected_quotes'),
                    claim=claim, matching_explicit_nonmeasurement=len(support),
                    matching_positive_measurement=len(conflict),
                    scope='selected clauses with matching named subjects and metric signatures; not semantic entailment'))
    return findings
