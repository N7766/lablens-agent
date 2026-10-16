"""Deterministic sentence mapping, not natural-language entailment."""
import re


def clauses(question):
    # Decimal points, commas and run IDs stay intact. This is deliberately not an NLP parser.
    parts = []
    start = 0
    for match in re.finditer(r'[；;。！？!?\n]+|(?<!\d)\.(?=\s|$)', question):
        raw = question[start:match.start()]
        if raw.strip():
            left = start + len(raw) - len(raw.lstrip())
            parts.append(dict(id=f'c{len(parts)+1}', text=raw.strip(), start=left, end=left+len(raw.strip())))
        start = match.end()
    raw = question[start:]
    if raw.strip():
        left = start + len(raw) - len(raw.lstrip())
        parts.append(dict(id=f'c{len(parts)+1}', text=raw.strip(), start=left, end=left+len(raw.strip())))
    return parts


def coverage(question, plan):
    rows = []
    for clause in clauses(question):
        # Only a full clause quote is counted. Partial quotes are explicitly unconfirmed.
        steps = [s.id for s in plan.steps if clause['text'] == s.request_excerpt]
        unhandled = [i for i, item in enumerate(plan.unhandled) if clause['text'] == item.request_excerpt]
        partial = [s.id for s in plan.steps if s.request_excerpt in clause['text'] and s.id not in steps]
        state = 'conflict' if steps and unhandled else ('mapped' if steps else ('unhandled' if unhandled else 'unmapped'))
        rows.append(dict(**clause, state=state, step_ids=steps, unhandled_indexes=unhandled, partial_quote_step_ids=partial))
    return dict(scope='Exact full-clause mapping only; does not establish semantic coverage', clauses=rows,
                all_clauses_accounted_for=all(r['state'] in {'mapped', 'unhandled'} for r in rows),
                semantic_validation='not_performed')
