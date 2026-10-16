"""Validated interchange format. Hashes identify provenance, not independent authenticity."""
import math
from pathlib import Path
from typing import Literal
from pydantic import Field, model_validator
from .schemas import StrictModel


class ExperimentRun(StrictModel):
    id: str = Field(pattern=r'^run:[a-zA-Z0-9_-]+$')
    label: str = Field(min_length=1)
    project: str = Field(min_length=1)
    dataset: str = Field(min_length=1)
    split_id: str = Field(min_length=1)
    num_samples: int = Field(gt=0)
    correct: int = Field(ge=0)
    accuracy: float = Field(ge=0, le=1, allow_inf_nan=False)
    mean_length: float = Field(gt=0, allow_inf_nan=False)
    length_unit: Literal['whitespace_words', 'tokenizer_tokens']
    tokenizer: str | None = None
    model_config_details: dict[str, str] = Field(min_length=1)
    used_for_checkpoint_selection: bool
    source_path: str = Field(min_length=1)
    source_sha256: str = Field(pattern=r'^[a-f0-9]{64}$')
    synthetic: bool

    @model_validator(mode='after')
    def consistent(self):
        if self.correct > self.num_samples or not math.isclose(self.accuracy, self.correct / self.num_samples, abs_tol=1e-9, rel_tol=0):
            raise ValueError('Accuracy must equal correct / num_samples')
        if self.length_unit == 'tokenizer_tokens' and not self.tokenizer:
            raise ValueError('tokenizer_tokens requires tokenizer identifier')
        if Path(self.source_path).is_absolute() or '..' in Path(self.source_path).parts:
            raise ValueError('source_path must be a relative provenance label')
        return self


def validate_runs(rows):
    if not isinstance(rows, list) or not rows:
        raise ValueError('Expected a nonempty list of experiments')
    runs = [ExperimentRun.model_validate(row).model_dump() for row in rows]
    if len({r['id'] for r in runs}) != len(runs):
        raise ValueError('Duplicate run IDs')
    return runs


def comparison_key(run):
    return tuple(run[k] for k in ('project', 'dataset', 'split_id', 'num_samples', 'length_unit', 'tokenizer', 'synthetic'))
