"""Real sockets verify a timing diagnosis, not provider capacity or answer quality."""
import importlib.util
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts/diagnose_http_timeout_round16.py'
spec = importlib.util.spec_from_file_location('timeout_probe_round16', SCRIPT)
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)


@pytest.fixture(scope='module')
def results():
    return probe.run_probes()


def test_real_loopback_diagnosis_reproduces_budget_and_cancellation_limits(results):
    assert probe.validate(results)['status'] == 'reproduced'


def test_probe_events_do_not_expose_payload_headers_or_credentials(results):
    import json
    text = json.dumps(results)
    assert 'local-fixture-placeholder' not in text
    assert 'round16-fixed-synthetic-service-identity-only' not in text
    assert 'Authorization' not in text
    assert 'Bearer ' not in text
    assert 'messages' not in text


def test_headers_and_body_observations_are_distinct_and_ordered(results):
    row = next(row for row in results if row['kind'] == 'stream_observer' and row['scenario'] == 'body_stall')
    client = [event for event in row['events'] if event['actor'] == 'client']
    assert [event['event'] for event in client] == ['request_started', 'headers_observed',
                                                 'first_body_chunk_observed', 'request_finished']
    assert client[1]['elapsed_ms'] <= client[2]['elapsed_ms'] <= client[3]['elapsed_ms']


def test_script_refuses_to_overwrite_existing_evidence(tmp_path):
    with pytest.raises(FileExistsError):
        probe.main(['--output', str(tmp_path)])
