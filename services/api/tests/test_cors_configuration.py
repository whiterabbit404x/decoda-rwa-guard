from __future__ import annotations

import importlib.util
import sys
import uuid
from pathlib import Path

from fastapi.testclient import TestClient

REPO_ROOT = Path(__file__).resolve().parents[3]
API_MAIN_PATH = Path(__file__).resolve().parents[1] / 'app' / 'main.py'

sys.path.insert(0, str(REPO_ROOT))


def load_api_main_module():
    module_name = f'phase1_api_cors_main_{uuid.uuid4().hex}'
    spec = importlib.util.spec_from_file_location(module_name, API_MAIN_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError('Unable to load API module for CORS tests.')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_resolve_allowed_origins_parses_csv_and_filters_invalid(monkeypatch):
    monkeypatch.setenv('APP_ENV', 'production')
    monkeypatch.setenv(
        'CORS_ALLOWED_ORIGINS',
        ' https://rwa.decodasecurity.com ,http://localhost:3000,notaurl,https://rwa.decodasecurity.com/path ',
    )

    api_main = load_api_main_module()

    assert api_main.ALLOWED_ORIGINS == [
        'https://rwa.decodasecurity.com',
        'http://localhost:3000',
    ]


def test_resolve_allowed_origins_falls_back_to_legacy_allowed_origins_env(monkeypatch):
    monkeypatch.setenv('APP_ENV', 'production')
    monkeypatch.delenv('CORS_ALLOWED_ORIGINS', raising=False)
    monkeypatch.setenv('ALLOWED_ORIGINS', 'https://rwa.decodasecurity.com,https://staging.rwa.decodasecurity.com')

    api_main = load_api_main_module()

    assert api_main.ALLOWED_ORIGINS == [
        'https://rwa.decodasecurity.com',
        'https://staging.rwa.decodasecurity.com',
    ]


def test_development_defaults_include_localhost_origins(monkeypatch):
    monkeypatch.setenv('APP_ENV', 'development')
    monkeypatch.delenv('CORS_ALLOWED_ORIGINS', raising=False)
    monkeypatch.delenv('ALLOWED_ORIGINS', raising=False)

    api_main = load_api_main_module()

    assert api_main.ALLOWED_ORIGINS == [
        'http://localhost:3000',
        'http://127.0.0.1:3000',
    ]


def test_production_defaults_do_not_allow_wildcard_or_localhost_without_env(monkeypatch):
    monkeypatch.setenv('APP_ENV', 'production')
    monkeypatch.delenv('CORS_ALLOWED_ORIGINS', raising=False)
    monkeypatch.delenv('ALLOWED_ORIGINS', raising=False)

    api_main = load_api_main_module()

    assert api_main.ALLOWED_ORIGINS == ['https://rwa.decodasecurity.com']


def test_preflight_and_actual_response_include_cors_headers_for_allowed_origin(monkeypatch):
    monkeypatch.setenv('APP_ENV', 'production')
    monkeypatch.setenv('CORS_ALLOWED_ORIGINS', 'https://rwa.decodasecurity.com,http://localhost:3000')

    api_main = load_api_main_module()
    client = TestClient(api_main.app)

    preflight = client.options(
        '/assets',
        headers={
            'Origin': 'https://rwa.decodasecurity.com',
            'Access-Control-Request-Method': 'POST',
            'Access-Control-Request-Headers': 'authorization,content-type,x-workspace-id',
        },
    )

    assert preflight.status_code == 200
    assert preflight.headers.get('access-control-allow-origin') == 'https://rwa.decodasecurity.com'
    assert 'authorization' in (preflight.headers.get('access-control-allow-headers') or '').lower()

    actual = client.get('/health', headers={'Origin': 'https://rwa.decodasecurity.com'})

    assert actual.status_code == 200
    assert actual.headers.get('access-control-allow-origin') == 'https://rwa.decodasecurity.com'


def test_preflight_for_runtime_status_supports_monitoring_endpoints(monkeypatch):
    monkeypatch.setenv('APP_ENV', 'production')
    monkeypatch.setenv('CORS_ALLOWED_ORIGINS', 'https://rwa.decodasecurity.com')

    api_main = load_api_main_module()
    client = TestClient(api_main.app)

    preflight = client.options(
        '/ops/monitoring/runtime-status',
        headers={
            'Origin': 'https://rwa.decodasecurity.com',
            'Access-Control-Request-Method': 'GET',
            'Access-Control-Request-Headers': 'x-workspace-id,x-csrf-token',
        },
    )

    assert preflight.status_code == 200
    assert preflight.headers.get('access-control-allow-origin') == 'https://rwa.decodasecurity.com'
    assert 'x-workspace-id' in (preflight.headers.get('access-control-allow-headers') or '').lower()


def test_preflight_for_monitoring_system_reconcile_supports_post_and_auth_headers(monkeypatch):
    monkeypatch.setenv('APP_ENV', 'production')
    monkeypatch.setenv('CORS_ALLOWED_ORIGINS', 'https://rwa.decodasecurity.com')

    api_main = load_api_main_module()
    client = TestClient(api_main.app)

    preflight = client.options(
        '/monitoring/systems/reconcile',
        headers={
            'Origin': 'https://rwa.decodasecurity.com',
            'Access-Control-Request-Method': 'POST',
            'Access-Control-Request-Headers': 'authorization,x-workspace-id,x-csrf-token,content-type',
        },
    )

    assert preflight.status_code == 200
    assert preflight.headers.get('access-control-allow-origin') == 'https://rwa.decodasecurity.com'
    allowed_headers = (preflight.headers.get('access-control-allow-headers') or '').lower()
    assert 'authorization' in allowed_headers
    assert 'x-workspace-id' in allowed_headers
    assert 'x-csrf-token' in allowed_headers
    assert 'content-type' in allowed_headers


def test_post_and_get_operations_include_cors_headers_for_allowed_origin(monkeypatch):
    monkeypatch.setenv('APP_ENV', 'production')
    monkeypatch.setenv('CORS_ALLOWED_ORIGINS', 'https://rwa.decodasecurity.com')

    api_main = load_api_main_module()
    client = TestClient(api_main.app)

    post_response = client.post('/assets', json={}, headers={'Origin': 'https://rwa.decodasecurity.com'})
    get_response = client.get('/alerts', headers={'Origin': 'https://rwa.decodasecurity.com'})

    assert post_response.headers.get('access-control-allow-origin') == 'https://rwa.decodasecurity.com'
    assert get_response.headers.get('access-control-allow-origin') == 'https://rwa.decodasecurity.com'


def test_disallowed_origin_does_not_get_allow_origin_header(monkeypatch):
    monkeypatch.setenv('APP_ENV', 'production')
    monkeypatch.setenv('CORS_ALLOWED_ORIGINS', 'https://rwa.decodasecurity.com')

    api_main = load_api_main_module()
    client = TestClient(api_main.app)

    preflight = client.options(
        '/targets',
        headers={
            'Origin': 'https://evil.example',
            'Access-Control-Request-Method': 'GET',
        },
    )

    assert preflight.status_code == 400
    assert preflight.headers.get('access-control-allow-origin') is None

    actual = client.get('/health', headers={'Origin': 'https://evil.example'})

    assert actual.status_code == 200
    assert actual.headers.get('access-control-allow-origin') is None


def test_cors_middleware_is_installed_on_api_app_entrypoint(monkeypatch):
    monkeypatch.setenv('APP_ENV', 'production')
    monkeypatch.setenv('CORS_ALLOWED_ORIGINS', 'https://rwa.decodasecurity.com')

    api_main = load_api_main_module()
    middleware_classes = [middleware.cls.__name__ for middleware in api_main.app.user_middleware]

    assert 'CORSMiddleware' in middleware_classes
