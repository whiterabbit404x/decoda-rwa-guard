"""
Tests: production /health never calls phase1_local.dev_support (SQLite).

Root cause (railway crash): dependency_debug_snapshot() called load_all_services()
unconditionally. In production with DATABASE_URL=postgresql://..., resolve_sqlite_path()
raised RuntimeError, bubbling to /health as HTTP 500.

Fixes verified here:
  - /health does not call load_all_services in production
  - /health returns 200 when DB dependency is healthy in production
  - /health never leaks DATABASE_URL credentials
  - POST /assets OPTIONS preflight is reachable from the allowed CORS origin
"""
from __future__ import annotations

import importlib.util
import sys
import uuid
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

REPO_ROOT = Path(__file__).resolve().parents[3]
API_MAIN_PATH = Path(__file__).resolve().parents[1] / 'app' / 'main.py'

sys.path.insert(0, str(REPO_ROOT))

_PROD_POSTGRES_URL = 'postgresql://neondb_owner:s3cr3t@ep-cool-rain-12345.us-east-2.aws.neon.tech/neondb'


def _load_api_module():
    module_name = f'phase1_api_health_prod_safety_{uuid.uuid4().hex}'
    spec = importlib.util.spec_from_file_location(module_name, API_MAIN_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError('Unable to load API module for health production safety tests.')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope='module')
def api_main():
    return _load_api_module()


# ---------------------------------------------------------------------------
# 1. Production /health must not call load_all_services (dev_support)
# ---------------------------------------------------------------------------

class TestHealthProductionSkipsDevSupport:
    def test_health_does_not_call_load_all_services_in_production(
        self, api_main, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv('APP_ENV', 'production')
        monkeypatch.delenv('ENABLE_LOCAL_DEV_SUPPORT', raising=False)
        monkeypatch.setenv('DATABASE_URL', _PROD_POSTGRES_URL)

        call_log: list[str] = []

        def _refuse(*args, **kwargs):
            call_log.append('load_all_services')
            raise RuntimeError('load_all_services must not be called in production')

        monkeypatch.setattr(api_main, 'load_all_services', _refuse)

        client = TestClient(api_main.app, raise_server_exceptions=False)
        response = client.get('/health')

        assert 'load_all_services' not in call_log, (
            'load_all_services was called in production — SQLite dev_support must be skipped'
        )
        assert response.status_code == 200

    def test_dependency_debug_snapshot_skips_registry_in_production(
        self, api_main, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv('APP_ENV', 'production')
        monkeypatch.delenv('ENABLE_LOCAL_DEV_SUPPORT', raising=False)

        call_log: list[str] = []

        def _refuse(*args, **kwargs):
            call_log.append('load_all_services')
            raise RuntimeError('must not be called in production')

        monkeypatch.setattr(api_main, 'load_all_services', _refuse)

        snapshot = api_main.dependency_debug_snapshot()

        assert 'load_all_services' not in call_log
        # All registry fields should be None (no SQLite data) in production
        for dep_data in snapshot.values():
            assert dep_data['registry_status'] is None
            assert dep_data['registry_detail'] is None

    def test_update_dependency_registry_entry_skips_sqlite_in_production(
        self, api_main, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv('APP_ENV', 'production')
        monkeypatch.delenv('ENABLE_LOCAL_DEV_SUPPORT', raising=False)

        call_log: list[str] = []

        def _refuse_upsert(*args, **kwargs):
            call_log.append('upsert_service')
            raise RuntimeError('upsert_service must not be called in production')

        monkeypatch.setattr(api_main, 'upsert_service', _refuse_upsert)

        result = api_main.update_dependency_registry_entry('risk_engine')

        assert 'upsert_service' not in call_log
        assert 'service_name' in result
        assert result['status'] in ('ok', 'degraded')


# ---------------------------------------------------------------------------
# 2. /health returns 200 in production when DB is healthy
# ---------------------------------------------------------------------------

class TestHealthReturns200InProduction:
    def test_health_200_with_postgres_database_url(
        self, api_main, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv('APP_ENV', 'production')
        monkeypatch.setenv('DATABASE_URL', _PROD_POSTGRES_URL)
        monkeypatch.setattr(
            api_main,
            'runtime_environment_identity',
            lambda: {'database_backend': 'postgres_hosted_neon'},
        )

        client = TestClient(api_main.app, raise_server_exceptions=False)
        response = client.get('/health')

        assert response.status_code == 200
        payload = response.json()
        assert payload['status'] == 'ok'
        assert payload['database_backend'] == 'postgres_hosted_neon'

    def test_health_200_does_not_raise_on_neon_database_url(
        self, api_main, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv('APP_ENV', 'production')
        monkeypatch.setenv('DATABASE_URL', _PROD_POSTGRES_URL)
        monkeypatch.delenv('ENABLE_LOCAL_DEV_SUPPORT', raising=False)

        client = TestClient(api_main.app, raise_server_exceptions=False)
        response = client.get('/health')

        # Must not 500 — the SQLite guard must have fired
        assert response.status_code == 200


# ---------------------------------------------------------------------------
# 3. /health never leaks DATABASE_URL
# ---------------------------------------------------------------------------

class TestHealthNeverLeaksDatabaseUrl:
    def test_database_url_credentials_not_in_response(
        self, api_main, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        secret = 's3cr3t_neon_pw_xyz'
        pg_url = f'postgresql://neondb_owner:{secret}@ep.neon.tech/neondb'
        monkeypatch.setattr(api_main, 'database_url', lambda: pg_url)
        monkeypatch.setattr(
            api_main,
            'runtime_environment_identity',
            lambda: {'database_backend': 'postgres_hosted_neon'},
        )

        client = TestClient(api_main.app, raise_server_exceptions=False)
        response = client.get('/health')

        assert response.status_code == 200
        response_text = response.text
        assert secret not in response_text, 'DATABASE_URL credentials must not appear in /health response'
        assert 'postgresql://' not in response_text, 'Raw DATABASE_URL must not appear in /health response'

    def test_database_url_shown_as_configured_sentinel(
        self, api_main, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            api_main,
            'database_url',
            lambda: 'postgresql://pilot:pass@db.internal:5432/decoda',
        )
        monkeypatch.setattr(
            api_main,
            'runtime_environment_identity',
            lambda: {'database_backend': 'postgres_hosted_other'},
        )

        client = TestClient(api_main.app, raise_server_exceptions=False)
        response = client.get('/health')

        assert response.status_code == 200
        payload = response.json()
        assert payload['database_url'] == '[configured]'
        assert payload['database_url_configured'] is True


# ---------------------------------------------------------------------------
# 4. POST /assets OPTIONS preflight reachable from allowed CORS origin
# ---------------------------------------------------------------------------

class TestAssetCreationCorsReachability:
    def test_assets_post_options_preflight_returns_allowed_headers(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        allowed_origin = 'https://rwa.decodasecurity.com'
        monkeypatch.setenv('APP_ENV', 'production')
        monkeypatch.setenv('CORS_ALLOWED_ORIGINS', allowed_origin)

        api_mod = _load_api_module()
        client = TestClient(api_mod.app, raise_server_exceptions=False)

        response = client.options(
            '/assets',
            headers={
                'Origin': allowed_origin,
                'Access-Control-Request-Method': 'POST',
                'Access-Control-Request-Headers': 'Content-Type, Authorization, X-Workspace-Id',
            },
        )

        assert response.status_code in (200, 204), (
            f'OPTIONS preflight for /assets from {allowed_origin} returned {response.status_code}'
        )
        assert allowed_origin in response.headers.get('access-control-allow-origin', ''), (
            'Allowed origin missing from CORS preflight response'
        )

    def test_assets_post_options_preflight_blocked_for_disallowed_origin(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv('APP_ENV', 'production')
        monkeypatch.setenv('CORS_ALLOWED_ORIGINS', 'https://rwa.decodasecurity.com')

        api_mod = _load_api_module()
        client = TestClient(api_mod.app, raise_server_exceptions=False)

        response = client.options(
            '/assets',
            headers={
                'Origin': 'https://evil.example.com',
                'Access-Control-Request-Method': 'POST',
                'Access-Control-Request-Headers': 'Content-Type',
            },
        )

        allow_origin = response.headers.get('access-control-allow-origin', '')
        assert 'evil.example.com' not in allow_origin, (
            'Disallowed origin must not appear in CORS allow-origin header'
        )

    def test_assets_endpoint_is_registered(self, api_main) -> None:
        routes = {getattr(r, 'path', None) for r in api_main.app.routes}
        assert '/assets' in routes, 'POST /assets route must be registered'
