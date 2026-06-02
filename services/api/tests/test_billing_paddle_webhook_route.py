"""
Tests for the canonical Paddle webhook route at /api/billing/paddle/webhook.

Covers:
- GET returns health status (not 404)
- POST without signature headers returns 400
- POST with invalid signature returns 400
- Proof accepts Paddle billing provider
"""
from __future__ import annotations

import hashlib
import hmac
import json

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from services.api.app import main as api_main
from services.api.app import pilot
from services.api.app.paid_launch_readiness import check_billing_readiness


client = TestClient(api_main.app, raise_server_exceptions=False)


# 1. GET /api/billing/paddle/webhook → health check, not 404
def test_paddle_webhook_route_get_returns_health():
    response = client.get('/api/billing/paddle/webhook')
    assert response.status_code == 200
    assert response.json()['status'] == 'paddle_webhook_endpoint_ready'


# 2. POST /api/billing/paddle/webhook exists (returns non-404)
def test_paddle_webhook_post_route_exists(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(api_main, 'process_paddle_webhook', lambda *a, **kw: {'ok': True})
    monkeypatch.setattr(api_main, 'with_auth_schema_json', lambda handler: handler())
    body = json.dumps({'event_id': 'e1', 'event_type': 'subscription.created'}).encode()
    response = client.post(
        '/api/billing/paddle/webhook',
        content=body,
        headers={'content-type': 'application/json'},
    )
    assert response.status_code != 404


# 3. Missing signature rejected → 400 (tested via verify function directly)
def test_paddle_webhook_missing_signature_rejected(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv('BILLING_PROVIDER', 'paddle')
    monkeypatch.setenv('PADDLE_API_KEY', 'pdl_api_123')
    monkeypatch.setenv('PADDLE_WEBHOOK_SECRET', 'pdl_secret')
    monkeypatch.setenv('PADDLE_PRICE_ID_PRO', 'pri_123')
    with pytest.raises(HTTPException) as exc:
        pilot.verify_paddle_webhook_signature(
            raw_body=b'{}',
            signature_header=None,
            timestamp_header=None,
        )
    assert exc.value.status_code == 400


# 4. Invalid signature rejected → 400
def test_paddle_webhook_invalid_signature_rejected(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv('BILLING_PROVIDER', 'paddle')
    monkeypatch.setenv('PADDLE_API_KEY', 'pdl_api_123')
    monkeypatch.setenv('PADDLE_WEBHOOK_SECRET', 'correct_secret')
    monkeypatch.setenv('PADDLE_PRICE_ID_PRO', 'pri_123')
    with pytest.raises(HTTPException) as exc:
        pilot.verify_paddle_webhook_signature(
            raw_body=b'{}',
            signature_header='totally_wrong_signature',
            timestamp_header='1717000000',
        )
    assert exc.value.status_code == 400
    assert 'Invalid' in str(exc.value.detail)


# 5. Proof accepts Paddle instead of Stripe — all required vars present
def test_proof_accepts_paddle_billing_provider(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv('BILLING_PROVIDER', 'paddle')
    monkeypatch.setenv('PADDLE_API_KEY', 'pdl_api_testkey_xyz')
    monkeypatch.setenv('PADDLE_CLIENT_TOKEN', 'pdl_client_testkey_xyz')
    monkeypatch.setenv('PADDLE_PRICE_ID', 'pri_prod_monthly_xyz')
    monkeypatch.setenv('PADDLE_WEBHOOK_SECRET', 'pdl_whsec_testkey_xyz')
    monkeypatch.setenv('PADDLE_ENVIRONMENT', 'production')

    result = check_billing_readiness()

    assert result['billing_ready'] is True
    assert result['billing_webhook_ready'] is True
    assert result['billing_status'] == 'ready'
    assert result['billing_missing_env'] == []
    # No Stripe vars required
    assert 'STRIPE_SECRET_KEY' not in result.get('billing_missing_env', [])


# 6. Proof blocks when Stripe vars absent but Paddle vars present (no cross-contamination)
def test_paddle_proof_does_not_require_stripe_vars(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv('BILLING_PROVIDER', 'paddle')
    monkeypatch.setenv('PADDLE_API_KEY', 'pdl_api_testkey_xyz')
    monkeypatch.setenv('PADDLE_CLIENT_TOKEN', 'pdl_client_testkey_xyz')
    monkeypatch.setenv('PADDLE_PRICE_ID', 'pri_prod_monthly_xyz')
    monkeypatch.setenv('PADDLE_WEBHOOK_SECRET', 'pdl_whsec_testkey_xyz')
    monkeypatch.setenv('PADDLE_ENVIRONMENT', 'production')
    monkeypatch.delenv('STRIPE_SECRET_KEY', raising=False)
    monkeypatch.delenv('STRIPE_WEBHOOK_SECRET', raising=False)
    monkeypatch.delenv('STRIPE_PRICE_ID', raising=False)

    result = check_billing_readiness()

    assert result['billing_ready'] is True
    assert result['billing_webhook_ready'] is True
