"""Screen 8 response-action approval — session MFA STEP-UP verification.

MFA enrollment and session step-up are SEPARATE workflows. Enrolling MFA marks the
account (and the enrolling session) MFA-verified, but a long-lived session that merely
enrolled is NOT freshly stepped up, so approving a response action still requires the
operator to re-enter their current authenticator code. These tests pin that step-up
flow end to end at the unit level (repo fake-connection style — no real DB / network):

  * the canonical read-side probe ``_session_approval_stepup_satisfied`` is TRUE only
    when the session has BOTH completed MFA AND stepped up within the workspace window,
    so the Approve control's enabled state matches what the command will actually allow;
  * ``_session_reauthentication_recent`` expires the assurance correctly and fails
    CLOSED when the session cannot be resolved;
  * ``verify_session_step_up`` verifies the authenticated user, requires MFA enrollment,
    validates the current TOTP code, marks the session MFA-satisfied + freshly stepped
    up, and reports the assurance expiry — never returning or logging the secret/code;
  * an invalid/expired code neither steps up the session nor lets an approval through;
  * after a successful step-up the gate flips to approvable and a one-of-one approval
    succeeds and leaves Pending Approval WITHOUT creating any new recommendation.
"""
from __future__ import annotations

import logging
from contextlib import contextmanager
from datetime import timedelta
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from services.api.app import pilot


USER_ID = 'user-approver-1'
WS = 'ws-1'
SECRET = 'JBSWY3DPEHPK3PXP'  # a plausible base32 TOTP secret (never asserted as valid)
GOOD_CODE = '123456'
BAD_CODE = '000000'


class _Result:
    def __init__(self, row=None, rows=None):
        self._row = row
        self._rows = rows or []

    def fetchone(self):
        return self._row

    def fetchall(self):
        return self._rows


class _StepUpConn:
    """Fake connection backing the users read, the workspace auth policy, and a MUTABLE
    auth_sessions row so a step-up UPDATE is observable by a later session-security SELECT
    (this is what makes the "approve after step-up" flow testable without a real DB)."""

    def __init__(self, *, mfa_enrolled=True, reauth_minutes=15, session=None):
        self.executed: list[tuple[str, object]] = []
        self.commits = 0
        self._mfa_enrolled = mfa_enrolled
        self._reauth_minutes = reauth_minutes
        # The live session row (subset of columns _current_session_security reads).
        self.session = session if session is not None else {
            'reauthenticated_at': None,
            'authenticated_at': None,
            'mfa_verified_at': None,
            'authentication_methods': [],
        }

    def execute(self, statement, params=None):
        n = ' '.join(str(statement).split())
        self.executed.append((n, params))

        if 'SELECT mfa_totp_secret, mfa_enabled_at FROM users' in n:
            return _Result(row={
                'mfa_totp_secret': 'enc:' + SECRET,
                'mfa_enabled_at': pilot.utc_now() if self._mfa_enrolled else None,
            })
        if 'FROM workspace_auth_policies' in n:
            return _Result(row={'mfa_enforcement': 'optional', 'reauthentication_minutes': self._reauth_minutes})
        if 'FROM auth_sessions WHERE session_token_hash' in n and n.startswith('SELECT'):
            return _Result(row=dict(self.session))
        if n.startswith('UPDATE auth_sessions SET mfa_verified_at = NOW(), reauthenticated_at = NOW()'):
            now = pilot.utc_now()
            self.session['mfa_verified_at'] = now
            self.session['reauthenticated_at'] = now
            self.session['authentication_methods'] = ['password', 'totp']
            return _Result()
        return _Result()

    def commit(self):
        self.commits += 1


def _req():
    return SimpleNamespace(
        headers={'authorization': 'Bearer test-token', 'x-workspace-id': WS},
        client=SimpleNamespace(host='127.0.0.1'), method='POST',
    )


@pytest.fixture(autouse=True)
def _stub_session_hash(monkeypatch):
    # Resolve the current session by a stable hash without needing the managed auth key;
    # these tests exercise the step-up LOGIC, not the token-hashing primitive.
    monkeypatch.setattr(pilot, '_current_session_hash', lambda request: 'session-hash-1')


def _patch_common(monkeypatch, conn):
    @contextmanager
    def _fake_pg():
        yield conn

    monkeypatch.setattr(pilot, 'require_live_mode', lambda: None)
    monkeypatch.setattr(pilot, 'ensure_pilot_schema', lambda *_: None)
    monkeypatch.setattr(pilot, 'pg_connection', _fake_pg)
    monkeypatch.setattr(pilot, 'authenticate_with_connection',
                        lambda *_a, **_k: {'id': USER_ID, 'current_workspace_id': WS})
    monkeypatch.setattr(pilot, 'resolve_workspace', lambda *_a, **_k: {'workspace_id': WS})
    monkeypatch.setattr(pilot, '_decrypt_mfa_secret', lambda _uid, stored: SECRET if stored else '')
    monkeypatch.setattr(pilot, 'log_audit', lambda *a, **k: None)


# ---------------------------------------------------------------------------
# _session_reauthentication_recent — the assurance window (and fail-closed).
# ---------------------------------------------------------------------------

def _session_conn(**over):
    base = {'reauthenticated_at': None, 'authenticated_at': None, 'mfa_verified_at': None, 'authentication_methods': []}
    base.update(over)
    return _StepUpConn(session=base)


def test_reauthentication_recent_true_within_window(monkeypatch):
    conn = _session_conn(reauthenticated_at=pilot.utc_now() - timedelta(minutes=5))
    assert pilot._session_reauthentication_recent(conn, _req(), 15) is True


def test_reauthentication_recent_false_after_window_expires(monkeypatch):
    # 5. Session assurance expires correctly: a step-up 20 minutes ago is stale for a
    #    15-minute window.
    conn = _session_conn(reauthenticated_at=pilot.utc_now() - timedelta(minutes=20))
    assert pilot._session_reauthentication_recent(conn, _req(), 15) is False


def test_reauthentication_recent_fails_closed_without_session():
    class _NoSessionConn:
        def execute(self, *_a, **_k):
            raise HTTPException(status_code=401, detail='Session is no longer active.')
    assert pilot._session_reauthentication_recent(_NoSessionConn(), _req(), 15) is False


# ---------------------------------------------------------------------------
# _session_approval_stepup_satisfied — MFA completed AND recent.
# ---------------------------------------------------------------------------

def test_stepup_satisfied_requires_both_mfa_and_recency(monkeypatch):
    # Verified MFA AND a fresh step-up -> approvable now.
    conn = _session_conn(
        mfa_verified_at=pilot.utc_now(), authentication_methods=['password', 'totp'],
        reauthenticated_at=pilot.utc_now() - timedelta(minutes=2),
    )
    assert pilot._session_approval_stepup_satisfied(conn, _req(), WS) is True


def test_stepup_not_satisfied_when_enrolled_but_not_recently_verified(monkeypatch):
    # 1. MFA enrolled but session NOT freshly stepped up: mfa_verified_at is set (from
    #    enrollment) yet there is no recent reauthentication -> NOT approvable, so the
    #    UI shows a disabled Approve + a verify-session prompt.
    conn = _session_conn(
        mfa_verified_at=pilot.utc_now() - timedelta(hours=3),
        authentication_methods=['password', 'totp'],
        reauthenticated_at=None, authenticated_at=pilot.utc_now() - timedelta(hours=3),
    )
    assert pilot._session_approval_stepup_satisfied(conn, _req(), WS) is False


def test_stepup_not_satisfied_when_mfa_never_completed(monkeypatch):
    # Short-circuit: no MFA factor on the session -> False regardless of recency.
    conn = _session_conn(
        mfa_verified_at=None, authentication_methods=['password'],
        reauthenticated_at=pilot.utc_now(),
    )
    assert pilot._session_approval_stepup_satisfied(conn, _req(), WS) is False


# ---------------------------------------------------------------------------
# verify_session_step_up — the dedicated endpoint.
# ---------------------------------------------------------------------------

def test_valid_totp_completes_step_up(monkeypatch):
    # 3. A valid current TOTP code completes the step-up: the session is marked
    #    MFA-verified AND freshly reauthenticated, and the assurance window is reported.
    conn = _StepUpConn(mfa_enrolled=True, reauth_minutes=15)
    _patch_common(monkeypatch, conn)
    monkeypatch.setattr(pilot, '_verify_totp', lambda secret, code: code == GOOD_CODE)

    result = pilot.verify_session_step_up({'code': GOOD_CODE}, _req())

    assert result['verified'] is True
    assert result['mfa_satisfied'] is True
    assert result['valid_for_minutes'] == 15
    assert result['verified_at'] and result['assurance_expires_at']
    # The session row was actually updated (mfa_verified_at + reauthenticated_at set).
    assert conn.session['mfa_verified_at'] is not None
    assert conn.session['reauthenticated_at'] is not None
    assert 'totp' in conn.session['authentication_methods']
    assert conn.commits == 1


def test_valid_totp_step_up_then_gate_is_approvable(monkeypatch):
    # 6. After a successful step-up the SAME session now satisfies the approval gate —
    #    the read-side probe that decides the Approve control's enabled state.
    conn = _StepUpConn(mfa_enrolled=True, reauth_minutes=15)
    _patch_common(monkeypatch, conn)
    monkeypatch.setattr(pilot, '_verify_totp', lambda secret, code: code == GOOD_CODE)

    # Before: enrolled but never stepped up -> not approvable.
    assert pilot._session_approval_stepup_satisfied(conn, _req(), WS) is False
    pilot.verify_session_step_up({'code': GOOD_CODE}, _req())
    # After: the persisted step-up makes the gate approvable.
    assert pilot._session_approval_stepup_satisfied(conn, _req(), WS) is True


def test_invalid_totp_does_not_step_up_or_approve(monkeypatch):
    # 4. An invalid TOTP code neither steps up the session nor lets an approval through.
    conn = _StepUpConn(mfa_enrolled=True)
    _patch_common(monkeypatch, conn)
    monkeypatch.setattr(pilot, '_verify_totp', lambda secret, code: code == GOOD_CODE)
    with pytest.raises(HTTPException) as exc:
        pilot.verify_session_step_up({'code': BAD_CODE}, _req())
    assert exc.value.status_code == 401
    assert exc.value.detail['code'] == 'INVALID_MFA_CODE'
    # No session UPDATE was issued and the gate stays closed.
    assert not any(s.startswith('UPDATE auth_sessions') for s, _ in conn.executed)
    assert pilot._session_approval_stepup_satisfied(conn, _req(), WS) is False


def test_step_up_requires_mfa_enrollment(monkeypatch):
    # 9. Enrollment and session reauthentication are separate: an un-enrolled caller is
    #    told to enable MFA, never silently pushed back into enrollment from here.
    conn = _StepUpConn(mfa_enrolled=False)
    _patch_common(monkeypatch, conn)
    monkeypatch.setattr(pilot, '_verify_totp', lambda secret, code: True)
    with pytest.raises(HTTPException) as exc:
        pilot.verify_session_step_up({'code': GOOD_CODE}, _req())
    assert exc.value.status_code == 400
    assert exc.value.detail['code'] == 'MFA_NOT_ENROLLED'
    assert not any(s.startswith('UPDATE auth_sessions') for s, _ in conn.executed)


def test_step_up_never_returns_or_logs_secret_or_code(monkeypatch, caplog):
    # The success payload and any log line must never contain the TOTP secret or the
    # submitted code (only the fact + the assurance window are surfaced/audited).
    conn = _StepUpConn(mfa_enrolled=True)
    _patch_common(monkeypatch, conn)
    monkeypatch.setattr(pilot, '_verify_totp', lambda secret, code: code == GOOD_CODE)
    with caplog.at_level(logging.INFO):
        result = pilot.verify_session_step_up({'code': GOOD_CODE}, _req())
    blob = str(result)
    assert SECRET not in blob and GOOD_CODE not in blob
    assert SECRET not in caplog.text and GOOD_CODE not in caplog.text


def test_step_up_invalid_code_never_logs_secret_or_code(monkeypatch, caplog):
    conn = _StepUpConn(mfa_enrolled=True)
    _patch_common(monkeypatch, conn)
    monkeypatch.setattr(pilot, '_verify_totp', lambda secret, code: False)
    with caplog.at_level(logging.INFO):
        with pytest.raises(HTTPException):
            pilot.verify_session_step_up({'code': BAD_CODE}, _req())
    # The generic client message reveals nothing; the log carries only a reason code.
    assert SECRET not in caplog.text and BAD_CODE not in caplog.text


def test_step_up_honors_workspace_reauthentication_window(monkeypatch):
    conn = _StepUpConn(mfa_enrolled=True, reauth_minutes=30)
    _patch_common(monkeypatch, conn)
    monkeypatch.setattr(pilot, '_verify_totp', lambda secret, code: True)
    result = pilot.verify_session_step_up({'code': GOOD_CODE}, _req())
    assert result['valid_for_minutes'] == 30


# ---------------------------------------------------------------------------
# Counts refresh WITHOUT creating a new recommendation.
# ---------------------------------------------------------------------------

def test_counts_after_approval_add_no_new_recommendation():
    # 7 & 8. A one-of-one approval moves the action out of Pending Approval while the
    #        Total Recommended Actions stays 1 — approving never mints a recommendation.
    pending = {'record_type': 'response_action', 'approval_status': 'pending',
               'lifecycle_state': 'awaiting_approval', 'execution_status': 'not_started'}
    before = pilot.build_response_actions_summary([pending])
    assert before['recommended'] == 1
    assert before['pending_approval'] == 1
    assert before['total'] == 1

    approved = {'record_type': 'response_action', 'approval_status': 'approved',
                'lifecycle_state': 'ready_to_execute', 'execution_status': 'not_started'}
    after = pilot.build_response_actions_summary([approved])
    assert after['pending_approval'] == 0
    assert after['recommended'] == 1  # still exactly one recommendation — none created
    assert after['total'] == 1
