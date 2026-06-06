from __future__ import annotations

import pytest
from fastapi import HTTPException, Request


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


def _reset_limiter(pilot, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(pilot, '_redis_rate_limiter', None)
    monkeypatch.setattr(pilot, '_rate_limit_state', {})
    monkeypatch.setattr(pilot, '_rate_limit_fallback_last_emitted', {})


def test_production_without_redis_uses_memory_fallback(monkeypatch, caplog) -> None:
    from services.api.app import pilot

    monkeypatch.setenv('APP_ENV', 'production')
    monkeypatch.delenv('REDIS_URL', raising=False)
    monkeypatch.delenv('UPSTASH_REDIS_REST_URL', raising=False)
    monkeypatch.delenv('UPSTASH_REDIS_REST_TOKEN', raising=False)
    _reset_limiter(pilot, monkeypatch)

    with caplog.at_level('WARNING'):
        pilot.enforce_auth_rate_limit(_make_request(), 'signin', 'team@example.com')

    assert 'Rate limiter fallback active: Redis unavailable.' in caplog.text


def test_production_redis_connection_failure_uses_memory_fallback(monkeypatch) -> None:
    from services.api.app import pilot

    class _BrokenRedis:
        def incr(self, key):
            raise ConnectionError('Redis down')

    monkeypatch.setenv('APP_ENV', 'production')
    monkeypatch.setenv('REDIS_URL', 'redis://localhost:6379')
    monkeypatch.delenv('UPSTASH_REDIS_REST_URL', raising=False)
    monkeypatch.delenv('UPSTASH_REDIS_REST_TOKEN', raising=False)
    _reset_limiter(pilot, monkeypatch)
    monkeypatch.setattr(pilot, '_redis_rate_limiter', _BrokenRedis())

    pilot.enforce_auth_rate_limit(_make_request(), 'signin', 'team@example.com')


def test_production_with_redis_url_set_uses_redis(monkeypatch) -> None:
    from services.api.app import pilot

    keys: list[str] = []

    class _FakeRedis:
        def incr(self, key):
            keys.append(key)
            return 1

        def expire(self, key, ttl):
            assert ttl == 15 * 60

    monkeypatch.setenv('APP_ENV', 'production')
    monkeypatch.setenv('REDIS_URL', 'redis://localhost:6379')
    _reset_limiter(pilot, monkeypatch)
    monkeypatch.setattr(pilot, '_redis_rate_limiter', _FakeRedis())

    pilot.enforce_auth_rate_limit(_make_request(), 'signin', 'Team@Example.com')

    assert len(keys) == 1
    assert 'team@example.com' not in keys[0]


def test_memory_fallback_limits_by_ip_and_email(monkeypatch) -> None:
    from services.api.app import pilot

    monkeypatch.delenv('REDIS_URL', raising=False)
    monkeypatch.delenv('UPSTASH_REDIS_REST_URL', raising=False)
    monkeypatch.delenv('UPSTASH_REDIS_REST_TOKEN', raising=False)
    _reset_limiter(pilot, monkeypatch)
    request = _make_request('203.0.113.10')

    for _ in range(5):
        pilot.enforce_auth_rate_limit(request, 'signin', 'first@example.com')

    with pytest.raises(HTTPException) as exc_info:
        pilot.enforce_auth_rate_limit(request, 'signin', 'first@example.com')
    assert exc_info.value.status_code == 429

    # A different email at the same IP has its own fallback bucket.
    pilot.enforce_auth_rate_limit(request, 'signin', 'second@example.com')
    # The same email from a different IP also has its own fallback bucket.
    pilot.enforce_auth_rate_limit(_make_request('203.0.113.11'), 'signin', 'first@example.com')


def test_missing_redis_env_is_not_a_startup_validation_error(monkeypatch) -> None:
    from services.api.app import pilot

    monkeypatch.setenv('APP_ENV', 'production')
    monkeypatch.delenv('REDIS_URL', raising=False)
    monkeypatch.delenv('UPSTASH_REDIS_REST_URL', raising=False)
    monkeypatch.delenv('UPSTASH_REDIS_REST_TOKEN', raising=False)

    validation = pilot.validate_runtime_configuration()

    assert not any('REDIS' in error.upper() or 'UPSTASH' in error.upper() for error in validation['errors'])
    assert validation['checks']['distributed_rate_limiter']['required'] is False
