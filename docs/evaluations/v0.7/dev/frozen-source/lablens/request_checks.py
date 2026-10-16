"""Small, fully matched Chinese query grammar; deliberately not general intent validation."""
import re
from decimal import Decimal

from .plan_checks import clauses
from .schemas import QueryRuns

SCOPE = 'Limited explicit Chinese query rules only; unrecognized clauses and general semantics are not validated'
FILTER_KEYS = ('min_accuracy', 'max_accuracy', 'min_correct', 'max_length',
               'shorter_than_baseline', 'more_accurate_than_baseline', 'pareto_only')
NUMBER = r'(?:0|[1-9]\d*)(?:\.\d+)?'
FILTERS = [
    (rf'正确题数至少(?P<n>\d+)', 'min_correct', int, 1),
    (rf'准确率不高于(?P<n>{NUMBER})%', 'max_accuracy', float, .01),
    (rf'准确率至少(?P<n>{NUMBER})%', 'min_accuracy', float, .01),
    (rf'平均长度严格小于(?P<n>{NUMBER})词', 'max_length', float, 1),
]
EXTREMA = {'准确率最高': ('accuracy', True), '准确率最低': ('accuracy', False),
           '平均长度最长': ('mean_length', True), '平均长度最短': ('mean_length', False),
           '正确题数最多': ('correct', True), '正确题数最少': ('correct', False)}
METRICS = {'准确率': 'accuracy', '平均长度': 'mean_length', '正确题数': 'correct'}


def _selection(text):
    if re.fullmatch(r'(?:所有|全部)(?:记录|实验)', text):
        return dict(top_k=None), ['top_k']
    if re.fullmatch(r'(?:完整|全部)?Pareto集合(?:（准确率更高且长度更短）)?', text, re.I):
        return dict(top_k=None, pareto_only=True), ['top_k']
    extreme = re.fullmatch(r'(' + '|'.join(EXTREMA) + r')的(?:所有|全部)并列(?:记录|实验)', text)
    if extreme:
        metric, descending = EXTREMA[extreme[1]]
        return dict(order_by=metric, descending=descending, top_k=1, include_ties=True), ['top_k', 'order_by', 'descending', 'include_ties']
    top = re.fullmatch(r'记录[，,]按(准确率|平均长度|正确题数)(升序|降序)取前([1-9]\d*)条并保留边界并列', text)
    if top:
        return dict(order_by=METRICS[top[1]], descending=top[2]=='降序', top_k=int(top[3]), include_ties=True), ['top_k', 'order_by', 'descending', 'include_ties']
    return None


def _parse_unit(text, inherited=False):
    # Full-match only. Quotes, negation, alternatives and trailing exclusions cannot be ignored.
    command = re.match(r'^(?:分别)?(?:列出|查询|找出)', text)
    if command:
        text = text[command.end():]
    elif not inherited:
        return None
    selected = _selection(text)
    args = {}
    if selected is None:
        # The first 的 separates filters from selection; all filter tokens must be consumed.
        filters, sep, tail = text.partition('的')
        if not sep:
            return None
        for token in re.split(r'且', filters):
            matches = [(re.fullmatch(pattern, token), key, convert, scale) for pattern, key, convert, scale in FILTERS]
            found = [(m, key, convert, scale) for m, key, convert, scale in matches if m]
            if len(found) != 1:
                return None
            m, key, convert, scale = found[0]
            if key in args:
                return None
            args[key] = float(Decimal(m['n']) / 100) if scale == .01 else convert(m['n'])
            if key == 'min_correct': args[key] = int(args[key])
        selected = _selection(tail)
    if selected is None:
        return None
    selection, fields = selected
    try:
        expected = QueryRuns.model_validate({**args, **selection}).model_dump()
    except ValueError:
        return None
    return expected, fields


def extract_requests(question):
    units, unassessed = [], []
    for clause in clauses(question):
        # Only the explicit enumeration delimiter is supported, not arbitrary comma/conjunction NLP.
        spans = []
        start = 0
        for delimiter in re.finditer(r'[，,]以及', clause['text']):
            spans.append((start, delimiter.start())); start = delimiter.end()
        spans.append((start, len(clause['text'])))
        parsed = []
        for index, (left, right) in enumerate(spans):
            raw = clause['text'][left:right]; value = raw.strip()
            part = _parse_unit(value, inherited=index > 0)
            if part is None: break
            offset = clause['start'] + left + len(raw) - len(raw.lstrip())
            expected, fields = part
            parsed.append(dict(clause_id=clause['id'], text=value, start=offset, end=offset+len(value),
                               expected=expected, checked_fields=list(FILTER_KEYS)+fields))
        if len(parsed) != len(spans):
            unassessed.append(dict(clause_id=clause['id'], text=clause['text'], reason='未完整匹配支持句式；该句需人工核对'))
            continue
        signatures = [tuple(u['expected'][k] for k in FILTER_KEYS) for u in parsed]
        if len(set(signatures)) != len(signatures):
            unassessed.append(dict(clause_id=clause['id'], text=clause['text'], reason='同句查询的筛选范围相同，无法唯一关联步骤；需人工核对'))
            continue
        units.extend(parsed)
    for i, unit in enumerate(units, 1): unit['id'] = f'r{i}'
    return dict(scope=SCOPE, units=units, unassessed=unassessed)


def check_requests(question, plan):
    report = extract_requests(question)
    parts = {c['id']: c for c in clauses(question)}
    accounted = set()
    eligible = [s for s in plan.steps if s.kind == 'query' and any(
        s.request_excerpt in {parts[u['clause_id']]['text'], u['text']} for u in report['units'])]
    for unit in report['units']:
        candidates = [s for s in eligible
                      if s.request_excerpt in {parts[unit['clause_id']]['text'], unit['text']}
                      and all(getattr(s.query, key) == unit['expected'][key] for key in FILTER_KEYS)]
        unit.update(step_ids=[s.id for s in candidates], actual=[dict(step_id=s.id, query=s.query.model_dump()) for s in candidates], differences=[])
        accounted.update(s.id for s in candidates)
        if not candidates:
            unit['state'] = 'missing'; unit['differences'].append('没有找到筛选范围与该请求一致的查询步骤')
        elif len(candidates) > 1:
            unit['state'] = 'ambiguous'; unit['differences'].append('多个查询对应同一请求，无法唯一核对；请合并或明确关联')
        else:
            step = candidates[0]
            for key in unit['checked_fields']:
                actual, expected = getattr(step.query, key), unit['expected'][key]
                if actual != expected:
                    unit['differences'].append(f'{step.id} 的 {key} 应为 {expected!r}，实际为 {actual!r}')
            unit['state'] = 'conflict' if unit['differences'] else 'matched'
    report['unexpected_step_ids'] = [s.id for s in eligible if s.id not in accounted]
    # A recognized clause represented only by describe/unsupported is a missing query, not a pass.
    report['has_conflicts'] = bool(report['unexpected_step_ids']) or any(u['state'] != 'matched' for u in report['units'])
    report['status'] = 'conflict' if report['has_conflicts'] else ('checked' if report['units'] else 'not_assessed')
    report['semantic_validation'] = 'not_performed'
    return report


def conflict_feedback(report):
    lines = [f"{u['id']} “{u['text']}”: " + '；'.join(u['differences']) + f"; expected={u['expected']}"
             for u in report['units'] if u['state'] != 'matched']
    if report['unexpected_step_ids']:
        lines.append('多余或筛选范围不符的查询步骤：' + ', '.join(report['unexpected_step_ids']))
    return 'Limited request-rule conflict: ' + '\n'.join(lines)
