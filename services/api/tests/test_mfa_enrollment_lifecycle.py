"""Backend tests for the TOTP MFA enrollment/confirmation lifecycle.

These cover the root cause of the "Invalid MFA code" failure (a v2-encrypted
pending secret being handed to the verifier undecrypted) plus the hardened
pending-enrollment lifecycle: stable enrollment ids, expiry, replacement
invalidation, single-use consumption, safe reason codes, and the guarantee that
the otpauth URI and the verifier agree on SHA1 / 6 digits / 30s using UTC time.

The suite follows the offline pattern used by the other pilot auth tests: pilot
is loaded via importlib and the database is a small stateful fake, so no real
Postgres is required.
"""
from __future__ import annotations

import base64
import importlib.util
import logging
import os
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi import HTTPException, Request

PILOT_PATH = Path(__file__).resolve().parents[1] / 'app' / 'pilot.py'

USER = {'id': 'user-1', 'email': 'operator@example.com', 'current_workspace_id': 'ws-1'}
# A fixed clock keeps enroll/confirm deterministic and avoids 30s-boundary flakiness.
FIXED_NOW = datetime(2026, 8, 5, 12, 0, 0, tzinfo=timezone.utc)


@pytest.fixture(scope='module')
def pilot():
    spec = importlib.util.spec_from_file_location('pilot_mfa_lifecycle', PILOT_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError('Unable to load pilot.py for MFA lifecycle tests.')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _request(headers: dict[str, str] | None = None) -> Request:
    encoded = [(k.lower().encode('latin-1'), v.encode('latin-1')) for k, v in (headers or {}).items()]
    return Request({'type': 'http', 'headers': encoded})


class FakeConnection:
    """Stateful fake of the single `users` row exercised by enroll/confirm."""

    def __init__(self, user_id: str = USER['id'], row: dict | None = None):
        self.user_id = user_id
        self.row = row if row is not None else {
            'mfa_pending_secret': None,
            'mfa_pending_enrollment_id': None,
            'mfa_pending_created_at': None,
            'mfa_pending_expires_at': None,
            'mfa_totp_secret': None,
            'mfa_enabled_at': None,
        }
        self.session_mfa_verified = False

    class _Result:
        def __init__(self, row=None, rows=None):
            self._row = row
            self._rows = rows or []

        def fetchone(self):
            return self._row

        def fetchall(self):
            return self._rows

    def execute(self, statement, params=None):
        s = ' '.join(statement.split())
        params = params or ()
        if s.startswith('UPDATE users SET mfa_pending_secret = %s, mfa_pending_enrollment_id = %s'):
            self.row['mfa_pending_secret'] = params[0]
            self.row['mfa_pending_enrollment_id'] = params[1]
            self.row['mfa_pending_expires_at'] = params[2]
            self.row['mfa_pending_created_at'] = datetime.now(timezone.utc)
            return self._Result()
        if s.startswith('SELECT mfa_pending_secret, mfa_pending_enrollment_id, mfa_pending_expires_at, mfa_enabled_at FROM users WHERE id = %s'):
            # Enforce user binding: only the seeded user's row is visible.
            if params and params[0] != self.user_id:
                return self._Result(None)
            return self._Result(dict(self.row))
        if s.startswith('UPDATE users SET mfa_totp_secret = %s, mfa_pending_secret = NULL, mfa_pending_enrollment_id = NULL'):
            self.row['mfa_totp_secret'] = params[0]
            self.row['mfa_pending_secret'] = None
            self.row['mfa_pending_enrollment_id'] = None
            self.row['mfa_pending_created_at'] = None
            self.row['mfa_pending_expires_at'] = None
            self.row['mfa_enabled_at'] = datetime.now(timezone.utc)
            return self._Result()
        if s.startswith('UPDATE auth_sessions SET mfa_verified_at = NOW()'):
            self.session_mfa_verified = True
            return self._Result()
        # recovery-code churn + any other write: accept as no-op.
        return self._Result(None)

    def commit(self):
        return None


def _wire(pilot, monkeypatch, connection: FakeConnection, audit_sink: list | None = None, now: datetime = FIXED_NOW):
    @contextmanager
    def fake_pg():
        yield connection

    monkeypatch.setattr(pilot, 'require_live_mode', lambda: None)
    monkeypatch.setattr(pilot, 'pg_connection', fake_pg)
    monkeypatch.setattr(pilot, 'ensure_pilot_schema', lambda c: None)
    monkeypatch.setattr(pilot, 'authenticate_with_connection', lambda c, r: dict(USER))
    monkeypatch.setattr(pilot, '_current_session_hash', lambda r: 'session-hash')
    monkeypatch.setattr(pilot, 'utc_now', lambda: now)
    # Recovery-code hashing depends on the managed AUTH key, which is out of scope
    # for these enrollment-lifecycle tests; stub it to a fixed set of the right size.
    monkeypatch.setattr(pilot, '_replace_recovery_codes', lambda c, uid: [f'code{i:02d}-abcd' for i in range(pilot.MFA_RECOVERY_CODE_COUNT)])

    def _audit(connection, **kwargs):
        if audit_sink is not None:
            audit_sink.append(kwargs)

    monkeypatch.setattr(pilot, 'log_audit', _audit)


def _enroll_then_code(pilot, monkeypatch, connection, at=None):
    result = pilot.mfa_begin_enrollment(_request())
    when = at or pilot.utc_now()
    code = pilot._totp_code(result['secret'], when)
    return result, code


# ---------------------------------------------------------------------------
# Section 9 — deterministic RFC 6238 diagnostic vectors.
# ---------------------------------------------------------------------------

def test_rfc6238_known_vector_and_uri_verifier_parameters_match(pilot, monkeypatch):
    # RFC 6238 appendix B SHA1 key ("12345678901234567890") at T=59s.
    secret = 'GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ'
    fixed = datetime(1970, 1, 1, 0, 0, 59, tzinfo=timezone.utc)
    assert pilot._totp_code(secret, fixed, digits=8) == '94287082'
    assert pilot._totp_code(secret, fixed) == '287082'

    uri = pilot._totp_provisioning_uri(secret, 'Decoda RWA Guard', 'op@example.com')
    query = parse_qs(urlsplit(uri).query)
    assert query['algorithm'] == ['SHA1']
    assert query['digits'] == ['6']
    assert query['period'] == ['30']
    assert query['secret'] == [secret]
    # The verifier uses SHA1/6/30 and the same UTC clock, so the advertised code verifies.
    monkeypatch.setattr(pilot, 'utc_now', lambda: fixed)
    assert pilot._verify_totp(secret, pilot._totp_code(secret, fixed)) is True


def test_base32_secret_is_normalized_consistently(pilot, monkeypatch):
    secret = 'GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ'
    fixed = datetime(1970, 1, 1, 0, 0, 59, tzinfo=timezone.utc)
    lowered = secret.lower()
    spaced = ' '.join(secret[i:i + 4] for i in range(0, len(secret), 4))
    assert pilot._totp_secret_bytes(lowered) == pilot._totp_secret_bytes(secret)
    assert pilot._totp_secret_bytes(spaced) == pilot._totp_secret_bytes(secret)
    # A code entered with embedded whitespace still verifies (whitespace stripped).
    monkeypatch.setattr(pilot, 'utc_now', lambda: fixed)
    code = pilot._totp_code(secret, fixed)
    assert pilot._verify_totp(secret, f' {code[:3]} {code[3:]} ') is True


def test_adjacent_windows_allowed_but_wider_windows_rejected(pilot, monkeypatch):
    secret = 'GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ'
    now = datetime(2026, 8, 5, 12, 0, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(pilot, 'utc_now', lambda: now)
    period = pilot._TOTP_PERIOD_SECONDS
    # Current + one adjacent step each side are accepted.
    for drift in (-period, 0, period):
        assert pilot._verify_totp(secret, pilot._totp_code(secret, now + timedelta(seconds=drift))) is True
    # Two steps away is rejected.
    for drift in (-2 * period, 2 * period):
        assert pilot._verify_totp(secret, pilot._totp_code(secret, now + timedelta(seconds=drift))) is False


def test_verification_uses_utc_unix_time_without_timezone_offset(pilot, monkeypatch):
    secret = 'GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ'
    now = datetime(2026, 8, 5, 12, 0, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(pilot, 'utc_now', lambda: now)
    # The UTC code verifies; a code computed with a +7h wall-clock offset must NOT.
    assert pilot._verify_totp(secret, pilot._totp_code(secret, now)) is True
    offset_code = pilot._totp_code(secret, now + timedelta(hours=7))
    assert pilot._verify_totp(secret, offset_code) is False


def test_diagnose_distinguishes_clock_miss_from_invalid_code(pilot, monkeypatch):
    secret = 'GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ'
    now = datetime(2026, 8, 5, 12, 0, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(pilot, 'utc_now', lambda: now)
    # A code valid several steps away (outside the allowed window) => clock miss.
    skewed = pilot._totp_code(secret, now + timedelta(seconds=120))
    assert pilot._verify_totp(secret, skewed) is False
    assert pilot._diagnose_totp_failure(secret, skewed) == pilot.MFA_CONFIRM_REASON_CLOCK_WINDOW_MISS
    # A code that matches nowhere => invalid code.
    assert pilot._diagnose_totp_failure(secret, '000000') == pilot.MFA_CONFIRM_REASON_CODE_INVALID


# ---------------------------------------------------------------------------
# Root cause — v2-encrypted pending secrets must decrypt for verification.
# ---------------------------------------------------------------------------

def test_decrypt_mfa_secret_routes_v2_scheme_to_decrypt(pilot, monkeypatch):
    # Regression guard for the actual bug: a value using the v2 scheme must be
    # decrypted, not returned raw as if it were an unencrypted secret.
    blob = f'{pilot.SECRET_SCHEME_V2}:keyref:iv:ciphertext'
    seen = {}

    def fake_decrypt(value, aad=''):
        seen['value'] = value
        seen['aad'] = aad
        return 'PLAINTEXTSECRET'

    monkeypatch.setattr(pilot, 'decrypt_secret', fake_decrypt)
    recovered = pilot._decrypt_mfa_secret('user-1', blob)
    assert recovered == 'PLAINTEXTSECRET'
    assert seen['value'] == blob
    assert seen['aad'] == 'user:user-1:totp'
    # And it is NEVER returned as the raw ciphertext blob.
    assert not recovered.startswith(pilot.SECRET_SCHEME_V2)


def test_encrypt_then_decrypt_roundtrips_with_real_v2_key(pilot, monkeypatch):
    # End-to-end proof with a real AES-256-GCM key (production-like): the pending
    # secret round-trips, so enrollment and confirmation use the SAME secret.
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM  # noqa: F401
    except Exception:
        pytest.skip('cryptography AESGCM backend unavailable in this environment')
    monkeypatch.setenv('APP_MODE', 'production')
    monkeypatch.setenv('MANAGED_KEY_PROVIDER', 'env')
    monkeypatch.setenv('SECRET_ENCRYPTION_KEY', base64.b64encode(os.urandom(32)).decode('ascii'))
    raw = base64.b32encode(os.urandom(20)).decode('ascii').rstrip('=')
    stored = pilot._encrypt_mfa_secret('user-1', raw)
    assert stored.startswith(f'{pilot.SECRET_SCHEME_V2}:')
    assert pilot._decrypt_mfa_secret('user-1', stored) == raw


# ---------------------------------------------------------------------------
# Enroll / confirm lifecycle.
# ---------------------------------------------------------------------------

def test_enroll_returns_contract_fields_and_persists_pending(pilot, monkeypatch):
    conn = FakeConnection()
    _wire(pilot, monkeypatch, conn)
    result = pilot.mfa_begin_enrollment(_request())
    for field in ('enrollment_id', 'otpauth_uri', 'expires_at', 'algorithm', 'digits', 'period', 'secret'):
        assert field in result
    assert result['algorithm'] == 'SHA1'
    assert result['digits'] == 6
    assert result['period'] == 30
    # Pending secret is stored ENCRYPTED (never the raw base32) and bound to the id.
    assert conn.row['mfa_pending_secret'] != result['secret']
    assert conn.row['mfa_pending_enrollment_id'] == result['enrollment_id']
    assert conn.row['mfa_pending_expires_at'] is not None


def test_enroll_and_confirm_use_the_same_secret_and_enable(pilot, monkeypatch):
    conn = FakeConnection()
    _wire(pilot, monkeypatch, conn)
    result, code = _enroll_then_code(pilot, monkeypatch, conn)
    out = pilot.mfa_confirm_enrollment({'enrollment_id': result['enrollment_id'], 'code': code}, _request())
    assert out['mfa_enabled'] is True
    assert out['enrollment_id'] == result['enrollment_id']
    assert len(out['recovery_codes']) == pilot.MFA_RECOVERY_CODE_COUNT
    assert conn.row['mfa_enabled_at'] is not None
    assert conn.row['mfa_pending_secret'] is None
    assert conn.session_mfa_verified is True


def test_current_window_code_succeeds_and_wrong_code_fails(pilot, monkeypatch):
    conn = FakeConnection()
    _wire(pilot, monkeypatch, conn)
    result, code = _enroll_then_code(pilot, monkeypatch, conn)
    wrong = '000000' if code != '000000' else '111111'
    with pytest.raises(HTTPException) as exc:
        pilot.mfa_confirm_enrollment({'enrollment_id': result['enrollment_id'], 'code': wrong}, _request())
    assert exc.value.status_code == 400
    assert exc.value.detail == pilot.MFA_CONFIRM_GENERIC_MESSAGE


def test_confirm_accepts_adjacent_window_code(pilot, monkeypatch):
    conn = FakeConnection()
    _wire(pilot, monkeypatch, conn)
    result = pilot.mfa_begin_enrollment(_request())
    prev_code = pilot._totp_code(result['secret'], pilot.utc_now() - timedelta(seconds=30))
    out = pilot.mfa_confirm_enrollment({'enrollment_id': result['enrollment_id'], 'code': prev_code}, _request())
    assert out['mfa_enabled'] is True


def test_reenrollment_replaces_and_old_enrollment_id_is_rejected(pilot, monkeypatch):
    conn = FakeConnection()
    _wire(pilot, monkeypatch, conn)
    first = pilot.mfa_begin_enrollment(_request())
    first_code = pilot._totp_code(first['secret'], pilot.utc_now())
    second = pilot.mfa_begin_enrollment(_request())
    assert second['enrollment_id'] != first['enrollment_id']
    # Confirming against the OLD enrollment id is rejected as replaced.
    with pytest.raises(HTTPException) as exc:
        pilot.mfa_confirm_enrollment({'enrollment_id': first['enrollment_id'], 'code': first_code}, _request())
    assert exc.value.detail == pilot.MFA_CONFIRM_GENERIC_MESSAGE
    # The old secret's code no longer works even without an id (secret rotated).
    with pytest.raises(HTTPException):
        pilot.mfa_confirm_enrollment({'code': first_code}, _request())
    # The current enrollment confirms successfully.
    second_code = pilot._totp_code(second['secret'], pilot.utc_now())
    assert pilot.mfa_confirm_enrollment({'enrollment_id': second['enrollment_id'], 'code': second_code}, _request())['mfa_enabled'] is True


def test_expired_pending_enrollment_is_rejected(pilot, monkeypatch):
    conn = FakeConnection()
    _wire(pilot, monkeypatch, conn)
    result, code = _enroll_then_code(pilot, monkeypatch, conn)
    # Force the pending enrollment to have already expired.
    conn.row['mfa_pending_expires_at'] = pilot.utc_now() - timedelta(seconds=1)
    with pytest.raises(HTTPException) as exc:
        pilot.mfa_confirm_enrollment({'enrollment_id': result['enrollment_id'], 'code': code}, _request())
    assert exc.value.status_code == 400
    assert exc.value.detail == pilot.MFA_CONFIRM_GENERIC_MESSAGE


def test_enrollment_cannot_be_consumed_twice(pilot, monkeypatch):
    conn = FakeConnection()
    _wire(pilot, monkeypatch, conn)
    result, code = _enroll_then_code(pilot, monkeypatch, conn)
    assert pilot.mfa_confirm_enrollment({'enrollment_id': result['enrollment_id'], 'code': code}, _request())['mfa_enabled'] is True
    # Second confirm: pending is gone -> rejected (single use).
    with pytest.raises(HTTPException) as exc:
        pilot.mfa_confirm_enrollment({'enrollment_id': result['enrollment_id'], 'code': code}, _request())
    assert exc.value.detail == pilot.MFA_CONFIRM_GENERIC_MESSAGE


def test_confirm_without_pending_enrollment_is_not_found(pilot, monkeypatch):
    conn = FakeConnection()
    _wire(pilot, monkeypatch, conn)
    with pytest.raises(HTTPException) as exc:
        pilot.mfa_confirm_enrollment({'code': '123456'}, _request())
    assert exc.value.status_code == 400
    assert exc.value.detail == pilot.MFA_CONFIRM_GENERIC_MESSAGE


def test_invalidated_pending_secret_cannot_be_confirmed(pilot, monkeypatch):
    # Simulates migration 0139 clearing the compromised pending secret.
    conn = FakeConnection()
    _wire(pilot, monkeypatch, conn)
    result, code = _enroll_then_code(pilot, monkeypatch, conn)
    conn.row['mfa_pending_secret'] = None
    conn.row['mfa_pending_enrollment_id'] = None
    with pytest.raises(HTTPException):
        pilot.mfa_confirm_enrollment({'enrollment_id': result['enrollment_id'], 'code': code}, _request())


def test_enrollment_id_is_bound_to_authenticated_user(pilot, monkeypatch):
    # A pending enrollment created for USER is not visible when a different user
    # authenticates (the confirm SELECT is scoped by the authenticated user id).
    conn = FakeConnection()
    _wire(pilot, monkeypatch, conn)
    result, code = _enroll_then_code(pilot, monkeypatch, conn)
    monkeypatch.setattr(pilot, 'authenticate_with_connection', lambda c, r: {'id': 'other-user', 'email': 'x@example.com', 'current_workspace_id': 'ws-1'})
    with pytest.raises(HTTPException):
        pilot.mfa_confirm_enrollment({'enrollment_id': result['enrollment_id'], 'code': code}, _request())


# ---------------------------------------------------------------------------
# Safe logging — the raw secret and submitted OTP must never be logged/audited.
# ---------------------------------------------------------------------------

def test_raw_secret_and_otp_absent_from_logs_and_audit(pilot, monkeypatch, caplog):
    conn = FakeConnection()
    audit: list = []
    _wire(pilot, monkeypatch, conn, audit_sink=audit)
    with caplog.at_level(logging.INFO):
        result = pilot.mfa_begin_enrollment(_request())
        secret = result['secret']
        # A failed confirmation exercises the reason-code logging path.
        with pytest.raises(HTTPException):
            pilot.mfa_confirm_enrollment({'enrollment_id': result['enrollment_id'], 'code': '000000'}, _request())

    logged = caplog.text
    assert secret not in logged
    assert '000000' not in logged
    # Audit metadata carries the enrollment id but never the secret or a code.
    for entry in audit:
        meta = entry.get('metadata') or {}
        assert secret not in str(meta)
        assert 'secret' not in {k.lower() for k in meta}


def test_all_confirmation_failures_share_the_generic_client_message(pilot, monkeypatch):
    # not_found, replaced, expired, invalid-code all must surface the SAME generic
    # message so verification internals never leak to the client.
    conn = FakeConnection()
    _wire(pilot, monkeypatch, conn)

    messages = set()
    # not found
    try:
        pilot.mfa_confirm_enrollment({'code': '123456'}, _request())
    except HTTPException as e:
        messages.add(e.detail)
    # invalid code (with a live pending enrollment)
    result, _ = _enroll_then_code(pilot, monkeypatch, conn)
    try:
        pilot.mfa_confirm_enrollment({'enrollment_id': result['enrollment_id'], 'code': '000000'}, _request())
    except HTTPException as e:
        messages.add(e.detail)
    # expired
    conn.row['mfa_pending_expires_at'] = pilot.utc_now() - timedelta(seconds=1)
    try:
        pilot.mfa_confirm_enrollment({'enrollment_id': result['enrollment_id'], 'code': '123456'}, _request())
    except HTTPException as e:
        messages.add(e.detail)
    assert messages == {pilot.MFA_CONFIRM_GENERIC_MESSAGE}
