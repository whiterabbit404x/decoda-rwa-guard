"""A completed password reset proves the inbox, so it also settles email verification.

The bug this covers: an account that never verified its email could request a reset,
receive the link, set a new password, and then still be refused at sign-in with
"Verify your email...". The reset link is itself an email-control challenge — a
single-use, expiring secret delivered only to the address on the account — so
consuming one proves exactly what the signup verification link proves.

The rules asserted here:

  * consuming a VALID reset token marks that account's email verified, in the same
    statement that changes the password, so the two can never diverge
  * an already-verified account keeps its original verification timestamp
  * nothing short of a consumed token verifies anything: not requesting a reset, not
    opening the reset screen, not an email in the request body, not a rejected,
    expired, or already-spent token
  * a token issued for one account never touches another account's state
  * the signup verification flow is unchanged

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
OLD_PASSWORD = 'OldPassword123'
NEW_PASSWORD = 'NewPassword123'


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
    """The `users` / `auth_tokens` / `auth_sessions` rows the recovery loop touches.

    Statements are matched on the exact SQL the application issues, so a change to
    one of those statements shows up here as a failing test rather than as a silently
    ignored write.
    """

    def __init__(self, users=None):
        # email -> user row
        self.users = users or {}
        # token_hash -> {'id', 'user_id', 'purpose', 'expires_at', 'used_at'}
        self.tokens = {}
        self.writes = defaultdict(list)
        # Set to raise from the password UPDATE, standing in for a mid-transaction failure.
        self.fail_on_password_update = False

    # -- helpers ------------------------------------------------------------
    def user_by_id(self, user_id):
        return next((user for user in self.users.values() if user['id'] == user_id), None)

    def verified_at(self, email):
        return self.users[email]['email_verified_at']

    def is_verified(self, email):
        return self.users[email]['email_verified_at'] is not None

    # -- query dispatch -----------------------------------------------------
    def execute(self, query, params=None):
        sql = ' '.join(str(query).split())
        self.writes['statements'].append(sql)

        # --- reads on users ---
        if sql.startswith('SELECT id FROM users WHERE email'):
            return FakeResult(row=self.users.get(params[0]))

        if sql.startswith('SELECT id, email_verified_at FROM users WHERE email'):
            row = self.users.get(params[0])
            return FakeResult(row={'id': row['id'], 'email_verified_at': row['email_verified_at']} if row else None)

        if sql.startswith('SELECT id, password_hash, email_verified_at, session_version, mfa_totp_secret, mfa_enabled_at FROM users WHERE email'):
            row = self.users.get(params[0])
            return FakeResult(row=dict(row) if row else None)

        if sql.startswith('SELECT suspended_at FROM users WHERE id'):
            row = self.user_by_id(params[0])
            return FakeResult(row={'suspended_at': row.get('suspended_at')} if row else None)

        if sql.startswith('SELECT email, email_verified_at FROM users WHERE id'):
            row = self.user_by_id(params[0])
            return FakeResult(
                row={'email': row['email'], 'email_verified_at': row['email_verified_at']} if row else None,
            )

        if sql.startswith('SELECT email FROM users WHERE id'):
            row = self.user_by_id(params[0])
            return FakeResult(row={'email': row['email']} if row else None)

        # --- auth_tokens ---
        if sql.startswith('SELECT id, user_id, expires_at, used_at FROM auth_tokens WHERE token_hash'):
            purpose = 'password_reset' if "purpose = 'password_reset'" in sql else 'email_verification'
            row = self.tokens.get(params[0])
            # Purpose is part of the lookup: a token issued for one purpose is simply
            # not found by the other.
            return FakeResult(row=row if row and row['purpose'] == purpose else None)

        if sql.startswith('INSERT INTO auth_tokens'):
            token_id, user_id, token_hash, purpose, ttl_minutes = params[:5]
            self.tokens[token_hash] = {
                'id': token_id,
                'user_id': user_id,
                'purpose': purpose,
                'expires_at': pilot.utc_now() + timedelta(minutes=int(ttl_minutes)),
                'used_at': None,
            }
            self.writes['token_inserts'].append({'id': token_id, 'user_id': user_id, 'purpose': purpose})
            return FakeResult()

        # The atomic single-use claim. Checked before the plainer UPDATE below, which
        # it shares a prefix with.
        if sql.startswith('UPDATE auth_tokens SET used_at = NOW() WHERE id = %s AND used_at IS NULL RETURNING id'):
            for row in self.tokens.values():
                if row['id'] == params[0] and row['used_at'] is None:
                    row['used_at'] = pilot.utc_now()
                    self.writes['token_consumptions'].append(params[0])
                    return FakeResult(row={'id': params[0]})
            return FakeResult(row=None)

        if sql.startswith('UPDATE auth_tokens SET used_at = NOW() WHERE user_id'):
            user_id, keep_id = params[0], params[1]
            for row in self.tokens.values():
                if row['user_id'] == user_id and row['used_at'] is None and row['id'] != keep_id:
                    row['used_at'] = pilot.utc_now()
                    self.writes['sibling_revocations'].append(row['id'])
            return FakeResult()

        if sql.startswith('UPDATE auth_tokens SET used_at = NOW() WHERE id'):
            for row in self.tokens.values():
                if row['id'] == params[0]:
                    row['used_at'] = pilot.utc_now()
                    self.writes['token_consumptions'].append(params[0])
            return FakeResult()

        # --- writes on users ---
        if sql.startswith('UPDATE users SET password_hash'):
            if self.fail_on_password_update:
                raise RuntimeError('simulated failure while writing the new password')
            self.writes['password_updates'].append({'password_hash': params[0], 'user_id': params[1], 'sql': sql})
            row = self.user_by_id(params[1])
            if row is not None:
                row['password_hash'] = params[0]
                if 'email_verified_at = COALESCE(email_verified_at, NOW())' in sql and row['email_verified_at'] is None:
                    row['email_verified_at'] = pilot.utc_now()
                    self.writes['email_verifications'].append(params[1])
            return FakeResult()

        if sql.startswith('UPDATE users SET email_verified_at = NOW()'):
            row = self.user_by_id(params[0])
            if row is not None:
                row['email_verified_at'] = pilot.utc_now()
            self.writes['standalone_email_verifications'].append(params[0])
            return FakeResult()

        if sql.startswith('UPDATE users SET last_sign_in_at'):
            self.writes['sign_ins'].append(params[0])
            return FakeResult()

        # --- sessions ---
        if sql.startswith('UPDATE auth_sessions SET revoked_at'):
            self.writes['session_revocations'].append(params[0])
            return FakeResult()

        if sql.startswith('INSERT INTO auth_sessions'):
            self.writes['session_inserts'].append(params[1])
            return FakeResult()

        return FakeResult()

    def commit(self):
        self.writes['commits'].append(True)


@contextmanager
def _fake_pg(conn):
    yield conn


def _request():
    return SimpleNamespace(headers={}, client=SimpleNamespace(host='127.0.0.1'), method='POST')


def _user(user_id: str, email: str, *, password: str = OLD_PASSWORD, email_verified_at=None):
    """A `users` row. Unverified by default — the state a fresh signup is in."""
    return {
        'id': user_id,
        'email': email,
        'password_hash': pilot.hash_password(password),
        'email_verified_at': email_verified_at,
        'session_version': 1,
        'mfa_totp_secret': None,
        'mfa_enabled_at': None,
        'suspended_at': None,
    }


@pytest.fixture
def sent_email(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, str]]:
    """Captures outbound transactional mail instead of contacting a provider."""
    outbox: list[dict[str, str]] = []
    monkeypatch.setenv('AUTH_TOKEN_SECRET', 'test-secret-for-reset-verification')
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


@pytest.fixture
def audit_log() -> list[dict]:
    return []


def _bootstrap(monkeypatch: pytest.MonkeyPatch, conn: FakeConn, audit_log: list[dict] | None = None) -> None:
    monkeypatch.setattr(pilot, 'require_live_mode', lambda: None)
    monkeypatch.setattr(pilot, 'ensure_pilot_schema', lambda *_: None)
    monkeypatch.setattr(pilot, 'pg_connection', lambda: _fake_pg(conn))
    monkeypatch.setattr(
        pilot,
        'log_audit',
        lambda connection, **kwargs: (audit_log.append(kwargs) if audit_log is not None else None),
    )
    # Hydration and session storage are not what these tests are about.
    monkeypatch.setattr(
        pilot,
        'build_user_response',
        lambda connection, user_id: {
            'id': user_id,
            'current_workspace_id': None,
            'current_workspace': None,
        },
    )


def _link_token(text: str, path: str) -> str:
    match = re.search(rf'/{path}\?token=(\S+)', text)
    assert match, f'no {path} link found in message: {text!r}'
    return match.group(1)


def _issue_reset_token(conn: FakeConn, sent_email: list[dict], email: str) -> str:
    """Runs the real request path and returns the raw token from the delivered mail."""
    before = len(sent_email)
    pilot.request_password_reset({'email': email}, _request())
    assert len(sent_email) == before + 1, 'expected exactly one reset email'
    return _link_token(sent_email[-1]['body'], 'reset-password')


def _issue_verification_token(conn: FakeConn, sent_email: list[dict], email: str) -> str:
    before = len(sent_email)
    pilot.request_email_verification({'email': email}, _request())
    assert len(sent_email) == before + 1, 'expected exactly one verification email'
    return _link_token(sent_email[-1]['body'], 'verify-email')


# ---------------------------------------------------------------------------
# 1 + 2. What a consumed reset token settles
# ---------------------------------------------------------------------------
def test_unverified_account_becomes_verified_by_completing_a_reset(monkeypatch, sent_email, audit_log):
    """The reported bug: reset succeeded but sign-in still demanded verification."""
    conn = FakeConn(users={REPORTER_EMAIL: _user('user-reporter', REPORTER_EMAIL)})
    _bootstrap(monkeypatch, conn, audit_log)

    assert conn.is_verified(REPORTER_EMAIL) is False

    raw_token = _issue_reset_token(conn, sent_email, REPORTER_EMAIL)
    assert pilot.reset_password({'token': raw_token, 'password': NEW_PASSWORD}, _request()) == {
        'password_reset': True,
    }

    # The address is now verified, with a timestamp — not merely a boolean.
    assert conn.is_verified(REPORTER_EMAIL) is True
    assert conn.verified_at(REPORTER_EMAIL) is not None
    assert conn.writes['email_verifications'] == ['user-reporter']

    # And the audit trail says how the address came to be verified.
    verification_entries = [entry for entry in audit_log if entry['action'] == 'auth.email_verified']
    assert len(verification_entries) == 1
    assert verification_entries[0]['entity_id'] == 'user-reporter'
    assert verification_entries[0]['metadata'] == {'method': 'password_reset_token'}


def test_sign_in_succeeds_with_the_new_password_after_the_reset(monkeypatch, sent_email, audit_log):
    conn = FakeConn(users={REPORTER_EMAIL: _user('user-reporter', REPORTER_EMAIL)})
    _bootstrap(monkeypatch, conn, audit_log)

    # Before the reset the account cannot sign in at all: unverified, fail-closed.
    with pytest.raises(HTTPException) as excinfo:
        pilot.signin_user({'email': REPORTER_EMAIL, 'password': OLD_PASSWORD}, _request())
    assert excinfo.value.status_code == 403

    raw_token = _issue_reset_token(conn, sent_email, REPORTER_EMAIL)
    pilot.reset_password({'token': raw_token, 'password': NEW_PASSWORD}, _request())

    # The new password now signs in, with no verification step left to complete.
    signed_in = pilot.signin_user({'email': REPORTER_EMAIL, 'password': NEW_PASSWORD}, _request())
    assert signed_in['user']['id'] == 'user-reporter'
    assert conn.writes['sign_ins'] == ['user-reporter']

    # The old password no longer works.
    with pytest.raises(HTTPException) as excinfo:
        pilot.signin_user({'email': REPORTER_EMAIL, 'password': OLD_PASSWORD}, _request())
    assert excinfo.value.status_code == 401


def test_an_already_verified_account_keeps_its_original_verification_timestamp(monkeypatch, sent_email, audit_log):
    verified_at = pilot.utc_now() - timedelta(days=30)
    conn = FakeConn(users={
        REPORTER_EMAIL: _user('user-reporter', REPORTER_EMAIL, email_verified_at=verified_at),
    })
    _bootstrap(monkeypatch, conn, audit_log)

    raw_token = _issue_reset_token(conn, sent_email, REPORTER_EMAIL)
    pilot.reset_password({'token': raw_token, 'password': NEW_PASSWORD}, _request())

    # Still verified, and the reset did not restamp a verification that already held.
    assert conn.verified_at(REPORTER_EMAIL) == verified_at
    assert conn.writes['email_verifications'] == []
    assert [entry['action'] for entry in audit_log if entry['action'] == 'auth.email_verified'] == []

    # The password change itself still happened.
    assert len(conn.writes['password_updates']) == 1
    assert pilot.verify_password(NEW_PASSWORD, conn.users[REPORTER_EMAIL]['password_hash'])


# ---------------------------------------------------------------------------
# 3-5. Nothing short of a consumed token verifies anything
# ---------------------------------------------------------------------------
@pytest.mark.parametrize('token', ['', '   ', 'not-a-real-token'])
def test_an_invalid_token_changes_neither_password_nor_verification(monkeypatch, sent_email, audit_log, token):
    conn = FakeConn(users={REPORTER_EMAIL: _user('user-reporter', REPORTER_EMAIL)})
    _bootstrap(monkeypatch, conn, audit_log)

    with pytest.raises(HTTPException) as excinfo:
        pilot.reset_password({'token': token, 'password': NEW_PASSWORD}, _request())

    assert excinfo.value.status_code == 400
    assert conn.writes['password_updates'] == []
    assert conn.is_verified(REPORTER_EMAIL) is False


def test_an_expired_token_leaves_the_email_unverified(monkeypatch, sent_email, audit_log):
    conn = FakeConn(users={REPORTER_EMAIL: _user('user-reporter', REPORTER_EMAIL)})
    _bootstrap(monkeypatch, conn, audit_log)

    raw_token = _issue_reset_token(conn, sent_email, REPORTER_EMAIL)
    conn.tokens[pilot._auth_token_hash(raw_token)]['expires_at'] = pilot.utc_now() - timedelta(minutes=1)

    with pytest.raises(HTTPException) as excinfo:
        pilot.reset_password({'token': raw_token, 'password': NEW_PASSWORD}, _request())

    assert excinfo.value.status_code == 400
    assert conn.writes['password_updates'] == []
    assert conn.is_verified(REPORTER_EMAIL) is False


def test_a_spent_token_cannot_verify_a_second_account_state(monkeypatch, sent_email, audit_log):
    """A reset token stays single-use, and its replay verifies nothing on its own."""
    verified_at = pilot.utc_now() - timedelta(days=1)
    conn = FakeConn(users={REPORTER_EMAIL: _user('user-reporter', REPORTER_EMAIL)})
    _bootstrap(monkeypatch, conn, audit_log)

    raw_token = _issue_reset_token(conn, sent_email, REPORTER_EMAIL)
    pilot.reset_password({'token': raw_token, 'password': NEW_PASSWORD}, _request())
    assert conn.is_verified(REPORTER_EMAIL) is True

    # Roll the account back to unverified and replay the spent link: it must not be
    # able to re-verify, and it must not set another password.
    conn.users[REPORTER_EMAIL]['email_verified_at'] = None
    with pytest.raises(HTTPException) as excinfo:
        pilot.reset_password({'token': raw_token, 'password': 'SecondPassword123'}, _request())

    assert excinfo.value.status_code == 400
    assert len(conn.writes['password_updates']) == 1
    assert conn.is_verified(REPORTER_EMAIL) is False
    del verified_at


def test_a_password_rejected_by_policy_verifies_nothing(monkeypatch, sent_email, audit_log):
    conn = FakeConn(users={REPORTER_EMAIL: _user('user-reporter', REPORTER_EMAIL)})
    _bootstrap(monkeypatch, conn, audit_log)

    raw_token = _issue_reset_token(conn, sent_email, REPORTER_EMAIL)

    with pytest.raises(HTTPException) as excinfo:
        pilot.reset_password({'token': raw_token, 'password': 'short'}, _request())

    assert excinfo.value.status_code == 400
    assert conn.is_verified(REPORTER_EMAIL) is False
    # The link survives a rejected attempt, so the real reset can still happen.
    assert conn.writes['token_consumptions'] == []


# ---------------------------------------------------------------------------
# 6-7. Asking for a link, or looking at one, is not proof
# ---------------------------------------------------------------------------
def test_requesting_a_reset_email_does_not_verify_the_address(monkeypatch, sent_email, audit_log):
    conn = FakeConn(users={REPORTER_EMAIL: _user('user-reporter', REPORTER_EMAIL)})
    _bootstrap(monkeypatch, conn, audit_log)

    pilot.request_password_reset({'email': REPORTER_EMAIL}, _request())

    assert sent_email[-1]['to'] == REPORTER_EMAIL
    assert conn.is_verified(REPORTER_EMAIL) is False
    assert conn.writes['email_verifications'] == []
    assert conn.writes['standalone_email_verifications'] == []


def test_opening_the_reset_screen_does_not_verify_the_address(monkeypatch, sent_email, audit_log):
    """Validating a link answers a question about it; it changes no account state."""
    conn = FakeConn(users={REPORTER_EMAIL: _user('user-reporter', REPORTER_EMAIL)})
    _bootstrap(monkeypatch, conn, audit_log)

    raw_token = _issue_reset_token(conn, sent_email, REPORTER_EMAIL)

    assert pilot.validate_password_reset_token({'token': raw_token}, _request())['status'] == 'valid'

    assert conn.is_verified(REPORTER_EMAIL) is False
    assert conn.writes['token_consumptions'] == []
    assert conn.writes['password_updates'] == []


# ---------------------------------------------------------------------------
# 8-9. A token settles state for its own account, and only that account
# ---------------------------------------------------------------------------
def test_an_email_in_the_request_body_cannot_verify_another_account(monkeypatch, sent_email, audit_log):
    conn = FakeConn(users={
        REPORTER_EMAIL: _user('user-reporter', REPORTER_EMAIL),
        OTHER_EMAIL: _user('user-other', OTHER_EMAIL),
    })
    _bootstrap(monkeypatch, conn, audit_log)

    raw_token = _issue_reset_token(conn, sent_email, REPORTER_EMAIL)

    # The other account's address rides along in the body and is ignored: the account
    # is resolved from the token record.
    pilot.reset_password(
        {'token': raw_token, 'password': NEW_PASSWORD, 'email': OTHER_EMAIL},
        _request(),
    )

    assert conn.is_verified(REPORTER_EMAIL) is True
    assert conn.is_verified(OTHER_EMAIL) is False
    assert conn.writes['email_verifications'] == ['user-reporter']
    assert [update['user_id'] for update in conn.writes['password_updates']] == ['user-reporter']


def test_one_accounts_token_never_moves_another_accounts_verification_state(monkeypatch, sent_email, audit_log):
    conn = FakeConn(users={
        REPORTER_EMAIL: _user('user-reporter', REPORTER_EMAIL),
        OTHER_EMAIL: _user('user-other', OTHER_EMAIL),
    })
    _bootstrap(monkeypatch, conn, audit_log)

    raw_token = _issue_reset_token(conn, sent_email, REPORTER_EMAIL)
    pilot.reset_password({'token': raw_token, 'password': NEW_PASSWORD}, _request())

    assert conn.is_verified(OTHER_EMAIL) is False
    assert conn.verified_at(OTHER_EMAIL) is None
    # And the other account still cannot sign in — its own challenge is unanswered.
    with pytest.raises(HTTPException) as excinfo:
        pilot.signin_user({'email': OTHER_EMAIL, 'password': OLD_PASSWORD}, _request())
    assert excinfo.value.status_code == 403


# ---------------------------------------------------------------------------
# 10. The signup verification flow is unchanged
# ---------------------------------------------------------------------------
def test_signup_verification_link_still_verifies_an_account(monkeypatch, sent_email, audit_log):
    conn = FakeConn(users={REPORTER_EMAIL: _user('user-reporter', REPORTER_EMAIL)})
    _bootstrap(monkeypatch, conn, audit_log)

    raw_token = _issue_verification_token(conn, sent_email, REPORTER_EMAIL)

    assert pilot.verify_email_token({'token': raw_token}, _request()) == {'verified': True}
    assert conn.is_verified(REPORTER_EMAIL) is True
    assert conn.writes['standalone_email_verifications'] == ['user-reporter']

    # Still single-use.
    with pytest.raises(HTTPException) as excinfo:
        pilot.verify_email_token({'token': raw_token}, _request())
    assert excinfo.value.status_code == 400

    # And the account signs in afterwards, exactly as before.
    assert pilot.signin_user({'email': REPORTER_EMAIL, 'password': OLD_PASSWORD}, _request())['user']['id'] == 'user-reporter'


def test_the_two_token_purposes_are_not_interchangeable(monkeypatch, sent_email, audit_log):
    """A reset link is not a verification link, and vice versa."""
    conn = FakeConn(users={REPORTER_EMAIL: _user('user-reporter', REPORTER_EMAIL)})
    _bootstrap(monkeypatch, conn, audit_log)

    reset_token = _issue_reset_token(conn, sent_email, REPORTER_EMAIL)
    verification_token = _issue_verification_token(conn, sent_email, REPORTER_EMAIL)

    with pytest.raises(HTTPException):
        pilot.verify_email_token({'token': reset_token}, _request())
    with pytest.raises(HTTPException):
        pilot.reset_password({'token': verification_token, 'password': NEW_PASSWORD}, _request())

    assert conn.is_verified(REPORTER_EMAIL) is False
    assert conn.writes['password_updates'] == []


def test_resend_verification_answers_the_same_way_for_every_address(monkeypatch, sent_email, audit_log):
    """The resend endpoint is unauthenticated, so it must not report account state."""
    verified_at = pilot.utc_now() - timedelta(days=2)
    conn = FakeConn(users={
        REPORTER_EMAIL: _user('user-reporter', REPORTER_EMAIL),
        OTHER_EMAIL: _user('user-other', OTHER_EMAIL, email_verified_at=verified_at),
    })
    _bootstrap(monkeypatch, conn, audit_log)

    unverified = pilot.request_email_verification({'email': REPORTER_EMAIL}, _request())
    already_verified = pilot.request_email_verification({'email': OTHER_EMAIL}, _request())
    unknown = pilot.request_email_verification({'email': 'nobody@example.com'}, _request())

    assert unverified == already_verified == unknown == {'sent': True, 'verification_token': None}
    # Only the account that actually needs a link is sent one.
    assert [message['to'] for message in sent_email] == [REPORTER_EMAIL]
    # And no response ever carries the token itself.
    assert all(response['verification_token'] is None for response in (unverified, already_verified, unknown))


# ---------------------------------------------------------------------------
# 11-12. Single use, and one indivisible write
# ---------------------------------------------------------------------------
def test_the_reset_token_remains_single_use(monkeypatch, sent_email, audit_log):
    conn = FakeConn(users={REPORTER_EMAIL: _user('user-reporter', REPORTER_EMAIL)})
    _bootstrap(monkeypatch, conn, audit_log)

    raw_token = _issue_reset_token(conn, sent_email, REPORTER_EMAIL)
    token_id = conn.writes['token_inserts'][0]['id']

    pilot.reset_password({'token': raw_token, 'password': NEW_PASSWORD}, _request())
    assert conn.writes['token_consumptions'] == [token_id]

    with pytest.raises(HTTPException):
        pilot.reset_password({'token': raw_token, 'password': 'SecondPassword123'}, _request())

    assert conn.writes['token_consumptions'] == [token_id]
    assert len(conn.writes['password_updates']) == 1


def test_password_and_verification_are_written_by_one_statement(monkeypatch, sent_email, audit_log):
    """They cannot diverge: there is no window where one landed and the other did not."""
    conn = FakeConn(users={REPORTER_EMAIL: _user('user-reporter', REPORTER_EMAIL)})
    _bootstrap(monkeypatch, conn, audit_log)

    raw_token = _issue_reset_token(conn, sent_email, REPORTER_EMAIL)
    commits_before = len(conn.writes['commits'])  # the request step has its own commit
    pilot.reset_password({'token': raw_token, 'password': NEW_PASSWORD}, _request())

    update_sql = conn.writes['password_updates'][0]['sql']
    assert 'password_hash = %s' in update_sql
    assert 'email_verified_at = COALESCE(email_verified_at, NOW())' in update_sql
    assert 'session_version = session_version + 1' in update_sql
    # No second statement stamps verification separately.
    assert conn.writes['standalone_email_verifications'] == []
    # The whole reset is one transaction, committed once at the end.
    assert len(conn.writes['commits']) - commits_before == 1


def test_a_failure_mid_reset_commits_no_partial_auth_state(monkeypatch, sent_email, audit_log):
    conn = FakeConn(users={REPORTER_EMAIL: _user('user-reporter', REPORTER_EMAIL)})
    _bootstrap(monkeypatch, conn, audit_log)

    raw_token = _issue_reset_token(conn, sent_email, REPORTER_EMAIL)
    commits_before = len(conn.writes['commits'])  # the request step has its own commit
    conn.fail_on_password_update = True

    with pytest.raises(RuntimeError):
        pilot.reset_password({'token': raw_token, 'password': NEW_PASSWORD}, _request())

    # The reset committed nothing, so the token claim, the password, and the
    # verification stamp all roll back together.
    assert len(conn.writes['commits']) == commits_before
    assert conn.writes['password_updates'] == []
    assert conn.writes['email_verifications'] == []
    assert conn.is_verified(REPORTER_EMAIL) is False
    # No confirmation mail claiming a change that did not happen.
    assert [message['subject'] for message in sent_email if 'Password changed' in message['subject']] == []


# ---------------------------------------------------------------------------
# The refusal the user actually sees
# ---------------------------------------------------------------------------
def test_unverified_sign_in_names_the_state_and_the_next_step(monkeypatch, sent_email, audit_log):
    conn = FakeConn(users={REPORTER_EMAIL: _user('user-reporter', REPORTER_EMAIL)})
    _bootstrap(monkeypatch, conn, audit_log)

    with pytest.raises(HTTPException) as excinfo:
        pilot.signin_user({'email': REPORTER_EMAIL, 'password': OLD_PASSWORD}, _request())

    assert excinfo.value.status_code == 403
    assert excinfo.value.detail == pilot.EMAIL_NOT_VERIFIED_MESSAGE
    # A machine-readable state, so the screen can offer the recovery action instead
    # of leaving the user with a sentence and no way forward.
    assert (excinfo.value.headers or {}).get('X-Decoda-Error-Code') == pilot.EMAIL_NOT_VERIFIED_CODE
    assert pilot.EMAIL_NOT_VERIFIED_CODE == 'EMAIL_NOT_VERIFIED'


def test_a_suspended_account_is_still_refused_after_a_reset(monkeypatch, sent_email, audit_log):
    """Verification is not an override: suspension is checked on its own."""
    conn = FakeConn(users={REPORTER_EMAIL: _user('user-reporter', REPORTER_EMAIL)})
    conn.users[REPORTER_EMAIL]['suspended_at'] = pilot.utc_now()
    _bootstrap(monkeypatch, conn, audit_log)

    raw_token = _issue_reset_token(conn, sent_email, REPORTER_EMAIL)
    pilot.reset_password({'token': raw_token, 'password': NEW_PASSWORD}, _request())
    assert conn.is_verified(REPORTER_EMAIL) is True

    with pytest.raises(HTTPException) as excinfo:
        pilot.signin_user({'email': REPORTER_EMAIL, 'password': NEW_PASSWORD}, _request())

    assert excinfo.value.status_code == 403
    assert 'suspended' in str(excinfo.value.detail).lower()
