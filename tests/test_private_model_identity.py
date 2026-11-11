"""Tiny real filesystem checks; no model, network, or provider operations."""
import hashlib
import importlib.util
import os
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location("bounded_identity", Path(__file__).resolve().parents[1] / "scripts/verify_private_model_identity.py")
verifier = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(verifier)
NAME = verifier.WEIGHT_FILES[0]
BODY = b"synthetic weight bytes only"
SHA = hashlib.sha256(BODY).hexdigest()
SHARED_SHA = "dd" + "a" * 62


@pytest.fixture
def layout(tmp_path):
    root = tmp_path / "model-cache"
    repo = root / "hub/models--test--model"
    snapshot = repo / "snapshots" / ("a" * 40)
    snapshot.mkdir(parents=True)
    (repo / "blobs").mkdir()
    return root, repo, snapshot


def link(source, target):
    source.symlink_to(os.path.relpath(target, source.parent))


def inspect(layout, expected=SHA, size=len(BODY)):
    root, _, snapshot = layout
    return verifier.inspect_file(snapshot, root, NAME, expected, size, len(BODY) + 20)[0]


@pytest.mark.parametrize("kind", ["direct", "old", "shared"])
def test_supported_layouts_verify_actual_bytes(layout, kind):
    root, repo, snapshot = layout
    source = snapshot / NAME
    if kind == "direct":
        source.write_bytes(BODY)
    else:
        blob = repo / "blobs" / SHA
        link(source, blob)
        if kind == "old":
            blob.write_bytes(BODY)
        else:
            shared = root / "hub/blobs" / SHARED_SHA[:2] / SHARED_SHA
            shared.parent.mkdir(parents=True)
            shared.write_bytes(BODY)
            link(blob, shared)
    report = inspect(layout)
    assert report["status"] == "matched"
    assert report["content_identity_verified"] is True
    assert report["observed_sha256"] == SHA


@pytest.mark.parametrize("case", ["outside", "wrong_first_hop", "wrong_prefix", "wrong_shared_name", "arbitrary_inside", "direct_to_shared"])
def test_disallowed_symlink_targets_are_not_read(layout, case):
    root, repo, snapshot = layout
    shared_name = SHARED_SHA if case != "wrong_shared_name" else "short"
    prefix = SHARED_SHA[:2] if case != "wrong_prefix" else "ee"
    target = root / "hub/blobs" / prefix / shared_name
    if case == "outside":
        target = root.parent / "outside"
    elif case == "arbitrary_inside":
        target = root / "unrelated-private-file"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(BODY)
    blob = repo / "blobs" / (SHA if case != "wrong_first_hop" else "f" * 64)
    link(snapshot / NAME, target if case == "direct_to_shared" else blob)
    if case != "direct_to_shared":
        link(blob, target)
    report = inspect(layout)
    assert report["status"] in {"symlink_outside_cache_root", "unexpected_symlink_target"}
    assert report["observed_bytes"] is None
    assert report["observed_sha256"] is None


@pytest.mark.parametrize("corruption", ["hash", "size"])
def test_shared_cache_still_requires_full_content_hash_and_size(layout, corruption):
    root, repo, snapshot = layout
    target = root / "hub/blobs" / SHARED_SHA[:2] / SHARED_SHA
    target.parent.mkdir(parents=True)
    target.write_bytes(b"X" * len(BODY) if corruption == "hash" else BODY + b"extra")
    blob = repo / "blobs" / SHA
    link(blob, target)
    link(snapshot / NAME, blob)
    report = inspect(layout)
    assert report["status"] == ("hash_mismatch" if corruption == "hash" else "size_mismatch")
    assert report["content_identity_verified"] is False
