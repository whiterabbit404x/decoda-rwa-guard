from __future__ import annotations

import importlib
import sys

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def api_main():
    sys.modules.pop('services.api.app.main', None)
    module = importlib.import_module('services.api.app.main')
    return module


def test_validate_runtime_configuration_requires_resend_key_in_production(monkeypatch: pytest.MonkeyPatch) -> None:
    from services.api.app import pilot

    monkeypatch.setenv('APP_ENV', 'production')
    monkeypatch.setenv('LIVE_MODE_ENABLED', 'true')
    monkeypatch.setenv('DATABASE_URL', 'postgresql://example')
    monkeypatch.setenv('AUTH_TOKEN_SECRET', 'secret')
    monkeypatch.setenv('EMAIL_PROVIDER', 'resend')
    monkeypatch.setenv('EMAIL_FROM', 'ops@decoda.app')
    monkeypatch.delenv('EMAIL_RESEND_API_KEY', raising=False)
    monkeypatch.setenv('REDIS_URL', 'redis://localhost:6379/0')
    monkeypatch.setenv('BILLING_ENABLED', 'false')

    payload = pilot.validate_runtime_configuration()

    assert 'EMAIL_RESEND_API_KEY is required when EMAIL_PROVIDER=resend in production.' in payload['errors']
    assert payload['checks']['email_resend_api_key']['ok'] is False


def test_health_diagnostics_exposes_machine_readable_checks(api_main, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv('APP_ENV', 'production')
    monkeypatch.setenv('APP_MODE', 'production')
    monkeypatch.setenv('LIVE_MODE_ENABLED', 'true')
    monkeypatch.delenv('DATABASE_URL', raising=False)
    monkeypatch.setenv('AUTH_TOKEN_SECRET', 'secret')
    monkeypatch.setenv('EMAIL_PROVIDER', 'console')
    monkeypatch.delenv('EMAIL_FROM', raising=False)
    monkeypatch.delenv('REDIS_URL', raising=False)

    client = TestClient(api_main.app)
    response = client.get('/health/diagnostics')

    assert response.status_code == 200
    payload = response.json()
    assert payload['status'] == 'not_ready'
    assert payload['checks']['database_url']['ok'] is False
    assert payload['checks']['email_provider_not_console']['ok'] is False
    assert payload['checks']['redis_url']['ok'] is False


def test_health_readiness_reports_healthy_when_production_requirements_are_met(api_main, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv('APP_ENV', 'production')
    monkeypatch.setenv('APP_MODE', 'production')
    monkeypatch.setenv('LIVE_MODE_ENABLED', 'true')
    monkeypatch.setenv('DATABASE_URL', 'postgresql://example')
    monkeypatch.setenv('AUTH_TOKEN_SECRET', 'secret')
    monkeypatch.setenv('EMAIL_PROVIDER', 'resend')
    monkeypatch.setenv('EMAIL_FROM', 'ops@decoda.app')
    monkeypatch.setenv('EMAIL_RESEND_API_KEY', 're_123')
    monkeypatch.setenv('REDIS_URL', 'redis://localhost:6379/0')
    monkeypatch.setenv('BILLING_ENABLED', 'true')
    monkeypatch.setenv('BILLING_PROVIDER', 'paddle')
    monkeypatch.setenv('PADDLE_API_KEY', 'pdl_api_123')
    monkeypatch.setenv('PADDLE_WEBHOOK_SECRET', 'pdl_whsec_123')
    monkeypatch.setenv('PADDLE_PRICE_ID_PRO', 'pri_123')

    client = TestClient(api_main.app)
    response = client.get('/health/readiness')

    assert response.status_code == 200
    assert response.json()['status'] == 'healthy'
