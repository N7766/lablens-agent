"""Reuse frozen Round 8 safety controls and test Round 9 history binding offline."""
import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO = Path(__file__).resolve().parents[1]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


harness = load("round9_harness_test", REPO / "scripts/evaluate_conversation_round9.py")
scoring = load("round9_score_test", REPO / "scripts/score_conversation_round9.py")
# Separate module globals keep the historical suite and its runner untouched.
_controls = load("round9_inherited_offline_controls", REPO / "tests/test_round8_evaluation_harness.py")
_controls.harness, _controls.scoring = harness, scoring
for _name, _value in vars(_controls).items():
    if _name.startswith("test_") or _name in {"evidence_resolver", "complete_scoring_fixture"}:
        globals()[_name] = _value


@pytest.fixture
def historical_prepare_fixture(tmp_path, monkeypatch):
    fixture = _controls.historical_prepare_fixture.__wrapped__(tmp_path, monkeypatch)
    fixture.base = fixture.repo / "round9"
    latest = fixture.repo / "round8"
    for relative in ("scripts/evaluate_conversation_round9.py", "scripts/score_conversation_round9.py",
                     "tests/test_round9_evaluation_harness.py", "round9/protocol.md"):
        path = fixture.repo / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("Synthetic Round 9 prepare fixture.\n")
    frozen = latest / "frozen/input.json"
    harness.dump(frozen, {"synthetic_fixture": True})
    harness.dump(latest / "input-freeze.json", {"files": {
        str(frozen.relative_to(fixture.repo)): harness.sha(frozen)}})
    run = latest / "runs/round8-once"
    harness.dump(run / "run-summary.json", {"candidate_sha": "c" * 40,
        "input_freeze_sha256": harness.sha(latest / "input-freeze.json")})
    harness.dump(run / "raw/request.json", {"synthetic_raw": True})
    harness.dump(run / "raw-seal.json", {"files": {
        str(p.relative_to(run)): harness.sha(p) for p in run.rglob("*.json") if p.name != "raw-seal.json"}})
    harness.dump(latest / "assessment/scores.json", {"synthetic_fixture": True})
    (latest / "assessment/failure-review.md").write_text("Preserved Round 8 failure.\n")
    grade_files = [latest / "assessment" / name for name in ("scores.json", "failure-review.md")]
    harness.dump(latest / "assessment/grading-manifest.json", {
        "candidate_sha": "c" * 40, "raw_seal_sha256": harness.sha(run / "raw-seal.json"),
        "files": [{"path": p.name, "sha256": harness.sha(p)} for p in grade_files]})
    for name in ("REPORT.md", "execution-notes.md"):
        (latest / name).write_text("Synthetic historical evidence; no quality claim.\n")
    monkeypatch.setattr(harness, "BASE", fixture.base)
    monkeypatch.setattr(harness, "LATEST", latest)
    monkeypatch.setattr(harness, "__file__", str(fixture.repo / "scripts/evaluate_conversation_round9.py"))
    fixture.latest = latest
    return fixture


def test_round9_binds_entire_round8_raw_grading_and_input_history(historical_prepare_fixture):
    fixture = historical_prepare_fixture
    harness.prepare(SimpleNamespace())
    manifest = harness.read(fixture.base / "input-freeze.json")
    required = {"round8/frozen/input.json", "round8/input-freeze.json", "round8/REPORT.md",
        "round8/execution-notes.md", "round8/runs/round8-once/raw/request.json",
        "round8/runs/round8-once/run-summary.json", "round8/runs/round8-once/raw-seal.json",
        "round8/assessment/grading-manifest.json", "round8/assessment/scores.json",
        "round8/assessment/failure-review.md"}
    assert required.issubset(manifest["files"])
    harness.verify_manifest(fixture.repo, manifest)


@pytest.mark.parametrize("target", ["frozen/input.json", "runs/round8-once/raw/request.json",
    "runs/round8-once/run-summary.json", "assessment/scores.json", "assessment/failure-review.md"])
def test_round9_rejects_changed_round8_history_before_prepare(historical_prepare_fixture, target):
    fixture = historical_prepare_fixture
    with (fixture.latest / target).open("a") as stream:
        stream.write("\n")
    with pytest.raises(RuntimeError, match="Manifest file mismatch"):
        harness.prepare(SimpleNamespace())
    assert not (fixture.base / "input-freeze.json").exists()


@pytest.mark.parametrize("field", ["raw_seal_sha256", "candidate_sha"])
def test_round9_rejects_round8_grading_identity_disagreement(historical_prepare_fixture, field):
    fixture = historical_prepare_fixture
    path = fixture.latest / "assessment/grading-manifest.json"
    data = harness.read(path)
    data[field] = "0" * (40 if field == "candidate_sha" else 64)
    harness.dump(path, data)
    with pytest.raises(RuntimeError, match="Round 8 grading/.* identity mismatch"):
        harness.prepare(SimpleNamespace())
    assert not (fixture.base / "input-freeze.json").exists()


def test_round9_rejects_round8_input_identity_disagreement(historical_prepare_fixture):
    fixture = historical_prepare_fixture
    path = fixture.latest / "input-freeze.json"
    data = harness.read(path)
    data["mutated_identity"] = True
    harness.dump(path, data)
    with pytest.raises(RuntimeError, match="Round 8 run/input freeze identity mismatch"):
        harness.prepare(SimpleNamespace())


def test_round9_detects_raw_mutation_after_input_freeze(historical_prepare_fixture):
    fixture = historical_prepare_fixture
    harness.prepare(SimpleNamespace())
    manifest = harness.read(fixture.base / "input-freeze.json")
    path = fixture.latest / "runs/round8-once/raw/request.json"
    with path.open("a") as stream:
        stream.write("\n")
    with pytest.raises(RuntimeError, match="Manifest file mismatch"):
        harness.verify_manifest(fixture.repo, manifest)


def test_round9_requires_historical_summary_inside_its_raw_seal(historical_prepare_fixture):
    fixture = historical_prepare_fixture
    path = fixture.latest / "runs/round8-once/raw-seal.json"
    data = harness.read(path)
    del data["files"]["run-summary.json"]
    harness.dump(path, data)
    with pytest.raises(RuntimeError, match="run summary must be sealed"):
        harness.prepare(SimpleNamespace())
    assert not (fixture.base / "input-freeze.json").exists()
