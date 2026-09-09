from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient

from services.api.app import main as api_main
from services.api.app.pilot import issue_csrf_token, validate_csrf_token


client = TestClient(api_main.app, raise_server_exceptions=False)


# ---------------------------------------------------------------------------
# CSRF token issuance and validation (unit tests - no HTTP)
# ---------------------------------------------------------------------------

def test_issue_csrf_token_returns_nonce_dot_sig(monkeypatch):
    monkeypatch.setenv('AUTH_TOKEN_SECRET', 'test-secret-for-csrf')
    token = issue_csrf_token()
    parts = token.split('.')
    assert len(parts) == 2
    nonce, sig = parts
    assert len(nonce) == 32  # 16 bytes hex
    assert len(sig) == 64    # sha256 hexdigest


def test_validate_csrf_token_accepts_freshly_issued(monkeypatch):
    monkeypatch.setenv('AUTH_TOKEN_SECRET', 'test-secret-for-csrf')
    token = issue_csrf_token()
    assert validate_csrf_token(token) is True


def test_validate_csrf_token_rejects_tampered_sig(monkeypatch):
    monkeypatch.setenv('AUTH_TOKEN_SECRET', 'test-secret-for-csrf')
    token = issue_csrf_token()
    nonce, _ = token.split('.')
    assert validate_csrf_token(f'{nonce}.deadbeef') is False


def test_validate_csrf_token_rejects_empty():
    assert validate_csrf_token('') is False


def test_validate_csrf_token_rejects_malformed():
    assert validate_csrf_token('nodot') is False
    assert validate_csrf_token('.') is False


def test_validate_csrf_token_rejects_wrong_secret(monkeypatch):
    monkeypatch.setenv('AUTH_TOKEN_SECRET', 'secret-a')
    token = issue_csrf_token()
    monkeypatch.setenv('AUTH_TOKEN_SECRET', 'secret-b')
    assert validate_csrf_token(token) is False


# ---------------------------------------------------------------------------
# CSRF middleware integration tests via TestClient
# ---------------------------------------------------------------------------

def test_csrf_get_request_always_passes(monkeypatch):
    monkeypatch.setattr(api_main, 'live_mode_enabled', lambda: True)
    monkeypatch.setattr(api_main, 'pilot_mode', lambda: 'live')
    response = client.get('/health')
    assert response.status_code == 200


def test_csrf_token_endpoint_returns_token(monkeypatch):
    monkeypatch.setenv('AUTH_TOKEN_SECRET', 'test-secret-for-csrf')
    response = client.get('/auth/csrf-token')
    assert response.status_code == 200
    data = response.json()
    assert 'csrf_token' in data
    assert '.' in data['csrf_token']


def test_authenticated_mutation_without_csrf_returns_403(monkeypatch):
    monkeypatch.setenv('AUTH_TOKEN_SECRET', 'test-secret-for-csrf')
    # POST to a non-exempt authenticated endpoint with a Bearer token but no CSRF header.
    # AUTH_TOKEN_SECRET is set so CSRF enforcement is active.
    response = client.post(
        '/workspaces',
        json={'name': 'test'},
        headers={'Authorization': 'Bearer fake-token'},
    )
    assert response.status_code == 403
    assert response.json()['code'] == 'CSRF_INVALID'


def test_authenticated_mutation_with_valid_csrf_passes_csrf_gate(monkeypatch):
    monkeypatch.setenv('AUTH_TOKEN_SECRET', 'test-secret-for-csrf')
    csrf = issue_csrf_token()
    # The request will fail at auth, but NOT at CSRF (status != 403 with CSRF_INVALID)
    response = client.post(
        '/workspaces',
        json={'name': 'test'},
        headers={'Authorization': 'Bearer fake-token', 'X-CSRF-Token': csrf},
    )
    assert response.status_code != 403 or response.json().get('code') != 'CSRF_INVALID'


def test_unauthenticated_mutation_skips_csrf_check(monkeypatch):
    monkeypatch.setenv('AUTH_TOKEN_SECRET', 'test-secret-for-csrf')
    # Signup is exempt; should not get CSRF 403
    monkeypatch.setattr(api_main, 'enforce_auth_rate_limit', lambda req, action, identifier=None: None)
    monkeypatch.setattr(api_main, 'signup_user', lambda payload, request: {'access_token': 't'})
    response = client.post('/auth/signup', json={'email': 'x@x.com', 'password': 'pass'})
    # May fail for other reasons (live mode etc.) but not CSRF
    assert response.status_code != 403 or response.json().get('code') != 'CSRF_INVALID'


def test_csrf_exempt_signin_requires_no_csrf(monkeypatch):
    monkeypatch.setenv('AUTH_TOKEN_SECRET', 'test-secret-for-csrf')
    monkeypatch.setattr(api_main, 'enforce_auth_rate_limit', lambda req, action, identifier=None: None)
    monkeypatch.setattr(api_main, 'signin_user', lambda payload, request: {'access_token': 't'})
    response = client.post(
        '/auth/signin',
        json={'email': 'x@x.com', 'password': 'pass'},
        headers={'Authorization': 'Bearer fake'},
    )
    assert response.status_code != 403 or response.json().get('code') != 'CSRF_INVALID'


def test_billing_webhook_exempt_from_csrf(monkeypatch):
    monkeypatch.setenv('AUTH_TOKEN_SECRET', 'test-secret-for-csrf')
    monkeypatch.setattr(api_main, 'process_stripe_webhook', lambda payload, request: {'received': True})
    response = client.post(
        '/billing/webhooks/stripe',
        json={},
        headers={'Authorization': 'Bearer fake'},
    )
    assert response.status_code != 403 or response.json().get('code') != 'CSRF_INVALID'


def test_csrf_not_enforced_without_auth_token_secret(monkeypatch):
    # When AUTH_TOKEN_SECRET is not configured, CSRF is skipped (auth system non-functional anyway)
    monkeypatch.delenv('AUTH_TOKEN_SECRET', raising=False)
    monkeypatch.delenv('JWT_SECRET', raising=False)
    response = client.post(
        '/workspaces',
        json={'name': 'test'},
        headers={'Authorization': 'Bearer fake-token'},
    )
    # Should not be blocked by CSRF (might fail at auth for other reasons)
    assert response.status_code != 403 or response.json().get('code') != 'CSRF_INVALID'


# ---------------------------------------------------------------------------
# POST /assets CSRF enforcement tests
# ---------------------------------------------------------------------------

def test_post_assets_without_csrf_returns_403(monkeypatch):
    monkeypatch.setenv('AUTH_TOKEN_SECRET', 'test-secret-for-csrf')
    response = client.post(
        '/assets',
        json={'name': 'test-asset', 'asset_type': 'wallet', 'chain_network': 'ethereum-mainnet', 'identifier': '0x1234'},
        headers={'Authorization': 'Bearer fake-token'},
    )
    assert response.status_code == 403
    assert response.json()['code'] == 'CSRF_INVALID'


def test_post_assets_with_valid_csrf_passes_csrf_gate(monkeypatch):
    monkeypatch.setenv('AUTH_TOKEN_SECRET', 'test-secret-for-csrf')
    csrf = issue_csrf_token()
    # Request will fail at auth (fake token) but NOT at the CSRF gate.
    response = client.post(
        '/assets',
        json={'name': 'test-asset', 'asset_type': 'wallet', 'chain_network': 'ethereum-mainnet', 'identifier': '0x1234'},
        headers={'Authorization': 'Bearer fake-token', 'X-CSRF-Token': csrf},
    )
    # Must not be blocked specifically by CSRF
    assert response.status_code != 403 or response.json().get('code') != 'CSRF_INVALID'


def test_post_assets_with_uuid_csrf_fails_backend_validation(monkeypatch):
    monkeypatch.setenv('AUTH_TOKEN_SECRET', 'test-secret-for-csrf')
    uuid_token = 'abc123def456789012345678901234ab'  # UUID-like, not HMAC nonce.sig
    response = client.post(
        '/assets',
        json={'name': 'test-asset', 'asset_type': 'wallet', 'chain_network': 'ethereum-mainnet', 'identifier': '0x1234'},
        headers={'Authorization': 'Bearer fake-token', 'X-CSRF-Token': uuid_token},
    )
    assert response.status_code == 403
    assert response.json()['code'] == 'CSRF_INVALID'


# ---------------------------------------------------------------------------
# Security Settings auth mutations enforce CSRF (Screen 8 MFA step-up + signout-all).
#
# These are the endpoints the Security Settings page drives (Enroll / Verify / Disable
# MFA, Sign out all sessions). They must remain CSRF-protected: none is on the exempt
# prefix list, so an authenticated POST without a valid HMAC X-CSRF-Token has to be
# rejected. Regression guard against accidentally exempting the MFA/step-up flow.
# ---------------------------------------------------------------------------

# (path, json body). Body is irrelevant for the missing/mismatched-token cases because the
# CSRF middleware runs before request-body validation, but confirm/disable expect a payload.
_AUTH_MUTATION_ENDPOINTS = [
    ('/auth/mfa/enroll', {}),
    ('/auth/mfa/confirm', {'code': '123456'}),
    ('/auth/mfa/disable', {'code': '123456'}),
    # Screen 8 response-action approval step-up: the dedicated session verification
    # endpoint must stay CSRF-protected like every other authenticated auth mutation.
    ('/auth/session/step-up', {'code': '123456'}),
    ('/auth/signout-all', {}),
]


@pytest.mark.parametrize('path,body', _AUTH_MUTATION_ENDPOINTS)
def test_auth_mutation_without_csrf_returns_403(monkeypatch, path, body):
    monkeypatch.setenv('AUTH_TOKEN_SECRET', 'test-secret-for-csrf')
    response = client.post(path, json=body, headers={'Authorization': 'Bearer fake-token'})
    assert response.status_code == 403
    assert response.json()['code'] == 'CSRF_INVALID'


@pytest.mark.parametrize('path,body', _AUTH_MUTATION_ENDPOINTS)
def test_auth_mutation_with_mismatched_csrf_returns_403(monkeypatch, path, body):
    monkeypatch.setenv('AUTH_TOKEN_SECRET', 'test-secret-for-csrf')
    # A UUID-shaped token (the shape the proxy USED to mint) is not a valid HMAC nonce.sig.
    uuid_token = 'abc123def456789012345678901234ab'
    response = client.post(
        path,
        json=body,
        headers={'Authorization': 'Bearer fake-token', 'X-CSRF-Token': uuid_token},
    )
    assert response.status_code == 403
    assert response.json()['code'] == 'CSRF_INVALID'


@pytest.mark.parametrize('path,body', _AUTH_MUTATION_ENDPOINTS)
def test_auth_mutation_with_valid_csrf_passes_csrf_gate(monkeypatch, path, body):
    monkeypatch.setenv('AUTH_TOKEN_SECRET', 'test-secret-for-csrf')
    # Neutralize rate limiting so confirm/disable reach the CSRF gate deterministically.
    monkeypatch.setattr(api_main, 'enforce_auth_rate_limit', lambda req, action, identifier=None: None)
    csrf = issue_csrf_token()
    response = client.post(
        path,
        json=body,
        headers={'Authorization': 'Bearer fake-token', 'X-CSRF-Token': csrf},
    )
    # The fake bearer token fails authentication downstream, but the request must clear the
    # CSRF gate — i.e. it is never rejected specifically as CSRF_INVALID.
    assert response.status_code != 403 or response.json().get('code') != 'CSRF_INVALID'


def test_auth_mutation_accepts_token_minted_by_csrf_token_endpoint(monkeypatch):
    """Production bootstrap path: a token minted by GET /auth/csrf-token — exactly what the
    web proxy relays to the browser and then forwards back as X-CSRF-Token — clears the CSRF
    gate on an authenticated mutation. This is the round trip that was failing when the auth
    proxy dropped the header before reaching the backend."""
    monkeypatch.setenv('AUTH_TOKEN_SECRET', 'test-secret-for-csrf')
    monkeypatch.setattr(api_main, 'enforce_auth_rate_limit', lambda req, action, identifier=None: None)
    minted = client.get('/auth/csrf-token')
    assert minted.status_code == 200
    token = minted.json()['csrf_token']
    response = client.post(
        '/auth/mfa/enroll',
        headers={'Authorization': 'Bearer fake-token', 'X-CSRF-Token': token},
    )
    assert response.status_code != 403 or response.json().get('code') != 'CSRF_INVALID'


# ---------------------------------------------------------------------------
# Token lifetime: an ABSOLUTE wall-clock boundary, not a rolling TTL.
#
# This is the root cause of the transient 403 on internal-admin mutations from
# /admin/customers. issue_csrf_token() signs `nonce:window` where `window` is
# int(now) // 3600 — an hour bucket anchored to the epoch, not to the moment the
# token was minted — and validate_csrf_token() accepts only the current and the
# previous bucket. So EVERY live token dies at the same instant (the top of an
# hour), and its observed lifetime is anywhere from 61 to 120 minutes depending
# on where inside the hour it was minted. A browser that mints its token once
# per page load and holds it across that boundary sends a dead token: the GET
# that renders the page is CSRF-safe and still succeeds, the POST behind a
# button is refused with 403 CSRF_INVALID, and a page refresh mints a token in
# the current bucket so the identical click then works.
#
# These specs PIN that behaviour rather than widen it. The window is a security
# parameter and is deliberately left alone; the browser is what has to re-mint,
# which apps/web/app/csrf-retry.ts now does on a CSRF rejection.
# ---------------------------------------------------------------------------

def _freeze_clock(monkeypatch, epoch_seconds: float):
    """Pin pilot.utc_now() to a fixed instant."""
    from datetime import datetime, timezone

    from services.api.app import pilot as pilot_module

    monkeypatch.setattr(
        pilot_module,
        'utc_now',
        lambda: datetime.fromtimestamp(epoch_seconds, tz=timezone.utc),
    )


# 1970-01-01T10:00:30Z and 1970-01-01T10:59:00Z: two tokens minted almost an
# hour apart inside the SAME bucket.
_EARLY_IN_HOUR = 10 * 3600 + 30
_LATE_IN_HOUR = 10 * 3600 + 59 * 60
_BUCKET_ROLLOVER = 12 * 3600  # both are refused from here


def test_csrf_token_is_valid_inside_its_two_accepted_buckets(monkeypatch):
    monkeypatch.setenv('AUTH_TOKEN_SECRET', 'test-secret-for-csrf')
    _freeze_clock(monkeypatch, _EARLY_IN_HOUR)
    token = issue_csrf_token()

    for moment in (_EARLY_IN_HOUR + 60, 11 * 3600, _BUCKET_ROLLOVER - 1):
        _freeze_clock(monkeypatch, moment)
        assert validate_csrf_token(token) is True, f'rejected at {moment}'


def test_csrf_token_is_refused_once_its_bucket_rolls_over(monkeypatch):
    monkeypatch.setenv('AUTH_TOKEN_SECRET', 'test-secret-for-csrf')
    _freeze_clock(monkeypatch, _EARLY_IN_HOUR)
    token = issue_csrf_token()

    _freeze_clock(monkeypatch, _BUCKET_ROLLOVER)
    assert validate_csrf_token(token) is False


def test_csrf_token_lifetime_depends_on_when_in_the_hour_it_was_minted(monkeypatch):
    """Why the 403 reads as random rather than as an expiry.

    Two tokens minted 58.5 minutes apart expire at the SAME instant, so one
    lived 119.5 minutes and the other 61. Nothing about a session predicts
    which; only the wall clock does.
    """
    monkeypatch.setenv('AUTH_TOKEN_SECRET', 'test-secret-for-csrf')

    _freeze_clock(monkeypatch, _EARLY_IN_HOUR)
    early_token = issue_csrf_token()
    _freeze_clock(monkeypatch, _LATE_IN_HOUR)
    late_token = issue_csrf_token()

    _freeze_clock(monkeypatch, _BUCKET_ROLLOVER - 1)
    assert validate_csrf_token(early_token) is True
    assert validate_csrf_token(late_token) is True

    _freeze_clock(monkeypatch, _BUCKET_ROLLOVER)
    assert validate_csrf_token(early_token) is False
    assert validate_csrf_token(late_token) is False


def test_reminting_after_rollover_restores_access(monkeypatch):
    """What a browser refresh does, and what the retry now does without one."""
    monkeypatch.setenv('AUTH_TOKEN_SECRET', 'test-secret-for-csrf')
    _freeze_clock(monkeypatch, _EARLY_IN_HOUR)
    stale_token = issue_csrf_token()

    _freeze_clock(monkeypatch, _BUCKET_ROLLOVER + 5)
    assert validate_csrf_token(stale_token) is False
    assert validate_csrf_token(issue_csrf_token()) is True


def test_admin_mutation_with_stale_csrf_is_refused_before_the_handler_runs(monkeypatch):
    """The refusal is middleware-level, which is why replaying it is safe.

    enforce_csrf_on_mutations returns 403 WITHOUT calling the route handler, so
    a CSRF-refused "Resend invitation" minted no invitation token and sent no
    email. The browser's one automatic replay therefore cannot duplicate a side
    effect. A stub raises if the handler is ever reached.
    """
    monkeypatch.setenv('AUTH_TOKEN_SECRET', 'test-secret-for-csrf')

    def _must_not_run(*args, **kwargs):  # pragma: no cover - asserted by absence
        raise AssertionError('the route handler ran despite a CSRF refusal')

    monkeypatch.setattr(
        api_main.tenancy_endpoints, 'resend_admin_pilot_invitation', _must_not_run,
    )

    _freeze_clock(monkeypatch, _EARLY_IN_HOUR)
    stale_token = issue_csrf_token()
    _freeze_clock(monkeypatch, _BUCKET_ROLLOVER + 5)

    response = client.post(
        '/admin/pilot-requests/req-1/resend-invitation',
        headers={'Authorization': 'Bearer internal-admin-session', 'X-CSRF-Token': stale_token},
    )
    assert response.status_code == 403
    assert response.json()['code'] == 'CSRF_INVALID'


def test_admin_mutation_with_current_csrf_clears_the_gate(monkeypatch):
    """The same request with a token minted in the current bucket is not a CSRF
    refusal — it goes on to real authentication and internal-admin
    authorization, which this test does not stub and does not weaken."""
    monkeypatch.setenv('AUTH_TOKEN_SECRET', 'test-secret-for-csrf')
    _freeze_clock(monkeypatch, _BUCKET_ROLLOVER + 5)
    fresh_token = issue_csrf_token()

    response = client.post(
        '/admin/pilot-requests/req-1/resend-invitation',
        headers={'Authorization': 'Bearer internal-admin-session', 'X-CSRF-Token': fresh_token},
    )
    assert response.status_code != 403 or response.json().get('code') != 'CSRF_INVALID'
