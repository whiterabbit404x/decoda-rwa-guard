"""Password reset must be addressed by the submitted email and authorized only by the token.

Regression cover for the Forgot Password / Reset Password flow:
  * a reset request targets exactly the address that was submitted
  * an unknown address is not enumerated and never falls back to another account
  * the reset link carries the token only, so it cannot leak or presume an identity
  * a missing / unknown / expired / already-used token cannot change any password
  * a valid token changes only the account it was issued for, even when the request
    body also carries a different account's email
  * pilot, founder, scale, and ordinary accounts all travel the same code path

Follows the repo's fake-connection unit style (no real DB, network, or mail provider).
"""
from __future__ import annotations

from collections import defaultdict
from contextlib import contextmanager
from datetime import timedelta
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from services.api.app import pilot


PILOT_EMAIL = 'decoda.guard@gmail.com'
REPORTER_EMAIL = 'thanhdat852@gmail.com'
STRONG_PASSWORD = 'NewPassword123'


# ---------------------------------------------------------------------------
# Fake connection
# ---------------------------------------------------------------------------
class FakeResult:
    def __init__(self, row=None, rows=None):
        self._row = row
        self._rows = rows or []

    def fetchone(self):
        return self._row

    def fetchall(self):
        return self._rows


class FakeConn:
    """Minimal stand-in for the auth tables the reset flow touches."""

    def __init__(self, users=None, tokens=None):
        # email -> {'id': ..., 'email': ...}
        self.users = users or {}
        # token_hash -> {'id', 'user_id', 'expires_at', 'used_at'}
        self.tokens = tokens or {}
        self.executed = []
        self.writes = defaultdict(list)

    def execute(self, query, params=None):
        sql = ' '.join(str(query).split())
        self.executed.append((sql, params))

        if sql.startswith('SELECT id FROM users WHERE email'):
            return FakeResult(row=self.users.get(params[0]))

        if sql.startswith('SELECT email FROM users WHERE id'):
            row = next((user for user in self.users.values() if user['id'] == params[0]), None)
            return FakeResult(row={'email': row['email']} if row else None)

        if sql.startswith('SELECT id, user_id, expires_at, used_at FROM auth_tokens WHERE token_hash'):
            return FakeResult(row=self.tokens.get(params[0]))

        if sql.startswith('INSERT INTO auth_tokens'):
            token_id, user_id, token_hash, purpose, ttl_minutes = params[0], params[1], params[2], params[3], params[4]
            self.writes['token_inserts'].append(
                {'id': token_id, 'user_id': user_id, 'token_hash': token_hash,
                 'purpose': purpose, 'ttl_minutes': ttl_minutes},
            )
            self.tokens[token_hash] = {
                'id': token_id,
                'user_id': user_id,
                'expires_at': pilot.utc_now() + timedelta(minutes=int(ttl_minutes)),
                'used_at': None,
            }
            return FakeResult()

        if sql.startswith('UPDATE auth_tokens SET used_at'):
            self.writes['token_consumptions'].append(params[0])
            for row in self.tokens.values():
                if row['id'] == params[0]:
                    row['used_at'] = pilot.utc_now()
            return FakeResult()

        if sql.startswith('UPDATE users SET password_hash'):
            self.writes['password_updates'].append({'password_hash': params[0], 'user_id': params[1], 'sql': sql})
            return FakeResult()

        if sql.startswith('UPDATE auth_sessions SET revoked_at'):
            self.writes['session_revocations'].append(params[0])
            return FakeResult()

        return FakeResult()

    def commit(self):
        self.writes['commits'].append(True)


@contextmanager
def _fake_pg(conn):
    yield conn


def _request():
    return SimpleNamespace(headers={}, client=SimpleNamespace(host='127.0.0.1'), method='POST')


def _user(user_id: str, email: str):
    return {'id': user_id, 'email': email}


@pytest.fixture
def sent_email(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, str]]:
    """Captures outbound transactional mail instead of contacting a provider."""
    outbox: list[dict[str, str]] = []
    monkeypatch.setenv('AUTH_TOKEN_SECRET', 'test-secret-for-password-reset')
    monkeypatch.setenv('APP_PUBLIC_URL', 'https://rwa.decodasecurity.com')
    monkeypatch.setenv('BACKGROUND_JOBS_MODE', 'inline')
    monkeypatch.setattr(
        pilot,
        '_send_email',
        lambda to_email, subject, text_body: outbox.append(
            {'to': to_email, 'subject': subject, 'body': text_body},
        ),
    )
    return outbox


def _bootstrap(monkeypatch: pytest.MonkeyPatch, conn: FakeConn) -> None:
    monkeypatch.setattr(pilot, 'require_live_mode', lambda: None)
    monkeypatch.setattr(pilot, 'ensure_pilot_schema', lambda *_: None)
    monkeypatch.setattr(pilot, 'pg_connection', lambda: _fake_pg(conn))
    monkeypatch.setattr(pilot, 'log_audit', lambda *a, **k: None)


def _issue_reset_token(monkeypatch, conn, sent_email, email: str) -> str:
    """Runs the real request path and returns the raw token from the delivered mail."""
    before = len(sent_email)
    pilot.request_password_reset({'email': email}, _request())
    assert len(sent_email) == before + 1, 'expected exactly one reset email'
    body = sent_email[-1]['body']
    return body.split('token=', 1)[1].strip()


# ---------------------------------------------------------------------------
# The reset request targets exactly the submitted email
# ---------------------------------------------------------------------------
def test_reset_request_targets_the_submitted_email(monkeypatch, sent_email):
    conn = FakeConn(users={
        REPORTER_EMAIL: _user('user-reporter', REPORTER_EMAIL),
        PILOT_EMAIL: _user('user-pilot', PILOT_EMAIL),
    })
    _bootstrap(monkeypatch, conn)

    assert pilot.request_password_reset({'email': REPORTER_EMAIL}, _request()) == {
        'sent': True,
        'reset_token': None,
    }

    # Mail goes to the submitted address only — the pilot account is untouched.
    assert [message['to'] for message in sent_email] == [REPORTER_EMAIL]
    assert PILOT_EMAIL not in sent_email[0]['body']

    # The token is bound to the submitted account's user id.
    assert [insert['user_id'] for insert in conn.writes['token_inserts']] == ['user-reporter']
    assert conn.writes['token_inserts'][0]['purpose'] == 'password_reset'
    assert int(conn.writes['token_inserts'][0]['ttl_minutes']) == pilot.PASSWORD_RESET_TTL_MINUTES


def test_reset_request_never_falls_back_to_another_account(monkeypatch, sent_email):
    conn = FakeConn(users={PILOT_EMAIL: _user('user-pilot', PILOT_EMAIL)})
    _bootstrap(monkeypatch, conn)

    # An address with no account: acknowledged without enumeration, and WITHOUT
    # quietly retargeting the only account that does exist.
    assert pilot.request_password_reset({'email': 'nobody@example.com'}, _request()) == {'sent': True}
    assert sent_email == []
    assert conn.writes['token_inserts'] == []


def test_reset_request_rejects_a_malformed_email_instead_of_defaulting(monkeypatch, sent_email):
    conn = FakeConn(users={PILOT_EMAIL: _user('user-pilot', PILOT_EMAIL)})
    _bootstrap(monkeypatch, conn)

    for malformed in ('', '   ', 'not-an-email'):
        with pytest.raises(HTTPException) as excinfo:
            pilot.request_password_reset({'email': malformed}, _request())
        assert excinfo.value.status_code == 400

    # `@example.com` clears the backend's shape check but matches no account, so it
    # is acknowledged without a token — still never retargeted at the pilot account.
    assert pilot.request_password_reset({'email': '@example.com'}, _request()) == {'sent': True}

    assert sent_email == []
    assert conn.writes['token_inserts'] == []


def test_reset_link_carries_only_the_token(monkeypatch, sent_email):
    conn = FakeConn(users={REPORTER_EMAIL: _user('user-reporter', REPORTER_EMAIL)})
    _bootstrap(monkeypatch, conn)

    raw_token = _issue_reset_token(monkeypatch, conn, sent_email, REPORTER_EMAIL)
    body = sent_email[-1]['body']

    assert f'https://rwa.decodasecurity.com/reset-password?token={raw_token}' in body
    assert 'email=' not in body
    assert REPORTER_EMAIL not in body


# ---------------------------------------------------------------------------
# Only a valid token authorizes a password change
# ---------------------------------------------------------------------------
@pytest.mark.parametrize('token', ['', '   ', 'not-a-real-token'])
def test_missing_or_invalid_token_cannot_change_a_password(monkeypatch, sent_email, token):
    conn = FakeConn(users={REPORTER_EMAIL: _user('user-reporter', REPORTER_EMAIL)})
    _bootstrap(monkeypatch, conn)

    with pytest.raises(HTTPException) as excinfo:
        pilot.reset_password({'token': token, 'password': STRONG_PASSWORD}, _request())

    assert excinfo.value.status_code == 400
    assert 'Invalid or expired password reset token.' in str(excinfo.value.detail)
    assert conn.writes['password_updates'] == []


def test_an_email_in_the_body_cannot_authorize_a_reset(monkeypatch, sent_email):
    """The email field is addressing data for the REQUEST step, never authorization."""
    conn = FakeConn(users={REPORTER_EMAIL: _user('user-reporter', REPORTER_EMAIL)})
    _bootstrap(monkeypatch, conn)

    for payload in (
        {'email': REPORTER_EMAIL, 'password': STRONG_PASSWORD},
        {'email': PILOT_EMAIL, 'token': '', 'password': STRONG_PASSWORD},
    ):
        with pytest.raises(HTTPException) as excinfo:
            pilot.reset_password(payload, _request())
        assert excinfo.value.status_code == 400

    assert conn.writes['password_updates'] == []


def test_expired_token_cannot_change_a_password(monkeypatch, sent_email):
    conn = FakeConn(users={REPORTER_EMAIL: _user('user-reporter', REPORTER_EMAIL)})
    _bootstrap(monkeypatch, conn)

    raw_token = _issue_reset_token(monkeypatch, conn, sent_email, REPORTER_EMAIL)
    token_hash = pilot._auth_token_hash(raw_token)
    conn.tokens[token_hash]['expires_at'] = pilot.utc_now() - timedelta(minutes=1)

    with pytest.raises(HTTPException) as excinfo:
        pilot.reset_password({'token': raw_token, 'password': STRONG_PASSWORD}, _request())

    assert excinfo.value.status_code == 400
    assert conn.writes['password_updates'] == []


def test_a_reset_token_is_single_use(monkeypatch, sent_email):
    conn = FakeConn(users={REPORTER_EMAIL: _user('user-reporter', REPORTER_EMAIL)})
    _bootstrap(monkeypatch, conn)

    raw_token = _issue_reset_token(monkeypatch, conn, sent_email, REPORTER_EMAIL)

    assert pilot.reset_password({'token': raw_token, 'password': STRONG_PASSWORD}, _request()) == {
        'password_reset': True,
    }
    assert len(conn.writes['password_updates']) == 1

    # Replaying the same link cannot set another password.
    with pytest.raises(HTTPException) as excinfo:
        pilot.reset_password({'token': raw_token, 'password': 'SecondPassword123'}, _request())

    assert excinfo.value.status_code == 400
    assert len(conn.writes['password_updates']) == 1


# ---------------------------------------------------------------------------
# A valid token resets only its own account
# ---------------------------------------------------------------------------
def test_valid_token_resets_only_its_associated_account(monkeypatch, sent_email):
    conn = FakeConn(users={
        REPORTER_EMAIL: _user('user-reporter', REPORTER_EMAIL),
        PILOT_EMAIL: _user('user-pilot', PILOT_EMAIL),
    })
    _bootstrap(monkeypatch, conn)

    raw_token = _issue_reset_token(monkeypatch, conn, sent_email, REPORTER_EMAIL)

    # A different account's address rides along in the body and is ignored.
    assert pilot.reset_password(
        {'token': raw_token, 'password': STRONG_PASSWORD, 'email': PILOT_EMAIL},
        _request(),
    ) == {'password_reset': True}

    assert [update['user_id'] for update in conn.writes['password_updates']] == ['user-reporter']
    assert conn.writes['session_revocations'] == ['user-reporter']
    # The confirmation goes to the token's own account, resolved from the database.
    assert sent_email[-1]['to'] == REPORTER_EMAIL
    assert sent_email[-1]['subject'].endswith('Password changed')


def test_reset_consumes_the_token_and_invalidates_existing_sessions(monkeypatch, sent_email):
    conn = FakeConn(users={REPORTER_EMAIL: _user('user-reporter', REPORTER_EMAIL)})
    _bootstrap(monkeypatch, conn)

    raw_token = _issue_reset_token(monkeypatch, conn, sent_email, REPORTER_EMAIL)
    token_id = conn.writes['token_inserts'][0]['id']

    pilot.reset_password({'token': raw_token, 'password': STRONG_PASSWORD}, _request())

    assert conn.writes['token_consumptions'] == [token_id]
    assert conn.writes['session_revocations'] == ['user-reporter']
    update_sql = conn.writes['password_updates'][0]['sql']
    assert 'session_version = session_version + 1' in update_sql
    assert 'WHERE id = %s' in update_sql
    # The stored value is a hash, never the plaintext the user typed.
    assert conn.writes['password_updates'][0]['password_hash'] != STRONG_PASSWORD
    assert conn.writes['password_updates'][0]['password_hash'].startswith('scrypt$')


def test_reset_enforces_password_strength_before_touching_the_account(monkeypatch, sent_email):
    conn = FakeConn(users={REPORTER_EMAIL: _user('user-reporter', REPORTER_EMAIL)})
    _bootstrap(monkeypatch, conn)

    raw_token = _issue_reset_token(monkeypatch, conn, sent_email, REPORTER_EMAIL)

    with pytest.raises(HTTPException) as excinfo:
        pilot.reset_password({'token': raw_token, 'password': 'short'}, _request())

    assert excinfo.value.status_code == 400
    assert conn.writes['password_updates'] == []
    # The token survives a rejected attempt so the real reset can still happen.
    assert conn.writes['token_consumptions'] == []


# ---------------------------------------------------------------------------
# Every account tier uses the same mechanism
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    'email,user_id',
    [
        (PILOT_EMAIL, 'user-pilot'),
        ('founder@example.com', 'user-founder'),
        ('scale@example.com', 'user-scale'),
        (REPORTER_EMAIL, 'user-reporter'),
    ],
)
def test_every_account_tier_uses_the_same_token_mechanism(monkeypatch, sent_email, email, user_id):
    conn = FakeConn(users={email: _user(user_id, email)})
    _bootstrap(monkeypatch, conn)

    raw_token = _issue_reset_token(monkeypatch, conn, sent_email, email)

    assert conn.writes['token_inserts'][0]['user_id'] == user_id
    assert conn.writes['token_inserts'][0]['purpose'] == 'password_reset'
    assert sent_email[0]['to'] == email

    assert pilot.reset_password({'token': raw_token, 'password': STRONG_PASSWORD}, _request()) == {
        'password_reset': True,
    }
    assert [update['user_id'] for update in conn.writes['password_updates']] == [user_id]


def test_no_pilot_credentials_are_hardcoded_in_the_auth_module():
    import inspect

    source = inspect.getsource(pilot)

    assert PILOT_EMAIL not in source
    assert 'decoda.guard' not in source
