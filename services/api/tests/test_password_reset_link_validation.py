"""The reset link's own lifecycle: verifying it, spending it, and voiding the rest.

The reset screen asks the API whether a link can still be used before it renders a
password form, so these are the guarantees that screen depends on:

  * a valid link reports the account it belongs to — resolved from the token record,
    never from anything the caller supplied
  * an expired, spent, or unrecognised link is reported as such, and reveals nothing
    about the token, the account, or why it failed
  * checking a link does not consume it, so reloading the page is safe
  * completing a reset consumes that link atomically, and voids every other reset link
    outstanding for the account

Follows the repo's fake-connection unit style (no real DB, network, or mail provider).
"""
from __future__ import annotations

import re
from collections import defaultdict
from contextlib import contextmanager
from datetime import timedelta
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from services.api.app import pilot


REPORTER_EMAIL = 'thanhdat852@gmail.com'
OTHER_EMAIL = 'someone.else@example.com'
STRONG_PASSWORD = 'NewPassword123'


class FakeResult:
    def __init__(self, row=None, rows=None):
        self._row = row
        self._rows = rows or []

    def fetchone(self):
        return self._row

    def fetchall(self):
        return self._rows


class FakeConn:
    """Stand-in for the `users` / `auth_tokens` / `auth_sessions` tables."""

    def __init__(self, users=None):
        self.users = users or {}
        self.tokens = {}
        self.writes = defaultdict(list)
        # Set to a token id to simulate another request winning the claim first.
        self.claim_stolen_for = None

    def execute(self, query, params=None):
        sql = ' '.join(str(query).split())

        if sql.startswith('SELECT id FROM users WHERE email'):
            return FakeResult(row=self.users.get(params[0]))

        if sql.startswith('SELECT email, email_verified_at FROM users WHERE id'):
            row = next((user for user in self.users.values() if user['id'] == params[0]), None)
            return FakeResult(
                row={'email': row['email'], 'email_verified_at': row.get('email_verified_at')} if row else None,
            )

        if sql.startswith('SELECT email FROM users WHERE id'):
            row = next((user for user in self.users.values() if user['id'] == params[0]), None)
            return FakeResult(row={'email': row['email']} if row else None)

        if sql.startswith('SELECT id, user_id, expires_at, used_at FROM auth_tokens WHERE token_hash'):
            return FakeResult(row=self.tokens.get(params[0]))

        if sql.startswith('INSERT INTO auth_tokens'):
            token_id, user_id, token_hash, purpose, ttl_minutes = params[:5]
            self.tokens[token_hash] = {
                'id': token_id,
                'user_id': user_id,
                'expires_at': pilot.utc_now() + timedelta(minutes=int(ttl_minutes)),
                'used_at': None,
            }
            self.writes['token_inserts'].append({'id': token_id, 'user_id': user_id, 'purpose': purpose})
            return FakeResult()

        if sql.startswith('UPDATE auth_tokens SET used_at = NOW() WHERE id = %s AND used_at IS NULL RETURNING id'):
            if self.claim_stolen_for == params[0]:
                # Another concurrent request claimed the row between the SELECT and here.
                for row in self.tokens.values():
                    if row['id'] == params[0]:
                        row['used_at'] = pilot.utc_now()
                self.writes['claim_losses'].append(params[0])
                return FakeResult(row=None)
            for row in self.tokens.values():
                if row['id'] == params[0] and row['used_at'] is None:
                    row['used_at'] = pilot.utc_now()
                    self.writes['token_consumptions'].append(params[0])
                    return FakeResult(row={'id': params[0]})
            self.writes['claim_losses'].append(params[0])
            return FakeResult(row=None)

        if sql.startswith('UPDATE auth_tokens SET used_at = NOW() WHERE user_id'):
            user_id, keep_id = params[0], params[1]
            for row in self.tokens.values():
                if row['user_id'] == user_id and row['used_at'] is None and row['id'] != keep_id:
                    row['used_at'] = pilot.utc_now()
                    self.writes['sibling_revocations'].append(row['id'])
            return FakeResult()

        if sql.startswith('UPDATE users SET password_hash'):
            self.writes['password_updates'].append({'password_hash': params[0], 'user_id': params[1]})
            # Mirror the statement's COALESCE onto the fake row.
            row = next((user for user in self.users.values() if user['id'] == params[1]), None)
            if row is not None and 'email_verified_at = COALESCE(email_verified_at, NOW())' in sql:
                if row.get('email_verified_at') is None:
                    row['email_verified_at'] = pilot.utc_now()
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


@pytest.fixture
def sent_email(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, str]]:
    outbox: list[dict[str, str]] = []
    monkeypatch.setenv('AUTH_TOKEN_SECRET', 'test-secret-for-reset-link-validation')
    monkeypatch.setenv('APP_PUBLIC_URL', 'https://rwa.decodasecurity.com')
    monkeypatch.setenv('BACKGROUND_JOBS_MODE', 'inline')
    monkeypatch.setattr(
        pilot,
        '_send_email',
        lambda to_email, subject, text_body, html_body=None: outbox.append(
            {'to': to_email, 'subject': subject, 'body': text_body, 'html': html_body or ''},
        ),
    )
    return outbox


def _bootstrap(monkeypatch: pytest.MonkeyPatch, conn: FakeConn) -> None:
    monkeypatch.setattr(pilot, 'require_live_mode', lambda: None)
    monkeypatch.setattr(pilot, 'ensure_pilot_schema', lambda *_: None)
    monkeypatch.setattr(pilot, 'pg_connection', lambda: _fake_pg(conn))
    monkeypatch.setattr(pilot, 'log_audit', lambda *a, **k: None)


def _issue_reset_token(conn, sent_email, email: str) -> str:
    before = len(sent_email)
    pilot.request_password_reset({'email': email}, _request())
    assert len(sent_email) == before + 1
    match = re.search(r'/reset-password\?token=(\S+)', sent_email[-1]['body'])
    assert match
    return match.group(1)


def _user(user_id: str, email: str, email_verified_at=None):
    return {'id': user_id, 'email': email, 'email_verified_at': email_verified_at}


# ---------------------------------------------------------------------------
# Validating a link
# ---------------------------------------------------------------------------
def test_a_valid_link_reports_the_account_it_belongs_to(monkeypatch, sent_email):
    conn = FakeConn(users={REPORTER_EMAIL: _user('user-reporter', REPORTER_EMAIL)})
    _bootstrap(monkeypatch, conn)
    raw_token = _issue_reset_token(conn, sent_email, REPORTER_EMAIL)

    result = pilot.validate_password_reset_token({'token': raw_token}, _request())

    assert result['status'] == 'valid'
    assert result['email'] == REPORTER_EMAIL
    # The policy the form must render comes from the same place that enforces it.
    assert result['password_policy'] == pilot.password_policy()


def test_the_reported_account_comes_from_the_token_not_the_request(monkeypatch, sent_email):
    conn = FakeConn(users={
        REPORTER_EMAIL: _user('user-reporter', REPORTER_EMAIL),
        OTHER_EMAIL: _user('user-other', OTHER_EMAIL),
    })
    _bootstrap(monkeypatch, conn)
    raw_token = _issue_reset_token(conn, sent_email, REPORTER_EMAIL)

    # A different address riding along in the body is ignored entirely.
    result = pilot.validate_password_reset_token(
        {'token': raw_token, 'email': OTHER_EMAIL},
        _request(),
    )

    assert result['email'] == REPORTER_EMAIL


def test_validating_a_link_does_not_spend_it(monkeypatch, sent_email):
    conn = FakeConn(users={REPORTER_EMAIL: _user('user-reporter', REPORTER_EMAIL)})
    _bootstrap(monkeypatch, conn)
    raw_token = _issue_reset_token(conn, sent_email, REPORTER_EMAIL)

    # Reloading the reset page must not burn the link.
    for _ in range(3):
        assert pilot.validate_password_reset_token({'token': raw_token}, _request())['status'] == 'valid'

    assert conn.writes['token_consumptions'] == []
    assert pilot.reset_password({'token': raw_token, 'password': STRONG_PASSWORD}, _request()) == {
        'password_reset': True,
    }


def test_an_expired_link_is_reported_as_expired(monkeypatch, sent_email):
    conn = FakeConn(users={REPORTER_EMAIL: _user('user-reporter', REPORTER_EMAIL)})
    _bootstrap(monkeypatch, conn)
    raw_token = _issue_reset_token(conn, sent_email, REPORTER_EMAIL)
    conn.tokens[pilot._auth_token_hash(raw_token)]['expires_at'] = pilot.utc_now() - timedelta(minutes=1)

    assert pilot.validate_password_reset_token({'token': raw_token}, _request()) == {
        'status': 'expired',
        'email': None,
    }


def test_a_spent_link_is_reported_as_used(monkeypatch, sent_email):
    conn = FakeConn(users={REPORTER_EMAIL: _user('user-reporter', REPORTER_EMAIL)})
    _bootstrap(monkeypatch, conn)
    raw_token = _issue_reset_token(conn, sent_email, REPORTER_EMAIL)
    pilot.reset_password({'token': raw_token, 'password': STRONG_PASSWORD}, _request())

    assert pilot.validate_password_reset_token({'token': raw_token}, _request()) == {
        'status': 'used',
        'email': None,
    }


@pytest.mark.parametrize('token', ['', '   ', 'not-a-real-token', 'a' * 200])
def test_an_unrecognised_link_is_reported_as_invalid(monkeypatch, sent_email, token):
    conn = FakeConn(users={REPORTER_EMAIL: _user('user-reporter', REPORTER_EMAIL)})
    _bootstrap(monkeypatch, conn)

    assert pilot.validate_password_reset_token({'token': token}, _request()) == {
        'status': 'invalid',
        'email': None,
    }


def test_a_token_whose_account_is_gone_cannot_be_completed(monkeypatch, sent_email):
    conn = FakeConn(users={REPORTER_EMAIL: _user('user-reporter', REPORTER_EMAIL)})
    _bootstrap(monkeypatch, conn)
    raw_token = _issue_reset_token(conn, sent_email, REPORTER_EMAIL)
    conn.users.clear()

    assert pilot.validate_password_reset_token({'token': raw_token}, _request()) == {
        'status': 'invalid',
        'email': None,
    }


def test_validation_discloses_nothing_beyond_the_state_and_a_verified_account(monkeypatch, sent_email):
    """A rejected link must not become a source of information about anything."""
    conn = FakeConn(users={REPORTER_EMAIL: _user('user-reporter', REPORTER_EMAIL)})
    _bootstrap(monkeypatch, conn)
    raw_token = _issue_reset_token(conn, sent_email, REPORTER_EMAIL)
    token_hash = pilot._auth_token_hash(raw_token)
    conn.tokens[token_hash]['expires_at'] = pilot.utc_now() - timedelta(minutes=1)

    for payload in ({'token': raw_token}, {'token': 'unknown-token'}):
        result = pilot.validate_password_reset_token(payload, _request())

        # Exactly two keys, and no account address for a link that cannot be used.
        assert set(result) == {'status', 'email'}
        assert result['email'] is None

        rendered = repr(result)
        assert raw_token not in rendered
        assert token_hash not in rendered
        assert 'user-reporter' not in rendered
        assert REPORTER_EMAIL not in rendered
        # No expiry timestamps, internal ids, or reason codes.
        assert 'expires_at' not in rendered and 'used_at' not in rendered


# ---------------------------------------------------------------------------
# Spending a link
# ---------------------------------------------------------------------------
def test_the_token_claim_is_atomic_so_a_race_cannot_reset_twice(monkeypatch, sent_email):
    """Two submissions of the same link both pass the pre-check; only one may win.

    The SELECT that reads the token is not a lock, so single-use has to be decided by
    the conditional UPDATE. This simulates the loser of that race: it sees an unused
    row, then fails to claim it, and must change nothing.
    """
    conn = FakeConn(users={REPORTER_EMAIL: _user('user-reporter', REPORTER_EMAIL)})
    _bootstrap(monkeypatch, conn)
    raw_token = _issue_reset_token(conn, sent_email, REPORTER_EMAIL)
    conn.claim_stolen_for = conn.writes['token_inserts'][0]['id']

    with pytest.raises(HTTPException) as excinfo:
        pilot.reset_password({'token': raw_token, 'password': STRONG_PASSWORD}, _request())

    assert excinfo.value.status_code == 400
    assert conn.writes['claim_losses'] == [conn.writes['token_inserts'][0]['id']]
    # The loser changed no password and revoked no sessions.
    assert conn.writes['password_updates'] == []
    assert conn.writes['session_revocations'] == []


def test_completing_a_reset_voids_every_other_outstanding_link(monkeypatch, sent_email):
    """A user who clicked "resend" three times must not leave two live links behind."""
    conn = FakeConn(users={REPORTER_EMAIL: _user('user-reporter', REPORTER_EMAIL)})
    _bootstrap(monkeypatch, conn)

    first = _issue_reset_token(conn, sent_email, REPORTER_EMAIL)
    second = _issue_reset_token(conn, sent_email, REPORTER_EMAIL)
    third = _issue_reset_token(conn, sent_email, REPORTER_EMAIL)

    assert pilot.reset_password({'token': third, 'password': STRONG_PASSWORD}, _request()) == {
        'password_reset': True,
    }

    # The two earlier links are now spent, and report as such rather than as valid.
    for superseded in (first, second):
        assert pilot.validate_password_reset_token({'token': superseded}, _request())['status'] == 'used'
        with pytest.raises(HTTPException):
            pilot.reset_password({'token': superseded, 'password': 'AnotherPassword123'}, _request())

    assert len(conn.writes['sibling_revocations']) == 2
    assert len(conn.writes['password_updates']) == 1


def test_another_accounts_outstanding_link_is_left_alone(monkeypatch, sent_email):
    conn = FakeConn(users={
        REPORTER_EMAIL: _user('user-reporter', REPORTER_EMAIL),
        OTHER_EMAIL: _user('user-other', OTHER_EMAIL),
    })
    _bootstrap(monkeypatch, conn)

    reporter_token = _issue_reset_token(conn, sent_email, REPORTER_EMAIL)
    other_token = _issue_reset_token(conn, sent_email, OTHER_EMAIL)

    pilot.reset_password({'token': reporter_token, 'password': STRONG_PASSWORD}, _request())

    # Revocation is scoped to the token's own account.
    assert pilot.validate_password_reset_token({'token': other_token}, _request()) == {
        'status': 'valid',
        'email': OTHER_EMAIL,
        'password_policy': pilot.password_policy(),
    }
    assert [update['user_id'] for update in conn.writes['password_updates']] == ['user-reporter']


def test_the_stored_password_is_a_hash_of_the_configured_scheme(monkeypatch, sent_email):
    conn = FakeConn(users={REPORTER_EMAIL: _user('user-reporter', REPORTER_EMAIL)})
    _bootstrap(monkeypatch, conn)
    raw_token = _issue_reset_token(conn, sent_email, REPORTER_EMAIL)

    pilot.reset_password({'token': raw_token, 'password': STRONG_PASSWORD}, _request())

    stored = conn.writes['password_updates'][0]['password_hash']
    assert STRONG_PASSWORD not in stored
    assert stored.startswith('scrypt$')
    # The value stored is one the sign-in path can actually verify.
    assert pilot.verify_password(STRONG_PASSWORD, stored) is True
    assert pilot.verify_password('NotThePassword123', stored) is False


# ---------------------------------------------------------------------------
# The reset email
# ---------------------------------------------------------------------------
def test_the_reset_email_carries_an_actionable_link_in_both_formats(monkeypatch, sent_email):
    conn = FakeConn(users={REPORTER_EMAIL: _user('user-reporter', REPORTER_EMAIL)})
    _bootstrap(monkeypatch, conn)
    raw_token = _issue_reset_token(conn, sent_email, REPORTER_EMAIL)

    message = sent_email[-1]
    url = f'https://rwa.decodasecurity.com/reset-password?token={raw_token}'

    assert message['subject'] == 'Reset your Decoda RWA Guard password'
    assert 'Password reset requested' in message['body']
    assert url in message['body']
    assert 'This link expires after 30 minutes.' in message['body']
    assert "If you didn't request this, you can safely ignore this email." in message['body']

    # The HTML alternative offers the button AND the same link as copyable text, so a
    # client that strips the button still leaves the user a way through.
    assert f'href="{url}"' in message['html']
    assert message['html'].count(url) >= 2
    assert 'Reset password' in message['html']


def test_the_reset_link_is_environment_aware(monkeypatch, sent_email):
    conn = FakeConn(users={REPORTER_EMAIL: _user('user-reporter', REPORTER_EMAIL)})
    _bootstrap(monkeypatch, conn)
    monkeypatch.setenv('APP_PUBLIC_URL', 'https://staging.decodasecurity.example/')

    raw_token = _issue_reset_token(conn, sent_email, REPORTER_EMAIL)

    # A staging deployment mails staging links; no production host is hardcoded.
    assert f'https://staging.decodasecurity.example/reset-password?token={raw_token}' in sent_email[-1]['body']
    assert 'rwa.decodasecurity.com' not in sent_email[-1]['body']
    assert 'rwa.decodasecurity.com' not in sent_email[-1]['html']


def test_the_reset_email_exposes_no_internal_identifiers(monkeypatch, sent_email):
    conn = FakeConn(users={REPORTER_EMAIL: _user('user-reporter', REPORTER_EMAIL)})
    _bootstrap(monkeypatch, conn)
    raw_token = _issue_reset_token(conn, sent_email, REPORTER_EMAIL)

    message = sent_email[-1]
    for leak in ('user-reporter', pilot._auth_token_hash(raw_token), 'workspace'):
        assert leak not in message['body']
        assert leak not in message['html']
    # The link is addressed by token alone: no account address rides in the URL.
    assert 'email=' not in message['body']
    assert 'email=' not in message['html']
