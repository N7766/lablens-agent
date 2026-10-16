"""Finite full-clause checks for named comparisons and evidence descriptions.

These rules compare declared operations, not free-form answers or general intent.
They deliberately do not resolve selected-result references or parse negation.
"""
import json
import re
from collections import Counter

from .plan_checks import clauses


SCOPE = ('Limited explicit Chinese named-pair comparison and evidence-description rules only; '
         'unrecognized clauses and general semantics are not validated')
QUOTES = ('“', '”', '‘', '’', '"', "'", '「', '」', '『', '』', '`')
PAIR = re.compile(r'以(?P<baseline>run:[a-zA-Z0-9_-]+)为基线比较'
                  r'(?P<candidate>run:[a-zA-Z0-9_-]+)的'
                  r'(?:正确题数差|准确率差|平均长度减少比例)')
DESCRIPTIONS = frozenset({'说明长度单位与数据来源', '说明验证集和显著性边界', '说明缺失字段与证据边界'})


def extract_tasks(question, *, run_ids):
    """Recognize whole clauses only, with pair names present in this snapshot."""
    parts = clauses(question)
    units, unassessed = [], []
    known = set(run_ids)
    quoted = any(mark in question for mark in QUOTES)
    for part in parts:
        text = part['text']
        match = PAIR.fullmatch(text)
        if quoted:
            reason = '包含引用标记；当前规则不解析引用上下文，需人工核对'
        elif match and {match['baseline'], match['candidate']} <= known:
            units.append(dict(**part, kind='compare', expected=dict(operation='compare',
                baseline=match['baseline'], candidate=match['candidate'])))
            continue
        elif text in DESCRIPTIONS:
            units.append(dict(**part, kind='describe', expected=dict(operation='describe')))
            continue
        elif match:
            reason = '显式比较包含当前快照不存在的记录 ID；该句未核对'
        else:
            reason = '未完整匹配支持的比较或说明句式；该句需人工核对'
        unassessed.append(dict(clause_id=part['id'], text=text, reason=reason))
    for index, unit in enumerate(units, 1):
        unit['clause_id'] = unit.pop('id')
        unit['id'] = f't{index}'
    return dict(scope=SCOPE, units=units, unassessed=unassessed)


def _overlaps(question, excerpt, unit):
    # Exact substrings can repeat. Every occurrence matters: choosing a short
    # excerpt or one spanning a delimiter cannot make an extra operation vanish.
    if not excerpt or not excerpt.strip():
        return False
    start = question.find(excerpt)
    while start != -1:
        if start < unit['end'] and start + len(excerpt) > unit['start']:
            return True
        start = question.find(excerpt, start + 1)
    return False


def _actual(step):
    return dict(step_id=step.id, **step.model_dump(exclude={'id', 'request_excerpt'}))


def check_tasks(question, plan, *, run_ids):
    """Check operation kind and named comparison direction without changing a plan."""
    report = extract_tasks(question, run_ids=run_ids)
    counts = Counter(unit['text'] for unit in report['units'])
    related, accounted = set(), set()
    for unit in report['units']:
        associated = [step for step in plan.steps if _overlaps(question, step.request_excerpt, unit)]
        exact = [step for step in associated if step.request_excerpt == unit['text']]
        unhandled = [index for index, item in enumerate(plan.unhandled)
                     if _overlaps(question, item.request_excerpt, unit)]
        related.update(step.id for step in associated)
        differences = []
        unit.update(step_ids=[step.id for step in associated], actual=[_actual(step) for step in associated],
                    unhandled_indexes=unhandled, differences=differences)
        if counts[unit['text']] > 1:
            state = 'ambiguous'
            differences.append('同一完整句子重复出现，编译计划缺少可区分的关联；需人工核对')
        elif not exact:
            state = 'missing'
            differences.append('没有引用该完整句子的执行步骤；部分引用不能确认该任务已处理')
        elif len(exact) > 1:
            accounted.update(step.id for step in exact)
            state = 'ambiguous'
            differences.append('多个步骤对应同一任务，无法唯一核对；请合并或明确关联')
        else:
            step = exact[0]
            accounted.add(step.id)
            if step.kind != unit['kind']:
                differences.append(f"{step.id} 的 operation 应为 {unit['kind']!r}，实际为 {step.kind!r}")
            elif step.kind == 'compare':
                for field in ('baseline', 'candidate'):
                    reference = getattr(step, field)
                    expected = unit['expected'][field]
                    if reference.run_id != expected or reference.query_step is not None:
                        differences.append(f'{step.id} 的 {field} 应为显式记录 {expected!r}，实际为 {reference.model_dump()!r}')
            state = 'conflict' if differences else 'matched'
        if len(associated) > len(exact):
            differences.append('存在部分引用或跨句引用的额外步骤，不能据此确认完整任务')
            if state == 'matched':
                state = 'conflict'
        if unhandled:
            differences.append('该已支持的任务被标记为未处理，不能计为已完成')
            if state == 'matched':
                state = 'conflict'
        unit['state'] = state
    report['unexpected_step_ids'] = [step.id for step in plan.steps if step.id in related - accounted]
    report['has_conflicts'] = bool(report['unexpected_step_ids']) or any(unit['state'] != 'matched' for unit in report['units'])
    report['status'] = 'conflict' if report['has_conflicts'] else ('checked' if report['units'] else 'not_assessed')
    report['semantic_validation'] = 'not_performed'
    return report


def task_conflict_feedback(report):
    """Return fields that the model can actually edit in a replacement proposal."""
    conflicts = [dict(task_id=unit['id'], state=unit['state'], step_ids=unit['step_ids'],
                      proposal_fields=dict(clause_id=unit['clause_id'], **unit['expected']))
                 for unit in report['units'] if unit['state'] != 'matched']
    return 'Limited task-rule conflict: ' + json.dumps(dict(
        conflicts=conflicts, unexpected_step_ids=report['unexpected_step_ids'],
        instruction='Return one full replacement proposal covering every clause. Use proposal_fields as action fields. '
                    'A named pair requires the explicit baseline and candidate run IDs in that direction, not query references. '
                    'A recognized describe task needs one describe action, not unsupported. Remove extra actions for these tasks. '
                    'General semantic correctness is not validated.'), ensure_ascii=False, separators=(',', ':'))
