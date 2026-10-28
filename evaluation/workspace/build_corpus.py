#!/usr/bin/env python3
"""Rebuild the exact public-source corpus; no HTTP or model calls."""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[2]
LABLENS_REV = 'cfdfe2d544d7dc01f67e75c018bf1d9a40bfb952'
DPO_REV = '0821ef3bca3eb117cab778a26416c820ebe73f26'


def sha(text: str) -> str:
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dpo-repository', type=Path, required=True)
    parser.add_argument('--output', type=Path, default=ROOT / 'evaluation/workspace/corpus.json')
    args = parser.parse_args()
    docs = []

    def original(repo: str, path: str) -> tuple[str, str]:
        directory, revision = (ROOT, LABLENS_REV) if repo == 'lablens-agent' else (args.dpo_repository, DPO_REV)
        data = subprocess.run(['git', 'show', f'{revision}:{path}'], cwd=directory,
                              check=True, capture_output=True).stdout
        return data.decode('utf-8'), revision

    def section(repo: str, path: str, heading: str, doc_id: str, title: str) -> None:
        source, revision = original(repo, path)
        begin = source.index(heading)
        level = len(heading) - len(heading.lstrip('#'))
        lines = source[begin:].splitlines(keepends=True)
        selected = [lines[0]]
        for line in lines[1:]:
            if line.startswith('#') and line.startswith('#' * level + ' '):
                break
            if level > 1 and line.startswith('# '):
                break
            selected.append(line)
        text = ''.join(selected).rstrip()
        docs.append({'id': doc_id, 'title': title, 'text': text,
            'source': f'{repo}/{path}#{heading.lstrip("# ")}',
            'provenance': {'repository': f'https://github.com/N7766/{repo}',
                'commit': revision, 'path': path, 'section': heading,
                'source_sha256': sha(source), 'content_sha256': sha(text),
                'selection': 'verbatim Markdown section; trailing whitespace removed',
                'source_status': 'historical project report; claims not re-measured in this evaluation'}})

    raw, revision = original('lablens-agent', 'lablens/data/notes.json')
    for note in json.loads(raw):
        docs.append({'id': note['id'], 'title': note['title'], 'text': note['text'],
            'source': 'lablens-agent/lablens/data/notes.json#' + note['id'],
            'provenance': {'repository': 'https://github.com/N7766/lablens-agent',
                'commit': revision, 'path': 'lablens/data/notes.json',
                'json_id': note['id'], 'source_sha256': sha(raw),
                'content_sha256': sha(note['text']), 'original_source_label': note['source'],
                'selection': 'verbatim note text', 'source_status': 'packaged v0.11 methodology note'}})
    dpo = 'dpo-cot-compression'
    specs = [
        ('docs/STAGE1_REPORT.md', '## 5. Rejected Candidate Generation', 'dpo:rejected', 'Stage 1 rejected candidate filtering'),
        ('docs/STAGE1_REPORT.md', '## 6. Chosen Candidate Generation', 'dpo:chosen', 'Stage 1 chosen teacher generation and filtering'),
        ('docs/STAGE1_REPORT.md', '## 7. Final Stage 1 Dataset', 'dpo:split', 'Stage 1 preference train validation split'),
        ('docs/STAGE1_REPORT.md', '## 10. Limitations and Risks', 'dpo:limitations', 'Stage 1 evaluation limitations and word counts'),
        ('docs/STAGE2_3_REPORT.md', '## 3. Stage 2 Setup', 'dpo:direct-setup', 'Stage 2 direct LoRA DPO training setup'),
        ('docs/STAGE2_3_REPORT.md', '## 4. Stage 2 Results', 'dpo:direct-result', 'Stage 2 preference loss versus free generation failure'),
        ('docs/STAGE3_REPORT.md', '## Training Configuration', 'dpo:sft-config', 'Stage 3 SFT training configuration'),
        ('docs/STAGE3_REPORT.md', '## Hardware and Runtime', 'dpo:sft-runtime', 'Stage 3 SFT hardware runtime and memory'),
        ('docs/STAGE4_REPORT.md', '## Training Configuration', 'dpo:fsdp-config', 'Stage 4 full parameter FSDP training configuration'),
        ('docs/STAGE4_REPORT.md', '## Free-Form Evaluation', 'dpo:fsdp-result', 'Stage 4 full parameter DPO versus SFT validation'),
        ('docs/STAGE5_RESULTS.md', '## Best Result', 'dpo:best', 'Stage 5 validation selected best checkpoint'),
        ('docs/STAGE5_RESULTS.md', '## Full-DPO Follow-Up', 'dpo:full-followup', 'Stage 5 full DPO conservative learning rate follow up'),
        ('README.md', '## Final Controlled Evaluation Protocol', 'dpo:final-protocol', 'Planned locked post selection test protocol; not completed test evidence'),
    ]
    for path, heading, doc_id, title in specs:
        section(dpo, path, heading, doc_id, title)
    specs = [
        ('docs/v0.5-workbench.md', '## 支持的计划', 'lab:plans', 'v0.5 plan steps and single-result references'),
        ('docs/v0.5-workbench.md', '## 不能由此推导的结论', 'lab:receipt-limits', 'v0.5 receipt verification evidence boundaries'),
        ('docs/v0.8-workbench.md', '## 数据选择与发送许可', 'lab:consent', 'v0.8 imported data consent and size limits'),
        ('docs/v0.8-workbench.md', '## 计划与快照始终一起传递', 'lab:binding', 'v0.8 snapshot binding and stale response invalidation'),
        ('docs/v0.9-workbench.md', '## 结果数量与排序分别表达', 'lab:selection', 'v0.9 selection versus sort direction'),
        ('docs/v0.9-workbench.md', '## 失败候选与有效草稿分开呈现', 'lab:repair', 'v0.9 failed candidate and model request budget'),
        ('docs/v0.10-evaluation.md', '## 核对的具体范围', 'lab:task-checks', 'v0.10 finite comparison and describe checks'),
        ('docs/v0.11-workbench.md', '## 怎样解读报告', 'lab:report-limits', 'v0.11 report interpretation, current rules, signatures and usage'),
    ]
    for path, heading, doc_id, title in specs:
        section('lablens-agent', path, heading, doc_id, title)
    payload = {'schema_version': 1, 'corpus_id': 'workspace-rag-public-v1',
        'authorship': 'Selection and titles authored by the implementation assistant; source text copied verbatim from public tracked project documents.',
        'limitations': [
            'One actual DPO experiment project plus its LabLens tooling; not a second independent real experiment dataset.',
            'Historical DPO reports use output tokens for whitespace word counts; see note:units and dpo:limitations.',
            'Historical notes and reports retain their original version scope; do not assume their capability statements describe later implementations.',
            'Sources are documentation evidence, not fresh training or production measurements.',
            'Question authors have seen the corpus and implementation goals; not independent human evaluation.'
        ], 'documents': docs}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({'documents': len(docs), 'text_characters': sum(len(d['text']) for d in docs),
                      'sha256': hashlib.sha256(args.output.read_bytes()).hexdigest()}))


if __name__ == '__main__':
    main()
