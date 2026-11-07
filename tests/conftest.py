"""Legacy offline cases run in explicit loopback development mode.

Production defaults remain authenticated. Security cases override the environment
and can supply any explicit client address to exercise the actual network guard.
"""
import functools

from fastapi.testclient import TestClient
import pytest


@pytest.fixture(autouse=True)
def explicit_offline_worker_mode(monkeypatch):
    monkeypatch.setenv('LABLENS_AUTH_ENABLED', 'false')
    original = TestClient.__init__

    @functools.wraps(original)
    def loopback_client(self, *args, **kwargs):
        kwargs.setdefault('client', ('127.0.0.1', 50000))
        original(self, *args, **kwargs)

    monkeypatch.setattr(TestClient, '__init__', loopback_client)
