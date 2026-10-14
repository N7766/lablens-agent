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


class Claim(StrictModel):
    text: str = Field(min_length=1, max_length=2000)
    source_ids: list[str] = Field(min_length=1, max_length=10)


class Answer(StrictModel):
    status: Literal["answered", "insufficient_evidence"]
    claims: list[Claim] = Field(max_length=10)
    limitations: list[str] = Field(min_length=1, max_length=10)
