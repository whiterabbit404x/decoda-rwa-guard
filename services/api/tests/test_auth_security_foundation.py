from __future__ import annotations

from fastapi.testclient import TestClient

from services.api.app import main as api_main


client = TestClient(api_main.app)


def test_auth_verify_email_route_delegates(monkeypatch):
    monkeypatch.setattr(api_main, 'with_auth_schema_json', lambda handler: handler())
    monkeypatch.setattr(api_main, 'verify_email_token', lambda payload, request: {'verified': payload.get('token') == 'ok'})

    response = client.post('/auth/verify-email', json={'token': 'ok'})

    assert response.status_code == 200
    assert response.json() == {'verified': True}


def test_auth_reset_password_route_applies_rate_limit(monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr(api_main, 'with_auth_schema_json', lambda handler: handler())
    monkeypatch.setattr(api_main, 'enforce_auth_rate_limit', lambda request, action: calls.append(action))
    monkeypatch.setattr(api_main, 'reset_password', lambda payload, request: {'password_reset': True})

    response = client.post('/auth/reset-password', json={'token': 't', 'password': 'StrongPass1234'})

    assert response.status_code == 200
    assert response.json() == {'password_reset': True}
    assert calls == ['reset_password']


def test_auth_signout_all_route_delegates(monkeypatch):
    monkeypatch.setattr(api_main, 'with_auth_schema_json', lambda handler: handler())
    monkeypatch.setattr(api_main, 'signout_all_sessions', lambda request: {'signed_out_all': True})

    response = client.post('/auth/signout-all')

    assert response.status_code == 200
    assert response.json() == {'signed_out_all': True}


def test_auth_mfa_complete_signin_route_applies_rate_limit(monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr(api_main, 'with_auth_schema_json', lambda handler: handler())
    monkeypatch.setattr(api_main, 'enforce_auth_rate_limit', lambda request, action: calls.append(action))
    monkeypatch.setattr(api_main, 'mfa_complete_signin', lambda payload, request: {'access_token': 'token', 'token_type': 'bearer', 'user': {'id': 'user-1'}})

    response = client.post('/auth/mfa/complete-signin', json={'mfa_token': 'token-1', 'code': '123456'})

    assert response.status_code == 200
    assert response.json()['token_type'] == 'bearer'
    assert calls == ['mfa_complete_signin']
