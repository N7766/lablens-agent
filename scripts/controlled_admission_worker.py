#!/usr/bin/env python3
"""Loopback-only, fixed-delay worker for synthetic admission tests; no providers."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys
import threading
import time
from uuid import UUID

from fastapi import FastAPI, Header, HTTPException
import uvicorn

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from lablens import rag


def _provider_forbidden(*args, **kwargs):
    raise RuntimeError('Provider configuration is forbidden in the controlled admission worker')


def create_app(delay_ms: float, log_path: Path) -> FastAPI:
    if not 1 <= delay_ms <= 5000:
        raise ValueError('delay_ms must be in 1..5000')
    log_path.parent.mkdir(parents=True, exist_ok=True)
    # Refuse to overwrite a previous run. This process is dedicated to the fixture.
    log = log_path.open('x', encoding='utf-8', buffering=1)
    rag.provider_config = _provider_forbidden
    app = FastAPI(title='LabLens controlled admission worker')
    lock = threading.Lock()
    state = dict(started=0, completed=0, failed=0, active=0, max_active=0,
                 provider_calls=0, fixed_delay_ms=delay_ms,
                 fixture='synthetic-bm25-extractive-only',
                 worker_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                 rag_sha256=rag.LOADED_IMPLEMENTATION_SHA256)

    def record(event, **fields):
        log.write(json.dumps(dict(event=event, observed_unix_ns=time.time_ns(), **fields),
                             ensure_ascii=False) + '\n')

    @app.get('/health')
    def health():
        return {'status': 'ok', 'controlled_worker': True}

    @app.get('/api/rag/status')
    @app.get('/control/status')
    def status():
        with lock:
            return dict(state)

    @app.post('/api/rag/answer')
    def answer(request: rag.RAGRequest, x_lablens_task_id: UUID | None = Header(default=None)):
        if request.mode != 'extractive' or request.strategy != 'bm25':
            raise HTTPException(422, detail='Controlled worker accepts only bm25/extractive')
        if not x_lablens_task_id:
            raise HTTPException(422, detail='Controlled worker requires X-LabLens-Task-Id')
        started = time.monotonic()
        task_id = str(x_lablens_task_id)
        with lock:
            state['started'] += 1
            state['active'] += 1
            state['max_active'] = max(state['max_active'], state['active'])
            record('started', task_id=task_id, snapshot_id=request.snapshot_id,
                   active=state['active'], delay_ms=delay_ms)
        try:
            time.sleep(delay_ms / 1000)
            result = rag.answer_rag(request)
            usage = result.get('usage', {})
            if any(usage.get(key) != 0 for key in ('model_calls', 'embedding_requests', 'embedded_inputs')):
                raise RuntimeError('Controlled worker observed nonzero or missing provider usage')
            with lock:
                state['completed'] += 1
                record('completed', task_id=task_id, snapshot_id=request.snapshot_id,
                       elapsed_ms=round((time.monotonic() - started) * 1000, 3), usage=usage,
                       status=result['status'])
            return result
        except Exception as exc:
            with lock:
                state['failed'] += 1
                record('failed', task_id=task_id, error_type=type(exc).__name__)
            raise
        finally:
            with lock:
                state['active'] -= 1

    app.state.evidence_log = log
    return app


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=8769)
    parser.add_argument('--delay-ms', type=float, default=400)
    parser.add_argument('--log', type=Path, required=True)
    args = parser.parse_args()
    if not 1024 <= args.port <= 65535:
        parser.error('port must be in 1024..65535')
    app = create_app(args.delay_ms, args.log)
    try:
        uvicorn.run(app, host='127.0.0.1', port=args.port, access_log=False)
    finally:
        app.state.evidence_log.close()


if __name__ == '__main__':
    main()
