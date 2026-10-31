"""Bounded, selected-quote relative-decrease arithmetic, not semantic proof.

Only ``(baseline - candidate) / baseline * 100 = result%`` is supported.
The named candidate, positive baseline and operand units must have unambiguous
literal measurements in the selected quotes. We never execute model text, search
other documents for matching numbers, or infer missing operands. Source authority,
population comparability and the meaning of the surrounding prose remain review
and evaluation responsibilities.
"""
from dataclasses import dataclass
from decimal import Decimal, DecimalException, localcontext
import re


VERSION = 'selected-percentage-arithmetic-v1'
MAX_PROSE_CHARS = 32_000
MAX_QUOTE_CHARS = 128_000
MAX_EXPRESSIONS = 16
NUMBER = r'[-+]?(?:\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)(?:[eE][-+]?\d+)?'
UNIT = r'(?:kWh|Wh|kW|W|milliseconds?|ms|seconds?|s|kilobytes?|KB|megabytes?|MB|gigabytes?|GB|毫秒|秒|千瓦时|瓦时|千瓦|瓦)'
PERCENT = r'(?:%|％|percent|百分比)(?![A-Za-z])'
EQUALS = r'(?:约等于|等于|=|≈)'
EXPRESSION = re.compile(
    rf'\(\s*(?P<baseline>{NUMBER})\s*(?P<baseline_unit>{UNIT})?\s*[-−]\s*'
    rf'(?P<candidate>{NUMBER})\s*(?P<candidate_unit>{UNIT})?\s*\)\s*[/÷]\s*'
    rf'(?P<denominator>{NUMBER})\s*(?P<denominator_unit>{UNIT})?\s*[×*]\s*'
    rf'(?P<factor>{NUMBER})\s*{EQUALS}\s*(?P<result>{NUMBER})\s*{PERCENT}', re.I)
# Recognize the surrounding shape before accepting the strict grammar. This also
# records missing operands, unknown operators and code-like expressions as a
# failure, even when their result happens to equal a quoted threshold.
CANDIDATE_EXPRESSION = re.compile(
    rf'\([^()\n]{{1,120}}\)\s*[/÷][^=≈\n]{{1,120}}?{EQUALS}\s*{NUMBER}\s*{PERCENT}', re.I)
FACT = re.compile(
    rf'(?<![A-Za-z0-9_-])(?P<subject>[A-Za-z][A-Za-z0-9_-]{{0,39}}|基线|基准)\s*'
    rf'(?:(?:consumed|uses?|used|measured|recorded|energy consumption|energy|latency|consumption)'
    rf'(?:\s+(?:was|is|of))?|(?:能耗|耗电量|消耗|延迟)(?:为|是)?|[:：])\s*[:=：]?\s*'
    rf'(?P<value>{NUMBER})\s*(?P<unit>{UNIT})(?![A-Za-z])', re.I)
BASELINE_NAMES = {'baseline', '基线', '基准'}
REDUCTION = re.compile(r'节电|节能|减少|下降|降低|降幅|降耗|\b(?:reduction|decrease|saving|savings)\b', re.I)
NEGATION = re.compile(r'(?:\bnot|\bno|\bunknown|\bunconfirmed|未确认|无法确认|不是|并非)\s*$', re.I)
UNIT_ALIASES = {
    '千瓦时': 'kwh', '瓦时': 'wh', '千瓦': 'kw', '瓦': 'w',
    '毫秒': 'ms', 'millisecond': 'ms', 'milliseconds': 'ms',
    '秒': 's', 'second': 's', 'seconds': 's',
    'kilobyte': 'kb', 'kilobytes': 'kb', 'megabyte': 'mb', 'megabytes': 'mb',
    'gigabyte': 'gb', 'gigabytes': 'gb',
}


@dataclass(frozen=True)
class PercentageInspection:
    """Derived numbers are for the numeric guard; findings must also be checked."""

    derived_quantities: frozenset
    accepted: tuple
    findings: tuple


def _number(text):
    if len(text) > 80:
        return None
    try:
        value = Decimal(text.replace(',', ''))
        if (not value.is_finite() or len(value.as_tuple().digits) > 18
                or abs(value.adjusted()) > 30 or abs(value.as_tuple().exponent) > 30):
            return None
        return value
    except DecimalException:
        return None


def _unit(text):
    value = text.casefold()
    return UNIT_ALIASES.get(value, value)


def _facts(quotes):
    facts = {}
    for match in FACT.finditer(quotes):
        if NEGATION.search(quotes[max(0, match.start() - 24):match.start()]):
            continue
        number = _number(match['value'])
        if number is None:
            continue
        key = match['subject'].casefold()
        facts.setdefault(key, set()).add((number, _unit(match['unit'])))
    return facts


def _mentions(name, text):
    # CJK may abut an English equipment name; another Latin identifier may not.
    return bool(re.search(rf'(?<![A-Za-z0-9_-]){re.escape(name)}(?![A-Za-z0-9_-])', text, re.I))


def inspect_percentage_derivations(prose, quotes):
    """Inspect explicit relative decreases using only the selected quote text.

    Supported measurements have a deliberately small grammar, for example
    ``Baseline consumed 240 kWh. Dune consumed 208 kWh.`` or
    ``Baseline能耗240 kWh，Dune能耗208 kWh``. One assessment must identify one
    non-baseline source subject. Conflicts, unnamed subjects, unit conversions,
    multiple populations and alternative formula shapes are withheld. An empty
    result means this grammar established no derivation, not that evidence is
    missing. A finding is a bounded-check failure, not a corrected conclusion.
    """
    if len(prose) > MAX_PROSE_CHARS or len(quotes) > MAX_QUOTE_CHARS:
        return PercentageInspection(frozenset(), (), ({'rule': 'percentage_input_limit_exceeded'},))
    candidates = list(CANDIDATE_EXPRESSION.finditer(prose))
    if len(candidates) > MAX_EXPRESSIONS:
        return PercentageInspection(frozenset(), (), ({'rule': 'percentage_expression_limit_exceeded'},))
    if not candidates:
        return PercentageInspection(frozenset(), (), ())
    facts = _facts(quotes)
    baseline_facts = {fact for name in BASELINE_NAMES for fact in facts.get(name, set())}
    targets = [name for name in facts if name not in BASELINE_NAMES and _mentions(name, prose)]
    accepted, findings, derived = [], [], set()
    for candidate in candidates:
        expression = candidate.group()
        match = EXPRESSION.fullmatch(expression)
        reason = None
        if match is None:
            reason = 'percentage_expression_outside_bounded_grammar'
        elif not REDUCTION.search(prose):
            reason = 'percentage_reduction_scope_not_explicit'
        elif len(baseline_facts) != 1:
            reason = 'percentage_baseline_measurement_missing_or_ambiguous'
        elif len(targets) != 1:
            reason = 'percentage_candidate_subject_missing_or_ambiguous'
        elif len(facts[targets[0]]) != 1:
            reason = 'percentage_candidate_measurement_ambiguous'
        if reason:
            findings.append({'rule': reason, 'expression': expression})
            continue
        baseline_value, baseline_unit = next(iter(baseline_facts))
        candidate_value, candidate_unit = next(iter(facts[targets[0]]))
        values = {name: _number(match[name]) for name in ('baseline', 'candidate', 'denominator', 'factor', 'result')}
        if any(value is None for value in values.values()):
            reason = 'percentage_quantity_outside_bounded_parser'
        elif baseline_value <= 0 or candidate_value < 0:
            reason = 'percentage_nonpositive_baseline_or_negative_measurement'
        elif baseline_unit != candidate_unit or any(
                match[name] and _unit(match[name]) != baseline_unit
                for name in ('baseline_unit', 'candidate_unit', 'denominator_unit')):
            reason = 'percentage_operand_unit_mismatch'
        elif values['baseline'] != baseline_value or values['candidate'] != candidate_value:
            reason = 'percentage_operand_subject_mismatch'
        elif values['denominator'] != baseline_value:
            reason = 'percentage_denominator_is_not_baseline'
        elif values['factor'] != 100:
            reason = 'percentage_scale_is_not_100'
        else:
            try:
                with localcontext() as context:
                    context.prec = 80
                    calculated = (baseline_value - candidate_value) / baseline_value * 100
                    tolerance = Decimal(5).scaleb(values['result'].as_tuple().exponent - 1)
                    if abs(calculated - values['result']) > tolerance:
                        reason = 'percentage_result_incorrect'
                    else:
                        derived.add(('ratio', values['result'] / 100))
            except (DecimalException, ZeroDivisionError):
                reason = 'percentage_arithmetic_outside_bounded_parser'
        if reason:
            findings.append({'rule': reason, 'expression': expression})
        else:
            accepted.append({'expression': expression, 'subject': targets[0],
                'baseline': str(baseline_value), 'candidate': str(candidate_value), 'unit': baseline_unit,
                'result_percent': str(values['result']), 'rounding_tolerance_percent': str(tolerance),
                'scope': 'literal selected-quote operands and explicit relative-decrease arithmetic only'})
    return PercentageInspection(frozenset(derived), tuple(accepted), tuple(findings))
