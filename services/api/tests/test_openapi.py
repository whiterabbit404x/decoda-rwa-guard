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


def test_openapi_json_returns_valid_schema(api_main) -> None:
    """GET /openapi.json must return a valid OpenAPI 3.x schema for enterprise review."""
    client = TestClient(api_main.app)
    response = client.get('/openapi.json')
    assert response.status_code == 200
    schema = response.json()
    assert schema.get('openapi', '').startswith('3.'), 'openapi version must be 3.x'
    assert 'info' in schema, 'schema must have info section'
    assert 'paths' in schema, 'schema must have paths section'
    assert len(schema['paths']) > 0, 'schema must expose at least one path'


def test_openapi_schema_exposes_auth_and_alert_routes(api_main) -> None:
    """The OpenAPI schema must expose the core monitoring and auth routes."""
    client = TestClient(api_main.app)
    schema = client.get('/openapi.json').json()
    paths = set(schema.get('paths', {}).keys())
    assert '/health' in paths, '/health route must be in schema'
    assert any('alert' in p for p in paths), 'alert routes must be in schema'
    assert any('auth' in p for p in paths), 'auth routes must be in schema'


def test_openapi_schema_does_not_leak_internal_env_vars(api_main) -> None:
    """OpenAPI schema must not contain secret env var names or values."""
    import json as _json
    client = TestClient(api_main.app)
    raw = client.get('/openapi.json').text
    for forbidden in ('SECRET_ENCRYPTION_KEY', 'AUTH_TOKEN_SECRET', 'EXPORT_SIGNING_SECRET',
                      'PADDLE_API_KEY', 'STRIPE_SECRET_KEY', 'RESEND_API_KEY'):
        assert forbidden not in raw, f'sensitive key {forbidden} must not appear in OpenAPI schema'
