"""Narrow, auditable answer obligations; never a semantic entailment judge.

Only user-message referents, review scope and selected-quote numeric anchors are
checked here, with bounded explicit-pair substitution and scalar-token bindings.
Named baseline reductions additionally bind literal operands;
historical nonmeasurement claims require local scope anchors. Matching numbers
alone do not prove the subject, metric, polarity or
meaning of a claim. A failed check withholds prose; it never manufactures a
citation by finding the same number elsewhere in the retrieved documents.
"""
import copy
import hashlib
import re
from decimal import Decimal, DecimalException
from pathlib import Path

from .evidence_arithmetic import inspect_percentage_derivations
from .evidence_binding import inspect_evidence_binding
from .evidence_derivations import inspect_report_derivations
from .evidence_scope import measurement_scope_findings, user_reference_contract


VERSION = 'answer-obligations-v4'
QUALITY_VERSION = 'selected-evidence-checks-v4'
IMPLEMENTATION_SHA256 = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
IDENTIFIER = r'[A-Za-z][A-Za-z0-9]*(?:[-_][A-Za-z0-9]+)+'


def answer_contract(question, context):
    """Delegate bounded current/previous USER reference checks to their module."""
    return user_reference_contract(question, context)


# Unit-bearing quantities only: p95, run IDs, dates and sample counts are not
# interpreted as metric values. Decimal conversion avoids float tolerance drift.
NUMBER = r'[-+]?(?:\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)(?:[eE][-+]?\d+)?'
UNIT = r'(?:milliseconds?|毫秒|ms|seconds?|秒|s|watts?|瓦(?:特)?|W|percent|百分比|%|％|kilobytes?|KB|megabytes?|MB|gigabytes?|GB)'
QUANTITY = re.compile(rf'(?<![A-Za-z0-9_.,])(?P<number>{NUMBER})\s*(?P<unit>{UNIT})(?![A-Za-z])', re.I)
PLAIN_NUMBER = re.compile(rf'(?<![A-Za-z0-9_.,-]){NUMBER}(?![A-Za-z0-9_]|[.,]\d)')
EQUATION = re.compile(rf'(?<![A-Za-z0-9_.,])(?P<a>{NUMBER})\s*(?P<ua>{UNIT})?\s*'
    rf'(?P<op>[+−\-×*/÷])\s*(?P<b>{NUMBER})\s*(?P<ub>{UNIT})?\s*'
    rf'(?:=|≈|等于|约等于)\s*(?P<c>{NUMBER})\s*(?P<uc>{UNIT})?', re.I)


def _unit(value):
    unit = (value or '').casefold()
    if unit in {'ms', '毫秒', 'millisecond', 'milliseconds'}:
        return 'time', Decimal('.001')
    if unit in {'s', '秒', 'second', 'seconds'}:
        return 'time', Decimal(1)
    if unit in {'w', '瓦', '瓦特', 'watt', 'watts'}:
        return 'power', Decimal(1)
    if unit in {'%', '％', 'percent', '百分比'}:
        return 'ratio', Decimal('.01')
    for prefix, factor in [('k', 1000), ('m', 1000000), ('g', 1000000000)]:
        if unit.startswith(prefix):
            return 'bytes', Decimal(factor)
    return '', Decimal(1)


def _quantities(text):
    values = []
    for match in QUANTITY.finditer(text):
        number = _decimal(match['number'])
        if number is not None:
            kind, factor = _unit(match['unit'])
            values.append((kind, number * factor, match.group()))
    return values


def _decimal(text):
    """Bound attacker/model supplied arithmetic before creating large values."""
    if len(text) > 80:
        return None
    try:
        number = Decimal(text.replace(',', ''))
        if (not number.is_finite() or len(number.as_tuple().digits) > 18
                or abs(number.adjusted()) > 30 or abs(number.as_tuple().exponent) > 30):
            return None
        return number
    except DecimalException:
        return None


def _arithmetic_results(prose, quotes):
    """Accept explicitly shown simple arithmetic, with selected-source operands.

    This checks arithmetic and numeric provenance only, not whether the operands
    describe comparable populations. Rounding is bounded by the written result's
    final decimal place. Chained expressions and natural-language arithmetic are
    intentionally outside the grammar and need explicit equations.
    """
    quoted_quantities = {(kind, number) for kind, number, _ in _quantities(quotes)}
    quoted_scalars = {value for match in PLAIN_NUMBER.finditer(quotes) if (value := _decimal(match.group())) is not None}
    derived = set()
    for match in EQUATION.finditer(prose):
        try:
            a, b, c = (_decimal(match[name]) for name in ('a', 'b', 'c'))
            if any(value is None for value in (a, b, c)):
                continue
            ka, fa = _unit(match['ua']); kb, fb = _unit(match['ub']); kc, fc = _unit(match['uc'])
            # A trailing unit in "96 - 80 = 16 ms" applies to both operands.
            if match['op'] in {'+', '-', '−'} and kc:
                if not ka: ka, fa = kc, fc
                if not kb: kb, fb = kc, fc
            if ((ka, a * fa) not in quoted_quantities if ka else a not in quoted_scalars):
                continue
            if ((kb, b * fb) not in quoted_quantities if kb else b not in quoted_scalars):
                continue
            if match['op'] in {'+', '-', '−'}:
                if ka != kb or kc != ka:
                    continue
                calculated = a * fa + b * fb if match['op'] == '+' else a * fa - b * fb
            elif match['op'] in {'/', '÷'}:
                if not b or ka != kb or kc not in {'', 'ratio'}:
                    continue
                calculated = (a * fa) / (b * fb)
            else:
                if ka and kb or kc != (ka or kb):
                    continue
                calculated = a * fa * b * fb
            # Half a unit in the last written decimal place permits rounding.
            tolerance = Decimal(5).scaleb(c.as_tuple().exponent - 1) * fc
            if abs(calculated - c * fc) <= tolerance:
                derived.add((kc, c * fc))
        except (DecimalException, ZeroDivisionError):
            continue
    # Also recognize a short difference sentence with all three quantities
    # explicitly present. Direction and comparability remain semantic work.
    for clause in re.split(r'[。;；\n]', prose):
        terms = _quantities(clause)
        if len(terms) == 3 and re.search(r'差|高|低|少|多|\b(?:difference|more|less|slower|faster)\b', clause, re.I):
            (ka, a, _), (kb, b, _), (kc, c, _) = terms
            if ka == kb == kc and (ka, a) in quoted_quantities and (kb, b) in quoted_quantities and abs(a - b) == c:
                derived.add((kc, c))
    return derived


def _selected_row_findings(raw, allowed_spans=None):
    findings = []
    for index, item in enumerate(raw['assessments']):
        # Insufficient claims may discuss a hypothetical or unknown value; this
        # numeric rule must not turn "cannot confirm 80 ms" into a false error.
        # Contradicted propositions may also contain the disproved value. Only
        # the explanation of a positive supported claim is in this rule's scope.
        if item['verdict'] != 'supported':
            continue
        quotes = '\n'.join(q['quote'] for q in item.get('quotes', []))
        anchored = {(kind, value) for kind, value, _ in _quantities(quotes)}
        explanation = item['statement'] + '\n' + item['explanation']
        percentage = inspect_percentage_derivations(explanation, quotes)
        derived = _arithmetic_results(explanation, quotes) | percentage.derived_quantities
        findings.extend(dict(assessment_index=index, **finding) for finding in percentage.findings)
        asserted = []
        out_of_range = []
        for match in QUANTITY.finditer(explanation):
            # Explicit rejected/hypothetical values are not positive numeric
            # observations. "not above 80 ms" still asserts an 80-ms boundary.
            before = explanation[max(0, match.start() - 24):match.start()]
            after = explanation[match.end():match.end() + 24]
            if re.search(r'(?:不是|并非|无法确认|不能确认|\bnot(?: equal to)?|\bcannot confirm)\s*$', before, re.I):
                continue
            if re.match(r'\s*(?:未测量|未经测量|was not measured|is not measured)', after, re.I):
                continue
            if _decimal(match['number']) is None:
                out_of_range.append(match.group())
                continue
            if match.span() not in (allowed_spans or {}).get(index, set()):
                asserted.extend(_quantities(match.group()))
        missing = list(dict.fromkeys(text for kind, value, text in asserted if (kind, value) not in anchored | derived))
        if missing:
            findings.append(dict(assessment_index=index, rule='quantity_not_anchored_in_selected_quotes', quantities=missing))
        if out_of_range:
            findings.append(dict(assessment_index=index, rule='quantity_outside_bounded_parser', quantities=list(dict.fromkeys(out_of_range))))
    return findings + measurement_scope_findings(raw)


def _selected_checks(raw, *, producer_eligible_rows=None, blocked_rows=()):
    # The first pass has no cross-row allowances. A row failing its independent
    # checks must never produce a certificate, even if another row could rescue
    # it later. This keeps dependencies strictly one hop and order independent.
    initial = _selected_row_findings(raw)
    blocked = set(blocked_rows) | {f['assessment_index'] for f in initial}
    derivations = inspect_report_derivations(raw,
        producer_eligible_rows=producer_eligible_rows, blocked_rows=blocked)
    final = _selected_row_findings(raw, derivations.allowed_spans) if derivations.allowed_spans else initial
    return final, derivations.audit


def selected_evidence_findings(raw):
    # Without actual hit metadata there is no final-validation eligibility proof.
    # Keep the public draft check conservative; only final review may reuse a
    # verified formula, and each consumer still selects its own exact evidence.
    return _selected_checks(raw)[0]


def make_review_contract(question, draft):
    statements = [item['statement'] for item in draft['assessments']]
    if len(statements) != len(set(statements)):
        raise ValueError('Draft assessment statements must be distinct for scoped review')
    binding = inspect_evidence_binding(question, draft)
    return dict(version=VERSION, statements=statements, exact_statement_set=True,
        evidence_binding=binding,
        derivation_dependencies=dict(state='unverified', reason='producer_final_validation_unavailable_at_draft'),
        draft_coverage_verified=False, draft_findings=selected_evidence_findings(draft),
        rules=['Keep exactly these statements, one each. Correct verdict, explanation and selected passages; do not add rows or unrequested propositions. An explicit current comparison may name both user-requested scopes in an existing explanation, using current source evidence for each.',
               'A threshold comparison needs its measurement AND its threshold in the selected passages of EACH assessment.',
               'Do not spread a conflict or missing authority for one aggregate metric to nonconflicting subgroups or other metrics.',
               'Do not add new named subjects when refusing an existing proposition.',
               'Do not substitute peer facts for the named subject. Verify the subject-specific proposition from selected passages, including pronoun and contrast interpretation; selecting both names alone is not proof of correct binding.',
               'For a positive current configuration claim using a literal snake_case key=value, select the passage with THAT exact key and scalar value. Another field with the same number is not support. If no source supports the row, label only that proposition insufficient and explain the precise citation gap.',
               'For a calculated difference or ratio show an explicit equation using source operands, e.g. 96 - 80 = 16 ms or 1/600 = 0.17%.',
               'The numeric check is narrow: passing it is not proof of entailment, coverage, units, subject identity or source authenticity.'])


def check_review_scope(question, draft, reviewed):
    """Require the review to edit existing propositions, not invent new ones."""
    wanted = [item['statement'] for item in draft['assessments']]
    actual = [item['statement'] for item in reviewed['assessments']]
    if sorted(actual) != sorted(wanted):
        raise ValueError('Review must preserve each original proposition exactly once')
    originals = {item['statement']: item for item in draft['assessments']}
    findings = []
    for index, item in enumerate(reviewed['assessments']):
        if item['verdict'] != 'insufficient':
            continue
        original = originals[item['statement']]
        allowed = {name.casefold() for name in re.findall(IDENTIFIER, question + ' ' + original['statement'] + ' ' + original['explanation'])}
        explanation = item['explanation']
        if not re.search(r'无权威|有效性|无效|无法|不能确认|未测量|未提供|没有.{0,15}(?:记录|资料|观测|证据)|缺少|'
                         r'\b(?:invalid|validity|authorit(?:y|ative)|cannot|unmeasured|missing|no (?:record|evidence|measurement))\b', explanation, re.I):
            continue
        # A newly cited run/source name is provenance, not automatically a new
        # subject. This exemption is grammatical and narrow, not source trust.
        provenance = re.findall(rf'(?:according to|source|report|根据|来自|资料|记录|引用)\s*({IDENTIFIER})', explanation, re.I)
        provenance += re.findall(rf'(?:缺少|missing)\s*({IDENTIFIER})\s*(?:的(?:观测|日志|记录|版本)|(?:observations?|logs?|records?|version))', explanation, re.I)
        accepted = allowed | {name.casefold() for name in provenance}
        # Plain hyphenated prose (long-term, out-of-sample, a-priori) is not a
        # named subject. This intentionally misses some all-lowercase compound
        # entity names; it is an identifier-shape guard, not an NER classifier.
        def identifier_shape(name):
            return (any(char.isupper() or char.isdigit() for char in name) or '_' in name
                    or len(name.rsplit('-', 1)[-1]) == 1)
        introduced = sorted({name for name in re.findall(IDENTIFIER, explanation)
            if name.casefold() not in accepted and identifier_shape(name)})
        if introduced:
            findings.append(dict(assessment_index=index, rule='insufficient_review_introduced_named_subject', identifiers=introduced))
    return findings


def enforce_review_checks(question, draft, reviewed, *, hits=None):
    binding = inspect_evidence_binding(question, reviewed)
    scope_findings = check_review_scope(question, draft, reviewed)
    eligible = None
    if hits is not None:
        from .assessment import validate_assessments
        validated = validate_assessments(question, reviewed, hits)
        eligible = set()
        for index, item in enumerate(reviewed['assessments']):
            # A final validator may withhold a cause, config-only history,
            # mislabeled test result, or normalize a quote. Such a row does not
            # license a dependency on its pre-validation form.
            if item.get('verdict') != 'supported':
                continue
            if any(all(actual.get(key) == item.get(key) for key in
                       ('statement', 'verdict', 'explanation', 'basis', 'quotes', 'citation_ids'))
                   for actual in validated['assessments']):
                eligible.add(index)
    numeric, derivations = _selected_checks(reviewed, producer_eligible_rows=eligible,
        blocked_rows={finding['assessment_index'] for finding in scope_findings + binding['findings']})
    findings = scope_findings + numeric + binding['findings']
    protected = copy.deepcopy(reviewed)
    withheld = []
    for index in sorted({finding['assessment_index'] for finding in findings}):
        original = reviewed['assessments'][index]
        withheld.append(dict(assessment_index=index, original_verdict=original['verdict'],
                            original_explanation=original['explanation']))
    for finding in findings:
        item = protected['assessments'][finding['assessment_index']]
        item['verdict'] = 'insufficient'
        item['explanation'] = ('本条生成内容未通过有限的引文/审阅范围检查，原结论未发布。'
            '这不表示工作区缺少资料，也不否定已有观测；需要核对本条所选引文与问题范围。')
    return protected, dict(version=QUALITY_VERSION, implementation_sha256=IMPLEMENTATION_SHA256,
        checked_assessments=len(reviewed['assessments']),
        findings=findings, evidence_binding=binding, derivation_dependencies=derivations, withheld_originals=withheld,
        semantic_entailment_verified=False)
