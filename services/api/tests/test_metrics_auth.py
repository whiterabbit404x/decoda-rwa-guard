"""
Tests for /metrics endpoint bearer token and internal-only protection.

Verifies:
- Unauthenticated requests return 401 in production/staging
- Valid bearer token grants access
- Invalid bearer token returns 401
- METRICS_INTERNAL_ONLY=true bypasses token check
- Local development (no APP_ENV=production) allows unauthenticated access
- No secrets appear in metrics output
"""
from __future__ import annotations

import os
import pytest
from fastapi import Request


def _make_request(headers: dict[str, str] | None = None) -> Request:
    header_list = [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()]
    scope = {
        'type': 'http',
        'method': 'GET',
        'path': '/metrics',
        'query_string': b'',
        'headers': header_list,
        'client': ('127.0.0.1', 9000),
    }
    return Request(scope)


@pytest.fixture(autouse=True)
def _clear_metrics_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv('METRICS_BEARER_TOKEN', raising=False)
    monkeypatch.delenv('METRICS_INTERNAL_ONLY', raising=False)
    monkeypatch.delenv('APP_ENV', raising=False)
    monkeypatch.delenv('APP_MODE', raising=False)


def _get_check_fn(monkeypatch: pytest.MonkeyPatch):
    """Re-import _check_metrics_auth after env changes."""
    import importlib
    import services.api.app.main as main_mod
    # Reload the module-level constants by re-reading env
    token = os.getenv('METRICS_BEARER_TOKEN', '').strip()
    internal_only = os.getenv('METRICS_INTERNAL_ONLY', '').strip().lower() in ('1', 'true', 'yes')
    monkeypatch.setattr(main_mod, '_METRICS_BEARER_TOKEN', token)
    monkeypatch.setattr(main_mod, '_METRICS_INTERNAL_ONLY', internal_only)
    return main_mod._check_metrics_auth


def test_production_without_token_or_internal_only_returns_401(monkeypatch: pytest.MonkeyPatch) -> None:
    """In production without METRICS_BEARER_TOKEN or METRICS_INTERNAL_ONLY, return 401."""
    monkeypatch.setenv('APP_ENV', 'production')
    check = _get_check_fn(monkeypatch)
    request = _make_request()
    response = check(request)
    assert response is not None
    assert response.status_code == 401


def test_staging_without_token_or_internal_only_returns_401(monkeypatch: pytest.MonkeyPatch) -> None:
    """In staging without METRICS_BEARER_TOKEN or METRICS_INTERNAL_ONLY, return 401."""
    monkeypatch.setenv('APP_ENV', 'staging')
    check = _get_check_fn(monkeypatch)
    request = _make_request()
    response = check(request)
    assert response is not None
    assert response.status_code == 401


def test_local_dev_without_token_allows_unauthenticated(monkeypatch: pytest.MonkeyPatch) -> None:
    """Local development with no APP_ENV set should allow unauthenticated access."""
    # APP_ENV not set (cleared by fixture)
    check = _get_check_fn(monkeypatch)
    request = _make_request()
    response = check(request)
    assert response is None  # None means access granted


def test_valid_bearer_token_grants_access(monkeypatch: pytest.MonkeyPatch) -> None:
    """Valid bearer token in Authorization header grants access."""
    monkeypatch.setenv('METRICS_BEARER_TOKEN', 'my-secret-token')
    monkeypatch.setenv('APP_ENV', 'production')
    check = _get_check_fn(monkeypatch)
    request = _make_request({'authorization': 'Bearer my-secret-token'})
    response = check(request)
    assert response is None  # None means access granted


def test_invalid_bearer_token_returns_401(monkeypatch: pytest.MonkeyPatch) -> None:
    """Invalid bearer token returns 401."""
    monkeypatch.setenv('METRICS_BEARER_TOKEN', 'my-secret-token')
    monkeypatch.setenv('APP_ENV', 'production')
    check = _get_check_fn(monkeypatch)
    request = _make_request({'authorization': 'Bearer wrong-token'})
    response = check(request)
    assert response is not None
    assert response.status_code == 401


def test_missing_authorization_header_with_token_configured_returns_401(monkeypatch: pytest.MonkeyPatch) -> None:
    """Request without Authorization header when token is configured returns 401."""
    monkeypatch.setenv('METRICS_BEARER_TOKEN', 'my-secret-token')
    monkeypatch.setenv('APP_ENV', 'production')
    check = _get_check_fn(monkeypatch)
    request = _make_request()  # No auth header
    response = check(request)
    assert response is not None
    assert response.status_code == 401


def test_internal_only_mode_allows_unauthenticated(monkeypatch: pytest.MonkeyPatch) -> None:
    """METRICS_INTERNAL_ONLY=true allows unauthenticated access in production."""
    monkeypatch.setenv('METRICS_INTERNAL_ONLY', 'true')
    monkeypatch.setenv('APP_ENV', 'production')
    check = _get_check_fn(monkeypatch)
    request = _make_request()  # No auth header
    response = check(request)
    assert response is None  # None means access granted (network layer enforces)


def test_token_check_takes_priority_over_internal_only(monkeypatch: pytest.MonkeyPatch) -> None:
    """When METRICS_BEARER_TOKEN is set, it takes priority over METRICS_INTERNAL_ONLY."""
    monkeypatch.setenv('METRICS_BEARER_TOKEN', 'secret')
    monkeypatch.setenv('METRICS_INTERNAL_ONLY', 'true')
    monkeypatch.setenv('APP_ENV', 'production')
    check = _get_check_fn(monkeypatch)

    # Valid token still works
    request_valid = _make_request({'authorization': 'Bearer secret'})
    assert check(request_valid) is None

    # Invalid token still blocked even with INTERNAL_ONLY
    request_invalid = _make_request({'authorization': 'Bearer wrong'})
    response = check(request_invalid)
    assert response is not None
    assert response.status_code == 401


def test_www_authenticate_header_present_on_401(monkeypatch: pytest.MonkeyPatch) -> None:
    """401 responses must include WWW-Authenticate header (RFC 7235)."""
    monkeypatch.setenv('APP_ENV', 'production')
    check = _get_check_fn(monkeypatch)
    request = _make_request()
    response = check(request)
    assert response is not None
    assert response.status_code == 401
    assert 'www-authenticate' in {k.lower() for k in (response.headers or {})}


def test_token_value_not_in_error_response(monkeypatch: pytest.MonkeyPatch) -> None:
    """The configured token value must never appear in the 401 response body."""
    secret = 'super-secret-metrics-token-xyz'
    monkeypatch.setenv('METRICS_BEARER_TOKEN', secret)
    monkeypatch.setenv('APP_ENV', 'production')
    check = _get_check_fn(monkeypatch)
    request = _make_request({'authorization': 'Bearer wrong'})
    response = check(request)
    assert response is not None
    body = response.body if hasattr(response, 'body') else b''
    assert secret.encode() not in body
