"""
Enterprise security audit tests — P0/P1/P2 coverage.

Tests prove:
- Unauthenticated requests to dashboard/ops routes return 401/403.
- Debug endpoints blocked in production unless ENABLE_DEBUG_ENDPOINTS=true.
- Ops mutation routes require admin auth.
- SQL identifier validation rejects malicious inputs.
- Startup config rejects missing/weak secrets in production.
- Dev fallback signing secret cannot be used in production.
- Production compliance fallback does not contain hardcoded test wallet.
"""
from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path
from typing import Any

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT))

API_MAIN_PATH = Path(__file__).resolve().parents[1] / 'app' / 'main.py'
PILOT_PATH = Path(__file__).resolve().parents[1] / 'app' / 'pilot.py'
TENANT_ISOLATION_PATH = Path(__file__).resolve().parents[1] / 'app' / 'tenant_isolation.py'
EVIDENCE_SIGNING_PATH = Path(__file__).resolve().parents[1] / 'app' / 'evidence_signing.py'


@pytest.fixture(scope='module')
def api_main():
    spec = importlib.util.spec_from_file_location('enterprise_sec_api_main', API_MAIN_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope='module')
def pilot_module():
    spec = importlib.util.spec_from_file_location('enterprise_sec_pilot', PILOT_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope='module')
def tenant_isolation_module():
    spec = importlib.util.spec_from_file_location('enterprise_sec_tenant_isolation', TENANT_ISOLATION_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope='module')
def evidence_signing_module():
    spec = importlib.util.spec_from_file_location('enterprise_sec_evidence_signing', EVIDENCE_SIGNING_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ---------------------------------------------------------------------------
# P0-1: Dashboard routes require authentication
# ---------------------------------------------------------------------------

UNAUTHENTICATED_DASHBOARD_ROUTES = [
    ('GET', '/risk/dashboard'),
    ('GET', '/threat/dashboard'),
    ('POST', '/threat/analyze/contract'),
    ('POST', '/threat/analyze/transaction'),
    ('POST', '/threat/analyze/market'),
    ('GET', '/compliance/dashboard'),
    ('POST', '/compliance/screen/transfer'),
    ('POST', '/compliance/screen/residency'),
    ('GET', '/compliance/policy/state'),
    ('GET', '/compliance/governance/actions'),
    ('POST', '/compliance/governance/actions'),
    ('GET', '/resilience/dashboard'),
    ('POST', '/resilience/reconcile/state'),
    ('POST', '/resilience/backstop/evaluate'),
    ('POST', '/resilience/incidents/record'),
    ('GET', '/resilience/incidents'),
]


@pytest.mark.parametrize('method,path', UNAUTHENTICATED_DASHBOARD_ROUTES)
def test_unauthenticated_dashboard_route_blocked(method: str, path: str, api_main, monkeypatch: pytest.MonkeyPatch) -> None:
    """Unauthenticated requests to product dashboard routes must return 401."""
    def _raise_401(request: Any) -> None:
        raise HTTPException(status_code=401, detail='Missing bearer token.')

    monkeypatch.setattr(api_main, 'authenticate_request', _raise_401)
    client = TestClient(api_main.app, raise_server_exceptions=False)

    if method == 'GET':
        response = client.get(path)
    else:
        response = client.post(path, json={})

    assert response.status_code in (401, 403), (
        f'{method} {path} must return 401 or 403 for unauthenticated requests, got {response.status_code}'
    )


def test_unauthenticated_compliance_governance_action_detail_blocked(api_main, monkeypatch: pytest.MonkeyPatch) -> None:
    """Unauthenticated GET /compliance/governance/actions/{id} must return 401."""
    def _raise_401(request: Any) -> None:
        raise HTTPException(status_code=401, detail='Missing bearer token.')

    monkeypatch.setattr(api_main, 'authenticate_request', _raise_401)
    client = TestClient(api_main.app, raise_server_exceptions=False)
    response = client.get('/compliance/governance/actions/some-action-id')
    assert response.status_code in (401, 403)


def test_unauthenticated_resilience_incident_detail_blocked(api_main, monkeypatch: pytest.MonkeyPatch) -> None:
    """Unauthenticated GET /resilience/incidents/{id} must return 401."""
    def _raise_401(request: Any) -> None:
        raise HTTPException(status_code=401, detail='Missing bearer token.')

    monkeypatch.setattr(api_main, 'authenticate_request', _raise_401)
    client = TestClient(api_main.app, raise_server_exceptions=False)
    response = client.get('/resilience/incidents/some-event-id')
    assert response.status_code in (401, 403)


def test_authenticated_risk_dashboard_returns_degraded_truthful_labels(api_main, monkeypatch: pytest.MonkeyPatch) -> None:
    """Authenticated access must still return truthful source/degraded fields on fallback."""
    _FALLBACK_QUEUE = [{'live_data': False, 'updated_at': '2026-01-01T00:00:00Z'}]

    def _fake_auth(request: Any) -> dict:
        return {'id': 'user-1', 'email': 'test@example.com'}

    monkeypatch.setattr(api_main, 'authenticate_request', _fake_auth)
    monkeypatch.setattr(api_main, 'build_risk_dashboard_queue', lambda: _FALLBACK_QUEUE)
    monkeypatch.setattr(api_main, 'build_risk_summary', lambda q: {})
    monkeypatch.setattr(api_main, 'serialize_queue_item', lambda item: item)
    monkeypatch.setattr(api_main, 'build_risk_alerts', lambda q: [])
    monkeypatch.setattr(api_main, 'build_contract_scan_results', lambda q: [])
    monkeypatch.setattr(api_main, 'build_decisions_log', lambda q: [])
    monkeypatch.setattr(api_main, 'attach_dependency_diagnostics', lambda p, *a, **kw: p)
    monkeypatch.setattr(api_main, 'record_dependency_runtime', lambda *a, **kw: None)
    monkeypatch.setattr(api_main, 'dependency_mode', lambda name: 'fallback')

    client = TestClient(api_main.app)
    response = client.get('/risk/dashboard', headers={'Authorization': 'Bearer fake-token'})
    assert response.status_code == 200
    body = response.json()
    assert body.get('degraded') is True
    assert body.get('source') == 'fallback'


# ---------------------------------------------------------------------------
# P0-2: Debug endpoints blocked in production
# ---------------------------------------------------------------------------

def test_debug_fixtures_blocked_in_production(api_main, monkeypatch: pytest.MonkeyPatch) -> None:
    """GET /debug/fixtures must return 404 in production when ENABLE_DEBUG_ENDPOINTS is not set."""
    monkeypatch.setenv('APP_ENV', 'production')
    monkeypatch.delenv('ENABLE_DEBUG_ENDPOINTS', raising=False)
    client = TestClient(api_main.app, raise_server_exceptions=False)
    response = client.get('/debug/fixtures')
    assert response.status_code == 404


def test_debug_downstream_status_blocked_in_production(api_main, monkeypatch: pytest.MonkeyPatch) -> None:
    """GET /debug/downstream-status must return 404 in production."""
    monkeypatch.setenv('APP_ENV', 'production')
    monkeypatch.delenv('ENABLE_DEBUG_ENDPOINTS', raising=False)
    client = TestClient(api_main.app, raise_server_exceptions=False)
    response = client.get('/debug/downstream-status')
    assert response.status_code == 404


def test_debug_fixtures_allowed_in_dev_mode(api_main, monkeypatch: pytest.MonkeyPatch) -> None:
    """GET /debug/fixtures must be accessible in development mode."""
    monkeypatch.setenv('APP_ENV', 'development')
    monkeypatch.delenv('ENABLE_DEBUG_ENDPOINTS', raising=False)
    monkeypatch.setattr(api_main, 'fixture_diagnostics', lambda: {'mode': 'development'})
    client = TestClient(api_main.app)
    response = client.get('/debug/fixtures')
    assert response.status_code == 200


def test_debug_fixtures_blocked_in_production_even_with_explicit_false(api_main, monkeypatch: pytest.MonkeyPatch) -> None:
    """ENABLE_DEBUG_ENDPOINTS=false must block debug endpoints in production."""
    monkeypatch.setenv('APP_ENV', 'production')
    monkeypatch.setenv('ENABLE_DEBUG_ENDPOINTS', 'false')
    client = TestClient(api_main.app, raise_server_exceptions=False)
    response = client.get('/debug/fixtures')
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# P0-3: Ops mutation routes require admin auth
# ---------------------------------------------------------------------------

def test_unauthenticated_ops_jobs_run_blocked(api_main, monkeypatch: pytest.MonkeyPatch) -> None:
    """POST /ops/jobs/run must return 401 without authentication."""
    def _raise_401(*args, **kwargs) -> None:
        raise HTTPException(status_code=401, detail='Missing bearer token.')

    monkeypatch.setattr(api_main, 'require_ops_rbac_guard', _raise_401)
    # Mock the rate limiter to not fail on missing redis
    monkeypatch.setattr(api_main, 'enforce_auth_rate_limit', lambda *a, **kw: None)
    monkeypatch.setattr(api_main, 'pg_connection', _fake_pg_context)
    monkeypatch.setattr(api_main, 'ensure_pilot_schema', lambda *a: None)
    client = TestClient(api_main.app, raise_server_exceptions=False)
    response = client.post('/ops/jobs/run', json={})
    assert response.status_code in (401, 403)


def test_unauthenticated_ops_monitoring_run_blocked(api_main, monkeypatch: pytest.MonkeyPatch) -> None:
    """POST /ops/monitoring/run must return 401 without authentication."""
    def _raise_401(*args, **kwargs) -> None:
        raise HTTPException(status_code=401, detail='Missing bearer token.')

    monkeypatch.setattr(api_main, 'require_ops_rbac_guard', _raise_401)
    monkeypatch.setattr(api_main, 'enforce_auth_rate_limit', lambda *a, **kw: None)
    monkeypatch.setattr(api_main, 'pg_connection', _fake_pg_context)
    monkeypatch.setattr(api_main, 'ensure_pilot_schema', lambda *a: None)
    client = TestClient(api_main.app, raise_server_exceptions=False)
    response = client.post('/ops/monitoring/run', json={})
    assert response.status_code in (401, 403)


def test_non_admin_ops_jobs_run_blocked(api_main, monkeypatch: pytest.MonkeyPatch) -> None:
    """POST /ops/jobs/run must return 403 for non-admin authenticated users."""
    def _raise_403(*args, **kwargs) -> None:
        raise HTTPException(status_code=403, detail='Owner or admin role is required for ops monitoring actions.')

    monkeypatch.setattr(api_main, 'require_ops_rbac_guard', _raise_403)
    monkeypatch.setattr(api_main, 'enforce_auth_rate_limit', lambda *a, **kw: None)
    monkeypatch.setattr(api_main, 'pg_connection', _fake_pg_context)
    monkeypatch.setattr(api_main, 'ensure_pilot_schema', lambda *a: None)
    client = TestClient(api_main.app, raise_server_exceptions=False)
    response = client.post('/ops/jobs/run', json={}, headers={'Authorization': 'Bearer viewer-token'})
    assert response.status_code in (401, 403)


# ---------------------------------------------------------------------------
# P1-1: SQL identifier validation
# ---------------------------------------------------------------------------

def test_validate_identifier_allows_safe_table_names(tenant_isolation_module) -> None:
    valid_names = ['users', 'workspace_members', 'monitoring_configs', 'telemetry_events']
    for name in valid_names:
        result = tenant_isolation_module.validate_identifier(name)
        assert result == name


def test_validate_identifier_rejects_sql_injection_table_names(tenant_isolation_module) -> None:
    malicious_names = [
        "users; DROP TABLE users--",
        "users WHERE 1=1--",
        "users UNION SELECT * FROM secrets--",
        "users'",
        'users"',
        'users OR 1=1',
        '1=1',
        ' users',
        'USERS',
    ]
    for name in malicious_names:
        with pytest.raises(ValueError):
            tenant_isolation_module.validate_identifier(name)


def test_validate_identifier_rejects_malicious_id_col(tenant_isolation_module) -> None:
    with pytest.raises(ValueError):
        tenant_isolation_module.validate_identifier("id; DROP TABLE users--", 'id_col')


def test_validate_identifier_rejects_uppercase_identifiers(tenant_isolation_module) -> None:
    with pytest.raises(ValueError):
        tenant_isolation_module.validate_identifier('Users')


def test_pilot_validate_sql_identifier_rejects_malicious_table(pilot_module) -> None:
    with pytest.raises(ValueError):
        pilot_module._validate_sql_identifier("users; DROP TABLE users--")


def test_pilot_validate_sql_identifier_allows_safe_table(pilot_module) -> None:
    pilot_module._validate_sql_identifier('monitoring_configs')
    pilot_module._validate_sql_identifier('telemetry_events')


# ---------------------------------------------------------------------------
# P1-2: Startup config secret assertions (production mode)
# ---------------------------------------------------------------------------

def test_production_startup_fails_without_export_signing_secret(pilot_module, monkeypatch: pytest.MonkeyPatch) -> None:
    """validate_runtime_configuration must report error when EXPORT_SIGNING_SECRET missing in production."""
    monkeypatch.setenv('APP_ENV', 'production')
    monkeypatch.setenv('APP_MODE', 'production')
    monkeypatch.delenv('EXPORT_SIGNING_SECRET', raising=False)
    monkeypatch.delenv('EVIDENCE_SIGNING_SECRET', raising=False)
    monkeypatch.setattr(pilot_module, 'auth_token_secret_configured', lambda: True)
    monkeypatch.setattr(pilot_module, 'validate_encryption_bootstrap', lambda: None)
    monkeypatch.setattr(pilot_module, 'billing_runtime_status', lambda: {'available': True, 'configured': True})
    monkeypatch.setattr(pilot_module, 'database_url', lambda: 'postgresql://localhost/test')
    monkeypatch.setattr(pilot_module, 'runtime_mode_config_summary', lambda: {
        'live_mode_requested': True,
        'backend_classification': 'postgres',
    })

    result = pilot_module.validate_runtime_configuration()
    assert len(result['errors']) > 0
    error_text = ' '.join(result['errors'])
    assert 'EXPORT_SIGNING_SECRET' in error_text or 'signing' in error_text.lower()


def test_production_startup_fails_with_weak_auth_secret(pilot_module, monkeypatch: pytest.MonkeyPatch) -> None:
    """validate_runtime_configuration must error when AUTH_TOKEN_SECRET is a known weak value."""
    monkeypatch.setenv('APP_ENV', 'production')
    monkeypatch.setenv('APP_MODE', 'production')
    monkeypatch.setenv('AUTH_TOKEN_SECRET', 'changeme')
    monkeypatch.setenv('EXPORT_SIGNING_SECRET', 'a-strong-random-production-secret-here')
    monkeypatch.setattr(pilot_module, 'auth_token_secret_configured', lambda: True)
    monkeypatch.setattr(pilot_module, 'validate_encryption_bootstrap', lambda: None)
    monkeypatch.setattr(pilot_module, 'billing_runtime_status', lambda: {'available': True, 'configured': True})
    monkeypatch.setattr(pilot_module, 'database_url', lambda: 'postgresql://localhost/test')
    monkeypatch.setattr(pilot_module, 'runtime_mode_config_summary', lambda: {
        'live_mode_requested': True,
        'backend_classification': 'postgres',
    })

    result = pilot_module.validate_runtime_configuration()
    assert len(result['errors']) > 0
    error_text = ' '.join(result['errors'])
    assert 'AUTH_TOKEN_SECRET' in error_text or 'weak' in error_text.lower() or 'default' in error_text.lower()


# ---------------------------------------------------------------------------
# P1-3: Dev fallback signing secret cannot be used in production
# ---------------------------------------------------------------------------

def test_evidence_signing_dev_fallback_blocked_in_production_via_app_env(
    evidence_signing_module, monkeypatch: pytest.MonkeyPatch
) -> None:
    """_require_signing_secret must raise RuntimeError when APP_ENV=production and no secret set."""
    monkeypatch.setenv('APP_ENV', 'production')
    monkeypatch.setenv('APP_MODE', '')
    monkeypatch.delenv('EXPORT_SIGNING_SECRET', raising=False)
    monkeypatch.delenv('EVIDENCE_SIGNING_SECRET', raising=False)

    with pytest.raises(RuntimeError, match='EXPORT_SIGNING_SECRET'):
        evidence_signing_module._require_signing_secret()


def test_evidence_signing_dev_fallback_blocked_in_staging_via_app_mode(
    evidence_signing_module, monkeypatch: pytest.MonkeyPatch
) -> None:
    """_require_signing_secret must raise RuntimeError when APP_MODE=staging and no secret set."""
    monkeypatch.setenv('APP_MODE', 'staging')
    monkeypatch.setenv('APP_ENV', '')
    monkeypatch.delenv('EXPORT_SIGNING_SECRET', raising=False)
    monkeypatch.delenv('EVIDENCE_SIGNING_SECRET', raising=False)

    with pytest.raises(RuntimeError, match='EXPORT_SIGNING_SECRET'):
        evidence_signing_module._require_signing_secret()


def test_evidence_signing_dev_fallback_allowed_in_dev_mode(
    evidence_signing_module, monkeypatch: pytest.MonkeyPatch
) -> None:
    """_require_signing_secret must return dev fallback in local/dev mode without error."""
    monkeypatch.setenv('APP_ENV', 'development')
    monkeypatch.setenv('APP_MODE', 'local')
    monkeypatch.delenv('EXPORT_SIGNING_SECRET', raising=False)
    monkeypatch.delenv('EVIDENCE_SIGNING_SECRET', raising=False)

    secret, is_production_secret = evidence_signing_module._require_signing_secret()
    assert secret == evidence_signing_module._DEV_FALLBACK_SECRET
    assert is_production_secret is False


# ---------------------------------------------------------------------------
# P2-4: Production compliance fallback does not contain hardcoded test wallet
# ---------------------------------------------------------------------------

def test_fallback_transfer_screening_no_hardcoded_test_wallet(api_main) -> None:
    """fallback_transfer_screening must not use hardcoded test wallet address."""
    import inspect
    source = inspect.getsource(api_main.fallback_transfer_screening)
    assert '0xblocked000000000000000000000000000000003' not in source, (
        'Hardcoded test wallet 0xblocked000000000000000000000000000000003 must not appear in '
        'production fallback_transfer_screening. Move test fixtures to test files.'
    )


def test_fallback_transfer_screening_blocks_based_on_policy_flags(api_main) -> None:
    """fallback_transfer_screening must block when sender_blocklist_match flag is set."""
    payload = {
        'sender_blocklist_match': True,
        'sender_kyc_status': 'verified',
        'receiver_kyc_status': 'verified',
        'asset_transfer_policy': {},
    }
    result = api_main.fallback_transfer_screening(payload)
    assert result['decision'] == 'blocked'
    assert result['source'] == 'fallback'
    assert result['degraded'] is True


def test_fallback_transfer_screening_approved_when_no_flags(api_main) -> None:
    """fallback_transfer_screening must approve clean transfers."""
    payload = {
        'sender_kyc_status': 'verified',
        'receiver_kyc_status': 'verified',
        'asset_transfer_policy': {},
    }
    result = api_main.fallback_transfer_screening(payload)
    assert result['decision'] == 'approved'
    assert result['source'] == 'fallback'


# ---------------------------------------------------------------------------
# Helper: fake pg_connection context manager for tests
# ---------------------------------------------------------------------------

from contextlib import contextmanager


@contextmanager
def _fake_pg_context():
    yield None
