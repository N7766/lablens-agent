#!/usr/bin/env python3
"""Read-only, offline verification of a pinned Hugging Face model snapshot.

Usage: python3 verify_remote_identity.py --cache-root PATH --profile PROFILE
JSON goes to stdout; redirect it to a new evidence file outside the model cache.
Exit 0: all pinned files, index mapping and chat template matched; 1: mismatch,
missing or changed file; 2: invalid inputs. No network or credential access.
This proves local file bytes, not which weights a live server has loaded.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys
import time


METADATA_FILES = (
    "config.json", "tokenizer_config.json", "model.safetensors.index.json", "LICENSE"
)
TOKENIZER_PAYLOAD_FILES = ("tokenizer.json", "vocab.json", "merges.txt")
WEIGHT_FILES = tuple(f"model-{i:05d}-of-00004.safetensors" for i in range(1, 5))
HEX_SHA256 = re.compile(r"[a-f0-9]{64}\Z")
HEX_REVISION = re.compile(r"[a-f0-9]{40}\Z")
CHUNK_BYTES = 4 * 1024 * 1024
SMALL_FILE_LIMIT = 2 * 1024 * 1024
TOKENIZER_FILE_LIMIT = 128 * 1024 * 1024


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def fingerprint(info: os.stat_result) -> tuple[int, ...]:
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def safe_error(exc: BaseException) -> str:
    # Do not print exception text: paths or OS messages may include unrelated data.
    return type(exc).__name__


def load_profile(path: Path) -> tuple[dict, str]:
    with path.open("rb") as stream:
        raw = stream.read(SMALL_FILE_LIMIT + 1)
    if len(raw) > SMALL_FILE_LIMIT:
        raise ValueError("profile_too_large")
    profile = json.loads(raw)
    if not isinstance(profile, dict) or profile.get("schema_version") != "lablens-private-model-v1":
        raise ValueError("unsupported_profile")
    model_id = profile.get("model_id")
    if not isinstance(model_id, str) or not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", model_id):
        raise ValueError("invalid_model_id")
    revision = profile.get("model_revision")
    if not isinstance(revision, str) or not HEX_REVISION.fullmatch(revision):
        raise ValueError("invalid_revision")
    if profile.get("tokenizer_revision") != revision:
        raise ValueError("separate_tokenizer_revision_not_supported")
    metadata = profile.get("model_metadata_sha256")
    if not isinstance(metadata, dict) or set(metadata) != set(METADATA_FILES):
        raise ValueError("unexpected_metadata_files")
    expected_hashes = list(metadata.values()) + [profile.get("chat_template_utf8_sha256")]
    if any(not isinstance(value, str) or not HEX_SHA256.fullmatch(value) for value in expected_hashes):
        raise ValueError("invalid_metadata_hash")
    weights = profile.get("weight_files_expected_from_hf_metadata")
    if not isinstance(weights, list) or len(weights) != len(WEIGHT_FILES):
        raise ValueError("unexpected_weight_count")
    names = []
    for item in weights:
        if not isinstance(item, dict):
            raise ValueError("invalid_weight_entry")
        names.append(item.get("path"))
        if type(item.get("bytes")) is not int or item["bytes"] < 1:
            raise ValueError("invalid_weight_size")
        if not isinstance(item.get("sha256"), str) or not HEX_SHA256.fullmatch(item["sha256"]):
            raise ValueError("invalid_weight_hash")
    if sorted(names) != sorted(WEIGHT_FILES):
        raise ValueError("unexpected_weight_names")
    if sum(item["bytes"] for item in weights) != profile.get("expected_weight_file_bytes"):
        raise ValueError("weight_total_mismatch_in_profile")
    return profile, hashlib.sha256(raw).hexdigest()


def locate_snapshot(root: Path, profile: dict) -> Path:
    repo = "models--" + profile["model_id"].replace("/", "--")
    revision = profile["model_revision"]
    candidates = (
        root / "hub" / repo / "snapshots" / revision,
        root / repo / "snapshots" / revision,
        root / "snapshots" / revision,
    )
    found = set()
    for candidate in candidates:
        if not candidate.is_dir():
            continue
        resolved = candidate.resolve(strict=True)
        if not within(resolved, root):
            raise ValueError("snapshot_outside_cache_root")
        found.add(resolved)
    if len(found) != 1:
        raise ValueError("snapshot_missing_or_ambiguous")
    return found.pop()


def inspect_file(snapshot: Path, root: Path, name: str, expected_hash: str | None,
                 expected_bytes: int | None, max_bytes: int, keep_content: bool = False
                 ) -> tuple[dict, bytes | None]:
    report = {
        "file": name, "expected_sha256": expected_hash,
        "expected_bytes": expected_bytes, "observed_sha256": None,
        "observed_bytes": None, "stable_during_read": False,
        "status": "not_checked", "content_identity_verified": False,
    }
    content = bytearray() if keep_content else None
    source = snapshot / name
    try:
        resolved = source.resolve(strict=True)
        if not within(resolved, root):
            report["status"] = "symlink_outside_cache_root"
            return report, None
        # HF snapshot symlinks into its repository's blobs/ are permitted. An
        # allowlisted filename may not target a token or another arbitrary file.
        repo_root = snapshot.parent.parent
        normal_copy = resolved == source
        blob_target = resolved.parent == repo_root / "blobs" and bool(
            re.fullmatch(r"(?:[a-f0-9]{40}|[a-f0-9]{64})", resolved.name)
        )
        if not (normal_copy or blob_target):
            report["status"] = "unexpected_symlink_target"
            return report, None
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
        fd = os.open(resolved, flags)
        with os.fdopen(fd, "rb") as stream:
            before = os.fstat(stream.fileno())
            report["observed_bytes"] = before.st_size
            if not stat.S_ISREG(before.st_mode):
                report["status"] = "not_regular_file"
                return report, None
            if before.st_size > max_bytes:
                report["status"] = "file_exceeds_bounded_size"
                return report, None
            digest = hashlib.sha256()
            total = 0
            while True:
                block = stream.read(min(CHUNK_BYTES, max_bytes - total + 1))
                if not block:
                    break
                total += len(block)
                if total > max_bytes:
                    report["status"] = "file_grew_beyond_bound"
                    return report, None
                digest.update(block)
                if content is not None:
                    content.extend(block)
            after = os.fstat(stream.fileno())
        report["observed_bytes"] = total
        report["observed_sha256"] = digest.hexdigest()
        report["stable_during_read"] = (
            fingerprint(before) == fingerprint(after)
            and source.resolve(strict=True) == resolved
            and fingerprint(resolved.stat()) == fingerprint(after)
        )
        if not report["stable_during_read"]:
            report["status"] = "changed_during_read"
        elif expected_bytes is not None and total != expected_bytes:
            report["status"] = "size_mismatch"
        elif expected_hash is None:
            report["status"] = "observed_only_no_expected_hash"
        elif report["observed_sha256"] != expected_hash:
            report["status"] = "hash_mismatch"
        else:
            report["status"] = "matched"
            report["content_identity_verified"] = True
        return report, bytes(content) if content is not None else None
    except FileNotFoundError:
        report["status"] = "missing"
    except (OSError, RuntimeError) as exc:
        report["status"] = "read_error"
        report["error_type"] = safe_error(exc)
    return report, None


def verify(root: Path, profile_path: Path) -> tuple[dict, int]:
    started = time.monotonic()
    result = {
        "schema_version": "lablens-remote-model-identity-v1",
        "started_at": utc_now(), "network_used": False,
        "credential_files_read": False, "model_requests_sent": 0,
        "passed": False, "live_server_loaded_identity_verified": False,
        "image_identity_verified": False,
        "tokenizer_payload_identity_verified": False,
    }
    try:
        profile, profile_hash = load_profile(profile_path)
        root = root.resolve(strict=True)
        if not root.is_dir():
            raise ValueError("cache_root_not_directory")
        snapshot = locate_snapshot(root, profile)
        result.update({
            "profile_sha256": profile_hash, "model_id": profile["model_id"],
            "model_revision": profile["model_revision"],
            "tokenizer_revision": profile["tokenizer_revision"],
            "snapshot_relative_path": str(snapshot.relative_to(root)),
            "files": [],
        })
        metadata_content = {}
        for name in METADATA_FILES:
            report, content = inspect_file(
                snapshot, root, name, profile["model_metadata_sha256"][name],
                None, SMALL_FILE_LIMIT, keep_content=True,
            )
            result["files"].append(report)
            if report["content_identity_verified"]:
                metadata_content[name] = content
        for item in profile["weight_files_expected_from_hf_metadata"]:
            report, _ = inspect_file(snapshot, root, item["path"], item["sha256"],
                                     item["bytes"], item["bytes"])
            result["files"].append(report)
        result["chat_template_sha256"] = None
        result["chat_template_verified"] = False
        if "tokenizer_config.json" in metadata_content:
            template = json.loads(metadata_content["tokenizer_config.json"]).get("chat_template")
            if isinstance(template, str):
                result["chat_template_sha256"] = hashlib.sha256(template.encode("utf-8")).hexdigest()
                result["chat_template_verified"] = (
                    result["chat_template_sha256"] == profile["chat_template_utf8_sha256"]
                )
        result["weight_index_references_verified"] = False
        if "model.safetensors.index.json" in metadata_content:
            index = json.loads(metadata_content["model.safetensors.index.json"])
            mapping = index.get("weight_map")
            result["weight_index_references_verified"] = (
                isinstance(mapping, dict) and bool(mapping)
                and all(isinstance(value, str) for value in mapping.values())
                and set(mapping.values()) == set(WEIGHT_FILES)
                and index.get("metadata", {}).get("total_size") == profile["estimated_weight_tensor_bytes"]
            )
        result["tokenizer_payload_observations"] = []
        for name in TOKENIZER_PAYLOAD_FILES:
            observation, _ = inspect_file(snapshot, root, name, None, None, TOKENIZER_FILE_LIMIT)
            result["tokenizer_payload_observations"].append(observation)
        result["weight_files_identity_verified"] = all(
            item["content_identity_verified"] for item in result["files"] if item["file"] in WEIGHT_FILES
        )
        result["pinned_metadata_identity_verified"] = all(
            item["content_identity_verified"] for item in result["files"] if item["file"] in METADATA_FILES
        )
        result["passed"] = all((
            result["weight_files_identity_verified"], result["pinned_metadata_identity_verified"],
            result["chat_template_verified"], result["weight_index_references_verified"],
        ))
        result["scope"] = "Pinned local weight/config/index/license/template bytes only; tokenizer payload has no expected hashes in this profile, and the live server and image are not attested."
        code = 0 if result["passed"] else 1
    except (OSError, RuntimeError, ValueError, TypeError, KeyError) as exc:
        result["error_type"] = safe_error(exc)
        if isinstance(exc, ValueError) and str(exc) in {
            "profile_too_large", "unsupported_profile", "invalid_model_id", "invalid_revision",
            "separate_tokenizer_revision_not_supported", "unexpected_metadata_files",
            "invalid_metadata_hash", "unexpected_weight_count", "invalid_weight_entry",
            "invalid_weight_size", "invalid_weight_hash", "unexpected_weight_names",
            "weight_total_mismatch_in_profile", "snapshot_outside_cache_root",
            "snapshot_missing_or_ambiguous", "cache_root_not_directory",
        }:
            result["error_code"] = str(exc)
        code = 2
    result["finished_at"] = utc_now()
    result["elapsed_seconds"] = round(time.monotonic() - started, 3)
    return result, code


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache-root", type=Path, required=True,
                        help="launcher model-cache, HF hub, or models--org--name directory")
    parser.add_argument("--profile", type=Path, required=True)
    args = parser.parse_args()
    result, code = verify(args.cache_root, args.profile)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return code


if __name__ == "__main__":
    sys.exit(main())
