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
    queries: list[str] = []

    class Conn:
        def execute(self, query, *_args, **_kwargs):
            queries.append(str(query))
            class R:
                def fetchone(self):
                    return {'id': 'k1', 'label': 'ops'}
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

    created = pilot_module.create_workspace_api_key({'label': 'ops', 'scopes': ['alerts:read']}, _request())
    listed = pilot_module._serialize_workspace_api_key_row(
        {'id': 'k1', 'label': 'ops', 'created_by_user_id': 'u1', 'secret_prefix': 'decoda_wk_x', 'scopes': ['alerts:read'], 'created_at': None, 'last_used_at': None, 'revoked_at': None}
    )

    assert created['secret'].startswith('decoda_wk_')
    assert 'secret_hash' not in created['api_key']
    assert created['api_key']['scopes'] == ['alerts:read']
    assert 'secret' not in listed
    assert any('INSERT INTO api_keys' in query for query in queries)


def test_workspace_api_keys_rbac(pilot_module, monkeypatch):
    class Conn:
        def execute(self, *_args, **_kwargs):
            class R:
                def fetchall(self):
                    return []
            return R()

    @contextmanager
    def fake_pg():
        yield Conn()

    monkeypatch.setattr(pilot_module, 'pg_connection', fake_pg)
    monkeypatch.setattr(pilot_module, 'authenticate_with_connection', lambda _c, _r: {'id': 'viewer-1'})
    monkeypatch.setattr(pilot_module, 'resolve_workspace', lambda *_: {'workspace_id': 'w1', 'role': 'viewer'})

    assert pilot_module.list_workspace_api_keys(_request()) == {'items': []}

    def deny(*_):
        raise HTTPException(status_code=403, detail='Owner or admin role is required for this action.')

    monkeypatch.setattr(pilot_module, '_require_workspace_admin', deny)
    with pytest.raises(HTTPException) as exc:
        pilot_module.create_workspace_api_key({'label': 'ops'}, _request())
    assert exc.value.status_code == 403


def test_revoked_keys_rejected(pilot_module):
    class RevokedConn:
        def execute(self, *_args, **_kwargs):
            class R:
                def fetchone(self):
                    return {'id': 'k1', 'workspace_id': 'w1', 'label': 'ops', 'scopes': [], 'revoked_at': '2025-01-01T00:00:00Z'}
            return R()

    with pytest.raises(HTTPException) as exc:
        pilot_module.validate_workspace_api_key_secret(connection=RevokedConn(), raw_secret='decoda_wk_test')
    assert exc.value.status_code == 401


def test_api_key_audit_trail_records(pilot_module, monkeypatch):
    actions: list[str] = []

    class Conn:
        def execute(self, query, *_args, **_kwargs):
            q = str(query)
            if 'SELECT id, label FROM api_keys' in q:
                class R:
                    def fetchone(self):
                        return {'id': 'k1', 'label': 'ops'}
                return R()
            if 'SELECT id FROM api_keys' in q:
                class R:
                    def fetchone(self):
                        return {'id': 'k1'}
                return R()
            class R:
                def fetchone(self):
                    return None
            return R()
        def commit(self):
            return None

    @contextmanager
    def fake_pg():
        yield Conn()

    monkeypatch.setattr(pilot_module, 'pg_connection', fake_pg)
    monkeypatch.setattr(pilot_module, '_require_workspace_admin', lambda c, r: ({'id': 'u1'}, {'workspace_id': 'w1'}))
    monkeypatch.setattr(pilot_module, 'log_audit', lambda _c, *, action, **_k: actions.append(action))
    monkeypatch.setattr(pilot_module, 'write_action_history', lambda *a, **k: None)

    pilot_module.create_workspace_api_key({'label': 'ops'}, _request())
    pilot_module.rotate_workspace_api_key('k1', _request())
    pilot_module.revoke_workspace_api_key('k1', _request())

    assert actions == ['workspace.api_key.create', 'workspace.api_key.rotate', 'workspace.api_key.revoke']
