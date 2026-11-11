#!/usr/bin/env python3
"""Plan or explicitly launch the pinned server INSIDE a rented Linux container.

This tool never rents, stops or destroys a provider instance. Its watchdog bounds
only its own process group; platform GPU/storage billing must be checked separately.
Default mode prints a plan and neither imports vLLM nor downloads model weights.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import importlib.metadata
import json
import math
import os
from pathlib import Path
import platform
import re
import shlex
import shutil
import signal
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PROFILE = ROOT / 'deploy/private-model/profile.json'
KEY_ENV = 'LABLENS_PRIVATE_MODEL_API_KEY'
# Exact metadata of the verified image variant, not a permissive version range.
# A different release or CUDA image requires an explicitly reviewed mapping.
VERIFIED_DISTRIBUTIONS = {('0.32.0', '12.9.1'): '0.32.0+cu129'}
VERIFIED_RUNTIME_DISTRIBUTIONS = {'torch': '2.13.0+cu129', 'torchcodec': '0.17.0+cpu'}


def load_profile(path):
    p = json.loads(Path(path).read_text())
    fixed = dict(schema_version='lablens-private-model-v1', platform='linux/amd64',
                 host='127.0.0.1', served_model_name='lablens-private', dtype='bfloat16',
                 max_num_seqs=1, enforce_eager=True, generation_config='vllm',
                 structured_outputs_backend='xgrammar', enable_prefix_caching=False,
                 enable_chunked_prefill=True, kv_cache_dtype='auto')
    if any(p.get(k) != v or type(p.get(k)) is not type(v) for k, v in fixed.items()):
        raise ValueError('Profile identity, loopback binding or single-sequence policy changed')
    for key in ('model_revision', 'tokenizer_revision'):
        if not re.fullmatch(r'[0-9a-f]{40}', p.get(key, '')):
            raise ValueError('Model and tokenizer require immutable 40-character revisions')
    if not re.fullmatch(r'sha256:[0-9a-f]{64}', p.get('image_digest', '')):
        raise ValueError('An immutable image digest is required')
    if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', p.get('model_id', '')):
        raise ValueError('Invalid public model identity')
    if not re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+', p.get('vllm_version', '')):
        raise ValueError('Use a fixed stable vLLM version')
    cuda_version = p.get('cuda_version')
    if type(cuda_version) is not str:
        raise ValueError('A verified CUDA image version is required')
    expected_distribution = VERIFIED_DISTRIBUTIONS.get((p['vllm_version'], cuda_version))
    if expected_distribution is None or p.get('vllm_distribution_version') != expected_distribution:
        raise ValueError('vLLM distribution must exactly match the verified release and CUDA image')
    if p.get('runtime_distribution_pins') != VERIFIED_RUNTIME_DISTRIBUTIONS:
        raise ValueError('Runtime distributions must exactly match the verified candidate pins')
    for key, low, high in [('max_model_len', 4096, 32768), ('port', 1024, 65535),
                         ('max_num_batched_tokens', 256, 4096),
                         ('max_runtime_seconds', 1, 7200), ('min_gpu_memory_mib', 23000, 100000),
                         ('min_host_memory_mib', 16000, 200000), ('min_disk_free_gib', 30, 200)]:
        if type(p.get(key)) is not int or not low <= p[key] <= high:
            raise ValueError('Profile bound invalid: ' + key)
    u = p.get('gpu_memory_utilization')
    if type(u) not in (int, float) or not math.isfinite(u) or not .5 <= u <= .9:
        raise ValueError('GPU memory utilization must be finite and <=0.9')
    version_tuple(p.get('min_driver_version', ''))
    version_tuple(p.get('min_gpu_compute_capability', ''))
    return p


def version_tuple(value):
    if not re.fullmatch(r'\d+\.\d+(?:\.\d+)?', value):
        raise ValueError('Invalid NVIDIA driver version')
    parts = tuple(int(x) for x in value.split('.'))
    return parts + (0,) * (3 - len(parts))


def server_command(p):
    return [sys.executable, '-m', 'vllm.entrypoints.openai.api_server',
            '--model', p['model_id'], '--revision', p['model_revision'],
            '--tokenizer-revision', p['tokenizer_revision'],
            '--served-model-name', p['served_model_name'], '--host', p['host'],
            '--port', str(p['port']), '--dtype', p['dtype'],
            '--kv-cache-dtype', p['kv_cache_dtype'],
            '--max-model-len', str(p['max_model_len']), '--max-num-seqs', '1',
            '--max-num-batched-tokens', str(p['max_num_batched_tokens']),
            '--gpu-memory-utilization', str(p['gpu_memory_utilization']),
            '--generation-config', 'vllm', '--no-enable-prefix-caching',
            '--structured-outputs-config', '{"backend":"xgrammar"}',
            '--enable-chunked-prefill', '--enforce-eager']


def check_host(p, workdir):
    checks = {'linux_amd64': platform.system() == 'Linux' and platform.machine() in ('x86_64', 'amd64')}
    details = {'vllm_release_version': p['vllm_version'],
               'expected_vllm_distribution_version': p['vllm_distribution_version']}
    try:
        details['vllm_version'] = importlib.metadata.version('vllm')
    except importlib.metadata.PackageNotFoundError:
        details['vllm_version'] = None
    checks['vllm_version_matches'] = details['vllm_version'] == p['vllm_distribution_version']
    details['expected_runtime_distribution_pins'] = dict(p['runtime_distribution_pins'])
    details['runtime_distribution_versions'] = {}
    for name, expected in p['runtime_distribution_pins'].items():
        try:
            observed = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            observed = None
        details['runtime_distribution_versions'][name] = observed
        checks[name + '_version_matches'] = observed == expected
    try:
        result = subprocess.run(['nvidia-smi', '--query-gpu=name,memory.total,driver_version,compute_cap',
                                 '--format=csv,noheader,nounits'], capture_output=True,
                                text=True, timeout=10, check=True)
        rows = [line.split(',') for line in result.stdout.strip().splitlines()]
        if not rows or any(len(row) != 4 for row in rows):
            raise ValueError('Malformed GPU inventory')
        details['gpus'] = [dict(name=r[0].strip(), memory_mib=int(r[1].strip()),
                               driver=r[2].strip(), compute_capability=r[3].strip()) for r in rows]
        checks['single_gpu'] = len(details['gpus']) == 1
        checks['gpu_memory'] = checks['single_gpu'] and details['gpus'][0]['memory_mib'] >= p['min_gpu_memory_mib']
        checks['driver'] = checks['single_gpu'] and version_tuple(details['gpus'][0]['driver']) >= version_tuple(p['min_driver_version'])
        checks['compute_capability'] = checks['single_gpu'] and version_tuple(details['gpus'][0]['compute_capability']) >= version_tuple(p['min_gpu_compute_capability'])
    except (OSError, subprocess.SubprocessError, ValueError, IndexError):
        checks.update(single_gpu=False, gpu_memory=False, driver=False, compute_capability=False)
    try:
        # cgroup limits can be smaller than host physical RAM. Use the minimum.
        limits = [os.sysconf('SC_PAGE_SIZE') * os.sysconf('SC_PHYS_PAGES')]
        for path in ('/sys/fs/cgroup/memory.max', '/sys/fs/cgroup/memory/memory.limit_in_bytes'):
            if Path(path).is_file():
                raw = Path(path).read_text().strip()
                if raw.isdecimal():
                    limits.append(int(raw))
        details['host_memory_mib'] = min(limits) // (1024 ** 2)
        checks['host_memory'] = details['host_memory_mib'] >= p['min_host_memory_mib']
    except (OSError, ValueError):
        checks['host_memory'] = False
    details['disk_free_gib'] = round(shutil.disk_usage(workdir).free / (1024 ** 3), 3)
    checks['disk_free'] = details['disk_free_gib'] >= p['min_disk_free_gib']
    checks['api_key_present'] = bool(re.fullmatch(r'[\x21-\x7e]{32,256}', os.getenv(KEY_ENV, '')))
    return dict(passed=all(checks.values()), checks=checks, details=details,
                scope='Host preflight, not proof of inference or image provenance')


def write_json(path, value):
    with open(path, 'x', encoding='utf-8') as stream:
        os.chmod(path, 0o600)
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write('\n')
        stream.flush()
        os.fsync(stream.fileno())


def group_has_running_members(pgid):
    # A zombie cannot execute or serve requests; killpg(0) is ambiguous for
    # just-reaped groups on macOS. Inspect the exact owned group, not a name.
    result = subprocess.run(['ps', '-axo', 'pid=,pgid=,stat='], capture_output=True,
                            text=True, timeout=2, check=True)
    for line in result.stdout.splitlines():
        fields = line.split()
        if len(fields) != 3 or not fields[0].isdigit() or not fields[1].isdigit():
            raise ValueError('Could not verify process group cleanup')
        if int(fields[1]) == pgid and not fields[2].startswith('Z'):
            return True
    return False


def stop_group(process, grace_seconds=2):
    # The group is created by this invocation, never a name-based global kill.
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    except PermissionError:
        if not group_has_running_members(process.pid):
            return
        raise
    deadline = time.monotonic() + grace_seconds
    while True:
        process.poll()  # Reap the leader, but also account for its descendants.
        if not group_has_running_members(process.pid):
            return
        if time.monotonic() >= deadline:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait(timeout=5)
            verification_deadline = time.monotonic() + 2
            while group_has_running_members(process.pid):
                if time.monotonic() >= verification_deadline:
                    raise ValueError('Owned process group cleanup could not be verified')
                time.sleep(.05)
            return
        time.sleep(.05)


def serve(p, profile_path, output):
    profile_bytes = Path(profile_path).read_bytes()
    if json.loads(profile_bytes) != p:
        raise ValueError('Profile changed after validation')
    profile_sha256 = hashlib.sha256(profile_bytes).hexdigest()
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    os.chmod(output, 0o700)
    check = check_host(p, output)
    write_json(output / 'host-check.json', check)
    if not check['passed']:
        raise ValueError('Host preflight failed; no model process started')
    if Path(profile_path).read_bytes() != profile_bytes:
        raise ValueError('Profile changed during host preflight; no model process started')
    with open(output / 'frozen-profile.json', 'xb') as frozen:
        os.chmod(output / 'frozen-profile.json', 0o600)
        frozen.write(profile_bytes)
        frozen.flush()
        os.fsync(frozen.fileno())
    env = dict(os.environ)
    env['VLLM_API_KEY'] = env.pop(KEY_ENV)
    env['HF_HUB_DISABLE_TELEMETRY'] = '1'
    env['VLLM_NO_USAGE_STATS'] = '1'
    # This public model needs no Hub credential or hosted-provider key.
    for name in ('HF_TOKEN', 'HF_TOKEN_PATH', 'HUGGING_FACE_HUB_TOKEN', 'LLM_API_KEY', 'EMBEDDING_API_KEY'):
        env.pop(name, None)
    cache = output / 'model-cache'
    env['HF_HOME'] = str(cache)
    env['HF_HUB_CACHE'] = str(cache / 'hub')
    env['HF_ASSETS_CACHE'] = str(cache / 'assets')
    env['HF_XET_CACHE'] = str(cache / 'xet')
    env['HF_HUB_DISABLE_IMPLICIT_TOKEN'] = '1'
    env.pop('TRANSFORMERS_CACHE', None)
    env.pop('HUGGINGFACE_HUB_CACHE', None)
    command = server_command(p) + ['--download-dir', str(cache / 'hub')]
    started = time.monotonic()
    write_json(output / 'launch-intent.json', dict(
        started_utc=datetime.now(timezone.utc).isoformat(), command=command,
        profile_sha256=profile_sha256,
        image_claim=p['image_digest'], image_provenance_verified=False,
        max_runtime_seconds=p['max_runtime_seconds'], rental_billing_stopped=False))
    process = None
    outcome = 'start_failed'
    return_code = None
    received_signal = None
    cleanup_error = None
    def mark_interrupted(signum, _frame):
        # Do not raise between Popen and assignment: that could lose the child PID.
        nonlocal received_signal
        received_signal = signum
    previous_handlers = {sig: signal.getsignal(sig) for sig in (signal.SIGTERM, signal.SIGINT)}
    for sig in previous_handlers:
        signal.signal(sig, mark_interrupted)
    try:
        with open(output / 'server.log', 'x') as log:
            os.chmod(output / 'server.log', 0o600)
            process = subprocess.Popen(command, env=env, stdout=log, stderr=subprocess.STDOUT,
                                       start_new_session=True)
            while True:
                if received_signal is not None:
                    outcome = 'operator_interrupted'
                    break
                remaining = p['max_runtime_seconds'] - (time.monotonic() - started)
                if remaining <= 0:
                    outcome = 'process_deadline_reached'
                    break
                try:
                    return_code = process.wait(timeout=min(.2, remaining))
                    outcome = 'server_exited'
                    break
                except subprocess.TimeoutExpired:
                    continue
    finally:
        try:
            try:
                if process is not None:
                    stop_group(process)
            except (OSError, ValueError, subprocess.SubprocessError) as exc:
                cleanup_error = type(exc).__name__
            write_json(output / 'exit.json', dict(outcome=outcome, return_code=return_code,
                       interrupted_signal=received_signal,
                       process_cleanup_verified=cleanup_error is None,
                       process_cleanup_error=cleanup_error,
                       elapsed_seconds=round(time.monotonic() - started, 3), rental_billing_stopped=False,
                       required_next_action='Export evidence, stop/destroy the owned Vast instance and verify provider state; stopping this process does not stop billing.'))
        finally:
            for sig, handler in previous_handlers.items():
                signal.signal(sig, handler)
    return 0 if outcome == 'server_exited' and return_code == 0 and cleanup_error is None else 1


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=['plan', 'host-check', 'serve'], nargs='?', default='plan')
    parser.add_argument('--profile', type=Path, default=DEFAULT_PROFILE)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args(argv)
    try:
        p = load_profile(args.profile)
        if args.mode == 'serve':
            if not args.output:
                raise ValueError('serve requires a new private --output directory')
            return serve(p, args.profile, args.output)
        if args.mode == 'host-check':
            report = check_host(p, Path.cwd())
            print(json.dumps(report, ensure_ascii=False, indent=2))
            return 0 if report['passed'] else 1
        plan = dict(model=p['model_id'], model_revision=p['model_revision'],
                    image='vllm/vllm-openai@' + p['image_digest'],
                    vllm_release_version=p['vllm_version'],
                    expected_vllm_distribution_version=p['vllm_distribution_version'],
                    expected_runtime_distribution_pins=p['runtime_distribution_pins'],
                    launch_mode='Vast SSH container; run launcher inside, no nested Docker',
                    server_command=server_command(p),
                    command_display=shlex.join(server_command(p)),
                    secret_env=KEY_ENV, downloads_performed=False, rental_performed=False,
                    context_window=p['max_model_len'], max_runtime_seconds=p['max_runtime_seconds'],
                    billing_limit_enforced_by_script=False)
        print(json.dumps(plan, ensure_ascii=False, indent=2))
        return 0
    except (ValueError, OSError, KeyError) as exc:
        print('Deployment preparation failed: ' + type(exc).__name__, file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
