"""Explicit command guard. Heuristic routing, not a semantic safety classifier."""
import re
from uuid import uuid4

BOUNDARY = '结论仅基于本次可见摘要；结构化校验不能保证自然语言推断正确。'
SCOPE = '本系统只能读取实验摘要和方法说明，不能训练、删除文件、上传权重、联网或修改实验结果。'
COMMAND = re.compile(r'^(?:请你|请|帮我|麻烦)?(?:立即|马上|现在|立刻)?(?:删除|上传|启动.{0,20}训练|联网|替换|把.{0,50}(?:上传|删除)|将.{0,50}(?:上传|删除))')


def refusal(question, mode):
    if not COMMAND.search(question.strip()):
        return None
    return dict(id=uuid4().hex, mode=mode, question=question, status='completed',
        answer=dict(status='insufficient_evidence',claims=[dict(text=SCOPE,source_ids=['policy:readonly'],measurements=[])],limitations=[BOUNDARY],limitation_ids=[],selection=None),
        events=[dict(type='policy_refusal',step=0,rule='explicit_readonly_command',source_ids=['policy:readonly'])],
        model_calls=0,tool_calls=0,latency_seconds=0,usage_incomplete=False,available_sources=['policy:readonly'],
        source_validation='policy',numeric_validation='not_applicable',factuality_review='not_performed')
