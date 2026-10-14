import json
from pathlib import Path
from lablens.baseline import run_direct


def test_frozen_dataset_has_unique_ids_and_disjoint_families():
    cases=json.loads(Path('evaluation/benchmark.json').read_text())
    assert len(cases)==32
    assert len({c['id'] for c in cases})==len(cases)
    dev={c['family'] for c in cases if c['split']=='dev'}
    holdout={c['family'] for c in cases if c['split']=='holdout'}
    assert not dev & holdout
    assert sum(c['split']=='holdout' for c in cases)==24
    assert all(c['rubric'] for c in cases)


def test_direct_has_all_raw_evidence_and_no_tools_or_retry():
    class Model:
        def complete(self,messages,tools):
            assert tools==[]
            assert 'run:dpo-12' in messages[1]['content']
            assert 'note:scope' in messages[1]['content']
            return {'content':'invalid json'}, {'prompt_tokens':1,'completion_tokens':1}
    result=run_direct(Model(),'q')
    assert result['status']=='invalid_answer'
    assert result['model_calls']==1
    assert result['tool_calls']==0
