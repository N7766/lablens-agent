#!/usr/bin/env python3
"""Offline ablation on the now-public v0.13 development regression set.

Measures required-document-group coverage, not semantic answer correctness.
Never calls a model. Keeps the corpus, chunking and BM25 scoring fixed while
replacing only the tokenizer with the previous version for the baseline arm.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from lablens import rag


def previous_tokens(text):
    tokens = re.findall(r'[a-z0-9_]+', text.lower())
    for sequence in re.findall(r'[\u3400-\u9fff]+', text):
        tokens.extend(sequence)
        tokens.extend(sequence[i:i+2] for i in range(len(sequence)-1))
    return tokens


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    suite = ROOT/'evaluation/independent_v013/suite'
    cases = json.loads((suite/'cases.json').read_text())['cases']
    docs = json.loads((suite/'corpus.json').read_text())['documents']
    docs = [{k:d[k] for k in ('id','title','source','text')} for d in docs]
    original = rag.tokenize
    arms = {}
    for name, tokenizer in [('previous_unigrams_bigrams', previous_tokens), ('bilingual_domain_v1', original)]:
        rag.tokenize = tokenizer
        records = []
        try:
            for case in cases:
                result = rag.answer_rag(rag.RAGRequest(question=case['question'], documents=docs,
                    snapshot_id='public-v013-regression', strategy='bm25', mode='extractive', top_k=5))
                retrieved = {h['document_id'] for h in result['retrieval']['hits']}
                groups = case['required_evidence_groups']
                coverage = [bool(set(group)&retrieved) for group in groups]
                records.append(dict(id=case['id'], question=case['question'],
                    required_groups=groups, covered_groups=coverage,
                    all_groups_present=all(coverage), retrieval=result['retrieval'], usage=result['usage']))
        finally:
            rag.tokenize = original
        arms[name] = dict(cases=records,
            full_document_group_coverage=sum(r['all_groups_present'] for r in records),
            covered_groups=sum(sum(r['covered_groups']) for r in records),
            required_groups=sum(len(r['covered_groups']) for r in records))
    metadata = dict(created_at_utc=datetime.now(timezone.utc).isoformat(),
        scope='Exposed v0.13 cases reused as development regressions, NOT fresh blind questions or semantic scores',
        model_calls=0, embedding_requests=0, inputs_sha256={n:hashlib.sha256((suite/n).read_bytes()).hexdigest()
            for n in ('cases.json','corpus.json')},
        source_sha256={n:hashlib.sha256((ROOT/n).read_bytes()).hexdigest()
            for n in ('lablens/rag.py','lablens/lexical.py', 'scripts/evaluate_round1_retrieval.py')},
        metric='required document groups present in top5; not necessary fact/span coverage or entailment', arms=arms)
    (args.output/'results.json').write_text(json.dumps(metadata,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({k:{n:v[n] for n in ('full_document_group_coverage','covered_groups','required_groups')}for k,v in arms.items()},ensure_ascii=False))


if __name__=='__main__':
    main()
