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
    from services.api.app.domains import rate_limit as _rl
    # Rate limiting state now lives in the domain module; patch there.
    monkeypatch.setattr(_rl, '_redis_rate_limiter', None)
    monkeypatch.setattr(_rl, '_rate_limit_state', {})
    monkeypatch.setattr(_rl, '_rate_limit_fallback_last_emitted', {})
    # Keep pilot-level alias in sync for any code that reads pilot._redis_rate_limiter directly.
    monkeypatch.setattr(pilot, '_redis_rate_limiter', None)


def test_production_without_redis_fails_closed(monkeypatch) -> None:
    """Production auth limiting rejects requests when no shared backend exists."""
    from services.api.app import pilot

    monkeypatch.setenv('APP_ENV', 'production')
    monkeypatch.delenv('REDIS_URL', raising=False)
    monkeypatch.delenv('UPSTASH_REDIS_REST_URL', raising=False)
    monkeypatch.delenv('UPSTASH_REDIS_REST_TOKEN', raising=False)
    _reset_limiter(pilot, monkeypatch)

    with pytest.raises(HTTPException) as exc_info:
        pilot.enforce_auth_rate_limit(_make_request(), 'signin', 'team@example.com')
    assert exc_info.value.status_code == 503


def test_production_redis_connection_failure_fails_closed(monkeypatch) -> None:
    from services.api.app import pilot
    from services.api.app.domains import rate_limit as _rl

    class _BrokenRedis:
        def incr(self, key):
            raise ConnectionError('Redis down')

    monkeypatch.setenv('APP_ENV', 'production')
    monkeypatch.setenv('REDIS_URL', 'redis://localhost:6379')
    monkeypatch.delenv('UPSTASH_REDIS_REST_URL', raising=False)
    monkeypatch.delenv('UPSTASH_REDIS_REST_TOKEN', raising=False)
    _reset_limiter(pilot, monkeypatch)
    monkeypatch.setattr(_rl, '_redis_rate_limiter', _BrokenRedis())

    with pytest.raises(HTTPException) as exc_info:
        pilot.enforce_auth_rate_limit(_make_request(), 'signin', 'team@example.com')
    assert exc_info.value.status_code == 503


def test_production_with_redis_url_set_uses_redis(monkeypatch) -> None:
    from services.api.app import pilot
    from services.api.app.domains import rate_limit as _rl

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
    monkeypatch.setattr(_rl, '_redis_rate_limiter', _FakeRedis())

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


def test_production_missing_redis_is_now_a_startup_validation_error(monkeypatch) -> None:
    """In production, missing Redis is a required error — startup must fail without an override."""
    from services.api.app import pilot

    monkeypatch.setenv('APP_ENV', 'production')
    monkeypatch.delenv('REDIS_URL', raising=False)
    monkeypatch.delenv('UPSTASH_REDIS_REST_URL', raising=False)
    monkeypatch.delenv('UPSTASH_REDIS_REST_TOKEN', raising=False)
    monkeypatch.delenv('ALLOW_IN_MEMORY_RATE_LIMIT_IN_PRODUCTION', raising=False)
    monkeypatch.delenv('REDIS_TEMPORARILY_DISABLED', raising=False)

    validation = pilot.validate_runtime_configuration()

    assert validation['checks']['distributed_rate_limiter']['required'] is True
    assert validation['checks']['distributed_rate_limiter']['ok'] is False
    assert any('REDIS_URL' in error for error in validation['errors'])


def test_production_with_redis_passes_config_validation(monkeypatch) -> None:
    """In production with REDIS_URL set, the distributed_rate_limiter check passes."""
    from services.api.app import pilot

    monkeypatch.setenv('APP_ENV', 'production')
    monkeypatch.setenv('REDIS_URL', 'redis://localhost:6379/0')
    monkeypatch.delenv('ALLOW_IN_MEMORY_RATE_LIMIT_IN_PRODUCTION', raising=False)
    monkeypatch.delenv('REDIS_TEMPORARILY_DISABLED', raising=False)

    validation = pilot.validate_runtime_configuration()

    assert validation['checks']['distributed_rate_limiter']['ok'] is True
    assert validation['checks']['distributed_rate_limiter']['required'] is True
    assert validation['checks'].get('rate_limit_backend') == 'redis'
    assert validation['checks'].get('rate_limit_enterprise_ready') is True


def test_production_memory_override_is_rejected(monkeypatch) -> None:
    """Break-glass memory flags cannot make a production deployment ready."""
    from services.api.app import pilot

    monkeypatch.setenv('APP_ENV', 'production')
    monkeypatch.delenv('REDIS_URL', raising=False)
    monkeypatch.delenv('UPSTASH_REDIS_REST_URL', raising=False)
    monkeypatch.delenv('UPSTASH_REDIS_REST_TOKEN', raising=False)
    monkeypatch.setenv('REDIS_TEMPORARILY_DISABLED', 'true')

    validation = pilot.validate_runtime_configuration()

    assert validation['checks']['distributed_rate_limiter']['required'] is True
    assert validation['checks']['distributed_rate_limiter']['ok'] is False
    assert validation['checks'].get('rate_limit_enterprise_ready') is False
    assert validation['checks'].get('rate_limit_backend') == 'memory'
    assert validation['checks'].get('redis_status') == 'memory_rejected'
    assert any('Memory-backed production rate limiting is rejected' in error for error in validation['errors'])



def test_production_upstash_only_rejects_missing_alert_stream_redis(monkeypatch) -> None:
    from services.api.app import pilot

    monkeypatch.setenv('APP_ENV', 'production')
    monkeypatch.delenv('REDIS_URL', raising=False)
    monkeypatch.setenv('UPSTASH_REDIS_REST_URL', 'https://example.upstash.io')
    monkeypatch.setenv('UPSTASH_REDIS_REST_TOKEN', 'token')

    validation = pilot.validate_runtime_configuration()

    assert validation['checks']['distributed_rate_limiter']['ok'] is True
    assert validation['checks']['shared_alert_stream']['ok'] is False
    assert any('Redis Streams alert delivery' in error or 'alert streaming' in error for error in validation['errors'])

def test_local_test_memory_limiter_still_works(monkeypatch) -> None:
    """In local/test mode, missing Redis is fine — in-memory limiter is expected."""
    from services.api.app import pilot

    monkeypatch.setenv('APP_ENV', 'local')
    monkeypatch.delenv('REDIS_URL', raising=False)
    monkeypatch.delenv('UPSTASH_REDIS_REST_URL', raising=False)
    monkeypatch.delenv('UPSTASH_REDIS_REST_TOKEN', raising=False)
    monkeypatch.delenv('ALLOW_IN_MEMORY_RATE_LIMIT_IN_PRODUCTION', raising=False)
    monkeypatch.delenv('REDIS_TEMPORARILY_DISABLED', raising=False)

    validation = pilot.validate_runtime_configuration()

    # In local mode, validate_runtime_configuration skips production checks
    # so no distributed_rate_limiter check should be emitted as a required error
    assert not any('REDIS_URL' in error for error in validation['errors'])


def test_production_rate_limit_failure_is_503(monkeypatch) -> None:
    from services.api.app import pilot

    monkeypatch.setenv('APP_ENV', 'production')
    monkeypatch.delenv('REDIS_URL', raising=False)
    monkeypatch.delenv('UPSTASH_REDIS_REST_URL', raising=False)
    monkeypatch.delenv('UPSTASH_REDIS_REST_TOKEN', raising=False)
    _reset_limiter(pilot, monkeypatch)

    with pytest.raises(HTTPException) as exc_info:
        pilot.enforce_auth_rate_limit(_make_request(), 'signin', 'user@example.com')
    assert exc_info.value.status_code == 503


def test_health_readiness_reports_not_ready_for_memory_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    from services.api.app import main as api_main

    monkeypatch.setenv('APP_ENV', 'production')

    monkeypatch.setattr(api_main, 'validate_runtime_configuration', lambda: {
        'errors': [],
        'warnings': [],
        'checks': {
            'redis_configured': False,
            'redis_status': 'memory_rejected',
            'rate_limit_backend': 'memory',
            'rate_limit_enterprise_ready': False,
        },
    })
    monkeypatch.setattr(api_main, 'billing_runtime_status', lambda: {'provider': 'paddle', 'available': True})
    monkeypatch.setattr(api_main, 'rate_limit_connectivity', lambda: {'connected': False, 'status': 'not_configured'})
    monkeypatch.setattr(api_main.alert_stream, 'connectivity_sync', lambda: {'connected': False, 'status': 'not_configured'})

    payload = api_main.health_readiness()

    assert payload['status'] == 'not_ready'
    assert payload['redis_configured'] is False
    assert payload['rate_limit_backend'] == 'memory'
    assert payload['rate_limit_enterprise_ready'] is False
    assert payload['enterprise_ready'] is False
    assert payload['warning'] is None
