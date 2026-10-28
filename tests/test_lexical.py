from lablens.rag import RAGRequest, answer_rag, tokenize


def result(question, docs):
    return answer_rag(RAGRequest(question=question, strategy='bm25', mode='extractive',
        top_k=3, snapshot_id='bilingual:1', documents=docs))


def doc(identity, text):
    return dict(id=identity, title=identity, source=identity, text=text)


def test_chinese_question_reaches_english_measurement_not_only_chinese_chatter():
    data = result('第三阶段 SFT 基线的验证集准确率是多少？', [
        doc('run', 'Stage 3 SFT baseline: validation set accuracy 0.812; 203 correct of 250.'),
        doc('chat', '请问资料里是否有这个模型？这个问题应该怎么回答？'),
        doc('unrelated', 'Database transactions use optimistic concurrency.')])
    assert data['retrieval']['hits'][0]['document_id'] == 'run'
    assert data['retrieval']['lexical_normalization']['matched_aliases']['accuracy'] == ['准确率']
    assert data['usage']['model_calls'] == data['usage']['embedding_requests'] == 0


def test_english_question_reaches_chinese_report_without_mutating_offsets():
    text = '训练记录：偏好准确率达到0.9，参考模型保持冻结。'
    data = result('preference accuracy reference model', [doc('cn', text), doc('noise', '没有相关数据库资料。')])
    hit = data['retrieval']['hits'][0]
    assert hit['document_id'] == 'cn'
    assert hit['text'] == text[hit['start']:hit['end']]


def test_split_and_units_are_not_conflated_by_normalization():
    assert 'validation' in tokenize('验证集') and 'test' not in tokenize('验证集')
    assert 'test' in tokenize('测试集') and 'validation' not in tokenize('测试集')
    assert 'token' not in tokenize('空白分隔词数')
    assert 'stage4' in tokenize('第4阶段')
    assert 'stage4' in tokenize('Stage_4')


def test_no_match_still_no_match_and_single_character_supported():
    assert result('不存在的xyzabctoken', [doc('foo', 'ordinary independent evidence')])['status'] == 'no_hits'
    assert result('火', [doc('cn', '火')])['retrieval']['hits'][0]['document_id'] == 'cn'
