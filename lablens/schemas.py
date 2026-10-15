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


class QueryRuns(StrictModel):
    min_accuracy: float = Field(default=0, ge=0, le=1)
    max_accuracy: float = Field(default=1, ge=0, le=1)
    min_correct: int = Field(default=0, ge=0)
    max_length: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    shorter_than_baseline: str | None = None
    more_accurate_than_baseline: str | None = None
    order_by: Literal["accuracy", "mean_length", "correct"] = "accuracy"
    descending: bool = True
    top_k: int | None = Field(default=None, ge=1, le=100)
    include_ties: bool = True
    pareto_only: bool = False


class DescribeData(StrictModel):
    pass


class Selection(StrictModel):
    query_id: str
    run_ids: list[str] = Field(max_length=100)


class Answer(StrictModel):
    status: Literal["answered", "insufficient_evidence"]
    claims: list[Claim] = Field(max_length=10)
    limitations: list[str] = Field(default_factory=list, max_length=10)
    limitation_ids: list[str] = Field(default_factory=list, max_length=10)
    selection: Selection | None = None


def answer_request_schema():
    """The wire contract forbids free prose in limitations; response rendering adds it later."""
    schema = Answer.model_json_schema()
    schema["properties"]["limitations"]["maxItems"] = 0
    schema["properties"]["limitations"]["description"] = "Always empty; use limitation_ids to select evidence excerpts"
    return schema
