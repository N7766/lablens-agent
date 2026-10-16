"""One immutable-by-copy snapshot for model input, preview and execution binding."""
import json
from dataclasses import dataclass

from .datasets import DatasetBundle
from .tools import ToolBox
from .workbench import inspect, tools_for

MAX_CONTEXT_BYTES = 128 * 1024


@dataclass
class PlanningContext:
    dataset: DatasetBundle
    tools: ToolBox
    evidence: dict
    summary: dict


def prepare_context(dataset: DatasetBundle | None = None) -> PlanningContext:
    # ToolBox revalidates, normalizes and owns a copy. Later caller mutations must
    # not change the snapshot used for the model's subsequent preview.
    tools = tools_for(dataset)
    frozen = DatasetBundle.model_validate({'runs': tools.runs, 'notes': tools.notes})
    evidence = dict(runs=tools.runs, coverage=tools.execute('describe_data', {}))
    size = len(json.dumps(evidence, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode('utf-8'))
    summary = inspect(frozen)
    summary.update(context_bytes=size, max_context_bytes=MAX_CONTEXT_BYTES,
                   model_input_allowed=size <= MAX_CONTEXT_BYTES,
                   model_upload_required=dataset is not None,
                   input_scope='All experiment fields, methodology notes and source labels; byte count excludes question and tool schema')
    return PlanningContext(frozen, tools, evidence, summary)
