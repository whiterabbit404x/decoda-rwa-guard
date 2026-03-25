from __future__ import annotations

import importlib.util
from contextlib import contextmanager
from pathlib import Path

import pytest
from fastapi import HTTPException, Request

PILOT_PATH = Path(__file__).resolve().parents[1] / 'app' / 'pilot.py'


@pytest.fixture(scope='module')
def pilot_module():
    spec = importlib.util.spec_from_file_location('pilot_enterprise', PILOT_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError('Unable to load pilot.py for enterprise tests.')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _request(headers: list[tuple[bytes, bytes]] | None = None) -> Request:
    return Request({'type': 'http', 'headers': headers or []})


def test_resend_verification_returns_sent_for_missing_user(pilot_module, monkeypatch: pytest.MonkeyPatch) -> None:
    class _Result:
        def fetchone(self):
            return None

    class _Connection:
        def execute(self, *_args, **_kwargs):
            return _Result()

    @contextmanager
    def fake_pg():
        yield _Connection()

    monkeypatch.setattr(pilot_module, 'require_live_mode', lambda: None)
    monkeypatch.setattr(pilot_module, 'pg_connection', fake_pg)
    monkeypatch.setattr(pilot_module, 'ensure_pilot_schema', lambda _connection: None)

    payload = pilot_module.resend_verification_email({'email': 'nobody@example.com'}, _request())
    assert payload['sent'] is True


def test_verify_email_rejects_missing_token(pilot_module, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(pilot_module, 'require_live_mode', lambda: None)
    with pytest.raises(HTTPException) as exc_info:
        pilot_module.verify_email_token({'token': ''}, _request())
    assert exc_info.value.status_code == 400


def test_workspace_invite_requires_admin_role(pilot_module, monkeypatch: pytest.MonkeyPatch) -> None:
    class _Connection:
        pass

    @contextmanager
    def fake_pg():
        yield _Connection()

    monkeypatch.setattr(pilot_module, 'require_live_mode', lambda: None)
    monkeypatch.setattr(pilot_module, 'pg_connection', fake_pg)
    monkeypatch.setattr(pilot_module, 'ensure_pilot_schema', lambda _connection: None)
    monkeypatch.setattr(
        pilot_module,
        'authenticate_with_connection',
        lambda _connection, _request: {'id': 'user-1', 'email': 'viewer@example.com'},
    )
    monkeypatch.setattr(
        pilot_module,
        'resolve_workspace',
        lambda _connection, _user_id, _workspace_id: {'workspace_id': 'ws-1', 'role': 'workspace_viewer', 'workspace': {'id': 'ws-1'}},
    )

    with pytest.raises(HTTPException) as exc_info:
        pilot_module.create_workspace_invitation({'email': 'new@example.com', 'role': 'workspace_member'}, _request())
    assert exc_info.value.status_code == 403


def test_reset_password_requires_min_length(pilot_module, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(pilot_module, 'require_live_mode', lambda: None)
    with pytest.raises(HTTPException) as exc_info:
        pilot_module.reset_password_with_token({'token': 'abc', 'password': 'short'}, _request())
    assert exc_info.value.status_code == 400
