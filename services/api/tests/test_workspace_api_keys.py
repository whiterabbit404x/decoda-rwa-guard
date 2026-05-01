from __future__ import annotations

import importlib.util
from contextlib import contextmanager
from pathlib import Path

import pytest
from fastapi import HTTPException, Request

PILOT_PATH = Path(__file__).resolve().parents[1] / 'app' / 'pilot.py'


@pytest.fixture(scope='module')
def pilot_module():
    spec = importlib.util.spec_from_file_location('pilot_workspace_api_keys', PILOT_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def _request() -> Request:
    return Request({'type': 'http', 'headers': []})


def test_create_workspace_api_key_one_time_secret(pilot_module, monkeypatch):
    class Conn:
        def execute(self, *args, **kwargs):
            class R:
                def fetchone(self): return {'id': 'k1', 'label': 'ops'}
            return R()
        def commit(self):
            return None

    @contextmanager
    def fake_pg():
        yield Conn()

    monkeypatch.setattr(pilot_module, 'pg_connection', fake_pg)
    monkeypatch.setattr(pilot_module, '_require_workspace_admin', lambda c, r: ({'id': 'u1'}, {'workspace_id': 'w1'}))
    monkeypatch.setattr(pilot_module, 'log_audit', lambda *a, **k: None)
    monkeypatch.setattr(pilot_module, 'write_action_history', lambda *a, **k: None)

    result = pilot_module.create_workspace_api_key({'label': 'ops'}, _request())
    assert result['secret'].startswith('decoda_wk_')
    assert 'secret_hash' not in result['api_key']


def test_workspace_api_keys_forbid_non_admin(pilot_module, monkeypatch):
    class Conn:
        pass

    @contextmanager
    def fake_pg():
        yield Conn()

    monkeypatch.setattr(pilot_module, 'pg_connection', fake_pg)
    def deny(*_):
        raise HTTPException(status_code=403, detail='Owner or admin role is required for this action.')
    monkeypatch.setattr(pilot_module, '_require_workspace_admin', deny)

    with pytest.raises(HTTPException) as exc:
        pilot_module.list_workspace_api_keys(_request())
    assert exc.value.status_code == 403
