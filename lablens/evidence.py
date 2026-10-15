"""Deterministic checks for structured numeric evidence, not free-text entailment."""
import math
import re
from .schemas import Answer

METRICS = {
    'accuracy': ('fraction', 'absolute', 0.00005),
    'correct': ('count', 'absolute', 0),
    'num_samples': ('count', 'absolute', 0),
    'mean_length': (None, 'absolute', 0.005),
    'accuracy_delta_percentage_points': ('percentage_points', 'candidate_minus_baseline', 0.005),
    'additional_correct': ('count', 'candidate_minus_baseline', 0),
    'length_reduction_percent': ('percent', 'baseline_minus_candidate_over_baseline', 0.005),
}


def validate_answer(answer: Answer, outputs: list[dict]):
    seen = {s for o in outputs for s in o.get('source_ids', [])}
    runs = {r['id']: r for o in outputs for r in o.get('runs', [])}
    comparisons = {}
    for o in outputs:
        if 'baseline' in o and 'candidate' in o:
            a, b = o['baseline'], o['candidate']
            runs.update({a['id']: a, b['id']: b})
            comparisons[a['id'], b['id']] = o
    if answer.status == 'answered' and not answer.claims:
        raise ValueError('answered requires at least one evidenced claim')
    for claim in answer.claims:
        if set(claim.source_ids) - seen:
            raise ValueError('Unknown or unread source IDs')
        numeric_text = claim.text
        for source_id in claim.source_ids:
            numeric_text = numeric_text.replace(source_id, '')
        if re.search(r'\d', numeric_text) and not claim.measurements:
            raise ValueError('Claims containing numeric text require structured measurements; use tools to read the relevant runs')
        for m in claim.measurements:
            unit, direction, tolerance = METRICS[m.metric]
            ids = {m.baseline_id} | ({m.candidate_id} if m.candidate_id else set())
            if not ids <= set(claim.source_ids) or not ids <= seen:
                raise ValueError('Measurement baseline_id and candidate_id must both be in this claim source_ids and returned by tools; required IDs: ' + ', '.join(sorted(ids)))
            if direction == 'absolute':
                if m.candidate_id is not None or m.baseline_id not in runs:
                    raise ValueError('Absolute metric requires one observed run and candidate_id=null')
                row = runs[m.baseline_id]
                expected = row[m.metric]
                if m.metric == 'mean_length':
                    unit = row['length_unit']
            else:
                pair = (m.baseline_id, m.candidate_id)
                if pair not in comparisons:
                    raise ValueError('Comparison must match an observed compare_runs baseline/candidate order')
                expected = comparisons[pair][m.metric]
            if m.unit != unit or m.direction != direction:
                raise ValueError(f'{m.metric} requires unit={unit}, direction={direction}')
            if not math.isclose(m.value, expected, abs_tol=tolerance, rel_tol=0):
                raise ValueError(f'{m.metric}: observed value {expected}; rounding tolerance {tolerance}')


def finalize_answer(answer: Answer, outputs: list[dict]):
    """Only render limitations copied from observed notes or the fixed product boundary."""
    from .policy import BOUNDARY
    notes = {n['id']: n for o in outputs for n in o.get('notes', []) + o.get('limitations', []) if isinstance(n, dict)}
    allowed = {n['text'] for n in notes.values()} | {BOUNDARY}
    if any(text not in allowed for text in answer.limitations):
        raise ValueError('Do not generate free-form limitations. Set limitations=[] and use observed note IDs in limitation_ids. Put evidence-based explanations in cited claims.')
    if set(answer.limitation_ids) - set(notes):
        raise ValueError('Unread limitation_ids; call describe_data or search_notes first')
    queries = {o['query_id']: o for o in outputs if 'query_id' in o}
    if queries and answer.selection is None:
        raise ValueError('query_runs was used: final selection must copy query_id and ALL ordered_run_ids, including an empty list')
    if answer.selection:
        selected = queries.get(answer.selection.query_id)
        if selected is None or answer.selection.run_ids != selected['ordered_run_ids']:
            raise ValueError('selection must exactly match a read query result, including order and ties')
    validate_answer(answer, outputs)
    result = answer.model_dump()
    result['limitations'] = list(dict.fromkeys(answer.limitations + [notes[s]['text'] for s in answer.limitation_ids])) or [BOUNDARY]
    return result
