"""
Tests for enterprise-readiness fixes:
- Task 1: SSE /stream/alerts endpoint
- Task 3: API key enforcement middleware
- Task 4: Trace-ID middleware
- Task 5: /metrics endpoint
- Task 6: GDPR /auth/delete-account endpoint
"""
from __future__ import annotations

import importlib.util
import uuid
import sys
from pathlib import Path

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

REPO_ROOT = Path(__file__).resolve().parents[3]
API_MAIN_PATH = Path(__file__).resolve().parents[1] / 'app' / 'main.py'

sys.path.insert(0, str(REPO_ROOT))


def _load_fresh_api_main():
    """Load a fresh copy of the API main module (avoids cross-test cache contamination)."""
    module_name = f'api_main_enterprise_{uuid.uuid4().hex}'
    spec = importlib.util.spec_from_file_location(module_name, API_MAIN_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError('Unable to load API main module for enterprise tests.')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# Load once per module run (all tests share this fresh instance)
_api_main = _load_fresh_api_main()
_client = TestClient(_api_main.app, raise_server_exceptions=False)


# ---------------------------------------------------------------------------
# Task 1: SSE /stream/alerts - unauthenticated request must be rejected
# ---------------------------------------------------------------------------
def test_stream_alerts_unauthenticated_rejected(monkeypatch):
    """GET /stream/alerts without auth headers must return 401."""
    def _raise_401(request):
        raise HTTPException(status_code=401, detail='Missing bearer token.')

    monkeypatch.setattr(_api_main, 'authenticate_request', _raise_401)

    response = _client.get('/stream/alerts')
    assert response.status_code == 401, (
        f'Expected 401 for unauthenticated SSE request, got {response.status_code}: {response.text}'
    )


def test_stream_alerts_route_exists():
    """Confirm the /stream/alerts route is registered on the app."""
    routes = {route.path for route in _api_main.app.routes}
    assert '/stream/alerts' in routes, f'Route /stream/alerts not found; routes: {sorted(routes)}'


# ---------------------------------------------------------------------------
# Task 3: API key middleware - missing key on /api/v1/* returns 401
# ---------------------------------------------------------------------------
def test_api_key_middleware_missing_key():
    """GET /api/v1/alerts without X-API-Key should return 401 with code API_KEY_MISSING."""
    response = _client.get('/api/v1/alerts')
    # Route may not exist (404) or middleware fires first (401).
    # The middleware must fire before route resolution for /api/v1/* paths.
    assert response.status_code == 401, (
        f'Expected 401 from API key middleware, got {response.status_code}: {response.text}'
    )
    body = response.json()
    assert body.get('code') == 'API_KEY_MISSING', f'Expected API_KEY_MISSING code, got: {body}'


def test_api_key_middleware_non_api_v1_path_passes_through():
    """Non /api/v1/ routes are not subject to API key enforcement."""
    # /health is a public endpoint that should always work without X-API-Key
    response = _client.get('/health')
    assert response.status_code == 200, (
        f'Expected /health to return 200 without API key, got {response.status_code}'
    )


# ---------------------------------------------------------------------------
# Task 4: Trace-ID middleware
# ---------------------------------------------------------------------------
def test_trace_id_returned_on_health():
    """GET /health must return an X-Trace-ID response header."""
    response = _client.get('/health')
    assert response.status_code == 200
    trace_id = response.headers.get('x-trace-id') or response.headers.get('X-Trace-ID')
    assert trace_id, f'X-Trace-ID header missing from /health response. Headers: {dict(response.headers)}'
    assert len(trace_id) > 0


def test_trace_id_propagates_from_request():
    """X-Request-ID in the request should be echoed back as X-Trace-ID."""
    custom_id = 'test-trace-abc123'
    response = _client.get('/health', headers={'X-Request-ID': custom_id})
    assert response.status_code == 200
    trace_id = response.headers.get('x-trace-id') or response.headers.get('X-Trace-ID')
    assert trace_id == custom_id, (
        f'Expected trace_id={custom_id!r}, got {trace_id!r}'
    )


# ---------------------------------------------------------------------------
# Task 5: /metrics endpoint
# ---------------------------------------------------------------------------
def test_metrics_endpoint_available():
    """GET /metrics must return 200 with Prometheus text format."""
    response = _client.get('/metrics')
    assert response.status_code == 200, (
        f'Expected /metrics to return 200, got {response.status_code}: {response.text}'
    )
    content_type = response.headers.get('content-type', '')
    assert 'text/plain' in content_type, f'Expected text/plain content-type, got {content_type!r}'


def test_metrics_contains_expected_metric_names():
    """GET /metrics must include key metric names."""
    response = _client.get('/metrics')
    assert response.status_code == 200
    body = response.text
    assert 'decoda_http_requests_total' in body, f'Missing decoda_http_requests_total in metrics: {body[:500]}'
    assert 'decoda_stream_connections_active' in body, f'Missing decoda_stream_connections_active in metrics: {body[:500]}'
    assert 'decoda_auth_failures_total' in body, f'Missing decoda_auth_failures_total in metrics: {body[:500]}'
    assert 'decoda_alerts_published_total' in body, f'Missing decoda_alerts_published_total in metrics: {body[:500]}'


# ---------------------------------------------------------------------------
# Task 6: GDPR /auth/delete-account - unauthenticated returns 401 or 400/503
# ---------------------------------------------------------------------------
def test_delete_account_requires_auth():
    """DELETE /auth/delete-account without auth token must be rejected (401/503)."""
    response = _client.request('DELETE', '/auth/delete-account', json={'current_password': 'somepassword'})
    # Live mode not enabled in test env → 503, or auth fails → 401/403
    assert response.status_code in {400, 401, 403, 503}, (
        f'Expected 401/403/503 for unauthenticated delete-account, got {response.status_code}: {response.text}'
    )


def test_delete_account_route_exists():
    """Confirm the /auth/delete-account route is registered."""
    routes = {route.path for route in _api_main.app.routes}
    assert '/auth/delete-account' in routes, (
        f'Route /auth/delete-account not found; routes: {sorted(r for r in routes if "auth" in r)}'
    )


# ---------------------------------------------------------------------------
# Internal: publishing uses the shared Redis Stream
# ---------------------------------------------------------------------------
def test_publish_alert_uses_shared_stream(monkeypatch):
    published = []
    monkeypatch.setattr(_api_main.alert_stream, 'publish', lambda workspace_id, payload: published.append((workspace_id, payload)) or '1-0')
    before = _api_main._ALERTS_PUBLISHED_COUNT

    _api_main.publish_alert_to_workspace('test-ws-counter', {'type': 'alert'})

    assert published == [('test-ws-counter', {'type': 'alert'})]
    assert _api_main._ALERTS_PUBLISHED_COUNT == before + 1
