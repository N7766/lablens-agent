"""One-hop reuse of explicitly checked energy-reduction arithmetic.

This is a numeric provenance contract, not an entailment checker. A consumer
selects its own exact operand excerpts and an explicit formula rule. No source,
number, equation or conclusion is inserted into an assessment. Only one named
subject, energy units Wh/kWh and a small affirmative reduction grammar qualify.
All other shapes stay unverified under the pre-existing numeric checks.
"""
from dataclasses import dataclass
from decimal import Decimal
import hashlib
import re
from pathlib import Path

from .evidence_arithmetic import (BASELINE_NAMES, FACT, _mentions,
                                  inspect_percentage_derivations)


VERSION = 'selected-energy-derivations-v1'
IMPLEMENTATION_SHA256 = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
MAX_ROWS = 16
MAX_PROSE_CHARS = 32_000
MAX_QUOTE_CHARS = 128_000
MAX_QUOTES = 16
ENERGY = r'(?:节电(?:百分比|率)?|节能(?:百分比|率)?|能耗(?:降幅|降低比例|降低百分比)|energy\s+(?:reduction|savings?))'
REDUCTION_LABEL = re.compile(ENERGY, re.I)
REUSED_PERCENT = re.compile(
    ENERGY + r'\s*(?:(?:为|是|达到|is|was|of)\s*|[:：=]\s*)?'
    r'(?P<quantity>(?P<value>[-+]?\d{1,6}(?:\.\d{1,6})?)\s*(?:%|％|percent)(?![A-Za-z0-9_.]))', re.I)
UNCERTAIN = re.compile(
    r'\b(?:if|unless|suppose|assuming|hypothetical|hypothetically|would|might|could|'
    r'not|no|unknown|unconfirmed|conflicting|contradictory|incorrect|false|untrue|counterfactual)\b|'
    r'如果|假设|假如|若|可能|不确定|未经确认|未确认|无法确认|不能确认|并非|不是|错误|未知|矛盾|冲突', re.I)
DISALLOWED_PROSE = re.compile(r'根本原因|导致|由.{0,50}引起|root cause|caused?\b|'
    r'训练时|运行时|动态计算|动态重算|每一?步|recomput|runtime|executed|相同模型|同一模型|same (?:model|weights)', re.I)
# A complete, affirmative imperative clause; do not recognize a formula copied
# inside a negation, condition or alternative. Deliberately not a rule-language
# interpreter. Names, thresholds and pass/fail predicates are never hardcoded.
FORMULA_RULE = re.compile(
    r'Use\s+\(baseline\s+energy\s+minus\s+candidate\s+energy\)\s+'
    r'divided\s+by\s+baseline\s+energy,\s*times\s+100', re.I)
CLAUSE_BREAK = re.compile(r'\.(?!\d)|[。;；\n]')


@dataclass(frozen=True)
class DerivationInspection:
    """Span offsets address statement + newline + explanation, never all ratios."""
    allowed_spans: dict
    audit: dict


def _sha(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def _quote_key(quote):
    return quote['chunk_id'], _sha(quote['quote'])


def _identity(keys):
    return [dict(chunk_id=chunk_id, quote_sha256=digest) for chunk_id, digest in sorted(keys)]


def _quote_input(row):
    quotes = row.get('quotes', [])
    ids = row.get('citation_ids', [])
    if (not isinstance(quotes, list) or not quotes or len(quotes) > MAX_QUOTES
            or not isinstance(ids, list) or len(ids) != len(set(ids))):
        return None
    if any(not isinstance(q, dict) or not isinstance(q.get('chunk_id'), str)
           or not q['chunk_id'] or not isinstance(q.get('quote'), str) or not q['quote']
           for q in quotes):
        return None
    if set(ids) != {q['chunk_id'] for q in quotes}:
        return None
    if sum(len(q['quote']) for q in quotes) > MAX_QUOTE_CHARS:
        return None
    return quotes


def _operand_keys(quotes, subject):
    """Bind exact excerpts containing both named operands; reject qualifiers.

    Exact selected excerpts are the unit of identity, not an entire chunk or a
    coincidentally equal scalar in a different excerpt. This cannot detect a
    qualifier omitted upstream from the selected excerpt; source trust is false.
    """
    keys, names = set(), set()
    wanted = BASELINE_NAMES | {subject}
    for quote in quotes:
        facts = [m for m in FACT.finditer(quote['quote']) if m['subject'].casefold() in wanted]
        if not facts:
            continue
        if UNCERTAIN.search(quote['quote']):
            return None
        names.update(m['subject'].casefold() for m in facts)
        keys.add(_quote_key(quote))
    if subject not in names or not names.intersection(BASELINE_NAMES):
        return None
    return keys


def _rule_keys(quotes):
    keys = set()
    for quote in quotes:
        for clause in CLAUSE_BREAK.split(quote['quote']):
            if FORMULA_RULE.fullmatch(clause.strip()):
                keys.add(_quote_key(quote))
    return keys


def _subjects(quotes, prose):
    names = {m['subject'].casefold() for q in quotes for m in FACT.finditer(q['quote'])}
    return {name for name in names - BASELINE_NAMES if _mentions(name, prose)}


def _standalone_expression(expression, prose):
    """Do not promote a recognized subexpression inside a larger calculation."""
    matches = list(re.finditer(re.escape(expression), prose))
    if not matches:
        return False
    for match in matches:
        left, right = prose[:match.start()].rstrip(), prose[match.end():].lstrip()
        if left and left[-1] in '+−-×*/÷^(':
            return False
        if (right and right[0] in '+−-×*/÷^)') or re.match(r'^[.,]\d', right):
            return False
    return True


def inspect_report_derivations(raw, *, producer_eligible_rows=None, blocked_rows=()):
    """Certify at most one hop from independently valid explicit formula rows.

    ``producer_eligible_rows`` must come from the final assessment validator
    with the actual source metadata. Without it no certificate is issued.
    Numeric/scope/binding failures are supplied separately in ``blocked_rows``.
    Reused rows can never produce certificates, so ordering/cycles add no power.
    """
    audit = dict(version=VERSION, implementation_sha256=IMPLEMENTATION_SHA256,
        certificates=[], dependencies=[], unverified=[],
        scope='one-hop same-subject energy-reduction arithmetic with reselected exact operands and an explicit formula rule',
        source_authenticity_verified=False, semantic_entailment_verified=False,
        acceptance_verified=False, transitive_dependencies_allowed=False)
    rows = raw.get('assessments', [])
    if not isinstance(rows, list) or len(rows) > MAX_ROWS:
        audit['unverified'].append(dict(reason='report_outside_bounded_parser'))
        return DerivationInspection({}, audit)
    if producer_eligible_rows is None:
        audit['unverified'].append(dict(reason='producer_final_validation_unavailable'))
        return DerivationInspection({}, audit)
    eligible = set(producer_eligible_rows) - set(blocked_rows)
    candidates = []
    for index, row in enumerate(rows):
        prose = row['statement'] + '\n' + row['explanation']
        if index not in eligible or row.get('verdict') != 'supported':
            continue
        if (row.get('basis') not in {'documented_fact', 'historical_observation', 'rule_evaluation'}
                or len(prose) > MAX_PROSE_CHARS or UNCERTAIN.search(prose)
                or DISALLOWED_PROSE.search(prose) or not REDUCTION_LABEL.search(prose)):
            audit['unverified'].append(dict(assessment_index=index, reason='producer_scope_not_eligible'))
            continue
        quotes = _quote_input(row)
        if quotes is None:
            continue
        inspection = inspect_percentage_derivations(prose, '\n'.join(q['quote'] for q in quotes))
        if inspection.findings:
            continue
        seen = set()
        for item in inspection.accepted:
            if item['unit'] not in {'kwh', 'wh'} or not _standalone_expression(item['expression'], prose):
                continue
            # A concrete source subject must appear in the proposition itself.
            if _subjects(quotes, row['statement']) != {item['subject']}:
                continue
            operands = _operand_keys(quotes, item['subject'])
            key = (item['subject'], item['baseline'], item['candidate'], item['unit'], item['result_percent'])
            if operands is None or key in seen:
                continue
            seen.add(key)
            rules = _rule_keys(quotes)
            certificate = dict(producer_assessment_index=index,
                producer_statement_sha256=_sha(row['statement']),
                expression=item['expression'], subject=item['subject'], quantity_type='energy_relative_reduction',
                baseline=item['baseline'], candidate=item['candidate'], unit=item['unit'],
                result_percent=item['result_percent'], rounding_tolerance_percent=item['rounding_tolerance_percent'],
                operand_sources=_identity(operands), producer_rule_sources=_identity(rules),
                producer_final_validation_passed=True)
            candidates.append((certificate, operands, rules))
            audit['certificates'].append(certificate)
    allowed = {}
    for index, row in enumerate(rows):
        if row.get('verdict') != 'supported':
            continue
        prose = row['statement'] + '\n' + row['explanation']
        if (len(prose) > MAX_PROSE_CHARS or UNCERTAIN.search(prose)
                or DISALLOWED_PROSE.search(prose)):
            continue
        quotes = _quote_input(row)
        if quotes is None:
            continue
        matches = list(REUSED_PERCENT.finditer(prose))
        if len(matches) != 1:
            continue
        match = matches[0]
        subject_names = _subjects(quotes, prose)
        rule_keys = _rule_keys(quotes)
        valid = []
        for certificate, operands, producer_rules in candidates:
            if certificate['producer_assessment_index'] == index:
                continue
            subject = certificate['subject']
            if (subject_names != {subject} or _subjects(quotes, row['statement']) != {subject}
                    or Decimal(match['value']) != Decimal(certificate['result_percent'])):
                continue
            own_operands = _operand_keys(quotes, subject)
            if own_operands != operands or not rule_keys or not producer_rules <= rule_keys:
                continue
            # Recheck the original explicit expression against this row's own
            # complete selected evidence. Never write it into the answer.
            checked = inspect_percentage_derivations(
                subject + ' energy reduction ' + certificate['expression'],
                '\n'.join(q['quote'] for q in quotes))
            if checked.findings or len(checked.accepted) != 1:
                continue
            original = certificate
            actual = checked.accepted[0]
            if any(actual[k] != original[k] for k in ('subject', 'baseline', 'candidate', 'unit', 'result_percent')):
                continue
            valid.append(certificate)
        if not valid:
            audit['unverified'].append(dict(assessment_index=index, reason='no_matching_independently_valid_derivation'))
            continue
        # Equivalent directly computed producers are harmless; select the
        # lowest source index deterministically and retain the full identity.
        certificate = min(valid, key=lambda c: c['producer_assessment_index'])
        span = match.span('quantity')
        allowed[index] = {span}
        audit['dependencies'].append(dict(consumer_assessment_index=index,
            consumer_statement_sha256=_sha(row['statement']), producer_assessment_index=certificate['producer_assessment_index'],
            subject=certificate['subject'], quantity_type=certificate['quantity_type'],
            result_percent=certificate['result_percent'], consumer_quantity=match['quantity'],
            consumer_quantity_span=list(span), operand_sources=certificate['operand_sources'],
            consumer_rule_sources=_identity(rule_keys), operation='relative_reduction_percent',
            rule_scope='explicit formula presence only; threshold, logic, scope and conclusion are not certified'))
    return DerivationInspection(allowed, audit)
