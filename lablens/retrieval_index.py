"""Bounded, derived retrieval artifacts, never the workspace source of truth.

MySQL owns workspace documents and revisions. This opt-in SQLite store contains
immutable snapshots supplied by the caller. Failed or interrupted builds are
terminal: repeating their identity never silently repeats potentially billed work.
"""
from contextlib import contextmanager
import hashlib
import json
import math
import os
from pathlib import Path
import sqlite3
import time
from typing import Literal
from uuid import uuid4

from pydantic import Field, model_validator

from .schemas import StrictModel
from . import rag
from .cancellation import ExecutionCancelled, check_cancelled


FORMAT_VERSION = 'chunk-manifest-v1:800:120'
MAX_ARTIFACT_BYTES = 16 * 1024 * 1024
MAX_DATABASE_PAGES = 32768  # 128 MiB with the fixed 4096-byte page size.


class IndexRequest(StrictModel):
    snapshot_id: str = Field(min_length=1, max_length=200)
    documents: list[rag.RAGDocument] = Field(max_length=100)
    strategy: Literal['bm25', 'dense', 'hybrid'] = 'bm25'

    @model_validator(mode='after')
    def bounded_snapshot(self):
        rag.RAGRequest(question='index', snapshot_id=self.snapshot_id,
                       documents=self.documents, strategy=self.strategy)
        return self


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def _digest(value):
    return hashlib.sha256(_json(value).encode('utf-8')).hexdigest()


def configuration():
    path = os.getenv('LABLENS_INDEX_DB', '').strip()
    if not path:
        return None
    try:
        capacity = int(os.getenv('LABLENS_INDEX_MAX_ENTRIES', '128'))
        if not 1 <= capacity <= 1024 or path == ':memory:':
            raise ValueError
    except ValueError:
        raise rag.RAGError('Persistent index configuration is invalid', 'unavailable') from None
    return path, capacity


def _identity(request, embedding):
    family = 'semantic' if request.strategy in {'dense', 'hybrid'} else 'bm25'
    public_config = {'format': FORMAT_VERSION, 'family': family,
                     'chunk_size': rag.CHUNK_SIZE, 'chunk_overlap': rag.CHUNK_OVERLAP}
    if embedding:
        public_config.update(base=embedding['base'], model=embedding['model'], revision=embedding['revision'])
    config_id = _digest(public_config)
    documents_hash = rag.documents_sha256(request.documents)
    # Only the composite lookup digest is stored, not the key or its standalone hash.
    private_config = [config_id, rag._sha(embedding['key']) if embedding else None]
    key = _digest([request.snapshot_id, documents_hash, private_config])
    return {'key': key, 'config_id': config_id, 'documents_sha256': documents_hash,
            'snapshot_id': request.snapshot_id, 'family': family}


class IndexStore:
    def __init__(self, path: str, capacity: int = 128):
        self.path = str(Path(path).expanduser().resolve())
        self.capacity = capacity
        Path(self.path).parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with self.connection() as db:
            db.execute('PRAGMA page_size=4096')
            db.execute(f'PRAGMA max_page_count={MAX_DATABASE_PAGES}')
            db.execute('''CREATE TABLE IF NOT EXISTS retrieval_indexes (
                lookup_key TEXT PRIMARY KEY, index_id TEXT NOT NULL UNIQUE,
                snapshot_id TEXT NOT NULL, documents_sha256 TEXT NOT NULL,
                config_id TEXT NOT NULL, family TEXT NOT NULL,
                status TEXT NOT NULL, owner TEXT, lease_until REAL,
                created_at REAL NOT NULL, updated_at REAL NOT NULL,
                chunk_count INTEGER NOT NULL DEFAULT 0, payload TEXT, payload_sha256 TEXT,
                build_usage TEXT NOT NULL, error TEXT)''')
        os.chmod(self.path, 0o600)

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.path, timeout=.5, isolation_level=None)
        db.row_factory = sqlite3.Row
        try:
            # No WAL: transaction journal is bounded by the database page cap.
            db.execute('PRAGMA busy_timeout=500')
            db.execute(f'PRAGMA max_page_count={MAX_DATABASE_PAGES}')
            yield db
        finally:
            db.close()

    @staticmethod
    def _expire(db, key):
        row = db.execute('SELECT * FROM retrieval_indexes WHERE lookup_key=?', (key,)).fetchone()
        if row and row['status'] == 'building' and row['lease_until'] <= time.time():
            usage = rag._new_usage()
            if row['family'] == 'semantic':
                usage.update(embedding_requests=None, embedded_inputs=None, embedding_tokens=None,
                             provider_usage_unknown=True)
            db.execute('''UPDATE retrieval_indexes SET status='failed', owner=NULL, lease_until=NULL,
                updated_at=?, build_usage=?, error=? WHERE lookup_key=? AND status='building' AND owner=?''',
                       (time.time(), _json(usage),
                        'Build lease expired or worker stopped; provider completion is unknown. No automatic retry.',
                        key, row['owner']))
            row = db.execute('SELECT * FROM retrieval_indexes WHERE lookup_key=?', (key,)).fetchone()
        return dict(row) if row else None

    def read(self, key):
        with self.connection() as db:
            return self._expire(db, key)

    def claim(self, identity, lease_seconds):
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            try:
                existing = self._expire(db, identity['key'])
                if existing:
                    db.execute('COMMIT')
                    return existing, False
                count = db.execute('SELECT COUNT(*) FROM retrieval_indexes').fetchone()[0]
                if count >= self.capacity:
                    db.execute('COMMIT')
                    return None, False
                now = time.time()
                index_id, owner = 'idx:' + str(uuid4()), str(uuid4())
                db.execute('''INSERT INTO retrieval_indexes
                    (lookup_key,index_id,snapshot_id,documents_sha256,config_id,family,status,owner,
                     lease_until,created_at,updated_at,build_usage)
                     VALUES (?,?,?,?,?,?,'building',?,?,?,?,?)''',
                           (identity['key'], index_id, identity['snapshot_id'], identity['documents_sha256'],
                            identity['config_id'], identity['family'], owner, now + lease_seconds,
                            now, now, _json(rag._new_usage())))
                row = dict(db.execute('SELECT * FROM retrieval_indexes WHERE lookup_key=?', (identity['key'],)).fetchone())
                db.execute('COMMIT')
                return row, True
            except BaseException:
                if db.in_transaction:
                    db.execute('ROLLBACK')
                raise

    def finish(self, row, status, usage, *, chunks=None, vectors=None, error=None):
        payload = _json({'chunks': chunks, 'vectors': vectors}) if status == 'ready' else None
        if payload is not None and len(payload.encode('utf-8')) > MAX_ARTIFACT_BYTES:
            raise rag.RAGError('Persistent index artifact exceeds the 16 MiB limit', 'capacity_exceeded')
        with self.connection() as db:
            changed = db.execute('''UPDATE retrieval_indexes
                SET status=?, owner=NULL, lease_until=NULL, updated_at=?, chunk_count=?, payload=?,
                    payload_sha256=?, build_usage=?, error=?
                WHERE lookup_key=? AND status='building' AND owner=? AND lease_until>?''',
                (status, time.time(), len(chunks or []), payload,
                 rag._sha(payload) if payload is not None else None, _json(usage), error,
                 row['lookup_key'], row['owner'], time.time())).rowcount
            if changed != 1:
                raise rag.RAGError('Index build lost its lease; no artifact was published', 'unavailable')
        return self.read(row['lookup_key'])

    def corrupt(self, row):
        with self.connection() as db:
            db.execute('''UPDATE retrieval_indexes SET status='failed', payload=NULL,
                payload_sha256=NULL, updated_at=?, error=? WHERE lookup_key=? AND status='ready' ''',
                (time.time(), 'Stored artifact validation failed; no automatic rebuild.', row['lookup_key']))
        return self.read(row['lookup_key'])


def _validate_artifact(row, request):
    raw = row['payload']
    if not isinstance(raw, str) or rag._sha(raw) != row['payload_sha256']:
        raise ValueError('checksum')
    payload = json.loads(raw)
    chunks, vectors = payload['chunks'], payload['vectors']
    if not isinstance(chunks, list) or len(chunks) != row['chunk_count']:
        raise ValueError('manifest')
    sources = {document.id: document for document in request.documents}
    previous = (-1, -1)
    document_positions = {document.id: i for i, document in enumerate(request.documents)}
    for chunk in chunks:
        document = sources[chunk['document_id']]
        start, end = chunk['start'], chunk['end']
        if type(start) is not int or type(end) is not int or not 0 <= start < end <= len(document.text):
            raise ValueError('offsets')
        if end - start > rag.CHUNK_SIZE or chunk['title'] != document.title or chunk['source'] != document.source:
            raise ValueError('source')
        text = document.text[start:end]
        identity = json.dumps([document.id, document.title, document.source, start, end, text], ensure_ascii=False)
        if (chunk['text'] != text or chunk['content_sha256'] != rag._sha(text)
                or chunk['chunk_id'] != 'chunk:' + rag._sha(identity)[:24]):
            raise ValueError('chunk')
        position = (document_positions[document.id], start)
        if position <= previous:
            raise ValueError('order')
        previous = position
    if row['family'] == 'semantic':
        if not isinstance(vectors, list) or len(vectors) != len(chunks):
            raise ValueError('vectors')
        vectors = rag._vectors({'data': [{'index': i, 'embedding': value} for i, value in enumerate(vectors)]}, len(chunks))
    elif vectors is not None:
        raise ValueError('unexpected vectors')
    return chunks, vectors


def _response(request, identity=None, row=None, *, status=None, usage=None, error=None, reused=False):
    result = dict(status=status or (row['status'] if row else 'missing'),
                  snapshot_id=request.snapshot_id, strategy=request.strategy,
                  documents_sha256=rag.documents_sha256(request.documents),
                  index_id=row['index_id'] if row else None,
                  config_id=identity['config_id'] if identity else None,
                  chunk_count=row['chunk_count'] if row else 0,
                  index_reused=reused, usage=usage if usage is not None else rag._new_usage(),
                  build_usage=json.loads(row['build_usage']) if row else None,
                  storage_scope='derived immutable artifact; MySQL owns workspace revision',
                  index_kind='persistent exact scan; not ANN',
                  retry_policy='terminal failures require a new snapshot identity; no automatic retry')
    if error or (row and row['error']):
        result['error'] = error or row['error']
    return result


def _context(request, started=None):
    setting = configuration()
    if not setting:
        return None, None, None
    embedding = rag.provider_config('embedding', deadline_started=started) if request.strategy in {'dense', 'hybrid'} else None
    return IndexStore(*setting), _identity(request, embedding), embedding


def lookup(request, *, embedding=None):
    """Return exact matching verified artifact, never creating or rebuilding it."""
    setting = configuration()
    if not setting:
        return {'status': 'disabled'}, None
    if request.strategy in {'dense', 'hybrid'} and embedding is None:
        embedding = rag.provider_config('embedding')
    identity = _identity(request, embedding)
    store = IndexStore(*setting)
    row = store.read(identity['key'])
    if row and row['status'] == 'ready':
        try:
            chunks, vectors = _validate_artifact(row, request)
        except (KeyError, TypeError, ValueError, OverflowError, rag.RAGError):
            row = store.corrupt(row)
        else:
            return _response(request, identity, row, reused=True), {'chunks': chunks, 'vectors': vectors}
    return _response(request, identity, row), None


def index_status(request):
    try:
        result, _ = lookup(request)
        return _response(request, status='disabled') if result['status'] == 'disabled' else result
    except (sqlite3.Error, OSError):
        return _response(request, status='unavailable', error='Persistent index storage is unavailable; no provider request was sent')
    except rag.RAGError as exc:
        return _response(request, status=exc.status, error=str(exc))


def build_index(request, *, budget_seconds=None):
    started = time.monotonic()
    usage = rag._new_usage()
    row = store = identity = None
    try:
        check_cancelled()
        store, identity, embedding = _context(request, started)
        if store is None:
            return _response(request, status='disabled')
        if budget_seconds is not None and (not math.isfinite(budget_seconds) or not 0 < budget_seconds <= 180):
            raise rag.RAGError('Index budget must be finite and in (0, 180] seconds', 'unavailable')
        seconds = min(embedding['total_seconds'] if embedding else 80, budget_seconds or 180)
        deadline = started + seconds
        if embedding:
            embedding['deadline'] = min(embedding['deadline'], deadline)
        row, claimed = store.claim(identity, seconds + 5)
        if row is None:
            return _response(request, identity, status='capacity_exceeded', error='Persistent index identity capacity reached; no existing identities were evicted')
        if not claimed:
            result, _ = lookup(request, embedding=embedding)
            return result
        chunks = rag.chunk_documents(request.documents)
        vectors = rag.document_vectors(chunks, usage, embedding) if embedding and chunks else ([] if embedding else None)
        check_cancelled()
        if time.monotonic() >= deadline:
            usage['budget_exhausted'] = True
            raise rag.RAGError('Index build deadline exhausted; no artifact was published')
        row = store.finish(row, 'ready', usage, chunks=chunks, vectors=vectors)
        return _response(request, identity, row, usage=usage)
    except ExecutionCancelled:
        status, error = 'cancelled', 'Index build cancelled at a safe boundary; no artifact was published'
    except rag.RAGError as exc:
        status, error = 'failed' if row else exc.status, str(exc)
    except (sqlite3.Error, OSError):
        status, error = 'failed' if row else 'unavailable', 'Persistent index storage failed; no automatic retry was sent'
    if row and store and row['status'] == 'building':
        try:
            row = store.finish(row, status, usage, error=error)
        except (sqlite3.Error, OSError, rag.RAGError):
            # Durable lease expiry will close this build; do not assert it published.
            pass
    return _response(request, identity, row, status=status, usage=usage, error=error)
