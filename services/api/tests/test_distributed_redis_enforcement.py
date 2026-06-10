"""
Distributed Redis enforcement tests.

Proves that:
 1. Production startup is blocked when Redis is configured but unreachable.
 2. Cross-instance token revocation: a token revoked by one process is rejected by another
    when both share the same Redis backend.
 3. bootstrap_live_pilot() raises RuntimeError whenever validate_runtime_configuration()
    returns errors (not just when Redis is missing).
 4. REDIS_TEMPORARILY_DISABLED=true is rejected at production startup.
 5. Local/dev mode can still run without Redis.
"""
from __future__ import annotations

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _full_production_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Apply the minimum env vars required for a passing production validation."""
    monkeypatch.setenv('APP_ENV', 'production')
    monkeypatch.setenv('APP_MODE', 'production')
    monkeypatch.setenv('LIVE_MODE_ENABLED', 'true')
    monkeypatch.setenv('MONITORING_INGESTION_MODE', 'demo')
    monkeypatch.setenv('DATABASE_URL', 'postgresql://example')
    monkeypatch.setenv('AUTH_TOKEN_SECRET', 'strong-production-auth-secret-for-enforcement-tests')
    monkeypatch.setenv('EXPORT_SIGNING_SECRET', 'strong-production-signing-secret-for-tests')
    monkeypatch.setenv('SECRET_ENCRYPTION_KEY', 'MDEyMzQ1Njc4OWFiY2RlZjAxMjM0NTY3ODlhYmNkZWY=')
    monkeypatch.setenv('EMAIL_PROVIDER', 'resend')
    monkeypatch.setenv('EMAIL_FROM', 'ops@decoda.app')
    monkeypatch.setenv('EMAIL_RESEND_API_KEY', 're_123')
    monkeypatch.setenv('REDIS_URL', 'redis://localhost:6379/0')
    monkeypatch.setenv('BILLING_PROVIDER', 'none')
    monkeypatch.delenv('REDIS_TEMPORARILY_DISABLED', raising=False)
    monkeypatch.delenv('ALLOW_IN_MEMORY_RATE_LIMIT_IN_PRODUCTION', raising=False)


# ---------------------------------------------------------------------------
# 1. Production startup blocked when Redis is unreachable
# ---------------------------------------------------------------------------

def test_production_startup_redis_unreachable_blocks_startup(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    bootstrap_live_pilot() must raise RuntimeError when REDIS_URL is configured but
    the Redis server is not reachable. Validates the post-config connectivity probe.
    """
    from services.api.app import main as api_main

    _full_production_env(monkeypatch)
    # Patch rate_limit_connectivity as it is imported into main's namespace.
    monkeypatch.setattr(
        api_main,
        'rate_limit_connectivity',
        lambda: {'backend': 'redis', 'configured': True, 'connected': False,
                 'status': 'unavailable', 'error': 'ConnectionRefused'},
    )
    monkeypatch.setattr(
        api_main,
        'run_startup_migrations_if_enabled',
        lambda **_kw: {'ran': False, 'process_role': 'api', 'reason': 'test'},
    )
    monkeypatch.setattr(
        api_main,
        'reconcile_monitored_systems_for_enabled_targets',
        lambda: {'enabled_targets_scanned': 0, 'created_or_updated': 0, 'invalid_targets': []},
    )

    with pytest.raises(RuntimeError) as exc_info:
        api_main.bootstrap_live_pilot()

    msg = str(exc_info.value)
    assert 'REDIS_URL is set but Redis is not reachable' in msg
    assert 'ConnectionRefused' in msg


def test_staging_startup_redis_unreachable_blocks_startup(monkeypatch: pytest.MonkeyPatch) -> None:
    """Staging (APP_ENV=staging) obeys the same connectivity probe as production."""
    from services.api.app import main as api_main

    _full_production_env(monkeypatch)
    monkeypatch.setenv('APP_ENV', 'staging')

    monkeypatch.setattr(
        api_main,
        'rate_limit_connectivity',
        lambda: {'backend': 'redis', 'configured': True, 'connected': False,
                 'status': 'unavailable', 'error': 'TimeoutError'},
    )
    monkeypatch.setattr(
        api_main,
        'run_startup_migrations_if_enabled',
        lambda **_kw: {'ran': False, 'process_role': 'api', 'reason': 'test'},
    )
    monkeypatch.setattr(
        api_main,
        'reconcile_monitored_systems_for_enabled_targets',
        lambda: {'enabled_targets_scanned': 0, 'created_or_updated': 0, 'invalid_targets': []},
    )

    with pytest.raises(RuntimeError) as exc_info:
        api_main.bootstrap_live_pilot()

    assert 'REDIS_URL is set but Redis is not reachable' in str(exc_info.value)


def test_dev_mode_startup_does_not_probe_redis(monkeypatch: pytest.MonkeyPatch) -> None:
    """In local/dev mode, the Redis connectivity probe is skipped even if a URL is set."""
    from services.api.app import main as api_main

    monkeypatch.setenv('APP_ENV', 'local')
    monkeypatch.setenv('APP_MODE', 'local')
    monkeypatch.setenv('REDIS_URL', 'redis://localhost:6379/0')

    probe_called = []

    def _failing_probe():
        probe_called.append(True)
        return {'backend': 'redis', 'configured': True, 'connected': False,
                'status': 'unavailable', 'error': 'ConnectionRefused'}

    # Patch rate_limit_connectivity from main's imported namespace
    monkeypatch.setattr(api_main, 'rate_limit_connectivity', _failing_probe)
    monkeypatch.setattr(
        api_main,
        'validate_runtime_configuration',
        lambda: {'errors': [], 'warnings': [], 'checks': {}},
    )
    monkeypatch.setattr(
        api_main,
        'run_startup_migrations_if_enabled',
        lambda **_kw: {'ran': False, 'process_role': 'api', 'reason': 'test'},
    )
    monkeypatch.setattr(
        api_main,
        'reconcile_monitored_systems_for_enabled_targets',
        lambda: {'enabled_targets_scanned': 0, 'created_or_updated': 0, 'invalid_targets': []},
    )

    api_main.bootstrap_live_pilot()

    assert not probe_called, 'Redis connectivity probe must not run in local/dev mode'


# ---------------------------------------------------------------------------
# 2. Cross-instance token revocation via shared Redis
# ---------------------------------------------------------------------------

def test_cross_instance_token_revocation_via_shared_redis(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    Token revoked by one service instance (using Redis) must be rejected by a second
    instance that shares the same Redis backend.

    Simulates two instances by creating two separate Redis client objects that share
    the same in-memory dict (as a stand-in for a real Redis server).
    """
    monkeypatch.setenv('REDIS_URL', 'redis://localhost:6379')
    from services.api.app import pilot

    shared_store: dict[str, str] = {}

    class _SharedFakeRedis:
        def setex(self, key: str, ttl: int, value: str) -> None:
            shared_store[key] = value

        def exists(self, key: str) -> int:
            return 1 if key in shared_store else 0

    instance_a_redis = _SharedFakeRedis()
    instance_b_redis = _SharedFakeRedis()

    token_hash = 'revoked-token-hash-abc123'

    # Instance A: revoke the token
    pilot._session_blacklist_redis = instance_a_redis
    pilot._blacklist_session_token(token_hash, 3600)

    # Instance B: check whether the token is blacklisted (reads from the same shared store)
    pilot._session_blacklist_redis = instance_b_redis
    assert pilot._is_session_blacklisted(token_hash) is True, (
        'A token revoked by instance A must be seen as revoked by instance B '
        'when both share the same Redis backend.'
    )


def test_cross_instance_token_not_revoked_is_allowed(monkeypatch: pytest.MonkeyPatch) -> None:
    """A token that was never revoked appears as valid on any instance."""
    monkeypatch.setenv('REDIS_URL', 'redis://localhost:6379')
    from services.api.app import pilot

    shared_store: dict[str, str] = {}

    class _SharedFakeRedis:
        def setex(self, key: str, ttl: int, value: str) -> None:
            shared_store[key] = value

        def exists(self, key: str) -> int:
            return 1 if key in shared_store else 0

    pilot._session_blacklist_redis = _SharedFakeRedis()
    assert pilot._is_session_blacklisted('never-revoked-hash') is False


# ---------------------------------------------------------------------------
# 3. bootstrap_live_pilot() raises on validation errors
# ---------------------------------------------------------------------------

def test_bootstrap_live_pilot_raises_on_validation_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    bootstrap_live_pilot() must raise RuntimeError (blocking startup) whenever
    validate_runtime_configuration() returns any errors — not only for Redis errors.
    """
    from services.api.app import main as api_main

    monkeypatch.setattr(
        api_main,
        'validate_runtime_configuration',
        lambda: {
            'errors': ['DATABASE_URL must be configured in production.', 'REDIS_URL is required.'],
            'warnings': [],
            'checks': {},
        },
    )

    with pytest.raises(RuntimeError) as exc_info:
        api_main.bootstrap_live_pilot()

    msg = str(exc_info.value)
    assert 'DATABASE_URL' in msg
    assert 'REDIS_URL' in msg


# ---------------------------------------------------------------------------
# 4. REDIS_TEMPORARILY_DISABLED rejected at production startup
# ---------------------------------------------------------------------------

def test_redis_temporarily_disabled_rejected_at_production_startup(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    Setting REDIS_TEMPORARILY_DISABLED=true in production must cause
    validate_runtime_configuration() to record a required error, which in turn causes
    bootstrap_live_pilot() to raise RuntimeError — blocking the server from starting.
    """
    from services.api.app import pilot, main as api_main

    monkeypatch.setenv('APP_ENV', 'production')
    monkeypatch.delenv('REDIS_URL', raising=False)
    monkeypatch.delenv('UPSTASH_REDIS_REST_URL', raising=False)
    monkeypatch.delenv('UPSTASH_REDIS_REST_TOKEN', raising=False)
    monkeypatch.setenv('REDIS_TEMPORARILY_DISABLED', 'true')

    validation = pilot.validate_runtime_configuration()
    assert validation['checks']['distributed_rate_limiter']['ok'] is False
    assert validation['checks']['distributed_rate_limiter']['required'] is True
    assert any('Memory-backed production rate limiting is rejected' in e for e in validation['errors'])

    # Confirm the startup path actually raises
    monkeypatch.setattr(api_main, 'validate_runtime_configuration', lambda: validation)
    with pytest.raises(RuntimeError) as exc_info:
        api_main.bootstrap_live_pilot()
    assert 'Memory-backed production rate limiting is rejected' in str(exc_info.value)


# ---------------------------------------------------------------------------
# 5. Dev/local mode memory fallback still works without Redis
# ---------------------------------------------------------------------------

def test_dev_mode_memory_fallback_allowed_without_redis(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    In local/dev mode (APP_ENV not in {production, prod, staging}),
    validate_runtime_configuration() must not emit errors for missing Redis.
    """
    from services.api.app import pilot

    monkeypatch.setenv('APP_ENV', 'local')
    monkeypatch.delenv('REDIS_URL', raising=False)
    monkeypatch.delenv('UPSTASH_REDIS_REST_URL', raising=False)
    monkeypatch.delenv('UPSTASH_REDIS_REST_TOKEN', raising=False)
    monkeypatch.delenv('REDIS_TEMPORARILY_DISABLED', raising=False)

    validation = pilot.validate_runtime_configuration()

    assert not any('REDIS_URL' in e for e in validation['errors']), (
        'Dev/local mode must not require Redis — memory fallback is intentional.'
    )
