"""Production startup validation and runtime configuration helpers.

Extracted from pilot.py. Contains env-flag readers, database backend
classification, billing helpers, auth token configuration, and the
validate_runtime_configuration() check that gates production boot.

No reverse import of pilot.py at module load time.
"""
from __future__ import annotations

import hashlib
import logging
import os
from typing import Any
from urllib.parse import urlsplit

from fastapi import HTTPException, status

from services.api.app.managed_keys import (
    load_managed_key,
    managed_key_enforcement_mode,
    managed_key_provider,
)
from services.api.app.evidence_signing import signing_key_status
from services.api.app.secret_crypto import validate_encryption_bootstrap
from services.api.app.paid_launch_readiness import check_billing_readiness

logger = logging.getLogger(__name__)

STARTUP_BOOTSTRAP_ENV = 'RUN_MIGRATIONS_ON_STARTUP'


# ---------------------------------------------------------------------------
# Environment flag and mode helpers
# ---------------------------------------------------------------------------

def env_flag(name: str, default: bool = False) -> bool:
    value = os.getenv(name, 'true' if default else 'false').strip().lower()
    return value in {'1', 'true', 'yes', 'on'}


def _safe_int_env(name: str, default: int, *, minimum: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        value = int(raw.strip())
    except (TypeError, ValueError):
        logger.warning('db_connect_option_invalid option=%s value=%s fallback=%s', name, raw, default)
        return default
    if value < minimum:
        logger.warning(
            'db_connect_option_out_of_range option=%s value=%s minimum=%s fallback=%s',
            name, value, minimum, default,
        )
        return default
    return value


# ---------------------------------------------------------------------------
# Database backend classification
# ---------------------------------------------------------------------------

def database_url() -> str | None:
    value = os.getenv('DATABASE_URL', '').strip()
    return value or None


def resolve_db_backend() -> str:
    db_url = database_url()
    if not db_url:
        return 'sqlite'
    parsed = urlsplit(db_url)
    scheme = (parsed.scheme or '').lower()
    hostname = (parsed.hostname or '').strip().lower()
    if scheme.startswith('sqlite'):
        return 'sqlite'
    if scheme.startswith('postgres'):
        if hostname in {'localhost', '127.0.0.1', 'docker-local'} or hostname.endswith('.docker-local'):
            return 'postgres_local'
        if '.neon.tech' in hostname:
            return 'postgres_hosted_neon'
        return 'postgres_hosted_other'
    return 'sqlite'


def runtime_mode_config_summary() -> dict[str, Any]:
    configured_mode = os.getenv('APP_MODE', 'demo').strip().lower() or 'demo'
    db_backend = resolve_db_backend()
    db_is_postgres = db_backend.startswith('postgres')
    live_mode_requested = env_flag('LIVE_MODE_ENABLED')
    live_mode = live_mode_requested and db_is_postgres
    auth_worker_persistence_enabled = live_mode and db_is_postgres
    return {
        'configured_app_mode': configured_mode,
        'resolved_app_mode': 'live' if live_mode else configured_mode,
        'live_mode_enabled': live_mode,
        'backend_classification': db_backend,
        'auth_worker_persistence_enabled': auth_worker_persistence_enabled,
        'demo_only_mode': not auth_worker_persistence_enabled,
        'live_mode_requested': live_mode_requested,
        'postgres_required_for_live_mode': live_mode_requested and not db_is_postgres,
    }


def runtime_environment_identity() -> dict[str, Any]:
    mode_summary = runtime_mode_config_summary()
    db_url = database_url()
    db_fingerprint = hashlib.sha256(db_url.encode('utf-8')).hexdigest()[:12] if db_url else 'missing'
    return {
        'app_mode': mode_summary['resolved_app_mode'],
        'live_mode_enabled': mode_summary['live_mode_enabled'],
        'railway_environment': os.getenv('RAILWAY_ENVIRONMENT_NAME', '').strip() or None,
        'railway_service': os.getenv('RAILWAY_SERVICE_NAME', '').strip() or None,
        'database_backend': mode_summary['backend_classification'],
        'database_fingerprint': db_fingerprint,
    }


def live_mode_enabled() -> bool:
    return bool(runtime_mode_config_summary()['live_mode_enabled'])


def pilot_mode() -> str:
    return str(runtime_mode_config_summary()['resolved_app_mode'])


# ---------------------------------------------------------------------------
# Authentication key helpers
# ---------------------------------------------------------------------------

def auth_token_secret_configured() -> bool:
    try:
        return bool(load_managed_key('AUTH').material)
    except RuntimeError:
        return False


def token_secret(version: str | None = None) -> str:
    try:
        return load_managed_key('AUTH', version=version).material.decode('utf-8')
    except (RuntimeError, UnicodeDecodeError) as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail='Managed authentication key is not configured.',
        ) from exc


# ---------------------------------------------------------------------------
# Billing runtime helpers
# ---------------------------------------------------------------------------

def billing_provider() -> str:
    return os.getenv('BILLING_PROVIDER', '').strip().lower()


def billing_runtime_status() -> dict[str, Any]:
    provider = billing_provider()
    strict_billing = env_flag('STRICT_PRODUCTION_BILLING')
    readiness = check_billing_readiness()
    if provider == 'none' or not provider:
        return {
            'provider': provider or 'none',
            'status': 'not_configured',
            'available': False,
            'checks': {'provider_selected': bool(provider), 'credentials_present': False},
            'message': readiness['billing_reason'],
            'strict_required': strict_billing,
        }
    if provider not in {'paddle', 'stripe'}:
        return {
            'provider': provider,
            'status': 'misconfigured',
            'available': False,
            'checks': {'provider_supported': False},
            'message': readiness['billing_reason'],
            'strict_required': strict_billing,
        }

    available = bool(readiness['billing_ready'] and readiness['billing_webhook_ready'])
    checks: dict[str, Any] = {
        'provider_supported': True,
        'billing_configuration_ready': bool(readiness['billing_ready']),
        'webhook_ready': bool(readiness['billing_webhook_ready']),
        'missing_env': list(readiness['billing_missing_env']),
    }
    if provider == 'paddle':
        checks['paddle_environment_valid'] = (
            os.getenv('PADDLE_ENVIRONMENT') or ''
        ).strip().lower() in {'sandbox', 'production'}
    return {
        'provider': provider,
        'status': 'healthy' if available else 'degraded',
        'available': available,
        'checks': checks,
        'message': (
            f'{provider.title()} configuration looks healthy.'
            if available
            else readiness['billing_reason']
        ),
        'strict_required': strict_billing,
    }


def _billing_unavailable_detail(
    *, operation: str, expected_provider: str | None = None,
) -> dict[str, Any]:
    billing_status = billing_runtime_status()
    message = billing_status['message']
    reason = 'provider_not_ready'
    if billing_status.get('provider') == 'none':
        reason = 'disabled_by_configuration'
    if expected_provider and billing_status['provider'] != expected_provider:
        message = (
            f"Billing endpoint requires provider={expected_provider} "
            f"but BILLING_PROVIDER={billing_status['provider']}."
        )
        reason = 'provider_mismatch'
    return {
        'error': 'billing_unavailable',
        'code': 'billing_unavailable',
        'reason': reason,
        'operation': operation,
        'provider': billing_status['provider'],
        'status': billing_status['status'],
        'message': message,
        'checks': billing_status.get('checks', {}),
    }


def ensure_billing_available(
    *, operation: str, expected_provider: str | None = None,
) -> None:
    billing_status = billing_runtime_status()
    if expected_provider and billing_status['provider'] != expected_provider:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=_billing_unavailable_detail(operation=operation, expected_provider=expected_provider),
        )
    if not billing_status['available']:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=_billing_unavailable_detail(operation=operation),
        )


def paddle_runtime_config() -> dict[str, Any]:
    readiness = check_billing_readiness()
    environment = (os.getenv('PADDLE_ENVIRONMENT') or '').strip().lower()
    price_id_names = [
        key for key, value in os.environ.items()
        if key.startswith('PADDLE_PRICE_ID_') and value.strip()
    ]
    return {
        'api_key_present': bool(os.getenv('PADDLE_API_KEY', '').strip()),
        'webhook_secret_present': bool(os.getenv('PADDLE_WEBHOOK_SECRET', '').strip()),
        'environment': environment,
        'environment_valid': environment in {'sandbox', 'production'},
        'price_ids_configured': (
            bool(os.getenv('PADDLE_PRICE_ID', '').strip()) or bool(price_id_names)
        ),
        'configured': bool(readiness['billing_ready'] and readiness['billing_webhook_ready']),
        'client_token_present': bool(os.getenv('PADDLE_CLIENT_TOKEN', '').strip()),
    }


# ---------------------------------------------------------------------------
# Full production runtime validation
# ---------------------------------------------------------------------------

def validate_runtime_configuration() -> dict[str, Any]:
    from services.api.app.activity_providers import monitoring_ingestion_runtime

    mode_summary = runtime_mode_config_summary()
    mode = os.getenv('APP_ENV', os.getenv('APP_MODE', 'development')).strip().lower()
    errors: list[str] = []
    warnings: list[str] = []
    checks: dict[str, Any] = {}
    is_production_like = mode in {'production', 'prod'}

    def _record_check(
        name: str, ok: bool, *, required: bool = False,
        detail: str | None = None, severity: str = 'error',
    ) -> None:
        checks[name] = {'ok': ok, 'required': required, 'severity': severity, 'detail': detail}
        if required and not ok and detail:
            if severity == 'warning':
                warnings.append(detail)
            else:
                errors.append(detail)

    try:
        monitoring_runtime = monitoring_ingestion_runtime()
        monitoring_live_degraded = (
            monitoring_runtime.get('mode') == 'live' and bool(monitoring_runtime.get('degraded'))
        )
        _record_check(
            'monitoring_live_rpc_config',
            not monitoring_live_degraded,
            required=bool(monitoring_runtime.get('mode') == 'live'),
            detail=f"MONITORING_INGESTION_MODE=live requires RPC config: {monitoring_runtime.get('reason')}",
        )
    except Exception as exc:
        _record_check(
            'monitoring_live_rpc_config', False, required=False,
            detail=f'monitoring ingestion runtime check failed: {exc}', severity='warning',
        )

    if is_production_like:
        _record_check(
            'live_mode_enabled',
            mode_summary['live_mode_requested'],
            required=True,
            detail=(
                'LIVE_MODE_ENABLED must be true in production. Disabling live mode in production forces '
                'monitoring runtime into offline/unconfigured fallback behavior.'
            ),
        )
        _record_check(
            'database_url', bool(database_url()), required=True,
            detail='DATABASE_URL must be configured when LIVE_MODE_ENABLED=true in production.',
        )
        _record_check(
            'database_backend_postgres',
            mode_summary['backend_classification'].startswith('postgres'),
            required=mode_summary['live_mode_requested'],
            detail='Postgres required when LIVE_MODE_ENABLED=true in production.',
        )
        managed_provider_configured = managed_key_provider() != 'env'
        strict_managed_keys = managed_key_enforcement_mode() == 'strict'
        _record_check(
            'managed_key_provider',
            managed_provider_configured,
            required=strict_managed_keys,
            severity='error' if strict_managed_keys else 'warning',
            detail=(
                'Production is using legacy environment-backed cryptographic keys. Configure '
                'MANAGED_KEY_PROVIDER and key secret IDs, then set MANAGED_KEY_ENFORCEMENT=strict.'
            ),
        )
        _record_check(
            'auth_token_secret', auth_token_secret_configured(), required=True,
            detail='Authentication key must be configured in production.',
        )
        _KNOWN_WEAK_SECRETS = {
            'changeme', 'local', 'test', 'secret', 'password',
            'decoda-dev-signing-secret-not-for-production',
            'proofpass123!', 'pdl_whsec_local',
            'replace-with-long-random-secret',
        }
        try:
            auth_secret_raw = token_secret()
        except HTTPException:
            auth_secret_raw = ''
        if auth_secret_raw and auth_secret_raw.lower() in _KNOWN_WEAK_SECRETS:
            _record_check(
                'auth_token_secret_not_default', False, required=True,
                detail='AUTH_TOKEN_SECRET is a known weak or default value. Set a strong random secret in production.',
            )

        evidence_key = signing_key_status()
        _record_check(
            'export_signing_secret',
            bool(evidence_key['configured']) and bool(evidence_key['strong']),
            required=True,
            detail=str(evidence_key.get('error') or 'A strong evidence-signing key must be configured in production.'),
        )

        try:
            validate_encryption_bootstrap()
            _record_check('secret_encryption_key', True, required=True, detail='Encryption key is configured.')
        except Exception as exc:
            _record_check('secret_encryption_key', False, required=True, detail=str(exc))

        email_provider = os.getenv('EMAIL_PROVIDER', 'console').strip().lower() or 'console'
        _record_check(
            'email_provider_set', bool(email_provider), required=True,
            detail='EMAIL_PROVIDER must be configured in production.',
        )
        _record_check(
            'email_provider_not_console',
            email_provider != 'console',
            required=True,
            detail=(
                'EMAIL_PROVIDER=console is not allowed in production. Configure '
                'EMAIL_PROVIDER=resend and EMAIL_RESEND_API_KEY.'
            ),
        )
        _record_check(
            'email_from',
            bool(os.getenv('EMAIL_FROM', '').strip()),
            required=True,
            detail='EMAIL_FROM must be configured in production.',
        )
        if email_provider == 'resend':
            _record_check(
                'email_resend_api_key',
                bool(os.getenv('EMAIL_RESEND_API_KEY', '').strip()),
                required=True,
                detail='EMAIL_RESEND_API_KEY is required when EMAIL_PROVIDER=resend in production.',
            )

        redis_url = os.getenv('REDIS_URL', '').strip()
        upstash_url = os.getenv('UPSTASH_REDIS_REST_URL', '').strip()
        upstash_token = os.getenv('UPSTASH_REDIS_REST_TOKEN', '').strip()
        distributed_rate_limiter_configured = bool(redis_url or (upstash_url and upstash_token))
        memory_override_requested = env_flag('REDIS_TEMPORARILY_DISABLED') or env_flag(
            'ALLOW_IN_MEMORY_RATE_LIMIT_IN_PRODUCTION'
        )
        if distributed_rate_limiter_configured:
            _record_check(
                'distributed_rate_limiter', True, required=True,
                detail='Redis/Upstash distributed rate limiter is configured. Multi-process rate limiting is active.',
            )
            checks['redis_configured'] = True
            checks['redis_status'] = 'configured'
            checks['rate_limit_backend'] = 'redis' if redis_url else 'upstash'
            checks['rate_limit_enterprise_ready'] = True
        else:
            detail = (
                'Memory-backed production rate limiting is rejected. Remove the memory override and configure '
                'REDIS_URL or UPSTASH_REDIS_REST_URL+UPSTASH_REDIS_REST_TOKEN.'
                if memory_override_requested
                else 'REDIS_URL or UPSTASH_REDIS_REST_URL+UPSTASH_REDIS_REST_TOKEN is required for '
                'production rate limiting. Per-process memory limiting is not a deployment path.'
            )
            _record_check('distributed_rate_limiter', False, required=True, detail=detail)
            checks['redis_configured'] = False
            checks['redis_status'] = 'memory_rejected' if memory_override_requested else 'missing'
            checks['rate_limit_backend'] = 'memory'
            checks['rate_limit_enterprise_ready'] = False

        _record_check(
            'shared_alert_stream',
            bool(redis_url),
            required=True,
            detail=(
                'REDIS_URL is configured for workspace-scoped Redis Streams alert delivery.'
                if redis_url
                else 'REDIS_URL is required for bounded, resumable, multi-replica alert streaming.'
            ),
        )
        checks['alert_stream_backend'] = 'redis_streams' if redis_url else 'unavailable'

        billing_status = billing_runtime_status()
        strict_billing = env_flag('STRICT_PRODUCTION_BILLING')
        _record_check(
            'billing_runtime',
            billing_status['available'],
            required=strict_billing,
            detail=(
                'Billing provider is unavailable in strict mode. Configure billing credentials '
                'or set BILLING_PROVIDER=none until launch.'
            ),
            severity='error' if strict_billing else 'warning',
        )
        checks['billing'] = billing_status

    return {'mode': mode, 'errors': errors, 'warnings': warnings, 'checks': checks}
