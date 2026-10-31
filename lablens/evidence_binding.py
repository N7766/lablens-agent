"""Bounded row-local bindings, not an entailment or source-authority judge.

Two contracts are intentionally small: names from one explicit current-user
pair cannot be replaced entirely by its peer in BOTH explanation and selected
quotes, and affirmative current-config
snake_case scalar assignments need that literal assignment in selected quotes.
Neither rule infers aliases, owners, predicates, source roles or truth. An
unmatched or ambiguous form is recorded as unverified, never counted as passed.
"""
import hashlib
import re
from decimal import Decimal, DecimalException
from pathlib import Path

from .evidence_scope import named_user_pairs

VERSION = 'row-local-literal-binding-v1'
IMPLEMENTATION_SHA256 = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()

# A configuration token grammar, not a prose fact extractor. Only snake_case
# names and scalar number/bool/null literals are in scope; no arbitrary strings,
# expressions, camelCase, natural-language settings or implicit default values.
_ASSIGNMENT = re.compile(
    r'(?<![A-Za-z0-9_])(?P<key>[A-Za-z][A-Za-z0-9]*(?:_[A-Za-z0-9]+)+)'
    r'["\'`]?\s*[:=]\s*(?P<value>true|false|null|[-+]?(?:\d+(?:\.\d+)?)(?:[eE][-+]?\d+)?)'
    # Do not call an expression or grouped integer a checked scalar merely
    # because its first operand/prefix matches. Used for claims AND sources.
    r'(?![A-Za-z0-9_]|\.\d|\s*[,，]\s*\d|\s*[+\-−*/×÷^%=<>|&±])', re.I)
_SENTENCE = re.compile(r'[。！？!?；;\n]|\.(?!\d)')
# Conservative exclusion of a literal embedded in a question, denial or
# hypothetical. This finite grammar deliberately skips whole sentences when
# these cues appear; it does not classify their semantic scope or polarity.
_NONASSERTIVE = re.compile(
    r'[?？]|如果|假如|假设|假定|若|是否|能否|不是|并非|不等于|不成立|不能确认|无法确认|例如|示例|'
    r'\b(?:if|unless|assuming|suppose|hypothetical|whether|not|cannot|unknown|'
    r'would|could|may|might|example)\b|\bis false\b', re.I)


def _mentions(text, names):
    return [name for name in names if re.search(
        rf'(?<![A-Za-z0-9_-]){re.escape(name)}(?![A-Za-z0-9_-])', text, re.I)]


def _scalar(value):
    if value.casefold() in {'true', 'false', 'null'}:
        return value.casefold()
    if len(value) > 64:
        return None
    try:
        number = Decimal(value)
        if (not number.is_finite() or len(number.as_tuple().digits) > 18
                or abs(number.adjusted()) > 30 or abs(number.as_tuple().exponent) > 30):
            return None
        # Decimal equality permits ordinary formatting differences, e.g. 2.0.
        return number
    except DecimalException:
        return None


def _assignments(text):
    rows = []
    for match in _ASSIGNMENT.finditer(text):
        value = _scalar(match['value'])
        if len(match['key']) <= 64 and value is not None:
            rows.append((match['key'], value, match.group()))
    return rows


def _positive_assignments(item):
    asserted, skipped = [], []
    for field in ('statement', 'explanation'):
        text = item[field]
        start = 0
        sentences = []
        for match in _SENTENCE.finditer(text):
            sentences.append(text[start:match.end()])
            start = match.end()
        sentences.append(text[start:])
        for sentence in sentences:
            assignments = _assignments(sentence)
            if not assignments:
                continue
            target = skipped if _NONASSERTIVE.search(sentence) else asserted
            for key, value, literal in assignments:
                if (key, value) not in {(a[0], a[1]) for a in target}:
                    target.append((key, value, literal))
    return asserted, skipped


def inspect_evidence_binding(question, raw):
    """Return findings plus per-row check/skip accounting without modifying rows.

    Subject checks require exactly one named pair in the CURRENT question and
    exactly one of those names in the statement. They reject only an observed
    all-peer substitution in both fields. Missing names, one-field peer mentions,
    co-mentions and aliases remain
    unverified. Config checks require a supported current_configuration row;
    insufficient/contradicted propositions are not affirmative assignments.
    """
    pairs = named_user_pairs(question)
    names = pairs[0][2] if len(pairs) == 1 else []
    findings, rows = [], []
    for index, item in enumerate(raw['assessments']):
        quotes = '\n'.join(quote['quote'] for quote in item.get('quotes', []))
        row = dict(assessment_index=index)
        expected = _mentions(item['statement'], names)
        if item['verdict'] == 'insufficient':
            row['subject'] = dict(state='unverified', reason='insufficient_proposition')
        elif len(names) != 2:
            row['subject'] = dict(state='unverified', reason='no_single_explicit_current_user_pair')
        elif len(expected) != 1:
            row['subject'] = dict(state='unverified', reason='statement_has_zero_or_multiple_pair_names')
        else:
            fields = {}
            mentions = dict(explanation=_mentions(item['explanation'], names),
                            selected_quotes=_mentions(quotes, names))
            peer_only = {field: bool(observed and expected[0] not in observed)
                         for field, observed in mentions.items()}
            substitution = all(peer_only.values())
            for field in ('explanation', 'selected_quotes'):
                observed = mentions[field]
                if substitution:
                    fields[field] = dict(state='violation', observed_subjects=observed)
                    findings.append(dict(assessment_index=index,
                        rule='explicit_subject_replaced_by_peer', field=field,
                        expected_subject=expected[0], observed_subjects=observed))
                elif observed == expected:
                    fields[field] = dict(state='checked_literal_presence', observed_subjects=observed)
                else:
                    fields[field] = dict(state='unverified', observed_subjects=observed,
                        reason=('both_names_present_no_predicate_binding' if len(observed) == 2
                                else 'peer_only_one_field_no_subject_resolution' if peer_only[field]
                                else 'no_pair_name_present_no_alias_resolution'))
            row['subject'] = dict(expected_subject=expected[0], fields=fields)

        if item['verdict'] != 'supported' or item.get('basis') != 'current_configuration':
            row['configuration'] = dict(state='unverified', reason='not_supported_current_configuration')
        else:
            asserted, skipped = _positive_assignments(item)
            selected = {(key, value) for key, value, _ in _assignments(quotes)}
            checks = []
            for key, value, literal in asserted:
                found = (key, value) in selected
                checks.append(dict(key=key, literal=literal,
                    state='checked_literal_presence' if found else 'violation'))
                if not found:
                    findings.append(dict(assessment_index=index,
                        rule='configuration_literal_not_in_selected_quotes', key=key, literal=literal))
            row['configuration'] = dict(checks=checks,
                skipped_literals=[dict(key=key, literal=literal,
                    reason='nonassertive_sentence_cue') for key, _, literal in skipped])
            if not checks:
                row['configuration'].update(state='unverified',
                    reason='no_in_scope_affirmative_scalar_assignment')
        rows.append(row)
    return dict(version=VERSION, implementation_sha256=IMPLEMENTATION_SHA256,
        scope='One current-user named pair and positive current-config snake_case scalar literals; selected quotes only',
        named_pair=names, assessments=rows, findings=findings,
        semantic_entailment_verified=False, subject_predicate_binding_verified=False,
        source_authority_verified=False)
