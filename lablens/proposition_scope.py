"""Bounded configuration/history scope checks, not an entailment classifier.

Never rewrite a proposition or invent its truth from a keyword. An unsupported
language shape is explicitly unclassified. A finding can require the existing
review to repair a row, or stop publication, but cannot certify a correct answer.
"""
import hashlib
import re
from pathlib import Path

VERSION = 'configuration-history-scope-v1'
IMPLEMENTATION_SHA256 = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
INSTRUCTIONS = '''Keep current configuration facts and historical execution as separate propositions with matching bases.
Missing historical logs cannot justify refusing a current configuration value. Do not combine these
scopes in one statement. If an insufficient explanation mentions a concrete setting from a source,
select that setting's passage too; empty evidence_ids do not establish that contextual fact.
'''
_CONFIG = re.compile(r'配置|\bconfig(?:uration)?\b|\bsettings?\b', re.I)
_RUNTIME = re.compile(r'历史(?:运行|执行|行为)|实际(?:运行|执行|生效)|运行时|对应运行|'
                      r'\bhistorical (?:run|execution|behavio[u]?r)|\bruntime\b|'
                      r'\bexecut(?:ed|ion)\b|\b(?:actual|past|previous) runs?\b', re.I)
_MISSING = re.compile(r'缺少|未提供|没有提供|没有.{0,15}(?:日志|记录|证据)|无法|不能|需要|'
                      r'\b(?:missing|without|unavailable|unknown|cannot|no|not (?:provided|supplied)|need)\b', re.I)
_CONFIG_GAP = re.compile(r'(?:缺少|未提供|没有提供)(?:当前|对应|该|现有)?配置(?:文件|快照|值|记录)?(?=[，。；;,.\s]|$)|'
                         r'(?:配置|设置)(?:文件|记录|值|快照)?(?:本身|当前|对应|尚|还|也|仍|存在|实际|是|均|\s){0,8}'
                         r'(?:未提供|没有提供|缺失|不明|未知|冲突|不可见)|'
                         r'\b(?:missing|no|without) (?:current )?(?:config(?:uration)?|setting)|'
                         r'\b(?:config(?:uration)?|setting)(?: snapshot| file| value| record| is| was| has| been| still| currently| also| itself| remains){0,8}'
                         r' (?:missing|unavailable|not (?:provided|supplied)|unknown|conflict)', re.I)


class PropositionScopeError(ValueError):
    def __init__(self, audit, stage):
        self.audit, self.stage = audit, stage
        super().__init__('Configuration/history proposition scope is inconsistent; report withheld.')


def statement_scope(statement):
    """Recognize explicit words only; basis is deliberately not an input."""
    # "runtime configuration" names a setting; the noun phrase itself does
    # not assert that a historical execution actually used that setting.
    statement = re.sub(r'运行时(?:的)?配置|\bruntime (?:configuration|settings?)\b',
                       '配置', statement, flags=re.I)
    parts = re.split(r'[，,；;]|并且|且|\band\b', statement, flags=re.I)
    if (any(_CONFIG.search(p) and not _RUNTIME.search(p) for p in parts)
            and any(_RUNTIME.search(p) and not _CONFIG.search(p) for p in parts)):
        return 'mixed_configuration_and_execution'
    if _RUNTIME.search(statement):
        return 'historical_execution'
    if _CONFIG.search(statement):
        return 'current_configuration'
    return 'unclassified'


def inspect_proposition_scopes(raw):
    # RAG's unscoped draft is untrusted model JSON, unlike resolved Agent rows.
    # Validate the shape consumed here before indexing; never leak KeyError or
    # TypeError instead of a controlled, usage-preserving model failure.
    if (not isinstance(raw, dict) or not isinstance(raw.get('assessments'), list)
            or not 1 <= len(raw['assessments']) <= 8
            or any(not isinstance(row, dict) or any(not isinstance(row.get(key), str)
                   for key in ('statement', 'explanation', 'basis', 'verdict'))
                   for row in raw['assessments'])):
        raise ValueError('Scope inspection requires a bounded list of assessment objects with textual fields.')
    rows, findings = [], []
    for index, item in enumerate(raw['assessments']):
        statement = item['statement']
        scope = statement_scope(statement)
        rows.append(dict(assessment_index=index, statement_scope=scope, basis=item['basis'],
                         statement_sha256=hashlib.sha256(statement.encode()).hexdigest()))
        rules = []
        if scope == 'mixed_configuration_and_execution':
            rules.append('mixed_configuration_and_execution_proposition')
        if scope == 'historical_execution' and item['basis'] == 'current_configuration':
            rules.append('configuration_basis_on_runtime_proposition')
        if scope == 'current_configuration':
            if item['basis'] == 'historical_observation':
                rules.append('historical_basis_on_configuration_proposition')
            if (item['verdict'] == 'insufficient' and _RUNTIME.search(item['explanation'])
                    and _MISSING.search(item['explanation']) and not _CONFIG_GAP.search(item['explanation'])):
                rules.append('configuration_refused_for_missing_runtime_evidence')
        findings.extend(dict(assessment_index=index, rule=rule) for rule in rules)
    return dict(version=VERSION, implementation_sha256=IMPLEMENTATION_SHA256, rows=rows, findings=findings,
                scope='Explicit configuration/history language only; unclassified text remains unverified. '
                      'No proof of source truth, polarity, run identity, question coverage or semantic correctness.',
                semantic_correctness_verified=False, question_coverage_verified=False)


def require_consistent_proposition_scopes(raw, *, stage):
    audit = inspect_proposition_scopes(raw)
    if audit['findings']:
        raise PropositionScopeError(audit, stage)
    return audit


def require_reviewable_propositions(raw):
    """A fixed-statement reviewer cannot split an explicit joint proposition."""
    audit = inspect_proposition_scopes(raw)
    if any(f['rule'] == 'mixed_configuration_and_execution_proposition' for f in audit['findings']):
        raise PropositionScopeError(audit, 'draft_contract')
    return audit
