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
