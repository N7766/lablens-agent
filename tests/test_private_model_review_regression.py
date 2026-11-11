"""Regression from the real Round19 synthetic scoped-review response.

The saved response was valid JSON but retained and replaced a1 simultaneously.
Rejection is the correct production behavior; this is not a model-quality pass.
"""
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

from lablens.review_delta import restore_review_delta, review_policy

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("round19_review_probe", ROOT / "scripts/verify_private_model.py")
probe = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(probe)

# Exact assistant content; original HTTP-body SHA256:
# 82923328664e6eb4c785fc07f9b7fb810e4ff3acf336a30d7b84ddb84807a38c
ACTUAL_ASSISTANT_CONTENT = '{"assessments": [{"verdict": "supported", "explanation": "合成资料明确列出了 SFT 基线。", "basis": "documented_fact", "evidence_ids": ["pe8da723a5d9fc7d0"] , "assessment_id": "a1"}, {"verdict": "insufficient", "explanation": "配置记录中提到 cache_enabled 为 true，但没有对应的历史运行日志来证明缓存实际在历史运行中启用。", "basis": "historical_observation", "evidence_ids": ["pff6bb3e15707965b", "pb7eaccb71ed780a9"] , "assessment_id": "a2"}], "retained_assessment_ids": ["a1"]}'
ASSISTANT_CONTENT_SHA256 = 'd6e85e95102e30c2660a7dc55a4565a6815588faa3b492ac829ca45c292530d4'


def test_actual_round19_response_cannot_retain_and_replace_the_same_assessment():
    assert hashlib.sha256(ACTUAL_ASSISTANT_CONTENT.encode()).hexdigest() == ASSISTANT_CONTENT_SHA256
    data = probe.fixture()
    policy = review_policy(data["draft"], data["hits"], data["context"]["scoped_review_contract"])
    assert policy["retainable_assessment_ids"] == ["a1"]
    reviewed = json.loads(ACTUAL_ASSISTANT_CONTENT)
    with pytest.raises(ValueError, match=r"^Unknown, duplicate or overlapping replacement ID\.$"):
        restore_review_delta(reviewed, data["draft"], data["hits"], policy)
