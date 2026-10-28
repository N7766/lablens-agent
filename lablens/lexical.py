"""Small, inspectable bilingual vocabulary for this research workspace.

This is deterministic query/document normalization, not translation or semantic
search. Original source text and offsets are never changed.
"""
import re

VERSION = 'research-lexical-v1'
ALIASES = {
    'accuracy': ('准确率', '正确率'),
    'baseline': ('基线', '基准模型'),
    'validation': ('验证集', '验证结果', '验证准确率'),
    'test': ('测试集',),
    'preference': ('偏好',),
    'reward': ('奖励',),
    'margin': ('间隔', '边际'),
    'generation': ('生成',),
    'free': ('自由生成',),
    'reference': ('参考模型',),
    'model': ('模型',),
    'training': ('训练',),
    'checkpoint': ('检查点',),
    'batch': ('批次', '批大小'),
    'memory': ('显存',),
    'runtime': ('运行时间', '训练耗时'),
    'latency': ('延迟', '时延'),
    'prompt': ('提示词', '提示模板'),
    'length': ('长度',),
    'chosen': ('优选回答', '正样本'),
    'rejected': ('劣选回答', '负样本'),
    'initialization': ('初始化',),
    'compression': ('压缩',),
}
STOPWORDS = {'the', 'a', 'an', 'is', 'are', 'of', 'in', 'on', 'to', 'and', 'what', 'how',
             'does', 'do', 'please', '资料', '请问', '是否', '多少', '什么', '怎么', '如何',
             '根据', '这个', '那个', '中的', '里的', '中有', '有无', '能否', '可以', '我们'}


def tokens(text: str) -> list[str]:
    lower = text.lower()
    result = re.findall(r'[a-z0-9_]+', lower)
    # Bigrams avoid unrelated documents matching ubiquitous single characters
    # such as 的/是. Single-character queries/content still remain searchable.
    for sequence in re.findall(r'[\u3400-\u9fff]+', text):
        result.extend([sequence] if len(sequence) == 1 else
                      [sequence[i:i+2] for i in range(len(sequence)-1)])
    for english, variants in ALIASES.items():
        for variant in variants:
            result.extend([english] * lower.count(variant))
    for number in re.findall(r'\bstage[ _-]*([0-9]+)\b', lower):
        result.append('stage' + str(int(number)))
    for number in re.findall(r'阶段\s*([0-9]+)|第\s*([0-9]+)\s*阶段', text):
        result.append('stage' + str(int(number[0] or number[1])))
    for chinese, number in zip('一二三四五六七八九', range(1, 10)):
        result.extend(['stage' + str(number)] * text.count('第' + chinese + '阶段'))
    return [term for term in result if term not in STOPWORDS]


def expansion_metadata(question: str) -> dict:
    return {'version': VERSION, 'kind': 'bounded bilingual domain vocabulary; not machine translation',
            'matched_aliases': {key: [v for v in variants if v in question]
                                for key, variants in ALIASES.items() if any(v in question for v in variants)},
            'source_text_modified': False}
