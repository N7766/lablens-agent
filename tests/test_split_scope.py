import hashlib

from lablens.assessment import validate_assessments


def check(prose, source, verdict='supported'):
    hit = dict(chunk_id='c',document_id='d',title='measurement',source='report.md',text=source,
               start=0,end=len(source),content_sha256=hashlib.sha256(source.encode()).hexdigest())
    raw = {'assessments':[dict(statement='Accuracy comparison',explanation=prose,verdict=verdict,
        basis='documented_fact',citation_ids=['c'],quotes=[dict(chunk_id='c',quote=source)])]}
    return validate_assessments('Compare the recorded runs', raw, [hit])


def test_validation_measurement_cannot_be_presented_as_test_set():
    x=check('基于同一测试集，准确率下降10个百分点。', 'Both runs used the same validation set; accuracy difference was 10 pp.')
    assert x['assessments'][0]['verdict']=='insufficient'
    assert '验证集' in x['assessments'][0]['explanation']
    assert x['assessment_validation']['adjustments'][0]['rule']=='validation_source_cannot_certify_test_measurement'


def test_actual_test_measurement_and_explicit_denial_remain_distinct():
    assert check('Measured on the test set.', 'The independent test set accuracy was 0.72.')['status']=='answered'
    assert check('这是验证集，不是测试集。', 'This is the validation set.')['status']=='answered'
    assert check('这是测试集吗？不能确定。', 'This is the validation set.', 'insufficient')['status']=='refused'


def test_implementation_word_count_cannot_certify_token_count():
    source = 'output_tokens = len(prediction.split())\nMean output_tokens: 32.42'
    result = check('平均输出 token 数为 32.42。', source)
    assert result['assessments'][0]['verdict'] == 'insufficient'
    assert result['assessment_validation']['adjustments'][0]['rule'] == 'whitespace_count_cannot_certify_token_measurement'
    assert check('output_tokens 实际统计空格分词数，为 32.42。', source)['status'] == 'answered'
    assert check('平均输出 token 数为 32.42。', 'Count from tokenizer.encode(prediction): 32.42')['status'] == 'answered'
