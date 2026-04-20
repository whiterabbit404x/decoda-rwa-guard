from __future__ import annotations

import importlib.util
import json
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
import uuid

import pytest
from fastapi import HTTPException, Request

PILOT_PATH = Path(__file__).resolve().parents[1] / 'app' / 'pilot.py'


@pytest.fixture(scope='module')
def pilot_module():
    spec = importlib.util.spec_from_file_location('pilot_self_serve', PILOT_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError('Unable to load pilot.py for self-serve auth tests.')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _request() -> Request:
    return Request({'type': 'http', 'headers': []})


def test_signup_success_returns_verification_required(pilot_module, monkeypatch: pytest.MonkeyPatch) -> None:
    class _Result:
        def __init__(self, row=None):
            self._row = row

        def fetchone(self):
            return self._row

    class _Connection:
        def execute(self, statement, params=None):
            if 'SELECT id FROM users WHERE email' in statement:
                return _Result(None)
            if 'SELECT 1 FROM workspaces WHERE slug' in statement:
                return _Result(None)
            return _Result(None)

        def commit(self):
            return None

    @contextmanager
    def fake_pg():
        yield _Connection()

    monkeypatch.setattr(pilot_module, 'require_live_mode', lambda: None)
    monkeypatch.setattr(pilot_module, 'pg_connection', fake_pg)
    monkeypatch.setattr(pilot_module, 'ensure_pilot_schema', lambda connection: None)
    monkeypatch.setattr(pilot_module, 'hash_password', lambda password: 'hashed-value')
    monkeypatch.setattr(pilot_module, 'build_user_response', lambda connection, user_id: {'id': user_id, 'current_workspace': {'id': 'ws-1'}})
    monkeypatch.setattr(pilot_module, 'log_audit', lambda *args, **kwargs: None)
    monkeypatch.setattr(pilot_module, '_create_user_token', lambda *args, **kwargs: 'verify-token')

    response = pilot_module.signup_user(
        {
            'email': 'team@example.com',
            'password': 'StrongPass1234',
            'full_name': 'Team Owner',
            'workspace_name': 'Treasury Ops',
        },
        _request(),
    )

    assert response['verification_required'] is True
    assert response['user']['current_workspace']['id'] == 'ws-1'


def test_signup_duplicate_returns_conflict(pilot_module, monkeypatch: pytest.MonkeyPatch) -> None:
    class _Result:
        def __init__(self, row=None):
            self._row = row

        def fetchone(self):
            return self._row

    class _Connection:
        def execute(self, statement, params=None):
            if 'SELECT id FROM users WHERE email' in statement:
                return _Result({'id': 'existing-user'})
            return _Result(None)

    @contextmanager
    def fake_pg():
        yield _Connection()

    monkeypatch.setattr(pilot_module, 'require_live_mode', lambda: None)
    monkeypatch.setattr(pilot_module, 'pg_connection', fake_pg)
    monkeypatch.setattr(pilot_module, 'ensure_pilot_schema', lambda connection: None)

    with pytest.raises(HTTPException) as exc_info:
        pilot_module.signup_user({'email': 'team@example.com', 'password': 'StrongPass1234'}, _request())

    assert exc_info.value.status_code == 409


def test_signin_success_returns_hydrated_user(pilot_module, monkeypatch: pytest.MonkeyPatch) -> None:
    class _Result:
        def __init__(self, row=None):
            self._row = row

        def fetchone(self):
            return self._row

    class _Connection:
        def execute(self, statement, params=None):
            if 'SELECT id, password_hash, email_verified_at, session_version, mfa_totp_secret, mfa_enabled_at FROM users WHERE email' in statement:
                return _Result({'id': 'user-1', 'password_hash': 'stored', 'email_verified_at': datetime(2026, 3, 1, tzinfo=timezone.utc), 'session_version': 1, 'mfa_totp_secret': None, 'mfa_enabled_at': None})
            return _Result(None)

        def commit(self):
            return None

    @contextmanager
    def fake_pg():
        yield _Connection()

    monkeypatch.setattr(pilot_module, 'require_live_mode', lambda: None)
    monkeypatch.setattr(pilot_module, 'pg_connection', fake_pg)
    monkeypatch.setattr(pilot_module, 'ensure_pilot_schema', lambda connection: None)
    monkeypatch.setattr(pilot_module, 'verify_password', lambda password, encoded: True)
    monkeypatch.setattr(pilot_module, 'build_user_response', lambda connection, user_id: {'id': user_id, 'current_workspace': None})
    monkeypatch.setattr(pilot_module, 'log_audit', lambda *args, **kwargs: None)
    monkeypatch.setattr(pilot_module, 'create_access_token', lambda user_id, session_version=1: f'token-{user_id}-{session_version}')
    monkeypatch.setattr(pilot_module, '_store_session', lambda *args, **kwargs: None)

    response = pilot_module.signin_user({'email': 'team@example.com', 'password': 'StrongPass1234'}, _request())

    assert response['token_type'] == 'bearer'
    assert response['user']['id'] == 'user-1'
    assert response['user']['current_workspace'] is None


def test_signin_invalid_credentials_returns_401(pilot_module, monkeypatch: pytest.MonkeyPatch) -> None:
    class _Result:
        def __init__(self, row=None):
            self._row = row

        def fetchone(self):
            return self._row

    class _Connection:
        def execute(self, statement, params=None):
            if 'SELECT id, password_hash, email_verified_at, session_version, mfa_totp_secret, mfa_enabled_at FROM users WHERE email' in statement:
                return _Result({'id': 'user-1', 'password_hash': 'stored', 'email_verified_at': datetime(2026, 3, 1, tzinfo=timezone.utc), 'session_version': 1, 'mfa_totp_secret': None, 'mfa_enabled_at': None})
            return _Result(None)

    @contextmanager
    def fake_pg():
        yield _Connection()

    monkeypatch.setattr(pilot_module, 'require_live_mode', lambda: None)
    monkeypatch.setattr(pilot_module, 'pg_connection', fake_pg)
    monkeypatch.setattr(pilot_module, 'ensure_pilot_schema', lambda connection: None)
    monkeypatch.setattr(pilot_module, 'verify_password', lambda password, encoded: False)

    with pytest.raises(HTTPException) as exc_info:
        pilot_module.signin_user({'email': 'team@example.com', 'password': 'StrongPass1234'}, _request())

    assert exc_info.value.status_code == 401


def test_signin_db_quota_exceeded_returns_graceful_503_and_machine_code(
    pilot_module, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    @contextmanager
    def fake_pg():
        raise RuntimeError('Neon error: exceeded the compute time quota for this project')
        yield

    monkeypatch.setattr(pilot_module, 'require_live_mode', lambda: None)
    monkeypatch.setattr(pilot_module, 'pg_connection', fake_pg)
    monkeypatch.setattr(pilot_module, 'database_url', lambda: 'postgres://user:pass@ep-decoda-neon.us-east-1.aws.neon.tech/db')

    with pytest.raises(HTTPException) as exc_info:
        with caplog.at_level('WARNING'):
            pilot_module.signin_user({'email': 'team@example.com', 'password': 'StrongPass1234'}, _request())

    assert exc_info.value.status_code == 503
    assert exc_info.value.detail == 'Authentication is temporarily unavailable. Please retry in a moment.'
    assert exc_info.value.headers['X-Decoda-Error-Code'] == 'AUTH_DB_QUOTA_EXCEEDED'
    assert exc_info.value.headers['X-Decoda-DB-Classification'] == 'quota_exceeded'
    assert any('event=auth_db_degraded classification=quota_exceeded' in record.message for record in caplog.records)


def test_enforce_auth_rate_limit_redis_failure_logs_condensed_warning(
    pilot_module, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    class _RedisClient:
        def incr(self, *_args, **_kwargs):
            raise RuntimeError('dns lookup failed')

    class _RedisFactory:
        @staticmethod
        def from_url(*_args, **_kwargs):
            return _RedisClient()

    class _RedisModule:
        Redis = _RedisFactory

    monkeypatch.setenv('REDIS_URL', 'redis://redis.invalid:6379/0')
    monkeypatch.setattr(pilot_module.importlib, 'import_module', lambda _: _RedisModule())
    monkeypatch.setattr(pilot_module, '_redis_rate_limiter', None)

    with caplog.at_level('WARNING'):
        pilot_module.enforce_auth_rate_limit(_request(), 'signin')

    fallback_logs = [record for record in caplog.records if 'redis rate limiter unavailable; falling back to in-memory limiter error=' in record.message]
    assert len(fallback_logs) == 1


def test_signin_db_network_unreachable_returns_graceful_503_without_credential_failure(
    pilot_module, monkeypatch: pytest.MonkeyPatch
) -> None:
    @contextmanager
    def fake_pg():
        raise RuntimeError('connect failed: Network is unreachable')
        yield

    monkeypatch.setattr(pilot_module, 'require_live_mode', lambda: None)
    monkeypatch.setattr(pilot_module, 'pg_connection', fake_pg)
    monkeypatch.setattr(pilot_module, 'database_url', lambda: 'postgres://user:pass@db.internal.local:5432/app')

    with pytest.raises(HTTPException) as exc_info:
        pilot_module.signin_user({'email': 'team@example.com', 'password': 'StrongPass1234'}, _request())

    assert exc_info.value.status_code == 503
    assert exc_info.value.detail == 'Authentication is temporarily unavailable. Please retry in a moment.'
    assert exc_info.value.headers['X-Decoda-Error-Code'] == 'AUTH_BACKEND_UNAVAILABLE'
    assert exc_info.value.headers['X-Decoda-DB-Classification'] == 'network_unreachable'


def test_json_safe_value_serializes_uuid_and_datetime(pilot_module) -> None:
    payload = {
        'id': uuid.uuid4(),
        'created_at': datetime(2026, 3, 24, 0, 0, tzinfo=timezone.utc),
    }

    serialized = pilot_module._json_safe_value(payload)
    json.loads(json.dumps(serialized))
    assert isinstance(serialized['id'], str)
    assert serialized['created_at'].endswith('+00:00')


def test_build_user_response_backfills_null_current_workspace_from_membership(
    pilot_module, monkeypatch: pytest.MonkeyPatch
) -> None:
    statements: list[tuple[str, object]] = []

    class _Result:
        def __init__(self, rows=None):
            self._rows = rows or []

        def fetchone(self):
            return self._rows[0] if self._rows else None

        def fetchall(self):
            return self._rows

    class _Connection:
        def execute(self, statement, params=None):
            normalized = ' '.join(str(statement).split())
            statements.append((normalized, params))
            if 'FROM users' in normalized:
                return _Result([{
                    'id': 'user-1',
                    'email': 'team@example.com',
                    'full_name': 'Team Owner',
                    'current_workspace_id': None,
                    'created_at': datetime(2026, 3, 20, tzinfo=timezone.utc),
                    'updated_at': datetime(2026, 3, 20, tzinfo=timezone.utc),
                    'last_sign_in_at': None,
                    'email_verified_at': datetime(2026, 3, 20, tzinfo=timezone.utc),
                    'mfa_enabled_at': None,
                }])
            if 'FROM workspace_members' in normalized:
                return _Result([{
                    'workspace_id': 'ws-1',
                    'role': 'owner',
                    'created_at': datetime(2026, 3, 20, tzinfo=timezone.utc),
                    'name': 'Treasury Ops',
                    'slug': 'treasury-ops',
                }])
            return _Result([])

    payload = pilot_module.build_user_response(_Connection(), 'user-1')

    assert payload['current_workspace_id'] == 'ws-1'
    assert payload['current_workspace']['id'] == 'ws-1'
    assert any('UPDATE users SET current_workspace_id' in statement for statement, _ in statements)


def test_build_history_response_returns_json_safe_workspace_records(
    pilot_module, monkeypatch: pytest.MonkeyPatch
) -> None:
    class _Result:
        def __init__(self, rows=None):
            self._rows = rows or []

        def fetchone(self):
            return self._rows[0] if self._rows else None

        def fetchall(self):
            return self._rows

    class _Connection:
        def execute(self, statement, params=None):
            normalized = ' '.join(str(statement).split())
            if 'FROM analysis_runs' in normalized:
                return _Result([{
                    'id': uuid.uuid4(),
                    'analysis_type': 'threat_transaction',
                    'service_name': 'threat-engine',
                    'status': 'completed',
                    'title': 'Threat transaction analysis',
                    'source': 'live',
                    'summary': 'Synthetic summary',
                    'request_payload': {'wallet': '0xabc'},
                    'response_payload': {'recommended_action': 'review'},
                    'created_at': datetime(2026, 3, 24, tzinfo=timezone.utc),
                }])
            if 'FROM alerts' in normalized or 'FROM governance_actions' in normalized or 'FROM incidents' in normalized:
                return _Result([])
            if 'FROM audit_logs' in normalized:
                return _Result([{
                    'id': uuid.uuid4(),
                    'action': 'analysis.run',
                    'entity_type': 'analysis_run',
                    'entity_id': uuid.uuid4(),
                    'ip_address': None,
                    'metadata': {'analysis_type': 'threat_transaction'},
                    'created_at': datetime(2026, 3, 24, tzinfo=timezone.utc),
                }])
            if 'SELECT (SELECT COUNT(*) FROM analysis_runs' in normalized:
                return _Result([{
                    'analysis_runs': 1,
                    'alerts': 0,
                    'governance_actions': 0,
                    'incidents': 0,
                    'audit_logs': 1,
                }])
            return _Result([])

    @contextmanager
    def fake_pg():
        yield _Connection()

    monkeypatch.setattr(pilot_module, 'require_live_mode', lambda: None)
    monkeypatch.setattr(pilot_module, 'pg_connection', fake_pg)
    monkeypatch.setattr(
        pilot_module,
        'authenticate_with_connection',
        lambda connection, request: {'id': 'user-1'},
    )
    monkeypatch.setattr(
        pilot_module,
        'resolve_workspace',
        lambda connection, user_id, requested_workspace_id=None: {
            'workspace_id': 'ws-1',
            'role': 'workspace_owner',
            'workspace': {'id': 'ws-1', 'name': 'Treasury Ops', 'slug': 'treasury-ops'},
        },
    )

    payload = pilot_module.build_history_response(_request(), limit=25)

    assert payload['workspace']['id'] == 'ws-1'
    assert payload['analysis_runs'][0]['id']
    assert payload['analysis_runs'][0]['created_at'].endswith('+00:00')
    assert isinstance(payload['counts'], dict)
