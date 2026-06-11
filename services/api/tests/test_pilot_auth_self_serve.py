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


def _request(headers: dict[str, str] | None = None) -> Request:
    encoded_headers = []
    if headers:
        encoded_headers = [(key.lower().encode('latin-1'), value.encode('latin-1')) for key, value in headers.items()]
    return Request({'type': 'http', 'headers': encoded_headers})


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


def test_signin_db_quota_exceeded_returns_graceful_503_and_throttles_degraded_warning_emission(
    pilot_module, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    @contextmanager
    def fake_pg():
        raise RuntimeError('Neon error: exceeded the compute time quota for this project')
        yield

    monkeypatch.setattr(pilot_module, 'require_live_mode', lambda: None)
    monkeypatch.setattr(pilot_module, 'pg_connection', fake_pg)
    monkeypatch.setattr(pilot_module, 'database_url', lambda: 'postgres://user:pass@ep-decoda-neon.us-east-1.aws.neon.tech/db')
    monkeypatch.setattr(pilot_module, '_auth_db_degraded_last_emitted', {})
    now = {'value': 10_000.0}
    monkeypatch.setattr(pilot_module, 'monotonic', lambda: now['value'])

    responses: list[HTTPException] = []
    with caplog.at_level('INFO'):
        for current, request_id in ((10_000.0, 'req-first-window'), (10_010.0, 'req-second-window')):
            now['value'] = current
            with pytest.raises(HTTPException) as exc_info:
                pilot_module.signin_user(
                    {'email': 'team@example.com', 'password': 'StrongPass1234'},
                    _request({'x-request-id': request_id}),
                )
            responses.append(exc_info.value)

    assert len(responses) == 2
    assert all(response.status_code == 503 for response in responses)
    assert all(response.detail == 'Authentication is temporarily unavailable. Please retry in a moment.' for response in responses)
    assert all(response.headers['X-Decoda-Error-Code'] == 'AUTH_DB_QUOTA_EXCEEDED' for response in responses)
    assert all(response.headers['X-Decoda-DB-Classification'] == 'quota_exceeded' for response in responses)
    assert responses[0].headers['X-Decoda-Correlation-Id'] == 'req-first-window'
    assert responses[1].headers['X-Decoda-Correlation-Id'] == 'req-second-window'
    degraded_records = [record for record in caplog.records if 'event=auth_db_degraded classification=quota_exceeded' in record.message]
    assert len(degraded_records) == 1
    assert degraded_records[0].levelname == 'INFO'
    assert 'reason=' in degraded_records[0].message
    assert 'correlation_id=req-first-window' in degraded_records[0].message
    assert 'correlation_id=req-second-window' not in degraded_records[0].message


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


def test_signin_recovers_after_temporary_db_outage_without_stale_degraded_state(
    pilot_module, monkeypatch: pytest.MonkeyPatch
) -> None:
    attempts = {'count': 0}

    class _Result:
        def __init__(self, row=None):
            self._row = row

        def fetchone(self):
            return self._row

    class _Connection:
        def execute(self, statement, params=None):
            if 'SELECT id, password_hash, email_verified_at, session_version, mfa_totp_secret, mfa_enabled_at FROM users WHERE email' in statement:
                return _Result(
                    {
                        'id': 'user-1',
                        'password_hash': 'stored',
                        'email_verified_at': datetime(2026, 3, 1, tzinfo=timezone.utc),
                        'session_version': 3,
                        'mfa_totp_secret': None,
                        'mfa_enabled_at': None,
                    }
                )
            return _Result(None)

        def commit(self):
            return None

    @contextmanager
    def fake_pg():
        attempts['count'] += 1
        if attempts['count'] == 1:
            raise RuntimeError('Neon error: exceeded the compute time quota for this project')
        yield _Connection()

    monkeypatch.setattr(pilot_module, 'require_live_mode', lambda: None)
    monkeypatch.setattr(pilot_module, 'pg_connection', fake_pg)
    monkeypatch.setattr(pilot_module, 'ensure_pilot_schema', lambda connection: None)
    monkeypatch.setattr(pilot_module, 'database_url', lambda: 'postgresql://pilot:pilot@ep-decoda-neon.us-east-1.aws.neon.tech:5432/app')
    monkeypatch.setattr(pilot_module, '_auth_db_degraded_last_emitted', {})
    monkeypatch.setattr(pilot_module, 'verify_password', lambda password, encoded: True)
    monkeypatch.setattr(pilot_module, 'build_user_response', lambda connection, user_id: {'id': user_id, 'current_workspace': None})
    monkeypatch.setattr(pilot_module, 'log_audit', lambda *args, **kwargs: None)
    monkeypatch.setattr(pilot_module, 'create_access_token', lambda user_id, session_version=1: f'token-{user_id}-{session_version}')
    monkeypatch.setattr(pilot_module, '_store_session', lambda *args, **kwargs: None)

    with pytest.raises(HTTPException) as exc_info:
        pilot_module.signin_user({'email': 'team@example.com', 'password': 'StrongPass1234'}, _request())

    assert exc_info.value.status_code == 503
    assert exc_info.value.headers['X-Decoda-DB-Classification'] == 'quota_exceeded'

    response = pilot_module.signin_user({'email': 'team@example.com', 'password': 'StrongPass1234'}, _request())

    assert response['token_type'] == 'bearer'
    assert response['access_token'] == 'token-user-1-3'
    assert response['user']['id'] == 'user-1'


def test_signin_db_degraded_log_uses_normalized_condensed_error_snippet(
    pilot_module, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    @contextmanager
    def fake_pg():
        raise RuntimeError(
            'connect failed: Network is unreachable.\n'
            'DETAIL: could not connect to server at "db.internal.local" (10.0.0.8), port 5432\n'
            'FATAL: timeout while opening socket for primary connection pool'
        )
        yield

    monkeypatch.setattr(pilot_module, 'require_live_mode', lambda: None)
    monkeypatch.setattr(pilot_module, 'pg_connection', fake_pg)
    monkeypatch.setattr(pilot_module, 'database_url', lambda: 'postgres://user:pass@db.internal.local:5432/app')
    monkeypatch.setattr(pilot_module, '_auth_db_degraded_last_emitted', {})
    monkeypatch.setattr(pilot_module, 'monotonic', lambda: 1000.0)

    with caplog.at_level('INFO'):
        with pytest.raises(HTTPException) as exc_info:
            pilot_module.signin_user({'email': 'team@example.com', 'password': 'StrongPass1234'}, _request())

    assert exc_info.value.status_code == 503
    degraded_records = [record.message for record in caplog.records if 'event=auth_db_degraded classification=network_unreachable' in record.message]
    assert len(degraded_records) == 1
    assert 'condensed_error=connect failed: Network is unreachable.' in degraded_records[0]
    assert 'DETAIL:' not in degraded_records[0]


def test_enforce_auth_rate_limit_redis_failure_logs_are_throttled_per_window(
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

    from services.api.app.domains import rate_limit as _rl

    monkeypatch.setenv('REDIS_URL', 'redis://redis.invalid:6379/0')
    # Rate limiting state and logic now live in the domain module; patch there.
    monkeypatch.setattr(_rl.importlib, 'import_module', lambda _: _RedisModule())
    monkeypatch.setattr(_rl, '_redis_rate_limiter', None)
    monkeypatch.setattr(_rl, '_rate_limit_state', {})
    monkeypatch.setattr(_rl, '_rate_limit_fallback_last_emitted', {})

    now = {'value': 1_000.0}
    monkeypatch.setattr(_rl, 'monotonic', lambda: now['value'])

    with caplog.at_level('WARNING'):
        for current in (1_000.0, 1_010.0, 1_020.0, 1_030.0, 1_301.0):
            now['value'] = current
            pilot_module.enforce_auth_rate_limit(_request(), 'signin')

    degraded_records = [
        record.message
        for record in caplog.records
        if 'Rate limiter fallback active: Redis unavailable. reason=dns lookup failed' in record.message
    ]
    assert len(degraded_records) == 2
    assert all(record.levelname == 'WARNING' for record in caplog.records if 'Rate limiter fallback active: Redis unavailable. reason=dns lookup failed' in record.message)


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


def test_signup_email_delivery_failure_returns_503(pilot_module, monkeypatch: pytest.MonkeyPatch) -> None:
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
    monkeypatch.setattr(pilot_module, 'log_audit', lambda *args, **kwargs: None)
    monkeypatch.setattr(pilot_module, '_create_user_token', lambda *args, **kwargs: 'verify-token')
    monkeypatch.setattr(pilot_module, '_dispatch_transactional_email', lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError('Failed to deliver email via Resend: status=403')))

    with pytest.raises(HTTPException) as exc_info:
        pilot_module.signup_user(
            {'email': 'team@example.com', 'password': 'StrongPass1234', 'full_name': 'Team Owner', 'workspace_name': 'Treasury Ops'},
            _request(),
        )

    assert exc_info.value.status_code == 503
    assert 'Email delivery' in exc_info.value.detail


def test_send_email_resend_403_logs_status_provider_domain_body(pilot_module, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    import io
    from urllib.error import HTTPError

    monkeypatch.setenv('EMAIL_PROVIDER', 'resend')
    monkeypatch.setenv('EMAIL_RESEND_API_KEY', 'test-key')
    monkeypatch.setenv('EMAIL_FROM', 'no-reply@send.decodasecurity.com')

    error_body = b'{"name":"forbidden","message":"API key is invalid","statusCode":403}'

    class _FakeHTTPError(HTTPError):
        def __init__(self):
            super().__init__('https://api.resend.com/emails', 403, 'Forbidden', {}, io.BytesIO(error_body))

        def read(self):
            return error_body

    def fake_urlopen(req, timeout=None):
        raise _FakeHTTPError()

    monkeypatch.setattr(pilot_module, 'urlopen', fake_urlopen)

    with caplog.at_level('ERROR'):
        with pytest.raises(RuntimeError, match='status=403'):
            pilot_module._send_email('target@example.com', 'Test', 'body text')

    error_records = [r for r in caplog.records if 'resend_email_failed' in r.getMessage()]
    assert error_records, 'Expected resend_email_failed log record'
    msg = error_records[0].getMessage()
    assert 'status=403' in msg
    assert 'provider=resend' in msg
    assert 'from_domain=send.decodasecurity.com' in msg
    assert 'forbidden' in msg or 'API key is invalid' in msg
    assert 'test-key' not in msg
    assert 'Bearer' not in msg


def test_send_email_resend_403_falls_back_to_fp_when_read_empty(pilot_module, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    import io
    from urllib.error import HTTPError

    monkeypatch.setenv('EMAIL_PROVIDER', 'resend')
    monkeypatch.setenv('EMAIL_RESEND_API_KEY', 'test-key')
    monkeypatch.setenv('EMAIL_FROM', 'no-reply@send.decodasecurity.com')

    fp_body = b'{"name":"forbidden","message":"from fp fallback","statusCode":403}'

    class _FakeHTTPError(HTTPError):
        def __init__(self):
            super().__init__('https://api.resend.com/emails', 403, 'Forbidden', {}, io.BytesIO(fp_body))

        def read(self):
            return b''  # empty — force fallback to exc.fp.read()

    def fake_urlopen(req, timeout=None):
        raise _FakeHTTPError()

    monkeypatch.setattr(pilot_module, 'urlopen', fake_urlopen)

    with caplog.at_level('ERROR'):
        with pytest.raises(RuntimeError, match='status=403'):
            pilot_module._send_email('target@example.com', 'Test', 'body text')

    error_records = [r for r in caplog.records if 'resend_email_failed' in r.getMessage()]
    assert error_records, 'Expected resend_email_failed log record with fp fallback body'
    assert 'from fp fallback' in error_records[0].getMessage()


def test_send_email_resend_request_includes_user_agent_and_accept(pilot_module, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv('EMAIL_PROVIDER', 'resend')
    monkeypatch.setenv('EMAIL_RESEND_API_KEY', 'test-key')
    monkeypatch.setenv('EMAIL_FROM', 'no-reply@send.decodasecurity.com')

    captured: list = []

    def fake_urlopen(req, timeout=None):
        captured.append(req)
        class _FakeResponse:
            def __enter__(self): return self
            def __exit__(self, *args): pass
        return _FakeResponse()

    monkeypatch.setattr(pilot_module, 'urlopen', fake_urlopen)
    pilot_module._send_email('target@example.com', 'Test Subject', 'body text')

    assert captured, 'urlopen was not called'
    req = captured[0]
    assert req.get_header('User-agent') == 'Decoda-RWA-Guard/1.0 (+https://rwa.decodasecurity.com)'
    assert req.get_header('Accept') == 'application/json'


def test_send_email_resend_api_key_not_logged(pilot_module, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    import io
    from urllib.error import HTTPError

    secret_key = 'secret-resend-key-must-not-appear-in-logs'
    monkeypatch.setenv('EMAIL_PROVIDER', 'resend')
    monkeypatch.setenv('EMAIL_RESEND_API_KEY', secret_key)
    monkeypatch.setenv('EMAIL_FROM', 'no-reply@send.decodasecurity.com')

    error_body = b'{"name":"forbidden","message":"missing user agent","statusCode":403}'

    class _FakeHTTPError(HTTPError):
        def __init__(self):
            super().__init__('https://api.resend.com/emails', 403, 'Forbidden', {}, io.BytesIO(error_body))
        def read(self):
            return error_body

    monkeypatch.setattr(pilot_module, 'urlopen', lambda req, timeout=None: (_ for _ in ()).throw(_FakeHTTPError()))

    with caplog.at_level('ERROR'):
        with pytest.raises(RuntimeError):
            pilot_module._send_email('target@example.com', 'Test', 'body text')

    for record in caplog.records:
        msg = record.getMessage()
        assert secret_key not in msg, f'API key leaked into log: {msg}'
        assert 'Bearer' not in msg, f'Authorization header leaked into log: {msg}'


def test_send_email_resend_403_returns_safe_503_via_signup(pilot_module, monkeypatch: pytest.MonkeyPatch) -> None:
    import io
    from contextlib import contextmanager
    from urllib.error import HTTPError
    from fastapi import HTTPException

    monkeypatch.setenv('EMAIL_PROVIDER', 'resend')
    monkeypatch.setenv('EMAIL_RESEND_API_KEY', 'test-key')
    monkeypatch.setenv('EMAIL_FROM', 'no-reply@send.decodasecurity.com')

    error_body = b'{"name":"forbidden","message":"missing user agent","statusCode":403}'

    class _FakeHTTPError(HTTPError):
        def __init__(self):
            super().__init__('https://api.resend.com/emails', 403, 'Forbidden', {}, io.BytesIO(error_body))
        def read(self):
            return error_body

    class _Result:
        def __init__(self, row=None): self._row = row
        def fetchone(self): return self._row

    class _Connection:
        def execute(self, statement, params=None): return _Result(None)
        def commit(self): return None

    @contextmanager
    def fake_pg():
        yield _Connection()

    monkeypatch.setattr(pilot_module, 'require_live_mode', lambda: None)
    monkeypatch.setattr(pilot_module, 'pg_connection', fake_pg)
    monkeypatch.setattr(pilot_module, 'ensure_pilot_schema', lambda connection: None)
    monkeypatch.setattr(pilot_module, 'hash_password', lambda password: 'hashed')
    monkeypatch.setattr(pilot_module, 'log_audit', lambda *a, **kw: None)
    monkeypatch.setattr(pilot_module, '_create_user_token', lambda *a, **kw: 'tok')
    monkeypatch.setattr(
        pilot_module, '_dispatch_transactional_email',
        lambda *a, **kw: (_ for _ in ()).throw(RuntimeError('Failed to deliver email via Resend: status=403')),
    )

    with pytest.raises(HTTPException) as exc_info:
        pilot_module.signup_user(
            {'email': 'user@example.com', 'password': 'StrongPass1', 'full_name': 'Test User', 'workspace_name': 'Test Workspace'},
            _request(),
        )

    assert exc_info.value.status_code == 503
    assert 'Email delivery' in exc_info.value.detail


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
