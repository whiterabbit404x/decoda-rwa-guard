"""
Verifies that the monitoring-sources frontend page uses the same-origin proxy
route (/api/monitoring/sources) and does NOT call /monitoring/sources directly
on the browser-facing domain.

Also verifies that the proxy route file exists and the backend endpoint exists.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
PAGE_TSX = REPO_ROOT / 'apps' / 'web' / 'app' / '(product)' / 'monitoring-sources' / 'page.tsx'
PROXY_ROUTE = REPO_ROOT / 'apps' / 'web' / 'app' / 'api' / 'monitoring' / 'sources' / 'route.ts'
BACKEND_MAIN = REPO_ROOT / 'services' / 'api' / 'app' / 'main.py'


def test_monitoring_sources_page_exists():
    assert PAGE_TSX.exists(), f'monitoring-sources page not found: {PAGE_TSX}'


def test_proxy_route_file_exists():
    assert PROXY_ROUTE.exists(), (
        'Missing Next.js proxy route apps/web/app/api/monitoring/sources/route.ts — '
        'the frontend must proxy through same-origin to avoid browser 404.'
    )


def test_page_does_not_call_monitoring_sources_directly():
    """The page must not fetch /monitoring/sources directly on the browser domain."""
    source = PAGE_TSX.read_text()
    forbidden = re.search(r'[`\'"](\$\{[^}]+\})?/monitoring/sources[`\'"]', source)
    assert forbidden is None, (
        'monitoring-sources/page.tsx fetches /monitoring/sources directly. '
        'It must use /api/monitoring/sources (same-origin proxy) instead.'
    )


def test_page_uses_proxy_route():
    """The page must call the same-origin proxy endpoint."""
    source = PAGE_TSX.read_text()
    assert "'/api/monitoring/sources'" in source or '"/api/monitoring/sources"' in source or '`/api/monitoring/sources`' in source, (
        'monitoring-sources/page.tsx must fetch /api/monitoring/sources (same-origin proxy). '
        'Found no such reference.'
    )


def test_proxy_route_forwards_to_backend_monitoring_sources():
    """The proxy route must forward to the backend /monitoring/sources endpoint."""
    source = PROXY_ROUTE.read_text()
    assert '/monitoring/sources' in source, (
        'Proxy route must forward to backend /monitoring/sources endpoint.'
    )


def test_proxy_route_forwards_auth_headers():
    """The proxy route must forward Authorization and X-Workspace-Id headers."""
    source = PROXY_ROUTE.read_text()
    assert 'authorization' in source.lower(), 'Proxy must forward Authorization header.'
    assert 'x-workspace-id' in source.lower(), 'Proxy must forward X-Workspace-Id header.'


def test_backend_monitoring_sources_endpoint_exists():
    """The Railway API must expose GET /monitoring/sources."""
    source = BACKEND_MAIN.read_text()
    assert "'/monitoring/sources'" in source or '"/monitoring/sources"' in source, (
        "Backend main.py must define GET '/monitoring/sources' route."
    )


def test_monitoring_sources_endpoint_aligns_with_assets(monkeypatch):
    """Backend /monitoring/sources returns assets + targets + systems together."""
    fastapi = pytest.importorskip('fastapi', reason='fastapi not installed')  # noqa: F841
    from services.api.app import main as api_main
    from fastapi.testclient import TestClient

    monkeypatch.setattr(api_main, 'list_monitoring_sources', lambda request: {
        'assets': [{'id': 'a1', 'name': 'US Treasury Settlement Contract'}],
        'targets': [],
        'systems': [],
    })

    client = TestClient(api_main.app)
    response = client.get('/monitoring/sources')
    assert response.status_code == 200
    payload = response.json()
    assert len(payload['assets']) == 1
    assert payload['assets'][0]['name'] == 'US Treasury Settlement Contract'
    assert payload['targets'] == []
    assert payload['systems'] == []
