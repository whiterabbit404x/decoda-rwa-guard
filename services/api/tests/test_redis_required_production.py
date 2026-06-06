from __future__ import annotations

import os

import pytest
from fastapi import HTTPException, Request
from fastapi.datastructures import Headers


def _make_request(host: str = '127.0.0.1') -> Request:
    scope = {
        'type': 'http',
        'method': 'POST',
        'path': '/auth/signin',
        'query_string': b'',
        'headers': [],
        'client': (host, 9000),
    }
    return Request(scope)


def test_production_without_redis_raises_503(monkeypatch):
    monkeypatch.setenv('APP_ENV', 'production')
    monkeypatch.delenv('REDIS_URL', raising=False)
    from services.api.app import pilot
    # Reset the cached rate limiter so it re-reads env
    pilot._redis_rate_limiter = None

    with pytest.raises(HTTPException) as exc_info:
        pilot.enforce_auth_rate_limit(_make_request(), 'signin')
    assert exc_info.value.status_code == 503


def test_production_without_redis_raises_503_prod_alias(monkeypatch):
    monkeypatch.setenv('APP_ENV', 'prod')
    monkeypatch.delenv('REDIS_URL', raising=False)
    from services.api.app import pilot
    pilot._redis_rate_limiter = None

    with pytest.raises(HTTPException) as exc_info:
        pilot.enforce_auth_rate_limit(_make_request(), 'signup')
    assert exc_info.value.status_code == 503


def test_development_without_redis_falls_back_to_memory(monkeypatch):
    monkeypatch.setenv('APP_ENV', 'development')
    monkeypatch.delenv('REDIS_URL', raising=False)
    from services.api.app import pilot
    pilot._redis_rate_limiter = None

    # Should not raise; uses in-memory limiter
    pilot.enforce_auth_rate_limit(_make_request(), 'signin')


def test_production_with_redis_url_set_attempts_redis(monkeypatch):
    monkeypatch.setenv('APP_ENV', 'production')
    monkeypatch.setenv('REDIS_URL', 'redis://localhost:6379')
    from services.api.app import pilot

    class _FakeRedis:
        def incr(self, key):
            return 1
        def expire(self, key, ttl):
            pass

    # Inject the fake client directly so no real network call is made
    monkeypatch.setattr(pilot, '_redis_rate_limiter', _FakeRedis())

    # Should not raise (fake Redis returns 1 attempt)
    pilot.enforce_auth_rate_limit(_make_request(), 'signin')


def test_production_redis_connection_failure_raises_503(monkeypatch):
    monkeypatch.setenv('APP_ENV', 'production')
    monkeypatch.setenv('REDIS_URL', 'redis://localhost:6379')
    from services.api.app import pilot

    class _BrokenRedis:
        def incr(self, key):
            raise ConnectionError('Redis down')

    pilot._redis_rate_limiter = _BrokenRedis()

    with pytest.raises(HTTPException) as exc_info:
        pilot.enforce_auth_rate_limit(_make_request(), 'signin')
    assert exc_info.value.status_code == 503
