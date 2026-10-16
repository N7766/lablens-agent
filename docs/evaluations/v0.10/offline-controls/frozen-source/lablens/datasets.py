"""Bounded, request-local experiment bundles; fingerprints are not signatures."""
import hashlib
import json
from pydantic import Field
from .schemas import StrictModel
from .importing import ExperimentRun


class Note(StrictModel):
    id: str = Field(pattern=r'^note:[a-zA-Z0-9_-]+$')
    title: str = Field(min_length=1, max_length=300)
    text: str = Field(min_length=1, max_length=6000)
    source: str = Field(default='', max_length=1000)


class DatasetBundle(StrictModel):
    runs: list[ExperimentRun] = Field(min_length=1, max_length=100)
    notes: list[Note] = Field(default_factory=list, max_length=20)

    def normalized(self):
        result = self.model_dump()
        for field in ('runs', 'notes'):
            ids = [row['id'] for row in result[field]]
            if len(ids) != len(set(ids)):
                raise ValueError(f'Duplicate {field} IDs')
            result[field].sort(key=lambda row: row['id'])
        return result


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def snapshot_id(runs, notes):
    return 'snapshot:' + digest({'runs': sorted(runs, key=lambda r: r['id']), 'notes': sorted(notes, key=lambda n: n['id'])})
