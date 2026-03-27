from __future__ import annotations

import importlib.util
from contextlib import contextmanager
from pathlib import Path

import pytest
from fastapi import HTTPException, Request

PILOT_PATH = Path(__file__).resolve().parents[1] / 'app' / 'pilot.py'


@pytest.fixture(scope='module')
def pilot_module():
    spec = importlib.util.spec_from_file_location('pilot_rbac', PILOT_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError('Unable to load pilot.py for RBAC tests.')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _request() -> Request:
    return Request({'type': 'http', 'headers': []})


def test_create_workspace_invitation_blocks_non_admin_role(pilot_module, monkeypatch: pytest.MonkeyPatch) -> None:
    @contextmanager
    def fake_pg():
        yield object()

    monkeypatch.setattr(pilot_module, 'require_live_mode', lambda: None)
    monkeypatch.setattr(pilot_module, 'pg_connection', fake_pg)
    monkeypatch.setattr(pilot_module, 'ensure_pilot_schema', lambda connection: None)
    monkeypatch.setattr(
        pilot_module,
        '_require_workspace_admin',
        lambda connection, request: (_ for _ in ()).throw(HTTPException(status_code=403, detail='Owner or admin role is required for this action.')),
    )

    with pytest.raises(HTTPException) as exc_info:
        pilot_module.create_workspace_invitation({'email': 'viewer@example.com', 'role': 'viewer'}, _request())

    assert exc_info.value.status_code == 403


def test_create_checkout_session_blocks_non_admin_role(pilot_module, monkeypatch: pytest.MonkeyPatch) -> None:
    @contextmanager
    def fake_pg():
        yield object()

    monkeypatch.setattr(pilot_module, 'require_live_mode', lambda: None)
    monkeypatch.setattr(pilot_module, 'pg_connection', fake_pg)
    monkeypatch.setattr(pilot_module, 'ensure_pilot_schema', lambda connection: None)
    monkeypatch.setattr(pilot_module.os, 'getenv', lambda key, default='': 'configured' if key == 'STRIPE_SECRET_KEY' else default)
    monkeypatch.setattr(
        pilot_module,
        '_require_workspace_admin',
        lambda connection, request: (_ for _ in ()).throw(HTTPException(status_code=403, detail='Owner or admin role is required for this action.')),
    )

    with pytest.raises(HTTPException) as exc_info:
        pilot_module.create_checkout_session({'plan_key': 'starter'}, _request())

    assert exc_info.value.status_code == 403


def test_create_workspace_invitation_enforces_seat_limit(pilot_module, monkeypatch: pytest.MonkeyPatch) -> None:
    class _Result:
        def __init__(self, row=None):
            self._row = row

        def fetchone(self):
            return self._row

    class _Connection:
        def execute(self, statement, params=None):
            normalized = ' '.join(str(statement).split())
            if 'SELECT COUNT(*) AS count FROM workspace_members' in normalized:
                return _Result({'count': 5})
            return _Result(None)

    @contextmanager
    def fake_pg():
        yield _Connection()

    monkeypatch.setattr(pilot_module, 'require_live_mode', lambda: None)
    monkeypatch.setattr(pilot_module, 'pg_connection', fake_pg)
    monkeypatch.setattr(pilot_module, 'ensure_pilot_schema', lambda connection: None)
    monkeypatch.setattr(pilot_module, '_workspace_plan', lambda connection, workspace_id: {'max_members': 5})
    monkeypatch.setattr(
        pilot_module,
        '_require_workspace_admin',
        lambda connection, request: ({'id': 'user-1'}, {'workspace_id': 'ws-1', 'workspace': {'id': 'ws-1'}}),
    )

    with pytest.raises(HTTPException) as exc_info:
        pilot_module.create_workspace_invitation({'email': 'new.user@example.com', 'role': 'viewer'}, _request())

    assert exc_info.value.status_code == 402
