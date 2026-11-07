"""Bounded extractive USER history; never model-authored facts or evidence.

Removing old assistant drafts saves context without paraphrasing away a user's
negation, correction, constraint or unfinished request. If exact user text no
longer fits, callers stop; this module never invents a shorter semantic summary.
"""
import hashlib
import json
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator

from .schemas import StrictModel

VERSION = 'conversation-summary-v1'
AUTHORITY = 'exact_previous_user_messages_not_verified_evidence'
MAX_MESSAGES = 100
MAX_BYTES = 16384
IMPLEMENTATION_SHA256 = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8')


class SummaryCapacityExceeded(ValueError):
    pass


class UserMessage(StrictModel):
    seq: int = Field(ge=1)
    question: str = Field(min_length=1, max_length=2000)
    snapshot_id: str = Field(min_length=1, max_length=200)
    question_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')

    @model_validator(mode='after')
    def exact_question_hash(self):
        if self.question_sha256 != hashlib.sha256(self.question.encode('utf-8')).hexdigest():
            raise ValueError('Summary question hash does not match its exact user text')
        return self


class SourceRange(StrictModel):
    first_seq: int = Field(ge=1)
    last_seq: int = Field(ge=1)


class ConversationSummary(StrictModel):
    version: Literal['conversation-summary-v1']
    authority: Literal['exact_previous_user_messages_not_verified_evidence']
    messages: list[UserMessage] = Field(min_length=1, max_length=MAX_MESSAGES)
    source_range: SourceRange
    source_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    omitted_assistant_answers: int = Field(ge=1, le=MAX_MESSAGES)

    @model_validator(mode='after')
    def complete_exact_history(self):
        seqs = [message.seq for message in self.messages]
        if seqs != sorted(set(seqs)):
            raise ValueError('Summary user messages must have unique increasing sequence numbers')
        if (self.source_range.first_seq, self.source_range.last_seq) != (seqs[0], seqs[-1]):
            raise ValueError('Summary source range does not match user messages')
        if self.omitted_assistant_answers != len(seqs):
            raise ValueError('Summary assistant omission count does not match user messages')
        if self.source_sha256 != hashlib.sha256(canonical([item.model_dump() for item in self.messages])).hexdigest():
            raise ValueError('Summary source hash does not match its exact attributed user messages')
        if len(canonical(self.model_dump())) > MAX_BYTES:
            raise ValueError('Exact user history exceeds the bounded summary capacity')
        return self


def build_summary(previous=None, turns=()):
    """Return a new attributed summary; never mutate frozen input or truncate."""
    messages = [item.model_dump() for item in previous.messages] if previous else []
    messages.extend(dict(seq=turn.seq, question=turn.question, snapshot_id=turn.snapshot_id,
                         question_sha256=hashlib.sha256(turn.question.encode('utf-8')).hexdigest()) for turn in turns)
    if not messages:
        return None
    if len(messages) > MAX_MESSAGES:
        raise SummaryCapacityExceeded('Exact user history exceeds 100 summary messages; start a new conversation with explicit state.')
    result = dict(version=VERSION, authority=AUTHORITY, messages=messages,
                  source_range=dict(first_seq=messages[0]['seq'], last_seq=messages[-1]['seq']),
                  source_sha256=hashlib.sha256(canonical(messages)).hexdigest(), omitted_assistant_answers=len(messages))
    if len(canonical(result)) > MAX_BYTES:
        raise SummaryCapacityExceeded('Exact user history exceeds the 16 KiB summary capacity; start a new conversation with explicit state.')
    return ConversationSummary.model_validate(result)


def summary_metadata(summary):
    if summary is None:
        return None
    return dict(version=summary.version, authority=summary.authority, message_count=len(summary.messages),
                source_range=summary.source_range.model_dump(), source_sha256=summary.source_sha256,
                utf8_bytes=len(canonical(summary.model_dump())), omitted_assistant_answers=summary.omitted_assistant_answers)
