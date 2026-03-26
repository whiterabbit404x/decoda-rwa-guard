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
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from time import monotonic
from typing import Any, Iterable
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request as UrlRequest, urlopen

import importlib
from fastapi import HTTPException, Request, status

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
)
DEFAULT_DEMO_EMAIL = 'demo@decoda.app'
EMAIL_VERIFICATION_TTL_MINUTES = 60 * 24
PASSWORD_RESET_TTL_MINUTES = 30
SESSION_TTL_HOURS = 24
MFA_RECOVERY_CODE_COUNT = 8
_rate_limit_lock = threading.Lock()
_rate_limit_state: dict[str, list[float]] = {}
logger = logging.getLogger(__name__)
_redis_rate_limiter: Any | None = None
_redis_rate_limiter_lock = threading.Lock()


STARTUP_BOOTSTRAP_ENV = 'RUN_MIGRATIONS_ON_STARTUP'


def env_flag(name: str, default: bool = False) -> bool:
    value = os.getenv(name, 'true' if default else 'false').strip().lower()
    return value in {'1', 'true', 'yes', 'on'}


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def utc_now_iso() -> str:
    return utc_now().isoformat()


def parse_csv_env(name: str, defaults: list[str]) -> list[str]:
    raw = os.getenv(name, '')
    values = [item.strip() for item in raw.split(',') if item.strip()]
    return values or defaults


SEVERITY_RANK = {'low': 1, 'medium': 2, 'high': 3, 'critical': 4}


def _severity_meets_threshold(value: str, threshold: str) -> bool:
    return SEVERITY_RANK.get((value or 'medium').lower(), 2) >= SEVERITY_RANK.get((threshold or 'medium').lower(), 2)


def database_url() -> str | None:
    value = os.getenv('DATABASE_URL', '').strip()
    return value or None


def live_mode_enabled() -> bool:
    return env_flag('LIVE_MODE_ENABLED') and database_url() is not None


def pilot_mode() -> str:
    if live_mode_enabled():
        return 'live'
    return os.getenv('APP_MODE', 'demo')


@contextmanager
def pg_connection() -> Iterable[Any]:
    db_url = database_url()
    if not db_url:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail='Live pilot mode is not configured.')
    psycopg, dict_row = load_psycopg()
    with psycopg.connect(db_url, row_factory=dict_row) as connection:
        yield connection


def require_live_mode() -> None:
    if database_url() is None:
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


def run_startup_migrations_if_enabled() -> dict[str, Any]:
    enabled = should_run_startup_migrations()
    if not enabled:
        return {'enabled': False, 'ran': False, 'applied_versions': []}
    require_live_mode()
    applied_versions = run_migrations()
    return {'enabled': True, 'ran': True, 'applied_versions': applied_versions}


def validate_runtime_configuration() -> dict[str, Any]:
    mode = os.getenv('APP_ENV', os.getenv('APP_MODE', 'development')).strip().lower()
    errors: list[str] = []
    warnings: list[str] = []
    is_production_like = mode in {'production', 'prod'}
    if is_production_like and live_mode_enabled():
        if not database_url():
            errors.append('DATABASE_URL must be configured when LIVE_MODE_ENABLED=true in production.')
        if not auth_token_secret_configured():
            errors.append('AUTH_TOKEN_SECRET must be configured in production.')
        if _email_provider() != 'console' and not os.getenv('EMAIL_FROM', '').strip():
            errors.append('EMAIL_FROM must be set when using a real email provider in production.')
        if _email_provider() == 'console':
            warnings.append('EMAIL_PROVIDER=console is not suitable for production.')
        if not os.getenv('REDIS_URL', '').strip():
            warnings.append('REDIS_URL missing: falling back to in-memory auth rate limiting.')
        strict_billing = env_flag('STRICT_PRODUCTION_BILLING')
        if strict_billing:
            if not os.getenv('STRIPE_SECRET_KEY', '').strip():
                errors.append('Stripe billing is not ready because STRIPE_SECRET_KEY is missing in production.')
            if not os.getenv('STRIPE_WEBHOOK_SECRET', '').strip():
                errors.append('Stripe billing is not ready because STRIPE_WEBHOOK_SECRET is missing in production.')
    return {'mode': mode, 'errors': errors, 'warnings': warnings}


def integration_health_snapshot(connection: Any | None = None) -> dict[str, Any]:
    stripe_key = bool(os.getenv('STRIPE_SECRET_KEY', '').strip())
    stripe_hook = bool(os.getenv('STRIPE_WEBHOOK_SECRET', '').strip())
    plan_prices_configured = False
    if connection is not None:
        row = connection.execute("SELECT COUNT(*) AS count FROM plan_entitlements WHERE stripe_price_id IS NOT NULL AND stripe_price_id <> ''").fetchone()
        plan_prices_configured = int((row or {}).get('count') or 0) > 0

    email_provider = _email_provider()
    email_ready = email_provider == 'console' or bool(os.getenv('EMAIL_RESEND_API_KEY', '').strip())

    return {
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
        'slack': {
            'status': 'healthy',
            'message': 'Slack integrations are configured per workspace. Use test delivery to validate channels.',
            'checks': {'webhook_mode_supported': True, 'bot_mode_supported': True},
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
    applied_versions: list[str] = []
    with pg_connection() as connection:
        ensure_migration_table(connection)
        already_applied = {
            row['version'] for row in connection.execute('SELECT version FROM schema_migrations').fetchall()
        }
        for path in sorted(migration_dir().glob('*.sql')):
            connection.execute(path.read_text())
            connection.execute(
                'INSERT INTO schema_migrations (version) VALUES (%s) ON CONFLICT (version) DO NOTHING',
                (path.name,),
            )
            if path.name not in already_applied:
                applied_versions.append(path.name)
        missing_tables = _fetch_missing_pilot_tables(connection)
        if missing_tables:
            raise _schema_missing_http_exception(missing_tables)
        connection.commit()
    return applied_versions


def _missing_relation_error(exc: Exception) -> bool:
    message = str(exc).lower()
    return exc.__class__.__name__ == 'UndefinedTable' or 'does not exist' in message and 'relation' in message


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
        except Exception:
            logger.exception('redis rate limiter unavailable; falling back to in-memory limiter', extra={'event': 'rate_limit.fallback'})
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
            request.client.host if request and request.client else None,
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
        'memberships': membership_payload,
    }
    )


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
    try:
        with pg_connection() as connection:
            ensure_pilot_schema(connection)
            try:
                user = connection.execute(
                    'SELECT id, password_hash, email_verified_at, session_version, mfa_totp_secret, mfa_enabled_at FROM users WHERE email = %s',
                    (email,),
                ).fetchone()
            except Exception:
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
            except Exception:
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
            except Exception:
                logger.exception('signin_user failed during audit log insert', extra={'step': 'insert_audit_log', 'user_id': user_id})
                raise
            connection.commit()
            try:
                hydrated_user = build_user_response(connection, user_id)
                if not hydrated_user.get('current_workspace'):
                    logger.info('signin_user completed without active workspace', extra={'event': 'auth.signin.no_workspace', 'user_id': user_id})
            except Exception:
                logger.exception('signin_user failed during user hydration', extra={'step': 'build_user_response', 'user_id': user_id})
                raise
    except HTTPException:
        raise
    except Exception as exc:
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
    except Exception:
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
            WHERE workspace_id = %s
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
        return {'workspace': workspace_context['workspace'], 'subscription': _json_safe_value(dict(subscription)) if subscription else None}


def create_checkout_session(payload: dict[str, Any], request: Request) -> dict[str, Any]:
    require_live_mode()
    plan_key = str(payload.get('plan_key', 'starter')).strip().lower()
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


def process_stripe_webhook(payload: dict[str, Any], signature_header: str | None) -> dict[str, Any]:
    require_live_mode()
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


def _encode_secret_value(value: str) -> str:
    return base64.b64encode(value.encode('utf-8')).decode('ascii')


def _decode_secret_value(value: str) -> str:
    if not value:
        return ''
    try:
        return base64.b64decode(value.encode('ascii')).decode('utf-8')
    except Exception:
        return value


def _normalize_routing_payload(payload: dict[str, Any], *, channel_type: str) -> dict[str, Any]:
    severity_threshold = str(payload.get('severity_threshold', 'medium')).strip().lower()
    if severity_threshold not in SEVERITY_RANK:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='severity_threshold must be low/medium/high/critical.')
    modules_include = payload.get('modules_include') if isinstance(payload.get('modules_include'), list) else []
    modules_exclude = payload.get('modules_exclude') if isinstance(payload.get('modules_exclude'), list) else []
    target_ids = payload.get('target_ids') if isinstance(payload.get('target_ids'), list) else []
    event_types = payload.get('event_types') if isinstance(payload.get('event_types'), list) else ['alert.created']
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
            INSERT INTO workspace_slack_integrations (id, workspace_id, display_name, slack_mode, webhook_url_encrypted, webhook_last4, bot_token_encrypted, bot_token_last4, default_channel, severity_routing, enabled, created_by_user_id)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, TRUE, %s)
            ''',
            (
                integration_id,
                workspace_context['workspace_id'],
                display_name,
                mode,
                (_encode_secret_value(webhook_url) if webhook_url else None),
                (webhook_url[-4:] if webhook_url else None),
                (_encode_secret_value(bot_token) if bot_token else None),
                (bot_token[-4:] if bot_token else None),
                default_channel,
                _json_dumps(_normalize_slack_severity_routing(payload)),
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
                updated_at = NOW()
            WHERE id = %s
            ''',
            (
                str(payload.get('display_name')).strip() if payload.get('display_name') is not None else None,
                next_mode,
                str(payload.get('default_channel')).strip() if payload.get('default_channel') is not None else None,
                payload.get('enabled') if payload.get('enabled') is not None else None,
                _encode_secret_value(next_url) if next_url else None,
                (next_url[-4:] if next_url else None),
                _encode_secret_value(next_bot_token) if next_bot_token else None,
                (next_bot_token[-4:] if next_bot_token else None),
                _json_dumps(_normalize_slack_severity_routing(payload)) if payload.get('severity_routing') is not None else None,
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
                'webhook_url': _decode_secret_value(str(integration['webhook_url_encrypted'])) if integration.get('webhook_url_encrypted') else '',
                'bot_token': _decode_secret_value(str(integration['bot_token_encrypted'])) if integration.get('bot_token_encrypted') else '',
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
            SELECT id, channel_type, severity_threshold, modules_include, modules_exclude, target_ids, event_types, enabled, created_at, updated_at
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
            INSERT INTO alert_routing_rules (id, workspace_id, channel_type, severity_threshold, modules_include, modules_exclude, target_ids, event_types, enabled, created_by_user_id)
            VALUES (%s, %s, %s, %s, %s::jsonb, %s::jsonb, %s::jsonb, %s::jsonb, %s, %s)
            ON CONFLICT (workspace_id, channel_type)
            DO UPDATE SET severity_threshold = EXCLUDED.severity_threshold,
                          modules_include = EXCLUDED.modules_include,
                          modules_exclude = EXCLUDED.modules_exclude,
                          target_ids = EXCLUDED.target_ids,
                          event_types = EXCLUDED.event_types,
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
                normalized['enabled'],
                user['id'],
            ),
        )
        row = connection.execute('SELECT id, channel_type, severity_threshold, modules_include, modules_exclude, target_ids, event_types, enabled FROM alert_routing_rules WHERE workspace_id = %s AND channel_type = %s', (workspace_context['workspace_id'], channel_type)).fetchone()
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
    connection.execute(
        '''
        INSERT INTO analysis_runs (id, workspace_id, user_id, analysis_type, service_name, status, title, source, summary, request_payload, response_payload, created_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb, NOW())
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
            SELECT channel_type, severity_threshold, modules_include, modules_exclude, target_ids, event_types, enabled
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
                    'webhook_url': _decode_secret_value(str(integration.get('webhook_url_encrypted') or '')),
                    'bot_token': _decode_secret_value(str(integration.get('bot_token_encrypted') or '')),
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
            'exports_enabled': False,
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
    if contract_identifier and len(contract_identifier) > 150:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='contract_identifier exceeds 150 chars.')
    if wallet_address and not re.match(r'^0x[a-fA-F0-9]{40}$', wallet_address):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='wallet_address must be an EVM-style address.')
    severity_preference = str(payload.get('severity_preference', 'medium')).strip().lower()
    if severity_preference not in {'low', 'medium', 'high', 'critical'}:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='severity_preference must be low/medium/high/critical.')
    tags_raw = payload.get('tags')
    tags = [str(item).strip().lower() for item in tags_raw] if isinstance(tags_raw, list) else []
    tags = [item for item in tags if item]
    return {
        'name': name,
        'target_type': target_type,
        'chain_network': chain_network,
        'contract_identifier': contract_identifier,
        'wallet_address': wallet_address,
        'asset_type': str(payload.get('asset_type', '')).strip() or None,
        'owner_notes': str(payload.get('owner_notes', '')).strip() or None,
        'severity_preference': severity_preference,
        'enabled': bool(payload.get('enabled', True)),
        'tags': tags,
    }


def _validate_asset_payload(payload: dict[str, Any]) -> dict[str, Any]:
    name = str(payload.get('name', '')).strip()
    description = str(payload.get('description', '')).strip() or None
    asset_type = str(payload.get('asset_type', '')).strip().lower()
    chain_network = str(payload.get('chain_network', '')).strip()
    identifier = str(payload.get('identifier', '')).strip()
    asset_class = str(payload.get('asset_class', '')).strip() or None
    risk_tier = str(payload.get('risk_tier', 'medium')).strip().lower()
    owner_team = str(payload.get('owner_team', '')).strip() or None
    notes = str(payload.get('notes', '')).strip() or None
    if not name or len(name) > 120:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='name is required (max 120 chars).')
    if asset_type not in ASSET_TYPES:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='Invalid asset_type.')
    if not chain_network or len(chain_network) > 64:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='chain_network is required (max 64 chars).')
    if not identifier or len(identifier) > 180:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='identifier is required (max 180 chars).')
    if risk_tier not in {'low', 'medium', 'high', 'critical'}:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='risk_tier must be low/medium/high/critical.')
    tags_raw = payload.get('tags')
    tags = [str(item).strip().lower() for item in tags_raw] if isinstance(tags_raw, list) else []
    tags = [item for item in tags if item][:25]
    return {
        'name': name,
        'description': description,
        'asset_type': asset_type,
        'chain_network': chain_network,
        'identifier': identifier,
        'asset_class': asset_class,
        'risk_tier': risk_tier,
        'owner_team': owner_team,
        'notes': notes,
        'enabled': bool(payload.get('enabled', True)),
        'tags': tags,
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
            SELECT id, name, description, asset_type, chain_network, identifier, asset_class, risk_tier, owner_team, notes, enabled, created_at, updated_at
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
        asset_id = str(uuid.uuid4())
        connection.execute(
            '''
            INSERT INTO assets (
                id, workspace_id, name, description, asset_type, chain_network, identifier, asset_class, risk_tier, owner_team, notes, enabled, created_by_user_id, updated_by_user_id
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
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
                user['id'],
                user['id'],
            ),
        )
        for tag in validated['tags']:
            connection.execute(
                'INSERT INTO asset_tags (id, workspace_id, asset_id, tag) VALUES (%s, %s, %s, %s) ON CONFLICT (asset_id, tag) DO NOTHING',
                (str(uuid.uuid4()), workspace_id, asset_id, tag),
            )
        log_audit(connection, action='asset.create', entity_type='asset', entity_id=asset_id, request=request, user_id=user['id'], workspace_id=workspace_id, metadata={'asset_type': validated['asset_type']})
        connection.commit()
        return {'id': asset_id, **validated}


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
            SET name = %s, description = %s, asset_type = %s, chain_network = %s, identifier = %s, asset_class = %s, risk_tier = %s, owner_team = %s, notes = %s, enabled = %s, updated_by_user_id = %s, updated_at = NOW()
            WHERE id = %s
            ''',
            (
                validated['name'], validated['description'], validated['asset_type'], validated['chain_network'], validated['identifier'], validated['asset_class'], validated['risk_tier'], validated['owner_team'], validated['notes'], validated['enabled'], user['id'], asset_id,
            ),
        )
        connection.execute('DELETE FROM asset_tags WHERE asset_id = %s', (asset_id,))
        for tag in validated['tags']:
            connection.execute('INSERT INTO asset_tags (id, workspace_id, asset_id, tag) VALUES (%s, %s, %s, %s)', (str(uuid.uuid4()), workspace_id, asset_id, tag))
        log_audit(connection, action='asset.update', entity_type='asset', entity_id=asset_id, request=request, user_id=user['id'], workspace_id=workspace_id, metadata={})
        connection.commit()
        return {'id': asset_id, **validated}


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


def list_targets(request: Request) -> dict[str, Any]:
    require_live_mode()
    with pg_connection() as connection:
        ensure_pilot_schema(connection)
        user = authenticate_with_connection(connection, request)
        workspace_context = resolve_workspace(connection, user['id'], request.headers.get('x-workspace-id'))
        workspace_id = workspace_context['workspace_id']
        rows = connection.execute(
            '''
            SELECT id, name, target_type, chain_network, contract_identifier, wallet_address, asset_type, owner_notes, severity_preference, enabled, created_at, updated_at
            FROM targets
            WHERE workspace_id = %s AND deleted_at IS NULL
            ORDER BY created_at DESC
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
        target_id = str(uuid.uuid4())
        connection.execute(
            '''
            INSERT INTO targets (
                id, workspace_id, name, target_type, chain_network, contract_identifier, wallet_address, asset_type, owner_notes, severity_preference, enabled, created_by_user_id, updated_by_user_id
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
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
                user['id'],
                user['id'],
            ),
        )
        for tag in validated['tags']:
            connection.execute(
                'INSERT INTO target_tags (id, workspace_id, target_id, tag) VALUES (%s, %s, %s, %s) ON CONFLICT (target_id, tag) DO NOTHING',
                (str(uuid.uuid4()), workspace_id, target_id, tag),
            )
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
            'SELECT * FROM targets WHERE id = %s AND workspace_id = %s AND deleted_at IS NULL',
            (target_id, workspace_context['workspace_id']),
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Target not found.')
        tags = connection.execute('SELECT tag FROM target_tags WHERE target_id = %s ORDER BY tag ASC', (target_id,)).fetchall()
        item = _json_safe_value(dict(row))
        item['tags'] = [str(tag['tag']) for tag in tags]
        return {'target': item}


def update_target(target_id: str, payload: dict[str, Any], request: Request) -> dict[str, Any]:
    require_live_mode()
    validated = _validate_target_payload(payload)
    with pg_connection() as connection:
        ensure_pilot_schema(connection)
        user, workspace_context = _require_workspace_admin(connection, request)
        workspace_id = workspace_context['workspace_id']
        found = connection.execute('SELECT id FROM targets WHERE id = %s AND workspace_id = %s AND deleted_at IS NULL', (target_id, workspace_id)).fetchone()
        if found is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Target not found.')
        connection.execute(
            '''
            UPDATE targets
            SET name = %s, target_type = %s, chain_network = %s, contract_identifier = %s, wallet_address = %s, asset_type = %s, owner_notes = %s, severity_preference = %s, enabled = %s, updated_by_user_id = %s, updated_at = NOW()
            WHERE id = %s
            ''',
            (
                validated['name'], validated['target_type'], validated['chain_network'], validated['contract_identifier'], validated['wallet_address'], validated['asset_type'], validated['owner_notes'], validated['severity_preference'], validated['enabled'], user['id'], target_id,
            ),
        )
        connection.execute('DELETE FROM target_tags WHERE target_id = %s', (target_id,))
        for tag in validated['tags']:
            connection.execute('INSERT INTO target_tags (id, workspace_id, target_id, tag) VALUES (%s, %s, %s, %s)', (str(uuid.uuid4()), workspace_id, target_id, tag))
        log_audit(connection, action='target.update', entity_type='target', entity_id=target_id, request=request, user_id=user['id'], workspace_id=workspace_id, metadata={})
        connection.commit()
        return {'id': target_id, **validated}


def delete_target(target_id: str, request: Request) -> dict[str, Any]:
    require_live_mode()
    with pg_connection() as connection:
        ensure_pilot_schema(connection)
        user, workspace_context = _require_workspace_admin(connection, request)
        row = connection.execute('SELECT id FROM targets WHERE id = %s AND workspace_id = %s AND deleted_at IS NULL', (target_id, workspace_context['workspace_id'])).fetchone()
        if row is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Target not found.')
        connection.execute('UPDATE targets SET deleted_at = NOW(), updated_by_user_id = %s, updated_at = NOW() WHERE id = %s', (user['id'], target_id))
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


def list_alerts(request: Request, *, severity: str | None = None, module: str | None = None, target_id: str | None = None, status_value: str | None = None) -> dict[str, Any]:
    require_live_mode()
    with pg_connection() as connection:
        ensure_pilot_schema(connection)
        user = authenticate_with_connection(connection, request)
        workspace_context = resolve_workspace(connection, user['id'], request.headers.get('x-workspace-id'))
        rows = connection.execute(
            '''
            SELECT id, alert_type, title, severity, status, summary, module_key, target_id, findings, acknowledged_at, resolved_at, created_at
            FROM alerts
            WHERE workspace_id = %s
              AND (%s::text IS NULL OR severity = %s::text)
              AND (%s::text IS NULL OR module_key = %s::text)
              AND (%s::uuid IS NULL OR target_id = %s::uuid)
              AND (%s::text IS NULL OR status = %s::text)
            ORDER BY created_at DESC
            LIMIT 200
            ''',
            (workspace_context['workspace_id'], severity, severity, module, module, target_id, target_id, status_value, status_value),
        ).fetchall()
        return {'alerts': [_json_safe_value(dict(row)) for row in rows]}


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
        return {'alert': _json_safe_value(dict(row)), 'events': [_json_safe_value(dict(item)) for item in events]}


def patch_alert(alert_id: str, payload: dict[str, Any], request: Request) -> dict[str, Any]:
    require_live_mode()
    next_status = str(payload.get('status', '')).strip().lower()
    if next_status not in {'open', 'acknowledged', 'resolved'}:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='status must be open/acknowledged/resolved')
    with pg_connection() as connection:
        ensure_pilot_schema(connection)
        user, workspace_context = _require_workspace_admin(connection, request)
        found = connection.execute('SELECT id FROM alerts WHERE id = %s AND workspace_id = %s', (alert_id, workspace_context['workspace_id'])).fetchone()
        if found is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Alert not found.')
        connection.execute(
            '''
            UPDATE alerts
            SET status = %s,
                acknowledged_at = CASE WHEN %s = 'acknowledged' THEN NOW() ELSE acknowledged_at END,
                acknowledged_by_user_id = CASE WHEN %s = 'acknowledged' THEN %s ELSE acknowledged_by_user_id END,
                resolved_at = CASE WHEN %s = 'resolved' THEN NOW() ELSE resolved_at END,
                resolved_by_user_id = CASE WHEN %s = 'resolved' THEN %s ELSE resolved_by_user_id END
            WHERE id = %s
            ''',
            (next_status, next_status, next_status, user['id'], next_status, next_status, user['id'], alert_id),
        )
        connection.execute('INSERT INTO alert_events (id, workspace_id, alert_id, actor_user_id, event_type, details) VALUES (%s, %s, %s, %s, %s, %s::jsonb)', (str(uuid.uuid4()), workspace_context['workspace_id'], alert_id, user['id'], f'alert.{next_status}', _json_dumps({'status': next_status})))
        connection.commit()
        return {'id': alert_id, 'status': next_status}


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
    if fmt not in {'csv', 'json'}:
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
            INSERT INTO export_jobs (id, workspace_id, requested_by_user_id, export_type, format, filters, status, output_path)
            VALUES (%s, %s, %s, %s, %s, %s::jsonb, 'queued', %s)
            ''',
            (job_id, workspace_context['workspace_id'], user['id'], export_type, fmt, _json_dumps(payload.get('filters') if isinstance(payload.get('filters'), dict) else {}), output_path),
        )
        _generate_export_artifact(connection, workspace_id=workspace_context['workspace_id'], export_id=job_id)
        log_audit(connection, action='export.generate', entity_type='export_job', entity_id=job_id, request=request, user_id=user['id'], workspace_id=workspace_context['workspace_id'], metadata={'export_type': export_type, 'format': fmt})
        connection.commit()
        completed = connection.execute('SELECT status, error_message FROM export_jobs WHERE id = %s', (job_id,)).fetchone()
        return {'job_id': job_id, 'status': str(completed['status']), 'download_url': f'/exports/{job_id}/download' if str(completed['status']) == 'completed' else None, 'error_message': completed.get('error_message')}


def _generate_export_artifact(connection: Any, *, workspace_id: str, export_id: str) -> None:
    job = connection.execute('SELECT id, export_type, format, filters FROM export_jobs WHERE id = %s AND workspace_id = %s', (export_id, workspace_id)).fetchone()
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Export not found.')
    rows: list[dict[str, Any]]
    match str(job['export_type']):
        case 'history':
            rows = [_json_safe_value(dict(row)) for row in connection.execute('SELECT id, analysis_type, service_name, status, title, summary, created_at FROM analysis_runs WHERE workspace_id = %s ORDER BY created_at DESC LIMIT 1000', (workspace_id,)).fetchall()]
        case 'alerts':
            rows = [_json_safe_value(dict(row)) for row in connection.execute('SELECT id, alert_type, title, severity, status, module_key, target_id, created_at FROM alerts WHERE workspace_id = %s ORDER BY created_at DESC LIMIT 1000', (workspace_id,)).fetchall()]
        case 'findings' | 'report':
            rows = [_json_safe_value(dict(row)) for row in connection.execute('SELECT id, analysis_type, status, title, summary, created_at FROM analysis_runs WHERE workspace_id = %s ORDER BY created_at DESC LIMIT 1000', (workspace_id,)).fetchall()]
        case _:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='Unsupported export type.')
    export_root = Path(os.getenv('EXPORTS_DIR', '/tmp/decoda-exports')).resolve()
    workspace_dir = export_root / workspace_id
    workspace_dir.mkdir(parents=True, exist_ok=True)
    destination = workspace_dir / f'{export_id}.{job["format"]}'
    try:
        if str(job['format']) == 'json':
            destination.write_text(json.dumps({'rows': rows}, indent=2), encoding='utf-8')
        else:
            headers = sorted({key for row in rows for key in row.keys()})
            with destination.open('w', encoding='utf-8', newline='') as handle:
                writer = csv.DictWriter(handle, fieldnames=headers)
                writer.writeheader()
                for row in rows:
                    writer.writerow({key: _json_safe_value(row.get(key)) for key in headers})
        connection.execute("UPDATE export_jobs SET status = 'completed', error_message = NULL, updated_at = NOW() WHERE id = %s", (export_id,))
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
            SELECT id, export_type, format, status, output_path, error_message, created_at, updated_at
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


def get_export_artifact_path(export_id: str, request: Request) -> Path:
    require_live_mode()
    with pg_connection() as connection:
        ensure_pilot_schema(connection)
        user = authenticate_with_connection(connection, request)
        workspace_context = resolve_workspace(connection, user['id'], request.headers.get('x-workspace-id'))
        row = connection.execute('SELECT id, workspace_id, format, status FROM export_jobs WHERE id = %s AND workspace_id = %s', (export_id, workspace_context['workspace_id'])).fetchone()
        if row is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Export not found.')
        if str(row['status']) != 'completed':
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail='Export is not ready yet.')
        path = Path(os.getenv('EXPORTS_DIR', '/tmp/decoda-exports')).resolve() / str(row['workspace_id']) / f"{row['id']}.{row['format']}"
        if not path.exists():
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Export artifact missing.')
        return path


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
