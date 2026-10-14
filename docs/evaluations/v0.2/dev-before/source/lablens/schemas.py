from typing import Literal
from pydantic import BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class ListRuns(StrictModel):
    min_accuracy: float = Field(default=0, ge=0, le=1)


class CompareRuns(StrictModel):
    baseline_id: str
    candidate_id: str


class SearchNotes(StrictModel):
    query: str = Field(min_length=1, max_length=500)
    limit: int = Field(default=3, ge=1, le=5)


class Measurement(StrictModel):
    metric: Literal["accuracy", "correct", "num_samples", "mean_length", "accuracy_delta_percentage_points", "additional_correct", "length_reduction_percent"]
    baseline_id: str
    candidate_id: str | None = None
    value: float = Field(allow_inf_nan=False)
    unit: Literal["fraction", "count", "whitespace_words", "tokenizer_tokens", "percentage_points", "percent"]
    direction: Literal["absolute", "candidate_minus_baseline", "baseline_minus_candidate_over_baseline"]


class Claim(StrictModel):
    text: str = Field(min_length=1, max_length=2000)
    source_ids: list[str] = Field(min_length=1, max_length=10)
    measurements: list[Measurement] = Field(default_factory=list, max_length=20)


class Answer(StrictModel):
    status: Literal["answered", "insufficient_evidence"]
    claims: list[Claim] = Field(max_length=10)
    limitations: list[str] = Field(min_length=1, max_length=10)
