from __future__ import annotations

import base64
import csv
import hashlib
import hmac
import io
import json
import logging
import os
import re
import secrets
import socket
import threading
import traceback
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from time import monotonic, sleep
from typing import Any, Iterable
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit, urlunsplit
from urllib.request import Request as UrlRequest, urlopen

import importlib
from fastapi import HTTPException, Request, status
from services.api.app.monitorable_target_types import (
    is_monitorable_target_type,
    monitorable_target_types_sql_clause,
    normalize_target_type,
)
from services.api.app.db_failure import (
    db_error_classification_context,
    extract_db_host_from_dsn,
    normalize_db_error_snippet,
)
from services.api.app.secret_crypto import encrypt_secret, read_encrypted_env, validate_encryption_bootstrap
from services.api.app.export_storage import load_export_storage

ROLE_VALUES = {'owner', 'admin', 'analyst', 'viewer', 'workspace_owner', 'workspace_admin', 'workspace_member'}
ROLE_CANONICAL_MAP = {
    'workspace_owner': 'owner',
    'workspace_admin': 'admin',
    'workspace_member': 'analyst',
    'owner': 'owner',
    'admin': 'admin',
    'analyst': 'analyst',
    'viewer': 'viewer',
}
AUTH_WINDOW_SECONDS = 60
AUTH_MAX_ATTEMPTS = 10
CORE_PILOT_TABLES = (
    'users',
    'workspaces',
    'workspace_members',
    'auth_sessions',
    'auth_tokens',
    'mfa_recovery_codes',
    'analysis_runs',
    'alerts',
    'governance_actions',
    'incidents',
    'audit_logs',
    'action_history',
    'workspace_onboarding_states',
)
MONITORING_RUNTIME_REQUIRED_COLUMNS: dict[str, tuple[str, ...]] = {
    'monitored_systems': (
        'last_coverage_telemetry_at',
        'freshness_status',
        'confidence_status',
        'coverage_reason',
    ),
    'monitoring_event_receipts': (
        'evidence_source',
        'telemetry_kind',
    ),
}
MONITORING_RUNTIME_SCHEMA_MIGRATION_HINTS = ('0036', '0037', '0038', '0039')
DEFAULT_DEMO_EMAIL = 'demo@decoda.app'
EMAIL_VERIFICATION_TTL_MINUTES = 60 * 24
PASSWORD_RESET_TTL_MINUTES = 30
SESSION_TTL_HOURS = 24
MFA_RECOVERY_CODE_COUNT = 8
SLACK_OAUTH_STATE_TTL_MINUTES = 10
_rate_limit_lock = threading.Lock()
_rate_limit_state: dict[str, list[float]] = {}
_rate_limit_fallback_warning_lock = threading.Lock()
_rate_limit_fallback_last_emitted: dict[str, float] = {}
RATE_LIMIT_FALLBACK_WARNING_WINDOW_SECONDS = 300
RATE_LIMIT_FALLBACK_REDIS_UNAVAILABLE_KEY = 'rate_limit.fallback.redis_unavailable'
logger = logging.getLogger(__name__)
_redis_rate_limiter: Any | None = None
_redis_rate_limiter_lock = threading.Lock()
_workspace_reconcile_lock = threading.Lock()
_workspace_reconcile_inflight: dict[str, dict[str, Any]] = {}
WORKSPACE_RECONCILE_CACHE_SECONDS = 30
_AUTH_DB_CLASSIFICATIONS = {'quota_exceeded', 'network_unreachable', 'db_unavailable', 'unknown_db_error'}
_AUTH_DB_ERROR_CODE_BY_CLASSIFICATION = {
    'quota_exceeded': 'AUTH_DB_QUOTA_EXCEEDED',
    'network_unreachable': 'AUTH_BACKEND_UNAVAILABLE',
    'db_unavailable': 'AUTH_BACKEND_UNAVAILABLE',
    'unknown_db_error': 'AUTH_BACKEND_UNAVAILABLE',
}
_auth_db_degraded_warning_lock = threading.Lock()
_auth_db_degraded_last_emitted: dict[str, int] = {}
AUTH_DB_DEGRADED_WARNING_WINDOW_SECONDS = 300


STARTUP_BOOTSTRAP_ENV = 'RUN_MIGRATIONS_ON_STARTUP'
MIGRATION_LOCK_KEY = 840174210431559231
MIGRATION_LOCK_WAIT_SECONDS_ENV = 'MIGRATION_LOCK_WAIT_SECONDS'
MIGRATION_LOCK_RETRY_INTERVAL_SECONDS_ENV = 'MIGRATION_LOCK_RETRY_INTERVAL_SECONDS'
MIGRATION_RETRY_ATTEMPTS_ENV = 'MIGRATION_RETRY_ATTEMPTS'
MIGRATION_RETRY_BACKOFF_SECONDS_ENV = 'MIGRATION_RETRY_BACKOFF_SECONDS'
MIGRATION_READINESS_WAIT_SECONDS_ENV = 'MIGRATION_READINESS_WAIT_SECONDS'
MIGRATION_READINESS_POLL_SECONDS_ENV = 'MIGRATION_READINESS_POLL_SECONDS'
MIGRATION_RETRYABLE_ERROR_NAMES = {'DeadlockDetected', 'LockNotAvailable', 'SerializationFailure', 'OperationalError'}


class MigrationExecutionError(RuntimeError):
    def __init__(self, migration_name: str, original: Exception):
        super().__init__(f'migration {migration_name} failed: {original}')
        self.migration_name = migration_name
        self.original = original


def env_flag(name: str, default: bool = False) -> bool:
    value = os.getenv(name, 'true' if default else 'false').strip().lower()
    return value in {'1', 'true', 'yes', 'on'}


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def utc_now_iso() -> str:
    return utc_now().isoformat()


def parse_csv_env(name: str, defaults: list[str]) -> list[str]:
    raw = os.getenv(name, '')
    values = [item.strip() for item in re.split(r'[\n,]', raw) if item.strip()]
    return values or defaults


SEVERITY_RANK = {'low': 1, 'medium': 2, 'high': 3, 'critical': 4}
MONITORING_DEMO_SCENARIOS = {
    'safe',
    'low_risk',
    'medium_risk',
    'high_risk',
    'flash_loan_like',
    'admin_abuse_like',
    'risky_approval_like',
}

ONBOARDING_STEP_ORDER = [
    'workspace_created',
    'industry_profile',
    'asset_added',
    'policy_configured',
    'integration_connected',
    'teammates_invited',
    'analysis_run',
]
ONBOARDING_MANUAL_STEPS = {'industry_profile', 'policy_configured'}
ONBOARDING_PROGRESS_STEP_ORDER = [
    'asset_added',
    'target_created',
    'monitoring_started',
    'evidence_recorded',
]


def _severity_meets_threshold(value: str, threshold: str) -> bool:
    return SEVERITY_RANK.get((value or 'medium').lower(), 2) >= SEVERITY_RANK.get((threshold or 'medium').lower(), 2)


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
        logger.warning('db_connect_option_out_of_range option=%s value=%s minimum=%s fallback=%s', name, value, minimum, default)
        return default
    return value


CONTINUITY_STATUS_VALUES = {'continuous_live', 'degraded', 'offline', 'idle_no_telemetry'}


def evaluate_workspace_monitoring_continuity(
    *,
    now: datetime,
    workspace_configured: bool,
    worker_running: bool,
    last_heartbeat_at: datetime | None,
    last_event_at: datetime | None,
    last_detection_at: datetime | None,
    heartbeat_ttl_seconds: int,
    telemetry_window_seconds: int,
    detection_window_seconds: int,
) -> dict[str, Any]:
    def _freshness_state(ts: datetime | None, *, fresh_seconds: int) -> tuple[str, int | None]:
        if ts is None:
            return 'missing', None
        age_seconds = max(0, int((now - ts).total_seconds()))
        if age_seconds <= max(fresh_seconds, 1):
            return 'fresh', age_seconds
        if age_seconds <= max(fresh_seconds * 3, fresh_seconds + 1):
            return 'stale', age_seconds
        return 'offline', age_seconds

    normalized_heartbeat_ttl_seconds = max(heartbeat_ttl_seconds, 1)
    normalized_telemetry_window_seconds = max(telemetry_window_seconds, 1)
    normalized_detection_window_seconds = max(detection_window_seconds, 1)
    heartbeat_state, heartbeat_age_seconds = _freshness_state(last_heartbeat_at, fresh_seconds=normalized_heartbeat_ttl_seconds)
    event_state, event_age_seconds = _freshness_state(last_event_at, fresh_seconds=normalized_telemetry_window_seconds)
    detection_state, detection_age_seconds = _freshness_state(last_detection_at, fresh_seconds=normalized_detection_window_seconds)
    worker_liveness = 'live' if worker_running else 'offline'
    if last_event_at is None:
        event_throughput_window = 'no_events'
    elif event_age_seconds is not None and event_age_seconds <= normalized_telemetry_window_seconds:
        event_throughput_window = 'in_window'
    elif event_state == 'stale':
        event_throughput_window = 'out_of_window'
    else:
        event_throughput_window = 'offline'

    continuity_reason_codes: list[str] = []
    if not workspace_configured:
        continuity_reason_codes.append('workspace_not_configured')
    if worker_liveness != 'live':
        continuity_reason_codes.append('worker_not_live')
    if heartbeat_state != 'fresh':
        continuity_reason_codes.append(f'heartbeat_{heartbeat_state}')
    if event_state != 'fresh':
        continuity_reason_codes.append(f'event_ingestion_{event_state}')
    if detection_state != 'fresh':
        continuity_reason_codes.append(f'detection_pipeline_{detection_state}')

    no_telemetry = (
        last_heartbeat_at is None
        and last_event_at is None
        and last_detection_at is None
    )
    all_offline_or_missing = all(state in {'offline', 'missing'} for state in (heartbeat_state, event_state, detection_state))
    if no_telemetry:
        continuity_status = 'idle_no_telemetry'
    elif all_offline_or_missing and worker_liveness != 'live':
        continuity_status = 'offline'
    elif worker_liveness == 'live' and heartbeat_state == 'fresh' and event_state == 'fresh' and detection_state == 'fresh':
        continuity_status = 'continuous_live'
    else:
        continuity_status = 'degraded'

    return {
        'continuity_status': continuity_status if continuity_status in CONTINUITY_STATUS_VALUES else 'degraded',
        'continuity_reason_codes': continuity_reason_codes,
        'continuity_signals': {
            'worker_liveness': worker_liveness,
            'heartbeat_freshness': heartbeat_state,
            'event_ingestion_freshness': event_state,
            'detection_pipeline_freshness': detection_state,
            'heartbeat_age_seconds': heartbeat_age_seconds,
            'event_age_seconds': event_age_seconds,
            'detection_age_seconds': detection_age_seconds,
            'event_throughput_window': event_throughput_window,
            'event_throughput_window_seconds': normalized_telemetry_window_seconds,
        },
        'ingestion_freshness': event_state,
        'detection_pipeline_freshness': detection_state,
        'worker_heartbeat_freshness': heartbeat_state,
        'event_throughput_window': event_throughput_window,
        'event_throughput_window_seconds': normalized_telemetry_window_seconds,
    }


def _database_connect_options() -> dict[str, int]:
    return {
        'connect_timeout': _safe_int_env('DB_CONNECT_TIMEOUT_SECONDS', 10, minimum=1),
        'keepalives': _safe_int_env('DB_KEEPALIVES', 1, minimum=0),
        'keepalives_idle': _safe_int_env('DB_KEEPALIVES_IDLE_SECONDS', 30, minimum=1),
        'keepalives_interval': _safe_int_env('DB_KEEPALIVES_INTERVAL_SECONDS', 10, minimum=1),
        'keepalives_count': _safe_int_env('DB_KEEPALIVES_COUNT', 5, minimum=1),
    }


def _resolve_database_url_for_connection(db_url: str) -> str:
    if not env_flag('DB_PREFER_IPV4'):
        return db_url

    parsed = urlsplit(db_url)
    hostname = parsed.hostname
    if not hostname:
        logger.info('db_ipv4_preference_skipped reason=missing_hostname')
        return db_url
    try:
        candidates = socket.getaddrinfo(hostname, parsed.port, socket.AF_INET, socket.SOCK_STREAM)
    except OSError as exc:
        logger.warning('db_ipv4_preference_skipped reason=resolution_failed host=%s error=%s', hostname, exc)
        return db_url

    ipv4_address = next((item[4][0] for item in candidates if item[4]), None)
    if not ipv4_address:
        logger.warning('db_ipv4_preference_skipped reason=no_ipv4_records host=%s', hostname)
        return db_url

    netloc = parsed.netloc
    if '@' in netloc:
        auth_prefix, host_segment = netloc.rsplit('@', 1)
        delimiter = '@'
    else:
        auth_prefix, host_segment = '', netloc
        delimiter = ''

    port_suffix = ''
    if host_segment.startswith('['):
        closing_index = host_segment.find(']')
        if closing_index != -1:
            port_suffix = host_segment[closing_index + 1 :]
    elif ':' in host_segment:
        port_suffix = host_segment[host_segment.rfind(':') :]

    resolved_netloc = f'{auth_prefix}{delimiter}{ipv4_address}{port_suffix}'
    logger.info('db_ipv4_preference_applied host=%s resolved_ipv4=%s', hostname, ipv4_address)
    return urlunsplit((parsed.scheme, resolved_netloc, parsed.path, parsed.query, parsed.fragment))


@contextmanager
def pg_connection() -> Iterable[Any]:
    db_url = database_url()
    if not db_url:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail='Live pilot mode is not configured.')
    psycopg, dict_row = load_psycopg()
    resolved_db_url = _resolve_database_url_for_connection(db_url)
    connect_options = _database_connect_options()
    with psycopg.connect(resolved_db_url, row_factory=dict_row, **connect_options) as connection:
        yield connection


def require_live_mode() -> None:
    mode_summary = runtime_mode_config_summary()
    if mode_summary['live_mode_enabled']:
        return
    if mode_summary['postgres_required_for_live_mode']:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail='Postgres required for live pilot mode. Set DATABASE_URL to a Postgres connection string for local live development.',
        )
    raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail='DATABASE_URL is required for live pilot mode.')


def load_psycopg() -> Any:
    module = importlib.import_module('psycopg')
    rows_module = importlib.import_module('psycopg.rows')
    return module, rows_module.dict_row


def migration_dir() -> Path:
    return Path(__file__).resolve().parents[1] / 'migrations'


def ensure_migration_table(connection: Any) -> None:
    connection.execute(
        '''
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version TEXT PRIMARY KEY,
            applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        '''
    )


def schema_missing_diagnostics(missing_tables: Iterable[str], *, status_value: str = 'missing_tables', reason: str | None = None) -> dict[str, Any]:
    unique_tables = sorted(dict.fromkeys(str(table) for table in missing_tables if str(table).strip()))
    diagnostics: dict[str, Any] = {
        'ready': not unique_tables,
        'status': 'ready' if not unique_tables else status_value,
        'missing_tables': unique_tables,
        'required_tables': list(CORE_PILOT_TABLES),
    }
    if reason:
        diagnostics['reason'] = reason
    return diagnostics


def should_run_startup_migrations() -> bool:
    return env_flag(STARTUP_BOOTSTRAP_ENV)


def startup_schema_init_plan(*, process_role: str = 'api') -> dict[str, Any]:
    normalized_role = (process_role or 'unknown').strip().lower()
    if normalized_role != 'api':
        return {
            'enabled': False,
            'ran': False,
            'applied_versions': [],
            'process_role': normalized_role,
            'reason': 'schema init is disabled for non-api processes',
        }
    enabled = should_run_startup_migrations()
    if not enabled:
        return {
            'enabled': False,
            'ran': False,
            'applied_versions': [],
            'process_role': normalized_role,
            'reason': f'{STARTUP_BOOTSTRAP_ENV} is disabled',
        }
    return {
        'enabled': True,
        'ran': False,
        'applied_versions': [],
        'process_role': normalized_role,
        'reason': f'{STARTUP_BOOTSTRAP_ENV} is enabled',
    }


def run_startup_migrations_if_enabled(*, process_role: str = 'api') -> dict[str, Any]:
    plan = startup_schema_init_plan(process_role=process_role)
    if not plan.get('enabled'):
        return plan
    require_live_mode()
    applied_versions = run_migrations()
    plan['ran'] = True
    plan['applied_versions'] = applied_versions
    try:
        with pg_connection() as connection:
            ensure_pilot_schema(connection)
            missing_runtime_columns = _fetch_missing_runtime_schema_columns(connection)
            plan['monitoring_runtime_missing_columns'] = missing_runtime_columns
            if missing_runtime_columns:
                logger.warning(
                    'startup_monitoring_runtime_schema_incomplete missing_columns=%s migration_hints=%s',
                    missing_runtime_columns,
                    ', '.join(MONITORING_RUNTIME_SCHEMA_MIGRATION_HINTS),
                )
    except Exception:
        logger.exception(
            'startup_monitoring_runtime_schema_check_failed migration_hints=%s',
            ', '.join(MONITORING_RUNTIME_SCHEMA_MIGRATION_HINTS),
        )
    return plan


def validate_runtime_configuration() -> dict[str, Any]:
    mode_summary = runtime_mode_config_summary()
    mode = os.getenv('APP_ENV', os.getenv('APP_MODE', 'development')).strip().lower()
    errors: list[str] = []
    warnings: list[str] = []
    checks: dict[str, dict[str, Any]] = {}
    is_production_like = mode in {'production', 'prod'}

    def _record_check(name: str, ok: bool, *, required: bool = False, detail: str | None = None, severity: str = 'error') -> None:
        checks[name] = {'ok': ok, 'required': required, 'severity': severity, 'detail': detail}
        if required and not ok and detail:
            if severity == 'warning':
                warnings.append(detail)
            else:
                errors.append(detail)

    try:
        from services.api.app.activity_providers import monitoring_ingestion_runtime

        monitoring_runtime = monitoring_ingestion_runtime()
        monitoring_live_degraded = monitoring_runtime.get('mode') == 'live' and bool(monitoring_runtime.get('degraded'))
        _record_check(
            'monitoring_live_rpc_config',
            not monitoring_live_degraded,
            required=bool(monitoring_runtime.get('mode') == 'live'),
            detail=f"MONITORING_INGESTION_MODE=live requires RPC config: {monitoring_runtime.get('reason')}",
        )
    except Exception as exc:
        _record_check('monitoring_live_rpc_config', False, required=False, detail=f'monitoring ingestion runtime check failed: {exc}', severity='warning')

    if is_production_like:
        _record_check(
            'live_mode_enabled',
            mode_summary['live_mode_requested'],
            required=True,
            detail='LIVE_MODE_ENABLED must be true in production. Disabling live mode in production forces monitoring runtime into offline/unconfigured fallback behavior.',
        )
        _record_check('database_url', bool(database_url()), required=True, detail='DATABASE_URL must be configured when LIVE_MODE_ENABLED=true in production.')
        _record_check(
            'database_backend_postgres',
            mode_summary['backend_classification'].startswith('postgres'),
            required=mode_summary['live_mode_requested'],
            detail='Postgres required when LIVE_MODE_ENABLED=true in production.',
        )
        _record_check('auth_token_secret', auth_token_secret_configured(), required=True, detail='AUTH_TOKEN_SECRET must be configured in production.')
        try:
            validate_encryption_bootstrap()
            _record_check('secret_encryption_key', True, required=True, detail='SECRET_ENCRYPTION_KEY is configured.')
        except Exception as exc:
            _record_check('secret_encryption_key', False, required=True, detail=str(exc))

        email_provider = _email_provider()
        _record_check('email_provider_set', bool(email_provider), required=True, detail='EMAIL_PROVIDER must be configured in production.')
        _record_check(
            'email_provider_not_console',
            email_provider != 'console',
            required=True,
            detail='EMAIL_PROVIDER=console is not allowed in production. Configure EMAIL_PROVIDER=resend and EMAIL_RESEND_API_KEY.',
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

        _record_check('redis_url', bool(os.getenv('REDIS_URL', '').strip()), required=True, detail='REDIS_URL is required in production for shared auth rate limiting.')

        billing_status = billing_runtime_status()
        strict_billing = env_flag('STRICT_PRODUCTION_BILLING')
        _record_check(
            'billing_runtime',
            billing_status['available'],
            required=strict_billing,
            detail='Billing provider is unavailable in strict mode. Configure billing credentials or set BILLING_PROVIDER=none until launch.',
            severity='error' if strict_billing else 'warning',
        )
        checks['billing'] = billing_status

    return {'mode': mode, 'errors': errors, 'warnings': warnings, 'checks': checks}


def integration_health_snapshot(connection: Any | None = None) -> dict[str, Any]:
    stripe_key = bool(os.getenv('STRIPE_SECRET_KEY', '').strip())
    stripe_hook = bool(os.getenv('STRIPE_WEBHOOK_SECRET', '').strip())
    plan_prices_configured = False
    if connection is not None:
        row = connection.execute("SELECT COUNT(*) AS count FROM plan_entitlements WHERE stripe_price_id IS NOT NULL AND stripe_price_id <> ''").fetchone()
        plan_prices_configured = int((row or {}).get('count') or 0) > 0

    email_provider = _email_provider()
    production = os.getenv('APP_ENV', os.getenv('APP_MODE', 'development')).strip().lower() in {'production', 'prod'}
    email_ready = (email_provider == 'resend' and bool(os.getenv('EMAIL_RESEND_API_KEY', '').strip())) if production else (email_provider == 'console' or bool(os.getenv('EMAIL_RESEND_API_KEY', '').strip()))
    redis_ready = bool(os.getenv('REDIS_URL', '').strip())

    billing_status = billing_runtime_status()
    return {
        'billing': billing_status,
        'stripe': {
            'status': 'healthy' if stripe_key and stripe_hook and plan_prices_configured else 'warning',
            'mode': 'live' if os.getenv('STRIPE_SECRET_KEY', '').strip().startswith('sk_live_') else 'test',
            'checks': {
                'secret_key_present': stripe_key,
                'webhook_secret_present': stripe_hook,
                'price_ids_configured': plan_prices_configured,
            },
            'message': 'Stripe billing is not ready because STRIPE_SECRET_KEY or STRIPE_WEBHOOK_SECRET is missing in production.' if not (stripe_key and stripe_hook) else 'Stripe configuration looks healthy.',
        },
        'email': {
            'status': 'healthy' if email_ready and bool(_email_from()) else 'warning',
            'provider': email_provider,
            'checks': {'from_address_present': bool(_email_from()), 'provider_key_present': email_ready},
            'message': 'Email delivery is disabled or incomplete. Configure EMAIL_PROVIDER, EMAIL_FROM, and provider credentials.' if not email_ready else 'Email configuration looks healthy.',
        },
        'auth_rate_limiter': {
            'status': 'healthy' if redis_ready else ('warning' if not production else 'degraded'),
            'checks': {'redis_url_present': redis_ready, 'shared_limiter': redis_ready},
            'message': 'Redis-backed auth rate limiting configured.' if redis_ready else ('REDIS_URL missing in production: auth throttling is not safely shared.' if production else 'REDIS_URL missing: using in-memory limiter for local development only.'),
        },
        'slack': {
            'status': 'healthy' if slack_oauth_configured() else 'warning',
            'message': 'Slack integrations are configured per workspace. OAuth is available for self-serve installs.' if slack_oauth_configured() else 'Slack OAuth is unavailable until SLACK_CLIENT_ID and SLACK_CLIENT_SECRET are configured.',
            'checks': {'webhook_mode_supported': True, 'bot_mode_supported': True, 'oauth_configured': slack_oauth_configured()},
        },
    }


def get_integration_health(request: Request) -> dict[str, Any]:
    require_live_mode()
    with pg_connection() as connection:
        ensure_pilot_schema(connection)
        user, workspace_context = _require_workspace_admin(connection, request)
        health = integration_health_snapshot(connection)
        health['workspace'] = workspace_context['workspace']
        health['checked_by'] = user['id']
        health['checked_at'] = utc_now_iso()
        return health


def test_integration_email(request: Request) -> dict[str, Any]:
    require_live_mode()
    with pg_connection() as connection:
        ensure_pilot_schema(connection)
        user, workspace_context = _require_workspace_admin(connection, request)
        user_row = connection.execute('SELECT email FROM users WHERE id = %s', (user['id'],)).fetchone()
        to_email = str((user_row or {}).get('email') or '').strip()
        if not to_email:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='Current admin account has no email address.')
        _send_email(to_email, f'[{_email_brand_name()}] Integration health test', 'This is a safe integration test message from Decoda RWA Guard.')
        log_audit(connection, action='integration.email.test', entity_type='workspace', entity_id=workspace_context['workspace_id'], request=request, user_id=user['id'], workspace_id=workspace_context['workspace_id'], metadata={})
        connection.commit()
        return {'sent': True, 'to': to_email}


def test_integration_slack(payload: dict[str, Any], request: Request) -> dict[str, Any]:
    integration_id = str(payload.get('integration_id', '')).strip()
    if not integration_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='integration_id is required.')
    return test_slack_integration(integration_id, request)


def run_migrations() -> list[str]:
    require_live_mode()
    with pg_connection() as connection:
        lock_state = _acquire_migration_lock(connection)
        if not lock_state['acquired']:
            logger.warning(
                'migration runner skipped: another process is holding migration lock key=%s after waiting %.2fs',
                MIGRATION_LOCK_KEY,
                lock_state['waited_seconds'],
            )
            if _wait_for_migration_readiness(connection):
                logger.info('migration runner readiness check passed while lock owner completed schema changes')
                return []
            raise RuntimeError('migration runner could not acquire lock and schema readiness was not reached before timeout')
        try:
            attempts = _migration_int_setting(MIGRATION_RETRY_ATTEMPTS_ENV, default=3, minimum=1)
            retry_backoff_seconds = _migration_float_setting(MIGRATION_RETRY_BACKOFF_SECONDS_ENV, default=1.0, minimum=0.0)
            for attempt in range(1, attempts + 1):
                try:
                    applied_versions = _run_migrations_once(connection)
                    logger.info(
                        'migration runner finished: applied=%s waited_for_lock=%.2fs attempts=%s',
                        len(applied_versions),
                        lock_state['waited_seconds'],
                        attempt,
                    )
                    return applied_versions
                except Exception as exc:
                    transient = _is_transient_migration_error(exc)
                    migration_name = exc.migration_name if isinstance(exc, MigrationExecutionError) else 'unknown'
                    if attempt >= attempts or not transient:
                        raise
                    _rollback_connection_safely(connection, reason='after transient migration failure')
                    delay = retry_backoff_seconds * attempt
                    logger.warning(
                        'migration runner transient error (%s) migration=%s attempt=%s/%s, retrying in %.2fs',
                        exc.__class__.__name__,
                        migration_name,
                        attempt,
                        attempts,
                        delay,
                    )
                    if delay > 0:
                        sleep(delay)
        finally:
            _release_migration_lock(connection)


def _migration_int_setting(name: str, *, default: int, minimum: int) -> int:
    raw_value = os.getenv(name, '').strip()
    if not raw_value:
        return default
    try:
        return max(minimum, int(raw_value))
    except ValueError:
        logger.warning('invalid migration setting %s=%r, using default=%s', name, raw_value, default)
        return default


def _migration_float_setting(name: str, *, default: float, minimum: float) -> float:
    raw_value = os.getenv(name, '').strip()
    if not raw_value:
        return default
    try:
        return max(minimum, float(raw_value))
    except ValueError:
        logger.warning('invalid migration setting %s=%r, using default=%s', name, raw_value, default)
        return default


def _acquire_migration_lock(connection: Any) -> dict[str, Any]:
    wait_timeout_seconds = _migration_float_setting(MIGRATION_LOCK_WAIT_SECONDS_ENV, default=45.0, minimum=0.0)
    retry_interval_seconds = _migration_float_setting(MIGRATION_LOCK_RETRY_INTERVAL_SECONDS_ENV, default=1.0, minimum=0.05)
    started = monotonic()
    while True:
        row = connection.execute('SELECT pg_try_advisory_lock(%s) AS locked', (MIGRATION_LOCK_KEY,)).fetchone() or {}
        if bool(row.get('locked')):
            waited = monotonic() - started
            if waited > 0:
                logger.info('migration lock acquired after waiting %.2fs (key=%s)', waited, MIGRATION_LOCK_KEY)
            return {'acquired': True, 'waited_seconds': waited}
        elapsed = monotonic() - started
        if elapsed >= wait_timeout_seconds:
            return {'acquired': False, 'waited_seconds': elapsed}
        sleep(min(retry_interval_seconds, max(0.01, wait_timeout_seconds - elapsed)))


def _release_migration_lock(connection: Any) -> None:
    _rollback_connection_safely(connection, reason='before migration advisory unlock')
    try:
        connection.execute('SELECT pg_advisory_unlock(%s)', (MIGRATION_LOCK_KEY,))
        logger.info('migration lock released key=%s', MIGRATION_LOCK_KEY)
    except Exception:
        logger.exception('failed to release migration advisory lock key=%s', MIGRATION_LOCK_KEY)


def _rollback_connection_safely(connection: Any, *, reason: str = 'after migration error') -> None:
    rollback = getattr(connection, 'rollback', None)
    if not callable(rollback):
        return
    try:
        rollback()
        logger.info('migration rollback completed reason=%s', reason)
    except Exception:
        logger.exception('migration rollback failed reason=%s', reason)


def _is_transient_migration_error(exc: Exception) -> bool:
    if isinstance(exc, MigrationExecutionError):
        return _is_transient_migration_error(exc.original)
    error_name = exc.__class__.__name__
    if error_name in MIGRATION_RETRYABLE_ERROR_NAMES:
        return True
    message = str(exc).lower()
    return 'deadlock detected' in message or 'could not obtain lock on relation' in message


def _run_migrations_once(connection: Any) -> list[str]:
    applied_versions: list[str] = []
    ensure_migration_table(connection)
    already_applied = {
        row['version'] for row in connection.execute('SELECT version FROM schema_migrations').fetchall()
    }
    for path in sorted(migration_dir().glob('*.sql')):
        if path.name in already_applied:
            continue
        logger.info('migration runner executing file=%s', path.name)
        try:
            connection.execute(path.read_text())
            connection.execute(
                'INSERT INTO schema_migrations (version) VALUES (%s) ON CONFLICT (version) DO NOTHING',
                (path.name,),
            )
            connection.commit()
        except Exception as exc:
            _rollback_connection_safely(connection, reason=f'after migration file failure: {path.name}')
            logger.warning(
                'migration runner failed while executing file=%s error=%s',
                path.name,
                exc.__class__.__name__,
            )
            raise MigrationExecutionError(path.name, exc) from exc
        applied_versions.append(path.name)
    missing_tables = _fetch_missing_pilot_tables(connection)
    if missing_tables:
        raise _schema_missing_http_exception(missing_tables)
    return applied_versions


def _wait_for_migration_readiness(connection: Any) -> bool:
    wait_timeout_seconds = _migration_float_setting(MIGRATION_READINESS_WAIT_SECONDS_ENV, default=300.0, minimum=0.0)
    poll_seconds = _migration_float_setting(MIGRATION_READINESS_POLL_SECONDS_ENV, default=1.0, minimum=0.05)
    started = monotonic()
    while True:
        if _all_migrations_applied(connection):
            return True
        elapsed = monotonic() - started
        if elapsed >= wait_timeout_seconds:
            logger.error('migration readiness wait timed out after %.2fs', elapsed)
            return False
        sleep(min(poll_seconds, max(0.01, wait_timeout_seconds - elapsed)))


def _all_migrations_applied(connection: Any) -> bool:
    versions = sorted(path.name for path in migration_dir().glob('*.sql'))
    try:
        ensure_migration_table(connection)
        applied_versions = {
            row['version'] for row in connection.execute('SELECT version FROM schema_migrations').fetchall()
        }
    except Exception as exc:
        _rollback_connection_safely(connection, reason='after migration readiness check failure')
        logger.info('migration readiness check pending reason=%s', exc.__class__.__name__)
        return False
    pending_count = len([version for version in versions if version not in applied_versions])
    if pending_count > 0:
        logger.info('migration readiness check pending remaining=%s', pending_count)
        return False
    missing_tables = _fetch_missing_pilot_tables(connection)
    if missing_tables:
        logger.info('migration readiness check pending missing_tables=%s', ','.join(missing_tables))
        return False
    return True


def _missing_relation_error(exc: Exception) -> bool:
    message = str(exc).lower()
    return exc.__class__.__name__ == 'UndefinedTable' or 'does not exist' in message and 'relation' in message


def _onboarding_query_unavailable_error(exc: Exception) -> bool:
    if _missing_relation_error(exc):
        return True
    if isinstance(exc, AssertionError):
        message = str(exc).lower()
        return 'unexpected sql' in message and 'workspace_onboarding_states' in message
    return False


def schema_missing_error_payload(missing_tables: Iterable[str]) -> dict[str, Any]:
    diagnostics = schema_missing_diagnostics(missing_tables)
    unique_tables = diagnostics['missing_tables']
    table_list = ', '.join(unique_tables) if unique_tables else 'unknown'
    return {
        'code': 'pilot_schema_missing',
        'detail': (
            'Pilot auth schema is not initialized. '
            f'Missing required tables: {table_list}. '
            'Run services/api/scripts/migrate.py before using live auth routes.'
        ),
        'message': (
            'Pilot auth schema is not initialized. '
            f'Missing required tables: {table_list}. '
            'Run services/api/scripts/migrate.py before using live auth routes.'
        ),
        'missingTables': unique_tables,
        'pilotSchemaReady': False,
        'schemaDiagnostics': diagnostics,
    }


def _schema_missing_http_exception(missing_tables: Iterable[str]) -> HTTPException:
    payload = schema_missing_error_payload(missing_tables)
    return HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail=payload['detail'],
        headers={
            'X-Decoda-Error-Code': str(payload['code']),
            'X-Decoda-Missing-Tables': ','.join(payload['missingTables']),
        },
    )


def _fetch_missing_pilot_tables(connection: Any) -> list[str]:
    rows = connection.execute(
        '''
        SELECT required.table_name
        FROM unnest(%s::text[]) AS required(table_name)
        WHERE to_regclass(required.table_name) IS NULL
        ORDER BY required.table_name
        ''',
        (list(CORE_PILOT_TABLES),),
    ).fetchall()
    return [str(row['table_name']) for row in rows]


def ensure_pilot_schema(connection: Any) -> None:
    missing_tables = _fetch_missing_pilot_tables(connection)
    if missing_tables:
        raise _schema_missing_http_exception(missing_tables)


def _fetch_missing_runtime_schema_columns(connection: Any) -> list[str]:
    missing: list[str] = []
    for table_name, required_columns in MONITORING_RUNTIME_REQUIRED_COLUMNS.items():
        table_exists_row = connection.execute('SELECT to_regclass(%s) IS NOT NULL AS exists', (table_name,)).fetchone()
        table_exists = bool((table_exists_row or {}).get('exists'))
        if not table_exists:
            missing.extend([f'{table_name}.{column}' for column in required_columns])
            continue
        rows = connection.execute(
            '''
            SELECT required.column_name
            FROM unnest(%s::text[]) AS required(column_name)
            WHERE NOT EXISTS (
                SELECT 1
                FROM information_schema.columns columns
                WHERE columns.table_schema = 'public'
                  AND columns.table_name = %s
                  AND columns.column_name = required.column_name
            )
            ORDER BY required.column_name
            ''',
            (list(required_columns), table_name),
        ).fetchall()
        missing.extend([f'{table_name}.{str(row["column_name"])}' for row in rows])
    return sorted(dict.fromkeys(missing))


def ensure_monitoring_runtime_schema_capabilities(connection: Any) -> None:
    missing_columns = _fetch_missing_runtime_schema_columns(connection)
    if not missing_columns:
        return
    first_missing = missing_columns[0]
    hint = ', '.join(MONITORING_RUNTIME_SCHEMA_MIGRATION_HINTS)
    logger.warning(
        'monitoring_runtime_schema_incomplete missing_columns=%s migration_hints=%s',
        missing_columns,
        hint,
    )
    raise HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail={
            'code': 'runtime_schema_incomplete',
            'message': (
                'Monitoring runtime schema is incomplete. '
                f'Apply migrations {hint} to enable runtime telemetry fields.'
            ),
            'configuration_reason': 'runtime_schema_incomplete',
            'status_reason': f'runtime_schema_column_missing:{first_missing}',
            'missing_columns': missing_columns,
            'migration_hints': list(MONITORING_RUNTIME_SCHEMA_MIGRATION_HINTS),
        },
    )


def pilot_schema_status() -> dict[str, Any]:
    if not live_mode_enabled():
        return schema_missing_diagnostics(CORE_PILOT_TABLES, status_value='not_configured')
    try:
        with pg_connection() as connection:
            missing_tables = _fetch_missing_pilot_tables(connection)
    except HTTPException:
        raise
    except Exception as exc:
        return schema_missing_diagnostics((), status_value='check_failed', reason=str(exc))
    return schema_missing_diagnostics(missing_tables)


def demo_seed_status(email: str = DEFAULT_DEMO_EMAIL) -> dict[str, Any]:
    normalized_email = email.strip().lower() or DEFAULT_DEMO_EMAIL
    if not live_mode_enabled():
        return {
            'present': False,
            'status': 'not_configured',
            'email': normalized_email,
        }
    try:
        with pg_connection() as connection:
            ensure_pilot_schema(connection)
            user = connection.execute(
                '''
                SELECT
                    users.id,
                    users.current_workspace_id,
                    workspaces.slug,
                    EXISTS (
                        SELECT 1
                        FROM workspace_members
                        WHERE workspace_members.user_id = users.id
                    ) AS has_membership,
                    EXISTS (
                        SELECT 1
                        FROM workspace_members
                        WHERE workspace_members.user_id = users.id
                          AND workspace_members.workspace_id = users.current_workspace_id
                    ) AS has_current_workspace_membership
                FROM users
                LEFT JOIN workspaces ON workspaces.id = users.current_workspace_id
                WHERE users.email = %s
                ''',
                (normalized_email,),
            ).fetchone()
    except HTTPException as exc:
        return {
            'present': False,
            'status': 'schema_missing' if exc.status_code == status.HTTP_503_SERVICE_UNAVAILABLE else 'check_failed',
            'email': normalized_email,
            'reason': str(exc.detail),
        }
    except Exception as exc:
        return {
            'present': False,
            'status': 'check_failed',
            'email': normalized_email,
            'reason': str(exc),
        }
    workspace_present = bool(user and user['current_workspace_id'] and user['slug'])
    membership_present = bool(user and (user['has_current_workspace_membership'] or user['has_membership']))
    return {
        'present': bool(user and workspace_present and membership_present),
        'status': 'present' if user and workspace_present and membership_present else 'missing',
        'email': normalized_email,
        'workspace_slug': None if user is None else user['slug'],
        'user_present': user is not None,
        'workspace_present': workspace_present,
        'membership_present': membership_present,
    }


def _b64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode('utf-8').rstrip('=')


def _b64url_decode(value: str) -> bytes:
    padding = '=' * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding)


def auth_token_secret_configured() -> bool:
    return bool(os.getenv('AUTH_TOKEN_SECRET', '').strip() or os.getenv('JWT_SECRET', '').strip())


def token_secret() -> str:
    value = os.getenv('AUTH_TOKEN_SECRET', '').strip() or os.getenv('JWT_SECRET', '').strip()
    if not value:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail='AUTH_TOKEN_SECRET is not configured.')
    return value


def _auth_token_hash(value: str) -> str:
    return hashlib.sha256(f'{token_secret()}::{value}'.encode('utf-8')).hexdigest()


def _normalize_workspace_role(role: str) -> str:
    normalized = ROLE_CANONICAL_MAP.get(role.strip().lower(), '')
    if not normalized:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='Invalid workspace role.')
    return normalized


def _require_strong_password(password: str) -> None:
    _require_password(password)
    if not re.search(r'[A-Z]', password) or not re.search(r'[a-z]', password) or not re.search(r'\d', password):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail='Password must include upper-case, lower-case, and numeric characters.',
        )


def _store_session(connection: Any, user_id: str, token: str, workspace_id: str | None = None, request: Request | None = None) -> None:
    connection.execute(
        '''
        INSERT INTO auth_sessions (id, user_id, workspace_id, session_token_hash, auth_mode, created_at, updated_at, expires_at, metadata, ip_address, user_agent)
        VALUES (%s, %s, %s, %s, %s, NOW(), NOW(), NOW() + (%s || ' hours')::interval, '{}'::jsonb, %s, %s)
        ''',
        (
            str(uuid.uuid4()),
            user_id,
            workspace_id,
            _auth_token_hash(token),
            'bearer_token',
            SESSION_TTL_HOURS,
            request.client.host if request and request.client else None,
            request.headers.get('user-agent') if request else None,
        ),
    )


def _create_user_token(connection: Any, user_id: str, purpose: str, ttl_minutes: int, request: Request | None = None) -> str:
    raw_token = secrets.token_urlsafe(32)
    connection.execute(
        '''
        INSERT INTO auth_tokens (id, user_id, token_hash, purpose, expires_at, created_at, metadata)
        VALUES (%s, %s, %s, %s, NOW() + (%s || ' minutes')::interval, NOW(), %s::jsonb)
        ''',
        (
            str(uuid.uuid4()),
            user_id,
            _auth_token_hash(raw_token),
            purpose,
            ttl_minutes,
            _json_dumps({'ip_address': request.client.host if request and request.client else None}),
        ),
    )
    return raw_token


def _queue_background_job(connection: Any, *, job_type: str, payload: dict[str, Any], max_attempts: int = 5) -> str:
    job_id = str(uuid.uuid4())
    connection.execute(
        '''
        INSERT INTO background_jobs (id, job_type, payload, status, attempts, max_attempts, run_after, created_at, updated_at)
        VALUES (%s, %s, %s::jsonb, 'queued', 0, %s, NOW(), NOW(), NOW())
        ''',
        (job_id, job_type, _json_dumps(payload), max_attempts),
    )
    return job_id


def _email_provider() -> str:
    return os.getenv('EMAIL_PROVIDER', 'console').strip().lower() or 'console'


def _email_from() -> str:
    return os.getenv('EMAIL_FROM', 'no-reply@decoda.app').strip() or 'no-reply@decoda.app'


def _email_brand_name() -> str:
    return os.getenv('EMAIL_BRAND_NAME', 'Decoda RWA Guard').strip() or 'Decoda RWA Guard'


def _send_email(to_email: str, subject: str, text_body: str, html_body: str | None = None) -> None:
    provider = _email_provider()
    if provider == 'console':
        logger.info(
            'console email provider delivered message',
            extra={'event': 'email.delivered.console', 'to': to_email, 'subject': subject, 'text': text_body},
        )
        return
    if provider == 'resend':
        api_key = os.getenv('EMAIL_RESEND_API_KEY', '').strip()
        if not api_key:
            raise RuntimeError('EMAIL_RESEND_API_KEY is required when EMAIL_PROVIDER=resend.')
        payload = {
            'from': _email_from(),
            'to': [to_email],
            'subject': subject,
            'text': text_body,
        }
        if html_body:
            payload['html'] = html_body
        request = UrlRequest(
            'https://api.resend.com/emails',
            method='POST',
            data=json.dumps(payload).encode('utf-8'),
            headers={'Content-Type': 'application/json', 'Authorization': f'Bearer {api_key}'},
        )
        try:
            with urlopen(request, timeout=8):
                return
        except (HTTPError, URLError) as exc:
            raise RuntimeError(f'Failed to deliver email via Resend: {exc}') from exc
    raise RuntimeError('EMAIL_PROVIDER must be one of: console, resend')


def _email_message(purpose: str, *, token: str | None = None) -> tuple[str, str]:
    brand = _email_brand_name()
    app_url = os.getenv('APP_PUBLIC_URL', 'http://localhost:3000').rstrip('/')
    if purpose == 'email_verification':
        url = f'{app_url}/verify-email?token={token}'
        return (f'[{brand}] Verify your email', f'Welcome to {brand}. Verify your email: {url}')
    if purpose == 'password_reset':
        url = f'{app_url}/reset-password?token={token}'
        return (f'[{brand}] Reset your password', f'Reset your {brand} password: {url}')
    if purpose == 'password_reset_confirmation':
        return (f'[{brand}] Password changed', f'Your {brand} password was changed successfully.')
    raise RuntimeError(f'Unsupported email purpose: {purpose}')


def _dispatch_transactional_email(
    connection: Any,
    *,
    to_email: str,
    purpose: str,
    token: str | None = None,
    request: Request | None = None,
) -> None:
    subject, text_body = _email_message(purpose, token=token)
    mode = os.getenv('BACKGROUND_JOBS_MODE', 'inline').strip().lower() or 'inline'
    if mode == 'inline':
        _send_email(to_email, subject, text_body)
        return
    _queue_background_job(
        connection,
        job_type='send_email',
        payload={
            'to_email': to_email,
            'subject': subject,
            'text_body': text_body,
            'workspace_id': None,
            'request_ip': request.client.host if request and request.client else None,
        },
    )


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode('utf-8'), salt=salt, n=2**14, r=8, p=1)
    return f"scrypt${_b64url(salt)}${_b64url(digest)}"


def verify_password(password: str, encoded_password: str) -> bool:
    try:
        scheme, salt_raw, digest_raw = encoded_password.split('$', 2)
    except ValueError:
        return False
    if scheme != 'scrypt':
        return False
    salt = _b64url_decode(salt_raw)
    expected = _b64url_decode(digest_raw)
    candidate = hashlib.scrypt(password.encode('utf-8'), salt=salt, n=2**14, r=8, p=1)
    return hmac.compare_digest(candidate, expected)


def create_access_token(user_id: str, session_version: int = 1) -> str:
    user_id = str(user_id)
    payload = {
        'sub': user_id,
        'exp': int((utc_now() + timedelta(hours=24)).timestamp()),
        'iat': int(utc_now().timestamp()),
        'jti': str(uuid.uuid4()),
        'sv': int(session_version),
    }
    payload_bytes = json.dumps(payload, separators=(',', ':'), sort_keys=True).encode('utf-8')
    payload_segment = _b64url(payload_bytes)
    signature = hmac.new(token_secret().encode('utf-8'), payload_segment.encode('utf-8'), hashlib.sha256).digest()
    return f'{payload_segment}.{_b64url(signature)}'


def decode_access_token(token: str) -> dict[str, Any]:
    try:
        payload_segment, signature_segment = token.split('.', 1)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail='Invalid access token.') from exc
    expected_signature = hmac.new(token_secret().encode('utf-8'), payload_segment.encode('utf-8'), hashlib.sha256).digest()
    if not hmac.compare_digest(expected_signature, _b64url_decode(signature_segment)):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail='Invalid access token signature.')
    payload = json.loads(_b64url_decode(payload_segment))
    if int(payload.get('exp', 0)) < int(utc_now().timestamp()):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail='Access token expired.')
    return payload


def _validate_session(connection: Any, token: str, payload: dict[str, Any]) -> None:
    session_hash = _auth_token_hash(token)
    session = connection.execute(
        '''
        SELECT revoked_at, expires_at
        FROM auth_sessions
        WHERE session_token_hash = %s
        ''',
        (session_hash,),
    ).fetchone()
    if session is None or session['revoked_at'] is not None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail='Session is no longer active.')
    if session['expires_at'] and session['expires_at'] < utc_now():
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail='Session expired.')
    user_session_version = connection.execute('SELECT session_version FROM users WHERE id = %s', (payload.get('sub'),)).fetchone()
    if not user_session_version or int(payload.get('sv', 0)) != int(user_session_version['session_version']):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail='Session version is no longer valid.')
    connection.execute(
        'UPDATE auth_sessions SET last_seen_at = NOW(), updated_at = NOW() WHERE session_token_hash = %s',
        (session_hash,),
    )


def enforce_auth_rate_limit(request: Request, action: str) -> None:
    client_host = request.client.host if request.client else 'unknown'
    redis_url = os.getenv('REDIS_URL', '').strip()
    if redis_url:
        global _redis_rate_limiter
        with _redis_rate_limiter_lock:
            if _redis_rate_limiter is None:
                redis_module = importlib.import_module('redis')
                _redis_rate_limiter = redis_module.Redis.from_url(redis_url, decode_responses=True)
        key = f'pilot:rate:{action}:{client_host}'
        try:
            attempts = int(_redis_rate_limiter.incr(key))
            if attempts == 1:
                _redis_rate_limiter.expire(key, AUTH_WINDOW_SECONDS)
            if attempts > AUTH_MAX_ATTEMPTS:
                raise HTTPException(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    detail='Too many authentication attempts. Please retry shortly.',
                )
            return
        except HTTPException:
            raise
        except Exception as exc:
            condensed_error = str(exc).strip().splitlines()[0] if str(exc).strip() else 'unknown_error'
            should_emit_info = False
            now = monotonic()
            with _rate_limit_fallback_warning_lock:
                last_emitted = _rate_limit_fallback_last_emitted.get(RATE_LIMIT_FALLBACK_REDIS_UNAVAILABLE_KEY)
                if last_emitted is None or now - last_emitted >= RATE_LIMIT_FALLBACK_WARNING_WINDOW_SECONDS:
                    _rate_limit_fallback_last_emitted[RATE_LIMIT_FALLBACK_REDIS_UNAVAILABLE_KEY] = now
                    should_emit_info = True
            if should_emit_info:
                logger.info(
                    'redis rate limiter unavailable; falling back to in-memory limiter error=%s',
                    condensed_error,
                    extra={'event': 'rate_limit.fallback', 'fallback_key': RATE_LIMIT_FALLBACK_REDIS_UNAVAILABLE_KEY},
                )
    key = f'{action}:{client_host}'
    cutoff = monotonic() - AUTH_WINDOW_SECONDS
    with _rate_limit_lock:
        attempts = [stamp for stamp in _rate_limit_state.get(key, []) if stamp >= cutoff]
        if len(attempts) >= AUTH_MAX_ATTEMPTS:
            _rate_limit_state[key] = attempts
            raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail='Too many authentication attempts. Please retry shortly.')
        attempts.append(monotonic())
        _rate_limit_state[key] = attempts


def _normalize_email(email: str) -> str:
    value = email.strip().lower()
    if '@' not in value or '.' not in value.split('@', 1)[-1]:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='A valid email address is required.')
    return value


def _require_password(password: str) -> None:
    if len(password) < 10:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='Password must be at least 10 characters long.')


def _slugify(value: str) -> str:
    slug = ''.join(char.lower() if char.isalnum() else '-' for char in value.strip())
    while '--' in slug:
        slug = slug.replace('--', '-')
    slug = slug.strip('-')
    return slug or f'workspace-{secrets.token_hex(3)}'


def _json_dumps(value: Any) -> str:
    return json.dumps(_json_safe_value(value), separators=(',', ':'))


def _json_safe_value(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, uuid.UUID):
        return str(value)
    if hasattr(value, 'isoformat'):
        try:
            return value.isoformat()
        except Exception:
            return str(value)
    if isinstance(value, dict):
        return {str(key): _json_safe_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe_value(item) for item in value]
    return str(value)


def _ensure_membership(connection: Any, user_id: str, workspace_id: str) -> dict[str, Any]:
    membership = connection.execute(
        '''
        SELECT wm.workspace_id, wm.role, w.name, w.slug
        FROM workspace_members wm
        JOIN workspaces w ON w.id = wm.workspace_id
        WHERE wm.user_id = %s AND wm.workspace_id = %s
        ''',
        (user_id, workspace_id),
    ).fetchone()
    if membership is None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail='You do not belong to that workspace.')
    membership['role'] = _normalize_workspace_role(str(membership['role']))
    return membership


def log_audit(
    connection: Any,
    *,
    action: str,
    entity_type: str,
    entity_id: str,
    request: Request | None,
    user_id: str | None,
    workspace_id: str | None,
    metadata: dict[str, Any] | None = None,
) -> None:
    safe_metadata = metadata or {}
    request_id = request.headers.get('x-request-id') if request else None
    ip_address = request.client.host if request and request.client else None
    if request_id and not safe_metadata.get('request_id'):
        safe_metadata = {**safe_metadata, 'request_id': request_id}
    if ip_address and not safe_metadata.get('source_ip'):
        safe_metadata = {**safe_metadata, 'source_ip': ip_address}
    connection.execute(
        '''
        INSERT INTO audit_logs (id, workspace_id, user_id, action, entity_type, entity_id, ip_address, metadata, created_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb, NOW())
        ''',
        (
            str(uuid.uuid4()),
            workspace_id,
            user_id,
            action,
            entity_type,
            entity_id,
            ip_address,
            _json_dumps(safe_metadata),
        ),
    )


def _normalize_incident_status(value: str | None) -> str:
    normalized = str(value or '').strip().lower().replace('_', '-')
    if normalized in {'investigating', 'in-progress', 'triaged'}:
        return 'investigating'
    if normalized in {'contained', 'containment'}:
        return 'contained'
    if normalized in {'resolved', 'closed'}:
        return 'resolved'
    if normalized in {'reopened', 're-opened'}:
        return 'reopened'
    return 'open'


def write_action_history(
    connection: Any,
    *,
    workspace_id: str,
    actor_type: str,
    actor_id: str | None,
    object_type: str,
    object_id: str,
    action_type: str,
    details: dict[str, Any] | None = None,
) -> None:
    connection.execute(
        '''
        INSERT INTO action_history (id, workspace_id, actor_type, actor_id, object_type, object_id, action_type, timestamp, details_json)
        VALUES (%s, %s, %s, %s, %s, %s, %s, NOW(), %s::jsonb)
        ''',
        (
            str(uuid.uuid4()),
            workspace_id,
            actor_type,
            actor_id,
            object_type,
            object_id,
            action_type,
            _json_dumps(details or {}),
        ),
    )


def append_incident_timeline_event(
    connection: Any,
    *,
    workspace_id: str,
    incident_id: str | None,
    event_type: str,
    message: str,
    actor_user_id: str | None,
    metadata: dict[str, Any] | None = None,
) -> None:
    if not incident_id:
        return
    connection.execute(
        '''
        INSERT INTO incident_timeline (id, workspace_id, incident_id, event_type, message, actor_user_id, metadata, created_at)
        VALUES (%s, %s, %s::uuid, %s, %s, %s::uuid, %s::jsonb, NOW())
        ''',
        (
            str(uuid.uuid4()),
            workspace_id,
            incident_id,
            event_type,
            message,
            actor_user_id,
            _json_dumps(metadata or {}),
        ),
    )


def build_user_response(connection: psycopg.Connection, user_id: str) -> dict[str, Any]:
    user = connection.execute(
        '''
        SELECT id, email, full_name, current_workspace_id, created_at, updated_at, last_sign_in_at, email_verified_at, mfa_enabled_at
        FROM users
        WHERE id = %s
        ''',
        (user_id,),
    ).fetchone()
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail='Unknown user.')
    memberships = connection.execute(
        '''
        SELECT wm.workspace_id, wm.role, wm.created_at, w.name, w.slug
        FROM workspace_members wm
        JOIN workspaces w ON w.id = wm.workspace_id
        WHERE wm.user_id = %s
        ORDER BY w.created_at ASC, w.name ASC
        ''',
        (user_id,),
    ).fetchall()
    membership_payload = []
    for membership in memberships:
        workspace_id = str(membership['workspace_id'])
        membership_payload.append(
            {
                'workspace_id': workspace_id,
                'role': _normalize_workspace_role(str(membership['role'])),
                'created_at': (
                    membership['created_at'].isoformat() if hasattr(membership['created_at'], 'isoformat') else str(membership['created_at'])
                ),
                'workspace': {
                    'id': workspace_id,
                    'name': membership['name'],
                    'slug': membership['slug'],
                },
            }
        )
    current_workspace_id = str(user['current_workspace_id']) if user['current_workspace_id'] else None
    membership_workspace_ids = {membership['workspace_id'] for membership in membership_payload}
    if current_workspace_id and current_workspace_id not in membership_workspace_ids:
        logger.warning(
            'user has stale current_workspace_id without membership',
            extra={'event': 'workspace.hydration.stale_current_workspace', 'user_id': str(user['id'])},
        )
        current_workspace_id = None
    if current_workspace_id is None and membership_payload:
        current_workspace_id = membership_payload[0]['workspace_id']
        connection.execute(
            'UPDATE users SET current_workspace_id = %s, updated_at = NOW() WHERE id = %s',
            (current_workspace_id, user_id),
        )
        logger.info(
            'user current workspace hydrated from first membership',
            extra={'event': 'workspace.hydration.backfilled_current_workspace', 'user_id': str(user['id'])},
        )
    current_workspace = next(
        (
            membership['workspace']
            for membership in membership_payload
            if membership['workspace_id'] == current_workspace_id
        ),
        membership_payload[0]['workspace'] if membership_payload else None,
    )
    onboarding_summary = None
    if current_workspace:
        try:
            onboarding_summary = _build_onboarding_response(
                connection,
                workspace_id=current_workspace['id'],
                user_id=str(user['id']),
                workspace_name=current_workspace['name'],
            )
        except Exception as exc:
            if not _onboarding_query_unavailable_error(exc):
                raise
            logger.warning(
                'skipping onboarding summary hydration because onboarding storage is unavailable',
                extra={'event': 'workspace.hydration.onboarding_summary_skipped', 'user_id': str(user['id'])},
            )
    return _json_safe_value(
        {
        'id': str(user['id']),
        'email': user['email'],
        'full_name': user['full_name'],
        'current_workspace_id': current_workspace['id'] if current_workspace else None,
        'created_at': user['created_at'].isoformat() if hasattr(user['created_at'], 'isoformat') else str(user['created_at']),
        'updated_at': user['updated_at'].isoformat() if hasattr(user['updated_at'], 'isoformat') else str(user['updated_at']),
        'last_sign_in_at': user['last_sign_in_at'].isoformat() if user['last_sign_in_at'] else None,
        'email_verified': bool(user['email_verified_at']),
        'email_verified_at': user['email_verified_at'].isoformat() if user['email_verified_at'] else None,
        'mfa_enabled': bool(user['mfa_enabled_at']),
        'current_workspace': current_workspace,
        'onboarding_summary': onboarding_summary,
        'memberships': membership_payload,
    }
    )



def _default_onboarding_state() -> dict[str, bool]:
    return {step: False for step in ONBOARDING_STEP_ORDER}


def _parse_onboarding_state(raw_state: Any) -> dict[str, bool]:
    baseline = _default_onboarding_state()
    if not isinstance(raw_state, dict):
        return baseline
    for step in ONBOARDING_STEP_ORDER:
        baseline[step] = bool(raw_state.get(step))
    return baseline


def _auto_onboarding_state(connection: Any, workspace_id: str) -> dict[str, bool]:
    counts = connection.execute(
        '''
        SELECT
            (SELECT COUNT(*) FROM assets WHERE workspace_id = %(workspace_id)s) AS assets_count,
            (SELECT COUNT(*) FROM targets WHERE workspace_id = %(workspace_id)s) AS targets_count,
            (SELECT COUNT(*) FROM analysis_runs WHERE workspace_id = %(workspace_id)s) AS analysis_count,
            (SELECT COUNT(*) FROM workspace_invitations WHERE workspace_id = %(workspace_id)s) AS invites_count,
            (SELECT COUNT(*) FROM workspace_webhooks WHERE workspace_id = %(workspace_id)s AND enabled = TRUE) AS webhook_count,
            (SELECT COUNT(*) FROM workspace_slack_integrations WHERE workspace_id = %(workspace_id)s AND enabled = TRUE) AS slack_count,
            (SELECT COUNT(*) FROM module_configs WHERE workspace_id = %(workspace_id)s) AS module_config_count
        ''',
        {'workspace_id': workspace_id},
    ).fetchone() or {}
    return {
        'workspace_created': True,
        'industry_profile': False,
        'asset_added': int(counts.get('assets_count') or 0) > 0 or int(counts.get('targets_count') or 0) > 0,
        'policy_configured': int(counts.get('module_config_count') or 0) > 0,
        'integration_connected': int(counts.get('webhook_count') or 0) > 0 or int(counts.get('slack_count') or 0) > 0,
        'teammates_invited': int(counts.get('invites_count') or 0) > 0,
        'analysis_run': int(counts.get('analysis_count') or 0) > 0,
    }


def _ensure_onboarding_record(connection: Any, workspace_id: str, user_id: str) -> dict[str, Any]:
    row = connection.execute(
        'SELECT workspace_id, state, completed_at, updated_at FROM workspace_onboarding_states WHERE workspace_id = %s',
        (workspace_id,),
    ).fetchone()
    if row is not None:
        return row
    initial_state = _json_dumps(_default_onboarding_state())
    connection.execute(
        '''
        INSERT INTO workspace_onboarding_states (workspace_id, state, created_by_user_id, updated_by_user_id, created_at, updated_at)
        VALUES (%s, %s::jsonb, %s, %s, NOW(), NOW())
        ''',
        (workspace_id, initial_state, user_id, user_id),
    )
    return connection.execute(
        'SELECT workspace_id, state, completed_at, updated_at FROM workspace_onboarding_states WHERE workspace_id = %s',
        (workspace_id,),
    ).fetchone()


def _build_onboarding_response(connection: Any, *, workspace_id: str, user_id: str, workspace_name: str | None = None) -> dict[str, Any]:
    row = _ensure_onboarding_record(connection, workspace_id, user_id)
    manual_state = _parse_onboarding_state(row.get('state') if row else {})
    auto_state = _auto_onboarding_state(connection, workspace_id)
    merged_state = {step: bool(manual_state.get(step)) or bool(auto_state.get(step)) for step in ONBOARDING_STEP_ORDER}
    completed_steps = sum(1 for step in ONBOARDING_STEP_ORDER if merged_state.get(step))
    total_steps = len(ONBOARDING_STEP_ORDER)
    complete = completed_steps == total_steps
    if complete and row and not row.get('completed_at'):
        connection.execute(
            'UPDATE workspace_onboarding_states SET completed_at = NOW(), updated_at = NOW() WHERE workspace_id = %s',
            (workspace_id,),
        )
    return {
        'workspace_id': workspace_id,
        'workspace_name': workspace_name,
        'steps': [{
            'key': step,
            'complete': bool(merged_state.get(step)),
            'source': 'manual' if bool(manual_state.get(step)) else ('automatic' if bool(auto_state.get(step)) else 'pending'),
        } for step in ONBOARDING_STEP_ORDER],
        'completed_steps': completed_steps,
        'total_steps': total_steps,
        'progress_percent': int((completed_steps / total_steps) * 100),
        'completed': complete,
        'completed_at': row['completed_at'].isoformat() if row and row.get('completed_at') else None,
        'updated_at': row['updated_at'].isoformat() if row and row.get('updated_at') else utc_now_iso(),
    }


def get_onboarding_progress(request: Request) -> dict[str, Any]:
    require_live_mode()
    with pg_connection() as connection:
        ensure_pilot_schema(connection)
        user = authenticate_with_connection(connection, request)
        workspace_context = resolve_workspace(connection, user['id'], request.headers.get('x-workspace-id'))
        workspace_id = workspace_context['workspace_id']
        counts = connection.execute(
            '''
            SELECT
                (SELECT COUNT(*) FROM assets WHERE workspace_id = %(workspace_id)s AND deleted_at IS NULL) AS assets_count,
                (SELECT COUNT(*) FROM targets WHERE workspace_id = %(workspace_id)s AND deleted_at IS NULL) AS targets_count,
                (SELECT COUNT(*) FROM targets WHERE workspace_id = %(workspace_id)s AND deleted_at IS NULL AND monitoring_enabled = TRUE AND enabled = TRUE AND is_active = TRUE) AS monitoring_targets_count,
                (SELECT COUNT(*) FROM targets WHERE workspace_id = %(workspace_id)s AND deleted_at IS NULL AND last_checked_at IS NOT NULL) AS evaluated_targets_count,
                (SELECT COUNT(*) FROM monitoring_event_receipts WHERE workspace_id = %(workspace_id)s) AS event_receipts_count
            ''',
            {'workspace_id': workspace_id},
        ).fetchone() or {}
        states = {
            'asset_added': int(counts.get('assets_count') or 0) > 0,
            'target_created': int(counts.get('targets_count') or 0) > 0,
            'monitoring_started': int(counts.get('monitoring_targets_count') or 0) > 0,
            'evidence_recorded': int(counts.get('evaluated_targets_count') or 0) > 0 or int(counts.get('event_receipts_count') or 0) > 0,
        }
        steps = [
            {'key': step, 'complete': bool(states.get(step)), 'source': 'automatic' if states.get(step) else 'pending'}
            for step in ONBOARDING_PROGRESS_STEP_ORDER
        ]
        completed_steps = sum(1 for step in steps if step['complete'])
        payload = {
            'workspace_id': workspace_id,
            'workspace_name': workspace_context['workspace']['name'],
            'steps': steps,
            'completed_steps': completed_steps,
            'total_steps': len(ONBOARDING_PROGRESS_STEP_ORDER),
            'progress_percent': int((completed_steps / len(ONBOARDING_PROGRESS_STEP_ORDER)) * 100),
            'completed': completed_steps == len(ONBOARDING_PROGRESS_STEP_ORDER),
            'next_step': next((step['key'] for step in steps if not step['complete']), None),
            'counts': {
                'assets': int(counts.get('assets_count') or 0),
                'targets': int(counts.get('targets_count') or 0),
                'monitoring_targets': int(counts.get('monitoring_targets_count') or 0),
                'evaluated_targets': int(counts.get('evaluated_targets_count') or 0),
                'event_receipts': int(counts.get('event_receipts_count') or 0),
            },
        }
        connection.commit()
        return payload


def get_current_workspace(request: Request) -> dict[str, Any]:
    require_live_mode()
    with pg_connection() as connection:
        ensure_pilot_schema(connection)
        user = authenticate_with_connection(connection, request)
        workspace_context = resolve_workspace(connection, user['id'], request.headers.get('x-workspace-id'))
        connection.commit()
        return {'workspace': workspace_context['workspace']}


def get_onboarding_state(request: Request) -> dict[str, Any]:
    """Legacy endpoint retained for backward compatibility; mirrors /onboarding/progress."""
    payload = get_onboarding_progress(request)
    payload['deprecated'] = True
    payload['source_of_truth'] = '/onboarding/progress'
    return payload


def update_onboarding_state(payload: dict[str, Any], request: Request) -> dict[str, Any]:
    step_key = str(payload.get('step', '')).strip().lower()
    if step_key not in ONBOARDING_STEP_ORDER:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='Invalid onboarding step.')
    complete = bool(payload.get('complete', True))
    if step_key not in ONBOARDING_MANUAL_STEPS:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='Step is system-managed and cannot be updated manually.')
    require_live_mode()
    with pg_connection() as connection:
        ensure_pilot_schema(connection)
        user = authenticate_with_connection(connection, request)
        workspace_context = resolve_workspace(connection, user['id'], request.headers.get('x-workspace-id'))
        row = _ensure_onboarding_record(connection, workspace_context['workspace_id'], user['id'])
        state = _parse_onboarding_state(row.get('state') if row else {})
        state[step_key] = complete
        connection.execute(
            '''
            UPDATE workspace_onboarding_states
            SET state = %s::jsonb, updated_by_user_id = %s, updated_at = NOW(), completed_at = CASE WHEN completed_at IS NOT NULL AND %s = FALSE THEN NULL ELSE completed_at END
            WHERE a.workspace_id = %s
            ''',
            (_json_dumps(state), user['id'], complete, workspace_context['workspace_id']),
        )
        log_audit(
            connection,
            action='onboarding.step_updated',
            entity_type='workspace',
            entity_id=workspace_context['workspace_id'],
            request=request,
            user_id=user['id'],
            workspace_id=workspace_context['workspace_id'],
            metadata={'step': step_key, 'complete': complete},
        )
        response = _build_onboarding_response(
            connection,
            workspace_id=workspace_context['workspace_id'],
            user_id=user['id'],
            workspace_name=workspace_context['workspace']['name'],
        )
        connection.commit()
        return response

def authenticate_request(request: Request) -> dict[str, Any]:
    require_live_mode()
    authorization = request.headers.get('authorization', '')
    if not authorization.startswith('Bearer '):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail='Missing bearer token.')
    token = authorization.split(' ', 1)[1].strip()
    payload = decode_access_token(token)
    user_id = str(payload.get('sub') or '')
    if not user_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail='Token payload missing subject.')
    with pg_connection() as connection:
        ensure_pilot_schema(connection)
        _validate_session(connection, token, payload)
        user = build_user_response(connection, user_id)
    return user


def authenticate_with_connection(connection: psycopg.Connection, request: Request) -> dict[str, Any]:
    authorization = request.headers.get('authorization', '')
    if not authorization.startswith('Bearer '):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail='Missing bearer token.')
    token = authorization.split(' ', 1)[1].strip()
    payload = decode_access_token(token)
    user_id = str(payload.get('sub') or '')
    if not user_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail='Token payload missing subject.')
    _validate_session(connection, token, payload)
    return build_user_response(connection, user_id)


def resolve_workspace(connection: psycopg.Connection, user_id: str, requested_workspace_id: str | None = None) -> dict[str, Any]:
    workspace_id = (requested_workspace_id or '').strip()
    if not workspace_id:
        current = connection.execute('SELECT current_workspace_id FROM users WHERE id = %s', (user_id,)).fetchone()
        workspace_id = str(current['current_workspace_id'] or '') if current else ''
    if not workspace_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='Select or create a workspace before using live mode.')
    membership = _ensure_membership(connection, user_id, workspace_id)
    return {
        'workspace_id': membership['workspace_id'],
        'role': membership['role'],
        'workspace': {
            'id': membership['workspace_id'],
            'name': membership['name'],
            'slug': membership['slug'],
        },
    }


def signup_user(payload: dict[str, Any], request: Request) -> dict[str, Any]:
    require_live_mode()
    email = _normalize_email(str(payload.get('email', '')))
    password = str(payload.get('password', ''))
    _require_strong_password(password)
    full_name = str(payload.get('full_name', '')).strip() or email.split('@', 1)[0]
    workspace_name = str(payload.get('workspace_name', '')).strip() or f"{full_name}'s Workspace"
    password_hash = hash_password(password)
    with pg_connection() as connection:
        ensure_pilot_schema(connection)
        existing = connection.execute('SELECT id FROM users WHERE email = %s', (email,)).fetchone()
        if existing is not None:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail='An account with that email already exists.')
        user_id = str(uuid.uuid4())
        workspace_id = str(uuid.uuid4())
        slug_base = _slugify(workspace_name)
        slug = slug_base
        suffix = 1
        while connection.execute('SELECT 1 FROM workspaces WHERE slug = %s', (slug,)).fetchone() is not None:
            suffix += 1
            slug = f'{slug_base}-{suffix}'
        connection.execute(
            '''
            INSERT INTO users (id, email, password_hash, full_name, current_workspace_id, email_verified_at, session_version, created_at, updated_at, last_sign_in_at)
            VALUES (%s, %s, %s, %s, %s, NULL, 1, NOW(), NOW(), NULL)
            ''',
            (user_id, email, password_hash, full_name, None),
        )
        connection.execute(
            '''
            INSERT INTO workspaces (id, name, slug, created_by_user_id, created_at)
            VALUES (%s, %s, %s, %s, NOW())
            ''',
            (workspace_id, workspace_name, slug, user_id),
        )
        connection.execute(
            '''
            INSERT INTO workspace_members (id, workspace_id, user_id, role, created_at)
            VALUES (%s, %s, %s, %s, NOW())
            ''',
            (str(uuid.uuid4()), workspace_id, user_id, 'owner'),
        )
        connection.execute(
            'UPDATE users SET current_workspace_id = %s, updated_at = NOW() WHERE id = %s',
            (workspace_id, user_id),
        )
        log_audit(
            connection,
            action='auth.signup',
            entity_type='user',
            entity_id=user_id,
            request=request,
            user_id=user_id,
            workspace_id=workspace_id,
            metadata={'email': email, 'workspace_name': workspace_name},
        )
        verification_token = _create_user_token(connection, user_id, 'email_verification', EMAIL_VERIFICATION_TTL_MINUTES, request=request)
        _dispatch_transactional_email(connection, to_email=email, purpose='email_verification', token=verification_token, request=request)
        connection.commit()
        user = build_user_response(connection, user_id)
    return {
        'verification_required': True,
        'verification_token': verification_token if env_flag('AUTH_EXPOSE_DEBUG_TOKENS', default=False) else None,
        'user': user,
    }


def signin_user(payload: dict[str, Any], request: Request) -> dict[str, Any]:
    require_live_mode()
    email = _normalize_email(str(payload.get('email', '')))
    password = str(payload.get('password', ''))

    def _raise_graceful_auth_backend_error(exc: Exception) -> None:
        error_context = db_error_classification_context(exc)
        classification = error_context['classification']
        if classification not in _AUTH_DB_CLASSIFICATIONS:
            return
        request_id = request.headers.get('x-request-id') if request else None
        correlation_id = request_id.strip() if request_id and request_id.strip() else secrets.token_hex(4)
        db_host = extract_db_host_from_dsn(database_url())
        request_path = request.scope.get('path') if isinstance(getattr(request, 'scope', None), dict) else None
        condensed_error = normalize_db_error_snippet(str(exc)) or 'unknown_error'
        warning_key = f'{classification}:{db_host or "unknown"}'
        should_emit_info = False
        now = monotonic()
        window_slot = int(now // AUTH_DB_DEGRADED_WARNING_WINDOW_SECONDS)
        with _auth_db_degraded_warning_lock:
            last_window_slot = _auth_db_degraded_last_emitted.get(warning_key)
            if last_window_slot != window_slot:
                _auth_db_degraded_last_emitted[warning_key] = window_slot
                should_emit_info = True
        if should_emit_info:
            warning_details = ''
            if error_context.get('classification_source'):
                warning_details += ' classification_source=%s'
            if error_context.get('raw_error_snippet'):
                warning_details += ' raw_error_snippet=%s'
            logger.info(
                f'event=auth_db_degraded classification=%s reason=%s db_host=%s request_path=%s '
                f'downgraded_response=%s correlation_id=%s condensed_error=%s{warning_details}',
                classification,
                error_context['reason'],
                db_host,
                request_path or '/auth/signin',
                True,
                correlation_id,
                condensed_error,
                *(value for value in (error_context.get('classification_source'), error_context.get('raw_error_snippet')) if value),
            )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail='Authentication is temporarily unavailable. Please retry in a moment.',
            headers={
                'X-Decoda-Error-Code': _AUTH_DB_ERROR_CODE_BY_CLASSIFICATION.get(classification, 'AUTH_BACKEND_UNAVAILABLE'),
                'X-Decoda-DB-Classification': classification,
                'X-Decoda-Correlation-Id': correlation_id,
            },
        ) from exc

    try:
        with pg_connection() as connection:
            ensure_pilot_schema(connection)
            try:
                user = connection.execute(
                    'SELECT id, password_hash, email_verified_at, session_version, mfa_totp_secret, mfa_enabled_at FROM users WHERE email = %s',
                    (email,),
                ).fetchone()
            except Exception as exc:
                _raise_graceful_auth_backend_error(exc)
                logger.exception('signin_user failed during user lookup', extra={'step': 'fetch_user_by_email', 'email': email})
                raise
            if user is None or not verify_password(password, user['password_hash']):
                logger.warning('signin_user rejected credentials', extra={'event': 'auth.signin.invalid_credentials', 'email': email})
                raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail='Invalid email or password.')
            user_id = str(user['id'])
            if not user['email_verified_at']:
                raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail='Verify your email before signing in.')
            if user['mfa_enabled_at']:
                challenge_token = _create_user_token(connection, user_id, 'mfa_challenge', 10, request=request)
                connection.commit()
                return {'mfa_required': True, 'mfa_token': challenge_token}
            try:
                connection.execute('UPDATE users SET last_sign_in_at = NOW(), updated_at = NOW() WHERE id = %s', (user_id,))
            except Exception as exc:
                _raise_graceful_auth_backend_error(exc)
                logger.exception('signin_user failed during last_sign_in_at update', extra={'step': 'update_last_sign_in_at', 'user_id': user_id})
                raise
            try:
                log_audit(
                    connection,
                    action='auth.signin',
                    entity_type='user',
                    entity_id=user_id,
                    request=request,
                    user_id=user_id,
                    workspace_id=None,
                    metadata={'email': email},
                )
            except Exception as exc:
                _raise_graceful_auth_backend_error(exc)
                logger.exception('signin_user failed during audit log insert', extra={'step': 'insert_audit_log', 'user_id': user_id})
                raise
            connection.commit()
            try:
                hydrated_user = build_user_response(connection, user_id)
                if not hydrated_user.get('current_workspace'):
                    logger.info('signin_user completed without active workspace', extra={'event': 'auth.signin.no_workspace', 'user_id': user_id})
            except Exception as exc:
                _raise_graceful_auth_backend_error(exc)
                logger.exception('signin_user failed during user hydration', extra={'step': 'build_user_response', 'user_id': user_id})
                raise
    except HTTPException:
        raise
    except Exception as exc:
        _raise_graceful_auth_backend_error(exc)
        if _missing_relation_error(exc):
            raise _schema_missing_http_exception(('users',)) from exc
        logger.exception('signin_user failed due to unexpected backend exception', extra={'step': 'unexpected'})
        raise
    try:
        access_token = create_access_token(user_id, int(user.get('session_version') or 1))
        with pg_connection() as connection:
            ensure_pilot_schema(connection)
            _store_session(connection, user_id, access_token, hydrated_user.get('current_workspace_id'), request=request)
            connection.commit()
    except Exception as exc:
        _raise_graceful_auth_backend_error(exc)
        logger.exception('signin_user failed during token creation', extra={'step': 'create_access_token', 'user_id': user_id})
        raise
    logger.info('signin_user succeeded', extra={'event': 'auth.signin.success', 'user_id': user_id})
    return {'access_token': access_token, 'token_type': 'bearer', 'user': hydrated_user}


def mfa_complete_signin(payload: dict[str, Any], request: Request) -> dict[str, Any]:
    require_live_mode()
    challenge_token = str(payload.get('mfa_token', '')).strip()
    code = str(payload.get('code', '')).strip()
    if not challenge_token or not code:
        raise HTTPException(status_code=400, detail='mfa_token and code are required.')
    with pg_connection() as connection:
        ensure_pilot_schema(connection)
        token_row = connection.execute(
            "SELECT id, user_id, expires_at, used_at FROM auth_tokens WHERE token_hash = %s AND purpose = 'mfa_challenge'",
            (_auth_token_hash(challenge_token),),
        ).fetchone()
        if token_row is None or token_row['used_at'] is not None or token_row['expires_at'] < utc_now():
            raise HTTPException(status_code=400, detail='Invalid or expired MFA challenge.')
        user = connection.execute('SELECT id, session_version, mfa_totp_secret FROM users WHERE id = %s', (token_row['user_id'],)).fetchone()
        if user is None:
            raise HTTPException(status_code=401, detail='Unknown user.')
        secret = str(user['mfa_totp_secret'] or '')
        if not secret or not _verify_totp(secret, code):
            recovery = connection.execute(
                'SELECT id FROM mfa_recovery_codes WHERE user_id = %s AND code_hash = %s AND consumed_at IS NULL',
                (token_row['user_id'], _auth_token_hash(code)),
            ).fetchone()
            if recovery is None:
                raise HTTPException(status_code=401, detail='Invalid MFA code.')
            connection.execute('UPDATE mfa_recovery_codes SET consumed_at = NOW() WHERE id = %s', (recovery['id'],))
        connection.execute('UPDATE auth_tokens SET used_at = NOW() WHERE id = %s', (token_row['id'],))
        hydrated_user = build_user_response(connection, str(user['id']))
        access_token = create_access_token(str(user['id']), int(user.get('session_version') or 1))
        _store_session(connection, str(user['id']), access_token, hydrated_user.get('current_workspace_id'), request=request)
        connection.commit()
        return {'access_token': access_token, 'token_type': 'bearer', 'user': hydrated_user}


def signout_user(request: Request) -> dict[str, Any]:
    require_live_mode()
    authorization = request.headers.get('authorization', '')
    token = authorization.split(' ', 1)[1].strip() if authorization.startswith('Bearer ') else ''
    with pg_connection() as connection:
        ensure_pilot_schema(connection)
        user = authenticate_with_connection(connection, request)
        if token:
            connection.execute(
                'UPDATE auth_sessions SET revoked_at = NOW(), updated_at = NOW() WHERE session_token_hash = %s',
                (_auth_token_hash(token),),
            )
        log_audit(
            connection,
            action='auth.signout',
            entity_type='user',
            entity_id=user['id'],
            request=request,
            user_id=user['id'],
            workspace_id=user['current_workspace']['id'] if user['current_workspace'] else None,
            metadata={},
        )
        connection.commit()
    return {'signed_out': True}


def signout_all_sessions(request: Request) -> dict[str, Any]:
    require_live_mode()
    with pg_connection() as connection:
        ensure_pilot_schema(connection)
        user = authenticate_with_connection(connection, request)
        connection.execute(
            'UPDATE auth_sessions SET revoked_at = NOW(), updated_at = NOW() WHERE user_id = %s AND revoked_at IS NULL',
            (user['id'],),
        )
        connection.execute(
            'UPDATE users SET session_version = session_version + 1, updated_at = NOW() WHERE id = %s',
            (user['id'],),
        )
        log_audit(connection, action='auth.signout_all', entity_type='user', entity_id=user['id'], request=request, user_id=user['id'], workspace_id=user['current_workspace_id'], metadata={})
        connection.commit()
        return {'signed_out_all': True}


def list_active_sessions(request: Request) -> dict[str, Any]:
    require_live_mode()
    with pg_connection() as connection:
        ensure_pilot_schema(connection)
        user = authenticate_with_connection(connection, request)
        rows = connection.execute(
            '''
            SELECT id, auth_mode, created_at, updated_at, expires_at, last_seen_at, revoked_at, ip_address, user_agent
            FROM auth_sessions
            WHERE user_id = %s
            ORDER BY created_at DESC
            ''',
            (user['id'],),
        ).fetchall()
        return {'sessions': [_json_safe_value(row) for row in rows]}


def revoke_session(request: Request, session_id: str) -> dict[str, Any]:
    require_live_mode()
    with pg_connection() as connection:
        ensure_pilot_schema(connection)
        user = authenticate_with_connection(connection, request)
        result = connection.execute(
            '''
            UPDATE auth_sessions
            SET revoked_at = NOW(), updated_at = NOW()
            WHERE id = %s AND user_id = %s AND revoked_at IS NULL
            ''',
            (session_id, user['id']),
        )
        connection.commit()
        return {'revoked': bool(getattr(result, 'rowcount', 0))}


def _totp_code(secret: str, at_time: datetime | None = None, digits: int = 6, period: int = 30) -> str:
    current_time = at_time or utc_now()
    counter = int(current_time.timestamp()) // period
    digest = hmac.new(_b64url_decode(secret), counter.to_bytes(8, 'big'), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    binary = ((digest[offset] & 0x7F) << 24) | ((digest[offset + 1] & 0xFF) << 16) | ((digest[offset + 2] & 0xFF) << 8) | (digest[offset + 3] & 0xFF)
    return str(binary % (10 ** digits)).zfill(digits)


def _verify_totp(secret: str, code: str) -> bool:
    candidate = re.sub(r'\s+', '', code or '')
    now = utc_now()
    for drift in (-30, 0, 30):
        if hmac.compare_digest(_totp_code(secret, now + timedelta(seconds=drift)), candidate):
            return True
    return False


def mfa_begin_enrollment(request: Request) -> dict[str, Any]:
    require_live_mode()
    with pg_connection() as connection:
        ensure_pilot_schema(connection)
        user = authenticate_with_connection(connection, request)
        secret = _b64url(secrets.token_bytes(20))
        connection.execute(
            'UPDATE users SET mfa_totp_secret = %s, updated_at = NOW() WHERE id = %s',
            (secret, user['id']),
        )
        issuer = os.getenv('MFA_ISSUER', 'Decoda RWA Guard')
        uri = f'otpauth://totp/{issuer}:{user["email"]}?secret={secret}&issuer={issuer}&digits=6&period=30'
        connection.commit()
        return {'secret': secret if env_flag('AUTH_EXPOSE_DEBUG_TOKENS', default=False) else None, 'otpauth_uri': uri}


def mfa_confirm_enrollment(payload: dict[str, Any], request: Request) -> dict[str, Any]:
    require_live_mode()
    code = str(payload.get('code', '')).strip()
    if not code:
        raise HTTPException(status_code=400, detail='MFA code is required.')
    with pg_connection() as connection:
        ensure_pilot_schema(connection)
        user = authenticate_with_connection(connection, request)
        row = connection.execute('SELECT mfa_totp_secret FROM users WHERE id = %s', (user['id'],)).fetchone()
        secret = str(row['mfa_totp_secret'] or '') if row else ''
        if not secret or not _verify_totp(secret, code):
            raise HTTPException(status_code=400, detail='Invalid MFA code.')
        recovery_codes = [secrets.token_hex(4) for _ in range(MFA_RECOVERY_CODE_COUNT)]
        connection.execute('DELETE FROM mfa_recovery_codes WHERE user_id = %s', (user['id'],))
        for recovery_code in recovery_codes:
            connection.execute(
                'INSERT INTO mfa_recovery_codes (id, user_id, code_hash, created_at) VALUES (%s, %s, %s, NOW())',
                (str(uuid.uuid4()), user['id'], _auth_token_hash(recovery_code)),
            )
        connection.execute(
            'UPDATE users SET mfa_enabled_at = NOW(), session_version = session_version + 1, updated_at = NOW() WHERE id = %s',
            (user['id'],),
        )
        connection.execute('UPDATE auth_sessions SET revoked_at = NOW(), updated_at = NOW() WHERE user_id = %s AND revoked_at IS NULL', (user['id'],))
        log_audit(connection, action='auth.mfa_enabled', entity_type='user', entity_id=user['id'], request=request, user_id=user['id'], workspace_id=user['current_workspace_id'], metadata={})
        connection.commit()
        return {'mfa_enabled': True, 'recovery_codes': recovery_codes}


def mfa_disable(payload: dict[str, Any], request: Request) -> dict[str, Any]:
    require_live_mode()
    code = str(payload.get('code', '')).strip()
    with pg_connection() as connection:
        ensure_pilot_schema(connection)
        user = authenticate_with_connection(connection, request)
        row = connection.execute('SELECT mfa_totp_secret, mfa_enabled_at FROM users WHERE id = %s', (user['id'],)).fetchone()
        secret = str(row['mfa_totp_secret'] or '') if row else ''
        if not row or not row['mfa_enabled_at'] or not secret or not _verify_totp(secret, code):
            raise HTTPException(status_code=400, detail='Valid MFA code is required to disable MFA.')
        connection.execute(
            'UPDATE users SET mfa_totp_secret = NULL, mfa_enabled_at = NULL, session_version = session_version + 1, updated_at = NOW() WHERE id = %s',
            (user['id'],),
        )
        connection.execute('DELETE FROM mfa_recovery_codes WHERE user_id = %s', (user['id'],))
        connection.execute('UPDATE auth_sessions SET revoked_at = NOW(), updated_at = NOW() WHERE user_id = %s AND revoked_at IS NULL', (user['id'],))
        log_audit(connection, action='auth.mfa_disabled', entity_type='user', entity_id=user['id'], request=request, user_id=user['id'], workspace_id=user['current_workspace_id'], metadata={})
        connection.commit()
        return {'mfa_enabled': False}


def request_email_verification(payload: dict[str, Any], request: Request) -> dict[str, Any]:
    require_live_mode()
    email = _normalize_email(str(payload.get('email', '')))
    with pg_connection() as connection:
        ensure_pilot_schema(connection)
        user = connection.execute('SELECT id, email_verified_at FROM users WHERE email = %s', (email,)).fetchone()
        if user is None:
            return {'sent': True}
        if user['email_verified_at']:
            return {'sent': True, 'already_verified': True}
        token = _create_user_token(connection, str(user['id']), 'email_verification', EMAIL_VERIFICATION_TTL_MINUTES, request=request)
        _dispatch_transactional_email(connection, to_email=email, purpose='email_verification', token=token, request=request)
        connection.commit()
        return {'sent': True, 'verification_token': token if env_flag('AUTH_EXPOSE_DEBUG_TOKENS', default=False) else None}


def verify_email_token(payload: dict[str, Any], request: Request) -> dict[str, Any]:
    require_live_mode()
    raw_token = str(payload.get('token', '')).strip()
    if not raw_token:
        raise HTTPException(status_code=400, detail='token is required')
    with pg_connection() as connection:
        ensure_pilot_schema(connection)
        token_row = connection.execute(
            "SELECT id, user_id, expires_at, used_at FROM auth_tokens WHERE token_hash = %s AND purpose = 'email_verification'",
            (_auth_token_hash(raw_token),),
        ).fetchone()
        if token_row is None:
            raise HTTPException(status_code=400, detail='Invalid verification token.')
        if token_row['used_at'] is not None:
            raise HTTPException(status_code=400, detail='Verification token was already used.')
        if token_row['expires_at'] < utc_now():
            raise HTTPException(status_code=400, detail='Verification token has expired.')
        connection.execute('UPDATE auth_tokens SET used_at = NOW() WHERE id = %s', (token_row['id'],))
        connection.execute('UPDATE users SET email_verified_at = NOW(), updated_at = NOW() WHERE id = %s', (token_row['user_id'],))
        log_audit(connection, action='auth.email_verified', entity_type='user', entity_id=str(token_row['user_id']), request=request, user_id=str(token_row['user_id']), workspace_id=None, metadata={})
        connection.commit()
        return {'verified': True}


def request_password_reset(payload: dict[str, Any], request: Request) -> dict[str, Any]:
    require_live_mode()
    email = _normalize_email(str(payload.get('email', '')))
    with pg_connection() as connection:
        ensure_pilot_schema(connection)
        user = connection.execute('SELECT id FROM users WHERE email = %s', (email,)).fetchone()
        if user is None:
            return {'sent': True}
        token = _create_user_token(connection, str(user['id']), 'password_reset', PASSWORD_RESET_TTL_MINUTES, request=request)
        _dispatch_transactional_email(connection, to_email=email, purpose='password_reset', token=token, request=request)
        connection.commit()
        return {'sent': True, 'reset_token': token if env_flag('AUTH_EXPOSE_DEBUG_TOKENS', default=False) else None}


def reset_password(payload: dict[str, Any], request: Request) -> dict[str, Any]:
    require_live_mode()
    raw_token = str(payload.get('token', '')).strip()
    password = str(payload.get('password', ''))
    _require_strong_password(password)
    with pg_connection() as connection:
        ensure_pilot_schema(connection)
        token_row = connection.execute(
            "SELECT id, user_id, expires_at, used_at FROM auth_tokens WHERE token_hash = %s AND purpose = 'password_reset'",
            (_auth_token_hash(raw_token),),
        ).fetchone()
        if token_row is None or token_row['used_at'] is not None or token_row['expires_at'] < utc_now():
            raise HTTPException(status_code=400, detail='Invalid or expired password reset token.')
        connection.execute('UPDATE auth_tokens SET used_at = NOW() WHERE id = %s', (token_row['id'],))
        connection.execute(
            'UPDATE users SET password_hash = %s, session_version = session_version + 1, updated_at = NOW() WHERE id = %s',
            (hash_password(password), token_row['user_id']),
        )
        connection.execute('UPDATE auth_sessions SET revoked_at = NOW(), updated_at = NOW() WHERE user_id = %s AND revoked_at IS NULL', (token_row['user_id'],))
        user_email_row = connection.execute('SELECT email FROM users WHERE id = %s', (token_row['user_id'],)).fetchone()
        if user_email_row and user_email_row['email']:
            _dispatch_transactional_email(connection, to_email=str(user_email_row['email']), purpose='password_reset_confirmation', request=request)
        log_audit(connection, action='auth.password_reset', entity_type='user', entity_id=str(token_row['user_id']), request=request, user_id=str(token_row['user_id']), workspace_id=None, metadata={})
        connection.commit()
        return {'password_reset': True}


def create_workspace_for_user(payload: dict[str, Any], request: Request) -> dict[str, Any]:
    require_live_mode()
    workspace_name = str(payload.get('name', '')).strip()
    if not workspace_name:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='Workspace name is required.')
    role = _normalize_workspace_role(str(payload.get('role', 'owner')).strip() or 'owner')
    with pg_connection() as connection:
        ensure_pilot_schema(connection)
        user = authenticate_with_connection(connection, request)
        workspace_id = str(uuid.uuid4())
        slug_base = _slugify(workspace_name)
        slug = slug_base
        suffix = 1
        while connection.execute('SELECT 1 FROM workspaces WHERE slug = %s', (slug,)).fetchone() is not None:
            suffix += 1
            slug = f'{slug_base}-{suffix}'
        connection.execute(
            'INSERT INTO workspaces (id, name, slug, created_by_user_id, created_at) VALUES (%s, %s, %s, %s, NOW())',
            (workspace_id, workspace_name, slug, user['id']),
        )
        connection.execute(
            'INSERT INTO workspace_members (id, workspace_id, user_id, role, created_at) VALUES (%s, %s, %s, %s, NOW())',
            (str(uuid.uuid4()), workspace_id, user['id'], role),
        )
        connection.execute('UPDATE users SET current_workspace_id = %s, updated_at = NOW() WHERE id = %s', (workspace_id, user['id']))
        log_audit(
            connection,
            action='workspace.create',
            entity_type='workspace',
            entity_id=workspace_id,
            request=request,
            user_id=user['id'],
            workspace_id=workspace_id,
            metadata={'name': workspace_name, 'role': role},
        )
        connection.commit()
        logger.info(
            'workspace created and selected',
            extra={'event': 'workspace.create.success', 'user_id': user['id'], 'workspace_id': str(workspace_id), 'role': role},
        )
        return build_user_response(connection, user['id'])


def select_workspace_for_user(workspace_id: str, request: Request) -> dict[str, Any]:
    require_live_mode()
    with pg_connection() as connection:
        ensure_pilot_schema(connection)
        user = authenticate_with_connection(connection, request)
        membership = _ensure_membership(connection, user['id'], workspace_id)
        connection.execute('UPDATE users SET current_workspace_id = %s, updated_at = NOW() WHERE id = %s', (workspace_id, user['id']))
        log_audit(
            connection,
            action='workspace.select',
            entity_type='workspace',
            entity_id=workspace_id,
            request=request,
            user_id=user['id'],
            workspace_id=workspace_id,
            metadata={'role': membership['role']},
        )
        connection.commit()
        logger.info(
            'workspace selected',
            extra={'event': 'workspace.select.success', 'user_id': user['id'], 'workspace_id': str(workspace_id), 'role': membership['role']},
        )
        return build_user_response(connection, user['id'])


def list_user_workspaces(request: Request) -> dict[str, Any]:
    require_live_mode()
    with pg_connection() as connection:
        ensure_pilot_schema(connection)
        user = authenticate_with_connection(connection, request)
        return {'workspaces': user['memberships'], 'current_workspace': user['current_workspace']}


def _workspace_role_can_manage_members(role: str) -> bool:
    return _normalize_workspace_role(role) in {'owner', 'admin'}


def _require_workspace_admin(connection: Any, request: Request) -> tuple[dict[str, Any], dict[str, Any]]:
    user = authenticate_with_connection(connection, request)
    workspace_context = resolve_workspace(connection, user['id'], request.headers.get('x-workspace-id'))
    if not _workspace_role_can_manage_members(str(workspace_context['role'])):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail='Owner or admin role is required for this action.')
    return user, workspace_context


def require_ops_rbac_guard(connection: Any, request: Request) -> tuple[dict[str, Any], dict[str, Any]]:
    user, workspace_context = _require_workspace_admin(connection, request)
    role = str(workspace_context.get('role') or '')
    if _normalize_workspace_role(role) not in {'owner', 'admin'}:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail='Owner or admin role is required for ops monitoring actions.')
    return user, workspace_context


def list_workspace_members(request: Request) -> dict[str, Any]:
    require_live_mode()
    with pg_connection() as connection:
        ensure_pilot_schema(connection)
        user = authenticate_with_connection(connection, request)
        workspace_context = resolve_workspace(connection, user['id'], request.headers.get('x-workspace-id'))
        rows = connection.execute(
            '''
            SELECT wm.id, wm.user_id, wm.role, wm.created_at, u.email, u.full_name
            FROM workspace_members wm
            JOIN users u ON u.id = wm.user_id
            WHERE wm.workspace_id = %s
            ORDER BY wm.created_at ASC
            ''',
            (workspace_context['workspace_id'],),
        ).fetchall()
        return {
            'workspace': workspace_context['workspace'],
            'members': [
                {
                    'id': str(row['id']),
                    'user_id': str(row['user_id']),
                    'email': str(row['email']),
                    'full_name': str(row['full_name']),
                    'role': _normalize_workspace_role(str(row['role'])),
                    'created_at': row['created_at'].isoformat() if hasattr(row['created_at'], 'isoformat') else str(row['created_at']),
                }
                for row in rows
            ],
        }


def list_workspace_invitations(request: Request) -> dict[str, Any]:
    require_live_mode()
    with pg_connection() as connection:
        ensure_pilot_schema(connection)
        user, workspace_context = _require_workspace_admin(connection, request)
        rows = connection.execute(
            '''
            SELECT id, email, role, status, expires_at, created_at, updated_at
            FROM workspace_invitations
            WHERE i.workspace_id = %s
            ORDER BY created_at DESC
            ''',
            (workspace_context['workspace_id'],),
        ).fetchall()
        return {
            'workspace': workspace_context['workspace'],
            'requested_by_user_id': user['id'],
            'invitations': [_json_safe_value(dict(row)) for row in rows],
        }


def create_workspace_invitation(payload: dict[str, Any], request: Request) -> dict[str, Any]:
    require_live_mode()
    email = _normalize_email(str(payload.get('email', '')))
    role = _normalize_workspace_role(str(payload.get('role', 'viewer')))
    if not email:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='Invitation email is required.')
    with pg_connection() as connection:
        ensure_pilot_schema(connection)
        user, workspace_context = _require_workspace_admin(connection, request)
        entitlements = _workspace_plan(connection, workspace_context['workspace_id'])
        member_count = connection.execute('SELECT COUNT(*) AS count FROM workspace_members WHERE workspace_id = %s', (workspace_context['workspace_id'],)).fetchone()
        if int((member_count or {}).get('count') or 0) >= int(entitlements.get('max_members') or 0):
            raise HTTPException(status_code=status.HTTP_402_PAYMENT_REQUIRED, detail='Seat limit reached for current plan.')
        token = secrets.token_urlsafe(32)
        token_hash = hashlib.sha256(token.encode('utf-8')).hexdigest()
        invitation_id = str(uuid.uuid4())
        connection.execute(
            '''
            INSERT INTO workspace_invitations (id, workspace_id, email, role, token_hash, invited_by_user_id, expires_at, status, created_at, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s, NOW() + interval '7 days', 'pending', NOW(), NOW())
            ON CONFLICT (workspace_id, email, status)
            DO UPDATE SET role = EXCLUDED.role, token_hash = EXCLUDED.token_hash, invited_by_user_id = EXCLUDED.invited_by_user_id, expires_at = EXCLUDED.expires_at, updated_at = NOW()
            ''',
            (invitation_id, workspace_context['workspace_id'], email, role, token_hash, user['id']),
        )
        log_audit(connection, action='invitation.create', entity_type='workspace_invitation', entity_id=invitation_id, request=request, user_id=user['id'], workspace_id=workspace_context['workspace_id'], metadata={'email': email, 'role': role})
        connection.commit()
        return {'invitation_id': invitation_id, 'workspace_id': workspace_context['workspace_id'], 'email': email, 'role': role, 'token': token, 'expires_in_days': 7}


def revoke_workspace_invitation(invitation_id: str, request: Request) -> dict[str, Any]:
    require_live_mode()
    with pg_connection() as connection:
        ensure_pilot_schema(connection)
        user, workspace_context = _require_workspace_admin(connection, request)
        invitation = connection.execute(
            'SELECT id, status FROM workspace_invitations WHERE id = %s AND workspace_id = %s',
            (invitation_id, workspace_context['workspace_id']),
        ).fetchone()
        if invitation is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Invitation not found.')
        if str(invitation.get('status', '')) == 'revoked':
            return {'revoked': True, 'id': invitation_id}
        connection.execute("UPDATE workspace_invitations SET status='revoked', updated_at=NOW() WHERE id=%s", (invitation_id,))
        log_audit(connection, action='invitation.revoke', entity_type='workspace_invitation', entity_id=invitation_id, request=request, user_id=user['id'], workspace_id=workspace_context['workspace_id'], metadata={})
        connection.commit()
        return {'revoked': True, 'id': invitation_id}


def resend_workspace_invitation(invitation_id: str, request: Request) -> dict[str, Any]:
    require_live_mode()
    with pg_connection() as connection:
        ensure_pilot_schema(connection)
        user, workspace_context = _require_workspace_admin(connection, request)
        invitation = connection.execute(
            'SELECT id, email, role FROM workspace_invitations WHERE id = %s AND workspace_id = %s',
            (invitation_id, workspace_context['workspace_id']),
        ).fetchone()
        if invitation is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Invitation not found.')
        token = secrets.token_urlsafe(32)
        token_hash = hashlib.sha256(token.encode('utf-8')).hexdigest()
        connection.execute(
            "UPDATE workspace_invitations SET status='pending', token_hash=%s, invited_by_user_id=%s, expires_at=NOW() + interval '7 days', updated_at=NOW() WHERE id=%s",
            (token_hash, user['id'], invitation_id),
        )
        log_audit(connection, action='invitation.resend', entity_type='workspace_invitation', entity_id=invitation_id, request=request, user_id=user['id'], workspace_id=workspace_context['workspace_id'], metadata={})
        connection.commit()
        return {'resent': True, 'id': invitation_id, 'email': str(invitation['email']), 'role': _normalize_workspace_role(str(invitation['role'])), 'token': token, 'expires_in_days': 7}


def accept_workspace_invitation(payload: dict[str, Any], request: Request) -> dict[str, Any]:
    require_live_mode()
    token = str(payload.get('token', '')).strip()
    if not token:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='Invitation token is required.')
    token_hash = hashlib.sha256(token.encode('utf-8')).hexdigest()
    with pg_connection() as connection:
        ensure_pilot_schema(connection)
        user = authenticate_with_connection(connection, request)
        invitation = connection.execute(
            '''
            SELECT * FROM workspace_invitations
            WHERE token_hash = %s AND status = 'pending' AND expires_at > NOW()
            ''',
            (token_hash,),
        ).fetchone()
        if invitation is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Invitation is invalid or expired.')
        connection.execute(
            'INSERT INTO workspace_members (id, workspace_id, user_id, role, created_at) VALUES (%s, %s, %s, %s, NOW()) ON CONFLICT (workspace_id, user_id) DO NOTHING',
            (str(uuid.uuid4()), invitation['workspace_id'], user['id'], _normalize_workspace_role(str(invitation['role']))),
        )
        connection.execute(
            "UPDATE workspace_invitations SET status='accepted', accepted_at=NOW(), accepted_by_user_id=%s, updated_at=NOW() WHERE id=%s",
            (user['id'], invitation['id']),
        )
        connection.execute('UPDATE users SET current_workspace_id=%s, updated_at=NOW() WHERE id=%s', (invitation['workspace_id'], user['id']))
        log_audit(connection, action='invitation.accept', entity_type='workspace_invitation', entity_id=str(invitation['id']), request=request, user_id=user['id'], workspace_id=str(invitation['workspace_id']), metadata={})
        connection.commit()
        return {'accepted': True, 'workspace_id': str(invitation['workspace_id']), 'role': _normalize_workspace_role(str(invitation['role'])), 'user': build_user_response(connection, user['id'])}


def update_workspace_member(member_id: str, payload: dict[str, Any], request: Request) -> dict[str, Any]:
    role = _normalize_workspace_role(str(payload.get('role', '')))
    with pg_connection() as connection:
        ensure_pilot_schema(connection)
        user, workspace_context = _require_workspace_admin(connection, request)
        row = connection.execute('SELECT id, user_id, role FROM workspace_members WHERE id = %s AND workspace_id = %s', (member_id, workspace_context['workspace_id'])).fetchone()
        if row is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Member not found.')
        current_role = _normalize_workspace_role(str(row['role']))
        if current_role == 'owner' and role != 'owner':
            owner_count = connection.execute("SELECT COUNT(*) AS count FROM workspace_members WHERE workspace_id = %s AND role = 'owner'", (workspace_context['workspace_id'],)).fetchone()
            if int((owner_count or {}).get('count') or 0) <= 1:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='Workspace must keep at least one owner.')
        connection.execute('UPDATE workspace_members SET role = %s WHERE id = %s', (role, member_id))
        log_audit(connection, action='member.role_update', entity_type='workspace_member', entity_id=member_id, request=request, user_id=user['id'], workspace_id=workspace_context['workspace_id'], metadata={'role': role})
        connection.commit()
        return {'id': member_id, 'role': role}


def remove_workspace_member(member_id: str, request: Request) -> dict[str, Any]:
    with pg_connection() as connection:
        ensure_pilot_schema(connection)
        user, workspace_context = _require_workspace_admin(connection, request)
        row = connection.execute('SELECT id, user_id, role FROM workspace_members WHERE id = %s AND workspace_id = %s', (member_id, workspace_context['workspace_id'])).fetchone()
        if row is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Member not found.')
        if _normalize_workspace_role(str(row['role'])) == 'owner':
            owner_count = connection.execute("SELECT COUNT(*) AS count FROM workspace_members WHERE workspace_id = %s AND role = 'owner'", (workspace_context['workspace_id'],)).fetchone()
            if int((owner_count or {}).get('count') or 0) <= 1:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='Workspace must keep at least one owner.')
        connection.execute('DELETE FROM workspace_members WHERE id = %s', (member_id,))
        log_audit(connection, action='member.remove', entity_type='workspace_member', entity_id=member_id, request=request, user_id=user['id'], workspace_id=workspace_context['workspace_id'], metadata={})
        connection.commit()
        return {'removed': True, 'id': member_id}


def get_team_seats(request: Request) -> dict[str, Any]:
    with pg_connection() as connection:
        ensure_pilot_schema(connection)
        user = authenticate_with_connection(connection, request)
        workspace_context = resolve_workspace(connection, user['id'], request.headers.get('x-workspace-id'))
        count = connection.execute('SELECT COUNT(*) AS count FROM workspace_members WHERE workspace_id = %s', (workspace_context['workspace_id'],)).fetchone()
        entitlements = _workspace_plan(connection, workspace_context['workspace_id'])
        return {'used': int((count or {}).get('count') or 0), 'limit': int(entitlements.get('max_members') or 0), 'plan_key': entitlements.get('plan_key')}


def _demo_monitoring_bootstrap_allowed() -> bool:
    app_env = str(os.getenv('APP_ENV') or os.getenv('ENV') or os.getenv('APP_MODE') or '').strip().lower()
    return app_env not in {'prod', 'production'}


def _seed_demo_monitoring_proof(connection: Any, *, workspace_id: str, user_id: str) -> dict[str, Any]:
    if not _demo_monitoring_bootstrap_allowed():
        return {'bootstrapped': False, 'reason': 'production_runtime'}
    proof_severity = 'high'
    proof_risk_score = 0.92
    asset_row = connection.execute(
        '''
        SELECT id
        FROM assets
        WHERE workspace_id = %s
          AND deleted_at IS NULL
          AND normalized_identifier = %s
        ORDER BY created_at ASC
        LIMIT 1
        ''',
        (workspace_id, 'demo-seed-wallet-monitor'),
    ).fetchone()
    if asset_row is None:
        asset_id = str(uuid.uuid4())
        connection.execute(
            '''
            INSERT INTO assets (
                id, workspace_id, name, description, asset_type, chain_network, identifier, asset_class, risk_tier, owner_team, notes, enabled,
                issuer_name, asset_symbol, asset_identifier, token_contract_address, custody_wallets, treasury_ops_wallets, oracle_sources, venue_labels,
                expected_counterparties, expected_flow_patterns, expected_approval_patterns, expected_liquidity_baseline,
                expected_oracle_freshness_seconds, expected_oracle_update_cadence_seconds, policy_tags, jurisdiction_tags,
                baseline_status, baseline_source, baseline_updated_at, baseline_confidence, baseline_coverage,
                normalized_identifier, verification_status, verification_summary, verification_checked_at,
                created_by_user_id, updated_by_user_id
            ) VALUES (
                %s, %s, 'Demo Seed Protected Asset', 'Seeded simulator-backed protected asset for non-production monitoring proof.',
                'wallet', 'ethereum-mainnet', %s, 'rwa', 'medium', 'security', 'Created by seed bootstrap.',
                TRUE, 'Decoda Demo', 'DSA', 'demo-seed-asset', '0x0000000000000000000000000000000000000001',
                %s::jsonb, %s::jsonb, %s::jsonb, %s::jsonb, %s::jsonb, %s::jsonb, %s::jsonb, %s::jsonb,
                %s, %s, %s::jsonb, %s::jsonb, 'established', 'workspace_contract', NOW(), %s, %s,
                %s, 'verified', %s::jsonb, NOW(), %s, %s
            )
            ''',
            (
                asset_id,
                workspace_id,
                'demo-seed-wallet-monitor',
                _json_dumps(['0x0000000000000000000000000000000000000001']),
                _json_dumps(['0x0000000000000000000000000000000000000002']),
                _json_dumps(['chainlink']),
                _json_dumps(['demo-dex']),
                _json_dumps(['0x00000000000000000000000000000000000000aa']),
                _json_dumps([{'source_class': 'treasury', 'destination_class': 'venue'}]),
                _json_dumps({'approval_contracts': ['0x00000000000000000000000000000000000000bb']}),
                _json_dumps({'expected_daily_volume_usd': 100000}),
                300,
                300,
                _json_dumps(['demo']),
                _json_dumps(['us']),
                0.9,
                0.9,
                'demo-seed-wallet-monitor',
                _json_dumps({'source': 'demo_seed'}),
                user_id,
                user_id,
            ),
        )
    else:
        asset_id = str(asset_row['id'])
    target_row = connection.execute(
        '''
        SELECT id
        FROM targets
        WHERE workspace_id = %s
          AND deleted_at IS NULL
          AND name = 'Demo Seed Wallet Monitor'
        ORDER BY created_at ASC
        LIMIT 1
        ''',
        (workspace_id,),
    ).fetchone()
    if target_row is None:
        target_id = str(uuid.uuid4())
        connection.execute(
            '''
            INSERT INTO targets (
                id, workspace_id, name, target_type, chain_network, contract_identifier, wallet_address, asset_type, owner_notes, severity_preference, enabled,
                asset_id, chain_id, target_metadata,
                monitoring_enabled, monitoring_mode, monitoring_interval_seconds, severity_threshold, auto_create_alerts, auto_create_incidents, notification_channels,
                monitored_by_workspace_id, is_active, created_by_user_id, updated_by_user_id
            ) VALUES (
                %s, %s, 'Demo Seed Wallet Monitor', 'wallet', 'ethereum-mainnet', NULL, '0x00000000000000000000000000000000000000aa',
                'wallet', 'Seeded monitoring target for simulator telemetry proof.', 'medium', TRUE,
                %s::uuid, 1, %s::jsonb,
                TRUE, 'poll', 60, 'high', TRUE, TRUE, %s::jsonb,
                %s, TRUE, %s, %s
            )
            ''',
            (
                target_id,
                workspace_id,
                asset_id,
                _json_dumps({'asset_label': 'Demo Seed Wallet', 'bootstrap_source': 'seed_demo_workspace', 'proof_mode': True}),
                _json_dumps(['email']),
                workspace_id,
                user_id,
                user_id,
            ),
        )
    else:
        target_id = str(target_row['id'])
        connection.execute(
            '''
            UPDATE targets
            SET enabled = TRUE,
                monitoring_enabled = TRUE,
                is_active = TRUE,
                asset_id = %s::uuid,
                severity_threshold = 'high',
                auto_create_alerts = TRUE,
                auto_create_incidents = TRUE,
                updated_at = NOW()
            WHERE id = %s::uuid
            ''',
            (asset_id, target_id),
        )
    monitor_bridge = ensure_monitored_system_for_target(connection, target_id=target_id, workspace_id=workspace_id)
    monitored_system_id = str(monitor_bridge.get('monitored_system_id') or '')
    if monitor_bridge.get('status') != 'ok' or not monitored_system_id:
        return {'bootstrapped': False, 'reason': monitor_bridge.get('reason') or monitor_bridge.get('status') or 'bridge_failed'}
    observed_at = utc_now()
    tx_hash = f'0x{hashlib.sha256(f"{workspace_id}:{target_id}:seed-demo".encode("utf-8")).hexdigest()[:64]}'
    log_index = 0
    dedupe_signature = hashlib.sha256(f'{workspace_id}:{target_id}:demo-seed-monitoring-alert'.encode('utf-8')).hexdigest()
    detection_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f'demo-seed-detection:{workspace_id}:{target_id}:{dedupe_signature}'))
    alert_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f'demo-seed-alert:{workspace_id}:{target_id}:{dedupe_signature}'))
    incident_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f'demo-seed-incident:{workspace_id}:{target_id}:{alert_id}'))
    response_action_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f'demo-seed-response-action:{workspace_id}:{incident_id}:{alert_id}'))
    alert_event_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f'demo-seed-alert-event:{workspace_id}:{alert_id}'))
    timeline_event_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f'demo-seed-incident-timeline:{workspace_id}:{incident_id}'))
    alert_audit_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f'demo-seed-audit-alert:{workspace_id}:{alert_id}'))
    incident_audit_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f'demo-seed-audit-incident:{workspace_id}:{incident_id}'))
    response_action_audit_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f'demo-seed-audit-response-action:{workspace_id}:{response_action_id}'))
    response_action_history_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f'demo-seed-history-response-action:{workspace_id}:{response_action_id}'))
    incident_action_history_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f'demo-seed-history-incident-action:{workspace_id}:{response_action_id}'))
    alert_action_history_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f'demo-seed-history-alert-action:{workspace_id}:{response_action_id}'))
    connection.execute(
        '''
        INSERT INTO detections (
            id, workspace_id, monitored_system_id, protected_asset_id, detection_type, severity, confidence, title, evidence_summary,
            evidence_source, source_rule, status, detected_at, raw_evidence_json, linked_alert_id, created_at, updated_at
        )
        VALUES (
            %s, %s, %s::uuid, %s::uuid, %s, %s, %s, %s, %s,
            %s, %s, 'open', %s, %s::jsonb, NULL, NOW(), NOW()
        )
        ON CONFLICT (id)
        DO UPDATE SET
            monitored_system_id = EXCLUDED.monitored_system_id,
            protected_asset_id = EXCLUDED.protected_asset_id,
            severity = EXCLUDED.severity,
            confidence = EXCLUDED.confidence,
            title = EXCLUDED.title,
            evidence_summary = EXCLUDED.evidence_summary,
            evidence_source = EXCLUDED.evidence_source,
            source_rule = EXCLUDED.source_rule,
            raw_evidence_json = EXCLUDED.raw_evidence_json,
            updated_at = NOW()
        ''',
        (
            detection_id,
            workspace_id,
            monitored_system_id,
            asset_id,
            'anomalous_transfer',
            proof_severity,
            proof_risk_score,
            'Demo Seed Monitoring Detection',
            'Seeded detection persisted for deterministic demo workflow visibility.',
            'simulator',
            'seed.demo.monitoring.rule',
            observed_at,
            _json_dumps({'source': 'seed_demo_workspace', 'target_id': target_id, 'tx_hash': tx_hash}),
        ),
    )
    connection.execute(
        '''
        INSERT INTO alerts (
            id, workspace_id, user_id, analysis_run_id, alert_type, title, severity, status, source_service, summary, payload, created_at, detection_id,
            target_id, module_key, source, dedupe_signature, occurrence_count, first_seen_at, last_seen_at, updated_at
        )
        VALUES (
            %s, %s, %s, NULL, %s, %s, %s, %s, %s, %s, %s::jsonb, NOW(), %s::uuid,
            %s::uuid, %s, %s, %s, 1, %s, %s, NOW()
        )
        ON CONFLICT (id)
        DO UPDATE SET
            detection_id = EXCLUDED.detection_id,
            target_id = EXCLUDED.target_id,
            module_key = EXCLUDED.module_key,
            source = EXCLUDED.source,
            dedupe_signature = EXCLUDED.dedupe_signature,
            severity = EXCLUDED.severity,
            status = EXCLUDED.status,
            source_service = EXCLUDED.source_service,
            title = EXCLUDED.title,
            summary = EXCLUDED.summary,
            payload = EXCLUDED.payload,
            first_seen_at = LEAST(alerts.first_seen_at, EXCLUDED.first_seen_at),
            last_seen_at = GREATEST(alerts.last_seen_at, EXCLUDED.last_seen_at),
            occurrence_count = GREATEST(alerts.occurrence_count, 1),
            updated_at = NOW()
        ''',
        (
            alert_id,
            workspace_id,
            user_id,
            'monitoring.seed_demo_detection',
            'Demo Seed Monitoring Alert',
            proof_severity,
            'open',
            'simulator',
            'Seeded simulator detection alert linked to persisted demo telemetry evidence.',
            _json_dumps(
                {
                    'source': 'seed_demo_workspace',
                    'detection_id': detection_id,
                    'target_id': target_id,
                    'asset_id': asset_id,
                    'tx_hash': tx_hash,
                    'bootstrap_source': 'seed_demo_workspace',
                }
            ),
            detection_id,
            target_id,
            'monitoring',
            'simulator',
            dedupe_signature,
            observed_at,
            observed_at,
        ),
    )
    connection.execute(
        'UPDATE detections SET linked_alert_id = %s::uuid, updated_at = NOW() WHERE id = %s::uuid',
        (alert_id, detection_id),
    )
    connection.execute(
        '''
        INSERT INTO incidents (
            id, workspace_id, user_id, analysis_run_id, target_id, event_type, title, severity, status, workflow_status, summary,
            linked_alert_ids, timeline, payload, created_at, updated_at
        )
        VALUES (
            %s, %s, %s, NULL, %s::uuid, %s, %s, %s, %s, %s, %s,
            %s::jsonb, %s::jsonb, %s::jsonb, NOW(), NOW()
        )
        ON CONFLICT (id)
        DO UPDATE SET
            target_id = EXCLUDED.target_id,
            event_type = EXCLUDED.event_type,
            title = EXCLUDED.title,
            severity = EXCLUDED.severity,
            status = EXCLUDED.status,
            workflow_status = EXCLUDED.workflow_status,
            summary = EXCLUDED.summary,
            linked_alert_ids = EXCLUDED.linked_alert_ids,
            timeline = EXCLUDED.timeline,
            payload = EXCLUDED.payload,
            updated_at = NOW()
        ''',
        (
            incident_id,
            workspace_id,
            user_id,
            target_id,
            'incident.monitoring_seed_alert',
            'Demo Seed Monitoring Incident',
            proof_severity,
            'open',
            'open',
            'Seeded incident created from deterministic demo monitoring alert.',
            _json_dumps([alert_id]),
            _json_dumps([{'event': 'incident.created_from_alert', 'at': observed_at.isoformat(), 'alert_id': alert_id}]),
            _json_dumps({'source': 'seed_demo_workspace', 'alert_id': alert_id, 'target_id': target_id}),
        ),
    )
    connection.execute(
        '''
        INSERT INTO alert_events (id, workspace_id, alert_id, actor_user_id, event_type, details, created_at)
        VALUES (%s, %s, %s::uuid, %s::uuid, %s, %s::jsonb, %s)
        ON CONFLICT (id)
        DO UPDATE SET
            event_type = EXCLUDED.event_type,
            details = EXCLUDED.details,
            created_at = EXCLUDED.created_at
        ''',
        (
            alert_event_id,
            workspace_id,
            alert_id,
            user_id,
            'alert.created_from_simulator_detection',
            _json_dumps({'incident_id': incident_id, 'dedupe_signature': dedupe_signature}),
            observed_at,
        ),
    )
    connection.execute(
        '''
        INSERT INTO incident_timeline (id, workspace_id, incident_id, event_type, message, actor_user_id, metadata, created_at)
        VALUES (%s, %s, %s::uuid, %s, %s, %s::uuid, %s::jsonb, %s)
        ON CONFLICT (id)
        DO UPDATE SET
            event_type = EXCLUDED.event_type,
            message = EXCLUDED.message,
            metadata = EXCLUDED.metadata,
            created_at = EXCLUDED.created_at
        ''',
        (
            timeline_event_id,
            workspace_id,
            incident_id,
            'incident.created_from_alert',
            'Incident opened from seeded simulator detection alert.',
            user_id,
            _json_dumps({'alert_id': alert_id, 'dedupe_signature': dedupe_signature}),
            observed_at,
        ),
    )
    connection.execute(
        '''
        INSERT INTO audit_logs (id, workspace_id, user_id, action, entity_type, entity_id, ip_address, metadata, created_at)
        VALUES (%s, %s, %s, %s, %s, %s, NULL, %s::jsonb, %s)
        ON CONFLICT (id)
        DO UPDATE SET
            action = EXCLUDED.action,
            metadata = EXCLUDED.metadata,
            created_at = EXCLUDED.created_at
        ''',
        (
            alert_audit_id,
            workspace_id,
            user_id,
            'alert.seeded_from_simulator',
            'alert',
            alert_id,
            _json_dumps({'target_id': target_id, 'incident_id': incident_id, 'dedupe_signature': dedupe_signature}),
            observed_at,
        ),
    )
    connection.execute(
        '''
        INSERT INTO response_actions (
            id, workspace_id, incident_id, alert_id, action_type, mode, status, result_summary, operator_notes, execution_metadata, created_by_user_id, approved_by_user_id, created_at, executed_at
        )
        VALUES (
            %s, %s, %s::uuid, %s::uuid, 'notify_team', 'simulated', 'executed', %s, %s, %s::jsonb, %s, %s, %s, %s
        )
        ON CONFLICT (id)
        DO UPDATE SET
            incident_id = EXCLUDED.incident_id,
            alert_id = EXCLUDED.alert_id,
            mode = EXCLUDED.mode,
            status = EXCLUDED.status,
            result_summary = EXCLUDED.result_summary,
            operator_notes = EXCLUDED.operator_notes,
            execution_metadata = EXCLUDED.execution_metadata,
            created_at = EXCLUDED.created_at,
            executed_at = EXCLUDED.executed_at
        ''',
        (
            response_action_id,
            workspace_id,
            incident_id,
            alert_id,
            'notify_team',
            'Seeded simulated notify-team response action for deterministic demo workflow.',
            'Seeded by seed_demo_workspace for demo visibility.',
            _json_dumps({'execution_mode': 'simulated', 'source': 'seed_demo_workspace', 'detection_id': detection_id}),
            user_id,
            user_id,
            observed_at,
            observed_at,
        ),
    )
    connection.execute(
        '''
        INSERT INTO action_history (id, workspace_id, actor_type, actor_id, object_type, object_id, action_type, timestamp, details_json)
        VALUES (%s, %s, 'system', %s, 'response_action', %s, 'response_action.executed', %s, %s::jsonb)
        ON CONFLICT (id)
        DO UPDATE SET
            action_type = EXCLUDED.action_type,
            timestamp = EXCLUDED.timestamp,
            details_json = EXCLUDED.details_json
        ''',
        (
            response_action_history_id,
            workspace_id,
            user_id,
            response_action_id,
            observed_at,
            _json_dumps({'mode': 'simulated', 'incident_id': incident_id, 'alert_id': alert_id, 'detection_id': detection_id}),
        ),
    )
    connection.execute(
        '''
        INSERT INTO action_history (id, workspace_id, actor_type, actor_id, object_type, object_id, action_type, timestamp, details_json)
        VALUES (%s, %s, 'system', %s, 'incident', %s, 'incident.response_action_created', %s, %s::jsonb)
        ON CONFLICT (id)
        DO UPDATE SET
            action_type = EXCLUDED.action_type,
            timestamp = EXCLUDED.timestamp,
            details_json = EXCLUDED.details_json
        ''',
        (
            incident_action_history_id,
            workspace_id,
            user_id,
            incident_id,
            observed_at,
            _json_dumps({'response_action_id': response_action_id, 'mode': 'simulated'}),
        ),
    )
    connection.execute(
        '''
        INSERT INTO action_history (id, workspace_id, actor_type, actor_id, object_type, object_id, action_type, timestamp, details_json)
        VALUES (%s, %s, 'system', %s, 'alert', %s, 'alert.response_action_created', %s, %s::jsonb)
        ON CONFLICT (id)
        DO UPDATE SET
            action_type = EXCLUDED.action_type,
            timestamp = EXCLUDED.timestamp,
            details_json = EXCLUDED.details_json
        ''',
        (
            alert_action_history_id,
            workspace_id,
            user_id,
            alert_id,
            observed_at,
            _json_dumps({'response_action_id': response_action_id, 'mode': 'simulated'}),
        ),
    )
    connection.execute(
        '''
        INSERT INTO audit_logs (id, workspace_id, user_id, action, entity_type, entity_id, ip_address, metadata, created_at)
        VALUES (%s, %s, %s, %s, %s, %s, NULL, %s::jsonb, %s)
        ON CONFLICT (id)
        DO UPDATE SET
            action = EXCLUDED.action,
            metadata = EXCLUDED.metadata,
            created_at = EXCLUDED.created_at
        ''',
        (
            response_action_audit_id,
            workspace_id,
            user_id,
            'response_action.seeded_simulated_execution',
            'response_action',
            response_action_id,
            _json_dumps({'incident_id': incident_id, 'alert_id': alert_id, 'detection_id': detection_id}),
            observed_at,
        ),
    )
    connection.execute(
        '''
        INSERT INTO audit_logs (id, workspace_id, user_id, action, entity_type, entity_id, ip_address, metadata, created_at)
        VALUES (%s, %s, %s, %s, %s, %s, NULL, %s::jsonb, %s)
        ON CONFLICT (id)
        DO UPDATE SET
            action = EXCLUDED.action,
            metadata = EXCLUDED.metadata,
            created_at = EXCLUDED.created_at
        ''',
        (
            incident_audit_id,
            workspace_id,
            user_id,
            'incident.seeded_from_alert',
            'incident',
            incident_id,
            _json_dumps({'target_id': target_id, 'alert_id': alert_id, 'dedupe_signature': dedupe_signature}),
            observed_at,
        ),
    )
    evidence_id = str(uuid.uuid4())
    evidence_row = connection.execute(
        '''
        INSERT INTO evidence (
            id, workspace_id, asset_id, target_id, alert_id, chain, block_number, tx_hash, log_index, event_type,
            monitored_system_id, severity, risk_score, summary, counterparty, amount_text, token_address, contract_address, source_provider,
            raw_payload_json, observed_at, created_at
        )
        VALUES (
            %s, %s, %s::uuid, %s::uuid, %s::uuid, %s, %s, %s, %s, %s,
            %s::uuid, %s, %s, %s, %s, %s, %s, %s, %s,
            %s::jsonb, %s, NOW()
        )
        ON CONFLICT (target_id, tx_hash, log_index, event_type)
        DO UPDATE SET
            alert_id = EXCLUDED.alert_id,
            monitored_system_id = EXCLUDED.monitored_system_id,
            source_provider = EXCLUDED.source_provider,
            summary = EXCLUDED.summary,
            raw_payload_json = EXCLUDED.raw_payload_json,
            observed_at = EXCLUDED.observed_at
        RETURNING id, observed_at
        ''',
        (
            evidence_id,
            workspace_id,
            asset_id,
            target_id,
            alert_id,
            'ethereum-mainnet',
            1,
            tx_hash,
            log_index,
            'simulator_seed_event',
            monitored_system_id,
            proof_severity,
            proof_risk_score,
            'Seeded simulator telemetry event with deterministic proof-mode high risk for alert and incident creation.',
            '0x00000000000000000000000000000000000000ff',
            '100.00',
            '0x0000000000000000000000000000000000000001',
            '0x0000000000000000000000000000000000000001',
            'simulator',
            _json_dumps(
                {
                    'metadata': {
                        'evidence_origin': 'simulator',
                        'bootstrap_source': 'seed_demo_workspace',
                        'production_claim_eligible': False,
                        'proof_mode': True,
                    },
                    'tx_hash': tx_hash,
                    'event_id': f'demo-seed-{target_id}',
                }
            ),
            observed_at,
        ),
    ).fetchone()
    last_event_at = (evidence_row or {}).get('observed_at') or observed_at
    connection.execute(
        '''
        UPDATE monitored_systems
        SET last_event_at = %s,
            runtime_status = CASE WHEN runtime_status IN ('failed', 'disabled') THEN runtime_status ELSE 'healthy' END,
            status = CASE WHEN runtime_status = 'failed' THEN 'error' WHEN runtime_status = 'disabled' THEN 'paused' ELSE 'active' END,
            freshness_status = 'fresh',
            confidence_status = 'medium',
            coverage_reason = NULL,
            last_error_text = NULL,
            last_heartbeat = NOW()
        WHERE id = %s::uuid
        ''',
        (last_event_at, monitored_system_id),
    )
    return {
        'bootstrapped': True,
        'workspace_id': workspace_id,
        'asset_id': asset_id,
        'target_id': target_id,
        'monitored_system_id': monitored_system_id,
        'detection_id': detection_id,
        'alert_id': alert_id,
        'incident_id': incident_id,
        'response_action_id': response_action_id,
        'response_action_history_id': response_action_history_id,
        'incident_action_history_id': incident_action_history_id,
        'alert_action_history_id': alert_action_history_id,
        'evidence_source': 'simulator',
        'telemetry_event_observed_at': last_event_at.isoformat() if isinstance(last_event_at, datetime) else str(last_event_at),
    }


def seed_demo_workspace(email: str, password: str, workspace_name: str, full_name: str = 'Pilot Demo User') -> dict[str, Any]:
    require_live_mode()
    normalized_email = _normalize_email(email)
    _require_password(password)
    normalized_full_name = full_name.strip() or 'Pilot Demo User'
    normalized_workspace_name = workspace_name.strip() or 'Decoda Demo Workspace'
    password_hash = hash_password(password)
    with pg_connection() as connection:
        ensure_pilot_schema(connection)
        existing = connection.execute(
            'SELECT id, current_workspace_id FROM users WHERE email = %s',
            (normalized_email,),
        ).fetchone()
        created_user = False
        workspace_created = False
        membership_created = False

        if existing is None:
            user_id = str(uuid.uuid4())
            connection.execute(
                '''
                INSERT INTO users (id, email, password_hash, full_name, current_workspace_id, email_verified_at, created_at, updated_at, last_sign_in_at)
                VALUES (%s, %s, %s, %s, NULL, NOW(), NOW(), NOW(), NOW())
                ''',
                (user_id, normalized_email, password_hash, normalized_full_name),
            )
            created_user = True
            workspace_id = ''
        else:
            user_id = str(existing['id'])
            workspace_id = str(existing['current_workspace_id'] or '')

        membership = connection.execute(
            '''
            SELECT wm.workspace_id, w.name, w.slug
            FROM workspace_members wm
            JOIN workspaces w ON w.id = wm.workspace_id
            WHERE wm.user_id = %s
            ORDER BY wm.created_at ASC
            LIMIT 1
            ''',
            (user_id,),
        ).fetchone()
        if workspace_id:
            workspace_row = connection.execute(
                'SELECT id, name, slug FROM workspaces WHERE id = %s',
                (workspace_id,),
            ).fetchone()
            if workspace_row is None:
                workspace_id = ''
        if membership is not None and not workspace_id:
            workspace_id = str(membership['workspace_id'])
        if not workspace_id:
            workspace_id = str(uuid.uuid4())
            slug_base = _slugify(normalized_workspace_name)
            slug = slug_base
            suffix = 1
            while connection.execute('SELECT 1 FROM workspaces WHERE slug = %s', (slug,)).fetchone() is not None:
                suffix += 1
                slug = f'{slug_base}-{suffix}'
            connection.execute(
                'INSERT INTO workspaces (id, name, slug, created_by_user_id, created_at) VALUES (%s, %s, %s, %s, NOW())',
                (workspace_id, normalized_workspace_name, slug, user_id),
            )
            workspace_created = True
        if workspace_created or membership is None:
            connection.execute(
                'INSERT INTO workspace_members (id, workspace_id, user_id, role, created_at) VALUES (%s, %s, %s, %s, NOW()) ON CONFLICT (workspace_id, user_id) DO NOTHING',
                (str(uuid.uuid4()), workspace_id, user_id, 'owner'),
            )
            membership_created = True
        connection.execute(
            '''
            UPDATE users
            SET password_hash = %s,
                full_name = %s,
                current_workspace_id = %s,
                email_verified_at = COALESCE(email_verified_at, NOW()),
                updated_at = NOW(),
                last_sign_in_at = NOW()
            WHERE id = %s
            ''',
            (password_hash, normalized_full_name, workspace_id, user_id),
        )
        monitoring_bootstrap = _seed_demo_monitoring_proof(connection, workspace_id=workspace_id, user_id=user_id)
        connection.commit()
        user = build_user_response(connection, user_id)
        return {
            'seeded': created_user or workspace_created or membership_created,
            'user': user,
            'email': normalized_email,
            'password': password,
            'workspace_created': workspace_created,
            'membership_created': membership_created,
            'user_created': created_user,
            'monitoring_bootstrap': monitoring_bootstrap,
        }


def run_background_jobs(*, worker_id: str = 'worker', limit: int = 20) -> dict[str, Any]:
    require_live_mode()
    processed = 0
    failed = 0
    with pg_connection() as connection:
        ensure_pilot_schema(connection)
        rows = connection.execute(
            '''
            SELECT id, job_type, payload, attempts, max_attempts
            FROM background_jobs
            WHERE status = 'queued' AND run_after <= NOW()
            ORDER BY created_at ASC
            LIMIT %s
            ''',
            (limit,),
        ).fetchall()
        for row in rows:
            job_id = str(row['id'])
            payload = row['payload'] or {}
            connection.execute(
                "UPDATE background_jobs SET status = 'running', locked_at = NOW(), locked_by = %s, updated_at = NOW() WHERE id = %s",
                (worker_id, job_id),
            )
            try:
                if row['job_type'] == 'send_email':
                    _send_email(str(payload.get('to_email', '')), str(payload.get('subject', '')), str(payload.get('text_body', '')))
                elif row['job_type'] == 'send_webhook':
                    _deliver_webhook_attempt(payload)
                    if payload.get('delivery_id'):
                        connection.execute(
                            "UPDATE webhook_deliveries SET status = 'succeeded', response_status = 200, updated_at = NOW() WHERE id = %s",
                            (str(payload['delivery_id']),),
                        )
                elif row['job_type'] == 'send_alert_email':
                    _deliver_alert_email_attempt(payload)
                elif row['job_type'] == 'send_slack':
                    _deliver_slack_attempt(payload)
                    if payload.get('delivery_id'):
                        connection.execute(
                            "UPDATE slack_deliveries SET status = 'succeeded', response_status = 200, updated_at = NOW() WHERE id = %s",
                            (str(payload['delivery_id']),),
                        )
                else:
                    raise RuntimeError(f'Unsupported job type {row["job_type"]}')
                connection.execute("UPDATE background_jobs SET status = 'succeeded', updated_at = NOW() WHERE id = %s", (job_id,))
                processed += 1
            except Exception as exc:
                failed += 1
                next_attempt = int(row.get('attempts') or 0) + 1
                backoff_seconds = min(900, 2 ** next_attempt)
                terminal = next_attempt >= int(row.get('max_attempts') or 5)
                connection.execute(
                    '''
                    UPDATE background_jobs
                    SET status = %s,
                        attempts = %s,
                        run_after = NOW() + (%s || ' seconds')::interval,
                        last_error = %s,
                        updated_at = NOW()
                    WHERE id = %s
                    ''',
                    ('failed' if terminal else 'queued', next_attempt, backoff_seconds, str(exc), job_id),
                )
                if row['job_type'] == 'send_webhook' and payload.get('delivery_id'):
                    connection.execute(
                        '''
                        UPDATE webhook_deliveries
                        SET status = %s,
                            error_message = %s,
                            attempt = %s,
                            updated_at = NOW()
                        WHERE id = %s
                        ''',
                        ('failed' if terminal else 'queued', str(exc), next_attempt, str(payload['delivery_id'])),
                    )
                if row['job_type'] == 'send_slack' and payload.get('delivery_id'):
                    connection.execute(
                        '''
                        UPDATE slack_deliveries
                        SET status = %s,
                            error_message = %s,
                            attempt = %s,
                            updated_at = NOW()
                        WHERE id = %s
                        ''',
                        ('failed' if terminal else 'queued', str(exc), next_attempt, str(payload['delivery_id'])),
                    )
                logger.exception('background job failed', extra={'event': 'jobs.failed', 'job_id': job_id, 'job_type': row['job_type']})
        connection.commit()
    return {'processed': processed, 'failed': failed}


def reconcile_monitored_systems_for_enabled_targets() -> dict[str, Any]:
    require_live_mode()
    with pg_connection() as connection:
        ensure_pilot_schema(connection)
        result = reconcile_enabled_targets_monitored_systems(connection)
        connection.commit()
        return result


def reconcile_workspace_monitored_systems(request: Request) -> dict[str, Any]:
    logger.info('monitoring_reconcile step=start')
    stage = 'require_live_mode'
    workspace_id: str | None = None
    user_id: str | None = None
    reconcile_id = str(uuid.uuid4())
    inflight_key: str | None = None
    logger.info('monitoring_reconcile step=%s', stage)
    try:
        require_live_mode()
    except HTTPException:
        raise
    except Exception as exc:
        raise _reconcile_error(stage, exc, request=request, workspace_id=workspace_id, user_id=user_id, reconcile_id=reconcile_id) from exc
    with pg_connection() as connection:
        stage = 'ensure_schema'
        logger.info('monitoring_reconcile step=%s', stage)
        try:
            ensure_pilot_schema(connection)
        except HTTPException:
            raise
        except Exception as exc:
            raise _reconcile_error(stage, exc, request=request, workspace_id=workspace_id, user_id=user_id, reconcile_id=reconcile_id) from exc
        stage = 'require_workspace_admin'
        logger.info('monitoring_reconcile step=%s', stage)
        try:
            user, workspace_context = _require_workspace_admin(connection, request)
        except HTTPException:
            raise
        except Exception as exc:
            raise _reconcile_error(stage, exc, request=request, workspace_id=workspace_id, user_id=user_id, reconcile_id=reconcile_id) from exc
        workspace_id = workspace_context['workspace_id']
        user_id = str(user.get('id') or '')
        inflight_key = f'{workspace_id}:{user_id}'
        logger.info('monitoring_reconcile step=workspace_resolved workspace_id=%s', workspace_id)
        with _workspace_reconcile_lock:
            in_flight = _workspace_reconcile_inflight.get(inflight_key)
            if in_flight:
                cached_status = str(in_flight.get('status') or 'running')
                existing_reconcile_id = str(in_flight.get('reconcile_id') or '')
                if cached_status == 'completed':
                    cached_response = in_flight.get('response')
                    completed_at_mono = float(in_flight.get('completed_at_monotonic') or 0.0)
                    if isinstance(cached_response, dict) and (monotonic() - completed_at_mono) <= WORKSPACE_RECONCILE_CACHE_SECONDS:
                        return cached_response
                    _workspace_reconcile_inflight.pop(inflight_key, None)
                return {
                    'workspace': workspace_context['workspace'],
                    'reconcile_id': existing_reconcile_id or reconcile_id,
                    'state': 'no_op_with_reasons',
                    'reconcile': _normalize_reconcile_result({
                        'targets_scanned': 0,
                        'created_or_updated': 0,
                        'skipped_reasons': {'reconcile_already_in_progress': 1},
                        'skipped_target_details': [{
                            'target_id': '__workspace__',
                            'code': 'reconcile_already_in_progress',
                            'reason': f"Reconcile request already in progress with reconcile_id={existing_reconcile_id or 'unknown'}.",
                        }],
                    }),
                    'systems': [],
                    'monitored_systems_count': 0,
                }
        stage = 'verify_eligible_targets'
        logger.info('monitoring_reconcile step=%s workspace_id=%s', stage, workspace_id)
        try:
            eligible_targets = _load_workspace_reconcile_eligible_targets(connection, workspace_id=workspace_id)
            if len(eligible_targets) <= 0:
                raise ValueError('No enabled, linked wallet/contract targets found for workspace.')
            broken_asset_links = _load_workspace_reconcile_broken_asset_links(connection, workspace_id=workspace_id)
            if broken_asset_links:
                raise ValueError(f'Found enabled target rows with missing/invalid asset links: {len(broken_asset_links)}')
        except HTTPException:
            raise
        except Exception as exc:
            raise _reconcile_error(stage, exc, request=request, workspace_id=workspace_id, user_id=user_id, reconcile_id=reconcile_id) from exc
        stage = 'debug_snapshot_before'
        logger.info('monitoring_reconcile step=%s workspace_id=%s', stage, workspace_id)
        try:
            pre_repair_snapshot = workspace_monitoring_debug_snapshot(connection, workspace_id=workspace_id)
        except Exception:
            logger.exception('monitoring_reconcile_debug_snapshot_before_failed workspace_id=%s', workspace_id)
            pre_repair_snapshot = {'workspace_id': workspace_id, 'error': 'snapshot_before_failed'}
        logger.info('monitoring_reconcile snapshot_before workspace_id=%s snapshot=%s', workspace_id, pre_repair_snapshot)

        stage = 'reconcile_targets'
        logger.info('monitoring_reconcile step=%s workspace_id=%s', stage, workspace_id)
        with _workspace_reconcile_lock:
            _workspace_reconcile_inflight[inflight_key] = {
                'reconcile_id': reconcile_id,
                'started_at': utc_now_iso(),
                'status': 'running',
            }
        try:
            result = _normalize_reconcile_result(reconcile_enabled_targets_monitored_systems(connection, workspace_id=workspace_id))
        except HTTPException:
            raise
        except Exception as exc:
            raise _reconcile_error(stage, exc, request=request, workspace_id=workspace_id, user_id=user_id, reconcile_id=reconcile_id) from exc
        finally:
            with _workspace_reconcile_lock:
                current = _workspace_reconcile_inflight.get(inflight_key)
                if current and str(current.get('reconcile_id') or '') == reconcile_id:
                    _workspace_reconcile_inflight.pop(inflight_key, None)
        logger.info(
            'monitoring_reconcile step=reconcile_completed workspace_id=%s created_or_updated=%s',
            workspace_id,
            result.get('created_or_updated', 0),
        )
        stage = 'validate_monitored_asset_links'
        logger.info('monitoring_reconcile step=%s workspace_id=%s', stage, workspace_id)
        try:
            mismatched_enabled_links = _load_workspace_enabled_monitored_asset_mismatches(connection, workspace_id=workspace_id)
            if mismatched_enabled_links:
                raise ValueError(f'Enabled monitored_systems rows with target/asset mismatch: {len(mismatched_enabled_links)}')
        except HTTPException:
            raise
        except Exception as exc:
            raise _reconcile_error(stage, exc, request=request, workspace_id=workspace_id, user_id=user_id, reconcile_id=reconcile_id) from exc
        stage = 'runtime_debug_assertions'
        logger.info('monitoring_reconcile step=%s workspace_id=%s', stage, workspace_id)
        try:
            runtime_debug_assertions = _workspace_runtime_debug_assertions(connection, workspace_id=workspace_id)
            if (
                int(runtime_debug_assertions.get('valid_protected_assets', 0) or 0) <= 0
                or int(runtime_debug_assertions.get('linked_monitored_systems', 0) or 0) <= 0
                or int(runtime_debug_assertions.get('enabled_configs', 0) or 0) <= 0
                or int(runtime_debug_assertions.get('valid_link_count', 0) or 0) <= 0
                or not bool(runtime_debug_assertions.get('workspace_configured'))
            ):
                raise ValueError(f'Runtime debug assertions failed: {runtime_debug_assertions}')
        except HTTPException:
            raise
        except Exception as exc:
            raise _reconcile_error(stage, exc, request=request, workspace_id=workspace_id, user_id=user_id, reconcile_id=reconcile_id) from exc

        stage = 'audit_log'
        logger.info('monitoring_reconcile step=%s workspace_id=%s', stage, workspace_id)
        try:
            repaired_target_ids = sorted({str(item.get('id')) for item in eligible_targets if item.get('id')})
            log_audit(
                connection,
                action='monitoring.reconcile',
                entity_type='workspace',
                entity_id=workspace_id,
                request=request,
                user_id=user_id,
                workspace_id=workspace_id,
                metadata={
                    'targets_scanned': result.get('targets_scanned', 0),
                    'created_or_updated': result.get('created_or_updated', 0),
                    'invalid_reasons': result.get('invalid_reasons', {}),
                    'skipped_reasons': result.get('skipped_reasons', {}),
                    'repaired_target_ids': repaired_target_ids,
                    'repaired_monitored_system_ids': result.get('repaired_monitored_system_ids', []),
                    'runtime_debug_assertions': runtime_debug_assertions,
                    'debug_before': pre_repair_snapshot,
                },
            )
        except HTTPException:
            raise
        except Exception as exc:
            raise _reconcile_error(stage, exc, request=request, workspace_id=workspace_id, user_id=user_id, reconcile_id=reconcile_id) from exc
        logger.info('monitoring_reconcile step=audit_logged workspace_id=%s', workspace_id)

        stage = 'list_rows'
        logger.info('monitoring_reconcile step=%s workspace_id=%s', stage, workspace_id)
        try:
            rows = list_workspace_monitored_system_rows(connection, workspace_id)
        except HTTPException:
            raise
        except Exception as exc:
            raise _reconcile_error(stage, exc, request=request, workspace_id=workspace_id, user_id=user_id, reconcile_id=reconcile_id) from exc
        logger.info('monitoring_reconcile step=rows_loaded workspace_id=%s count=%s', workspace_id, len(rows))
        stage = 'debug_snapshot_after'
        logger.info('monitoring_reconcile step=%s workspace_id=%s', stage, workspace_id)
        try:
            post_repair_snapshot = workspace_monitoring_debug_snapshot(connection, workspace_id=workspace_id)
        except Exception:
            logger.exception('monitoring_reconcile_debug_snapshot_after_failed workspace_id=%s', workspace_id)
            post_repair_snapshot = {'workspace_id': workspace_id, 'error': 'snapshot_after_failed'}
        logger.info('monitoring_reconcile snapshot_after workspace_id=%s snapshot=%s', workspace_id, post_repair_snapshot)

        stage = 'commit'
        logger.info('monitoring_reconcile step=%s workspace_id=%s', stage, workspace_id)
        try:
            connection.commit()
        except HTTPException:
            raise
        except Exception as exc:
            raise _reconcile_error(stage, exc, request=request, workspace_id=workspace_id, user_id=user_id, reconcile_id=reconcile_id) from exc
        logger.info('monitoring_reconcile step=commit_completed workspace_id=%s', workspace_id)

        systems = [_json_safe_value(row) for row in rows]
        unresolved_count = len(result.get('invalid_target_details') or []) + len(result.get('skipped_target_details') or [])
        reconcile_state = 'success'
        if int(result.get('created_or_updated', 0) or 0) <= 0 and unresolved_count > 0:
            reconcile_state = 'no_op_with_reasons'
        reason_counts = {
            'invalid': int(sum(int(count or 0) for count in (result.get('invalid_reasons') or {}).values())),
            'skipped': int(sum(int(count or 0) for count in (result.get('skipped_reasons') or {}).values())),
        }
        response: dict[str, Any] = {
            'workspace': workspace_context['workspace'],
            'reconcile_id': reconcile_id,
            'state': reconcile_state,
            'reason_counts': reason_counts,
            'reconcile': result,
            'systems': systems,
            'monitored_systems_count': len(systems),
        }
        if os.getenv('APP_ENV', 'development').strip().lower() != 'production':
            response['diagnostics'] = {
                'resolved_workspace_id': workspace_id,
                'post_reconcile_monitored_systems_count': len(systems),
                'post_reconcile_monitored_system_ids': [str(row.get('id')) for row in systems if row.get('id')],
                'targets_scanned': result.get('targets_scanned', 0),
                'created_or_updated': result.get('created_or_updated', 0),
                'repaired_monitored_system_ids': result.get('repaired_monitored_system_ids', []),
                'repaired_target_ids': repaired_target_ids,
                'reason_counts': reason_counts,
                'runtime_debug_assertions': runtime_debug_assertions,
                'debug_before': pre_repair_snapshot,
                'debug_after': post_repair_snapshot,
            }
        with _workspace_reconcile_lock:
            _workspace_reconcile_inflight[inflight_key] = {
                'reconcile_id': reconcile_id,
                'started_at': utc_now_iso(),
                'status': 'completed',
                'completed_at_monotonic': monotonic(),
                'response': response,
            }
        return response


def _load_workspace_reconcile_eligible_targets(connection: Any, *, workspace_id: str) -> list[dict[str, Any]]:
    return [
        dict(row)
        for row in connection.execute(
            '''
            SELECT t.id, t.asset_id, t.target_type
            FROM targets t
            JOIN assets a
              ON a.id = t.asset_id
             AND a.workspace_id = t.workspace_id
             AND a.deleted_at IS NULL
            WHERE t.workspace_id = %s::uuid
              AND t.deleted_at IS NULL
              AND t.enabled = TRUE
              AND t.asset_id IS NOT NULL
              AND t.target_type IN ('wallet', 'contract')
            ORDER BY t.created_at ASC
            ''',
            (workspace_id,),
        ).fetchall()
    ]


def _load_workspace_reconcile_broken_asset_links(connection: Any, *, workspace_id: str) -> list[dict[str, Any]]:
    return [
        dict(row)
        for row in connection.execute(
            '''
            SELECT t.id, t.asset_id
            FROM targets t
            LEFT JOIN assets a
              ON a.id = t.asset_id
             AND a.workspace_id = t.workspace_id
             AND a.deleted_at IS NULL
            WHERE t.workspace_id = %s::uuid
              AND t.deleted_at IS NULL
              AND t.enabled = TRUE
              AND t.asset_id IS NOT NULL
              AND t.target_type IN ('wallet', 'contract')
              AND a.id IS NULL
            ORDER BY t.created_at ASC
            ''',
            (workspace_id,),
        ).fetchall()
    ]


def _load_workspace_enabled_monitored_asset_mismatches(connection: Any, *, workspace_id: str) -> list[dict[str, Any]]:
    return [
        dict(row)
        for row in connection.execute(
            '''
            SELECT ms.id, ms.target_id, ms.asset_id AS monitored_asset_id, t.asset_id AS target_asset_id
            FROM monitored_systems ms
            JOIN targets t
              ON t.id = ms.target_id
             AND t.workspace_id = ms.workspace_id
             AND t.deleted_at IS NULL
             AND t.enabled = TRUE
             AND t.target_type IN ('wallet', 'contract')
            JOIN assets a
              ON a.id = t.asset_id
             AND a.workspace_id = t.workspace_id
             AND a.deleted_at IS NULL
            WHERE ms.workspace_id = %s::uuid
              AND COALESCE(ms.is_enabled, TRUE) = TRUE
              AND (ms.asset_id IS NULL OR ms.asset_id <> t.asset_id)
            ORDER BY ms.created_at ASC
            ''',
            (workspace_id,),
        ).fetchall()
    ]


def _workspace_runtime_debug_assertions(connection: Any, *, workspace_id: str) -> dict[str, Any]:
    enabled_target_rows = _load_workspace_reconcile_eligible_targets(connection, workspace_id=workspace_id)
    enabled_target_ids = {str(row.get('id')) for row in enabled_target_rows if row.get('id')}
    enabled_target_asset_ids = {str(row.get('asset_id')) for row in enabled_target_rows if row.get('asset_id')}
    valid_link_rows = connection.execute(
        '''
        SELECT ms.id, ms.target_id, ms.asset_id
        FROM monitored_systems ms
        JOIN targets t
          ON t.id = ms.target_id
         AND t.workspace_id = ms.workspace_id
         AND t.deleted_at IS NULL
         AND t.enabled = TRUE
         AND t.target_type IN ('wallet', 'contract')
        JOIN assets a
          ON a.id = t.asset_id
         AND a.workspace_id = t.workspace_id
         AND a.deleted_at IS NULL
        WHERE ms.workspace_id = %s::uuid
          AND COALESCE(ms.is_enabled, TRUE) = TRUE
          AND ms.asset_id = t.asset_id
        ''',
        (workspace_id,),
    ).fetchall()
    linked_monitored_target_ids = {str(row.get('target_id')) for row in valid_link_rows if row.get('target_id')}
    valid_link_count = len(valid_link_rows)
    linked_monitored_systems = len(linked_monitored_target_ids)
    valid_protected_assets = len(enabled_target_asset_ids)
    enabled_configs = len(enabled_target_ids)
    workspace_configured = (
        valid_protected_assets > 0
        and linked_monitored_systems > 0
        and enabled_configs > 0
        and valid_link_count > 0
    )
    reason_codes: list[str] = []
    if valid_protected_assets <= 0:
        reason_codes.append('no_valid_protected_assets')
    if linked_monitored_systems <= 0:
        reason_codes.append('no_linked_monitored_systems')
    if enabled_configs <= 0:
        reason_codes.append('no_persisted_enabled_monitoring_config')
    if valid_link_count <= 0:
        reason_codes.append('target_system_linkage_invalid')
    return {
        'workspace_id': workspace_id,
        'valid_protected_assets': valid_protected_assets,
        'linked_monitored_systems': linked_monitored_systems,
        'enabled_configs': enabled_configs,
        'valid_link_count': valid_link_count,
        'workspace_configured': workspace_configured,
        'configuration_reason': reason_codes[0] if reason_codes else None,
        'reason_codes': reason_codes,
        'configuration_diagnostics': {
            'valid_protected_assets': valid_protected_assets,
            'linked_monitored_systems': linked_monitored_systems,
            'enabled_configs': enabled_configs,
            'valid_link_count': valid_link_count,
            'workspace_configured': workspace_configured,
            'configuration_reason': reason_codes[0] if reason_codes else None,
            'reason_codes': reason_codes,
        },
    }


def _reconcile_error(
    stage: str,
    exc: Exception,
    *,
    request: Request | None = None,
    workspace_id: str | None = None,
    user_id: str | None = None,
    reconcile_id: str | None = None,
) -> HTTPException:
    method = getattr(request, 'method', None) if request else None
    url = getattr(request, 'url', None) if request else None
    path = getattr(url, 'path', None) if url is not None else None
    logger.exception(
        'monitoring_reconcile_failed stage=%s method=%s path=%s workspace_id=%s user_id=%s error_type=%s error_message=%s traceback=%s',
        stage,
        method,
        path,
        workspace_id,
        user_id,
        type(exc).__name__,
        str(exc),
        traceback.format_exc(),
    )
    detail: dict[str, Any] = {
        'code': 'monitoring_reconcile_failed',
        'state': 'failure',
        'reconcile_id': reconcile_id,
        'detail': 'Unexpected backend error during monitored systems reconcile.',
        'stage': stage,
    }
    if os.getenv('APP_ENV', 'development').strip().lower() not in {'production', 'prod'}:
        detail['debug_error_type'] = type(exc).__name__
        detail['debug_error_message'] = str(exc)
    return HTTPException(status_code=500, detail=detail)


def _deliver_webhook_attempt(payload: dict[str, Any]) -> None:
    webhook_id = str(payload.get('webhook_id', ''))
    delivery_id = str(payload.get('delivery_id', ''))
    target_url = str(payload.get('target_url', ''))
    secret = str(payload.get('secret', ''))
    body_payload = payload.get('payload') if isinstance(payload.get('payload'), dict) else {}
    if not webhook_id or not delivery_id or not target_url or not secret:
        raise RuntimeError('Webhook delivery payload missing required fields.')
    encoded = _json_dumps(body_payload).encode('utf-8')
    signature = hmac.new(secret.encode('utf-8'), encoded, hashlib.sha256).hexdigest()
    request = UrlRequest(
        target_url,
        method='POST',
        data=encoded,
        headers={
            'Content-Type': 'application/json',
            'X-Decoda-Signature': signature,
            'X-Decoda-Webhook-Id': webhook_id,
            'X-Decoda-Delivery-Id': delivery_id,
        },
    )
    with urlopen(request, timeout=8):
        return


def _deliver_alert_email_attempt(payload: dict[str, Any]) -> None:
    to_email = str(payload.get('to_email', ''))
    title = str(payload.get('title', ''))
    summary = str(payload.get('summary', ''))
    if not to_email:
        raise RuntimeError('Missing to_email for alert delivery.')
    subject = f'[{_email_brand_name()}] Alert: {title}'
    _send_email(to_email, subject, summary or 'A new alert requires attention.')


def _deliver_slack_attempt(payload: dict[str, Any]) -> None:
    mode = str(payload.get('mode') or 'webhook').strip().lower()
    body_payload = payload.get('payload') if isinstance(payload.get('payload'), dict) else {}
    encoded = _json_dumps(body_payload).encode('utf-8')
    if mode == 'bot':
        bot_token = str(payload.get('bot_token', ''))
        channel = str(payload.get('default_channel', '')).strip()
        if not bot_token or not channel:
            raise RuntimeError('Slack bot delivery payload missing bot_token or default_channel.')
        bot_payload = dict(body_payload)
        bot_payload['channel'] = channel
        request = UrlRequest(
            'https://slack.com/api/chat.postMessage',
            method='POST',
            data=_json_dumps(bot_payload).encode('utf-8'),
            headers={'Content-Type': 'application/json; charset=utf-8', 'Authorization': f'Bearer {bot_token}'},
        )
        with urlopen(request, timeout=8) as response:
            body = json.loads(response.read().decode('utf-8'))
            if not bool(body.get('ok')):
                raise RuntimeError(f"Slack bot send failed: {body.get('error')}")
        return

    webhook_url = str(payload.get('webhook_url', ''))
    if not webhook_url:
        raise RuntimeError('Slack delivery payload missing webhook_url.')
    request = UrlRequest(
        webhook_url,
        method='POST',
        data=encoded,
        headers={'Content-Type': 'application/json'},
    )
    with urlopen(request, timeout=8):
        return


def list_plan_entitlements() -> dict[str, Any]:
    require_live_mode()
    with pg_connection() as connection:
        ensure_pilot_schema(connection)
        plans = connection.execute(
            '''
            SELECT plan_key, plan_name, monthly_price_cents, yearly_price_cents, trial_days, max_members, max_webhooks, features
            FROM plan_entitlements
            WHERE is_public = TRUE
            ORDER BY monthly_price_cents ASC
            '''
        ).fetchall()
        return {'plans': [_json_safe_value(dict(plan)) for plan in plans]}


def get_workspace_subscription(request: Request) -> dict[str, Any]:
    require_live_mode()
    with pg_connection() as connection:
        ensure_pilot_schema(connection)
        user = authenticate_with_connection(connection, request)
        workspace_context = resolve_workspace(connection, user['id'], request.headers.get('x-workspace-id'))
        subscription = connection.execute(
            '''
            SELECT plan_key, status, trial_ends_at, current_period_ends_at, cancel_at_period_end
            FROM billing_subscriptions
            WHERE workspace_id = %s
            ORDER BY created_at DESC
            LIMIT 1
            ''',
            (workspace_context['workspace_id'],),
        ).fetchone()
        billing_status = billing_runtime_status()
        return {
            'workspace': workspace_context['workspace'],
            'subscription': _json_safe_value(dict(subscription)) if subscription else None,
            'billing': billing_status,
        }


def create_checkout_session(payload: dict[str, Any], request: Request) -> dict[str, Any]:
    require_live_mode()
    plan_key = str(payload.get('plan_key', 'starter')).strip().lower()
    provider = billing_provider()
    ensure_billing_available(operation='create_checkout_session')
    if provider == 'paddle':
        return create_paddle_checkout_session(payload, request)
    stripe_key = os.getenv('STRIPE_SECRET_KEY', '').strip()
    if not stripe_key:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail='Stripe is not configured.')
    with pg_connection() as connection:
        ensure_pilot_schema(connection)
        user, workspace_context = _require_workspace_admin(connection, request)
        plan = connection.execute('SELECT plan_key, trial_days, stripe_price_id FROM plan_entitlements WHERE plan_key = %s', (plan_key,)).fetchone()
        if plan is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Unknown plan.')
        price_id = str(plan.get('stripe_price_id') or '').strip()
        if not price_id:
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=f'Stripe price is not configured for plan {plan_key}.')
        customer = connection.execute('SELECT provider_customer_id FROM billing_customers WHERE workspace_id = %s ORDER BY created_at DESC LIMIT 1', (workspace_context['workspace_id'],)).fetchone()
        checkout_payload = {
            'mode': 'subscription',
            'line_items[0][price]': price_id,
            'line_items[0][quantity]': '1',
            'success_url': f"{os.getenv('APP_PUBLIC_URL', 'http://localhost:3000').rstrip('/')}/settings?billing=success",
            'cancel_url': f"{os.getenv('APP_PUBLIC_URL', 'http://localhost:3000').rstrip('/')}/settings?billing=cancelled",
            'metadata[workspace_id]': workspace_context['workspace_id'],
            'metadata[plan_key]': plan_key,
        }
        if customer and customer.get('provider_customer_id'):
            checkout_payload['customer'] = str(customer['provider_customer_id'])
        request_data = urlencode(checkout_payload).encode('utf-8')
        stripe_request = UrlRequest('https://api.stripe.com/v1/checkout/sessions', method='POST', data=request_data, headers={'Authorization': f'Bearer {stripe_key}', 'Content-Type': 'application/x-www-form-urlencoded'})
        try:
            with urlopen(stripe_request, timeout=10) as response:
                checkout_session = json.loads(response.read().decode('utf-8'))
        except (HTTPError, URLError) as exc:
            raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=f'Unable to create Stripe checkout session: {exc}')
        log_audit(connection, action='billing.checkout_session_created', entity_type='workspace', entity_id=workspace_context['workspace_id'], request=request, user_id=user['id'], workspace_id=workspace_context['workspace_id'], metadata={'plan_key': plan_key})
        connection.commit()
        return {
            'checkout_url': checkout_session.get('url'),
            'session_id': checkout_session.get('id'),
            'plan_key': plan_key,
        }


def create_portal_session(request: Request) -> dict[str, Any]:
    require_live_mode()
    ensure_billing_available(operation='create_portal_session')
    if billing_provider() == 'paddle':
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail='Billing portal is not supported for Paddle. Use Paddle subscription links from checkout.')
    stripe_key = os.getenv('STRIPE_SECRET_KEY', '').strip()
    if not stripe_key:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail='Stripe is not configured.')
    with pg_connection() as connection:
        ensure_pilot_schema(connection)
        user, workspace_context = _require_workspace_admin(connection, request)
        customer = connection.execute('SELECT provider_customer_id FROM billing_customers WHERE workspace_id = %s ORDER BY created_at DESC LIMIT 1', (workspace_context['workspace_id'],)).fetchone()
        if customer is None or not customer.get('provider_customer_id'):
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Workspace does not have a billing customer yet.')
        request_data = urlencode({'customer': str(customer['provider_customer_id']), 'return_url': f"{os.getenv('APP_PUBLIC_URL', 'http://localhost:3000').rstrip('/')}/settings"}).encode('utf-8')
        stripe_request = UrlRequest('https://api.stripe.com/v1/billing_portal/sessions', method='POST', data=request_data, headers={'Authorization': f'Bearer {stripe_key}', 'Content-Type': 'application/x-www-form-urlencoded'})
        try:
            with urlopen(stripe_request, timeout=10) as response:
                portal_session = json.loads(response.read().decode('utf-8'))
        except (HTTPError, URLError) as exc:
            raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=f'Unable to create Stripe portal session: {exc}')
        return {
            'portal_url': portal_session.get('url'),
            'workspace_id': workspace_context['workspace_id'],
            'requested_by_user_id': user['id'],
        }


def _paddle_price_id_for_plan(plan_key: str) -> str:
    env_key = f'PADDLE_PRICE_ID_{plan_key.upper()}'
    return str(os.getenv(env_key, '')).strip()


def create_paddle_checkout_session(payload: dict[str, Any], request: Request) -> dict[str, Any]:
    require_live_mode()
    plan_key = str(payload.get('plan_key', 'starter')).strip().lower()
    paddle_config = paddle_runtime_config()
    if not paddle_config['configured']:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail='Paddle billing is unavailable.')
    price_id = _paddle_price_id_for_plan(plan_key)
    if not price_id:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=f'Paddle price is not configured for plan {plan_key}.')
    with pg_connection() as connection:
        ensure_pilot_schema(connection)
        user, workspace_context = _require_workspace_admin(connection, request)
        plan = connection.execute('SELECT plan_key, trial_days FROM plan_entitlements WHERE plan_key = %s', (plan_key,)).fetchone()
        if plan is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Unknown plan.')
        api_host = 'https://api.paddle.com' if paddle_config['environment'] == 'live' else 'https://sandbox-api.paddle.com'
        request_payload = {
            'items': [{'price_id': price_id, 'quantity': 1}],
            'custom_data': {'workspace_id': workspace_context['workspace_id'], 'plan_key': plan_key},
            'success_url': f"{os.getenv('APP_PUBLIC_URL', 'http://localhost:3000').rstrip('/')}/settings?billing=success",
        }
        paddle_request = UrlRequest(
            f'{api_host}/checkouts',
            method='POST',
            data=_json_dumps(request_payload).encode('utf-8'),
            headers={'Authorization': f"Bearer {os.getenv('PADDLE_API_KEY', '').strip()}", 'Content-Type': 'application/json'},
        )
        try:
            with urlopen(paddle_request, timeout=10) as response:
                checkout_session = json.loads(response.read().decode('utf-8'))
        except (HTTPError, URLError) as exc:
            raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=f'Unable to create Paddle checkout session: {exc}')
        data = checkout_session.get('data') if isinstance(checkout_session.get('data'), dict) else checkout_session
        checkout_url = data.get('url') or data.get('checkout_url')
        log_audit(connection, action='billing.checkout_session_created', entity_type='workspace', entity_id=workspace_context['workspace_id'], request=request, user_id=user['id'], workspace_id=workspace_context['workspace_id'], metadata={'plan_key': plan_key, 'provider': 'paddle'})
        connection.commit()
        return {
            'provider': 'paddle',
            'checkout_url': checkout_url,
            'session_id': data.get('id'),
            'plan_key': plan_key,
            'client_token': os.getenv('PADDLE_CLIENT_TOKEN', '').strip() or None,
        }


def _parse_paddle_timestamp(value: Any) -> datetime | None:
    text = str(value or '').strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace('Z', '+00:00'))
    except ValueError:
        return None


def _paddle_to_subscription_status(event_type: str, source_status: str) -> str:
    normalized = source_status.strip().lower()
    if normalized in {'active', 'trialing'}:
        return normalized
    if normalized in {'past_due', 'paused'}:
        return 'past_due'
    if normalized in {'canceled', 'cancelled', 'inactive'}:
        return 'canceled'
    if event_type.endswith('.canceled') or event_type.endswith('.cancelled') or event_type.endswith('.paused'):
        return 'canceled'
    return 'incomplete'


def verify_paddle_webhook_signature(*, raw_body: bytes, signature_header: str | None, timestamp_header: str | None) -> None:
    ensure_billing_available(operation='verify_paddle_webhook_signature', expected_provider='paddle')
    expected_secret = os.getenv('PADDLE_WEBHOOK_SECRET', '').strip()
    if not expected_secret:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=_billing_unavailable_detail(operation='verify_paddle_webhook_signature'))
    if not signature_header or not timestamp_header:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='Missing Paddle webhook signature headers.')
    signed_payload = f'{timestamp_header}:{raw_body.decode("utf-8")}'.encode('utf-8')
    digest = hmac.new(expected_secret.encode('utf-8'), signed_payload, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature_header.strip(), digest):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='Invalid Paddle webhook signature.')


def process_paddle_webhook(payload: dict[str, Any], *, signature_header: str | None, timestamp_header: str | None, raw_body: bytes) -> dict[str, Any]:
    require_live_mode()
    ensure_billing_available(operation='process_paddle_webhook', expected_provider='paddle')
    verify_paddle_webhook_signature(raw_body=raw_body, signature_header=signature_header, timestamp_header=timestamp_header)
    event_id = str(payload.get('event_id') or payload.get('id') or '').strip()
    event_type = str(payload.get('event_type') or payload.get('type') or '').strip().lower()
    data = payload.get('data') if isinstance(payload.get('data'), dict) else {}
    if not event_id or not event_type:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='Paddle webhook must include event_id and event_type.')
    custom_data = data.get('custom_data') if isinstance(data.get('custom_data'), dict) else {}
    workspace_id = str(custom_data.get('workspace_id') or '').strip() or None
    customer_id = str((data.get('customer') or {}).get('id') if isinstance(data.get('customer'), dict) else data.get('customer_id') or '').strip() or None
    subscription_id = str(data.get('id') or data.get('subscription_id') or '').strip() or None
    transaction_id = str(data.get('transaction_id') or ((payload.get('data') or {}).get('transaction_id')) or '').strip() or None
    plan_key = str(custom_data.get('plan_key') or '').strip().lower() or 'starter'
    source_status = str(data.get('status') or '').strip().lower()
    mapped_status = _paddle_to_subscription_status(event_type, source_status)
    period_end = _parse_paddle_timestamp(data.get('current_billing_period', {}).get('ends_at') if isinstance(data.get('current_billing_period'), dict) else None)
    with pg_connection() as connection:
        ensure_pilot_schema(connection)
        existing = connection.execute('SELECT processing_status FROM billing_events WHERE provider_event_id = %s', (event_id,)).fetchone()
        if existing is not None:
            return {'received': True, 'duplicate': True, 'event_id': event_id, 'status': existing['processing_status']}
        if workspace_id and customer_id:
            connection.execute(
                '''
                INSERT INTO billing_customers (id, workspace_id, provider, provider_customer_id, metadata)
                VALUES (%s, %s, 'paddle', %s, %s::jsonb)
                ON CONFLICT (provider_customer_id) DO UPDATE SET workspace_id = EXCLUDED.workspace_id, metadata = EXCLUDED.metadata, updated_at = NOW()
                ''',
                (str(uuid.uuid4()), workspace_id, customer_id, _json_dumps({'event_id': event_id, 'provider': 'paddle'})),
            )
        if workspace_id and subscription_id:
            connection.execute(
                '''
                INSERT INTO billing_subscriptions (id, workspace_id, provider, provider_subscription_id, provider_transaction_id, plan_key, status, current_period_ends_at, metadata)
                VALUES (%s, %s, 'paddle', %s, %s, %s, %s, %s, %s::jsonb)
                ON CONFLICT (provider_subscription_id) DO UPDATE SET provider_transaction_id = EXCLUDED.provider_transaction_id, plan_key = EXCLUDED.plan_key, status = EXCLUDED.status, current_period_ends_at = EXCLUDED.current_period_ends_at, metadata = EXCLUDED.metadata, updated_at = NOW()
                ''',
                (str(uuid.uuid4()), workspace_id, subscription_id, transaction_id, plan_key, mapped_status, period_end, _json_dumps({'event_type': event_type, 'source_status': source_status})),
            )
        connection.execute(
            '''
            INSERT INTO billing_events (id, provider, provider_event_id, workspace_id, event_type, payload, processing_status, processed_at)
            VALUES (%s, 'paddle', %s, %s, %s, %s::jsonb, 'processed', NOW())
            ''',
            (str(uuid.uuid4()), event_id, workspace_id, event_type, _json_dumps(payload)),
        )
        connection.commit()
    return {'received': True, 'duplicate': False, 'event_id': event_id, 'status': 'processed'}


def process_stripe_webhook(payload: dict[str, Any], signature_header: str | None) -> dict[str, Any]:
    require_live_mode()
    ensure_billing_available(operation='process_stripe_webhook', expected_provider='stripe')
    event_id = str(payload.get('id', '')).strip()
    event_type = str(payload.get('type', '')).strip()
    if not event_id or not event_type:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='Stripe webhook must include id and type.')
    expected = os.getenv('STRIPE_WEBHOOK_SECRET', '').strip()
    if expected and signature_header != expected:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='Invalid webhook signature.')
    with pg_connection() as connection:
        ensure_pilot_schema(connection)
        existing = connection.execute('SELECT processing_status FROM billing_events WHERE provider_event_id = %s', (event_id,)).fetchone()
        if existing is not None:
            return {'received': True, 'duplicate': True, 'event_id': event_id, 'status': existing['processing_status']}
        data_object = ((payload.get('data') or {}).get('object') or {})
        metadata = data_object.get('metadata') if isinstance(data_object.get('metadata'), dict) else {}
        workspace_id = str(metadata.get('workspace_id', '')).strip() or None
        customer_id = str(data_object.get('customer', '')).strip() or None
        subscription_id = str(data_object.get('subscription', '')).strip() or str(data_object.get('id', '')).strip() or None
        plan_key = str(metadata.get('plan_key', '')).strip().lower() or 'starter'
        subscription_status = str(data_object.get('status', '')).strip().lower() or ('active' if event_type == 'checkout.session.completed' else 'trialing')
        if workspace_id and customer_id:
            connection.execute(
                '''
                INSERT INTO billing_customers (id, workspace_id, provider, provider_customer_id, metadata)
                VALUES (%s, %s, 'stripe', %s, %s::jsonb)
                ON CONFLICT (provider_customer_id) DO UPDATE SET workspace_id = EXCLUDED.workspace_id, metadata = EXCLUDED.metadata, updated_at = NOW()
                ''',
                (str(uuid.uuid4()), workspace_id, customer_id, _json_dumps({'event_id': event_id})),
            )
        if workspace_id and subscription_id:
            connection.execute(
                '''
                INSERT INTO billing_subscriptions (id, workspace_id, provider, provider_subscription_id, plan_key, status, metadata)
                VALUES (%s, %s, 'stripe', %s, %s, %s, %s::jsonb)
                ON CONFLICT (provider_subscription_id) DO UPDATE SET plan_key = EXCLUDED.plan_key, status = EXCLUDED.status, metadata = EXCLUDED.metadata, updated_at = NOW()
                ''',
                (str(uuid.uuid4()), workspace_id, subscription_id, plan_key, subscription_status, _json_dumps({'event_type': event_type})),
            )
        connection.execute(
            '''
            INSERT INTO billing_events (id, provider, provider_event_id, workspace_id, event_type, payload, processing_status, processed_at)
            VALUES (%s, 'stripe', %s, %s, %s, %s::jsonb, 'processed', NOW())
            ''',
            (str(uuid.uuid4()), event_id, workspace_id, event_type, _json_dumps(payload)),
        )
        connection.commit()
        return {'received': True, 'duplicate': False, 'event_id': event_id, 'status': 'processed'}


def _mask_url(value: str) -> str:
    trimmed = value.strip()
    if len(trimmed) <= 8:
        return '****'
    return f'{trimmed[:5]}...{trimmed[-4:]}'


def _encode_secret_value(value: str, *, aad: str = '') -> str:
    return encrypt_secret(value, aad=aad)


def _decode_secret_value(value: str, *, aad: str = '') -> str:
    if not value:
        return ''
    return decrypt_secret(value, aad=aad)


def _normalize_routing_payload(payload: dict[str, Any], *, channel_type: str) -> dict[str, Any]:
    severity_threshold = str(payload.get('severity_threshold', 'medium')).strip().lower()
    if severity_threshold not in SEVERITY_RANK:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='severity_threshold must be low/medium/high/critical.')
    modules_include = payload.get('modules_include') if isinstance(payload.get('modules_include'), list) else []
    modules_exclude = payload.get('modules_exclude') if isinstance(payload.get('modules_exclude'), list) else []
    target_ids = payload.get('target_ids') if isinstance(payload.get('target_ids'), list) else []
    event_types = payload.get('event_types') if isinstance(payload.get('event_types'), list) else ['alert.created']
    target_types = payload.get('target_types') if isinstance(payload.get('target_types'), list) else []
    enabled = bool(payload.get('enabled', True))
    if channel_type not in {'dashboard', 'email', 'webhook', 'slack'}:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='Unsupported channel type.')
    return {
        'channel_type': channel_type,
        'severity_threshold': severity_threshold,
        'modules_include': [str(value).strip().lower() for value in modules_include if str(value).strip()],
        'modules_exclude': [str(value).strip().lower() for value in modules_exclude if str(value).strip()],
        'target_ids': [str(value).strip() for value in target_ids if str(value).strip()],
        'event_types': [str(value).strip() for value in event_types if str(value).strip()] or ['alert.created'],
        'target_types': [str(value).strip().lower() for value in target_types if str(value).strip()],
        'enabled': enabled,
    }


def list_webhooks(request: Request) -> dict[str, Any]:
    require_live_mode()
    with pg_connection() as connection:
        ensure_pilot_schema(connection)
        user = authenticate_with_connection(connection, request)
        workspace_context = resolve_workspace(connection, user['id'], request.headers.get('x-workspace-id'))
        rows = connection.execute(
            '''
            SELECT id, target_url, description, event_types, enabled, secret_last4, created_at, updated_at
            FROM workspace_webhooks
            WHERE workspace_id = %s
            ORDER BY created_at DESC
            ''',
            (workspace_context['workspace_id'],),
        ).fetchall()
        return {'webhooks': [_json_safe_value(dict(row)) for row in rows]}


def create_webhook(payload: dict[str, Any], request: Request) -> dict[str, Any]:
    require_live_mode()
    target_url = str(payload.get('target_url', '')).strip()
    if not target_url.startswith('http'):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='target_url must be an absolute http(s) URL.')
    event_types = payload.get('event_types') if isinstance(payload.get('event_types'), list) else ['analysis.completed']
    with pg_connection() as connection:
        ensure_pilot_schema(connection)
        user, workspace_context = _require_workspace_admin(connection, request)
        secret = secrets.token_urlsafe(32)
        webhook_id = str(uuid.uuid4())
        connection.execute(
            '''
            INSERT INTO workspace_webhooks (id, workspace_id, target_url, description, event_types, secret_hash, secret_last4, secret_token, enabled, created_by_user_id)
            VALUES (%s, %s, %s, %s, %s::jsonb, %s, %s, %s, TRUE, %s)
            ''',
            (
                webhook_id,
                workspace_context['workspace_id'],
                target_url,
                str(payload.get('description', '')).strip() or None,
                _json_dumps(event_types),
                hashlib.sha256(secret.encode('utf-8')).hexdigest(),
                secret[-4:],
                secret,
                user['id'],
            ),
        )
        log_audit(connection, action='webhook.create', entity_type='workspace_webhook', entity_id=webhook_id, request=request, user_id=user['id'], workspace_id=workspace_context['workspace_id'], metadata={'target_url': target_url})
        connection.commit()
        return {'id': webhook_id, 'target_url': target_url, 'event_types': event_types, 'enabled': True, 'secret': secret}


def update_webhook(webhook_id: str, payload: dict[str, Any], request: Request) -> dict[str, Any]:
    require_live_mode()
    with pg_connection() as connection:
        ensure_pilot_schema(connection)
        _, workspace_context = _require_workspace_admin(connection, request)
        webhook = connection.execute('SELECT id FROM workspace_webhooks WHERE id = %s AND workspace_id = %s', (webhook_id, workspace_context['workspace_id'])).fetchone()
        if webhook is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Webhook not found.')
        enabled = bool(payload.get('enabled', True))
        description = str(payload.get('description', '')).strip() or None
        connection.execute(
            'UPDATE workspace_webhooks SET enabled = %s, description = %s, updated_at = NOW() WHERE id = %s',
            (enabled, description, webhook_id),
        )
        connection.commit()
        return {'id': webhook_id, 'enabled': enabled, 'description': description}


def rotate_webhook_secret(webhook_id: str, request: Request) -> dict[str, Any]:
    require_live_mode()
    with pg_connection() as connection:
        ensure_pilot_schema(connection)
        user, workspace_context = _require_workspace_admin(connection, request)
        webhook = connection.execute('SELECT id FROM workspace_webhooks WHERE id = %s AND workspace_id = %s', (webhook_id, workspace_context['workspace_id'])).fetchone()
        if webhook is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Webhook not found.')
        secret = secrets.token_urlsafe(32)
        connection.execute(
            'UPDATE workspace_webhooks SET secret_hash = %s, secret_last4 = %s, secret_token = %s, updated_at = NOW() WHERE id = %s',
            (hashlib.sha256(secret.encode('utf-8')).hexdigest(), secret[-4:], secret, webhook_id),
        )
        log_audit(connection, action='webhook.rotate_secret', entity_type='workspace_webhook', entity_id=webhook_id, request=request, user_id=user['id'], workspace_id=workspace_context['workspace_id'], metadata={})
        connection.commit()
        return {'id': webhook_id, 'secret': secret}


def list_webhook_deliveries(webhook_id: str, request: Request) -> dict[str, Any]:
    require_live_mode()
    with pg_connection() as connection:
        ensure_pilot_schema(connection)
        user = authenticate_with_connection(connection, request)
        workspace_context = resolve_workspace(connection, user['id'], request.headers.get('x-workspace-id'))
        rows = connection.execute(
            '''
            SELECT id, event_type, status, response_status, error_message, attempt, created_at
            FROM webhook_deliveries
            WHERE webhook_id = %s AND workspace_id = %s
            ORDER BY created_at DESC
            LIMIT 100
            ''',
            (webhook_id, workspace_context['workspace_id']),
        ).fetchall()
        return {'deliveries': [_json_safe_value(dict(row)) for row in rows]}


def _normalize_slack_mode(payload: dict[str, Any]) -> str:
    mode = str(payload.get('mode') or payload.get('slack_mode') or 'webhook').strip().lower()
    if mode not in {'webhook', 'bot'}:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='Slack mode must be webhook or bot.')
    return mode


def _normalize_slack_severity_routing(payload: dict[str, Any]) -> dict[str, str]:
    incoming = payload.get('severity_routing')
    if not isinstance(incoming, dict):
        return {'low': 'default', 'medium': 'default', 'high': 'default', 'critical': 'default'}
    normalized: dict[str, str] = {}
    for level in ('low', 'medium', 'high', 'critical'):
        value = str(incoming.get(level) or 'default').strip()
        normalized[level] = value[:80] if value else 'default'
    return normalized


def slack_oauth_configured() -> bool:
    return bool(os.getenv('SLACK_CLIENT_ID', '').strip() and os.getenv('SLACK_CLIENT_SECRET', '').strip())


def slack_oauth_callback_url() -> str:
    configured = os.getenv('SLACK_OAUTH_REDIRECT_URI', '').strip()
    if configured:
        return configured
    api_base = os.getenv('API_PUBLIC_URL', os.getenv('API_URL', 'http://localhost:8000')).rstrip('/')
    return f'{api_base}/integrations/slack/oauth/callback'


def begin_slack_oauth_install(payload: dict[str, Any], request: Request) -> dict[str, Any]:
    require_live_mode()
    if not slack_oauth_configured():
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail='Slack OAuth is unavailable. Configure SLACK_CLIENT_ID and SLACK_CLIENT_SECRET.')
    with pg_connection() as connection:
        ensure_pilot_schema(connection)
        user, workspace_context = _require_workspace_admin(connection, request)
        state_token = secrets.token_urlsafe(32)
        redirect_after_install = str(payload.get('redirect_after_install') or '/integrations').strip() or '/integrations'
        expires_at = utc_now() + timedelta(minutes=SLACK_OAUTH_STATE_TTL_MINUTES)
        connection.execute(
            '''
            INSERT INTO slack_oauth_states (state_token, workspace_id, user_id, redirect_after_install, expires_at)
            VALUES (%s, %s, %s, %s, %s)
            ''',
            (state_token, workspace_context['workspace_id'], user['id'], redirect_after_install[:400], expires_at),
        )
        connection.execute('DELETE FROM slack_oauth_states WHERE expires_at < NOW()')
        connection.commit()
        params = urlencode(
            {
                'client_id': os.getenv('SLACK_CLIENT_ID', '').strip(),
                'scope': os.getenv('SLACK_OAUTH_SCOPES', 'chat:write,incoming-webhook'),
                'redirect_uri': slack_oauth_callback_url(),
                'state': state_token,
            }
        )
        return {'authorize_url': f'https://slack.com/oauth/v2/authorize?{params}', 'expires_at': expires_at.isoformat()}


def complete_slack_oauth_install(*, state_token: str, code: str) -> dict[str, Any]:
    require_live_mode()
    if not slack_oauth_configured():
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail='Slack OAuth is unavailable. Configure SLACK_CLIENT_ID and SLACK_CLIENT_SECRET.')
    with pg_connection() as connection:
        ensure_pilot_schema(connection)
        oauth_state = connection.execute(
            '''
            SELECT state_token, workspace_id, user_id, redirect_after_install
            FROM slack_oauth_states
            WHERE state_token = %s AND expires_at > NOW()
            ''',
            (state_token,),
        ).fetchone()
        if oauth_state is None:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='Slack OAuth state is invalid or expired.')
        data = urlencode(
            {
                'code': code,
                'client_id': os.getenv('SLACK_CLIENT_ID', '').strip(),
                'client_secret': os.getenv('SLACK_CLIENT_SECRET', '').strip(),
                'redirect_uri': slack_oauth_callback_url(),
            }
        ).encode('utf-8')
        try:
            with urlopen(
                UrlRequest(
                    'https://slack.com/api/oauth.v2.access',
                    method='POST',
                    data=data,
                    headers={'Content-Type': 'application/x-www-form-urlencoded'},
                ),
                timeout=15,
            ) as response:
                oauth_payload = json.loads(response.read().decode('utf-8'))
        except (HTTPError, URLError, TimeoutError) as exc:
            raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=f'Failed to complete Slack OAuth exchange: {exc}') from exc
        if not oauth_payload.get('ok'):
            error = str(oauth_payload.get('error') or 'unknown_error')
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f'Slack OAuth exchange failed: {error}')

        workspace_id = str(oauth_state['workspace_id'])
        bot_token = str(oauth_payload.get('access_token') or '').strip()
        incoming_webhook = oauth_payload.get('incoming_webhook') if isinstance(oauth_payload.get('incoming_webhook'), dict) else {}
        webhook_url = str(incoming_webhook.get('url') or '').strip()
        channel_id = str(incoming_webhook.get('channel_id') or '').strip() or None
        channel_name = str(incoming_webhook.get('channel') or '').strip() or None
        team_data = oauth_payload.get('team') if isinstance(oauth_payload.get('team'), dict) else {}
        integration_id = str(uuid.uuid4())
        connection.execute(
            '''
            INSERT INTO workspace_slack_integrations (id, workspace_id, display_name, slack_mode, webhook_url_encrypted, webhook_last4, bot_token_encrypted, bot_token_last4, default_channel, severity_routing, secret_scheme, secret_key_id, enabled, created_by_user_id, installation_method, slack_team_id, slack_team_name, slack_installer_user_id)
            VALUES (%s, %s, %s, 'bot', %s, %s, %s, %s, %s, %s::jsonb, %s, %s, TRUE, %s, 'oauth', %s, %s, %s)
            ''',
            (
                integration_id,
                workspace_id,
                f"Slack ({team_data.get('name') or 'workspace'})",
                (_encode_secret_value(webhook_url, aad=f'slack:{workspace_id}:webhook') if webhook_url else None),
                (webhook_url[-4:] if webhook_url else None),
                (_encode_secret_value(bot_token, aad=f'slack:{workspace_id}:bot') if bot_token else None),
                (bot_token[-4:] if bot_token else None),
                channel_id or channel_name,
                _json_dumps({'low': 'default', 'medium': 'default', 'high': 'default', 'critical': 'default'}),
                'aes256gcm:v1',
                os.getenv('SECRET_ENCRYPTION_KEY_ID', 'env-default').strip() or 'env-default',
                str(oauth_state['user_id']),
                str(team_data.get('id') or '')[:120] or None,
                str(team_data.get('name') or '')[:200] or None,
                str((oauth_payload.get('authed_user') or {}).get('id') or '')[:120] or None,
            ),
        )
        connection.execute('DELETE FROM slack_oauth_states WHERE state_token = %s', (state_token,))
        log_audit(
            connection,
            action='integration.slack.oauth_install',
            entity_type='workspace_slack_integration',
            entity_id=integration_id,
            request=None,
            user_id=str(oauth_state['user_id']),
            workspace_id=workspace_id,
            metadata={'installation_method': 'oauth'},
        )
        connection.commit()
        return {
            'integration_id': integration_id,
            'redirect_after_install': str(oauth_state.get('redirect_after_install') or '/integrations'),
            'team_name': team_data.get('name'),
            'default_channel': channel_id or channel_name,
        }


def list_slack_integrations(request: Request) -> dict[str, Any]:
    require_live_mode()
    with pg_connection() as connection:
        ensure_pilot_schema(connection)
        user = authenticate_with_connection(connection, request)
        workspace_context = resolve_workspace(connection, user['id'], request.headers.get('x-workspace-id'))
        rows = connection.execute(
            '''
            SELECT id, display_name, slack_mode, webhook_last4, bot_token_last4, default_channel, severity_routing, enabled, created_at, updated_at
            FROM workspace_slack_integrations
            WHERE workspace_id = %s
            ORDER BY created_at DESC
            ''',
            (workspace_context['workspace_id'],),
        ).fetchall()
        return {'integrations': [_json_safe_value(dict(row)) for row in rows]}


def create_slack_integration(payload: dict[str, Any], request: Request) -> dict[str, Any]:
    require_live_mode()
    mode = _normalize_slack_mode(payload)
    webhook_url = str(payload.get('webhook_url', '')).strip()
    bot_token = str(payload.get('bot_token', '')).strip()
    default_channel = str(payload.get('default_channel', '')).strip() or None
    display_name = str(payload.get('display_name', '')).strip() or 'Workspace Slack'
    if mode == 'webhook' and not webhook_url.startswith('https://hooks.slack.com/'):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='webhook_url must be a valid Slack incoming webhook URL.')
    if mode == 'bot' and not bot_token.startswith('xoxb-'):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='bot_token must be a valid Slack bot token (xoxb-...).')
    with pg_connection() as connection:
        ensure_pilot_schema(connection)
        user, workspace_context = _require_workspace_admin(connection, request)
        integration_id = str(uuid.uuid4())
        connection.execute(
            '''
            INSERT INTO workspace_slack_integrations (id, workspace_id, display_name, slack_mode, webhook_url_encrypted, webhook_last4, bot_token_encrypted, bot_token_last4, default_channel, severity_routing, secret_scheme, secret_key_id, enabled, created_by_user_id)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s, TRUE, %s)
            ''',
            (
                integration_id,
                workspace_context['workspace_id'],
                display_name,
                mode,
                (_encode_secret_value(webhook_url, aad=f'slack:{workspace_context["workspace_id"]}:webhook') if webhook_url else None),
                (webhook_url[-4:] if webhook_url else None),
                (_encode_secret_value(bot_token, aad=f'slack:{workspace_context["workspace_id"]}:bot') if bot_token else None),
                (bot_token[-4:] if bot_token else None),
                default_channel,
                _json_dumps(_normalize_slack_severity_routing(payload)),
                'aes256gcm:v1',
                os.getenv('SECRET_ENCRYPTION_KEY_ID', 'env-default').strip() or 'env-default',
                user['id'],
            ),
        )
        log_audit(connection, action='integration.slack.create', entity_type='workspace_slack_integration', entity_id=integration_id, request=request, user_id=user['id'], workspace_id=workspace_context['workspace_id'], metadata={'display_name': display_name, 'mode': mode})
        connection.commit()
        return {'id': integration_id, 'display_name': display_name, 'enabled': True, 'mode': mode, 'webhook_last4': webhook_url[-4:] if webhook_url else None, 'bot_token_last4': bot_token[-4:] if bot_token else None}


def update_slack_integration(integration_id: str, payload: dict[str, Any], request: Request) -> dict[str, Any]:
    require_live_mode()
    with pg_connection() as connection:
        ensure_pilot_schema(connection)
        user, workspace_context = _require_workspace_admin(connection, request)
        integration = connection.execute(
            'SELECT id FROM workspace_slack_integrations WHERE id = %s AND workspace_id = %s',
            (integration_id, workspace_context['workspace_id']),
        ).fetchone()
        if integration is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Slack integration not found.')
        next_url = str(payload.get('webhook_url', '')).strip()
        next_bot_token = str(payload.get('bot_token', '')).strip()
        next_mode = str(payload.get('mode') or payload.get('slack_mode') or '').strip().lower() or None
        if next_url and not next_url.startswith('https://hooks.slack.com/'):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='webhook_url must be a valid Slack incoming webhook URL.')
        if next_bot_token and not next_bot_token.startswith('xoxb-'):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='bot_token must be a valid Slack bot token (xoxb-...).')
        if next_mode and next_mode not in {'webhook', 'bot'}:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='Slack mode must be webhook or bot.')
        connection.execute(
            '''
            UPDATE workspace_slack_integrations
            SET display_name = COALESCE(%s, display_name),
                slack_mode = COALESCE(%s, slack_mode),
                default_channel = COALESCE(%s, default_channel),
                enabled = COALESCE(%s, enabled),
                webhook_url_encrypted = COALESCE(%s, webhook_url_encrypted),
                webhook_last4 = COALESCE(%s, webhook_last4),
                bot_token_encrypted = COALESCE(%s, bot_token_encrypted),
                bot_token_last4 = COALESCE(%s, bot_token_last4),
                severity_routing = COALESCE(%s::jsonb, severity_routing),
                secret_scheme = COALESCE(%s, secret_scheme),
                secret_key_id = COALESCE(%s, secret_key_id),
                updated_at = NOW()
            WHERE id = %s
            ''',
            (
                str(payload.get('display_name')).strip() if payload.get('display_name') is not None else None,
                next_mode,
                str(payload.get('default_channel')).strip() if payload.get('default_channel') is not None else None,
                payload.get('enabled') if payload.get('enabled') is not None else None,
                _encode_secret_value(next_url, aad=f'slack:{workspace_context["workspace_id"]}:webhook') if next_url else None,
                (next_url[-4:] if next_url else None),
                _encode_secret_value(next_bot_token, aad=f'slack:{workspace_context["workspace_id"]}:bot') if next_bot_token else None,
                (next_bot_token[-4:] if next_bot_token else None),
                _json_dumps(_normalize_slack_severity_routing(payload)) if payload.get('severity_routing') is not None else None,
                'aes256gcm:v1' if (next_url or next_bot_token) else None,
                os.getenv('SECRET_ENCRYPTION_KEY_ID', 'env-default').strip() or 'env-default' if (next_url or next_bot_token) else None,
                integration_id,
            ),
        )
        log_audit(connection, action='integration.slack.update', entity_type='workspace_slack_integration', entity_id=integration_id, request=request, user_id=user['id'], workspace_id=workspace_context['workspace_id'], metadata={})
        connection.commit()
        row = connection.execute('SELECT id, display_name, slack_mode, enabled, webhook_last4, bot_token_last4, default_channel, severity_routing FROM workspace_slack_integrations WHERE id = %s', (integration_id,)).fetchone()
        return _json_safe_value(dict(row))


def delete_slack_integration(integration_id: str, request: Request) -> dict[str, Any]:
    require_live_mode()
    with pg_connection() as connection:
        ensure_pilot_schema(connection)
        user, workspace_context = _require_workspace_admin(connection, request)
        deleted = connection.execute('DELETE FROM workspace_slack_integrations WHERE id = %s AND workspace_id = %s', (integration_id, workspace_context['workspace_id'])).rowcount
        if not deleted:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Slack integration not found.')
        log_audit(connection, action='integration.slack.delete', entity_type='workspace_slack_integration', entity_id=integration_id, request=request, user_id=user['id'], workspace_id=workspace_context['workspace_id'], metadata={})
        connection.commit()
        return {'deleted': True, 'id': integration_id}


def list_slack_deliveries(integration_id: str, request: Request) -> dict[str, Any]:
    require_live_mode()
    with pg_connection() as connection:
        ensure_pilot_schema(connection)
        user = authenticate_with_connection(connection, request)
        workspace_context = resolve_workspace(connection, user['id'], request.headers.get('x-workspace-id'))
        rows = connection.execute(
            '''
            SELECT id, event_type, status, response_status, error_message, attempt, provider_mode, created_at
            FROM slack_deliveries
            WHERE slack_integration_id = %s AND workspace_id = %s
            ORDER BY created_at DESC
            LIMIT 100
            ''',
            (integration_id, workspace_context['workspace_id']),
        ).fetchall()
        return {'deliveries': [_json_safe_value(dict(row)) for row in rows]}


def test_slack_integration(integration_id: str, request: Request) -> dict[str, Any]:
    require_live_mode()
    with pg_connection() as connection:
        ensure_pilot_schema(connection)
        user, workspace_context = _require_workspace_admin(connection, request)
        integration = connection.execute(
            'SELECT id, display_name, slack_mode, default_channel, webhook_url_encrypted, bot_token_encrypted FROM workspace_slack_integrations WHERE id = %s AND workspace_id = %s',
            (integration_id, workspace_context['workspace_id']),
        ).fetchone()
        if integration is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Slack integration not found.')
        workspace_row = connection.execute('SELECT name FROM workspaces WHERE id = %s', (workspace_context['workspace_id'],)).fetchone()
        now = utc_now_iso()
        text = f'[{workspace_row["name"]}] Slack integration test from Decoda RWA Guard at {now}'
        slack_payload = {'text': text, 'blocks': [{'type': 'section', 'text': {'type': 'mrkdwn', 'text': text}}]}
        delivery_id = str(uuid.uuid4())
        connection.execute(
            '''
            INSERT INTO slack_deliveries (id, workspace_id, slack_integration_id, event_type, request_body, status, provider_mode, response_status, response_body, error_message, attempt)
            VALUES (%s, %s, %s, 'alert.test', %s::jsonb, 'queued', %s, NULL, NULL, NULL, 0)
            ''',
            (delivery_id, workspace_context['workspace_id'], integration_id, _json_dumps(slack_payload), str(integration.get('slack_mode') or 'webhook')),
        )
        _queue_background_job(
            connection,
            job_type='send_slack',
            payload={
                'slack_integration_id': integration_id,
                'delivery_id': delivery_id,
                'mode': str(integration.get('slack_mode') or 'webhook'),
                'default_channel': str(integration.get('default_channel') or ''),
                'webhook_url': _decode_secret_value(str(integration['webhook_url_encrypted']), aad=f'slack:{workspace_context["workspace_id"]}:webhook') if integration.get('webhook_url_encrypted') else '',
                'bot_token': _decode_secret_value(str(integration['bot_token_encrypted']), aad=f'slack:{workspace_context["workspace_id"]}:bot') if integration.get('bot_token_encrypted') else '',
                'payload': slack_payload,
            },
            max_attempts=4,
        )
        log_audit(connection, action='integration.slack.test', entity_type='workspace_slack_integration', entity_id=integration_id, request=request, user_id=user['id'], workspace_id=workspace_context['workspace_id'], metadata={})
        connection.commit()
        return {'queued': True, 'delivery_id': delivery_id}


def list_alert_routing_rules(request: Request) -> dict[str, Any]:
    require_live_mode()
    with pg_connection() as connection:
        ensure_pilot_schema(connection)
        user = authenticate_with_connection(connection, request)
        workspace_context = resolve_workspace(connection, user['id'], request.headers.get('x-workspace-id'))
        rows = connection.execute(
            '''
            SELECT id, channel_type, severity_threshold, modules_include, modules_exclude, target_ids, event_types, target_types, enabled, created_at, updated_at
            FROM alert_routing_rules
            WHERE workspace_id = %s
            ORDER BY channel_type ASC
            ''',
            (workspace_context['workspace_id'],),
        ).fetchall()
        return {'rules': [_json_safe_value(dict(row)) for row in rows]}


def upsert_alert_routing_rule(channel_type: str, payload: dict[str, Any], request: Request) -> dict[str, Any]:
    require_live_mode()
    normalized = _normalize_routing_payload(payload, channel_type=channel_type)
    with pg_connection() as connection:
        ensure_pilot_schema(connection)
        user, workspace_context = _require_workspace_admin(connection, request)
        rule_id = str(uuid.uuid4())
        connection.execute(
            '''
            INSERT INTO alert_routing_rules (id, workspace_id, channel_type, severity_threshold, modules_include, modules_exclude, target_ids, event_types, target_types, enabled, created_by_user_id)
            VALUES (%s, %s, %s, %s, %s::jsonb, %s::jsonb, %s::jsonb, %s::jsonb, %s::jsonb, %s, %s)
            ON CONFLICT (workspace_id, channel_type)
            DO UPDATE SET severity_threshold = EXCLUDED.severity_threshold,
                          modules_include = EXCLUDED.modules_include,
                          modules_exclude = EXCLUDED.modules_exclude,
                          target_ids = EXCLUDED.target_ids,
                          event_types = EXCLUDED.event_types,
                          target_types = EXCLUDED.target_types,
                          enabled = EXCLUDED.enabled,
                          updated_at = NOW()
            ''',
            (
                rule_id,
                workspace_context['workspace_id'],
                normalized['channel_type'],
                normalized['severity_threshold'],
                _json_dumps(normalized['modules_include']),
                _json_dumps(normalized['modules_exclude']),
                _json_dumps(normalized['target_ids']),
                _json_dumps(normalized['event_types']),
                _json_dumps(normalized['target_types']),
                normalized['enabled'],
                user['id'],
            ),
        )
        row = connection.execute('SELECT id, channel_type, severity_threshold, modules_include, modules_exclude, target_ids, event_types, target_types, enabled FROM alert_routing_rules WHERE workspace_id = %s AND channel_type = %s', (workspace_context['workspace_id'], channel_type)).fetchone()
        log_audit(connection, action='alert.routing.update', entity_type='alert_routing_rule', entity_id=str(row['id']), request=request, user_id=user['id'], workspace_id=workspace_context['workspace_id'], metadata={'channel_type': channel_type})
        connection.commit()
        return {'rule': _json_safe_value(dict(row))}


def persist_analysis_run(
    connection: Any,
    *,
    workspace_id: str,
    user_id: str,
    analysis_type: str,
    service_name: str,
    title: str,
    status_value: str,
    request_payload: dict[str, Any],
    response_payload: dict[str, Any],
    request: Request,
) -> str:
    analysis_run_id = str(uuid.uuid4())
    summary = str(response_payload.get('explanation') or response_payload.get('explainability_summary') or response_payload.get('summary') or title)
    source = str(response_payload.get('source') or 'live')
    analysis_source = str(response_payload.get('analysis_source') or source)
    analysis_status = str(response_payload.get('analysis_status') or 'completed')
    degraded_reason = response_payload.get('degraded_reason')
    connection.execute(
        '''
        INSERT INTO analysis_runs (id, workspace_id, user_id, analysis_type, service_name, status, title, source, summary, analysis_source, analysis_status, degraded_reason, request_payload, response_payload, created_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb, NOW())
        ''',
        (
            analysis_run_id,
            workspace_id,
            user_id,
            analysis_type,
            service_name,
            status_value,
            title,
            source,
            summary,
            analysis_source,
            analysis_status,
            degraded_reason,
            _json_dumps(request_payload),
            _json_dumps(response_payload),
        ),
    )
    log_audit(
        connection,
        action='analysis.run',
        entity_type='analysis_run',
        entity_id=analysis_run_id,
        request=request,
        user_id=user_id,
        workspace_id=workspace_id,
        metadata={'analysis_type': analysis_type, 'service_name': service_name, 'status': status_value},
    )
    return analysis_run_id


def maybe_insert_alert(
    connection: Any,
    *,
    workspace_id: str,
    user_id: str,
    analysis_run_id: str,
    alert_type: str,
    title: str,
    response_payload: dict[str, Any],
) -> str | None:
    severity = str(response_payload.get('severity') or response_payload.get('risk_level') or '').strip().lower()
    action = str(response_payload.get('recommended_action') or response_payload.get('decision') or response_payload.get('backstop_decision') or '').strip()
    if severity in {'', 'low', 'info'} and action.lower() in {'allow', 'approved', 'normal', ''}:
        return None
    alert_id = str(uuid.uuid4())
    connection.execute(
        '''
        INSERT INTO alerts (id, workspace_id, user_id, analysis_run_id, alert_type, title, severity, status, source_service, summary, payload, created_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, NOW())
        ''',
        (
            alert_id,
            workspace_id,
            user_id,
            analysis_run_id,
            alert_type,
            title,
            severity or 'medium',
            'open',
            str(response_payload.get('source') or 'live'),
            str(response_payload.get('explanation') or response_payload.get('explainability_summary') or title),
            _json_dumps(response_payload),
        ),
    )
    _queue_alert_deliveries(
        connection,
        workspace_id=workspace_id,
        alert_id=alert_id,
        title=title,
        severity=severity or 'medium',
        summary=str(response_payload.get('explanation') or response_payload.get('explainability_summary') or title),
        payload=response_payload,
    )
    return alert_id


def _queue_alert_deliveries(
    connection: Any,
    *,
    workspace_id: str,
    alert_id: str,
    title: str,
    severity: str,
    summary: str,
    payload: dict[str, Any],
) -> None:
    channel_rules = {
        str(rule['channel_type']): dict(rule)
        for rule in connection.execute(
            '''
            SELECT channel_type, severity_threshold, modules_include, modules_exclude, target_ids, event_types, target_types, enabled
            FROM alert_routing_rules
            WHERE workspace_id = %s
            ''',
            (workspace_id,),
        ).fetchall()
    }

    def route_enabled(channel: str) -> bool:
        rule = channel_rules.get(channel)
        if rule is None:
            return True
        if not bool(rule.get('enabled')):
            return False
        if not _severity_meets_threshold(severity, str(rule.get('severity_threshold') or 'medium')):
            return False
        module_key = str(payload.get('module_name') or payload.get('module_key') or '').strip().lower()
        modules_include = [str(item).strip().lower() for item in (rule.get('modules_include') or []) if str(item).strip()]
        modules_exclude = [str(item).strip().lower() for item in (rule.get('modules_exclude') or []) if str(item).strip()]
        if modules_include and module_key not in modules_include:
            return False
        if modules_exclude and module_key in modules_exclude:
            return False
        target_id = str(payload.get('target_id') or '').strip()
        target_ids = [str(item).strip() for item in (rule.get('target_ids') or []) if str(item).strip()]
        if target_ids and target_id and target_id not in target_ids:
            return False
        target_type = str(payload.get('target_type') or '').strip().lower()
        target_types = [str(item).strip().lower() for item in (rule.get('target_types') or []) if str(item).strip()]
        if target_types and target_type and target_type not in target_types:
            return False
        event_types = [str(item).strip() for item in (rule.get('event_types') or []) if str(item).strip()]
        return 'alert.created' in event_types if event_types else True

    event_payload = {
        'event': 'alert.created',
        'alert_id': alert_id,
        'title': title,
        'severity': severity,
        'summary': summary,
        'payload': payload,
        'occurred_at': utc_now_iso(),
    }
    if route_enabled('webhook'):
        webhooks = connection.execute(
            '''
            SELECT id, target_url, secret_token
            FROM workspace_webhooks
            WHERE workspace_id = %s AND enabled = TRUE
            ''',
            (workspace_id,),
        ).fetchall()
        for webhook in webhooks:
            delivery_id = str(uuid.uuid4())
            connection.execute(
                '''
                INSERT INTO webhook_deliveries (id, workspace_id, webhook_id, event_type, request_body, status, response_status, response_body, error_message, attempt, created_at, updated_at)
                VALUES (%s, %s, %s, %s, %s::jsonb, 'queued', NULL, NULL, NULL, 0, NOW(), NOW())
                ''',
                (delivery_id, workspace_id, webhook['id'], 'alert.created', _json_dumps(event_payload)),
            )
            _queue_background_job(
                connection,
                job_type='send_webhook',
                payload={
                    'webhook_id': str(webhook['id']),
                    'delivery_id': delivery_id,
                    'target_url': str(webhook['target_url']),
                    'secret': str(webhook.get('secret_token') or ''),
                    'payload': event_payload,
                },
                max_attempts=4,
            )

    if route_enabled('email') and severity in {'high', 'critical'}:
        recipients = connection.execute(
            '''
            SELECT DISTINCT u.email
            FROM workspace_members wm
            JOIN users u ON u.id = wm.user_id
            WHERE wm.workspace_id = %s AND u.email IS NOT NULL
            ''',
            (workspace_id,),
        ).fetchall()
        for recipient in recipients:
            email = str(recipient.get('email') or '').strip().lower()
            if not email:
                continue
            _queue_background_job(
                connection,
                job_type='send_alert_email',
                payload={'to_email': email, 'title': title, 'summary': summary, 'alert_id': alert_id},
                max_attempts=4,
            )

    if route_enabled('slack'):
        integrations = connection.execute(
            '''
            SELECT id, slack_mode, default_channel, webhook_url_encrypted, bot_token_encrypted
            FROM workspace_slack_integrations
            WHERE workspace_id = %s AND enabled = TRUE
            ''',
            (workspace_id,),
        ).fetchall()
        workspace = connection.execute('SELECT name FROM workspaces WHERE id = %s', (workspace_id,)).fetchone()
        module_name = str(payload.get('module_name') or payload.get('module_key') or 'analysis').strip()
        target_name = str(payload.get('target_name') or payload.get('target_id') or 'n/a').strip()
        findings = payload.get('findings') if isinstance(payload.get('findings'), list) else []
        top_reasons = '\n'.join([f'• {str(item)}' for item in findings[:3]]) if findings else '• Review the finding details in Decoda.'
        app_url = os.getenv('APP_URL', 'http://localhost:3000').rstrip('/')
        deep_link = f'{app_url}/alerts?alertId={alert_id}'
        text = f'[{workspace["name"]}] {severity.upper()} alert in {module_name}: {title}'
        slack_payload = {
            'text': text,
            'blocks': [
                {'type': 'header', 'text': {'type': 'plain_text', 'text': f'{severity.upper()} alert: {title}'[:150]}},
                {'type': 'section', 'fields': [
                    {'type': 'mrkdwn', 'text': f'*Workspace*\n{workspace["name"]}'},
                    {'type': 'mrkdwn', 'text': f'*Module*\n{module_name}'},
                    {'type': 'mrkdwn', 'text': f'*Severity*\n{severity}'},
                    {'type': 'mrkdwn', 'text': f'*Target*\n{target_name}'},
                ]},
                {'type': 'section', 'text': {'type': 'mrkdwn', 'text': f'*Summary*\n{summary[:700]}'}},
                {'type': 'section', 'text': {'type': 'mrkdwn', 'text': f'*Top reasons/findings*\n{top_reasons[:900]}'}},
                {'type': 'section', 'text': {'type': 'mrkdwn', 'text': f'*Timestamp*\n{event_payload["occurred_at"]}'}},
                {'type': 'actions', 'elements': [{'type': 'button', 'text': {'type': 'plain_text', 'text': 'Open in Decoda'}, 'url': deep_link}]},
            ],
        }
        for integration in integrations:
            delivery_id = str(uuid.uuid4())
            provider_mode = str(integration.get('slack_mode') or 'webhook')
            connection.execute(
                '''
                INSERT INTO slack_deliveries (id, workspace_id, slack_integration_id, event_type, request_body, status, provider_mode, response_status, response_body, error_message, attempt, created_at, updated_at)
                VALUES (%s, %s, %s, %s, %s::jsonb, 'queued', %s, NULL, NULL, NULL, 0, NOW(), NOW())
                ''',
                (delivery_id, workspace_id, integration['id'], 'alert.created', _json_dumps(slack_payload), provider_mode),
            )
            _queue_background_job(
                connection,
                job_type='send_slack',
                payload={
                    'slack_integration_id': str(integration['id']),
                    'delivery_id': delivery_id,
                    'mode': provider_mode,
                    'default_channel': str(integration.get('default_channel') or ''),
                    'webhook_url': _decode_secret_value(str(integration.get('webhook_url_encrypted') or ''), aad=f'slack:{workspace_id}:webhook'),
                    'bot_token': _decode_secret_value(str(integration.get('bot_token_encrypted') or ''), aad=f'slack:{workspace_id}:bot'),
                    'payload': slack_payload,
                },
                max_attempts=4,
            )



def create_governance_action_record(
    connection: Any,
    *,
    workspace_id: str,
    user_id: str,
    analysis_run_id: str,
    payload: dict[str, Any],
    response_payload: dict[str, Any],
) -> str:
    governance_id = str(uuid.uuid4())
    connection.execute(
        '''
        INSERT INTO governance_actions (id, workspace_id, user_id, analysis_run_id, action_type, target_type, target_id, status, reason, payload, created_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, NOW())
        ''',
        (
            governance_id,
            workspace_id,
            user_id,
            analysis_run_id,
            str(response_payload.get('action_type') or payload.get('action_type') or 'governance_action'),
            str(response_payload.get('target_type') or payload.get('target_type') or 'workspace'),
            str(response_payload.get('target_id') or payload.get('target_id') or workspace_id),
            str(response_payload.get('status') or 'recorded'),
            str(response_payload.get('reason') or payload.get('reason') or 'Governance action recorded.'),
            _json_dumps({'request': payload, 'response': response_payload}),
        ),
    )
    return governance_id


def create_incident_record(
    connection: Any,
    *,
    workspace_id: str,
    user_id: str,
    analysis_run_id: str,
    payload: dict[str, Any],
    response_payload: dict[str, Any],
) -> str:
    incident_id = str(uuid.uuid4())
    connection.execute(
        '''
        INSERT INTO incidents (id, workspace_id, user_id, analysis_run_id, event_type, severity, status, summary, payload, created_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, NOW())
        ''',
        (
            incident_id,
            workspace_id,
            user_id,
            analysis_run_id,
            str(response_payload.get('event_type') or payload.get('event_type') or 'incident'),
            str(response_payload.get('severity') or payload.get('severity') or 'medium'),
            str(response_payload.get('status') or payload.get('status') or 'open'),
            str(response_payload.get('summary') or payload.get('summary') or 'Incident recorded.'),
            _json_dumps({'request': payload, 'response': response_payload}),
        ),
    )
    return incident_id


def build_history_response(request: Request, limit: int = 25) -> dict[str, Any]:
    require_live_mode()
    limit = max(1, min(limit, 100))
    with pg_connection() as connection:
        user = authenticate_with_connection(connection, request)
        workspace_context = resolve_workspace(connection, user['id'], request.headers.get('x-workspace-id'))
        workspace_id = workspace_context['workspace_id']
        analysis_runs = connection.execute(
            '''
            SELECT id, analysis_type, service_name, status, title, source, summary, request_payload, response_payload, created_at
            FROM analysis_runs
            WHERE workspace_id = %s
            ORDER BY created_at DESC
            LIMIT %s
            ''',
            (workspace_id, limit),
        ).fetchall()
        alerts = connection.execute(
            '''
            SELECT id, alert_type, title, severity, status, source_service, summary, payload, created_at
            FROM alerts
            WHERE workspace_id = %s
            ORDER BY created_at DESC
            LIMIT %s
            ''',
            (workspace_id, limit),
        ).fetchall()
        governance_actions = connection.execute(
            '''
            SELECT id, action_type, target_type, target_id, status, reason, payload, created_at
            FROM governance_actions
            WHERE workspace_id = %s
            ORDER BY created_at DESC
            LIMIT %s
            ''',
            (workspace_id, limit),
        ).fetchall()
        incidents = connection.execute(
            '''
            SELECT id, event_type, severity, status, summary, payload, created_at
            FROM incidents
            WHERE workspace_id = %s
            ORDER BY created_at DESC
            LIMIT %s
            ''',
            (workspace_id, limit),
        ).fetchall()
        audit_logs = connection.execute(
            '''
            SELECT id, action, entity_type, entity_id, ip_address, metadata, created_at
            FROM audit_logs
            WHERE workspace_id = %s OR (workspace_id IS NULL AND user_id = %s)
            ORDER BY created_at DESC
            LIMIT %s
            ''',
            (workspace_id, user['id'], limit),
        ).fetchall()
        counts = connection.execute(
            '''
            SELECT
                (SELECT COUNT(*) FROM analysis_runs WHERE workspace_id = %s) AS analysis_runs,
                (SELECT COUNT(*) FROM alerts WHERE workspace_id = %s) AS alerts,
                (SELECT COUNT(*) FROM governance_actions WHERE workspace_id = %s) AS governance_actions,
                (SELECT COUNT(*) FROM incidents WHERE workspace_id = %s) AS incidents,
                (SELECT COUNT(*) FROM audit_logs WHERE workspace_id = %s OR (workspace_id IS NULL AND user_id = %s)) AS audit_logs
            ''',
            (workspace_id, workspace_id, workspace_id, workspace_id, workspace_id, user['id']),
        ).fetchone()

    def serialize(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        serialized: list[dict[str, Any]] = []
        for row in rows:
            item: dict[str, Any] = {}
            for key, value in row.items():
                if hasattr(value, 'isoformat'):
                    item[key] = value.isoformat()
                elif isinstance(value, uuid.UUID):
                    item[key] = str(value)
                else:
                    item[key] = _json_safe_value(value)
            serialized.append(item)
        return serialized

    return {
        'mode': 'live',
        'workspace': _json_safe_value(workspace_context['workspace']),
        'role': str(workspace_context['role']),
        'counts': _json_safe_value(counts),
        'analysis_runs': serialize(analysis_runs),
        'alerts': serialize(alerts),
        'governance_actions': serialize(governance_actions),
        'incidents': serialize(incidents),
        'audit_logs': serialize(audit_logs),
    }

def _coerce_bool(value: Any, default: bool) -> bool:
    return bool(value) if isinstance(value, bool) else default


def _coerce_number(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def normalize_module_config(module_key: str, config: dict[str, Any]) -> dict[str, Any]:
    if module_key == 'threat':
        return {
            'risky_approvals_enabled': _coerce_bool(config.get('risky_approvals_enabled'), True),
            'unlimited_approval_detection_enabled': _coerce_bool(config.get('unlimited_approval_detection_enabled'), True),
            'unknown_target_threshold': max(0, int(_coerce_number(config.get('unknown_target_threshold'), 2))),
            'privileged_function_sensitivity': str(config.get('privileged_function_sensitivity') or 'high').lower() if str(config.get('privileged_function_sensitivity') or 'high').lower() in {'low', 'medium', 'high', 'critical'} else 'high',
            'large_transfer_threshold': max(1, _coerce_number(config.get('large_transfer_threshold'), 250000)),
            'allowlist': [str(item).strip() for item in (config.get('allowlist') or []) if str(item).strip()] if isinstance(config.get('allowlist'), list) else [],
            'denylist': [str(item).strip() for item in (config.get('denylist') or []) if str(item).strip()] if isinstance(config.get('denylist'), list) else [],
            'escalation_map': config.get('escalation_map') if isinstance(config.get('escalation_map'), dict) else {'low': 'low', 'medium': 'medium', 'high': 'high', 'critical': 'critical'},
        }
    if module_key == 'compliance':
        return {
            'evidence_retention_period_days': max(30, int(_coerce_number(config.get('evidence_retention_period_days'), 90))),
            'required_review_checklist': [str(item).strip() for item in (config.get('required_review_checklist') or []) if str(item).strip()] if isinstance(config.get('required_review_checklist'), list) else [],
            'required_approvers_count': max(1, int(_coerce_number(config.get('required_approvers_count'), 2))),
            'classification_mapping': config.get('classification_mapping') if isinstance(config.get('classification_mapping'), dict) else {'pii': 'restricted', 'transaction': 'confidential'},
            'exception_policy': str(config.get('exception_policy') or 'manual_review'),
            'reporting_profile': str(config.get('reporting_profile') or 'standard'),
        }
    if module_key == 'resilience':
        return {
            'oracle_dependency_checks_enabled': _coerce_bool(config.get('oracle_dependency_checks_enabled'), True),
            'oracle_sensitivity_threshold': max(1, int(_coerce_number(config.get('oracle_sensitivity_threshold'), 70))),
            'settlement_control_checks_enabled': _coerce_bool(config.get('settlement_control_checks_enabled'), True),
            'control_concentration_threshold': max(1, int(_coerce_number(config.get('control_concentration_threshold'), 65))),
            'privileged_role_change_alerts': _coerce_bool(config.get('privileged_role_change_alerts'), True),
            'emergency_trigger_threshold': str(config.get('emergency_trigger_threshold') or 'high'),
            'monitoring_cadence_minutes': max(1, int(_coerce_number(config.get('monitoring_cadence_minutes'), 15))),
        }
    return config


def summarize_module_config(module_key: str, config: dict[str, Any]) -> str:
    if module_key == 'threat':
        return f"Unlimited approvals={'on' if config.get('unlimited_approval_detection_enabled') else 'off'}, large transfer>{config.get('large_transfer_threshold')}"
    if module_key == 'compliance':
        return f"Retention={config.get('evidence_retention_period_days')} days, approvers={config.get('required_approvers_count')}"
    if module_key == 'resilience':
        return f"Oracle checks={'on' if config.get('oracle_dependency_checks_enabled') else 'off'}, emergency={config.get('emergency_trigger_threshold')}"
    return 'Configured'


TARGET_TYPES = {'contract', 'wallet', 'oracle', 'treasury-linked asset', 'settlement component', 'admin-controlled module'}
MODULE_KEYS = {'threat', 'compliance', 'resilience'}
ASSET_TYPES = {
    'contract',
    'wallet',
    'treasury-linked asset',
    'oracle',
    'custody component',
    'settlement component',
    'admin-controlled module',
    'monitored counterparty',
    'policy-controlled workflow object',
}
ASSET_CLASSES = {'treasury_token', 'bond_token', 'money_market_token', 'rwa_other'}
BASELINE_SOURCES = {'observed', 'manual', 'imported'}
ASSET_TOKEN_STANDARDS = {'erc20', 'erc4626', 'erc721', 'unknown'}
ASSET_WALLET_ROLES = {'treasury_ops', 'custody', 'counterparty', 'venue'}

_ERC20_NAME_SELECTOR = '0x06fdde03'
_ERC20_SYMBOL_SELECTOR = '0x95d89b41'
_ERC20_DECIMALS_SELECTOR = '0x313ce567'
_ERC4626_ASSET_SELECTOR = '0x38d52e0f'
_ERC165_SUPPORTS_INTERFACE_SELECTOR = '0x01ffc9a7'
_ERC721_INTERFACE_ID = '80ac58cd'


def _normalize_address_list(value: Any, *, field_name: str) -> list[str]:
    items = [str(item).strip().lower() for item in value] if isinstance(value, list) else []
    filtered: list[str] = []
    for item in items:
        if not item:
            continue
        if not re.match(r'^0x[a-f0-9]{40}$', item):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f'{field_name} entries must be EVM-style addresses.')
        filtered.append(item)
    return filtered[:100]


def _rpc_url_for_chain(chain_network: str, *, rpc_url_override: str | None = None) -> str:
    if rpc_url_override:
        return rpc_url_override
    suffix = re.sub(r'[^a-z0-9]+', '_', (chain_network or '').strip().lower()).strip('_').upper()
    if suffix:
        candidate = os.getenv(f'EVM_RPC_URL_{suffix}', '').strip()
        if candidate:
            return candidate
    return os.getenv('EVM_RPC_URL', '').strip()


def _eth_call_raw(rpc_url: str, *, to_address: str, data: str) -> str:
    payload = {'jsonrpc': '2.0', 'id': 1, 'method': 'eth_call', 'params': [{'to': to_address, 'data': data}, 'latest']}
    request = UrlRequest(rpc_url, data=_json_dumps(payload).encode('utf-8'), headers={'Content-Type': 'application/json'})
    with urlopen(request, timeout=10) as response:  # nosec B310
        body = json.loads(response.read().decode('utf-8') or '{}')
    if body.get('error'):
        raise RuntimeError(f"json-rpc error: {body.get('error')}")
    value = str(body.get('result') or '')
    if not value.startswith('0x'):
        raise RuntimeError('json-rpc eth_call returned invalid hex payload')
    return value


def _decode_uint256(hex_value: str) -> int:
    value = hex_value[2:] if hex_value.startswith('0x') else hex_value
    if not value:
        return 0
    return int(value, 16)


def _decode_abi_string(hex_value: str) -> str | None:
    raw = hex_value[2:] if hex_value.startswith('0x') else hex_value
    if len(raw) < 128:
        return None
    try:
        offset = int(raw[:64], 16) * 2
        length = int(raw[offset:offset + 64], 16) * 2
        data_start = offset + 64
        text_hex = raw[data_start:data_start + length]
        if len(text_hex) != length:
            return None
        return bytes.fromhex(text_hex).decode('utf-8', errors='ignore').strip() or None
    except Exception:
        return None


def _encode_supports_interface_call(interface_id_hex: str) -> str:
    normalized = interface_id_hex.replace('0x', '').strip().lower()
    if len(normalized) != 8:
        raise ValueError('interface id must be 4 bytes / 8 hex chars')
    return f'{_ERC165_SUPPORTS_INTERFACE_SELECTOR}{normalized.rjust(64, "0")}'


def _detect_token_standard(rpc_url: str, token_address: str) -> str:
    try:
        _eth_call_raw(rpc_url, to_address=token_address, data=_ERC20_DECIMALS_SELECTOR)
        standard = 'erc20'
    except Exception:
        standard = 'unknown'
    try:
        asset_response = _eth_call_raw(rpc_url, to_address=token_address, data=_ERC4626_ASSET_SELECTOR)
        asset_value = asset_response[2:].rjust(64, '0')
        if int(asset_value[-40:], 16) != 0:
            return 'erc4626'
    except Exception:
        pass
    if standard == 'erc20':
        return standard
    try:
        supports = _eth_call_raw(rpc_url, to_address=token_address, data=_encode_supports_interface_call(_ERC721_INTERFACE_ID))
        return 'erc721' if bool(_decode_uint256(supports)) else 'unknown'
    except Exception:
        return 'unknown'


def billing_provider() -> str:
    value = os.getenv('BILLING_PROVIDER', 'paddle').strip().lower()
    if value in {'none', 'stripe', 'paddle'}:
        return value
    return 'paddle'


def billing_runtime_status() -> dict[str, Any]:
    provider = billing_provider()
    strict_billing = env_flag('STRICT_PRODUCTION_BILLING')
    if provider == 'none':
        return {
            'provider': 'none',
            'status': 'not_configured',
            'available': False,
            'checks': {'provider_selected': True, 'credentials_present': False},
            'message': 'Billing is not configured yet because BILLING_PROVIDER=none.',
            'strict_required': strict_billing,
        }
    if provider == 'paddle':
        paddle_config = paddle_runtime_config()
        status_value = 'healthy' if paddle_config['configured'] else 'degraded'
        message = 'Paddle configuration looks healthy.' if paddle_config['configured'] else 'Paddle billing is unavailable because required PADDLE_* variables are missing.'
        return {
            'provider': provider,
            'status': status_value,
            'available': paddle_config['configured'],
            'checks': {
                'paddle_api_key_present': paddle_config['api_key_present'],
                'paddle_webhook_secret_present': paddle_config['webhook_secret_present'],
                'paddle_price_ids_configured': paddle_config['price_ids_configured'],
            },
            'message': message,
            'strict_required': strict_billing,
        }
    stripe_key = bool(os.getenv('STRIPE_SECRET_KEY', '').strip())
    stripe_hook = bool(os.getenv('STRIPE_WEBHOOK_SECRET', '').strip())
    available = stripe_key and stripe_hook
    return {
        'provider': provider,
        'status': 'healthy' if available else 'degraded',
        'available': available,
        'checks': {'stripe_secret_key_present': stripe_key, 'stripe_webhook_secret_present': stripe_hook},
        'message': 'Stripe configuration looks healthy.' if available else 'Stripe billing is unavailable because STRIPE_SECRET_KEY or STRIPE_WEBHOOK_SECRET is missing.',
        'strict_required': strict_billing,
    }


def _billing_unavailable_detail(*, operation: str, expected_provider: str | None = None) -> dict[str, Any]:
    billing_status = billing_runtime_status()
    message = billing_status['message']
    reason = 'provider_not_ready'
    if billing_status.get('provider') == 'none':
        reason = 'disabled_by_configuration'
    if expected_provider and billing_status['provider'] != expected_provider:
        message = f"Billing endpoint requires provider={expected_provider} but BILLING_PROVIDER={billing_status['provider']}."
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


def ensure_billing_available(*, operation: str, expected_provider: str | None = None) -> None:
    billing_status = billing_runtime_status()
    if expected_provider and billing_status['provider'] != expected_provider:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=_billing_unavailable_detail(operation=operation, expected_provider=expected_provider))
    if not billing_status['available']:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=_billing_unavailable_detail(operation=operation))


def paddle_runtime_config() -> dict[str, Any]:
    api_key_present = bool(os.getenv('PADDLE_API_KEY', '').strip())
    webhook_secret_present = bool(os.getenv('PADDLE_WEBHOOK_SECRET', '').strip())
    environment = os.getenv('PADDLE_ENVIRONMENT', 'sandbox').strip().lower()
    price_id_names = [key for key, _ in os.environ.items() if key.startswith('PADDLE_PRICE_ID_')]
    return {
        'api_key_present': api_key_present,
        'webhook_secret_present': webhook_secret_present,
        'environment': environment if environment in {'sandbox', 'live'} else 'sandbox',
        'price_ids_configured': bool(price_id_names),
        'configured': api_key_present and webhook_secret_present and bool(price_id_names),
        'client_token_present': bool(os.getenv('PADDLE_CLIENT_TOKEN', '').strip()),
    }


def _workspace_plan(connection: Any, workspace_id: str) -> dict[str, Any]:
    subscription = connection.execute(
        '''
        SELECT plan_key
        FROM billing_subscriptions
        WHERE workspace_id = %s AND status IN ('trialing', 'active')
        ORDER BY created_at DESC
        LIMIT 1
        ''',
        (workspace_id,),
    ).fetchone()
    plan_key = str((subscription or {}).get('plan_key') or 'free_trial')
    plan = connection.execute(
        '''
        SELECT plan_key, max_members, max_webhooks, max_targets, exports_enabled, alert_retention_days, features
        FROM plan_entitlements
        WHERE plan_key = %s
        ''',
        (plan_key,),
    ).fetchone()
    if plan is None:
        fallback = connection.execute(
            '''
            SELECT plan_key, max_members, max_webhooks, max_targets, exports_enabled, alert_retention_days, features
            FROM plan_entitlements
            WHERE plan_key = 'free_trial'
            LIMIT 1
            '''
        ).fetchone()
        plan = fallback or {
            'plan_key': 'free_trial',
            'max_members': 3,
            'max_webhooks': 0,
            'max_targets': 10,
            'exports_enabled': True,
            'alert_retention_days': 14,
            'features': {},
        }
    return _json_safe_value(dict(plan))


def _validate_target_payload(payload: dict[str, Any]) -> dict[str, Any]:
    name = str(payload.get('name', '')).strip()
    target_type = str(payload.get('target_type', '')).strip().lower()
    chain_network = str(payload.get('chain_network', '')).strip()
    if not name or len(name) > 120:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='name is required (max 120 chars).')
    if target_type not in TARGET_TYPES:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='Invalid target_type.')
    if not chain_network or len(chain_network) > 64:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='chain_network is required (max 64 chars).')
    contract_identifier = str(payload.get('contract_identifier', '')).strip() or None
    wallet_address = str(payload.get('wallet_address', '')).strip() or None
    token_address = str(payload.get('token_address', '')).strip() or None
    bridge_endpoint = str(payload.get('bridge_endpoint', '')).strip() or None
    settlement_endpoint = str(payload.get('settlement_endpoint', '')).strip() or None
    asset_label = str(payload.get('asset_label', '')).strip() or None
    chain_id = int(_coerce_number(payload.get('chain_id'), 0) or 0) or None
    if contract_identifier and len(contract_identifier) > 150:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='contract_identifier exceeds 150 chars.')
    if wallet_address and not re.match(r'^0x[a-fA-F0-9]{40}$', wallet_address):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='wallet_address must be an EVM-style address.')
    if token_address and not re.match(r'^0x[a-fA-F0-9]{40}$', token_address):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='token_address must be an EVM-style address.')
    severity_preference = str(payload.get('severity_preference', 'medium')).strip().lower()
    if severity_preference not in {'low', 'medium', 'high', 'critical'}:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='severity_preference must be low/medium/high/critical.')
    monitoring_mode = str(payload.get('monitoring_mode', 'poll')).strip().lower()
    if monitoring_mode not in {'poll', 'stream'}:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='monitoring_mode must be poll/stream.')
    severity_threshold = str(payload.get('severity_threshold', severity_preference)).strip().lower()
    if severity_threshold not in {'low', 'medium', 'high', 'critical'}:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='severity_threshold must be low/medium/high/critical.')
    notification_channels = payload.get('notification_channels')
    channels = [str(item).strip().lower() for item in notification_channels] if isinstance(notification_channels, list) else []
    channels = [item for item in channels if item]
    tags_raw = payload.get('tags')
    tags = [str(item).strip().lower() for item in tags_raw] if isinstance(tags_raw, list) else []
    tags = [item for item in tags if item]
    if any(key in payload for key in ('monitoring_scenario', 'monitoring_demo_scenario', 'monitoring_profile')):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail='monitoring_demo_scenario is deprecated and not accepted in production target APIs.',
        )
    asset_id = str(payload.get('asset_id', '')).strip() or None
    if asset_id:
        try:
            uuid.UUID(asset_id)
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='asset_id must be a UUID.') from exc
    return {
        'name': name,
        'target_type': target_type,
        'chain_network': chain_network,
        'contract_identifier': contract_identifier,
        'wallet_address': wallet_address,
        'asset_type': str(payload.get('asset_type', '')).strip() or None,
        'owner_notes': str(payload.get('owner_notes', '')).strip() or None,
        'chain_id': chain_id,
        'target_metadata': {
            'token_address': token_address,
            'asset_label': asset_label,
            'bridge_endpoint': bridge_endpoint,
            'settlement_endpoint': settlement_endpoint,
        },
        'severity_preference': severity_preference,
        'enabled': bool(payload.get('enabled', True)),
        'monitoring_enabled': bool(payload.get('monitoring_enabled', False)),
        'monitoring_mode': monitoring_mode,
        'monitoring_interval_seconds': max(30, int(_coerce_number(payload.get('monitoring_interval_seconds'), 300))),
        'severity_threshold': severity_threshold,
        'auto_create_alerts': bool(payload.get('auto_create_alerts', True)),
        'auto_create_incidents': bool(payload.get('auto_create_incidents', False)),
        'notification_channels': channels,
        'is_active': bool(payload.get('is_active', True)),
        'tags': tags,
        'asset_id': asset_id,
    }


MONITORED_SYSTEM_RUNTIME_STATUSES = {'provisioning', 'healthy', 'degraded', 'idle', 'failed', 'disabled'}
MONITORED_SYSTEM_FRESHNESS_STATUSES = {'fresh', 'stale', 'unavailable'}
MONITORED_SYSTEM_CONFIDENCE_STATUSES = {'high', 'medium', 'low', 'unavailable'}


def _target_health_payload(
    *,
    enabled: bool,
    monitoring_enabled: bool,
    asset_id: str | None,
    asset_exists: bool,
    monitored_system_id: str | None,
) -> tuple[str, str | None]:
    if not asset_id or not asset_exists:
        return 'broken', 'linked_asset_missing'
    if enabled and monitoring_enabled and not monitored_system_id:
        return 'degraded', 'monitored_system_missing'
    if enabled and monitoring_enabled:
        return 'healthy', None
    if enabled:
        return 'idle', 'monitoring_disabled'
    return 'disabled', None


def ensure_monitored_system_for_target(
    connection: Any,
    *,
    target_id: str,
    workspace_id: str | None = None,
    require_enabled: bool = True,
) -> dict[str, Any]:
    logger.info(
        'target_monitoring_bridge start target_id=%s workspace_filter=%s require_enabled=%s',
        target_id,
        workspace_id,
        require_enabled,
    )
    target = connection.execute(
        '''
        SELECT t.id, t.workspace_id, t.asset_id, t.chain_network, t.target_type, t.enabled, t.monitoring_enabled,
               a.id AS resolved_asset_id,
               aa.id AS any_asset_id,
               aa.workspace_id AS any_asset_workspace_id
        FROM targets t
        LEFT JOIN assets a
            ON a.id = t.asset_id
           AND a.workspace_id = t.workspace_id
           AND a.deleted_at IS NULL
        LEFT JOIN assets aa
            ON aa.id = t.asset_id
           AND aa.deleted_at IS NULL
        WHERE t.id = %s::uuid
          AND t.deleted_at IS NULL
          AND (%s::uuid IS NULL OR t.workspace_id = %s::uuid)
        ''',
        (target_id, workspace_id, workspace_id),
    ).fetchone()
    if target is None:
        result = {'status': 'target_not_found', 'reason': 'target_not_found', 'target_id': target_id}
        logger.info('target_monitoring_bridge result=%s', result)
        return result

    target_workspace_id = str(target['workspace_id'])
    enabled = bool(target.get('enabled'))
    monitoring_enabled = bool(target.get('monitoring_enabled'))
    target_type = normalize_target_type(target.get('target_type'))
    asset_id = str(target.get('asset_id') or '') or None
    resolved_asset_id = str(target.get('resolved_asset_id') or '') or None
    any_asset_id = str(target.get('any_asset_id') or '') or None
    any_asset_workspace_id = str(target.get('any_asset_workspace_id') or '') or None
    if require_enabled and not enabled:
        result = {
            'status': 'target_not_enabled',
            'reason': 'target_not_enabled',
            'workspace_id': target_workspace_id,
            'target_id': target_id,
            'enabled': enabled,
            'monitoring_enabled': monitoring_enabled,
            'asset_id': asset_id,
            'resolved_asset_id': resolved_asset_id,
        }
        logger.info('target_monitoring_bridge result=%s', result)
        return result
    if require_enabled and not monitoring_enabled:
        result = {
            'status': 'target_not_enabled',
            'reason': 'monitoring_disabled',
            'workspace_id': target_workspace_id,
            'target_id': target_id,
            'enabled': enabled,
            'monitoring_enabled': monitoring_enabled,
            'asset_id': asset_id,
            'resolved_asset_id': resolved_asset_id,
        }
        logger.info('target_monitoring_bridge result=%s', result)
        return result

    if not is_monitorable_target_type(target_type):
        connection.execute(
            '''
            UPDATE targets
            SET last_run_status = 'unsupported_target_type',
                watcher_degraded_reason = %s,
                updated_at = NOW()
            WHERE id = %s::uuid
            ''',
            ('unsupported_target_type_for_live_coverage', target_id),
        )
        connection.execute(
            '''
            UPDATE monitored_systems
            SET is_enabled = FALSE,
                runtime_status = 'disabled',
                status = 'paused',
                freshness_status = 'unavailable',
                confidence_status = 'unavailable',
                coverage_reason = %s
            WHERE target_id = %s::uuid
              AND workspace_id = %s::uuid
            ''',
            ('unsupported_target_type_for_live_coverage', target_id, target_workspace_id),
        )
        result = {
            'status': 'unsupported_target_type',
            'reason': 'unsupported_target_type_for_live_coverage',
            'workspace_id': target_workspace_id,
            'target_id': target_id,
            'target_type': target_type,
            'enabled': enabled,
            'monitoring_enabled': monitoring_enabled,
            'asset_id': asset_id,
            'resolved_asset_id': resolved_asset_id,
        }
        logger.info('target_monitoring_bridge result=%s', result)
        return result

    if not asset_id or not resolved_asset_id:
        reason = 'workspace_mismatch' if (asset_id and any_asset_id and any_asset_workspace_id != target_workspace_id) else 'linked_asset_missing'
        connection.execute(
            '''
            UPDATE targets
            SET last_run_status = 'invalid_missing_asset',
                watcher_degraded_reason = %s,
                updated_at = NOW()
            WHERE id = %s::uuid
            ''',
            (reason, target_id),
        )
        connection.execute('DELETE FROM monitored_systems WHERE target_id = %s::uuid AND workspace_id = %s::uuid', (target_id, target_workspace_id))
        result = {
            'status': 'invalid_target',
            'reason': reason,
            'workspace_id': target_workspace_id,
            'target_id': target_id,
            'enabled': enabled,
            'monitoring_enabled': monitoring_enabled,
            'asset_id': asset_id,
            'resolved_asset_id': resolved_asset_id,
        }
        logger.info('target_monitoring_bridge result=%s', result)
        return result

    normalized_chain = (str(target.get('chain_network') or '').strip() or 'unknown')
    row = connection.execute(
        '''
        INSERT INTO monitored_systems (id, workspace_id, asset_id, target_id, chain, is_enabled, runtime_status, status, freshness_status, confidence_status, coverage_reason)
        VALUES (%s, %s, %s::uuid, %s::uuid, %s, TRUE, 'provisioning', 'active', 'unavailable', 'unavailable', 'provisioning_pending_first_heartbeat')
        ON CONFLICT (workspace_id, target_id)
        DO UPDATE SET
            asset_id = EXCLUDED.asset_id,
            chain = EXCLUDED.chain,
            is_enabled = TRUE,
            runtime_status = CASE
                WHEN monitored_systems.runtime_status = 'healthy'
                     AND monitored_systems.last_heartbeat IS NOT NULL
                     AND monitored_systems.last_heartbeat >= NOW() - INTERVAL '10 minutes' THEN 'healthy'
                ELSE 'idle'
            END,
            status = 'active',
            freshness_status = CASE
                WHEN monitored_systems.last_heartbeat IS NOT NULL
                     AND monitored_systems.last_heartbeat >= NOW() - INTERVAL '10 minutes' THEN 'fresh'
                ELSE 'unavailable'
            END,
            confidence_status = CASE
                WHEN monitored_systems.last_heartbeat IS NOT NULL
                     AND monitored_systems.last_heartbeat >= NOW() - INTERVAL '10 minutes' THEN 'medium'
                ELSE 'unavailable'
            END,
            coverage_reason = CASE
                WHEN monitored_systems.last_heartbeat IS NOT NULL
                     AND monitored_systems.last_heartbeat >= NOW() - INTERVAL '10 minutes' THEN NULL
                ELSE 'waiting_for_runtime_telemetry'
            END,
            last_error_text = NULL
        RETURNING id
        ''',
        (str(uuid.uuid4()), target_workspace_id, resolved_asset_id, target_id, normalized_chain),
    ).fetchone()
    connection.execute(
        '''
        UPDATE targets
        SET last_run_status = 'ready',
            watcher_degraded_reason = NULL,
            updated_at = NOW()
        WHERE id = %s::uuid
        ''',
        (target_id,),
    )
    result = {
        'status': 'ok',
        'workspace_id': target_workspace_id,
        'target_id': target_id,
        'asset_id': resolved_asset_id,
        'enabled': enabled,
        'monitoring_enabled': monitoring_enabled,
        'resolved_asset_id': resolved_asset_id,
        'monitored_system_id': str((row or {}).get('id')) if row else None,
    }
    logger.info('target_monitoring_bridge result=%s', result)
    return result


def reconcile_enabled_targets_monitored_systems(connection: Any, *, workspace_id: str | None = None) -> dict[str, Any]:
    rows = connection.execute(
        '''
        SELECT id, target_type, enabled, monitoring_enabled, asset_id
        FROM targets
        WHERE deleted_at IS NULL
          AND (%s::uuid IS NULL OR workspace_id = %s::uuid)
        ORDER BY created_at ASC
        '''
        ,
        (workspace_id, workspace_id),
    ).fetchall()
    created_or_updated = 0
    eligible_targets = 0
    invalid_targets: list[str] = []
    invalid_reasons: dict[str, int] = {}
    invalid_target_details: list[dict[str, str]] = []
    skipped_reasons: dict[str, int] = {}
    skipped_target_details: list[dict[str, str]] = []
    repaired_monitored_system_ids: list[str] = []
    unsupported_repairs = connection.execute(
        f'''
        UPDATE monitored_systems ms
        SET is_enabled = FALSE,
            runtime_status = 'disabled',
            status = 'paused',
            freshness_status = 'unavailable',
            confidence_status = 'unavailable',
            coverage_reason = %s
        FROM targets t
        WHERE t.id = ms.target_id
          AND t.deleted_at IS NULL
          AND ({monitorable_target_types_sql_clause('t.target_type')}) = FALSE
          AND (%s::uuid IS NULL OR ms.workspace_id = %s::uuid)
        RETURNING ms.id
        ''',
        ('unsupported_target_type_for_live_coverage', workspace_id, workspace_id),
    ).fetchall()
    repaired_unsupported_count = len(unsupported_repairs)
    existing_rows = connection.execute(
        '''
        SELECT id, target_id
        FROM monitored_systems
        WHERE (%s::uuid IS NULL OR workspace_id = %s::uuid)
        ''',
        (workspace_id, workspace_id),
    ).fetchall()
    existing_target_to_system_id = {str(item['target_id']): str(item['id']) for item in existing_rows if item.get('target_id') and item.get('id')}
    enabled_target_ids = [str(row.get('id')) for row in rows if bool(row.get('enabled')) and row.get('id')]
    enabled_with_valid_asset_rows = connection.execute(
        '''
        SELECT t.id
        FROM targets t
        JOIN assets a
          ON a.id = t.asset_id
         AND a.workspace_id = t.workspace_id
         AND a.deleted_at IS NULL
        WHERE t.deleted_at IS NULL
          AND t.enabled = TRUE
          AND (%s::uuid IS NULL OR t.workspace_id = %s::uuid)
        ''',
        (workspace_id, workspace_id),
    ).fetchall()
    enabled_with_valid_asset_target_ids = [str(item.get('id')) for item in enabled_with_valid_asset_rows if item.get('id')]
    logger.info(
        'target_monitoring_reconcile_data_path workspace_id=%s step=before_repair targets=%s enabled_target_ids=%s enabled_targets_with_valid_asset_ids=%s monitored_system_rows_before=%s monitored_system_row_ids_before=%s monitored_system_rows_before_detail=%s',
        workspace_id,
        [str(row.get('id')) for row in rows if row.get('id')],
        enabled_target_ids,
        enabled_with_valid_asset_target_ids,
        len(existing_rows),
        [str(item.get('id')) for item in existing_rows if item.get('id')],
        [
            {
                'id': str(item.get('id') or ''),
                'target_id': str(item.get('target_id') or ''),
            }
            for item in existing_rows
        ],
    )
    enabled_valid_targets_found = 0
    disabled_or_invalid_targets_found = 0
    for row in rows:
        target_id = str(row['id'])
        target_enabled = bool(row.get('enabled'))
        target_monitoring_enabled = bool(row.get('monitoring_enabled'))
        target_type = normalize_target_type(row.get('target_type'))
        target_has_asset_id = bool(row.get('asset_id'))
        if not target_enabled:
            disabled_or_invalid_targets_found += 1
            skipped_reasons['target_not_enabled'] = skipped_reasons.get('target_not_enabled', 0) + 1
            skipped_target_details.append({
                'target_id': target_id,
                'code': 'target_not_enabled',
                'reason': 'Target is disabled and cannot be reconciled.',
            })
            logger.info(
                'target_monitoring_reconcile target_id=%s workspace_id=%s enabled=%s monitoring_enabled=%s asset_id=%s resolved_asset_id=%s status=%s reason=%s monitored_system_id=%s',
                target_id,
                workspace_id,
                target_enabled,
                target_monitoring_enabled,
                row.get('asset_id'),
                None,
                'target_not_enabled',
                'target_not_enabled',
                None,
            )
            continue
        if not is_monitorable_target_type(target_type):
            disabled_or_invalid_targets_found += 1
            logger.info(
                'target_monitoring_reconcile target_id=%s workspace_id=%s enabled=%s monitoring_enabled=%s target_type=%s asset_id=%s resolved_asset_id=%s status=%s reason=%s monitored_system_id=%s',
                target_id,
                workspace_id,
                target_enabled,
                target_monitoring_enabled,
                target_type,
                row.get('asset_id'),
                None,
                'unsupported_target_type',
                'unsupported_target_type_for_live_coverage',
                None,
            )
            result = ensure_monitored_system_for_target(
                connection,
                target_id=str(row['id']),
                workspace_id=workspace_id,
                require_enabled=False,
            )
            skipped_reason = str(result.get('reason') or 'unsupported_target_type_for_live_coverage')
            skipped_reasons[skipped_reason] = skipped_reasons.get(skipped_reason, 0) + 1
            skipped_target_details.append({
                'target_id': target_id,
                'code': skipped_reason,
                'reason': 'Target type is not supported for live monitoring coverage.',
            })
            continue
        if target_enabled and target_has_asset_id and not target_monitoring_enabled:
            connection.execute(
                '''
                UPDATE targets
                SET monitoring_enabled = TRUE,
                    updated_at = NOW()
                WHERE id = %s::uuid
                ''',
                (target_id,),
            )
            target_monitoring_enabled = True
        result = ensure_monitored_system_for_target(
            connection,
            target_id=str(row['id']),
            workspace_id=workspace_id,
            require_enabled=False,
        )
        if result.get('status') == 'ok':
            eligible_targets += 1
            enabled_valid_targets_found += 1
            verified_row = connection.execute(
                '''
                SELECT id
                FROM monitored_systems
                WHERE workspace_id = %s::uuid
                  AND target_id = %s::uuid
                ''',
                (str(result.get('workspace_id') or workspace_id), str(result.get('target_id') or row.get('id'))),
            ).fetchone()
            if verified_row:
                created_or_updated += 1
                repaired_monitored_system_ids.append(str(verified_row['id']))
            else:
                skipped_reasons['post_upsert_not_visible'] = skipped_reasons.get('post_upsert_not_visible', 0) + 1
                skipped_target_details.append({
                    'target_id': target_id,
                    'code': 'post_upsert_not_visible',
                    'reason': 'Target reconcile completed, but the monitored system row was not visible after upsert.',
                })
        elif result.get('status') == 'invalid_target':
            disabled_or_invalid_targets_found += 1
            invalid_targets.append(str(result.get('target_id')))
            invalid_reason = str(result.get('reason') or 'invalid_target')
            invalid_reasons[invalid_reason] = invalid_reasons.get(invalid_reason, 0) + 1
            invalid_target_details.append({
                'target_id': str(result.get('target_id') or target_id),
                'code': invalid_reason,
                'reason': invalid_reason.replace('_', ' '),
            })
        else:
            if not target_enabled or not target_monitoring_enabled or not target_has_asset_id:
                disabled_or_invalid_targets_found += 1
            skipped_reason = str(result.get('reason') or 'skipped')
            skipped_reasons[skipped_reason] = skipped_reasons.get(skipped_reason, 0) + 1
            skipped_target_details.append({
                'target_id': str(result.get('target_id') or target_id),
                'code': skipped_reason,
                'reason': skipped_reason.replace('_', ' '),
            })
        logger.info(
            'target_monitoring_reconcile target_id=%s workspace_id=%s enabled=%s monitoring_enabled=%s asset_id=%s resolved_asset_id=%s status=%s reason=%s monitored_system_id=%s',
            result.get('target_id') or row.get('id'),
            result.get('workspace_id') or workspace_id,
            result.get('enabled'),
            result.get('monitoring_enabled'),
            result.get('asset_id'),
            result.get('resolved_asset_id'),
            result.get('status'),
            result.get('reason'),
            result.get('monitored_system_id'),
        )
    refreshed_rows = connection.execute(
        '''
        SELECT id, target_id
        FROM monitored_systems
        WHERE (%s::uuid IS NULL OR workspace_id = %s::uuid)
        ''',
        (workspace_id, workspace_id),
    ).fetchall()
    logger.info(
        'target_monitoring_reconcile_data_path workspace_id=%s step=after_repair monitored_system_rows_after=%s monitored_system_row_ids_after=%s monitored_system_rows_after_detail=%s repaired_monitored_system_ids=%s',
        workspace_id,
        len(refreshed_rows),
        [str(item.get('id')) for item in refreshed_rows if item.get('id')],
        [
            {
                'id': str(item.get('id') or ''),
                'target_id': str(item.get('target_id') or ''),
            }
            for item in refreshed_rows
        ],
        repaired_monitored_system_ids,
    )
    refreshed_target_to_system_id = {str(item['target_id']): str(item['id']) for item in refreshed_rows if item.get('target_id') and item.get('id')}
    created_monitored_systems = max(0, len(set(refreshed_target_to_system_id.values()) - set(existing_target_to_system_id.values())))
    preserved_monitored_systems = sum(1 for target_id, system_id in existing_target_to_system_id.items() if refreshed_target_to_system_id.get(target_id) == system_id)
    removed_monitored_systems = max(0, len(set(existing_target_to_system_id.values()) - set(refreshed_target_to_system_id.values())))
    final_workspace_monitored_system_count = len(refreshed_target_to_system_id)
    logger.info(
        'target_monitoring_reconcile_summary workspace_id=%s targets_scanned=%s enabled_valid_targets_found=%s disabled_or_invalid_targets_found=%s monitored_systems_created=%s monitored_systems_preserved=%s monitored_systems_removed=%s final_workspace_monitored_system_count=%s invalid_reasons=%s skipped_reasons=%s',
        workspace_id,
        len(rows),
        enabled_valid_targets_found,
        disabled_or_invalid_targets_found,
        created_monitored_systems,
        preserved_monitored_systems,
        removed_monitored_systems,
        final_workspace_monitored_system_count,
        invalid_reasons,
        skipped_reasons,
    )
    return _normalize_reconcile_result({
        'enabled_targets_scanned': eligible_targets + len(invalid_targets),
        'targets_scanned': len(rows),
        'eligible_targets': eligible_targets,
        'created_or_updated': created_or_updated,
        'created_monitored_systems': created_monitored_systems,
        'preserved_monitored_systems': preserved_monitored_systems,
        'removed_monitored_systems': removed_monitored_systems,
        'final_workspace_monitored_system_count': final_workspace_monitored_system_count,
        'enabled_valid_targets_found': enabled_valid_targets_found,
        'disabled_or_invalid_targets_found': disabled_or_invalid_targets_found,
        'invalid_targets': invalid_targets,
        'invalid_reasons': invalid_reasons,
        'invalid_target_details': invalid_target_details,
        'skipped_reasons': skipped_reasons,
        'skipped_target_details': skipped_target_details,
        'repaired_monitored_system_ids': repaired_monitored_system_ids,
        'repaired_unsupported_monitored_systems': repaired_unsupported_count,
        'workspace_id': workspace_id,
    })


def _normalize_reconcile_result(result: dict[str, Any]) -> dict[str, Any]:
    invalid_target_details = [
        {
            'target_id': str(item.get('target_id') or ''),
            'code': str(item.get('code') or 'invalid_target'),
            'reason': str(item.get('reason') or ''),
        }
        for item in (result.get('invalid_target_details') or [])
        if str(item.get('target_id') or '').strip()
    ]
    skipped_target_details = [
        {
            'target_id': str(item.get('target_id') or ''),
            'code': str(item.get('code') or 'skipped'),
            'reason': str(item.get('reason') or ''),
        }
        for item in (result.get('skipped_target_details') or [])
        if str(item.get('target_id') or '').strip()
    ]
    return {
        'enabled_targets_scanned': int(result.get('enabled_targets_scanned', 0) or 0),
        'targets_scanned': int(result.get('targets_scanned', 0) or 0),
        'eligible_targets': int(result.get('eligible_targets', 0) or 0),
        'created_or_updated': int(result.get('created_or_updated', 0) or 0),
        'created_monitored_systems': int(result.get('created_monitored_systems', 0) or 0),
        'preserved_monitored_systems': int(result.get('preserved_monitored_systems', 0) or 0),
        'removed_monitored_systems': int(result.get('removed_monitored_systems', 0) or 0),
        'final_workspace_monitored_system_count': int(result.get('final_workspace_monitored_system_count', 0) or 0),
        'enabled_valid_targets_found': int(result.get('enabled_valid_targets_found', 0) or 0),
        'disabled_or_invalid_targets_found': int(result.get('disabled_or_invalid_targets_found', 0) or 0),
        'invalid_targets': [str(value) for value in (result.get('invalid_targets') or []) if str(value).strip()],
        'invalid_reasons': {str(key): int(value) for key, value in dict(result.get('invalid_reasons') or {}).items()},
        'invalid_target_details': invalid_target_details,
        'skipped_reasons': {str(key): int(value) for key, value in dict(result.get('skipped_reasons') or {}).items()},
        'skipped_target_details': skipped_target_details,
        'repaired_monitored_system_ids': [str(value) for value in (result.get('repaired_monitored_system_ids') or []) if str(value).strip()],
        'repaired_unsupported_monitored_systems': int(result.get('repaired_unsupported_monitored_systems', 0) or 0),
        'workspace_id': result.get('workspace_id'),
    }


def workspace_monitoring_debug_snapshot(connection: Any, *, workspace_id: str) -> dict[str, Any]:
    all_targets = [
        dict(row)
        for row in connection.execute(
            '''
            SELECT id, workspace_id, asset_id, enabled, monitoring_enabled, deleted_at
            FROM targets
            WHERE workspace_id = %s
              AND deleted_at IS NULL
            ORDER BY created_at ASC
            ''',
            (workspace_id,),
        ).fetchall()
    ]
    enabled_targets = [row for row in all_targets if bool(row.get('enabled'))]
    valid_linked_targets = [
        dict(row)
        for row in connection.execute(
            '''
            SELECT t.id, t.workspace_id, t.asset_id, t.enabled, t.monitoring_enabled
            FROM targets t
            JOIN assets a
              ON a.id = t.asset_id
             AND a.workspace_id = t.workspace_id
             AND a.deleted_at IS NULL
            WHERE t.workspace_id = %s
              AND t.deleted_at IS NULL
              AND t.enabled = TRUE
            ORDER BY t.created_at ASC
            ''',
            (workspace_id,),
        ).fetchall()
    ]
    monitored_rows = [
        dict(row)
        for row in connection.execute(
            '''
            SELECT id, workspace_id, target_id, asset_id, is_enabled, runtime_status, status, freshness_status, confidence_status, last_heartbeat, last_event_at, last_error_text, coverage_reason
            FROM monitored_systems
            WHERE workspace_id = %s
            ORDER BY created_at ASC
            ''',
            (workspace_id,),
        ).fetchall()
    ]
    enabled_monitored_rows = [row for row in monitored_rows if monitored_system_row_enabled(row)]
    protected_assets = len({str(row.get('asset_id')) for row in enabled_monitored_rows if row.get('asset_id')})
    return {
        'workspace_id': workspace_id,
        'target_count': len(all_targets),
        'targets': all_targets,
        'enabled_targets': enabled_targets,
        'enabled_target_count': len(enabled_targets),
        'enabled_valid_target_count': len(valid_linked_targets),
        'valid_linked_asset_targets': valid_linked_targets,
        'monitored_systems_count': len(monitored_rows),
        'monitored_system_rows': monitored_rows,
        'enabled_monitored_system_rows': enabled_monitored_rows,
        'enabled_monitored_system_count': len(enabled_monitored_rows),
        'protected_asset_count': protected_assets,
    }


def monitored_system_row_enabled(row: dict[str, Any] | None) -> bool:
    value = (row or {}).get('is_enabled')
    if value is None:
        return True
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {'', '0', 'false', 'f', 'off', 'disabled', 'no'}:
            return False
        return True
    if isinstance(value, (int, float)):
        return int(value) != 0
    return bool(value)


def workspace_monitoring_status_inputs(connection: Any, *, workspace_id: str) -> dict[str, Any]:
    snapshot = workspace_monitoring_debug_snapshot(connection, workspace_id=workspace_id)
    monitored_rows = snapshot.get('monitored_system_rows') if isinstance(snapshot.get('monitored_system_rows'), list) else []
    monitored_enabled_rows = [row for row in monitored_rows if monitored_system_row_enabled(row)]
    status_inputs = {
        'workspace_id': workspace_id,
        'total_targets': int(snapshot.get('target_count') or 0),
        'enabled_valid_targets': int(snapshot.get('enabled_valid_target_count') or 0),
        'monitored_systems': int(snapshot.get('monitored_systems_count') or 0),
        'protected_assets': len({str((row or {}).get('asset_id') or '') for row in monitored_enabled_rows if (row or {}).get('asset_id')}),
        'enabled_monitored_systems': len(monitored_enabled_rows),
    }
    logger.info('workspace_monitoring_status_inputs workspace_id=%s status_inputs=%s', workspace_id, status_inputs)
    return status_inputs


def get_workspace_monitoring_debug(request: Request) -> dict[str, Any]:
    require_live_mode()
    with pg_connection() as connection:
        ensure_pilot_schema(connection)
        _, workspace_context, _ = resolve_workspace_context_for_request(connection, request)
        workspace_id = workspace_context['workspace_id']
        snapshot = workspace_monitoring_debug_snapshot(connection, workspace_id=workspace_id)
        status_inputs = workspace_monitoring_status_inputs(connection, workspace_id=workspace_id)
        listed_rows = list_workspace_monitored_system_rows(connection, workspace_id)
        listed_enabled_rows = [row for row in listed_rows if monitored_system_row_enabled(row)]
        listed_protected_assets = len({str((row or {}).get('asset_id') or '') for row in listed_enabled_rows if (row or {}).get('asset_id')})
        return {
            'workspace': workspace_context['workspace'],
            'workspace_resolution': {
                'resolved_workspace_id': workspace_id,
                'workspace_header': (request.headers.get('x-workspace-id') or '').strip() or None,
            },
            'debug': _json_safe_value(snapshot),
            'status_inputs': _json_safe_value(status_inputs),
            'list_route_snapshot': _json_safe_value(
                {
                    'resolved_workspace_id': workspace_id,
                    'monitored_system_rows': listed_rows,
                    'monitored_systems_count': len(listed_rows),
                    'enabled_monitored_systems_count': len(listed_enabled_rows),
                    'protected_asset_count': listed_protected_assets,
                }
            ),
        }


def resolve_workspace_context_for_request(connection: psycopg.Connection, request: Request) -> tuple[dict[str, Any], dict[str, Any], bool]:
    user = authenticate_with_connection(connection, request)
    header_workspace_id = request.headers.get('x-workspace-id')
    workspace_context = resolve_workspace(connection, user['id'], header_workspace_id)
    return user, workspace_context, bool((header_workspace_id or '').strip())


def list_workspace_monitored_system_rows(connection: psycopg.Connection, workspace_id: str) -> list[dict[str, Any]]:
    try:
        rows = connection.execute(
            '''
            SELECT ms.id, ms.workspace_id, ms.asset_id, ms.target_id, ms.chain, ms.is_enabled, ms.runtime_status, ms.status, ms.last_heartbeat, ms.last_event_at, ms.last_coverage_telemetry_at, ms.last_error_text, ms.coverage_reason,
                   ms.freshness_status, ms.confidence_status, ms.created_at,
                   COALESCE(t.monitoring_interval_seconds, 30) AS monitoring_interval_seconds, a.name AS asset_name, t.name AS target_name
            FROM monitored_systems ms
            LEFT JOIN assets a ON a.id = ms.asset_id AND a.workspace_id = ms.workspace_id AND a.deleted_at IS NULL
            LEFT JOIN targets t ON t.id = ms.target_id AND t.workspace_id = ms.workspace_id
            WHERE ms.workspace_id = %s
            ORDER BY ms.created_at DESC
            ''',
            (workspace_id,),
        ).fetchall()
    except Exception as exc:
        if 'last_coverage_telemetry_at' not in str(exc):
            raise
        logger.warning(
            'list_workspace_monitored_system_rows_legacy_schema_fallback workspace_id=%s error_type=%s',
            workspace_id,
            type(exc).__name__,
        )
        rows = connection.execute(
            '''
            SELECT ms.id, ms.workspace_id, ms.asset_id, ms.target_id, ms.chain, ms.is_enabled, ms.runtime_status, ms.status, ms.last_heartbeat, ms.last_event_at, NULL::timestamptz AS last_coverage_telemetry_at, ms.last_error_text, ms.coverage_reason,
                   ms.freshness_status, ms.confidence_status, ms.created_at,
                   COALESCE(t.monitoring_interval_seconds, 30) AS monitoring_interval_seconds, a.name AS asset_name, t.name AS target_name
            FROM monitored_systems ms
            LEFT JOIN assets a ON a.id = ms.asset_id AND a.workspace_id = ms.workspace_id AND a.deleted_at IS NULL
            LEFT JOIN targets t ON t.id = ms.target_id AND t.workspace_id = ms.workspace_id
            WHERE ms.workspace_id = %s
            ORDER BY ms.created_at DESC
            ''',
            (workspace_id,),
        ).fetchall()
    return [dict(row) for row in rows]


def list_monitored_systems(request: Request) -> dict[str, Any]:
    logger.info('monitoring_systems_list step=start')
    stage = 'require_live_mode'
    logger.info('monitoring_systems_list step=%s', stage)
    try:
        require_live_mode()
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception('monitoring_systems_list_failed stage=%s', stage)
        raise HTTPException(status_code=500, detail='Unable to load monitored systems.') from exc

    with pg_connection() as connection:
        stage = 'ensure_schema'
        logger.info('monitoring_systems_list step=%s', stage)
        ensure_pilot_schema(connection)
        stage = 'workspace_resolve'
        logger.info('monitoring_systems_list step=%s', stage)
        _, workspace_context, _ = resolve_workspace_context_for_request(connection, request)
        workspace_id = workspace_context['workspace_id']
        logger.info('monitoring_systems_list step=workspace_resolved workspace_id=%s', workspace_id)
        stage = 'list_rows'
        logger.info('monitoring_systems_list step=%s workspace_id=%s', stage, workspace_id)
        rows = list_workspace_monitored_system_rows(connection, workspace_id)
        enabled_rows = [row for row in rows if monitored_system_row_enabled(row)]
        protected_assets = len({str((row or {}).get('asset_id') or '') for row in enabled_rows if (row or {}).get('asset_id')})
        logger.info(
            'monitoring_systems_list step=rows_loaded workspace_id=%s count=%s row_ids=%s enabled_count=%s protected_assets=%s rows=%s',
            workspace_id,
            len(rows),
            [str((row or {}).get('id') or '') for row in rows if (row or {}).get('id')],
            len(enabled_rows),
            protected_assets,
            rows,
        )
        return {'systems': [_json_safe_value(row) for row in rows], 'workspace': workspace_context['workspace']}


def list_monitoring_runs(request: Request, *, limit: int = 20) -> dict[str, Any]:
    require_live_mode()
    with pg_connection() as connection:
        ensure_pilot_schema(connection)
        _, workspace_context, _ = resolve_workspace_context_for_request(connection, request)
        workspace_id = workspace_context['workspace_id']
        max_limit = max(1, min(int(limit or 20), 100))
        rows = connection.execute(
            '''
            SELECT id,
                   workspace_id,
                   started_at,
                   completed_at,
                   status,
                   trigger_type,
                   systems_checked_count,
                   assets_checked_count,
                   detections_created_count,
                   alerts_created_count,
                   telemetry_records_seen_count,
                   notes
            FROM monitoring_runs
            WHERE workspace_id = %s::uuid
            ORDER BY started_at DESC
            LIMIT %s
            ''',
            (workspace_id, max_limit),
        ).fetchall()
        return {'runs': [_json_safe_value(dict(row)) for row in rows], 'workspace': workspace_context['workspace']}


def get_monitoring_run(run_id: str, request: Request) -> dict[str, Any]:
    require_live_mode()
    with pg_connection() as connection:
        ensure_pilot_schema(connection)
        _, workspace_context, _ = resolve_workspace_context_for_request(connection, request)
        workspace_id = workspace_context['workspace_id']
        row = connection.execute(
            '''
            SELECT id,
                   workspace_id,
                   started_at,
                   completed_at,
                   status,
                   trigger_type,
                   systems_checked_count,
                   assets_checked_count,
                   detections_created_count,
                   alerts_created_count,
                   telemetry_records_seen_count,
                   notes
            FROM monitoring_runs
            WHERE id = %s::uuid
              AND workspace_id = %s::uuid
            LIMIT 1
            ''',
            (run_id, workspace_id),
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Monitoring run not found.')
        return {'run': _json_safe_value(dict(row)), 'workspace': workspace_context['workspace']}


def create_monitored_system(payload: dict[str, Any], request: Request) -> dict[str, Any]:
    require_live_mode()
    asset_id = str(payload.get('asset_id') or '').strip()
    target_id = str(payload.get('target_id') or '').strip()
    if not asset_id or not target_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='asset_id and target_id are required.')
    with pg_connection() as connection:
        ensure_pilot_schema(connection)
        user, workspace_context = _require_workspace_admin(connection, request)
        workspace_id = workspace_context['workspace_id']
        asset = connection.execute(
            'SELECT id, chain_network FROM assets WHERE id = %s::uuid AND workspace_id = %s AND deleted_at IS NULL',
            (asset_id, workspace_id),
        ).fetchone()
        if asset is None:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='asset_id must reference an asset in this workspace.')
        target = connection.execute(
            'SELECT id, asset_id, chain_network FROM targets WHERE id = %s::uuid AND workspace_id = %s AND deleted_at IS NULL',
            (target_id, workspace_id),
        ).fetchone()
        if target is None:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='target_id must reference a target in this workspace.')
        if str(target.get('asset_id') or '') != asset_id:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='target.asset_id must match asset_id.')
        result = ensure_monitored_system_for_target(connection, target_id=target_id, workspace_id=workspace_id, require_enabled=False)
        if result.get('status') != 'ok':
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='Target cannot be bridged to monitoring until its linked asset is valid.')
        monitored_system_id = str(result.get('monitored_system_id') or '')
        log_audit(connection, action='monitored_system.create', entity_type='monitored_system', entity_id=monitored_system_id, request=request, user_id=user['id'], workspace_id=workspace_id, metadata={'asset_id': asset_id, 'target_id': target_id})
        connection.commit()
        row = connection.execute(
            '''
            SELECT ms.id, ms.workspace_id, ms.asset_id, ms.target_id, ms.chain, ms.is_enabled, ms.runtime_status, ms.status, ms.last_heartbeat, ms.last_event_at, ms.last_error_text, ms.coverage_reason,
                   ms.freshness_status, ms.confidence_status, ms.created_at,
                   a.name AS asset_name, t.name AS target_name
            FROM monitored_systems ms
            JOIN assets a ON a.id = ms.asset_id
            JOIN targets t ON t.id = ms.target_id
            WHERE ms.id = %s::uuid
            ''',
            (monitored_system_id,),
        ).fetchone()
        return {'system': _json_safe_value(dict(row))}


def patch_monitored_system(system_id: str, payload: dict[str, Any], request: Request) -> dict[str, Any]:
    require_live_mode()
    if 'runtime_status' not in payload and 'enabled' not in payload:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='Provide runtime_status or enabled.')
    with pg_connection() as connection:
        ensure_pilot_schema(connection)
        user, workspace_context = _require_workspace_admin(connection, request)
        row = connection.execute(
            'SELECT id, is_enabled, runtime_status, freshness_status, confidence_status FROM monitored_systems WHERE id = %s::uuid AND workspace_id = %s',
            (system_id, workspace_context['workspace_id']),
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Monitored system not found.')
        is_enabled_value = bool(row.get('is_enabled'))
        runtime_status_value = str(row.get('runtime_status') or 'disabled')
        freshness_status_value = str(row.get('freshness_status') or 'unavailable')
        confidence_status_value = str(row.get('confidence_status') or 'unavailable')
        if 'enabled' in payload:
            if not isinstance(payload.get('enabled'), bool):
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='enabled must be boolean.')
            is_enabled_value = bool(payload.get('enabled'))
            if is_enabled_value and runtime_status_value == 'disabled':
                runtime_status_value = 'provisioning'
            if not is_enabled_value:
                runtime_status_value = 'disabled'
                freshness_status_value = 'unavailable'
                confidence_status_value = 'unavailable'
        if 'runtime_status' in payload:
            runtime_status_value = str(payload.get('runtime_status') or '').strip().lower()
        if 'freshness_status' in payload:
            freshness_status_value = str(payload.get('freshness_status') or '').strip().lower()
        if 'confidence_status' in payload:
            confidence_status_value = str(payload.get('confidence_status') or '').strip().lower()
        if runtime_status_value not in MONITORED_SYSTEM_RUNTIME_STATUSES:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='runtime_status must be provisioning/healthy/degraded/idle/failed/disabled.')
        if freshness_status_value not in MONITORED_SYSTEM_FRESHNESS_STATUSES:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='freshness_status must be fresh/stale/unavailable.')
        if confidence_status_value not in MONITORED_SYSTEM_CONFIDENCE_STATUSES:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='confidence_status must be high/medium/low/unavailable.')
        status_value = 'active' if is_enabled_value and runtime_status_value in {'provisioning', 'healthy', 'idle', 'degraded'} else ('error' if runtime_status_value == 'failed' else 'paused')
        connection.execute(
            """
            UPDATE monitored_systems
            SET is_enabled = %s,
                runtime_status = %s,
                status = %s,
                freshness_status = %s,
                confidence_status = %s,
                coverage_reason = CASE
                    WHEN %s = 'healthy' THEN NULL
                    WHEN %s = 'disabled' THEN 'monitoring_disabled'
                    ELSE coverage_reason
                END,
                last_heartbeat = CASE WHEN %s = 'healthy' THEN NOW() ELSE last_heartbeat END
            WHERE id = %s::uuid
            """,
            (is_enabled_value, runtime_status_value, status_value, freshness_status_value, confidence_status_value, runtime_status_value, runtime_status_value, runtime_status_value, system_id),
        )
        log_audit(connection, action='monitored_system.update', entity_type='monitored_system', entity_id=system_id, request=request, user_id=user['id'], workspace_id=workspace_context['workspace_id'], metadata={'enabled': is_enabled_value, 'runtime_status': runtime_status_value})
        connection.commit()
        updated = connection.execute(
            'SELECT id, workspace_id, asset_id, target_id, chain, is_enabled, runtime_status, status, freshness_status, confidence_status, last_heartbeat, last_event_at, last_error_text, coverage_reason, created_at FROM monitored_systems WHERE id = %s::uuid',
            (system_id,),
        ).fetchone()
        return {'system': _json_safe_value(dict(updated))}


def delete_monitored_system(system_id: str, request: Request) -> dict[str, Any]:
    require_live_mode()
    with pg_connection() as connection:
        ensure_pilot_schema(connection)
        user, workspace_context = _require_workspace_admin(connection, request)
        row = connection.execute(
            'SELECT id, target_id FROM monitored_systems WHERE id = %s::uuid AND workspace_id = %s',
            (system_id, workspace_context['workspace_id']),
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Monitored system not found.')
        connection.execute('DELETE FROM monitored_systems WHERE id = %s::uuid', (system_id,))
        connection.execute('UPDATE targets SET monitoring_enabled = FALSE, updated_by_user_id = %s, updated_at = NOW() WHERE id = %s::uuid', (user['id'], row['target_id']))
        log_audit(connection, action='monitored_system.delete', entity_type='monitored_system', entity_id=system_id, request=request, user_id=user['id'], workspace_id=workspace_context['workspace_id'], metadata={})
        connection.commit()
        return {'deleted': True, 'id': system_id}


def _derive_asset_verification(*, identifier: str, chain_network: str) -> dict[str, Any]:
    normalized_identifier = identifier.strip().lower()
    summary: dict[str, Any] = {
        'normalized_identifier': normalized_identifier,
        'chain_network': chain_network,
        'reachable': False,
        'recent_activity': 'unknown',
        'confidence': 0,
        'status_detail': 'verification_pending',
    }
    if re.fullmatch(r'^0x[a-f0-9]{40}$', normalized_identifier):
        summary['normalized_identifier'] = normalized_identifier
    if not chain_network.lower().startswith(('ethereum', 'base', 'arbitrum')):
        return {
            'normalized_identifier': summary['normalized_identifier'],
            'verification_status': 'pending',
            'verification_summary': summary,
        }
    rpc_url = _rpc_url_for_chain(chain_network)
    if not rpc_url:
        summary['status_detail'] = 'provider_unavailable'
        return {
            'normalized_identifier': summary['normalized_identifier'],
            'verification_status': 'unavailable',
            'verification_summary': summary,
        }
    try:
        block_payload = {'jsonrpc': '2.0', 'id': 1, 'method': 'eth_blockNumber', 'params': []}
        block_request = UrlRequest(rpc_url, data=_json_dumps(block_payload).encode('utf-8'), headers={'Content-Type': 'application/json'})
        with urlopen(block_request, timeout=10) as response:  # nosec B310
            block_body = json.loads(response.read().decode('utf-8') or '{}')
        latest_block_hex = str(block_body.get('result') or '0x0')
        code_payload = {'jsonrpc': '2.0', 'id': 1, 'method': 'eth_getCode', 'params': [summary['normalized_identifier'], 'latest']}
        code_request = UrlRequest(rpc_url, data=_json_dumps(code_payload).encode('utf-8'), headers={'Content-Type': 'application/json'})
        with urlopen(code_request, timeout=10) as response:  # nosec B310
            code_body = json.loads(response.read().decode('utf-8') or '{}')
        tx_count_payload = {'jsonrpc': '2.0', 'id': 1, 'method': 'eth_getTransactionCount', 'params': [summary['normalized_identifier'], 'latest']}
        tx_request = UrlRequest(rpc_url, data=_json_dumps(tx_count_payload).encode('utf-8'), headers={'Content-Type': 'application/json'})
        with urlopen(tx_request, timeout=10) as response:  # nosec B310
            tx_body = json.loads(response.read().decode('utf-8') or '{}')
        code = str(code_body.get('result') or '0x')
        tx_count = int(str(tx_body.get('result') or '0x0'), 16)
        latest_block = int(latest_block_hex, 16)
        reachable = bool(code and code != '0x') or tx_count >= 0
        summary.update({
            'reachable': reachable,
            'is_contract': code not in {'', '0x'},
            'tx_count': tx_count,
            'latest_block': latest_block,
            'recent_activity': 'observed' if tx_count > 0 else 'none_observed',
            'confidence': 85 if reachable else 40,
            'status_detail': 'verified',
        })
        return {
            'normalized_identifier': summary['normalized_identifier'],
            'verification_status': 'verified' if reachable else 'needs_attention',
            'verification_summary': summary,
        }
    except Exception as exc:
        summary['status_detail'] = f'verification_unavailable:{exc.__class__.__name__}'
        return {
            'normalized_identifier': summary['normalized_identifier'],
            'verification_status': 'unavailable',
            'verification_summary': summary,
        }


def _validate_asset_payload(payload: dict[str, Any]) -> dict[str, Any]:
    def validation_error(*, field: str, message: str) -> None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                'message': message,
                'field_errors': {
                    field: message,
                },
            },
        )

    name = str(payload.get('name', '')).strip()
    description = str(payload.get('description', '')).strip() or None
    asset_type = str(payload.get('asset_type', '')).strip().lower()
    chain_network = str(payload.get('chain_network', '')).strip()
    identifier = str(payload.get('identifier', '')).strip()
    asset_class = str(payload.get('asset_class', '')).strip().lower() or None
    risk_tier = str(payload.get('risk_tier', 'medium')).strip().lower()
    owner_team = str(payload.get('owner_team', '')).strip() or None
    notes = str(payload.get('notes', '')).strip() or None
    if not name or len(name) > 120:
        validation_error(field='name', message='Asset name is required (max 120 chars).')
    if asset_type not in ASSET_TYPES:
        validation_error(field='asset_type', message='Asset type is invalid.')
    if not chain_network or len(chain_network) > 64:
        validation_error(field='chain_network', message='Chain / network is required (max 64 chars).')
    if not identifier or len(identifier) > 180:
        validation_error(field='identifier', message='Wallet address / identifier is required (max 180 chars).')
    if risk_tier not in {'low', 'medium', 'high', 'critical'}:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='risk_tier must be low/medium/high/critical.')
    if asset_class is not None and asset_class not in ASSET_CLASSES:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='asset_class must be treasury_token/bond_token/money_market_token/rwa_other.')
    token_contract_address = str(payload.get('token_contract_address', '')).strip().lower() or None
    if token_contract_address and not re.match(r'^0x[a-f0-9]{40}$', token_contract_address):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='token_contract_address must be an EVM-style address.')
    baseline_source = str(payload.get('baseline_source', 'manual')).strip().lower()
    if baseline_source not in BASELINE_SOURCES:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='baseline_source must be observed/manual/imported.')
    baseline_status = str(payload.get('baseline_status', 'missing')).strip().lower()
    if baseline_status not in {'missing', 'configured', 'observed', 'stale'}:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='baseline_status must be missing/configured/observed/stale.')
    baseline_confidence = max(0, min(100, int(_coerce_number(payload.get('baseline_confidence'), 0))))
    baseline_coverage = max(0, min(100, int(_coerce_number(payload.get('baseline_coverage'), 0))))
    expected_oracle_freshness_seconds = max(0, int(_coerce_number(payload.get('expected_oracle_freshness_seconds'), 0)))
    expected_oracle_update_cadence_seconds = max(0, int(_coerce_number(payload.get('expected_oracle_update_cadence_seconds'), 0)))
    tags_raw = payload.get('tags')
    tags = [str(item).strip().lower() for item in tags_raw] if isinstance(tags_raw, list) else []
    tags = [item for item in tags if item][:25]
    return {
        'name': name,
        'description': description,
        'asset_type': asset_type,
        'chain_network': chain_network,
        'identifier': identifier,
        'normalized_identifier': identifier.strip().lower(),
        'asset_class': asset_class,
        'risk_tier': risk_tier,
        'owner_team': owner_team,
        'notes': notes,
        'enabled': bool(payload.get('enabled', True)),
        'tags': tags,
        'issuer_name': str(payload.get('issuer_name', '')).strip() or None,
        'asset_symbol': str(payload.get('asset_symbol', '')).strip() or None,
        'asset_identifier': str(payload.get('asset_identifier', '')).strip() or None,
        'token_contract_address': token_contract_address,
        'custody_wallets': _normalize_address_list(payload.get('custody_wallets'), field_name='custody_wallets'),
        'treasury_ops_wallets': _normalize_address_list(payload.get('treasury_ops_wallets'), field_name='treasury_ops_wallets'),
        'oracle_sources': [str(item).strip() for item in (payload.get('oracle_sources') or []) if str(item).strip()][:50] if isinstance(payload.get('oracle_sources'), list) else [],
        'venue_labels': [str(item).strip() for item in (payload.get('venue_labels') or []) if str(item).strip()][:50] if isinstance(payload.get('venue_labels'), list) else [],
        'expected_counterparties': _normalize_address_list(payload.get('expected_counterparties'), field_name='expected_counterparties'),
        'expected_flow_patterns': payload.get('expected_flow_patterns') if isinstance(payload.get('expected_flow_patterns'), list) else [],
        'expected_approval_patterns': payload.get('expected_approval_patterns') if isinstance(payload.get('expected_approval_patterns'), dict) else {},
        'expected_liquidity_baseline': payload.get('expected_liquidity_baseline') if isinstance(payload.get('expected_liquidity_baseline'), dict) else {},
        'policy_tags': [str(item).strip() for item in (payload.get('policy_tags') or []) if str(item).strip()][:25] if isinstance(payload.get('policy_tags'), list) else [],
        'jurisdiction_tags': [str(item).strip() for item in (payload.get('jurisdiction_tags') or []) if str(item).strip()][:25] if isinstance(payload.get('jurisdiction_tags'), list) else [],
        'expected_oracle_freshness_seconds': expected_oracle_freshness_seconds,
        'expected_oracle_update_cadence_seconds': expected_oracle_update_cadence_seconds,
        'baseline_status': baseline_status,
        'baseline_source': baseline_source,
        'baseline_confidence': baseline_confidence,
        'baseline_coverage': baseline_coverage,
    }


def list_assets(request: Request) -> dict[str, Any]:
    require_live_mode()
    with pg_connection() as connection:
        ensure_pilot_schema(connection)
        user = authenticate_with_connection(connection, request)
        workspace_context = resolve_workspace(connection, user['id'], request.headers.get('x-workspace-id'))
        workspace_id = workspace_context['workspace_id']
        rows = connection.execute(
            '''
            SELECT id, name, description, asset_type, chain_network, identifier, asset_class, risk_tier, owner_team, notes, enabled,
                   issuer_name, asset_symbol, asset_identifier, token_contract_address, token_decimals, token_name, token_standard, chainlink_feeds, custody_wallets, treasury_ops_wallets, oracle_sources, venue_labels,
                   expected_counterparties, expected_flow_patterns, expected_approval_patterns, expected_liquidity_baseline,
                   expected_oracle_freshness_seconds, expected_oracle_update_cadence_seconds, policy_tags, jurisdiction_tags,
                   baseline_status, baseline_source, baseline_updated_at, baseline_confidence, baseline_coverage,
                   normalized_identifier, verification_status, verification_summary, verification_checked_at,
                   (SELECT COUNT(*) FROM targets t WHERE t.workspace_id = assets.workspace_id AND t.asset_id = assets.id AND t.deleted_at IS NULL) AS monitoring_target_count,
                   created_at, updated_at
            FROM assets
            WHERE workspace_id = %s AND deleted_at IS NULL
            ORDER BY created_at DESC
            ''',
            (workspace_id,),
        ).fetchall()
        asset_ids = [str(row['id']) for row in rows]
        tags_map: dict[str, list[str]] = {asset_id: [] for asset_id in asset_ids}
        if asset_ids:
            tag_rows = connection.execute(
                'SELECT asset_id, tag FROM asset_tags WHERE workspace_id = %s AND asset_id = ANY(%s::uuid[]) ORDER BY tag ASC',
                (workspace_id, asset_ids),
            ).fetchall()
            for row in tag_rows:
                tags_map[str(row['asset_id'])].append(str(row['tag']))
        assets: list[dict[str, Any]] = []
        for row in rows:
            item = _json_safe_value(dict(row))
            item['tags'] = tags_map.get(str(row['id']), [])
            assets.append(item)
        return {'assets': assets, 'workspace': workspace_context['workspace']}


def create_asset(payload: dict[str, Any], request: Request) -> dict[str, Any]:
    require_live_mode()
    validated = _validate_asset_payload(payload)
    with pg_connection() as connection:
        ensure_pilot_schema(connection)
        user, workspace_context = _require_workspace_admin(connection, request)
        workspace_id = workspace_context['workspace_id']
        entitlements = _workspace_plan(connection, workspace_id)
        max_targets = int(entitlements.get('max_targets') or 0)
        max_assets = max(5, max_targets)
        count_row = connection.execute('SELECT COUNT(*) AS count FROM assets WHERE workspace_id = %s AND deleted_at IS NULL', (workspace_id,)).fetchone()
        if int((count_row or {}).get('count') or 0) >= max_assets:
            raise HTTPException(status_code=status.HTTP_402_PAYMENT_REQUIRED, detail='Asset limit reached for current plan.')
        duplicate = connection.execute(
            '''
            SELECT id
            FROM assets
            WHERE workspace_id = %s
              AND deleted_at IS NULL
              AND lower(chain_network) = lower(%s)
              AND lower(identifier) = lower(%s)
            ''',
            (workspace_id, validated['chain_network'], validated['identifier']),
        ).fetchone()
        if duplicate is not None:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail='An asset with this chain and identifier already exists in this workspace.')
        asset_id = str(uuid.uuid4())
        verification = _derive_asset_verification(identifier=validated['identifier'], chain_network=validated['chain_network'])
        connection.execute(
            '''
            INSERT INTO assets (
                id, workspace_id, name, description, asset_type, chain_network, identifier, asset_class, risk_tier, owner_team, notes, enabled,
                issuer_name, asset_symbol, asset_identifier, token_contract_address, custody_wallets, treasury_ops_wallets, oracle_sources, venue_labels,
                expected_counterparties, expected_flow_patterns, expected_approval_patterns, expected_liquidity_baseline,
                expected_oracle_freshness_seconds, expected_oracle_update_cadence_seconds, policy_tags, jurisdiction_tags,
                baseline_status, baseline_source, baseline_updated_at, baseline_confidence, baseline_coverage,
                normalized_identifier, verification_status, verification_summary, verification_checked_at,
                created_by_user_id, updated_by_user_id
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb, %s::jsonb, %s::jsonb, %s::jsonb, %s::jsonb, %s::jsonb, %s::jsonb, %s, %s, %s::jsonb, %s::jsonb, %s, %s, NOW(), %s, %s, %s, %s, %s::jsonb, NOW(), %s, %s)
            ''',
            (
                asset_id,
                workspace_id,
                validated['name'],
                validated['description'],
                validated['asset_type'],
                validated['chain_network'],
                validated['identifier'],
                validated['asset_class'],
                validated['risk_tier'],
                validated['owner_team'],
                validated['notes'],
                validated['enabled'],
                validated['issuer_name'],
                validated['asset_symbol'],
                validated['asset_identifier'],
                validated['token_contract_address'],
                _json_dumps(validated['custody_wallets']),
                _json_dumps(validated['treasury_ops_wallets']),
                _json_dumps(validated['oracle_sources']),
                _json_dumps(validated['venue_labels']),
                _json_dumps(validated['expected_counterparties']),
                _json_dumps(validated['expected_flow_patterns']),
                _json_dumps(validated['expected_approval_patterns']),
                _json_dumps(validated['expected_liquidity_baseline']),
                validated['expected_oracle_freshness_seconds'] or None,
                validated['expected_oracle_update_cadence_seconds'] or None,
                _json_dumps(validated['policy_tags']),
                _json_dumps(validated['jurisdiction_tags']),
                validated['baseline_status'],
                validated['baseline_source'],
                validated['baseline_confidence'],
                validated['baseline_coverage'],
                verification['normalized_identifier'],
                verification['verification_status'],
                _json_dumps(verification['verification_summary']),
                user['id'],
                user['id'],
            ),
        )
        connection.execute(
            '''
            INSERT INTO asset_baselines (id, workspace_id, asset_id, status, source, confidence, coverage, details, updated_by_user_id, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s, NOW())
            ON CONFLICT (workspace_id, asset_id)
            DO UPDATE SET status = EXCLUDED.status, source = EXCLUDED.source, confidence = EXCLUDED.confidence, coverage = EXCLUDED.coverage, details = EXCLUDED.details, updated_by_user_id = EXCLUDED.updated_by_user_id, updated_at = NOW()
            ''',
            (str(uuid.uuid4()), workspace_id, asset_id, validated['baseline_status'], validated['baseline_source'], validated['baseline_confidence'], validated['baseline_coverage'], _json_dumps({'expected_counterparties': validated['expected_counterparties'], 'expected_approval_patterns': validated['expected_approval_patterns'], 'expected_flow_patterns': validated['expected_flow_patterns'], 'expected_liquidity_baseline': validated['expected_liquidity_baseline'], 'expected_oracle_freshness_seconds': validated['expected_oracle_freshness_seconds'], 'expected_oracle_update_cadence_seconds': validated['expected_oracle_update_cadence_seconds']}), user['id']),
        )
        for tag in validated['tags']:
            connection.execute(
                'INSERT INTO asset_tags (id, workspace_id, asset_id, tag) VALUES (%s, %s, %s, %s) ON CONFLICT (asset_id, tag) DO NOTHING',
                (str(uuid.uuid4()), workspace_id, asset_id, tag),
            )
        log_audit(connection, action='asset.create', entity_type='asset', entity_id=asset_id, request=request, user_id=user['id'], workspace_id=workspace_id, metadata={'asset_type': validated['asset_type']})
        connection.commit()
        return {'id': asset_id, **validated, 'verification_status': verification['verification_status'], 'verification_summary': verification['verification_summary'], 'normalized_identifier': verification['normalized_identifier']}


def get_asset(asset_id: str, request: Request) -> dict[str, Any]:
    require_live_mode()
    with pg_connection() as connection:
        ensure_pilot_schema(connection)
        user = authenticate_with_connection(connection, request)
        workspace_context = resolve_workspace(connection, user['id'], request.headers.get('x-workspace-id'))
        row = connection.execute(
            'SELECT * FROM assets WHERE id = %s AND workspace_id = %s AND deleted_at IS NULL',
            (asset_id, workspace_context['workspace_id']),
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Asset not found.')
        tags = connection.execute('SELECT tag FROM asset_tags WHERE asset_id = %s ORDER BY tag ASC', (asset_id,)).fetchall()
        item = _json_safe_value(dict(row))
        item['tags'] = [str(tag['tag']) for tag in tags]
        return {'asset': item}


def update_asset(asset_id: str, payload: dict[str, Any], request: Request) -> dict[str, Any]:
    require_live_mode()
    validated = _validate_asset_payload(payload)
    with pg_connection() as connection:
        ensure_pilot_schema(connection)
        user, workspace_context = _require_workspace_admin(connection, request)
        workspace_id = workspace_context['workspace_id']
        found = connection.execute('SELECT id FROM assets WHERE id = %s AND workspace_id = %s AND deleted_at IS NULL', (asset_id, workspace_id)).fetchone()
        if found is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Asset not found.')
        connection.execute(
            '''
            UPDATE assets
            SET name = %s, description = %s, asset_type = %s, chain_network = %s, identifier = %s, asset_class = %s, risk_tier = %s, owner_team = %s, notes = %s, enabled = %s,
                issuer_name = %s, asset_symbol = %s, asset_identifier = %s, token_contract_address = %s, custody_wallets = %s::jsonb, treasury_ops_wallets = %s::jsonb,
                oracle_sources = %s::jsonb, venue_labels = %s::jsonb, expected_counterparties = %s::jsonb, expected_flow_patterns = %s::jsonb, expected_approval_patterns = %s::jsonb,
                expected_liquidity_baseline = %s::jsonb, expected_oracle_freshness_seconds = %s, expected_oracle_update_cadence_seconds = %s, policy_tags = %s::jsonb, jurisdiction_tags = %s::jsonb,
                baseline_status = %s, baseline_source = %s, baseline_updated_at = NOW(), baseline_confidence = %s, baseline_coverage = %s,
                updated_by_user_id = %s, updated_at = NOW()
            WHERE id = %s
            ''',
            (
                validated['name'], validated['description'], validated['asset_type'], validated['chain_network'], validated['identifier'], validated['asset_class'], validated['risk_tier'], validated['owner_team'], validated['notes'], validated['enabled'],
                validated['issuer_name'], validated['asset_symbol'], validated['asset_identifier'], validated['token_contract_address'], _json_dumps(validated['custody_wallets']), _json_dumps(validated['treasury_ops_wallets']),
                _json_dumps(validated['oracle_sources']), _json_dumps(validated['venue_labels']), _json_dumps(validated['expected_counterparties']), _json_dumps(validated['expected_flow_patterns']), _json_dumps(validated['expected_approval_patterns']),
                _json_dumps(validated['expected_liquidity_baseline']), validated['expected_oracle_freshness_seconds'] or None, validated['expected_oracle_update_cadence_seconds'] or None, _json_dumps(validated['policy_tags']), _json_dumps(validated['jurisdiction_tags']),
                validated['baseline_status'], validated['baseline_source'], validated['baseline_confidence'], validated['baseline_coverage'], user['id'], asset_id,
            ),
        )
        connection.execute(
            '''
            INSERT INTO asset_baselines (id, workspace_id, asset_id, status, source, confidence, coverage, details, updated_by_user_id, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s, NOW())
            ON CONFLICT (workspace_id, asset_id)
            DO UPDATE SET status = EXCLUDED.status, source = EXCLUDED.source, confidence = EXCLUDED.confidence, coverage = EXCLUDED.coverage, details = EXCLUDED.details, updated_by_user_id = EXCLUDED.updated_by_user_id, updated_at = NOW()
            ''',
            (str(uuid.uuid4()), workspace_id, asset_id, validated['baseline_status'], validated['baseline_source'], validated['baseline_confidence'], validated['baseline_coverage'], _json_dumps({'expected_counterparties': validated['expected_counterparties'], 'expected_approval_patterns': validated['expected_approval_patterns'], 'expected_flow_patterns': validated['expected_flow_patterns'], 'expected_liquidity_baseline': validated['expected_liquidity_baseline'], 'expected_oracle_freshness_seconds': validated['expected_oracle_freshness_seconds'], 'expected_oracle_update_cadence_seconds': validated['expected_oracle_update_cadence_seconds']}), user['id']),
        )
        connection.execute('DELETE FROM asset_tags WHERE asset_id = %s', (asset_id,))
        for tag in validated['tags']:
            connection.execute('INSERT INTO asset_tags (id, workspace_id, asset_id, tag) VALUES (%s, %s, %s, %s)', (str(uuid.uuid4()), workspace_id, asset_id, tag))
        log_audit(connection, action='asset.update', entity_type='asset', entity_id=asset_id, request=request, user_id=user['id'], workspace_id=workspace_id, metadata={})
        connection.commit()
        return {'id': asset_id, **validated, 'verification_status': verification['verification_status'], 'verification_summary': verification['verification_summary'], 'normalized_identifier': verification['normalized_identifier']}


def delete_asset(asset_id: str, request: Request) -> dict[str, Any]:
    require_live_mode()
    with pg_connection() as connection:
        ensure_pilot_schema(connection)
        user, workspace_context = _require_workspace_admin(connection, request)
        row = connection.execute('SELECT id FROM assets WHERE id = %s AND workspace_id = %s AND deleted_at IS NULL', (asset_id, workspace_context['workspace_id'])).fetchone()
        if row is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Asset not found.')
        connection.execute('UPDATE assets SET deleted_at = NOW(), updated_by_user_id = %s, updated_at = NOW() WHERE id = %s', (user['id'], asset_id))
        log_audit(connection, action='asset.delete', entity_type='asset', entity_id=asset_id, request=request, user_id=user['id'], workspace_id=workspace_context['workspace_id'], metadata={})
        connection.commit()
        return {'deleted': True, 'id': asset_id}


def resolve_asset_onchain(asset_id: str, payload: dict[str, Any], request: Request) -> dict[str, Any]:
    require_live_mode()
    chain_network = str(payload.get('chain_network') or '').strip().lower()
    rpc_url_override = str(payload.get('rpc_url_override') or '').strip() or None
    if not chain_network:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='chain_network is required.')
    with pg_connection() as connection:
        ensure_pilot_schema(connection)
        user, workspace_context = _require_workspace_admin(connection, request)
        workspace_id = workspace_context['workspace_id']
        asset = connection.execute(
            'SELECT id, token_contract_address, asset_symbol FROM assets WHERE id = %s AND workspace_id = %s AND deleted_at IS NULL',
            (asset_id, workspace_id),
        ).fetchone()
        if asset is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Asset not found.')
        token_address = str(asset.get('token_contract_address') or '').strip().lower()
        if not re.match(r'^0x[a-f0-9]{40}$', token_address):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='Asset token_contract_address is required for on-chain resolve.')
        rpc_url = _rpc_url_for_chain(chain_network, rpc_url_override=rpc_url_override)
        if not rpc_url:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='No RPC URL configured for chain_network.')
        try:
            token_name = _decode_abi_string(_eth_call_raw(rpc_url, to_address=token_address, data=_ERC20_NAME_SELECTOR))
            token_symbol = _decode_abi_string(_eth_call_raw(rpc_url, to_address=token_address, data=_ERC20_SYMBOL_SELECTOR))
            token_decimals = _decode_uint256(_eth_call_raw(rpc_url, to_address=token_address, data=_ERC20_DECIMALS_SELECTOR))
            token_standard = _detect_token_standard(rpc_url, token_address)
        except Exception as exc:
            raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=f'On-chain metadata resolve failed: {exc}') from exc
        if token_standard not in ASSET_TOKEN_STANDARDS:
            token_standard = 'unknown'
        resolved_at = utc_now_iso()
        connection.execute(
            '''
            UPDATE assets
            SET chain_network = %s,
                token_name = %s,
                asset_symbol = COALESCE(%s, asset_symbol),
                token_decimals = %s,
                token_standard = %s,
                updated_by_user_id = %s,
                updated_at = NOW()
            WHERE id = %s
            ''',
            (chain_network, token_name, token_symbol, token_decimals, token_standard, user['id'], asset_id),
        )
        log_audit(connection, action='asset.bind.resolve_onchain', entity_type='asset', entity_id=asset_id, request=request, user_id=user['id'], workspace_id=workspace_id, metadata={'chain_network': chain_network, 'token_standard': token_standard})
        connection.commit()
    logger.info('asset_onchain_resolve_ok asset_id=%s decimals=%s standard=%s', asset_id, token_decimals, token_standard)
    return {'token_name': token_name, 'token_symbol': token_symbol, 'token_decimals': token_decimals, 'standard': token_standard, 'resolved_at': resolved_at}


def bind_asset_wallets(asset_id: str, payload: dict[str, Any], request: Request) -> dict[str, Any]:
    require_live_mode()
    wallets = payload.get('wallets')
    if not isinstance(wallets, list):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='wallets must be a list.')
    normalized_wallets: list[dict[str, str]] = []
    for item in wallets:
        if not isinstance(item, dict):
            continue
        address = str(item.get('address') or '').strip().lower()
        role = str(item.get('role') or '').strip().lower()
        if not re.match(r'^0x[a-f0-9]{40}$', address):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='wallet address must be an EVM-style address.')
        if role not in ASSET_WALLET_ROLES:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='wallet role must be treasury_ops/custody/counterparty/venue.')
        normalized_wallets.append({'address': address, 'role': role})
    with pg_connection() as connection:
        ensure_pilot_schema(connection)
        user, workspace_context = _require_workspace_admin(connection, request)
        workspace_id = workspace_context['workspace_id']
        found = connection.execute('SELECT id FROM assets WHERE id = %s AND workspace_id = %s AND deleted_at IS NULL', (asset_id, workspace_id)).fetchone()
        if found is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Asset not found.')
        for item in normalized_wallets:
            connection.execute(
                '''
                INSERT INTO asset_wallet_bindings (id, workspace_id, asset_id, wallet_address, wallet_role)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (workspace_id, asset_id, wallet_address, wallet_role) DO NOTHING
                ''',
                (str(uuid.uuid4()), workspace_id, asset_id, item['address'], item['role']),
            )
        bindings = connection.execute(
            'SELECT wallet_address, wallet_role FROM asset_wallet_bindings WHERE workspace_id = %s AND asset_id = %s ORDER BY wallet_role, wallet_address',
            (workspace_id, asset_id),
        ).fetchall()
        by_role: dict[str, list[str]] = {role: [] for role in ASSET_WALLET_ROLES}
        for row in bindings:
            role = str(row['wallet_role'])
            by_role.setdefault(role, []).append(str(row['wallet_address']).lower())
        connection.execute(
            '''
            UPDATE assets
            SET treasury_ops_wallets = %s::jsonb,
                custody_wallets = %s::jsonb,
                expected_counterparties = %s::jsonb,
                venue_labels = %s::jsonb,
                updated_by_user_id = %s,
                updated_at = NOW()
            WHERE id = %s
            ''',
            (
                _json_dumps(by_role.get('treasury_ops', [])),
                _json_dumps(by_role.get('custody', [])),
                _json_dumps(by_role.get('counterparty', [])),
                _json_dumps(by_role.get('venue', [])),
                user['id'],
                asset_id,
            ),
        )
        log_audit(connection, action='asset.bind.wallets', entity_type='asset', entity_id=asset_id, request=request, user_id=user['id'], workspace_id=workspace_id, metadata={'wallet_count': len(normalized_wallets)})
        connection.commit()
        return {'asset_id': asset_id, 'wallets': [{'address': row['wallet_address'], 'role': row['wallet_role']} for row in bindings]}


def bind_asset_chainlink_feeds(asset_id: str, payload: dict[str, Any], request: Request) -> dict[str, Any]:
    require_live_mode()
    feeds = payload.get('feeds')
    if not isinstance(feeds, list):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='feeds must be a list.')
    normalized_feeds: list[dict[str, str]] = []
    for item in feeds:
        if not isinstance(item, dict):
            continue
        chain_network = str(item.get('chain_network') or '').strip().lower()
        proxy_address = str(item.get('proxy_address') or '').strip().lower()
        pair = str(item.get('pair') or '').strip().upper()
        if not chain_network:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='chain_network is required for each feed.')
        if not re.match(r'^0x[a-f0-9]{40}$', proxy_address):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='proxy_address must be an EVM-style address.')
        if not pair:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='pair is required for each feed.')
        normalized_feeds.append({'chain_network': chain_network, 'proxy_address': proxy_address, 'pair': pair})
    with pg_connection() as connection:
        ensure_pilot_schema(connection)
        user, workspace_context = _require_workspace_admin(connection, request)
        workspace_id = workspace_context['workspace_id']
        found = connection.execute('SELECT id FROM assets WHERE id = %s AND workspace_id = %s AND deleted_at IS NULL', (asset_id, workspace_id)).fetchone()
        if found is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Asset not found.')
        connection.execute(
            'UPDATE assets SET chainlink_feeds = %s::jsonb, updated_by_user_id = %s, updated_at = NOW() WHERE id = %s',
            (_json_dumps(normalized_feeds), user['id'], asset_id),
        )
        log_audit(connection, action='asset.bind.chainlink', entity_type='asset', entity_id=asset_id, request=request, user_id=user['id'], workspace_id=workspace_id, metadata={'feed_count': len(normalized_feeds)})
        connection.commit()
        return {'asset_id': asset_id, 'feeds': normalized_feeds}


def list_targets(request: Request) -> dict[str, Any]:
    require_live_mode()
    with pg_connection() as connection:
        ensure_pilot_schema(connection)
        user = authenticate_with_connection(connection, request)
        workspace_context = resolve_workspace(connection, user['id'], request.headers.get('x-workspace-id'))
        workspace_id = workspace_context['workspace_id']
        rows = connection.execute(
            '''
            SELECT t.id, t.name, t.target_type, t.chain_network, t.contract_identifier, t.wallet_address, t.asset_type, t.owner_notes, t.severity_preference, t.enabled,
                   t.asset_id,
                   t.chain_id, t.target_metadata,
                   t.monitoring_enabled, t.monitoring_mode, t.monitoring_interval_seconds, t.severity_threshold, t.auto_create_alerts, t.auto_create_incidents,
                   t.notification_channels, t.last_checked_at, t.last_run_status, t.last_run_id, t.last_alert_at, t.monitored_by_workspace_id, t.is_active,
                   t.created_at, t.updated_at,
                   a.id AS resolved_asset_id, a.name AS asset_name,
                   ms.id AS monitored_system_id
            FROM targets t
            LEFT JOIN assets a
              ON a.id = t.asset_id
             AND a.workspace_id = t.workspace_id
             AND a.deleted_at IS NULL
            LEFT JOIN monitored_systems ms
              ON ms.target_id = t.id
             AND ms.workspace_id = t.workspace_id
            WHERE t.workspace_id = %s AND t.deleted_at IS NULL
            ORDER BY t.created_at DESC
            ''',
            (workspace_id,),
        ).fetchall()
        target_ids = [str(row['id']) for row in rows]
        tags_map: dict[str, list[str]] = {target_id: [] for target_id in target_ids}
        if target_ids:
            tag_rows = connection.execute(
                'SELECT target_id, tag FROM target_tags WHERE workspace_id = %s AND target_id = ANY(%s::uuid[]) ORDER BY tag ASC',
                (workspace_id, target_ids),
            ).fetchall()
            for row in tag_rows:
                tags_map[str(row['target_id'])].append(str(row['tag']))
        targets: list[dict[str, Any]] = []
        for row in rows:
            item = _json_safe_value(dict(row))
            health_status, health_reason = _target_health_payload(
                enabled=bool(row.get('enabled')),
                monitoring_enabled=bool(row.get('monitoring_enabled')),
                asset_id=str(row.get('asset_id') or '') or None,
                asset_exists=bool(row.get('resolved_asset_id')),
                monitored_system_id=str(row.get('monitored_system_id') or '') or None,
            )
            item['asset_missing'] = not bool(row.get('resolved_asset_id'))
            item['health_status'] = health_status
            item['health_reason'] = health_reason
            item['tags'] = tags_map.get(str(row['id']), [])
            targets.append(item)
        return {'targets': targets, 'workspace': workspace_context['workspace']}


def create_target(payload: dict[str, Any], request: Request) -> dict[str, Any]:
    require_live_mode()
    validated = _validate_target_payload(payload)
    with pg_connection() as connection:
        ensure_pilot_schema(connection)
        user, workspace_context = _require_workspace_admin(connection, request)
        workspace_id = workspace_context['workspace_id']
        entitlements = _workspace_plan(connection, workspace_id)
        count_row = connection.execute('SELECT COUNT(*) AS count FROM targets WHERE workspace_id = %s AND deleted_at IS NULL', (workspace_id,)).fetchone()
        if int((count_row or {}).get('count') or 0) >= int(entitlements.get('max_targets') or 0):
            raise HTTPException(status_code=status.HTTP_402_PAYMENT_REQUIRED, detail='Target limit reached for current plan.')
        if validated['asset_id'] is None:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='asset_id is required when creating a target.')
        if validated['asset_id'] is not None:
            asset_row = connection.execute(
                'SELECT id FROM assets WHERE id = %s AND workspace_id = %s AND deleted_at IS NULL',
                (validated['asset_id'], workspace_id),
            ).fetchone()
            if asset_row is None:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='asset_id must reference an asset in this workspace.')
        target_id = str(uuid.uuid4())
        connection.execute(
            '''
            INSERT INTO targets (
                id, workspace_id, name, target_type, chain_network, contract_identifier, wallet_address, asset_type, owner_notes, severity_preference, enabled,
                asset_id,
                chain_id, target_metadata,
                monitoring_enabled, monitoring_mode, monitoring_interval_seconds, severity_threshold, auto_create_alerts, auto_create_incidents, notification_channels,
                monitored_by_workspace_id, is_active, created_by_user_id, updated_by_user_id
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::uuid, %s, %s::jsonb, %s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s, %s, %s)
            ''',
            (
                target_id,
                workspace_id,
                validated['name'],
                validated['target_type'],
                validated['chain_network'],
                validated['contract_identifier'],
                validated['wallet_address'],
                validated['asset_type'],
                validated['owner_notes'],
                validated['severity_preference'],
                validated['enabled'],
                validated['asset_id'],
                validated['chain_id'],
                _json_dumps(validated['target_metadata']),
                validated['monitoring_enabled'],
                validated['monitoring_mode'],
                validated['monitoring_interval_seconds'],
                validated['severity_threshold'],
                validated['auto_create_alerts'],
                validated['auto_create_incidents'],
                _json_dumps(validated['notification_channels']),
                workspace_id,
                validated['is_active'],
                user['id'],
                user['id'],
            ),
        )
        for tag in validated['tags']:
            connection.execute(
                'INSERT INTO target_tags (id, workspace_id, target_id, tag) VALUES (%s, %s, %s, %s) ON CONFLICT (target_id, tag) DO NOTHING',
                (str(uuid.uuid4()), workspace_id, target_id, tag),
            )
        if validated['enabled'] and validated['monitoring_enabled']:
            result = ensure_monitored_system_for_target(connection, target_id=target_id, workspace_id=workspace_id)
            if result.get('status') != 'ok':
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='Enabled targets require a valid linked asset before monitoring can start.')
        log_audit(connection, action='target.create', entity_type='target', entity_id=target_id, request=request, user_id=user['id'], workspace_id=workspace_id, metadata={'target_type': validated['target_type']})
        connection.commit()
        return {'id': target_id, **validated}


def get_target(target_id: str, request: Request) -> dict[str, Any]:
    require_live_mode()
    with pg_connection() as connection:
        ensure_pilot_schema(connection)
        user = authenticate_with_connection(connection, request)
        workspace_context = resolve_workspace(connection, user['id'], request.headers.get('x-workspace-id'))
        row = connection.execute(
            '''
            SELECT t.id, t.workspace_id, t.name, t.target_type, t.chain_network, t.contract_identifier, t.wallet_address, t.asset_type, t.owner_notes, t.severity_preference, t.enabled,
                   t.asset_id, t.chain_id, t.target_metadata, t.monitoring_enabled, t.monitoring_mode, t.monitoring_interval_seconds, t.severity_threshold, t.auto_create_alerts,
                   t.auto_create_incidents, t.notification_channels, t.last_checked_at, t.last_run_status, t.last_run_id, t.last_alert_at, t.monitored_by_workspace_id, t.is_active,
                   t.created_at, t.updated_at,
                   a.id AS resolved_asset_id,
                   ms.id AS monitored_system_id
            FROM targets t
            LEFT JOIN assets a ON a.id = t.asset_id AND a.workspace_id = t.workspace_id AND a.deleted_at IS NULL
            LEFT JOIN monitored_systems ms ON ms.target_id = t.id AND ms.workspace_id = t.workspace_id
            WHERE t.id = %s AND t.workspace_id = %s AND t.deleted_at IS NULL
            ''',
            (target_id, workspace_context['workspace_id']),
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Target not found.')
        tags = connection.execute('SELECT tag FROM target_tags WHERE target_id = %s ORDER BY tag ASC', (target_id,)).fetchall()
        item = _json_safe_value(dict(row))
        health_status, health_reason = _target_health_payload(
            enabled=bool(row.get('enabled')),
            monitoring_enabled=bool(row.get('monitoring_enabled')),
            asset_id=str(row.get('asset_id') or '') or None,
            asset_exists=bool(row.get('resolved_asset_id')),
            monitored_system_id=str(row.get('monitored_system_id') or '') or None,
        )
        item['asset_missing'] = not bool(row.get('resolved_asset_id'))
        item['health_status'] = health_status
        item['health_reason'] = health_reason
        item['tags'] = [str(tag['tag']) for tag in tags]
        return {'target': item}


def update_target(target_id: str, payload: dict[str, Any], request: Request) -> dict[str, Any]:
    require_live_mode()
    with pg_connection() as connection:
        ensure_pilot_schema(connection)
        user, workspace_context = _require_workspace_admin(connection, request)
        workspace_id = workspace_context['workspace_id']
        found = connection.execute(
            '''
            SELECT id, workspace_id
            FROM targets
            WHERE id = %s AND workspace_id = %s AND deleted_at IS NULL
            ''',
            (target_id, workspace_id),
        ).fetchone()
        if found is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Target not found.')
        merged_payload = dict(payload)
        validated = _validate_target_payload(merged_payload)
        if validated['enabled'] and validated['monitoring_enabled'] and validated['asset_id'] is None:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='Enabled monitoring requires asset_id.')
        if validated['asset_id'] is not None:
            asset_row = connection.execute(
                'SELECT id, name FROM assets WHERE id = %s AND workspace_id = %s AND deleted_at IS NULL',
                (validated['asset_id'], workspace_id),
            ).fetchone()
            if asset_row is None:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='asset_id must reference an asset in this workspace.')
        connection.execute(
            '''
            UPDATE targets
            SET name = %s, target_type = %s, chain_network = %s, contract_identifier = %s, wallet_address = %s, asset_type = %s, owner_notes = %s, severity_preference = %s, enabled = %s, asset_id = %s::uuid,
                chain_id = %s, target_metadata = %s::jsonb,
                monitoring_enabled = %s, monitoring_mode = %s, monitoring_interval_seconds = %s, severity_threshold = %s, auto_create_alerts = %s, auto_create_incidents = %s,
                notification_channels = %s::jsonb, monitored_by_workspace_id = %s, is_active = %s, updated_by_user_id = %s, updated_at = NOW()
            WHERE id = %s
            ''',
            (
                validated['name'], validated['target_type'], validated['chain_network'], validated['contract_identifier'], validated['wallet_address'], validated['asset_type'], validated['owner_notes'], validated['severity_preference'], validated['enabled'], validated['asset_id'],
                validated['chain_id'], _json_dumps(validated['target_metadata']),
                validated['monitoring_enabled'], validated['monitoring_mode'], validated['monitoring_interval_seconds'], validated['severity_threshold'], validated['auto_create_alerts'], validated['auto_create_incidents'],
                _json_dumps(validated['notification_channels']), workspace_id, validated['is_active'], user['id'], target_id,
            ),
        )
        connection.execute('DELETE FROM target_tags WHERE target_id = %s', (target_id,))
        for tag in validated['tags']:
            connection.execute('INSERT INTO target_tags (id, workspace_id, target_id, tag) VALUES (%s, %s, %s, %s)', (str(uuid.uuid4()), workspace_id, target_id, tag))
        if validated['enabled'] and validated['monitoring_enabled']:
            result = ensure_monitored_system_for_target(connection, target_id=target_id, workspace_id=workspace_id)
            if result.get('status') != 'ok':
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='Enabled targets require a valid linked asset before monitoring can start.')
        else:
            connection.execute(
                "UPDATE monitored_systems SET is_enabled = FALSE, runtime_status = 'offline', status = 'paused' WHERE target_id = %s::uuid AND workspace_id = %s",
                (target_id, workspace_id),
            )
            reconcile_enabled_targets_monitored_systems(connection, workspace_id=workspace_id)
        log_audit(connection, action='target.update', entity_type='target', entity_id=target_id, request=request, user_id=user['id'], workspace_id=workspace_id, metadata={})
        connection.commit()
        return {'id': target_id, **validated}


def set_target_enabled(target_id: str, enabled: bool, request: Request) -> dict[str, Any]:
    require_live_mode()
    with pg_connection() as connection:
        ensure_pilot_schema(connection)
        user, workspace_context = _require_workspace_admin(connection, request)
        row = connection.execute(
            'SELECT id, asset_id, chain_network FROM targets WHERE id = %s AND workspace_id = %s AND deleted_at IS NULL',
            (target_id, workspace_context['workspace_id']),
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Target not found.')
        if enabled:
            asset_valid = connection.execute(
                'SELECT a.id FROM assets a WHERE a.id = %s::uuid AND a.workspace_id = %s::uuid AND a.deleted_at IS NULL',
                (row.get('asset_id'), workspace_context['workspace_id']),
            ).fetchone()
            if asset_valid is None:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='Cannot enable target: linked asset is missing or deleted.')
        connection.execute(
            'UPDATE targets SET enabled = %s, monitoring_enabled = %s, updated_by_user_id = %s, updated_at = NOW() WHERE id = %s',
            (enabled, enabled, user['id'], target_id),
        )
        if enabled:
            result = ensure_monitored_system_for_target(connection, target_id=target_id, workspace_id=workspace_context['workspace_id'])
            if result.get('status') != 'ok':
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='Cannot enable target: linked asset is missing or deleted.')
        else:
            connection.execute(
                "UPDATE monitored_systems SET is_enabled = FALSE, runtime_status = 'offline', status = 'paused' WHERE target_id = %s::uuid AND workspace_id = %s",
                (target_id, workspace_context['workspace_id']),
            )
            reconcile_enabled_targets_monitored_systems(connection, workspace_id=workspace_context['workspace_id'])
        log_audit(
            connection,
            action='target.enable' if enabled else 'target.disable',
            entity_type='target',
            entity_id=target_id,
            request=request,
            user_id=user['id'],
            workspace_id=workspace_context['workspace_id'],
            metadata={},
        )
        connection.commit()
        return {'id': target_id, 'enabled': enabled, 'monitoring_enabled': enabled}


def delete_target(target_id: str, request: Request) -> dict[str, Any]:
    require_live_mode()
    with pg_connection() as connection:
        ensure_pilot_schema(connection)
        user, workspace_context = _require_workspace_admin(connection, request)
        row = connection.execute('SELECT id FROM targets WHERE id = %s AND workspace_id = %s AND deleted_at IS NULL', (target_id, workspace_context['workspace_id'])).fetchone()
        if row is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Target not found.')
        connection.execute('UPDATE targets SET deleted_at = NOW(), updated_by_user_id = %s, updated_at = NOW() WHERE id = %s', (user['id'], target_id))
        connection.execute('DELETE FROM monitored_systems WHERE target_id = %s::uuid AND workspace_id = %s', (target_id, workspace_context['workspace_id']))
        log_audit(connection, action='target.delete', entity_type='target', entity_id=target_id, request=request, user_id=user['id'], workspace_id=workspace_context['workspace_id'], metadata={})
        connection.commit()
        return {'deleted': True, 'id': target_id}


def get_module_config(module_key: str, request: Request) -> dict[str, Any]:
    require_live_mode()
    if module_key not in MODULE_KEYS:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Unknown module.')
    with pg_connection() as connection:
        ensure_pilot_schema(connection)
        user = authenticate_with_connection(connection, request)
        workspace_context = resolve_workspace(connection, user['id'], request.headers.get('x-workspace-id'))
        row = connection.execute('SELECT config, updated_at FROM module_configs WHERE workspace_id = %s AND module_key = %s', (workspace_context['workspace_id'], module_key)).fetchone()
        normalized = normalize_module_config(module_key, _json_safe_value((row or {}).get('config') or {}))
        return {'module': module_key, 'config': normalized, 'summary': summarize_module_config(module_key, normalized), 'updated_at': _json_safe_value((row or {}).get('updated_at'))}


def put_module_config(module_key: str, payload: dict[str, Any], request: Request) -> dict[str, Any]:
    require_live_mode()
    if module_key not in MODULE_KEYS:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Unknown module.')
    config = payload.get('config')
    if not isinstance(config, dict):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='config must be an object.')
    with pg_connection() as connection:
        ensure_pilot_schema(connection)
        user, workspace_context = _require_workspace_admin(connection, request)
        normalized_config = normalize_module_config(module_key, config)
        entitlements = _workspace_plan(connection, workspace_context['workspace_id'])
        if module_key in {'threat', 'resilience'} and entitlements['plan_key'] == 'free_trial' and len(normalized_config.keys()) > 4:
            raise HTTPException(status_code=status.HTTP_402_PAYMENT_REQUIRED, detail='Advanced module configuration requires Starter or higher.')
        config_id = str(uuid.uuid4())
        connection.execute(
            '''
            INSERT INTO module_configs (id, workspace_id, module_key, config, updated_by_user_id)
            VALUES (%s, %s, %s, %s::jsonb, %s)
            ON CONFLICT (workspace_id, module_key)
            DO UPDATE SET config = excluded.config, updated_by_user_id = excluded.updated_by_user_id, updated_at = NOW()
            ''',
            (config_id, workspace_context['workspace_id'], module_key, _json_dumps(normalized_config), user['id']),
        )
        log_audit(connection, action='module_config.update', entity_type='module_config', entity_id=module_key, request=request, user_id=user['id'], workspace_id=workspace_context['workspace_id'], metadata={'module': module_key})
        connection.commit()
        return {'module': module_key, 'config': normalized_config, 'summary': summarize_module_config(module_key, normalized_config), 'saved': True}


def list_detections(
    request: Request,
    *,
    limit: int = 50,
    severity: str | None = None,
    status_value: str | None = None,
    evidence_source: str | None = None,
    monitored_system_id: str | None = None,
    protected_asset_id: str | None = None,
) -> dict[str, Any]:
    require_live_mode()
    with pg_connection() as connection:
        ensure_pilot_schema(connection)
        user = authenticate_with_connection(connection, request)
        workspace_context = resolve_workspace(connection, user['id'], request.headers.get('x-workspace-id'))
        max_limit = max(1, min(int(limit or 50), 200))
        rows = connection.execute(
            '''
            SELECT id,
                   workspace_id,
                   monitored_system_id,
                   protected_asset_id,
                   detection_type,
                   severity,
                   confidence,
                   title,
                   evidence_summary,
                   evidence_source,
                   source_rule,
                   status,
                   detected_at,
                   raw_evidence_json,
                   monitoring_run_id,
                   linked_alert_id,
                   COALESCE(raw_evidence_json->'observed_evidence'->>'tx_hash', raw_evidence_json->>'tx_hash') AS tx_hash,
                   COALESCE(
                       NULLIF(raw_evidence_json->'observed_evidence'->>'block_number', '')::bigint,
                       NULLIF(raw_evidence_json->>'block_number', '')::bigint
                   ) AS block_number,
                   COALESCE(
                       raw_evidence_json->'observed_evidence'->>'detector_kind',
                       raw_evidence_json->'observed_evidence'->>'detector_family',
                       raw_evidence_json->>'detector_kind',
                       raw_evidence_json->>'detector_family'
                   ) AS detector_kind,
                   COALESCE(raw_evidence_json->'observed_evidence'->>'evidence_origin', evidence_source) AS evidence_origin,
                   a.incident_id AS linked_incident_id,
                   ra.id AS linked_action_id,
                   ev_stats.linked_evidence_count,
                   ev_latest.last_evidence_at,
                   ev_latest.evidence_source AS last_evidence_source,
                   COALESCE(
                       ev_latest.raw_payload_json->>'evidence_origin',
                       ev_latest.evidence_source,
                       COALESCE(raw_evidence_json->'observed_evidence'->>'evidence_origin', evidence_source)
                   ) AS last_evidence_origin,
                   COALESCE(ev_latest.tx_hash, COALESCE(raw_evidence_json->'observed_evidence'->>'tx_hash', raw_evidence_json->>'tx_hash')) AS chain_tx_hash,
                   COALESCE(
                       ev_latest.block_number,
                       NULLIF(raw_evidence_json->'observed_evidence'->>'block_number', '')::bigint,
                       NULLIF(raw_evidence_json->>'block_number', '')::bigint
                   ) AS chain_block_number,
                   COALESCE(
                       ev_latest.raw_payload_json->>'detector_kind',
                       ev_latest.raw_payload_json->>'detector_family',
                       COALESCE(
                           raw_evidence_json->'observed_evidence'->>'detector_kind',
                           raw_evidence_json->'observed_evidence'->>'detector_family',
                           raw_evidence_json->>'detector_kind',
                           raw_evidence_json->>'detector_family'
                       )
                   ) AS chain_detector_kind,
                   created_at,
                   updated_at
            FROM detections
            LEFT JOIN alerts a ON a.id = detections.linked_alert_id AND a.workspace_id = detections.workspace_id
            LEFT JOIN LATERAL (
                SELECT COUNT(*)::int AS linked_evidence_count
                FROM evidence e
                WHERE e.workspace_id = detections.workspace_id
                  AND e.alert_id = detections.linked_alert_id
            ) ev_stats ON TRUE
            LEFT JOIN LATERAL (
                SELECT e.observed_at AS last_evidence_at,
                       e.source_provider AS evidence_source,
                       e.tx_hash,
                       e.block_number,
                       e.raw_payload_json
                FROM evidence e
                WHERE e.workspace_id = detections.workspace_id
                  AND e.alert_id = detections.linked_alert_id
                ORDER BY e.observed_at DESC, e.created_at DESC, e.id DESC
                LIMIT 1
            ) ev_latest ON TRUE
            LEFT JOIN LATERAL (
                SELECT id
                FROM response_actions
                WHERE workspace_id = detections.workspace_id
                  AND (
                      (detections.linked_alert_id IS NOT NULL AND alert_id = detections.linked_alert_id)
                      OR (a.incident_id IS NOT NULL AND incident_id = a.incident_id)
                  )
                ORDER BY created_at DESC
                LIMIT 1
            ) ra ON TRUE
            WHERE workspace_id = %s
              AND (%s::text IS NULL OR severity = %s::text)
              AND (%s::text IS NULL OR status = %s::text)
              AND (%s::text IS NULL OR evidence_source = %s::text)
              AND (%s::uuid IS NULL OR monitored_system_id = %s::uuid)
              AND (%s::uuid IS NULL OR protected_asset_id = %s::uuid)
            ORDER BY detected_at DESC
            LIMIT %s
            ''',
            (
                workspace_context['workspace_id'],
                severity,
                severity,
                status_value,
                status_value,
                evidence_source,
                evidence_source,
                monitored_system_id,
                monitored_system_id,
                protected_asset_id,
                protected_asset_id,
                max_limit,
            ),
        ).fetchall()
        serialized: list[dict[str, Any]] = []
        for row in rows:
            item = _json_safe_value(dict(row))
            item['tx_hash'] = item.get('chain_tx_hash') or item.get('tx_hash')
            item['block_number'] = item.get('chain_block_number') or item.get('block_number')
            item['detector_kind'] = item.get('chain_detector_kind') or item.get('detector_kind')
            item['evidence_source'] = item.get('last_evidence_source') or item.get('evidence_source')
            item['evidence_origin'] = item.get('last_evidence_origin') or item.get('evidence_origin')
            item['linked_evidence_count'] = int(item.get('linked_evidence_count') or 0)
            item['last_evidence_at'] = item.get('last_evidence_at')
            item['linked_detection_id'] = item.get('id')
            item['linked_alert_id'] = item.get('linked_alert_id')
            item['linked_incident_id'] = item.get('linked_incident_id')
            item['linked_action_id'] = item.get('linked_action_id')
            item['chain_linked_ids'] = {
                'detection_id': item.get('linked_detection_id'),
                'alert_id': item.get('linked_alert_id'),
                'incident_id': item.get('linked_incident_id'),
                'action_id': item.get('linked_action_id'),
            }
            serialized.append(item)
        return {'detections': serialized}


def get_detection(detection_id: str, request: Request) -> dict[str, Any]:
    require_live_mode()
    with pg_connection() as connection:
        ensure_pilot_schema(connection)
        user = authenticate_with_connection(connection, request)
        workspace_context = resolve_workspace(connection, user['id'], request.headers.get('x-workspace-id'))
        row = connection.execute(
            '''
            SELECT id,
                   workspace_id,
                   monitored_system_id,
                   protected_asset_id,
                   detection_type,
                   severity,
                   confidence,
                   title,
                   evidence_summary,
                   evidence_source,
                   source_rule,
                   status,
                   detected_at,
                   raw_evidence_json,
                   monitoring_run_id,
                   linked_alert_id,
                   created_at,
                   updated_at
            FROM detections
            WHERE id = %s::uuid AND workspace_id = %s
            ''',
            (detection_id, workspace_context['workspace_id']),
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Detection not found.')
        return {'detection': _json_safe_value(dict(row))}


def get_detection_evidence(detection_id: str, request: Request) -> dict[str, Any]:
    require_live_mode()
    with pg_connection() as connection:
        ensure_pilot_schema(connection)
        user = authenticate_with_connection(connection, request)
        workspace_context = resolve_workspace(connection, user['id'], request.headers.get('x-workspace-id'))
        row = connection.execute(
            '''
            SELECT id,
                   workspace_id,
                   evidence_summary,
                   raw_evidence_json,
                   linked_alert_id,
                   monitoring_run_id
            FROM detections
            WHERE id = %s::uuid AND workspace_id = %s
            ''',
            (detection_id, workspace_context['workspace_id']),
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Detection not found.')
        detection = _json_safe_value(dict(row))
        return {
            'detection_id': detection.get('id'),
            'summary': detection.get('evidence_summary'),
            'raw_evidence_json': detection.get('raw_evidence_json') or {},
            'linked_alert_id': detection.get('linked_alert_id'),
            'monitoring_run_id': detection.get('monitoring_run_id'),
        }


def list_alerts(request: Request, *, severity: str | None = None, module: str | None = None, target_id: str | None = None, status_value: str | None = None, source: str | None = None) -> dict[str, Any]:
    require_live_mode()
    with pg_connection() as connection:
        ensure_pilot_schema(connection)
        user = authenticate_with_connection(connection, request)
        workspace_context = resolve_workspace(connection, user['id'], request.headers.get('x-workspace-id'))
        rows = connection.execute(
            '''
            SELECT
                a.id, a.alert_type, a.title, a.severity, a.status, a.summary, a.module_key, a.target_id, a.detection_id, a.incident_id, a.assigned_to, a.evidence_summary,
                a.source, a.source_service, a.recommended_action, a.degraded, a.occurrence_count, a.last_seen_at, a.findings, a.owner_user_id, a.triage_status,
                a.resolution_note, a.suppressed_until, a.acknowledged_at, a.resolved_at, a.created_at, a.updated_at,
                ev_stats.linked_evidence_count,
                ev_latest.last_evidence_at,
                ev_latest.evidence_source,
                ev_latest.tx_hash,
                ev_latest.block_number,
                COALESCE(
                    ev_latest.raw_payload_json->>'detector_kind',
                    ev_latest.raw_payload_json->>'detector_family',
                    a.findings->>'detector_kind',
                    a.findings->>'detector_family'
                ) AS detector_kind,
                COALESCE(ev_latest.raw_payload_json->>'evidence_origin', ev_latest.source_provider, a.source) AS evidence_origin,
                ra.id AS linked_action_id,
                (
                    SELECT ra.mode
                    FROM response_actions ra
                    WHERE ra.workspace_id = a.workspace_id
                      AND (ra.alert_id = a.id OR (a.incident_id IS NOT NULL AND ra.incident_id = a.incident_id))
                    ORDER BY ra.created_at DESC
                    LIMIT 1
                ) AS response_action_mode
            FROM alerts a
            LEFT JOIN LATERAL (
                SELECT COUNT(*)::int AS linked_evidence_count
                FROM evidence e
                WHERE e.workspace_id = a.workspace_id
                  AND e.alert_id = a.id
            ) ev_stats ON TRUE
            LEFT JOIN LATERAL (
                SELECT e.observed_at AS last_evidence_at,
                       e.source_provider AS evidence_source,
                       e.tx_hash,
                       e.block_number,
                       e.raw_payload_json
                FROM evidence e
                WHERE e.workspace_id = a.workspace_id
                  AND e.alert_id = a.id
                ORDER BY e.observed_at DESC, e.created_at DESC, e.id DESC
                LIMIT 1
            ) ev_latest ON TRUE
            LEFT JOIN LATERAL (
                SELECT id
                FROM response_actions
                WHERE workspace_id = a.workspace_id
                  AND (alert_id = a.id OR (a.incident_id IS NOT NULL AND incident_id = a.incident_id))
                ORDER BY created_at DESC
                LIMIT 1
            ) ra ON TRUE
            WHERE workspace_id = %s
              AND (%s::text IS NULL OR severity = %s::text)
              AND (%s::text IS NULL OR module_key = %s::text)
              AND (%s::uuid IS NULL OR target_id = %s::uuid)
              AND (%s::text IS NULL OR status = %s::text)
              AND (%s::text IS NULL OR source = %s::text OR source_service = %s::text)
            ORDER BY created_at DESC
            LIMIT 200
            ''',
            (workspace_context['workspace_id'], severity, severity, module, module, target_id, target_id, status_value, status_value, source, source, source),
        ).fetchall()
        serialized_alerts: list[dict[str, Any]] = []
        for row in rows:
            item = _json_safe_value(dict(row))
            item['linked_evidence_count'] = int(item.get('linked_evidence_count') or 0)
            item['last_evidence_at'] = item.get('last_evidence_at')
            item['evidence_origin'] = item.get('evidence_origin')
            item['tx_hash'] = item.get('tx_hash')
            item['block_number'] = item.get('block_number')
            item['detector_kind'] = item.get('detector_kind')
            item['linked_detection_id'] = item.get('detection_id')
            item['linked_alert_id'] = item.get('id')
            item['linked_incident_id'] = item.get('incident_id')
            item['linked_action_id'] = item.get('linked_action_id')
            item['chain_linked_ids'] = {
                'detection_id': item.get('linked_detection_id'),
                'alert_id': item.get('linked_alert_id'),
                'incident_id': item.get('linked_incident_id'),
                'action_id': item.get('linked_action_id'),
            }
            serialized_alerts.append(item)
        return {'alerts': serialized_alerts}


def get_alert(alert_id: str, request: Request) -> dict[str, Any]:
    require_live_mode()
    with pg_connection() as connection:
        ensure_pilot_schema(connection)
        user = authenticate_with_connection(connection, request)
        workspace_context = resolve_workspace(connection, user['id'], request.headers.get('x-workspace-id'))
        row = connection.execute('SELECT * FROM alerts WHERE id = %s AND workspace_id = %s', (alert_id, workspace_context['workspace_id'])).fetchone()
        if row is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Alert not found.')
        events = connection.execute('SELECT id, event_type, details, created_at FROM alert_events WHERE alert_id = %s ORDER BY created_at DESC', (alert_id,)).fetchall()
        evidence = connection.execute(
            '''
            SELECT id, observed_at, event_type, severity, risk_score, summary, tx_hash, block_number, log_index, counterparty, amount_text, source_provider, created_at
            FROM evidence
            WHERE alert_id = %s
            ORDER BY observed_at DESC
            LIMIT 200
            ''',
            (alert_id,),
        ).fetchall()
        return {
            'alert': _json_safe_value(dict(row)),
            'events': [_json_safe_value(dict(item)) for item in events],
            'evidence_timeline': [_json_safe_value(dict(item)) for item in evidence],
            'evidence_count': len(evidence),
        }


def patch_alert(alert_id: str, payload: dict[str, Any], request: Request) -> dict[str, Any]:
    require_live_mode()
    next_status = str(payload.get('status', '')).strip().lower()
    if next_status not in {'open', 'acknowledged', 'investigating', 'resolved', 'suppressed'}:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='status must be open/acknowledged/investigating/resolved/suppressed')
    with pg_connection() as connection:
        ensure_pilot_schema(connection)
        user, workspace_context = _require_workspace_admin(connection, request)
        found = connection.execute('SELECT id FROM alerts WHERE id = %s AND workspace_id = %s', (alert_id, workspace_context['workspace_id'])).fetchone()
        if found is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Alert not found.')
        incident_id = payload.get('incident_id')
        connection.execute(
            '''
            UPDATE alerts
            SET status = %s,
                acknowledged_at = CASE WHEN %s = 'acknowledged' THEN NOW() ELSE acknowledged_at END,
                acknowledged_by_user_id = CASE WHEN %s = 'acknowledged' THEN %s ELSE acknowledged_by_user_id END,
                resolved_at = CASE WHEN %s = 'resolved' THEN NOW() ELSE resolved_at END,
                resolved_by_user_id = CASE WHEN %s = 'resolved' THEN %s ELSE resolved_by_user_id END,
                owner_user_id = COALESCE(%s::uuid, owner_user_id),
                assigned_to = COALESCE(%s::uuid, assigned_to),
                triage_status = CASE WHEN %s IN ('open', 'investigating', 'resolved', 'suppressed') THEN %s ELSE triage_status END,
                resolution_note = COALESCE(%s, resolution_note),
                evidence_summary = COALESCE(%s, evidence_summary),
                incident_id = COALESCE(%s::uuid, incident_id),
                suppressed_until = COALESCE(%s::timestamptz, suppressed_until),
                updated_at = NOW()
            WHERE id = %s
            ''',
            (
                next_status,
                next_status,
                next_status,
                user['id'],
                next_status,
                next_status,
                user['id'],
                payload.get('owner_user_id'),
                payload.get('assigned_to'),
                next_status,
                next_status,
                payload.get('resolution_note'),
                payload.get('evidence_summary'),
                incident_id,
                payload.get('suppressed_until'),
                alert_id,
            ),
        )
        if incident_id:
            connection.execute(
                '''
                UPDATE incidents
                SET source_alert_id = COALESCE(source_alert_id, %s::uuid),
                    linked_alert_ids = CASE
                        WHEN linked_alert_ids @> to_jsonb(ARRAY[%s::text]) THEN linked_alert_ids
                        ELSE linked_alert_ids || to_jsonb(ARRAY[%s::text])
                    END,
                    updated_at = NOW()
                WHERE id = %s::uuid
                  AND workspace_id = %s
                ''',
                (alert_id, alert_id, alert_id, incident_id, workspace_context['workspace_id']),
            )
        connection.execute('INSERT INTO alert_events (id, workspace_id, alert_id, actor_user_id, event_type, details) VALUES (%s, %s, %s, %s, %s, %s::jsonb)', (str(uuid.uuid4()), workspace_context['workspace_id'], alert_id, user['id'], f'alert.{next_status}', _json_dumps({'status': next_status, 'owner_user_id': payload.get('owner_user_id'), 'suppressed_until': payload.get('suppressed_until')})))
        write_action_history(
            connection,
            workspace_id=workspace_context['workspace_id'],
            actor_type='user',
            actor_id=user['id'],
            object_type='alert',
            object_id=alert_id,
            action_type=f'alert.{next_status}',
            details={
                'status': next_status,
                'incident_id': incident_id,
                'assigned_to': payload.get('assigned_to'),
                'owner_user_id': payload.get('owner_user_id'),
            },
        )
        if incident_id:
            write_action_history(
                connection,
                workspace_id=workspace_context['workspace_id'],
                actor_type='user',
                actor_id=user['id'],
                object_type='incident',
                object_id=str(incident_id),
                action_type='incident.linked_alert_updated',
                details={
                    'alert_id': alert_id,
                    'status': next_status,
                    'assigned_to': payload.get('assigned_to'),
                },
            )
        connection.commit()
        return {'id': alert_id, 'status': next_status}


def escalate_alert_to_incident(alert_id: str, payload: dict[str, Any], request: Request) -> dict[str, Any]:
    require_live_mode()
    with pg_connection() as connection:
        ensure_pilot_schema(connection)
        user, workspace_context = _require_workspace_admin(connection, request)
        alert = connection.execute(
            'SELECT id, target_id, analysis_run_id, title, severity, summary, detection_id FROM alerts WHERE id = %s AND workspace_id = %s',
            (alert_id, workspace_context['workspace_id']),
        ).fetchone()
        if alert is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Alert not found.')
        latest_evidence = connection.execute(
            '''
            SELECT id, tx_hash, observed_at
            FROM evidence
            WHERE workspace_id = %s
              AND alert_id = %s::uuid
            ORDER BY observed_at DESC, created_at DESC, id DESC
            LIMIT 1
            ''',
            (workspace_context['workspace_id'], alert_id),
        ).fetchone()
        incident_id = str(uuid.uuid4())
        title = str(payload.get('title') or f"Escalated alert: {alert.get('title') or alert_id}")
        summary = str(payload.get('summary') or alert.get('summary') or 'Escalated from alert')
        link_row = connection.execute(
            '''
            WITH inserted_incident AS (
                INSERT INTO incidents (id, workspace_id, user_id, analysis_run_id, target_id, event_type, title, severity, status, workflow_status, source_alert_id, owner, summary, linked_alert_ids, timeline, payload, created_at, updated_at)
                VALUES (%s, %s, %s, %s, %s, 'alert_escalation', %s, %s, 'open', 'open', %s::uuid, %s::uuid, %s, %s::jsonb, %s::jsonb, %s::jsonb, NOW(), NOW())
                RETURNING id
            )
            UPDATE alerts
            SET incident_id = inserted_incident.id,
                status = CASE WHEN status = 'resolved' THEN status ELSE 'investigating' END,
                updated_at = NOW()
            FROM inserted_incident
            WHERE alerts.id = %s
              AND alerts.workspace_id = %s
            RETURNING inserted_incident.id AS incident_id
            ''',
            (
                incident_id,
                workspace_context['workspace_id'],
                user['id'],
                alert.get('analysis_run_id'),
                alert.get('target_id'),
                title,
                str(alert.get('severity') or 'medium'),
                alert_id,
                user['id'],
                summary,
                _json_dumps([alert_id]),
                _json_dumps([{'event': 'incident.created_from_alert', 'at': datetime.now(timezone.utc).isoformat(), 'alert_id': alert_id}]),
                _json_dumps({'source': 'alert_escalation', 'alert_id': alert_id, 'detection_id': alert.get('detection_id')}),
                alert_id,
                workspace_context['workspace_id'],
            ),
        ).fetchone()
        if link_row is None:
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail='Unable to link alert and incident.')
        connection.execute('INSERT INTO alert_events (id, workspace_id, alert_id, actor_user_id, event_type, details) VALUES (%s, %s, %s, %s, %s, %s::jsonb)', (str(uuid.uuid4()), workspace_context['workspace_id'], alert_id, user['id'], 'alert.escalated', _json_dumps({'incident_id': incident_id})))
        write_action_history(
            connection,
            workspace_id=workspace_context['workspace_id'],
            actor_type='user',
            actor_id=user['id'],
            object_type='alert',
            object_id=alert_id,
            action_type='alert.escalated_to_incident',
            details={'incident_id': incident_id, 'detection_id': alert.get('detection_id')},
        )
        write_action_history(
            connection,
            workspace_id=workspace_context['workspace_id'],
            actor_type='user',
            actor_id=user['id'],
            object_type='incident',
            object_id=incident_id,
            action_type='incident.created_from_alert',
            details={'alert_id': alert_id, 'detection_id': alert.get('detection_id')},
        )
        write_action_history(
            connection,
            workspace_id=workspace_context['workspace_id'],
            actor_type='user',
            actor_id=user['id'],
            object_type='incident',
            object_id=incident_id,
            action_type='incident.action_recorded',
            details={'source_alert_id': alert_id, 'incident_id': incident_id, 'detection_id': alert.get('detection_id')},
        )
        append_incident_timeline_event(
            connection,
            workspace_id=workspace_context['workspace_id'],
            incident_id=incident_id,
            event_type='alert.escalated',
            message='Alert escalated to incident.',
            actor_user_id=user['id'],
            metadata={
                'alert_id': alert_id,
                'detection_id': alert.get('detection_id'),
                'evidence_reference': {
                    'evidence_id': str(latest_evidence.get('id') or '') if latest_evidence is not None else None,
                    'tx_hash': latest_evidence.get('tx_hash') if latest_evidence is not None else None,
                    'observed_at': latest_evidence.get('observed_at') if latest_evidence is not None else None,
                },
            },
        )
        if latest_evidence is not None:
            append_incident_timeline_event(
                connection,
                workspace_id=workspace_context['workspace_id'],
                incident_id=incident_id,
                event_type='evidence.linked',
                message='Latest alert evidence linked to incident timeline.',
                actor_user_id=user['id'],
                metadata={
                    'alert_id': alert_id,
                    'detection_id': alert.get('detection_id'),
                    'external_references': {
                        'safe_tx_hash': latest_evidence.get('tx_hash'),
                        'governance_action_id': None,
                        'attestation_hash': None,
                    },
                    'evidence_reference': {
                        'evidence_id': str(latest_evidence.get('id') or ''),
                        'tx_hash': latest_evidence.get('tx_hash'),
                        'observed_at': latest_evidence.get('observed_at'),
                    },
                },
            )
        connection.commit()
        return {'incident_id': incident_id, 'alert_id': alert_id, 'status': 'open'}


def create_alert_suppression(payload: dict[str, Any], request: Request) -> dict[str, Any]:
    require_live_mode()
    with pg_connection() as connection:
        ensure_pilot_schema(connection)
        user, workspace_context = _require_workspace_admin(connection, request)
        suppression_id = str(uuid.uuid4())
        connection.execute(
            '''
            INSERT INTO alert_suppression_rules (id, workspace_id, target_id, dedupe_signature, trusted_sender, trusted_spender, trusted_contract, mute_until, reason, created_by_user_id)
            VALUES (%s, %s, %s::uuid, %s, %s, %s, %s, %s::timestamptz, %s, %s)
            ''',
            (
                suppression_id,
                workspace_context['workspace_id'],
                payload.get('target_id'),
                payload.get('dedupe_signature'),
                payload.get('trusted_sender'),
                payload.get('trusted_spender'),
                payload.get('trusted_contract'),
                payload.get('mute_until'),
                payload.get('reason'),
                user['id'],
            ),
        )
        log_audit(connection, action='alert.suppression.create', entity_type='alert_suppression_rule', entity_id=suppression_id, request=request, user_id=user['id'], workspace_id=workspace_context['workspace_id'], metadata={})
        connection.commit()
        return {'id': suppression_id, 'created': True}


def list_alert_evidence(alert_id: str, request: Request) -> dict[str, Any]:
    require_live_mode()
    with pg_connection() as connection:
        ensure_pilot_schema(connection)
        user = authenticate_with_connection(connection, request)
        workspace_context = resolve_workspace(connection, user['id'], request.headers.get('x-workspace-id'))
        row = connection.execute('SELECT id, target_id, payload, reasons, matched_patterns FROM alerts WHERE id = %s AND workspace_id = %s', (alert_id, workspace_context['workspace_id'])).fetchone()
        if row is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Alert not found.')
        payload = row['payload'] if isinstance(row['payload'], dict) else {}
        evidence_rows = connection.execute(
            '''
            WITH latest AS (
                SELECT MAX(observed_at) AS observed_at
                FROM evidence
                WHERE workspace_id = %s AND alert_id = %s
            )
            SELECT id, alert_id, observed_at, target_id, monitored_system_id, asset_id, event_type, severity, risk_score, summary, source_provider
            FROM evidence
            WHERE workspace_id = %s
              AND alert_id = %s
              AND observed_at = (SELECT observed_at FROM latest)
            ORDER BY created_at DESC, id DESC
            ''',
            (workspace_context['workspace_id'], alert_id, workspace_context['workspace_id'], alert_id),
        ).fetchall()
        latest_evidence = dict(evidence_rows[0]) if evidence_rows else {}
        source_provider = str(latest_evidence.get('source_provider') or '').strip().lower()
        source_label = 'simulator' if source_provider.startswith('simulator') else 'live'
        target = connection.execute('SELECT id, name FROM targets WHERE id = %s', (row['target_id'],)).fetchone() if row.get('target_id') else None
        return {
            'alert_id': alert_id,
            'evidence': {
                'tx_hash': payload.get('tx_hash'),
                'block_number': payload.get('block_number'),
                'observed_at': latest_evidence.get('observed_at'),
                'event_type': latest_evidence.get('event_type'),
                'severity': latest_evidence.get('severity'),
                'risk_score': latest_evidence.get('risk_score'),
                'summary': latest_evidence.get('summary'),
                'source_provider': latest_evidence.get('source_provider'),
                'source_label': source_label if latest_evidence else None,
                'alert_id': str(latest_evidence.get('alert_id') or alert_id),
                'target_id': str(row.get('target_id') or ''),
                'monitored_system_id': str(latest_evidence.get('monitored_system_id') or ''),
                'asset_id': str(latest_evidence.get('asset_id') or ''),
                'target_name': str(target['name']) if target is not None else '',
                'matched_patterns': row.get('matched_patterns') or [],
                'reasons': row.get('reasons') or [],
                'raw_payload_excerpt': payload,
            },
            'linked_evidence': [
                {
                    'id': str(item.get('id') or ''),
                    'alert_id': str(item.get('alert_id') or alert_id),
                    'observed_at': item.get('observed_at'),
                    'target_id': str(item.get('target_id') or ''),
                    'monitored_system_id': str(item.get('monitored_system_id') or ''),
                    'asset_id': str(item.get('asset_id') or ''),
                    'event_type': item.get('event_type'),
                    'severity': item.get('severity'),
                    'risk_score': item.get('risk_score'),
                    'summary': item.get('summary'),
                    'source_provider': item.get('source_provider'),
                    'source_label': 'simulator' if str(item.get('source_provider') or '').strip().lower().startswith('simulator') else 'live',
                }
                for item in evidence_rows
            ],
        }


def list_incidents(request: Request, *, severity: str | None = None, target_id: str | None = None, status_value: str | None = None, assignee_user_id: str | None = None) -> dict[str, Any]:
    require_live_mode()
    with pg_connection() as connection:
        ensure_pilot_schema(connection)
        user = authenticate_with_connection(connection, request)
        workspace_context = resolve_workspace(connection, user['id'], request.headers.get('x-workspace-id'))
        rows = connection.execute(
            '''
            SELECT
                i.id, i.event_type, i.title, i.severity, i.status, i.workflow_status, i.target_id, i.source_alert_id, i.linked_alert_ids, i.owner, i.owner_user_id,
                i.assignee_user_id, i.summary, i.resolution_note, i.resolution_notes, i.timeline, i.created_at, i.updated_at,
                sa.detection_id AS linked_detection_id,
                ev_stats.linked_evidence_count,
                ev_latest.last_evidence_at,
                ev_latest.evidence_source,
                ev_latest.tx_hash,
                ev_latest.block_number,
                COALESCE(ev_latest.raw_payload_json->>'detector_kind', ev_latest.raw_payload_json->>'detector_family') AS detector_kind,
                COALESCE(ev_latest.raw_payload_json->>'evidence_origin', ev_latest.evidence_source) AS evidence_origin,
                ra.id AS linked_action_id,
                (
                    SELECT ra.mode
                    FROM response_actions ra
                    WHERE ra.workspace_id = i.workspace_id AND ra.incident_id = i.id
                    ORDER BY ra.created_at DESC
                    LIMIT 1
                ) AS response_action_mode
            FROM incidents i
            LEFT JOIN alerts sa ON sa.id = i.source_alert_id AND sa.workspace_id = i.workspace_id
            LEFT JOIN LATERAL (
                SELECT COUNT(*)::int AS linked_evidence_count
                FROM evidence e
                WHERE e.workspace_id = i.workspace_id
                  AND (
                    e.alert_id = i.source_alert_id
                    OR (e.alert_id IS NOT NULL AND i.linked_alert_ids @> to_jsonb(ARRAY[e.alert_id::text]))
                  )
            ) ev_stats ON TRUE
            LEFT JOIN LATERAL (
                SELECT e.observed_at AS last_evidence_at,
                       e.source_provider AS evidence_source,
                       e.tx_hash,
                       e.block_number,
                       e.raw_payload_json
                FROM evidence e
                WHERE e.workspace_id = i.workspace_id
                  AND (
                    e.alert_id = i.source_alert_id
                    OR (e.alert_id IS NOT NULL AND i.linked_alert_ids @> to_jsonb(ARRAY[e.alert_id::text]))
                  )
                ORDER BY e.observed_at DESC, e.created_at DESC, e.id DESC
                LIMIT 1
            ) ev_latest ON TRUE
            LEFT JOIN LATERAL (
                SELECT id
                FROM response_actions
                WHERE workspace_id = i.workspace_id
                  AND incident_id = i.id
                ORDER BY created_at DESC
                LIMIT 1
            ) ra ON TRUE
            WHERE workspace_id = %s
              AND (%s::text IS NULL OR severity = %s::text)
              AND (%s::uuid IS NULL OR target_id = %s::uuid)
              AND (%s::text IS NULL OR workflow_status = %s::text OR status = %s::text)
              AND (%s::uuid IS NULL OR assignee_user_id = %s::uuid)
            ORDER BY created_at DESC
            LIMIT 200
            ''',
            (workspace_context['workspace_id'], severity, severity, target_id, target_id, status_value, status_value, status_value, assignee_user_id, assignee_user_id),
        ).fetchall()
        serialized_incidents: list[dict[str, Any]] = []
        for row in rows:
            item = _json_safe_value(dict(row))
            item['linked_evidence_count'] = int(item.get('linked_evidence_count') or 0)
            item['last_evidence_at'] = item.get('last_evidence_at')
            item['evidence_origin'] = item.get('evidence_origin')
            item['tx_hash'] = item.get('tx_hash')
            item['block_number'] = item.get('block_number')
            item['detector_kind'] = item.get('detector_kind')
            item['linked_alert_id'] = item.get('source_alert_id')
            item['linked_incident_id'] = item.get('id')
            item['linked_action_id'] = item.get('linked_action_id')
            item['chain_linked_ids'] = {
                'detection_id': item.get('linked_detection_id'),
                'alert_id': item.get('linked_alert_id'),
                'incident_id': item.get('linked_incident_id'),
                'action_id': item.get('linked_action_id'),
            }
            serialized_incidents.append(item)
        return {'incidents': serialized_incidents}


def patch_incident(incident_id: str, payload: dict[str, Any], request: Request) -> dict[str, Any]:
    require_live_mode()
    next_workflow_status = _normalize_incident_status(payload.get('workflow_status', payload.get('status', '')))
    if next_workflow_status not in {'open', 'investigating', 'contained', 'resolved', 'reopened'}:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='workflow_status must be open/investigating/contained/resolved/reopened.')
    with pg_connection() as connection:
        ensure_pilot_schema(connection)
        user, workspace_context = _require_workspace_admin(connection, request)
        found = connection.execute(
            'SELECT id, timeline, workflow_status, assignee_user_id, resolution_note FROM incidents WHERE id = %s AND workspace_id = %s',
            (incident_id, workspace_context['workspace_id']),
        ).fetchone()
        if found is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Incident not found.')
        timeline = found.get('timeline') if isinstance(found.get('timeline'), list) else []
        timeline.append({'event': f'incident.{next_workflow_status}', 'at': utc_now_iso(), 'actor_user_id': user['id']})
        connection.execute(
            '''
            UPDATE incidents
            SET status = %s,
                workflow_status = %s,
                resolved_at = CASE WHEN %s = 'resolved' THEN NOW() WHEN %s = 'reopened' THEN NULL ELSE resolved_at END,
                owner_user_id = COALESCE(%s, owner_user_id),
                owner = COALESCE(%s, owner),
                assignee_user_id = COALESCE(%s, assignee_user_id),
                resolution_note = COALESCE(%s, resolution_note),
                resolution_notes = COALESCE(%s, resolution_notes),
                timeline = %s::jsonb,
                updated_at = NOW()
            WHERE id = %s
            ''',
            (
                next_workflow_status,
                next_workflow_status,
                next_workflow_status,
                next_workflow_status,
                payload.get('owner_user_id'),
                payload.get('owner'),
                payload.get('assignee_user_id'),
                payload.get('resolution_note'),
                payload.get('resolution_notes'),
                _json_dumps(timeline),
                incident_id,
            ),
        )
        source_alert_id = payload.get('source_alert_id')
        if source_alert_id:
            connection.execute(
                '''
                UPDATE incidents
                SET source_alert_id = COALESCE(source_alert_id, %s::uuid),
                    linked_alert_ids = CASE
                        WHEN linked_alert_ids @> to_jsonb(ARRAY[%s::text]) THEN linked_alert_ids
                        ELSE linked_alert_ids || to_jsonb(ARRAY[%s::text])
                    END,
                    updated_at = NOW()
                WHERE id = %s
                ''',
                (source_alert_id, source_alert_id, source_alert_id, incident_id),
            )
            connection.execute(
                'UPDATE alerts SET incident_id = %s::uuid, updated_at = NOW() WHERE id = %s::uuid AND workspace_id = %s',
                (incident_id, source_alert_id, workspace_context['workspace_id']),
            )
        connection.execute(
            '''
            INSERT INTO incident_timeline (id, workspace_id, incident_id, event_type, message, actor_user_id, metadata, created_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb, NOW())
            ''',
            (
                str(uuid.uuid4()),
                workspace_context['workspace_id'],
                incident_id,
                'action_executed',
                f'Workflow moved to {next_workflow_status}.',
                user['id'],
                _json_dumps({'workflow_status': next_workflow_status, 'assignee_user_id': payload.get('assignee_user_id')}),
            ),
        )
        log_audit(connection, action='incident.updated', entity_type='incident', entity_id=incident_id, request=request, user_id=user['id'], workspace_id=workspace_context['workspace_id'], metadata={'workflow_status': next_workflow_status, 'assignee_user_id': payload.get('assignee_user_id')})
        write_action_history(
            connection,
            workspace_id=workspace_context['workspace_id'],
            actor_type='user',
            actor_id=user['id'],
            object_type='incident',
            object_id=incident_id,
            action_type=f'incident.{next_workflow_status}',
            details={
                'workflow_status': next_workflow_status,
                'assignee_user_id': payload.get('assignee_user_id'),
                'owner': payload.get('owner'),
                'source_alert_id': source_alert_id,
            },
        )
        if source_alert_id:
            write_action_history(
                connection,
                workspace_id=workspace_context['workspace_id'],
                actor_type='user',
                actor_id=user['id'],
                object_type='alert',
                object_id=str(source_alert_id),
                action_type='alert.linked_incident_updated',
                details={'incident_id': incident_id, 'workflow_status': next_workflow_status},
            )
        connection.commit()
        return {'id': incident_id, 'workflow_status': next_workflow_status, 'assignee_user_id': payload.get('assignee_user_id'), 'resolution_note': payload.get('resolution_note')}


def list_incident_timeline(incident_id: str, request: Request) -> dict[str, Any]:
    require_live_mode()
    with pg_connection() as connection:
        ensure_pilot_schema(connection)
        user = authenticate_with_connection(connection, request)
        workspace_context = resolve_workspace(connection, user['id'], request.headers.get('x-workspace-id'))
        found = connection.execute('SELECT id FROM incidents WHERE id = %s AND workspace_id = %s', (incident_id, workspace_context['workspace_id'])).fetchone()
        if found is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Incident not found.')
        rows = connection.execute(
            '''
            SELECT id, incident_id, event_type, message, actor_user_id, metadata, created_at
            FROM incident_timeline
            WHERE workspace_id = %s
              AND incident_id = %s
            ORDER BY created_at DESC
            LIMIT 500
            ''',
            (workspace_context['workspace_id'], incident_id),
        ).fetchall()
        return {'incident_id': incident_id, 'timeline': [_json_safe_value(dict(row)) for row in rows]}


def list_action_history(request: Request, *, object_type: str | None = None, object_id: str | None = None, limit: int = 200) -> dict[str, Any]:
    require_live_mode()
    max_limit = max(1, min(limit, 500))
    normalized_type = str(object_type or '').strip().lower() or None
    with pg_connection() as connection:
        ensure_pilot_schema(connection)
        user = authenticate_with_connection(connection, request)
        workspace_context = resolve_workspace(connection, user['id'], request.headers.get('x-workspace-id'))
        rows = connection.execute(
            '''
            SELECT id, actor_type, actor_id, object_type, object_id, action_type, timestamp, details_json
            FROM action_history
            WHERE workspace_id = %s
              AND (%s::text IS NULL OR object_type = %s::text)
              AND (%s::text IS NULL OR object_id = %s::text)
            ORDER BY timestamp DESC
            LIMIT %s
            ''',
            (workspace_context['workspace_id'], normalized_type, normalized_type, object_id, object_id, max_limit),
        ).fetchall()
        return {'history': [_json_safe_value(dict(row)) for row in rows]}


def append_incident_timeline_note(incident_id: str, payload: dict[str, Any], request: Request) -> dict[str, Any]:
    require_live_mode()
    message = str(payload.get('message') or '').strip()
    if not message:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='message is required.')
    with pg_connection() as connection:
        ensure_pilot_schema(connection)
        user, workspace_context = _require_workspace_admin(connection, request)
        found = connection.execute('SELECT id FROM incidents WHERE id = %s AND workspace_id = %s', (incident_id, workspace_context['workspace_id'])).fetchone()
        if found is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Incident not found.')
        timeline_id = str(uuid.uuid4())
        metadata = payload.get('metadata') if isinstance(payload.get('metadata'), dict) else {}
        connection.execute(
            '''
            INSERT INTO incident_timeline (id, workspace_id, incident_id, event_type, message, actor_user_id, metadata, created_at)
            VALUES (%s, %s, %s, 'note', %s, %s, %s::jsonb, NOW())
            ''',
            (timeline_id, workspace_context['workspace_id'], incident_id, message, user['id'], _json_dumps(metadata)),
        )
        connection.execute(
            'UPDATE incidents SET updated_at = NOW(), timeline = timeline || %s::jsonb WHERE id = %s',
            (_json_dumps([{'event': 'note', 'message': message, 'at': utc_now_iso(), 'actor_user_id': user['id']}]), incident_id),
        )
        log_audit(connection, action='incident.timeline_note_added', entity_type='incident_timeline', entity_id=timeline_id, request=request, user_id=user['id'], workspace_id=workspace_context['workspace_id'], metadata={'incident_id': incident_id, 'event_type': 'note'})
        write_action_history(
            connection,
            workspace_id=workspace_context['workspace_id'],
            actor_type='user',
            actor_id=user['id'],
            object_type='incident',
            object_id=incident_id,
            action_type='incident.timeline_note_added',
            details={'timeline_id': timeline_id, 'message': message},
        )
        connection.commit()
        return {'id': timeline_id, 'incident_id': incident_id, 'event_type': 'note', 'message': message}


RESPONSE_ACTION_TYPES = {
    'freeze_wallet',
    'block_transaction',
    'revoke_approval',
    'disable_monitored_system',
    'suppress_rule',
    'notify_team',
}
RESPONSE_ACTION_SUPPORTED_MODES = ('simulated', 'recommended', 'live')
RESPONSE_ACTION_LIVE_EXECUTION_PATHS = {'safe', 'governance', 'manual_only', 'unsupported'}
RESPONSE_ACTION_LIVE_EXECUTION_DEFAULTS: dict[str, tuple[str, str | None]] = {
    'notify_team': ('governance', None),
    'disable_monitored_system': ('manual_only', 'Manual-only in live mode'),
    'suppress_rule': ('manual_only', 'Manual-only in live mode'),
    'freeze_wallet': ('governance', None),
    'block_transaction': ('unsupported', 'Unsupported live action'),
}
LEGACY_ACTION_TYPE_ALIASES = {
    'revoke_erc20_approval': 'revoke_approval',
    'pause_asset': 'disable_monitored_system',
    'notify_only': 'notify_team',
    'compensating_reapprove_erc20_approval': 'revoke_approval',
}
ENFORCEMENT_STATUSES = {'pending', 'executed', 'failed', 'canceled'}


def _normalize_eth_address(value: str | None, *, field: str) -> str | None:
    normalized = str(value or '').strip()
    if not normalized:
        return None
    if not re.fullmatch(r'0x[a-fA-F0-9]{40}', normalized):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f'{field} must be a 20-byte hex address.')
    return normalized.lower()


def _encode_erc20_approve_calldata(spender: str, amount: int) -> str:
    if amount < 0:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='amount must be >= 0.')
    spender_hex = spender[2:].lower()
    spender_word = spender_hex.rjust(64, '0')
    amount_word = format(amount, 'x').rjust(64, '0')
    # selector approve(address,uint256) = 0x095ea7b3
    return f'0x095ea7b3{spender_word}{amount_word}'


def _enforcement_default_dry_run() -> bool:
    return env_flag('ENFORCEMENT_DRY_RUN_DEFAULT', default=True)


def _safe_execution_configured() -> bool:
    service_url = os.getenv('SAFE_TX_SERVICE_URL', '').strip()
    safe_wallet = os.getenv('SAFE_WALLET_ADDRESS', '').strip()
    return bool(service_url and safe_wallet)


def response_action_capability(action_type: str) -> dict[str, Any]:
    normalized_action_type = _normalize_response_action_type(action_type)
    live_execution_path = 'unsupported'
    reason: str | None = 'Unsupported live action'
    supported_modes = list(RESPONSE_ACTION_SUPPORTED_MODES)

    if normalized_action_type == 'revoke_approval':
        if _safe_execution_configured():
            live_execution_path = 'safe'
            reason = None
        else:
            live_execution_path = 'manual_only'
            reason = 'Manual-only in live mode'
    elif normalized_action_type in RESPONSE_ACTION_LIVE_EXECUTION_DEFAULTS:
        live_execution_path, reason = RESPONSE_ACTION_LIVE_EXECUTION_DEFAULTS[normalized_action_type]

    if live_execution_path == 'unsupported':
        supported_modes = ['simulated', 'recommended']

    return {
        'action_type': normalized_action_type,
        'supported_modes': supported_modes,
        'live_execution_path': live_execution_path,
        'reason': reason,
    }


def resolve_response_action_capability(action_type: str, mode: str | None = None) -> dict[str, Any]:
    capability = response_action_capability(action_type)
    resolved_mode = str(mode or '').strip().lower() or None
    supports_mode = resolved_mode is None or resolved_mode in set(capability.get('supported_modes') or [])
    return {
        **capability,
        'supports_mode': supports_mode,
        'mode': resolved_mode,
    }


def list_response_action_capabilities(request: Request) -> dict[str, Any]:
    require_live_mode()
    with pg_connection() as connection:
        ensure_pilot_schema(connection)
        user = authenticate_with_connection(connection, request)
        workspace_context = resolve_workspace(connection, user['id'], request.headers.get('x-workspace-id'))
        actions = [resolve_response_action_capability(action_type) for action_type in sorted(RESPONSE_ACTION_TYPES)]
        return {'workspace_id': workspace_context['workspace_id'], 'actions': actions}


def _normalize_response_action_type(value: Any) -> str:
    action_type = str(value or '').strip().lower()
    return LEGACY_ACTION_TYPE_ALIASES.get(action_type, action_type)


def _normalize_response_action_mode(payload: dict[str, Any]) -> str:
    mode = str(payload.get('mode') or '').strip().lower()
    if not mode:
        dry_run = payload.get('dry_run')
        if dry_run is None:
            dry_run = _enforcement_default_dry_run()
        mode = 'simulated' if bool(dry_run) else 'live'
    if mode == 'live_enforcement':
        mode = 'live'
    if mode not in {'simulated', 'recommended', 'live'}:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='mode must be simulated/recommended/live.')
    return mode


def _response_action_payload(action: dict[str, Any]) -> dict[str, Any]:
    mode = str(action.get('mode') or 'simulated')
    result = dict(action)
    result['dry_run'] = mode != 'live'
    result['is_simulated'] = mode != 'live'
    return result


def _normalize_response_action_status(value: Any) -> str:
    status_value = str(value or '').strip().lower()
    if status_value in {'', 'pending'}:
        return 'pending'
    if status_value in {'planned', 'approved'}:
        return 'pending'
    if status_value == 'rolled_back':
        return 'canceled'
    if status_value in {'executed', 'failed', 'canceled'}:
        return status_value
    raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='status must be pending/executed/failed/canceled.')


def _fallback_governance_action(payload: dict[str, Any]) -> dict[str, Any]:
    action_type = str(payload.get('action_type') or 'governance_action')
    target_id = str(payload.get('target_id') or payload.get('target_wallet') or 'target')
    return {
        **payload,
        'action_id': f'gov-fallback-{secrets.token_hex(6)}',
        'created_at': utc_now_iso(),
        'status': 'submitted',
        'attestation_hash': f'fallback-{action_type}-{target_id}',
        'policy_effects': [f'Fallback governance action {action_type} submitted for {target_id}.'],
        'source': 'fallback',
        'degraded': True,
    }


def _submit_freeze_wallet_governance_action(action: dict[str, Any], workspace_context: dict[str, Any], user: dict[str, Any]) -> dict[str, Any]:
    payload = {
        'action_type': 'freeze_wallet',
        'target_type': 'wallet',
        'target_id': action.get('target_wallet') or action.get('id'),
        'target_wallet': action.get('target_wallet'),
        'workspace_id': workspace_context.get('workspace_id'),
        'operator_user_id': user.get('id'),
        'reason': action.get('operator_notes') or 'Submitted from response action execution flow.',
    }
    compliance_service_url = os.getenv('COMPLIANCE_SERVICE_URL', '').strip().rstrip('/')
    if not compliance_service_url:
        return _fallback_governance_action(payload)
    request = UrlRequest(
        f'{compliance_service_url}/governance/actions',
        data=_json_dumps(payload).encode('utf-8'),
        headers={'Content-Type': 'application/json'},
        method='POST',
    )
    try:
        with urlopen(request, timeout=10) as response:
            return _json_safe_value(json.loads(response.read().decode('utf-8')))
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError):
        return _fallback_governance_action(payload)


def _safe_signer_key() -> str:
    return read_encrypted_env('SAFE_SIGNER_KEY', aad='safe-signer-key') or read_encrypted_env('SAFE_SIGNER_KEY_ENCRYPTED', aad='safe-signer-key')


def _propose_safe_transaction(action_id: str, *, to: str, data: str, chain_network: str | None = None) -> str:
    service_url = os.getenv('SAFE_TX_SERVICE_URL', '').strip().rstrip('/')
    safe_wallet = _normalize_eth_address(os.getenv('SAFE_WALLET_ADDRESS', '').strip(), field='SAFE_WALLET_ADDRESS')
    if not service_url or not safe_wallet:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail='SAFE_TX_SERVICE_URL and SAFE_WALLET_ADDRESS are required for Safe execution.')
    payload = {
        'to': to,
        'value': '0',
        'data': data,
        'operation': 0,
        'safeTxGas': 0,
        'baseGas': 0,
        'gasPrice': '0',
        'gasToken': '0x0000000000000000000000000000000000000000',
        'refundReceiver': '0x0000000000000000000000000000000000000000',
        'nonce': int(utc_now().timestamp()),
        'contractTransactionHash': hashlib.sha256(f'{action_id}:{to}:{data}'.encode('utf-8')).hexdigest(),
        'sender': safe_wallet,
        'signature': _safe_signer_key() or '0x',
        'origin': _json_dumps({'source': 'decoda-rwa-guard', 'action_id': action_id, 'chain_network': chain_network}),
    }
    request = UrlRequest(
        f'{service_url}/api/v1/safes/{safe_wallet}/multisig-transactions/',
        data=_json_dumps(payload).encode('utf-8'),
        headers={'Content-Type': 'application/json'},
        method='POST',
    )
    try:
        with urlopen(request, timeout=10) as response:
            data = json.loads(response.read().decode('utf-8')) if response.readable() else {}
    except (HTTPError, URLError) as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=f'Safe transaction proposal failed: {exc}') from exc
    safe_tx_hash = str(data.get('safeTxHash') or data.get('safe_tx_hash') or data.get('contractTransactionHash') or '').strip()
    if not safe_tx_hash:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail='Safe transaction proposal did not return safeTxHash.')
    return safe_tx_hash


def create_enforcement_action(payload: dict[str, Any], request: Request) -> dict[str, Any]:
    require_live_mode()
    action_type = _normalize_response_action_type(payload.get('action_type'))
    if action_type not in RESPONSE_ACTION_TYPES:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='Unsupported action_type.')
    params = payload.get('params') if isinstance(payload.get('params'), dict) else {}
    with pg_connection() as connection:
        ensure_pilot_schema(connection)
        user, workspace_context = _require_workspace_admin(connection, request)
        action_id = str(uuid.uuid4())
        incident_id = payload.get('incident_id')
        alert_id = payload.get('alert_id')
        chain_network = str(params.get('chain_network') or '').strip() or None
        token_contract = _normalize_eth_address(params.get('token_contract'), field='token_contract')
        spender = _normalize_eth_address(params.get('spender'), field='spender')
        target_wallet = _normalize_eth_address(params.get('target_wallet'), field='target_wallet')
        mode = _normalize_response_action_mode(payload)
        capability = resolve_response_action_capability(action_type, mode)
        if not capability.get('supports_mode'):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=str(capability.get('reason') or 'Selected mode is not supported for this action.'),
            )
        status_value = _normalize_response_action_status(payload.get('status'))
        execution_metadata = {'params': params, 'created_via': 'api'}
        calldata: str | None = None
        if action_type == 'revoke_approval':
            if not token_contract or not spender:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='token_contract and spender are required for ERC20 approval actions.')
            amount = int(params.get('amount') or 0)
            calldata = _encode_erc20_approve_calldata(spender, amount)
            execution_metadata['erc20_approve_amount'] = str(amount)
            if params.get('previous_allowance') is not None:
                execution_metadata['previous_allowance'] = str(params.get('previous_allowance'))
        external_references = {
            'safe_tx_hash': params.get('safe_tx_hash'),
            'governance_action_id': params.get('governance_action_id'),
            'attestation_hash': params.get('attestation_hash'),
        }
        connection.execute(
            '''
            INSERT INTO response_actions (
                id, workspace_id, incident_id, alert_id, action_type, mode, status, result_summary, operator_notes,
                chain_network, target_wallet, token_contract, spender, calldata,
                execution_state, execution_metadata, created_by_user_id
            )
            VALUES (%s, %s, %s::uuid, %s::uuid, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s)
            ''',
            (
                action_id,
                workspace_context['workspace_id'],
                incident_id,
                alert_id,
                action_type,
                mode,
                status_value,
                str(payload.get('result_summary') or '').strip() or None,
                str(payload.get('operator_notes') or '').strip() or None,
                chain_network,
                target_wallet,
                token_contract,
                spender,
                calldata,
                'proposed' if mode == 'live' else 'simulated_executed',
                _json_dumps(execution_metadata),
                user['id'],
            ),
        )
        write_action_history(
            connection,
            workspace_id=workspace_context['workspace_id'],
            actor_type='user',
            actor_id=user['id'],
            object_type='response_action',
            object_id=action_id,
            action_type='response_action.created',
            details={'action_type': action_type, 'mode': mode, 'incident_id': incident_id, 'alert_id': alert_id},
        )
        if incident_id:
            write_action_history(
                connection,
                workspace_id=workspace_context['workspace_id'],
                actor_type='user',
                actor_id=user['id'],
                object_type='incident',
                object_id=str(incident_id),
                action_type='incident.response_action_created',
                details={'response_action_id': action_id, 'action_type': action_type, 'mode': mode},
            )
            append_incident_timeline_event(
                connection,
                workspace_id=workspace_context['workspace_id'],
                incident_id=str(incident_id),
                event_type='response_action.created',
                message='Response action created.',
                actor_user_id=user['id'],
                metadata={
                    'response_action_id': action_id,
                    'action_type': action_type,
                    'mode': mode,
                    'status': status_value,
                    'alert_id': alert_id,
                    'external_references': external_references,
                },
            )
        if alert_id:
            write_action_history(
                connection,
                workspace_id=workspace_context['workspace_id'],
                actor_type='user',
                actor_id=user['id'],
                object_type='alert',
                object_id=str(alert_id),
                action_type='alert.response_action_created',
                details={'response_action_id': action_id, 'action_type': action_type, 'mode': mode},
            )
        log_audit(connection, action='enforcement.action.create', entity_type='response_action', entity_id=action_id, request=request, user_id=user['id'], workspace_id=workspace_context['workspace_id'], metadata={'action_type': action_type, 'mode': mode})
        connection.commit()
        logger.info('response_action_created action_id=%s type=%s mode=%s status=%s', action_id, action_type, mode, status_value)
        return _response_action_payload(
            {
                'id': action_id,
                'status': status_value,
                'action_type': action_type,
                'mode': mode,
                'calldata': calldata,
                'execution_state': 'proposed' if mode == 'live' else 'simulated_executed',
                'live_execution_path': capability.get('live_execution_path'),
            }
        )


def approve_enforcement_action(action_id: str, request: Request) -> dict[str, Any]:
    require_live_mode()
    with pg_connection() as connection:
        ensure_pilot_schema(connection)
        user, workspace_context = _require_workspace_admin(connection, request)
        row = connection.execute(
            'SELECT id, status, incident_id, alert_id, action_type, mode FROM response_actions WHERE id = %s AND workspace_id = %s',
            (action_id, workspace_context['workspace_id']),
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Enforcement action not found.')
        if str(row.get('status')) not in {'pending', 'failed', 'planned'}:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail='Action cannot be approved from current status.')
        connection.execute('UPDATE response_actions SET status = %s, approved_by_user_id = %s, execution_metadata = execution_metadata || %s::jsonb WHERE id = %s', ('pending', user['id'], _json_dumps({'approved_at': utc_now_iso()}), action_id))
        write_action_history(
            connection,
            workspace_id=workspace_context['workspace_id'],
            actor_type='user',
            actor_id=user['id'],
            object_type='response_action',
            object_id=action_id,
            action_type='response_action.approved',
            details={},
        )
        append_incident_timeline_event(
            connection,
            workspace_id=workspace_context['workspace_id'],
            incident_id=str(row.get('incident_id') or ''),
            event_type='response_action.approved',
            message='Response action approved.',
            actor_user_id=user['id'],
            metadata={
                'response_action_id': action_id,
                'action_type': row.get('action_type'),
                'mode': row.get('mode'),
                'alert_id': row.get('alert_id'),
            },
        )
        log_audit(connection, action='enforcement.action.approve', entity_type='enforcement_action', entity_id=action_id, request=request, user_id=user['id'], workspace_id=workspace_context['workspace_id'], metadata={})
        connection.commit()
        return {'id': action_id, 'status': 'pending'}


def execute_enforcement_action(action_id: str, request: Request) -> dict[str, Any]:
    require_live_mode()
    with pg_connection() as connection:
        ensure_pilot_schema(connection)
        user, workspace_context = _require_workspace_admin(connection, request)
        row = connection.execute('SELECT * FROM response_actions WHERE id = %s AND workspace_id = %s', (action_id, workspace_context['workspace_id'])).fetchone()
        if row is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Enforcement action not found.')
        if str(row.get('status')) not in {'pending', 'approved', 'planned'}:
            logger.warning('execute_blocked_invalid_status action_id=%s status=%s', action_id, row.get('status'))
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail='Action must be pending before execute.')
        action = _json_safe_value(dict(row))
        safe_tx_hash = None
        metadata = action.get('execution_metadata') if isinstance(action.get('execution_metadata'), dict) else {}
        capability = resolve_response_action_capability(str(action.get('action_type') or ''), str(action.get('mode') or ''))
        execution_state = 'simulated_executed'
        next_status = 'executed'
        result_summary = 'Action executed in simulation mode.'
        if str(action.get('mode') or 'simulated') != 'live':
            metadata['execution_mode'] = 'simulated'
        elif capability.get('live_execution_path') == 'safe':
            safe_tx_hash = _propose_safe_transaction(
                action_id,
                to=str(action.get('token_contract') or ''),
                data=str(action.get('calldata') or ''),
                chain_network=str(action.get('chain_network') or ''),
            )
            logger.info('enforcement_proposed_safe_tx action_id=%s safe_tx_hash=%s', action_id, safe_tx_hash)
            metadata['execution_mode'] = 'safe_proposed'
            metadata['execution_state'] = 'proposed'
            metadata['safe_tx_hash'] = safe_tx_hash
            metadata['proposal_timestamp'] = utc_now_iso()
            metadata['proposal_operator_user_id'] = user['id']
            execution_state = 'proposed'
            next_status = 'pending'
            result_summary = 'Safe transaction proposed; awaiting wallet execution.'
        elif capability.get('live_execution_path') == 'governance':
            if str(action.get('action_type') or '') == 'freeze_wallet':
                governance_response = _submit_freeze_wallet_governance_action(action, workspace_context, user)
                metadata['governance_action'] = governance_response
                metadata['external_governance_action_id'] = governance_response.get('action_id')
                metadata['attestation_hash'] = governance_response.get('attestation_hash')
                metadata['policy_effects'] = governance_response.get('policy_effects') or []
                metadata['execution_mode'] = 'governance_submitted'
                metadata['execution_state'] = 'proposed'
                execution_state = 'proposed'
                next_status = 'pending'
                result_summary = 'Freeze wallet governance action submitted; awaiting governance execution.'
            else:
                metadata['execution_mode'] = 'governance'
                metadata['execution_state'] = 'proposed'
                execution_state = 'proposed'
                next_status = 'pending'
                result_summary = 'Governance action submitted; awaiting execution.'
        elif capability.get('live_execution_path') == 'manual_only':
            metadata['execution_mode'] = 'manual_only'
            metadata['execution_state'] = 'proposed'
            execution_state = 'live_manual_required'
            next_status = 'pending'
            result_summary = str(capability.get('reason') or 'Manual-only in live mode')
            write_action_history(
                connection,
                workspace_id=workspace_context['workspace_id'],
                actor_type='user',
                actor_id=user['id'],
                object_type='response_action',
                object_id=action_id,
                action_type='response_action.manual_required',
                details={'mode': action.get('mode'), 'action_type': action.get('action_type')},
            )
            append_incident_timeline_event(
                connection,
                workspace_id=workspace_context['workspace_id'],
                incident_id=str(action.get('incident_id') or ''),
                event_type='response_action.manual_required',
                message='Response action requires manual execution in selected mode.',
                actor_user_id=user['id'],
                metadata={
                    'response_action_id': action_id,
                    'action_type': action.get('action_type'),
                    'mode': action.get('mode'),
                    'status': next_status,
                    'execution_state': execution_state,
                    'alert_id': action.get('alert_id'),
                    'external_references': {
                        'safe_tx_hash': metadata.get('safe_tx_hash'),
                        'governance_action_id': metadata.get('external_governance_action_id'),
                        'attestation_hash': metadata.get('attestation_hash'),
                    },
                },
            )
        else:
            metadata['execution_mode'] = 'unsupported'
            metadata['execution_state'] = 'unsupported'
            result_summary = str(capability.get('reason') or 'Unsupported live action')
            connection.execute(
                'UPDATE response_actions SET status = %s, execution_state = %s, execution_metadata = %s::jsonb, result_summary = %s WHERE id = %s',
                ('failed', 'unsupported', _json_dumps(metadata), result_summary, action_id),
            )
            write_action_history(
                connection,
                workspace_id=workspace_context['workspace_id'],
                actor_type='user',
                actor_id=user['id'],
                object_type='response_action',
                object_id=action_id,
                action_type='response_action.unsupported',
                details={'mode': action.get('mode'), 'action_type': action.get('action_type'), 'code': 'RESPONSE_ACTION_UNSUPPORTED_EXECUTOR'},
            )
            append_incident_timeline_event(
                connection,
                workspace_id=workspace_context['workspace_id'],
                incident_id=str(action.get('incident_id') or ''),
                event_type='response_action.unsupported',
                message='Response action execution is unsupported in selected mode.',
                actor_user_id=user['id'],
                metadata={
                    'response_action_id': action_id,
                    'action_type': action.get('action_type'),
                    'mode': action.get('mode'),
                    'status': 'failed',
                    'execution_state': 'unsupported',
                    'alert_id': action.get('alert_id'),
                    'external_references': {
                        'safe_tx_hash': metadata.get('safe_tx_hash'),
                        'governance_action_id': metadata.get('external_governance_action_id'),
                        'attestation_hash': metadata.get('attestation_hash'),
                    },
                },
            )
            connection.commit()
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    'code': 'RESPONSE_ACTION_UNSUPPORTED_EXECUTOR',
                    'message': result_summary,
                    'action_id': action_id,
                    'action_type': action.get('action_type'),
                    'live_execution_path': capability.get('live_execution_path'),
                    'status': 'failed',
                    'execution_state': 'unsupported',
                    'reason': result_summary,
                },
            )
        if next_status not in ENFORCEMENT_STATUSES:
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail='Invalid next status for response action execution.')
        connection.execute(
            f"""
            UPDATE response_actions
            SET status = '{next_status}', execution_state = %s, safe_tx_hash = COALESCE(%s, safe_tx_hash), execution_metadata = %s::jsonb, executed_at = CASE WHEN '{next_status}' = 'executed' THEN NOW() ELSE executed_at END, result_summary = COALESCE(result_summary, %s)
            WHERE id = %s
            """,
            (execution_state, safe_tx_hash, _json_dumps(metadata), result_summary, action_id),
        )
        write_action_history(
            connection,
            workspace_id=workspace_context['workspace_id'],
            actor_type='user',
            actor_id=user['id'],
            object_type='response_action',
            object_id=action_id,
            action_type='response_action.executed',
            details={'mode': action.get('mode'), 'safe_tx_hash': safe_tx_hash, 'execution_state': execution_state},
        )
        governance_action = metadata.get('governance_action') if isinstance(metadata.get('governance_action'), dict) else {}
        external_references: dict[str, Any] = {}
        if safe_tx_hash:
            external_references['safe_tx_hash'] = safe_tx_hash
        governance_action_id = metadata.get('external_governance_action_id') or governance_action.get('action_id')
        if governance_action_id:
            external_references['governance_action_id'] = governance_action_id
        attestation_hash = metadata.get('attestation_hash') or governance_action.get('attestation_hash')
        if attestation_hash:
            external_references['attestation_hash'] = attestation_hash
        timeline_event_type = 'response_action.proposed' if execution_state == 'proposed' else 'response_action.executed'
        timeline_message = 'Response action proposed; awaiting external execution.' if execution_state == 'proposed' else 'Response action executed.'
        append_incident_timeline_event(
            connection,
            workspace_id=workspace_context['workspace_id'],
            incident_id=str(action.get('incident_id') or ''),
            event_type=timeline_event_type,
            message=timeline_message,
            actor_user_id=user['id'],
            metadata={
                'response_action_id': action_id,
                'action_type': action.get('action_type'),
                'mode': action.get('mode'),
                'status': next_status,
                'execution_state': execution_state,
                'alert_id': action.get('alert_id'),
                'external_references': external_references,
            },
        )
        if action.get('incident_id'):
            write_action_history(
                connection,
                workspace_id=workspace_context['workspace_id'],
                actor_type='user',
                actor_id=user['id'],
                object_type='incident',
                object_id=str(action.get('incident_id')),
                action_type='incident.response_action_executed',
                details={'response_action_id': action_id, 'action_type': action.get('action_type'), 'mode': action.get('mode')},
            )
        if action.get('alert_id'):
            write_action_history(
                connection,
                workspace_id=workspace_context['workspace_id'],
                actor_type='user',
                actor_id=user['id'],
                object_type='alert',
                object_id=str(action.get('alert_id')),
                action_type='alert.response_action_executed',
                details={'response_action_id': action_id, 'action_type': action.get('action_type'), 'mode': action.get('mode')},
            )
        log_audit(connection, action='enforcement.action.execute', entity_type='enforcement_action', entity_id=action_id, request=request, user_id=user['id'], workspace_id=workspace_context['workspace_id'], metadata={'safe_tx_hash': safe_tx_hash})
        connection.commit()
        return _response_action_payload(
            {
                'id': action_id,
                'status': next_status,
                'safe_tx_hash': safe_tx_hash,
                'mode': action.get('mode'),
                'execution_state': execution_state,
                'live_execution_path': capability.get('live_execution_path'),
                'reason': capability.get('reason'),
                'result_summary': result_summary,
            }
        )


def rollback_enforcement_action(action_id: str, request: Request) -> dict[str, Any]:
    require_live_mode()
    with pg_connection() as connection:
        ensure_pilot_schema(connection)
        user, workspace_context = _require_workspace_admin(connection, request)
        row = connection.execute('SELECT * FROM response_actions WHERE id = %s AND workspace_id = %s', (action_id, workspace_context['workspace_id'])).fetchone()
        if row is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Enforcement action not found.')
        action = _json_safe_value(dict(row))
        if str(action.get('status')) not in {'executed', 'failed'}:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail='Only executed/failed actions can be rolled back.')
        metadata = action.get('execution_metadata') if isinstance(action.get('execution_metadata'), dict) else {}
        rollback_id = str(uuid.uuid4())
        compensating_type = 'notify_team'
        compensating_calldata = None
        if action.get('action_type') == 'revoke_approval' and metadata.get('previous_allowance') is not None:
            compensating_type = 'revoke_approval'
            compensating_calldata = _encode_erc20_approve_calldata(str(action.get('spender')), int(metadata.get('previous_allowance')))
        connection.execute(
            '''
            INSERT INTO response_actions (
                id, workspace_id, incident_id, alert_id, action_type, mode, status, chain_network, target_wallet,
                token_contract, spender, calldata, execution_metadata, created_by_user_id
            )
            VALUES (%s, %s, %s::uuid, %s::uuid, %s, %s, 'pending', %s, %s, %s, %s, %s, %s::jsonb, %s)
            ''',
            (
                rollback_id,
                workspace_context['workspace_id'],
                action.get('incident_id'),
                action.get('alert_id'),
                compensating_type,
                action.get('mode') or 'simulated',
                action.get('chain_network'),
                action.get('target_wallet'),
                action.get('token_contract'),
                action.get('spender'),
                compensating_calldata,
                _json_dumps({'compensating_for_action_id': action_id, 'previous_allowance': metadata.get('previous_allowance')}),
                user['id'],
            ),
        )
        connection.execute('UPDATE response_actions SET status = %s, rolled_back_at = NOW() WHERE id = %s', ('canceled', action_id))
        append_incident_timeline_event(
            connection,
            workspace_id=workspace_context['workspace_id'],
            incident_id=str(action.get('incident_id') or ''),
            event_type='response_action.rollback_created',
            message='Compensating rollback action created.',
            actor_user_id=user['id'],
            metadata={
                'response_action_id': action_id,
                'compensating_action_id': rollback_id,
                'compensating_action_type': compensating_type,
                'action_type': action.get('action_type'),
                'mode': action.get('mode'),
                'alert_id': action.get('alert_id'),
                'external_references': {
                    'safe_tx_hash': action.get('safe_tx_hash'),
                    'governance_action_id': metadata.get('external_governance_action_id'),
                    'attestation_hash': metadata.get('attestation_hash'),
                },
            },
        )
        write_action_history(
            connection,
            workspace_id=workspace_context['workspace_id'],
            actor_type='user',
            actor_id=user['id'],
            object_type='response_action',
            object_id=action_id,
            action_type='response_action.rolled_back',
            details={'compensating_action_id': rollback_id, 'compensating_action_type': compensating_type},
        )
        append_incident_timeline_event(
            connection,
            workspace_id=workspace_context['workspace_id'],
            incident_id=str(action.get('incident_id') or ''),
            event_type='response_action.rollback_completed',
            message='Response action rollback completed with compensating action.',
            actor_user_id=user['id'],
            metadata={
                'response_action_id': action_id,
                'compensating_action_id': rollback_id,
                'compensating_action_type': compensating_type,
                'action_type': action.get('action_type'),
                'mode': action.get('mode'),
                'alert_id': action.get('alert_id'),
                'external_references': {
                    'safe_tx_hash': action.get('safe_tx_hash'),
                    'governance_action_id': metadata.get('external_governance_action_id'),
                    'attestation_hash': metadata.get('attestation_hash'),
                },
            },
        )
        append_incident_timeline_event(
            connection,
            workspace_id=workspace_context['workspace_id'],
            incident_id=str(action.get('incident_id') or ''),
            event_type='response_action.rolled_back',
            message='Response action rolled back with compensating action.',
            actor_user_id=user['id'],
            metadata={
                'response_action_id': action_id,
                'compensating_action_id': rollback_id,
                'compensating_action_type': compensating_type,
                'action_type': action.get('action_type'),
                'mode': action.get('mode'),
                'alert_id': action.get('alert_id'),
                'external_references': {
                    'safe_tx_hash': action.get('safe_tx_hash'),
                    'governance_action_id': metadata.get('external_governance_action_id'),
                    'attestation_hash': metadata.get('attestation_hash'),
                },
            },
        )
        log_audit(connection, action='enforcement.action.rollback', entity_type='enforcement_action', entity_id=action_id, request=request, user_id=user['id'], workspace_id=workspace_context['workspace_id'], metadata={'compensating_action_id': rollback_id})
        connection.commit()
        return {'id': action_id, 'status': 'canceled', 'compensating_action_id': rollback_id, 'compensating_action_type': compensating_type}


def list_enforcement_actions(
    request: Request,
    *,
    incident_id: str | None = None,
    alert_id: str | None = None,
    status_value: str | None = None,
    limit: int = 200,
) -> dict[str, Any]:
    require_live_mode()
    max_limit = max(1, min(limit, 500))
    normalized_status = _normalize_response_action_status(status_value) if status_value else None
    with pg_connection() as connection:
        ensure_pilot_schema(connection)
        user = authenticate_with_connection(connection, request)
        workspace_context = resolve_workspace(connection, user['id'], request.headers.get('x-workspace-id'))
        rows = connection.execute(
            '''
            SELECT id, action_type, mode, status, execution_state, result_summary, operator_notes, created_at, executed_at, rolled_back_at, incident_id, alert_id, safe_tx_hash, execution_metadata
            FROM response_actions
            WHERE workspace_id = %s
              AND (%s::uuid IS NULL OR incident_id = %s::uuid)
              AND (%s::uuid IS NULL OR alert_id = %s::uuid)
              AND (%s::text IS NULL OR status = %s::text)
            ORDER BY created_at DESC
            LIMIT %s
            ''',
            (workspace_context['workspace_id'], incident_id, incident_id, alert_id, alert_id, normalized_status, normalized_status, max_limit),
        ).fetchall()
        actions: list[dict[str, Any]] = []
        for row in rows:
            action = _json_safe_value(dict(row))
            action['capability'] = resolve_response_action_capability(str(action.get('action_type') or ''), str(action.get('mode') or ''))
            actions.append(_response_action_payload(action))
        return {'actions': actions}


def create_action_history_entry(payload: dict[str, Any], request: Request) -> dict[str, Any]:
    require_live_mode()
    object_type = str(payload.get('object_type') or '').strip().lower()
    object_id = str(payload.get('object_id') or '').strip()
    action_type = str(payload.get('action_type') or '').strip()
    if not object_type or not object_id or not action_type:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='object_type, object_id, and action_type are required.')
    details = payload.get('details_json') if isinstance(payload.get('details_json'), dict) else {}
    with pg_connection() as connection:
        ensure_pilot_schema(connection)
        user, workspace_context = _require_workspace_admin(connection, request)
        history_id = str(uuid.uuid4())
        connection.execute(
            '''
            INSERT INTO action_history (id, workspace_id, actor_type, actor_id, object_type, object_id, action_type, timestamp, details_json)
            VALUES (%s, %s, %s, %s, %s, %s, %s, NOW(), %s::jsonb)
            ''',
            (
                history_id,
                workspace_context['workspace_id'],
                'user',
                user['id'],
                object_type,
                object_id,
                action_type,
                _json_dumps(details),
            ),
        )
        log_audit(connection, action='action_history.created', entity_type='action_history', entity_id=history_id, request=request, user_id=user['id'], workspace_id=workspace_context['workspace_id'], metadata={'object_type': object_type, 'object_id': object_id, 'action_type': action_type})
        connection.commit()
        return {'id': history_id, 'object_type': object_type, 'object_id': object_id, 'action_type': action_type, 'details_json': details}


def create_finding_decision(finding_id: str, payload: dict[str, Any], request: Request) -> dict[str, Any]:
    decision_type = str(payload.get('decision_type', '')).strip().lower()
    if decision_type not in {'accepted_risk', 'suppress', 'exception_approved', 'escalated'}:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='Invalid decision_type.')
    with pg_connection() as connection:
        ensure_pilot_schema(connection)
        user, workspace_context = _require_workspace_admin(connection, request)
        exists = connection.execute('SELECT id FROM alerts WHERE id = %s AND workspace_id = %s', (finding_id, workspace_context['workspace_id'])).fetchone()
        if exists is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Finding not found.')
        decision_id = str(uuid.uuid4())
        connection.execute(
            '''
            INSERT INTO finding_decisions (id, workspace_id, finding_id, actor_user_id, decision_type, reason, notes, status, created_at, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, 'open', NOW(), NOW())
            ''',
            (decision_id, workspace_context['workspace_id'], finding_id, user['id'], decision_type, str(payload.get('reason', '')).strip() or None, str(payload.get('notes', '')).strip() or None),
        )
        log_audit(connection, action='finding.decision', entity_type='finding_decision', entity_id=decision_id, request=request, user_id=user['id'], workspace_id=workspace_context['workspace_id'], metadata={'finding_id': finding_id, 'decision_type': decision_type})
        connection.commit()
        return {'id': decision_id, 'finding_id': finding_id, 'decision_type': decision_type}


def create_finding_action(finding_id: str, payload: dict[str, Any], request: Request) -> dict[str, Any]:
    with pg_connection() as connection:
        ensure_pilot_schema(connection)
        user, workspace_context = _require_workspace_admin(connection, request)
        exists = connection.execute('SELECT id FROM alerts WHERE id = %s AND workspace_id = %s', (finding_id, workspace_context['workspace_id'])).fetchone()
        if exists is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Finding not found.')
        action_id = str(uuid.uuid4())
        connection.execute(
            '''
            INSERT INTO finding_actions (id, workspace_id, finding_id, owner_user_id, created_by_user_id, action_type, status, title, notes, due_at, created_at, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s::timestamptz, NOW(), NOW())
            ''',
            (
                action_id,
                workspace_context['workspace_id'],
                finding_id,
                payload.get('owner_user_id'),
                user['id'],
                str(payload.get('action_type', 'remediation')).strip(),
                str(payload.get('status', 'open')).strip(),
                str(payload.get('title', 'Remediation task')).strip(),
                str(payload.get('notes', '')).strip() or None,
                str(payload.get('due_at')) if payload.get('due_at') else None,
            ),
        )
        log_audit(connection, action='finding.action.create', entity_type='finding_action', entity_id=action_id, request=request, user_id=user['id'], workspace_id=workspace_context['workspace_id'], metadata={'finding_id': finding_id})
        connection.commit()
        return {'id': action_id, 'finding_id': finding_id}


def patch_finding_action(action_id: str, payload: dict[str, Any], request: Request) -> dict[str, Any]:
    with pg_connection() as connection:
        ensure_pilot_schema(connection)
        user, workspace_context = _require_workspace_admin(connection, request)
        found = connection.execute('SELECT id FROM finding_actions WHERE id = %s AND workspace_id = %s', (action_id, workspace_context['workspace_id'])).fetchone()
        if found is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Action item not found.')
        connection.execute(
            '''
            UPDATE finding_actions
            SET status = COALESCE(%s, status),
                owner_user_id = COALESCE(%s::uuid, owner_user_id),
                notes = COALESCE(%s, notes),
                due_at = COALESCE(%s::timestamptz, due_at),
                updated_at = NOW()
            WHERE id = %s
            ''',
            (payload.get('status'), payload.get('owner_user_id'), payload.get('notes'), payload.get('due_at'), action_id),
        )
        log_audit(connection, action='finding.action.update', entity_type='finding_action', entity_id=action_id, request=request, user_id=user['id'], workspace_id=workspace_context['workspace_id'], metadata={})
        connection.commit()
        return {'id': action_id, 'updated': True}


def list_finding_actions(request: Request) -> dict[str, Any]:
    with pg_connection() as connection:
        ensure_pilot_schema(connection)
        user = authenticate_with_connection(connection, request)
        workspace_context = resolve_workspace(connection, user['id'], request.headers.get('x-workspace-id'))
        rows = connection.execute('SELECT * FROM finding_actions WHERE workspace_id = %s ORDER BY created_at DESC LIMIT 200', (workspace_context['workspace_id'],)).fetchall()
        return {'actions': [_json_safe_value(dict(row)) for row in rows]}


def list_finding_decisions(request: Request) -> dict[str, Any]:
    with pg_connection() as connection:
        ensure_pilot_schema(connection)
        user = authenticate_with_connection(connection, request)
        workspace_context = resolve_workspace(connection, user['id'], request.headers.get('x-workspace-id'))
        rows = connection.execute('SELECT * FROM finding_decisions WHERE workspace_id = %s ORDER BY created_at DESC LIMIT 200', (workspace_context['workspace_id'],)).fetchall()
        return {'decisions': [_json_safe_value(dict(row)) for row in rows]}


def create_export_job(export_type: str, payload: dict[str, Any], request: Request) -> dict[str, Any]:
    require_live_mode()
    fmt = str(payload.get('format', 'csv')).strip().lower()
    if fmt not in {'csv', 'json', 'pdf'}:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='Unsupported export format.')
    with pg_connection() as connection:
        ensure_pilot_schema(connection)
        user = authenticate_with_connection(connection, request)
        workspace_context = resolve_workspace(connection, user['id'], request.headers.get('x-workspace-id'))
        entitlements = _workspace_plan(connection, workspace_context['workspace_id'])
        if not bool(entitlements.get('exports_enabled')):
            raise HTTPException(status_code=status.HTTP_402_PAYMENT_REQUIRED, detail='Exports are not available on this plan.')
        job_id = str(uuid.uuid4())
        output_path = f'{workspace_context["workspace_id"]}/{job_id}.{fmt}'
        connection.execute(
            '''
            INSERT INTO export_jobs (id, workspace_id, requested_by_user_id, export_type, format, filters, status, output_path, storage_backend, storage_object_key)
            VALUES (%s, %s, %s, %s, %s, %s::jsonb, 'queued', %s, %s, %s)
            ''',
            (job_id, workspace_context['workspace_id'], user['id'], export_type, fmt, _json_dumps(payload.get('filters') if isinstance(payload.get('filters'), dict) else {}), output_path, 'pending', output_path),
        )
        _generate_export_artifact(connection, workspace_id=workspace_context['workspace_id'], export_id=job_id)
        log_audit(connection, action='export.generate', entity_type='export_job', entity_id=job_id, request=request, user_id=user['id'], workspace_id=workspace_context['workspace_id'], metadata={'export_type': export_type, 'format': fmt})
        connection.commit()
        completed = connection.execute('SELECT status, error_message FROM export_jobs WHERE id = %s', (job_id,)).fetchone()
        return {'job_id': job_id, 'status': str(completed['status']), 'download_url': f'/exports/{job_id}/download' if str(completed['status']) == 'completed' else None, 'error_message': completed.get('error_message')}


def get_mttd_metrics(request: Request, *, window_days: int = 7) -> dict[str, Any]:
    require_live_mode()
    bounded_window_days = max(1, min(int(window_days), 90))
    with pg_connection() as connection:
        ensure_pilot_schema(connection)
        user = authenticate_with_connection(connection, request)
        workspace_context = resolve_workspace(connection, user['id'], request.headers.get('x-workspace-id'))
        base = connection.execute(
            '''
            SELECT COUNT(*) AS count,
                   AVG(mttd_seconds) AS avg_mttd,
                   PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY mttd_seconds) AS p50_mttd,
                   PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY mttd_seconds) AS p95_mttd
            FROM detection_metrics
            WHERE workspace_id = %s
              AND detected_at >= NOW() - (%s || ' days')::interval
            ''',
            (workspace_context['workspace_id'], bounded_window_days),
        ).fetchone() or {}
        by_severity_rows = connection.execute(
            '''
            SELECT LOWER(COALESCE(a.severity, 'unknown')) AS severity, COUNT(*) AS count
            FROM detection_metrics dm
            LEFT JOIN alerts a ON a.id = dm.alert_id
            WHERE dm.workspace_id = %s
              AND dm.detected_at >= NOW() - (%s || ' days')::interval
            GROUP BY 1
            ORDER BY count DESC, severity ASC
            ''',
            (workspace_context['workspace_id'], bounded_window_days),
        ).fetchall()
        by_detector_rows = connection.execute(
            '''
            SELECT COALESCE(dm.evidence->>'detector_family', 'unknown') AS detector_family, COUNT(*) AS count
            FROM detection_metrics dm
            WHERE dm.workspace_id = %s
              AND dm.detected_at >= NOW() - (%s || ' days')::interval
            GROUP BY 1
            ORDER BY count DESC, detector_family ASC
            ''',
            (workspace_context['workspace_id'], bounded_window_days),
        ).fetchall()
        return {
            'window_days': bounded_window_days,
            'count': int(base.get('count') or 0),
            'avg': float(base.get('avg_mttd') or 0.0),
            'p50': float(base.get('p50_mttd') or 0.0),
            'p95': float(base.get('p95_mttd') or 0.0),
            'by_severity': [_json_safe_value(dict(row)) for row in by_severity_rows],
            'by_detector_family': [_json_safe_value(dict(row)) for row in by_detector_rows],
        }


def create_proof_bundle_export(payload: dict[str, Any], request: Request) -> dict[str, Any]:
    export_payload = {
        'format': 'json',
        'filters': {
            'incident_id': payload.get('incident_id'),
            'include_raw_events': bool(payload.get('include_raw_events', True)),
        },
    }
    result = create_export_job('proof_bundle', export_payload, request)
    return {
        'export_job_id': result['job_id'],
        'download_link': result.get('download_url'),
        'status': result.get('status'),
        'error_message': result.get('error_message'),
    }


def create_incident_report_export(payload: dict[str, Any], request: Request) -> dict[str, Any]:
    export_payload = {
        'format': str(payload.get('format', 'json')).strip().lower(),
        'filters': {
            'incident_id': payload.get('incident_id'),
        },
    }
    result = create_export_job('incident_report', export_payload, request)
    return {
        'export_job_id': result['job_id'],
        'download_link': result.get('download_url'),
        'status': result.get('status'),
        'error_message': result.get('error_message'),
    }


def _generate_export_artifact(connection: Any, *, workspace_id: str, export_id: str) -> None:
    job = connection.execute('SELECT id, export_type, format, filters FROM export_jobs WHERE id = %s AND workspace_id = %s', (export_id, workspace_id)).fetchone()
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Export not found.')
    rows: list[dict[str, Any]]
    filters = job.get('filters') if isinstance(job.get('filters'), dict) else {}
    match str(job['export_type']):
        case 'history':
            rows = [_json_safe_value(dict(row)) for row in connection.execute('SELECT id, analysis_type, service_name, status, title, summary, created_at FROM analysis_runs WHERE workspace_id = %s ORDER BY created_at DESC LIMIT 1000', (workspace_id,)).fetchall()]
        case 'alerts':
            rows = [_json_safe_value(dict(row)) for row in connection.execute('SELECT id, alert_type, title, severity, status, module_key, target_id, created_at FROM alerts WHERE workspace_id = %s ORDER BY created_at DESC LIMIT 1000', (workspace_id,)).fetchall()]
        case 'findings' | 'report':
            rows = [_json_safe_value(dict(row)) for row in connection.execute('SELECT id, analysis_type, status, title, summary, created_at FROM analysis_runs WHERE workspace_id = %s ORDER BY created_at DESC LIMIT 1000', (workspace_id,)).fetchall()]
        case 'feature1_evidence':
            target_id = str(filters.get('target_id', '')).strip() or None
            target = connection.execute(
                '''
                SELECT t.*, a.name AS asset_name, a.asset_class, a.asset_symbol, a.identifier AS asset_identifier
                FROM targets t
                LEFT JOIN assets a ON a.id = t.asset_id
                WHERE t.workspace_id = %s
                  AND t.deleted_at IS NULL
                  AND (%s::uuid IS NULL OR t.id = %s::uuid)
                ORDER BY COALESCE(t.last_checked_at, t.created_at) DESC
                LIMIT 1
                ''',
                (workspace_id, target_id, target_id),
            ).fetchone()
            alerts = connection.execute(
                'SELECT id, analysis_run_id, target_id, title, severity, status, summary, payload, created_at FROM alerts WHERE workspace_id = %s ORDER BY created_at DESC LIMIT 20',
                (workspace_id,),
            ).fetchall()
            runs = connection.execute(
                "SELECT id, target_id, analysis_type, status, response_payload, created_at FROM analysis_runs WHERE workspace_id = %s AND analysis_type LIKE 'monitoring_%%' ORDER BY created_at DESC LIMIT 100",
                (workspace_id,),
            ).fetchall()
            incidents = connection.execute(
                'SELECT id, target_id, title, severity, status, summary, linked_alert_ids, created_at FROM incidents WHERE workspace_id = %s ORDER BY created_at DESC LIMIT 20',
                (workspace_id,),
            ).fetchall()
            audits = connection.execute(
                "SELECT id, action, entity_type, entity_id, metadata, created_at FROM audit_logs WHERE workspace_id = %s AND action IN ('asset.create','asset.update','target.update','export.generate') ORDER BY created_at DESC LIMIT 50",
                (workspace_id,),
            ).fetchall()
            normalized_alerts = [_json_safe_value(dict(row)) for row in alerts]
            normalized_runs = [_json_safe_value(dict(row)) for row in runs]
            normalized_incidents = [_json_safe_value(dict(item)) for item in incidents]
            worker_runs = [
                item for item in normalized_runs
                if str(((item.get('response_payload') or {}).get('monitoring_path') or 'worker')).lower() == 'worker'
            ]
            worker_run_ids = {str(item.get('id')) for item in worker_runs}
            def _strict_real_alert(item: dict[str, Any]) -> bool:
                payload = item.get('payload') if isinstance(item.get('payload'), dict) else {}
                observed = payload.get('observed_evidence') if isinstance(payload.get('observed_evidence'), dict) else {}
                severity = str(item.get('severity') or payload.get('severity') or 'low').lower()
                incident_linked = any(item.get('id') in (inc.get('linked_alert_ids') or []) for inc in normalized_incidents)
                analysis_run_id = str(item.get('analysis_run_id') or '')
                return (
                    str(observed.get('evidence_origin') or '').lower() == 'real'
                    and str(payload.get('detector_status') or '').lower() == 'anomaly_detected'
                    and str(payload.get('detector_family') or payload.get('detection_family') or '') in {'counterparty', 'flow_pattern', 'approval_pattern', 'liquidity_venue', 'oracle_integrity'}
                    and str(payload.get('source') or '').lower() == 'live'
                    and str(payload.get('monitoring_path') or 'worker') == 'worker'
                    and analysis_run_id in worker_run_ids
                    and not bool(payload.get('degraded'))
                    and not any(key in payload for key in ('monitoring_demo_scenario', 'monitoring_scenario', 'monitoring_profile'))
                    and (severity not in {'high', 'critical'} or incident_linked)
                )
            strict_anomaly = any(_strict_real_alert(item) for item in normalized_alerts)
            reason_codes: list[str] = []
            if not worker_runs:
                reason_codes.append('no_worker_generated_runs')
            if not normalized_alerts:
                reason_codes.append('no_alerts_found')
            if normalized_alerts and not strict_anomaly:
                reason_codes.append('alerts_failed_strict_real_anomaly_checks')
            rows = [{
                'generated_at': utc_now_iso(),
                'workspace_id': workspace_id,
                'target': _json_safe_value(dict(target)) if target else None,
                'runs': normalized_runs,
                'alerts': normalized_alerts,
                'incidents': normalized_incidents,
                'audit_trail': [_json_safe_value(dict(item)) for item in audits],
                'real_anomaly_observed': strict_anomaly,
                'real_anomaly_reason_codes': reason_codes,
                'sales_safe_claim': 'anomaly_detected_from_real_worker_evidence' if strict_anomaly else 'insufficient_real_anomaly_evidence',
                'coverage_snapshots': [
                    {
                        'run_id': item.get('id'),
                        'target_id': item.get('target_id'),
                        'market_coverage_status': ((item.get('response_payload') or {}).get('market_coverage_status')),
                        'oracle_coverage_status': ((item.get('response_payload') or {}).get('oracle_coverage_status')),
                        'enterprise_claim_eligibility': bool((item.get('response_payload') or {}).get('enterprise_claim_eligibility')),
                        'claim_ineligibility_reasons': ((item.get('response_payload') or {}).get('claim_ineligibility_reasons') or []),
                        'provider_coverage_status': ((item.get('response_payload') or {}).get('provider_coverage_status') or {}),
                        'protected_asset_context': ((item.get('response_payload') or {}).get('protected_asset_context') or {}),
                    }
                    for item in worker_runs[:20]
                ],
            }]
        case 'proof_bundle':
            incident_id = str(filters.get('incident_id') or '').strip()
            if not incident_id:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='incident_id is required for proof bundle export.')
            include_raw_events = bool(filters.get('include_raw_events', True))
            incident = connection.execute(
                'SELECT * FROM incidents WHERE workspace_id = %s AND id = %s',
                (workspace_id, incident_id),
            ).fetchone()
            if incident is None:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Incident not found.')
            alerts = connection.execute(
                '''
                SELECT a.*
                FROM alerts a
                JOIN detection_metrics dm ON dm.alert_id = a.id
                WHERE dm.workspace_id = %s
                  AND dm.incident_id = %s
                ORDER BY a.created_at DESC
                ''',
                (workspace_id, incident_id),
            ).fetchall()
            metrics = connection.execute(
                '''
                SELECT *
                FROM detection_metrics
                WHERE workspace_id = %s
                  AND incident_id = %s
                ORDER BY detected_at DESC
                ''',
                (workspace_id, incident_id),
            ).fetchall()
            evidence_rows = [_json_safe_value(dict(item)) for item in metrics]
            summary = {
                'generated_at': utc_now_iso(),
                'workspace_id': workspace_id,
                'incident_id': incident_id,
                'include_raw_events': include_raw_events,
                'detection_metric_count': len(evidence_rows),
            }
            rows = [{
                'summary.json': summary,
                'alerts.json': [_json_safe_value(dict(item)) for item in alerts],
                'incidents.json': [_json_safe_value(dict(incident))],
                'evidence.json': [item.get('evidence') for item in evidence_rows],
                'detection_metrics.json': evidence_rows if include_raw_events else [
                    {'id': item.get('id'), 'event_observed_at': item.get('event_observed_at'), 'detected_at': item.get('detected_at'), 'mttd_seconds': item.get('mttd_seconds'), 'evidence': item.get('evidence')}
                    for item in evidence_rows
                ],
            }]
        case 'incident_report':
            incident_id = str(filters.get('incident_id') or '').strip()
            if not incident_id:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='incident_id is required for incident report export.')
            incident = connection.execute('SELECT * FROM incidents WHERE workspace_id = %s AND id = %s', (workspace_id, incident_id)).fetchone()
            if incident is None:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Incident not found.')
            timeline = connection.execute(
                'SELECT id, event_type, message, actor_user_id, metadata, created_at FROM incident_timeline WHERE workspace_id = %s AND incident_id = %s ORDER BY created_at DESC',
                (workspace_id, incident_id),
            ).fetchall()
            alert_ids = incident.get('linked_alert_ids') if isinstance(incident.get('linked_alert_ids'), list) else []
            linked_alerts = []
            if alert_ids:
                linked_alerts = connection.execute(
                    'SELECT id, title, severity, status, summary, created_at FROM alerts WHERE workspace_id = %s AND id = ANY(%s::uuid[]) ORDER BY created_at DESC',
                    (workspace_id, alert_ids),
                ).fetchall()
            enforcement_actions = connection.execute(
                "SELECT id, action_type, status, mode <> 'live' AS dry_run, mode, execution_metadata, created_at, executed_at, rolled_back_at FROM response_actions WHERE workspace_id = %s AND incident_id = %s ORDER BY created_at DESC",
                (workspace_id, incident_id),
            ).fetchall()
            rows = [{
                'incident.json': _json_safe_value(dict(incident)),
                'timeline.json': [_json_safe_value(dict(item)) for item in timeline],
                'linked_alerts.json': [_json_safe_value(dict(item)) for item in linked_alerts],
                'enforcement_actions.json': [_json_safe_value(dict(item)) for item in enforcement_actions],
            }]
        case _:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='Unsupported export type.')
    storage = load_export_storage()
    try:
        if str(job['format']) == 'json':
            content = json.dumps({'rows': rows}, indent=2).encode('utf-8')
        elif str(job['format']) == 'pdf':
            content = json.dumps({'rows': rows}, indent=2).encode('utf-8')
        else:
            headers = sorted({key for row in rows for key in row.keys()})
            buffer = io.StringIO()
            writer = csv.DictWriter(buffer, fieldnames=headers)
            writer.writeheader()
            for row in rows:
                writer.writerow({key: _json_safe_value(row.get(key)) for key in headers})
            content = buffer.getvalue().encode('utf-8')
        object_key = storage.write_bytes(object_key=f"{workspace_id}/{export_id}.{job['format']}", content=content)
        connection.execute(
            "UPDATE export_jobs SET status = 'completed', error_message = NULL, storage_backend = %s, storage_object_key = %s, updated_at = NOW() WHERE id = %s",
            (storage.backend_name, object_key, export_id),
        )
    except Exception as exc:
        connection.execute("UPDATE export_jobs SET status = 'failed', error_message = %s, updated_at = NOW() WHERE id = %s", (str(exc), export_id))


def list_exports(request: Request) -> dict[str, Any]:
    require_live_mode()
    with pg_connection() as connection:
        ensure_pilot_schema(connection)
        user = authenticate_with_connection(connection, request)
        workspace_context = resolve_workspace(connection, user['id'], request.headers.get('x-workspace-id'))
        rows = connection.execute(
            '''
            SELECT id, export_type, format, status, output_path, storage_backend, storage_object_key, error_message, created_at, updated_at
            FROM export_jobs
            WHERE workspace_id = %s
            ORDER BY created_at DESC
            LIMIT 200
            ''',
            (workspace_context['workspace_id'],),
        ).fetchall()
        exports = []
        for row in rows:
            item = _json_safe_value(dict(row))
            item['download_url'] = f"/exports/{item['id']}/download" if item.get('status') == 'completed' else None
            exports.append(item)
        return {'exports': exports}


def get_export(export_id: str, request: Request) -> dict[str, Any]:
    require_live_mode()
    with pg_connection() as connection:
        ensure_pilot_schema(connection)
        user = authenticate_with_connection(connection, request)
        workspace_context = resolve_workspace(connection, user['id'], request.headers.get('x-workspace-id'))
        row = connection.execute(
            'SELECT * FROM export_jobs WHERE id = %s AND workspace_id = %s',
            (export_id, workspace_context['workspace_id']),
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Export not found.')
        item = _json_safe_value(dict(row))
        item['download_url'] = f'/exports/{export_id}/download' if item.get('status') == 'completed' else None
        return {'export': item}


def get_export_artifact_content(export_id: str, request: Request) -> tuple[bytes, str]:
    require_live_mode()
    with pg_connection() as connection:
        ensure_pilot_schema(connection)
        user = authenticate_with_connection(connection, request)
        workspace_context = resolve_workspace(connection, user['id'], request.headers.get('x-workspace-id'))
        row = connection.execute('SELECT id, workspace_id, format, status, storage_object_key FROM export_jobs WHERE id = %s AND workspace_id = %s', (export_id, workspace_context['workspace_id'])).fetchone()
        if row is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Export not found.')
        if str(row['status']) != 'completed':
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail='Export is not ready yet.')
        object_key = str(row.get('storage_object_key') or f"{row['workspace_id']}/{row['id']}.{row['format']}")
        storage = load_export_storage()
        try:
            content = storage.read_bytes(object_key=object_key)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Export artifact missing.') from exc
        return content, f"{row['id']}.{row['format']}"


def get_history_item(history_id: str, request: Request) -> dict[str, Any]:
    require_live_mode()
    with pg_connection() as connection:
        ensure_pilot_schema(connection)
        user = authenticate_with_connection(connection, request)
        workspace_context = resolve_workspace(connection, user['id'], request.headers.get('x-workspace-id'))
        run = connection.execute(
            '''
            SELECT id, analysis_type, service_name, status, title, source, summary, request_payload, response_payload, created_at
            FROM analysis_runs
            WHERE workspace_id = %s AND id = %s
            ''',
            (workspace_context['workspace_id'], history_id),
        ).fetchone()
        if run is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='History record not found.')
        alerts = connection.execute(
            'SELECT id, severity, status, title, summary, created_at FROM alerts WHERE workspace_id = %s AND analysis_run_id = %s ORDER BY created_at DESC',
            (workspace_context['workspace_id'], history_id),
        ).fetchall()
        return {'history': _json_safe_value(dict(run)), 'alerts': [_json_safe_value(dict(row)) for row in alerts]}


def list_templates() -> dict[str, Any]:
    return {
        'templates': [
            {'id': 'treasury-safe-mode', 'name': 'Treasury Safe Mode', 'module': 'threat', 'description': 'Baseline thresholds and allowlist policy for treasury operations.'},
            {'id': 'compliance-us-eu', 'name': 'US/EU Compliance Starter', 'module': 'compliance', 'description': 'Transfer review checklist and residency controls for US/EU corridors.'},
            {'id': 'oracle-dependency-monitoring', 'name': 'Oracle Dependency Monitoring', 'module': 'resilience', 'description': 'Sensitivity controls for oracle concentration and emergency thresholds.'},
        ]
    }


def apply_template(template_id: str, request: Request) -> dict[str, Any]:
    template_map = {
        'treasury-safe-mode': ('threat', {'unknown_target_threshold': 2, 'unlimited_approval_block_rule': True, 'large_transfer_threshold': 250000}),
        'compliance-us-eu': ('compliance', {'required_review_checklist': ['kyc', 'jurisdiction', 'accreditation'], 'evidence_retention_period_days': 90}),
        'oracle-dependency-monitoring': ('resilience', {'oracle_dependency_sensitivity': 'high', 'control_concentration_alerts': True, 'emergency_action_threshold': 'high'}),
    }
    if template_id not in template_map:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Template not found.')
    module_key, config = template_map[template_id]
    return put_module_config(module_key, {'config': config}, request)
