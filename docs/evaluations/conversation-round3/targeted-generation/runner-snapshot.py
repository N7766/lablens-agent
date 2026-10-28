#!/usr/bin/env python3
"""Replay the evidence actually retrieved for known semantic failures.

This isolates generation, not retrieval. No training, no automatic retries, no
automatic semantic grading. Preserve every response for a separate review.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from lablens.rag import _generate, _new_usage, provider_config, RAGError


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--env-file', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--targeted', action='store_true', help='Also test the two exact disputed propositions on their original evidence')
    parser.add_argument('--only-targeted', action='store_true', help='Test only the two exact disputed propositions; takes precedence over --targeted')
    args = parser.parse_args()
    if args.env_file:
        load_dotenv(args.env_file, override=True)
    args.output.mkdir(parents=True, exist_ok=False)
    source = ROOT / 'evaluation/regressions/v013/known-failures.json'
    raw = source.read_bytes()
    cases = json.loads(raw)
    if args.targeted or args.only_targeted:
        originals = {case['id']: case for case in cases}
        if args.only_targeted:
            cases = []
        for cid, question in [
            ('d01', '有人说资料没有 SFT 自由生成基线，这个说法是否成立？请核对基线数值，并判断这些比较能否证明过拟合是唯一根因。'),
            ('h01', '根据 Stage 2 配置 precompute_ref_log_probs: false，能否确认历史运行每一步实际重算参考模型概率？请区分配置记录与实际运行事实。'),
        ]:
            cases.append(dict(**{k:v for k,v in originals[cid].items() if k not in {'id','question'}}, id=cid+'-targeted', question=question))
    metadata = dict(started_at=datetime.now(timezone.utc).isoformat(),
                    input_sha256=hashlib.sha256(raw).hexdigest(),
                    implementation_sha256={name: hashlib.sha256((ROOT/name).read_bytes()).hexdigest()
                                           for name in ('lablens/rag.py', 'lablens/assessment.py')},
                    scope='Known development cases, generation on original retrieved parent documents; no retrieval score',
                    automatic_semantic_grading=False, automatic_retries=0,
                    targeted=args.targeted or args.only_targeted,
                    only_targeted=args.only_targeted, requests=len(cases), parallelism=2,
                    runner_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest())
    (args.output/'metadata.json').write_text(json.dumps(metadata, indent=2)+'\n')
    def run_case(case):
        hits = []
        for doc in case['documents']:
            text = doc['text']
            digest = hashlib.sha256(text.encode()).hexdigest()
            hits.append(dict(chunk_id='regression:'+doc['id'], document_id=doc['id'],
                             title=doc['title'], source=doc['source'], text=text,
                             start=0, end=len(text), content_sha256=digest))
        usage = _new_usage()
        config = provider_config('generation')
        generations = []
        try:
            result = _generate(case['question'], hits, usage, config, observer=generations.append)
        except RAGError as exc:
            result = dict(status=exc.status, error=str(exc))
        record = dict(id=case['id'], question=case['question'], model=config['model'],
                      result=result, usage=usage, raw_generations=generations, reviewed=False)
        return record
    with ThreadPoolExecutor(max_workers=2) as pool:
        for future in as_completed([pool.submit(run_case, case) for case in cases]):
            record = future.result()
            with (args.output/'results.jsonl').open('a') as f:
                f.write(json.dumps(record, ensure_ascii=False)+'\n')
            print(record['id'], record['result']['status'], 'calls', record['usage']['model_calls'], flush=True)


if __name__ == '__main__':
    main()
