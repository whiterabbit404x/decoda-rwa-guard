from __future__ import annotations

import importlib.util
import sys
from contextlib import contextmanager
from pathlib import Path

import pytest
from fastapi import HTTPException, Request
from fastapi.testclient import TestClient

REPO_ROOT = Path(__file__).resolve().parents[3]
API_MAIN_PATH = Path(__file__).resolve().parents[1] / 'app' / 'main.py'
PILOT_PATH = Path(__file__).resolve().parents[1] / 'app' / 'pilot.py'

sys.path.insert(0, str(REPO_ROOT))


@pytest.fixture(scope='module')
def api_main():
    spec = importlib.util.spec_from_file_location('phase1_api_auth_diag_main', API_MAIN_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError('Unable to load API module for auth diagnostics tests.')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope='module')
def pilot_module():
    spec = importlib.util.spec_from_file_location('phase1_api_auth_diag_pilot', PILOT_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError('Unable to load pilot module for auth diagnostics tests.')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_health_details_reports_safe_config_booleans_when_auth_secret_is_missing(api_main, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv('AUTH_TOKEN_SECRET', raising=False)
    monkeypatch.delenv('JWT_SECRET', raising=False)
    monkeypatch.delenv('DATABASE_URL', raising=False)
    monkeypatch.setenv('APP_MODE', 'production')
    monkeypatch.setenv('APP_ENV', 'production')
    monkeypatch.setenv('LIVE_MODE_ENABLED', 'true')
    monkeypatch.setattr(api_main, 'ALLOWED_ORIGINS', ['https://web.decoda.example', 'https://ops.decoda.example'])
    monkeypatch.setattr(api_main, 'CORS_ALLOW_CREDENTIALS', False)
    monkeypatch.setattr(api_main, 'database_url', lambda: None)

    diagnostics = api_main.fixture_diagnostics()

    assert diagnostics['config'] == {
        'app_mode': 'production',
        'live_mode_enabled': False,
        'auth_token_secret_configured': False,
        'database_url_configured': False,
        'allowed_origins': ['https://web.decoda.example', 'https://ops.decoda.example'],
        'cors_allow_credentials': False,
    }


def test_token_secret_raises_clear_error_when_auth_token_secret_is_missing(pilot_module, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv('AUTH_TOKEN_SECRET', raising=False)
    monkeypatch.delenv('JWT_SECRET', raising=False)

    with pytest.raises(HTTPException) as exc_info:
        pilot_module.token_secret()

    assert exc_info.value.status_code == 500
    assert exc_info.value.detail == 'AUTH_TOKEN_SECRET is not configured.'


def test_pilot_signin_raises_clear_schema_error_when_users_table_is_missing(pilot_module, monkeypatch: pytest.MonkeyPatch) -> None:
    class _Result:
        def __init__(self, rows):
            self._rows = rows

        def fetchall(self):
            return self._rows

    class _Connection:
        def execute(self, statement, params=None):
            if 'unnest' in statement:
                return _Result([{'table_name': 'users'}])
            raise AssertionError(f'unexpected SQL executed after schema check: {statement}')

    @contextmanager
    def fake_pg_connection():
        yield _Connection()

    monkeypatch.setattr(pilot_module, 'require_live_mode', lambda: None)
    monkeypatch.setattr(pilot_module, 'pg_connection', fake_pg_connection)

    with pytest.raises(HTTPException) as exc_info:
        pilot_module.signin_user({'email': 'demo@decoda.app', 'password': 'PilotDemoPass123!'}, Request({'type': 'http', 'headers': []}))

    assert exc_info.value.status_code == 503
    assert 'Pilot auth schema is not initialized.' in str(exc_info.value.detail)
    assert 'users' in str(exc_info.value.detail)


def test_auth_signin_route_returns_json_schema_error_instead_of_500(api_main, monkeypatch: pytest.MonkeyPatch) -> None:
    client = TestClient(api_main.app)
    monkeypatch.setattr(api_main, 'enforce_auth_rate_limit', lambda request, action: None)

    def _raise_schema_error(payload, request):
        raise HTTPException(status_code=503, detail='Pilot auth schema is not initialized. Missing required tables: users.')

    monkeypatch.setattr(api_main, 'signin_user', _raise_schema_error)

    response = client.post('/auth/signin', json={'email': 'demo@decoda.app', 'password': 'PilotDemoPass123!'})

    assert response.status_code == 503
    payload = response.json()
    assert payload['code'] == 'pilot_schema_missing'
    assert payload['missingTables'] == ['users']
    assert payload['pilotSchemaReady'] is False
    assert payload['schemaDiagnostics']['status'] == 'missing_tables'
    assert payload['schemaDiagnostics']['missing_tables'] == ['users']
    assert 'users' in payload['schemaDiagnostics']['required_tables']


def test_auth_signin_route_returns_graceful_json_when_auth_db_is_unavailable(api_main, monkeypatch: pytest.MonkeyPatch) -> None:
    client = TestClient(api_main.app)
    monkeypatch.setattr(api_main, 'enforce_auth_rate_limit', lambda request, action: None)

    def _raise_db_degraded(payload, request):
        raise HTTPException(
            status_code=503,
            detail='Authentication is temporarily unavailable. Please retry in a moment.',
            headers={
                'X-Decoda-Error-Code': 'AUTH_DB_QUOTA_EXCEEDED',
                'X-Decoda-DB-Classification': 'quota_exceeded',
            },
        )

    monkeypatch.setattr(api_main, 'signin_user', _raise_db_degraded)

    response = client.post('/auth/signin', json={'email': 'demo@decoda.app', 'password': 'PilotDemoPass123!'})

    assert response.status_code == 503
    assert response.json() == {
        'code': 'AUTH_DB_QUOTA_EXCEEDED',
        'detail': 'Authentication is temporarily unavailable. Please retry in a moment.',
        'message': 'Authentication is temporarily unavailable. Please retry in a moment.',
        'retryable': True,
        'classification': 'quota_exceeded',
    }


def test_auth_signin_route_supports_generic_postgres_dsn_without_neon_host(api_main, monkeypatch: pytest.MonkeyPatch) -> None:
    client = TestClient(api_main.app)
    monkeypatch.setattr(api_main, 'enforce_auth_rate_limit', lambda request, action: None)
    monkeypatch.setattr(api_main, 'database_url', lambda: 'postgresql://pilot:pilot@db.internal.local:5432/decoda')

    def _raise_db_unavailable(payload, request):
        raise HTTPException(
            status_code=503,
            detail='Authentication is temporarily unavailable. Please retry in a moment.',
            headers={
                'X-Decoda-Error-Code': 'AUTH_BACKEND_UNAVAILABLE',
                'X-Decoda-DB-Classification': 'db_unavailable',
            },
        )

    monkeypatch.setattr(api_main, 'signin_user', _raise_db_unavailable)

    response = client.post('/auth/signin', json={'email': 'demo@decoda.app', 'password': 'PilotDemoPass123!'})
    payload = response.json()

    assert response.status_code == 503
    assert payload['code'] == 'AUTH_BACKEND_UNAVAILABLE'
    assert payload['classification'] == 'db_unavailable'
    assert '.neon.tech' not in str(payload)


def test_health_endpoint_masks_database_url(api_main, monkeypatch: pytest.MonkeyPatch) -> None:
    client = TestClient(api_main.app)
    monkeypatch.setattr(api_main, 'database_url', lambda: 'postgres://user:pass@example.internal:5432/app')
    monkeypatch.setattr(api_main, 'runtime_environment_identity', lambda: {'database_backend': 'postgres_hosted_other'})

    response = client.get('/health')
    payload = response.json()

    assert response.status_code == 200
    assert payload['database_url'] == '[configured]'
    assert payload['database_url_configured'] is True
    assert payload['database_backend'] == 'postgres_hosted_other'


def test_resolve_db_backend_classifies_sqlite_and_postgres_hosts(pilot_module, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv('DATABASE_URL', raising=False)
    assert pilot_module.resolve_db_backend() == 'sqlite'

    monkeypatch.setenv('DATABASE_URL', 'sqlite:///tmp/dev.db')
    assert pilot_module.resolve_db_backend() == 'sqlite'

    monkeypatch.setenv('DATABASE_URL', 'postgresql://user:pass@localhost:5432/app')
    assert pilot_module.resolve_db_backend() == 'postgres_local'

    monkeypatch.setenv('DATABASE_URL', 'postgresql://user:pass@monitoring.docker-local:5432/app')
    assert pilot_module.resolve_db_backend() == 'postgres_local'

    monkeypatch.setenv('DATABASE_URL', 'postgresql://user:pass@my-db.us-east-2.aws.neon.tech:5432/app')
    assert pilot_module.resolve_db_backend() == 'postgres_hosted_neon'

    monkeypatch.setenv('DATABASE_URL', 'postgresql://user:pass@db.example.internal:5432/app')
    assert pilot_module.resolve_db_backend() == 'postgres_hosted_other'


def test_health_details_reports_pilot_and_embedded_readiness_flags(api_main, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        api_main,
        'pilot_schema_status',
        lambda: {'ready': True, 'status': 'ready', 'missing_tables': [], 'required_tables': ['users', 'workspaces']},
    )
    monkeypatch.setattr(
        api_main,
        'demo_seed_status',
        lambda email='demo@decoda.app': {
            'present': True,
            'status': 'present',
            'email': email,
            'workspace_slug': 'decoda-demo-workspace',
            'user_present': True,
            'workspace_present': True,
            'membership_present': True,
        },
    )

    def _embedded(service_slug: str, operation: str):
        return {'ready': service_slug != 'threat-engine', 'reason': None if service_slug != 'threat-engine' else 'import collision fixed but runtime unavailable'}

    monkeypatch.setattr(api_main, 'embedded_service_health', _embedded)
    api_main.DEPENDENCY_RUNTIME_STATUS.clear()
    api_main.DEPENDENCY_RUNTIME_STATUS['threat_engine'] = {'last_error': 'embedded threat failure'}

    diagnostics = api_main.fixture_diagnostics()

    assert diagnostics['pilotSchemaReady'] is True
    assert diagnostics['missingPilotTables'] == []
    assert diagnostics['demoSeedPresent'] is True
    assert diagnostics['pilotSchemaDiagnostics']['required_tables'] == ['users', 'workspaces']
    assert diagnostics['demoSeedDiagnostics']['membership_present'] is True
    assert diagnostics['embeddedThreatReady'] is False
    assert diagnostics['embeddedComplianceReady'] is True
    assert diagnostics['embeddedResilienceReady'] is True
    assert diagnostics['embeddedRiskReady'] is True
    assert diagnostics['lastEmbeddedFailureReason']['threat'] == 'embedded threat failure'


def test_fixture_diagnostics_falls_back_when_monitoring_runtime_fails(api_main, monkeypatch: pytest.MonkeyPatch) -> None:
    import services.api.app.activity_providers as activity_providers

    def _raise_runtime_error():
        raise RuntimeError('monitoring helper unavailable')

    monkeypatch.setattr(activity_providers, 'monitoring_ingestion_runtime', _raise_runtime_error)

    diagnostics = api_main.fixture_diagnostics()

    assert diagnostics['monitoring_ingestion_mode'] == 'unknown'
    assert diagnostics['monitoring_ingestion_degraded'] is None
    assert diagnostics['monitoring_ingestion_reason'] is None


def test_emit_startup_fixture_diagnostics_never_raises(api_main, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(api_main, 'fixture_diagnostics', lambda: (_ for _ in ()).throw(RuntimeError('broken diagnostics')))

    api_main.emit_startup_fixture_diagnostics()


def test_health_readiness_reports_not_ready_when_production_dependencies_missing(api_main, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv('APP_MODE', 'production')
    monkeypatch.setenv('APP_ENV', 'production')
    monkeypatch.setenv('LIVE_MODE_ENABLED', 'true')
    monkeypatch.setenv('EMAIL_PROVIDER', 'console')
    monkeypatch.delenv('REDIS_URL', raising=False)
    monkeypatch.delenv('DATABASE_URL', raising=False)
    monkeypatch.setenv('AUTH_TOKEN_SECRET', 'secret')

    client = TestClient(api_main.app)
    response = client.get('/health/readiness')
    payload = response.json()

    assert response.status_code == 200
    assert payload['status'] == 'not_ready'
    assert any('EMAIL_PROVIDER=console is not allowed in production' in error for error in payload['errors'])
    assert any('REDIS_URL is required in production' in error for error in payload['errors'])


def test_health_details_route_reports_readiness_flags_and_missing_tables(api_main, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        api_main,
        'pilot_schema_status',
        lambda: {
            'ready': False,
            'status': 'missing_tables',
            'missing_tables': ['users', 'workspaces'],
            'required_tables': ['users', 'workspaces'],
        },
    )
    monkeypatch.setattr(
        api_main,
        'demo_seed_status',
        lambda email='demo@decoda.app': {'present': False, 'status': 'missing', 'email': email},
    )
    monkeypatch.setattr(api_main, 'STARTUP_BOOTSTRAP_STATUS', {'enabled': True, 'ran': True, 'applied_versions': ['0001_pilot_foundation.sql']})
    monkeypatch.setattr(api_main, 'embedded_service_health', lambda service_slug, operation: {'ready': True, 'reason': None})

    client = TestClient(api_main.app)
    response = client.get('/health/details')

    assert response.status_code == 200
    payload = response.json()
    assert payload['pilotSchemaReady'] is False
    assert payload['demoSeedPresent'] is False
    assert payload['missingPilotTables'] == ['users', 'workspaces']
    assert payload['startupBootstrap'] == {'enabled': True, 'ran': True, 'applied_versions': ['0001_pilot_foundation.sql']}
