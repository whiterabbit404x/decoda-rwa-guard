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
    monkeypatch.setenv('LIVE_MODE_ENABLED', 'true')
    monkeypatch.setattr(api_main, 'ALLOWED_ORIGINS', ['https://web.decoda.example', 'https://ops.decoda.example'])
    monkeypatch.setattr(api_main, 'database_url', lambda: None)

    diagnostics = api_main.fixture_diagnostics()

    assert diagnostics['config'] == {
        'app_mode': 'production',
        'live_mode_enabled': False,
        'auth_token_secret_configured': False,
        'database_url_configured': False,
        'allowed_origins': ['https://web.decoda.example', 'https://ops.decoda.example'],
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
    assert response.json() == {
        'code': 'pilot_schema_missing',
        'detail': 'Pilot auth schema is not initialized. Missing required tables: users. Run services/api/scripts/migrate.py before using live auth routes.',
        'message': 'Pilot auth schema is not initialized. Missing required tables: users. Run services/api/scripts/migrate.py before using live auth routes.',
        'missingTables': ['users'],
        'pilotSchemaReady': False,
        'schemaDiagnostics': {
            'ready': False,
            'status': 'missing_tables',
            'missing_tables': ['users'],
            'required_tables': [
                'users',
                'workspaces',
                'workspace_members',
                'auth_sessions',
                'analysis_runs',
                'alerts',
                'governance_actions',
                'incidents',
                'audit_logs',
            ],
        },
    }


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
    assert diagnostics['demoSeedPresent'] is True
    assert diagnostics['pilotSchemaDiagnostics']['required_tables'] == ['users', 'workspaces']
    assert diagnostics['demoSeedDiagnostics']['membership_present'] is True
    assert diagnostics['embeddedThreatReady'] is False
    assert diagnostics['embeddedComplianceReady'] is True
    assert diagnostics['embeddedResilienceReady'] is True
    assert diagnostics['embeddedRiskReady'] is True
    assert diagnostics['lastEmbeddedFailureReason']['threat'] == 'embedded threat failure'
