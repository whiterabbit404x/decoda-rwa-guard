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
    monkeypatch.setenv('SECRET_ENCRYPTION_KEY', 'MDEyMzQ1Njc4OWFiY2RlZjAxMjM0NTY3ODlhYmNkZWY=')
    monkeypatch.setenv('EMAIL_PROVIDER', 'resend')
    monkeypatch.setenv('EMAIL_FROM', 'ops@decoda.app')
    monkeypatch.delenv('EMAIL_RESEND_API_KEY', raising=False)
    monkeypatch.setenv('REDIS_URL', 'redis://localhost:6379/0')
    monkeypatch.setenv('BILLING_ENABLED', 'false')

    payload = pilot.validate_runtime_configuration()

    assert 'EMAIL_RESEND_API_KEY is required when EMAIL_PROVIDER=resend in production.' in payload['errors']
    assert payload['checks']['email_resend_api_key']['ok'] is False


def test_validate_runtime_configuration_requires_live_mode_enabled_in_production(monkeypatch: pytest.MonkeyPatch) -> None:
    from services.api.app import pilot

    monkeypatch.setenv('APP_ENV', 'production')
    monkeypatch.setenv('LIVE_MODE_ENABLED', 'false')
    monkeypatch.setenv('DATABASE_URL', 'postgresql://example')
    monkeypatch.setenv('AUTH_TOKEN_SECRET', 'secret')
    monkeypatch.setenv('SECRET_ENCRYPTION_KEY', 'MDEyMzQ1Njc4OWFiY2RlZjAxMjM0NTY3ODlhYmNkZWY=')
    monkeypatch.setenv('EMAIL_PROVIDER', 'resend')
    monkeypatch.setenv('EMAIL_FROM', 'ops@decoda.app')
    monkeypatch.setenv('EMAIL_RESEND_API_KEY', 're_123')
    monkeypatch.setenv('REDIS_URL', 'redis://localhost:6379/0')
    monkeypatch.setenv('BILLING_ENABLED', 'false')

    payload = pilot.validate_runtime_configuration()
    expected_error = (
        'LIVE_MODE_ENABLED must be true in production. Disabling live mode in production forces monitoring runtime '
        'into offline/unconfigured fallback behavior.'
    )

    assert expected_error in payload['errors']
    assert payload['checks']['live_mode_enabled']['ok'] is False
    assert payload['checks']['database_url']['ok'] is True


def test_validate_runtime_configuration_accepts_live_mode_enabled_with_database_url_in_production(monkeypatch: pytest.MonkeyPatch) -> None:
    from services.api.app import pilot

    monkeypatch.setenv('APP_ENV', 'production')
    monkeypatch.setenv('LIVE_MODE_ENABLED', 'true')
    monkeypatch.setenv('DATABASE_URL', 'postgresql://example')
    monkeypatch.setenv('AUTH_TOKEN_SECRET', 'secret')
    monkeypatch.setenv('SECRET_ENCRYPTION_KEY', 'MDEyMzQ1Njc4OWFiY2RlZjAxMjM0NTY3ODlhYmNkZWY=')
    monkeypatch.setenv('EMAIL_PROVIDER', 'resend')
    monkeypatch.setenv('EMAIL_FROM', 'ops@decoda.app')
    monkeypatch.setenv('EMAIL_RESEND_API_KEY', 're_123')
    monkeypatch.setenv('REDIS_URL', 'redis://localhost:6379/0')
    monkeypatch.setenv('BILLING_ENABLED', 'false')

    payload = pilot.validate_runtime_configuration()

    assert payload['checks']['live_mode_enabled']['ok'] is True
    assert payload['checks']['database_url']['ok'] is True
    assert all('LIVE_MODE_ENABLED must be true in production.' not in error for error in payload['errors'])


def test_health_diagnostics_exposes_machine_readable_checks(api_main, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv('APP_ENV', 'production')
    monkeypatch.setenv('APP_MODE', 'production')
    monkeypatch.setenv('MONITORING_INGESTION_MODE', 'demo')
    monkeypatch.setenv('LIVE_MODE_ENABLED', 'true')
    monkeypatch.delenv('DATABASE_URL', raising=False)
    monkeypatch.setenv('AUTH_TOKEN_SECRET', 'secret')
    monkeypatch.setenv('SECRET_ENCRYPTION_KEY', 'MDEyMzQ1Njc4OWFiY2RlZjAxMjM0NTY3ODlhYmNkZWY=')
    monkeypatch.setenv('EMAIL_PROVIDER', 'console')
    monkeypatch.delenv('EMAIL_FROM', raising=False)
    monkeypatch.delenv('REDIS_URL', raising=False)

    client = TestClient(api_main.app)
    response = client.get('/health/diagnostics')

    assert response.status_code == 200
    payload = response.json()
    assert payload['status'] == 'not_ready'
    assert payload['production_live_mode_drift'] is False
    assert payload['checks']['database_url']['ok'] is False
    assert payload['checks']['email_provider_not_console']['ok'] is False
    assert payload['checks']['redis_url']['ok'] is False


def test_health_diagnostics_flags_production_live_mode_drift(api_main, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv('APP_ENV', 'production')
    monkeypatch.setenv('APP_MODE', 'production')
    monkeypatch.setenv('MONITORING_INGESTION_MODE', 'demo')
    monkeypatch.setenv('LIVE_MODE_ENABLED', 'false')
    monkeypatch.setenv('DATABASE_URL', 'postgresql://example')
    monkeypatch.setenv('AUTH_TOKEN_SECRET', 'secret')
    monkeypatch.setenv('SECRET_ENCRYPTION_KEY', 'MDEyMzQ1Njc4OWFiY2RlZjAxMjM0NTY3ODlhYmNkZWY=')
    monkeypatch.setenv('EMAIL_PROVIDER', 'resend')
    monkeypatch.setenv('EMAIL_FROM', 'ops@decoda.app')
    monkeypatch.setenv('EMAIL_RESEND_API_KEY', 're_123')
    monkeypatch.setenv('REDIS_URL', 'redis://localhost:6379/0')

    client = TestClient(api_main.app)
    response = client.get('/health/diagnostics')

    assert response.status_code == 200
    payload = response.json()
    assert payload['status'] == 'not_ready'
    assert payload['production_live_mode_drift'] is True
    assert payload['checks']['live_mode_enabled']['ok'] is False
    assert 'offline/unconfigured fallback behavior' in payload['checks']['live_mode_enabled']['detail']


def test_health_readiness_reports_healthy_when_production_requirements_are_met(api_main, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv('APP_ENV', 'production')
    monkeypatch.setenv('APP_MODE', 'production')
    monkeypatch.setenv('MONITORING_INGESTION_MODE', 'demo')
    monkeypatch.setenv('LIVE_MODE_ENABLED', 'true')
    monkeypatch.setenv('DATABASE_URL', 'postgresql://example')
    monkeypatch.setenv('AUTH_TOKEN_SECRET', 'secret')
    monkeypatch.setenv('SECRET_ENCRYPTION_KEY', 'MDEyMzQ1Njc4OWFiY2RlZjAxMjM0NTY3ODlhYmNkZWY=')
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
    payload = response.json()
    assert payload['status'] == 'healthy'
    assert payload['production_live_mode_drift'] is False


def test_health_readiness_stays_healthy_when_billing_provider_none(api_main, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv('APP_ENV', 'production')
    monkeypatch.setenv('APP_MODE', 'production')
    monkeypatch.setenv('MONITORING_INGESTION_MODE', 'demo')
    monkeypatch.setenv('LIVE_MODE_ENABLED', 'true')
    monkeypatch.setenv('DATABASE_URL', 'postgresql://example')
    monkeypatch.setenv('AUTH_TOKEN_SECRET', 'secret')
    monkeypatch.setenv('SECRET_ENCRYPTION_KEY', 'MDEyMzQ1Njc4OWFiY2RlZjAxMjM0NTY3ODlhYmNkZWY=')
    monkeypatch.setenv('EMAIL_PROVIDER', 'resend')
    monkeypatch.setenv('EMAIL_FROM', 'ops@decoda.app')
    monkeypatch.setenv('EMAIL_RESEND_API_KEY', 're_123')
    monkeypatch.setenv('REDIS_URL', 'redis://localhost:6379/0')
    monkeypatch.setenv('BILLING_PROVIDER', 'none')
    monkeypatch.delenv('STRICT_PRODUCTION_BILLING', raising=False)
    monkeypatch.delenv('PADDLE_API_KEY', raising=False)
    monkeypatch.delenv('PADDLE_WEBHOOK_SECRET', raising=False)
    monkeypatch.delenv('PADDLE_PRICE_ID_PRO', raising=False)

    client = TestClient(api_main.app)
    response = client.get('/health/readiness')
    payload = response.json()

    assert response.status_code == 200
    assert payload['status'] == 'healthy'
    assert payload['billing']['provider'] == 'none'
    assert payload['billing']['status'] == 'not_configured'
    assert payload['checks']['billing_runtime']['ok'] is False


def test_strict_billing_mode_marks_readiness_not_ready_when_billing_missing(api_main, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv('APP_ENV', 'production')
    monkeypatch.setenv('APP_MODE', 'production')
    monkeypatch.setenv('MONITORING_INGESTION_MODE', 'demo')
    monkeypatch.setenv('LIVE_MODE_ENABLED', 'true')
    monkeypatch.setenv('DATABASE_URL', 'postgresql://example')
    monkeypatch.setenv('AUTH_TOKEN_SECRET', 'secret')
    monkeypatch.setenv('SECRET_ENCRYPTION_KEY', 'MDEyMzQ1Njc4OWFiY2RlZjAxMjM0NTY3ODlhYmNkZWY=')
    monkeypatch.setenv('EMAIL_PROVIDER', 'resend')
    monkeypatch.setenv('EMAIL_FROM', 'ops@decoda.app')
    monkeypatch.setenv('EMAIL_RESEND_API_KEY', 're_123')
    monkeypatch.setenv('REDIS_URL', 'redis://localhost:6379/0')
    monkeypatch.setenv('BILLING_PROVIDER', 'paddle')
    monkeypatch.setenv('STRICT_PRODUCTION_BILLING', 'true')
    monkeypatch.delenv('PADDLE_API_KEY', raising=False)
    monkeypatch.delenv('PADDLE_WEBHOOK_SECRET', raising=False)

    client = TestClient(api_main.app)
    response = client.get('/health/readiness')
    payload = response.json()

    assert response.status_code == 200
    assert payload['status'] == 'not_ready'
    assert payload['checks']['billing_runtime']['required'] is True


def test_non_billing_health_endpoint_still_works_when_billing_unconfigured(api_main, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv('BILLING_PROVIDER', 'none')
    monkeypatch.delenv('PADDLE_API_KEY', raising=False)
    monkeypatch.delenv('PADDLE_WEBHOOK_SECRET', raising=False)
    client = TestClient(api_main.app)
    response = client.get('/health')
    assert response.status_code == 200
    payload = response.json()
    assert payload['status'] == 'ok'
    assert payload['billing']['status'] == 'not_configured'


def test_run_startup_migrations_enabled_path_supports_local_postgres(monkeypatch: pytest.MonkeyPatch) -> None:
    from services.api.app import pilot

    class _Conn:
        pass

    class _Ctx:
        def __enter__(self):
            return _Conn()

        def __exit__(self, exc_type, exc, tb):
            return False

    ensure_calls: list[object] = []

    monkeypatch.setenv('APP_MODE', 'local')
    monkeypatch.setenv('LIVE_MODE_ENABLED', 'true')
    monkeypatch.setenv('DATABASE_URL', 'postgresql://pilot:pilot@localhost:5432/decoda')
    monkeypatch.setenv('RUN_MIGRATIONS_ON_STARTUP', 'true')
    monkeypatch.setattr(pilot, 'run_migrations', lambda: ['0041_local_monitoring.sql'])
    monkeypatch.setattr(pilot, 'pg_connection', lambda: _Ctx())
    monkeypatch.setattr(pilot, 'ensure_pilot_schema', lambda connection: ensure_calls.append(connection))
    monkeypatch.setattr(pilot, '_fetch_missing_runtime_schema_columns', lambda connection: {})

    payload = pilot.run_startup_migrations_if_enabled(process_role='api')

    assert payload['enabled'] is True
    assert payload['ran'] is True
    assert payload['applied_versions'] == ['0041_local_monitoring.sql']
    assert ensure_calls


def test_validate_runtime_configuration_accepts_local_postgres_without_neon_host_requirement(monkeypatch: pytest.MonkeyPatch) -> None:
    from services.api.app import pilot

    monkeypatch.setenv('APP_ENV', 'production')
    monkeypatch.setenv('APP_MODE', 'local')
    monkeypatch.setenv('LIVE_MODE_ENABLED', 'true')
    monkeypatch.setenv('DATABASE_URL', 'postgresql://pilot:pilot@db.internal.local:5432/decoda')
    monkeypatch.setenv('AUTH_TOKEN_SECRET', 'secret')
    monkeypatch.setenv('SECRET_ENCRYPTION_KEY', 'MDEyMzQ1Njc4OWFiY2RlZjAxMjM0NTY3ODlhYmNkZWY=')
    monkeypatch.setenv('EMAIL_PROVIDER', 'resend')
    monkeypatch.setenv('EMAIL_FROM', 'ops@decoda.app')
    monkeypatch.setenv('EMAIL_RESEND_API_KEY', 're_123')
    monkeypatch.setenv('REDIS_URL', 'redis://localhost:6379/0')
    monkeypatch.setenv('BILLING_ENABLED', 'false')

    payload = pilot.validate_runtime_configuration()

    assert payload['checks']['database_backend_postgres']['ok'] is True
    assert payload['checks']['database_url']['ok'] is True
    assert payload['errors'] == []
    assert all('.neon.tech' not in error for error in payload['errors'])
