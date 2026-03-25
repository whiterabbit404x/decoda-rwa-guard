from __future__ import annotations

import importlib.util
from contextlib import contextmanager
from datetime import timedelta
from pathlib import Path

import pytest
from fastapi import HTTPException, Request

PILOT_PATH = Path(__file__).resolve().parents[1] / 'app' / 'pilot.py'


@pytest.fixture(scope='module')
def pilot_module():
    spec = importlib.util.spec_from_file_location('pilot_self_serve_foundation', PILOT_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def _request() -> Request:
    return Request({'type': 'http', 'headers': []})


def test_verify_email_token_invalid_returns_400(pilot_module, monkeypatch: pytest.MonkeyPatch):
    class _Result:
        def fetchone(self):
            return None

    class _Connection:
        def execute(self, statement, params=None):
            return _Result()

    @contextmanager
    def fake_pg():
        yield _Connection()

    monkeypatch.setattr(pilot_module, 'require_live_mode', lambda: None)
    monkeypatch.setattr(pilot_module, 'pg_connection', fake_pg)
    monkeypatch.setattr(pilot_module, 'ensure_pilot_schema', lambda connection: None)

    with pytest.raises(HTTPException) as exc:
        pilot_module.verify_email_token({'token': 'bad'})
    assert exc.value.status_code == 400


def test_request_password_reset_non_enumerating(pilot_module, monkeypatch: pytest.MonkeyPatch):
    class _Result:
        def fetchone(self):
            return None

    class _Connection:
        def execute(self, statement, params=None):
            return _Result()

        def commit(self):
            return None

    @contextmanager
    def fake_pg():
        yield _Connection()

    monkeypatch.setattr(pilot_module, 'require_live_mode', lambda: None)
    monkeypatch.setattr(pilot_module, 'pg_connection', fake_pg)
    monkeypatch.setattr(pilot_module, 'ensure_pilot_schema', lambda connection: None)

    payload = pilot_module.request_password_reset({'email': 'nobody@example.com'}, _request())
    assert payload['accepted'] is True


def test_reset_password_expired_token_returns_400(pilot_module, monkeypatch: pytest.MonkeyPatch):
    class _Result:
        def fetchone(self):
            return {'id': 't1', 'user_id': 'u1', 'expires_at': pilot_module.utc_now() - timedelta(minutes=5), 'consumed_at': None}

    class _Connection:
        def execute(self, statement, params=None):
            return _Result()

    @contextmanager
    def fake_pg():
        yield _Connection()

    monkeypatch.setattr(pilot_module, 'require_live_mode', lambda: None)
    monkeypatch.setattr(pilot_module, 'pg_connection', fake_pg)
    monkeypatch.setattr(pilot_module, 'ensure_pilot_schema', lambda connection: None)

    with pytest.raises(HTTPException) as exc:
        pilot_module.reset_password({'token': 'expired', 'password': 'StrongPass1234'})
    assert exc.value.status_code == 400
