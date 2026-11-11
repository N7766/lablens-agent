"""CPU-only deployment checks: no GPU, model download, provider API or server."""
from contextlib import suppress
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/private_model_deploy.py"
SPEC = importlib.util.spec_from_file_location("private_model_deploy_test", SCRIPT)
deploy = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(deploy)


@pytest.fixture
def profile(tmp_path):
    value = json.loads((ROOT / "deploy/private-model/profile.json").read_text())
    path = tmp_path / "profile.json"
    path.write_text(json.dumps(value))
    return value, path


def _write_profile(profile):
    value, path = profile
    path.write_text(json.dumps(value))
    return path


@pytest.fixture
def host(monkeypatch, profile):
    value, _ = profile
    state = {
        "gpu_rows": [("RTX A6000", "49140", "580.178.04", "8.6")],
        "cgroup": str(48 * 1024**3),
        "disk_bytes": 60 * 1024**3,
        "malformed_extra_row": False,
        "vllm_distribution_version": value["vllm_distribution_version"],
        "runtime_distribution_versions": dict(value["runtime_distribution_pins"]),
    }
    monkeypatch.setattr(deploy.platform, "system", lambda: "Linux")
    monkeypatch.setattr(deploy.platform, "machine", lambda: "x86_64")
    def distribution_version(name):
        if name == "vllm":
            return state["vllm_distribution_version"]
        observed = state["runtime_distribution_versions"][name]
        if observed is None:
            raise deploy.importlib.metadata.PackageNotFoundError(name)
        return observed
    monkeypatch.setattr(deploy.importlib.metadata, "version", distribution_version)
    monkeypatch.setenv(deploy.KEY_ENV, "unit-test-key-" + "x" * 40)
    monkeypatch.setattr(deploy.os, "sysconf", lambda key: 4096 if key == "SC_PAGE_SIZE" else 64 * 1024**3 // 4096)
    original_read = Path.read_text
    original_is_file = Path.is_file

    def read(path, *args, **kwargs):
        if str(path) == "/sys/fs/cgroup/memory.max":
            return state["cgroup"]
        return original_read(path, *args, **kwargs)

    def is_file(path):
        if str(path) in ("/sys/fs/cgroup/memory.max", "/sys/fs/cgroup/memory/memory.limit_in_bytes"):
            return str(path).endswith("memory.max")
        return original_is_file(path)

    def nvidia(command, **kwargs):
        assert command[0] == "nvidia-smi"
        with_capability = "compute_cap" in " ".join(command)
        rows = [", ".join(row if with_capability else row[:3]) for row in state["gpu_rows"]]
        if state["malformed_extra_row"]:
            rows.append("malformed second GPU row")
        return SimpleNamespace(stdout="\n".join(rows) + "\n")

    monkeypatch.setattr(Path, "read_text", read)
    monkeypatch.setattr(Path, "is_file", is_file)
    monkeypatch.setattr(deploy.shutil, "disk_usage", lambda path: SimpleNamespace(free=state["disk_bytes"]))
    monkeypatch.setattr(deploy.subprocess, "run", nvidia)
    return state


def test_default_plan_has_no_process_or_download_and_no_key(profile, monkeypatch, capsys):
    key = "unit-test-secret-" + "k" * 40
    monkeypatch.setenv(deploy.KEY_ENV, key)
    def forbidden(*args, **kwargs):
        raise AssertionError("Planning must not invoke any child process")
    monkeypatch.setattr(deploy.subprocess, "run", forbidden)
    monkeypatch.setattr(deploy.subprocess, "Popen", forbidden)
    assert deploy.main(["--profile", str(profile[1])]) == 0
    output = capsys.readouterr().out
    result = json.loads(output)
    assert key not in output
    assert result["downloads_performed"] is False
    assert result["rental_performed"] is False
    assert result["billing_limit_enforced_by_script"] is False
    assert result["expected_vllm_distribution_version"] == "0.32.0+cu129"
    assert result["expected_runtime_distribution_pins"] == {"torch": "2.13.0+cu129", "torchcodec": "0.17.0+cpu"}
    command = result["server_command"]
    assert command[command.index("--host") + 1] == "127.0.0.1"
    assert command[command.index("--revision") + 1] == profile[0]["model_revision"]
    assert "--api-key" not in command
    assert "--enable-chunked-prefill" in command


@pytest.mark.parametrize("field,bad", [
    ("model_revision", "main"),
    ("image_digest", "latest"),
    ("host", "0.0.0.0"),
    ("max_num_seqs", 2),
    ("max_runtime_seconds", 7201),
    ("gpu_memory_utilization", float("nan")),
    ("generation_config", "auto"),
    ("structured_outputs_backend", "guidance"),
    ("enable_prefix_caching", True),
    ("enable_chunked_prefill", False),
    ("kv_cache_dtype", "fp8"),
])
def test_profile_rejects_mutability_exposure_or_unbounded_execution(profile, field, bad):
    profile[0][field] = bad
    with pytest.raises(ValueError):
        deploy.load_profile(_write_profile(profile))


def test_host_accepts_exact_runtime_and_bounded_container(profile, host, tmp_path):
    result = deploy.check_host(profile[0], tmp_path)
    assert result["passed"] is True
    assert result["details"]["host_memory_mib"] == 48 * 1024
    assert result["details"]["vllm_version"] == "0.32.0+cu129"
    assert result["details"]["expected_vllm_distribution_version"] == "0.32.0+cu129"
    assert result["details"]["vllm_release_version"] == "0.32.0"
    assert result["details"]["runtime_distribution_versions"] == {"torch": "2.13.0+cu129", "torchcodec": "0.17.0+cpu"}
    assert result["details"]["expected_runtime_distribution_pins"] == result["details"]["runtime_distribution_versions"]
    assert "unit-test-key-" not in json.dumps(result)


@pytest.mark.parametrize("changes", [
    {"vllm_distribution_version": None},
    {"vllm_distribution_version": "0.32.0"},
    {"vllm_distribution_version": "0.32.0+cu130"},
    {"vllm_distribution_version": "0.32.1+cu129"},
    {"vllm_distribution_version": "0.32.0+cu129.post1"},
    {"cuda_version": "13.0.0", "vllm_distribution_version": "0.32.0+cu130"},
    {"cuda_version": "12.9.0"},
    {"cuda_version": None},
    {"vllm_version": "0.32.1", "vllm_distribution_version": "0.32.1+cu129"},
])
def test_profile_rejects_unverified_release_cuda_distribution_mapping(profile, changes):
    profile[0].update(changes)
    with pytest.raises(ValueError):
        deploy.load_profile(_write_profile(profile))


def test_profile_requires_explicit_distribution_version(profile):
    del profile[0]["vllm_distribution_version"]
    with pytest.raises(ValueError):
        deploy.load_profile(_write_profile(profile))


@pytest.mark.parametrize("observed", [
    "0.32.0", "0.32.0+cu130", "0.32.1+cu129", "0.32.0+cu129.post1",
    "0.32.0+cu129.dev1", "0.32.0+cu129 ", None,
])
def test_host_requires_exact_distribution_including_cuda_suffix(profile, host, tmp_path, observed):
    host["vllm_distribution_version"] = observed
    result = deploy.check_host(profile[0], tmp_path)
    assert result["passed"] is False
    assert result["checks"]["vllm_version_matches"] is False
    assert result["details"]["vllm_version"] == observed
    assert result["details"]["expected_vllm_distribution_version"] == "0.32.0+cu129"


def test_bare_release_fails_preflight_before_launch(profile, host, monkeypatch, tmp_path):
    host["vllm_distribution_version"] = "0.32.0"
    monkeypatch.setattr(deploy.subprocess, "Popen", lambda *args, **kwargs: pytest.fail("Wrong distribution must not launch"))
    output = tmp_path / "wrong-distribution"
    assert deploy.main(["serve", "--profile", str(profile[1]), "--output", str(output)]) == 2
    result = json.loads((output / "host-check.json").read_text())
    assert result["checks"]["vllm_version_matches"] is False
    assert not (output / "launch-intent.json").exists()


@pytest.mark.parametrize("pins", [
    None, {}, {"torch": "2.13.0+cu129"},
    {"torch": "2.13.0", "torchcodec": "0.17.0+cpu"},
    {"torch": "2.13.0+cpu", "torchcodec": "0.17.0+cpu"},
    {"torch": "2.13.0+cu130", "torchcodec": "0.17.0+cpu"},
    {"torch": "2.12.0+cu129", "torchcodec": "0.17.0+cpu"},
    {"torch": "2.13.0+cu129", "torchcodec": "0.17.0"},
    {"torch": "2.13.0+cu129", "torchcodec": "0.17.0+cu130"},
    {"torch": "2.13.0+cu129", "torchcodec": "0.16.0+cpu"},
    {"torch": "2.13.0+cu129", "torchcodec": "0.17.0+cpu", "numpy": "2.0.0"},
])
def test_profile_rejects_unverified_runtime_distribution_pins(profile, pins):
    profile[0]["runtime_distribution_pins"] = pins
    with pytest.raises(ValueError):
        deploy.load_profile(_write_profile(profile))


def test_profile_requires_runtime_distribution_pins(profile):
    del profile[0]["runtime_distribution_pins"]
    with pytest.raises(ValueError):
        deploy.load_profile(_write_profile(profile))


@pytest.mark.parametrize("name,observed", [
    ("torch", "2.13.0"), ("torch", "2.13.0+cpu"), ("torch", "2.13.0+cu130"),
    ("torch", "2.12.0+cu129"), ("torch", None),
    ("torchcodec", "0.17.0"), ("torchcodec", "0.17.0+cu130"),
    ("torchcodec", "0.16.0+cpu"), ("torchcodec", "0.17.0+cpu.post1"), ("torchcodec", None),
])
def test_runtime_distribution_mismatch_blocks_process_launch(profile, host, monkeypatch, tmp_path, name, observed):
    host["runtime_distribution_versions"][name] = observed
    monkeypatch.setattr(deploy.subprocess, "Popen", lambda *args, **kwargs: pytest.fail("Unverified runtime must not launch"))
    output = tmp_path / "wrong-runtime"
    assert deploy.main(["serve", "--profile", str(profile[1]), "--output", str(output)]) == 2
    result = json.loads((output / "host-check.json").read_text())
    assert result["passed"] is False
    assert result["checks"][name + "_version_matches"] is False
    assert result["details"]["runtime_distribution_versions"][name] == observed
    assert result["details"]["expected_runtime_distribution_pins"] == {"torch": "2.13.0+cu129", "torchcodec": "0.17.0+cpu"}
    assert not (output / "launch-intent.json").exists()


@pytest.mark.parametrize("kind", ["cgroup", "driver", "gpu_memory", "two_gpus", "disk", "capability", "malformed"])
def test_host_rejects_incompatible_or_unverifiable_resources(profile, host, tmp_path, kind):
    if kind == "cgroup":
        host["cgroup"] = str(8 * 1024**3)
    elif kind == "driver":
        host["gpu_rows"] = [("RTX A6000", "49140", "550.54.15", "8.6")]
    elif kind == "gpu_memory":
        host["gpu_rows"] = [("RTX 4060", "8192", "580.178.04", "8.9")]
    elif kind == "two_gpus":
        host["gpu_rows"] *= 2
    elif kind == "disk":
        host["disk_bytes"] = 10 * 1024**3
    elif kind == "capability":
        host["gpu_rows"] = [("Titan RTX", "24576", "580.178.04", "7.5")]
    else:
        host["malformed_extra_row"] = True
    assert deploy.check_host(profile[0], tmp_path)["passed"] is False


def test_failed_host_preflight_never_starts_a_process(profile, monkeypatch, tmp_path):
    monkeypatch.setattr(deploy, "check_host", lambda *args: {"passed": False, "checks": {"gpu_memory": False}})
    monkeypatch.setattr(deploy.subprocess, "Popen", lambda *args, **kwargs: pytest.fail("GPU preflight failed"))
    output = tmp_path / "run"
    assert deploy.main(["serve", "--profile", str(profile[1]), "--output", str(output)]) == 2
    assert json.loads((output / "host-check.json").read_text())["passed"] is False
    assert not (output / "launch-intent.json").exists()


def test_profile_change_during_preflight_cannot_rebind_launch_evidence(profile, monkeypatch, tmp_path):
    original_hash = hashlib.sha256(profile[1].read_bytes()).hexdigest()
    def drift(*args):
        changed = dict(profile[0], port=8001)
        profile[1].write_text(json.dumps(changed))
        return {"passed": True, "scope": "CPU test double"}
    monkeypatch.setattr(deploy, "check_host", drift)
    monkeypatch.setenv(deploy.KEY_ENV, "unit-test-key-" + "x" * 40)
    monkeypatch.setattr(deploy.subprocess, "Popen", lambda *args, **kwargs: pytest.fail("Changed profile must not start a child"))
    output = tmp_path / "run"
    with pytest.raises(ValueError):
        deploy.serve(profile[0], profile[1], output)
    intent = output / "launch-intent.json"
    if intent.exists():
        assert json.loads(intent.read_text())["profile_sha256"] == original_hash


def _allow_cpu_child(monkeypatch, code):
    monkeypatch.setattr(deploy, "check_host", lambda *args: {"passed": True, "scope": "CPU test double"})
    monkeypatch.setattr(deploy, "server_command", lambda p: [sys.executable, "-c", code])
    monkeypatch.setenv(deploy.KEY_ENV, "test-private-model-key-" + "x" * 40)


@pytest.mark.skipif(os.name != "posix", reason="Private deployment requires POSIX process groups")
@pytest.mark.parametrize("code,expected_status", [(0, 0), (7, 1)])
def test_real_cpu_child_exit_is_recorded_without_claiming_billing_stopped(profile, monkeypatch, tmp_path, code, expected_status):
    _allow_cpu_child(monkeypatch, "raise SystemExit(%d)" % code)
    output = tmp_path / "run"
    handlers = {sig: signal.getsignal(sig) for sig in (signal.SIGINT, signal.SIGTERM)}
    assert deploy.serve(profile[0], profile[1], output) == expected_status
    assert all(signal.getsignal(sig) == handler for sig, handler in handlers.items())
    exit_info = json.loads((output / "exit.json").read_text())
    assert exit_info["outcome"] == "server_exited"
    assert exit_info["return_code"] == code
    assert exit_info["rental_billing_stopped"] is False
    assert output.stat().st_mode & 0o777 == 0o700
    for path in output.iterdir():
        assert path.stat().st_mode & 0o777 == (0o700 if path.is_dir() else 0o600)


@pytest.mark.skipif(os.name != "posix", reason="Private deployment requires POSIX process groups")
def test_cleanup_failure_still_records_exit_evidence_and_cannot_report_success(profile, monkeypatch, tmp_path):
    _allow_cpu_child(monkeypatch, "raise SystemExit(0)")
    def denied(process):
        assert process.poll() == 0  # The short CPU child has already exited; no orphan is created.
        raise PermissionError("private-environment-value-must-not-enter-evidence")
    monkeypatch.setattr(deploy, "stop_group", denied)
    output = tmp_path / "run"
    old_handler = signal.getsignal(signal.SIGTERM)
    assert deploy.serve(profile[0], profile[1], output) == 1
    result = json.loads((output / "exit.json").read_text())
    assert result["outcome"] == "server_exited" and result["return_code"] == 0
    assert result["process_cleanup_verified"] is False
    assert result["process_cleanup_error"] == "PermissionError"
    assert result["rental_billing_stopped"] is False
    assert "private-environment-value" not in json.dumps(result)
    assert signal.getsignal(signal.SIGTERM) == old_handler


@pytest.mark.parametrize("member_state,should_raise", [("S", True), ("Z", False)])
def test_eperm_is_resolved_by_exact_group_members_not_assumed_absent(monkeypatch, member_state, should_raise):
    process = SimpleNamespace(pid=4321)
    def denied(pgid, sig):
        assert pgid == 4321 and sig == signal.SIGTERM
        raise PermissionError("simulated signal permission")
    def process_table(command, **kwargs):
        assert command == ["ps", "-axo", "pid=,pgid=,stat="]
        # The group leader has gone; its descendant is still in the owned group.
        return SimpleNamespace(stdout=f"4322 4321 {member_state}\n9000 9000 S\n")
    monkeypatch.setattr(deploy.os, "killpg", denied)
    monkeypatch.setattr(deploy.subprocess, "run", process_table)
    if should_raise:
        with pytest.raises(PermissionError):
            deploy.stop_group(process)
    else:
        deploy.stop_group(process)


@pytest.mark.skipif(os.name != "posix", reason="Private deployment requires POSIX process groups")
def test_child_receives_only_runtime_key_without_putting_it_in_artifacts(profile, monkeypatch, tmp_path):
    code = """import os,json
print(json.dumps({'runtime_key_ok': os.environ.get('VLLM_API_KEY') == 'test-private-model-key-' + 'x'*40,
 'launcher_key_present': 'LABLENS_PRIVATE_MODEL_API_KEY' in os.environ,
 'provider_key_present': any(k in os.environ for k in ('HF_TOKEN','HUGGING_FACE_HUB_TOKEN','LLM_API_KEY','EMBEDDING_API_KEY'))}))
"""
    _allow_cpu_child(monkeypatch, code)
    for name in ("HF_TOKEN", "HUGGING_FACE_HUB_TOKEN", "LLM_API_KEY", "EMBEDDING_API_KEY"):
        monkeypatch.setenv(name, "unrelated-provider-secret")
    output = tmp_path / "run"
    assert deploy.serve(profile[0], profile[1], output) == 0
    result = json.loads((output / "server.log").read_text())
    assert result == {"runtime_key_ok": True, "launcher_key_present": False, "provider_key_present": False}
    # The test command contains an expression for the key, never its expanded value.
    for path in output.iterdir():
        if not path.is_file():
            continue
        assert ("test-private-model-key-" + "x" * 40) not in path.read_text()
        assert "unrelated-provider-secret" not in path.read_text()


@pytest.mark.skipif(os.name != "posix", reason="Private deployment requires POSIX process groups")
def test_inherited_hub_caches_are_replaced_by_the_preflight_filesystem(profile, monkeypatch, tmp_path):
    inherited = tmp_path / "unrelated-user-cache"
    inherited.mkdir()
    (inherited / "token").write_text("a-user-token-that-must-not-be-read")
    for key in ("HF_HOME", "HF_HUB_CACHE", "HUGGINGFACE_HUB_CACHE", "TRANSFORMERS_CACHE", "HF_XET_CACHE", "HF_ASSETS_CACHE"):
        monkeypatch.setenv(key, str(inherited))
    monkeypatch.setenv("HF_TOKEN_PATH", str(inherited / "token"))
    code = """import os,json
print(json.dumps({k:os.environ.get(k) for k in ('HF_HOME','HF_HUB_CACHE','HUGGINGFACE_HUB_CACHE','TRANSFORMERS_CACHE','HF_XET_CACHE','HF_ASSETS_CACHE','HF_TOKEN_PATH','HF_HUB_DISABLE_IMPLICIT_TOKEN')}))
"""
    _allow_cpu_child(monkeypatch, code)
    output = tmp_path / "run"
    assert deploy.serve(profile[0], profile[1], output) == 0
    env = json.loads((output / "server.log").read_text())
    cache_root = (output / "model-cache").resolve()
    for key in ("HF_HOME", "HF_HUB_CACHE"):
        assert env[key], key + " must point into the checked filesystem"
    assert env.pop("HF_HUB_DISABLE_IMPLICIT_TOKEN") == "1"
    assert env.get("HF_TOKEN_PATH") is None or Path(env["HF_TOKEN_PATH"]).resolve().is_relative_to(cache_root)
    for value in env.values():
        if value is not None:
            assert Path(value).resolve().is_relative_to(cache_root)
    assert (inherited / "token").read_text() == "a-user-token-that-must-not-be-read"
    assert "a-user-token-that-must-not-be-read" not in (output / "server.log").read_text()


def _wait_for_file(path, timeout=5):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if path.exists() and path.read_text():
            return int(path.read_text())
        time.sleep(.02)
    pytest.fail("CPU child did not publish its PID before timeout")


def _running(pid):
    with suppress(ProcessLookupError):
        os.kill(pid, 0)
        # A defunct orphan is no longer executing; an init process may reap it later.
        result = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True)
        return bool(result.stdout.strip()) and not result.stdout.strip().startswith("Z")
    return False


def _assert_stopped(pid, timeout=2):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if not _running(pid):
            return
        time.sleep(.02)
    assert not _running(pid), "Owned CPU descendant survived launcher cleanup"


@pytest.mark.skipif(os.name != "posix", reason="Private deployment requires POSIX process groups")
def test_real_cpu_deadline_stops_process_and_keeps_billing_warning(profile, monkeypatch, tmp_path):
    pid_file = tmp_path / "child.pid"
    code = "import os,time,pathlib; pathlib.Path(%r).write_text(str(os.getpid())); time.sleep(60)" % str(pid_file)
    _allow_cpu_child(monkeypatch, code)
    profile[0]["max_runtime_seconds"] = 1
    _write_profile(profile)
    output = tmp_path / "run"
    try:
        assert deploy.serve(profile[0], profile[1], output) == 1
        _assert_stopped(int(pid_file.read_text()))
        result = json.loads((output / "exit.json").read_text())
        assert result["outcome"] == "process_deadline_reached"
        assert result["elapsed_seconds"] < 10
        assert result["rental_billing_stopped"] is False
    finally:
        if pid_file.exists():
            with suppress(ProcessLookupError):
                os.killpg(int(pid_file.read_text()), signal.SIGKILL)


@pytest.mark.skipif(os.name != "posix", reason="Private deployment requires POSIX process groups")
def test_cleanup_kills_term_ignoring_descendant_after_group_leader_exits(tmp_path):
    pid_file = tmp_path / "descendant.pid"
    child_code = "import os,signal,time,pathlib; signal.signal(signal.SIGTERM,signal.SIG_IGN); pathlib.Path(%r).write_text(str(os.getpid())); time.sleep(60)" % str(pid_file)
    parent_code = "import subprocess,sys,time; subprocess.Popen([sys.executable,'-c',%r]); time.sleep(60)" % child_code
    parent = subprocess.Popen([sys.executable, "-c", parent_code], start_new_session=True)
    try:
        child_pid = _wait_for_file(pid_file)
        deploy.stop_group(parent)
        _assert_stopped(child_pid)
    finally:
        with suppress(ProcessLookupError):
            os.killpg(parent.pid, signal.SIGKILL)
        parent.wait(timeout=5)


@pytest.mark.skipif(os.name != "posix", reason="Private deployment requires POSIX process groups")
def test_sigterm_to_launcher_cleans_owned_group_and_writes_exit_evidence(profile, tmp_path):
    profile[0]["max_runtime_seconds"] = 30
    _write_profile(profile)
    pid_file = tmp_path / "server.pid"
    output = tmp_path / "run"
    child_code = "import os,time,pathlib; pathlib.Path(%r).write_text(str(os.getpid())); time.sleep(60)" % str(pid_file)
    wrapper = """import importlib.util,json,sys
spec=importlib.util.spec_from_file_location('deploy_child',sys.argv[1]); d=importlib.util.module_from_spec(spec); spec.loader.exec_module(d)
d.check_host=lambda *args: {'passed':True,'scope':'CPU test double'}
d.server_command=lambda p: [sys.executable,'-c',sys.argv[4]]
p=d.load_profile(sys.argv[2]); p['max_runtime_seconds']=30
raise SystemExit(d.serve(p,sys.argv[2],sys.argv[3]))
"""
    env = dict(os.environ, LABLENS_PRIVATE_MODEL_API_KEY="cpu-test-" + "x" * 40)
    launcher = subprocess.Popen([sys.executable, "-c", wrapper, str(SCRIPT), str(profile[1]), str(output), child_code], env=env)
    server_pid = None
    try:
        server_pid = _wait_for_file(pid_file)
        launcher.send_signal(signal.SIGTERM)
        launcher.wait(timeout=10)
        _assert_stopped(server_pid)
        result = json.loads((output / "exit.json").read_text())
        assert result["outcome"] in ("operator_interrupted", "operator_terminated", "signal_received")
        assert result["rental_billing_stopped"] is False
    finally:
        if server_pid is not None:
            with suppress(ProcessLookupError):
                os.killpg(server_pid, signal.SIGKILL)
        if launcher.poll() is None:
            launcher.kill()
        launcher.wait(timeout=5)
