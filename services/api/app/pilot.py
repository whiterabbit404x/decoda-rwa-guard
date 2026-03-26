from __future__ import annotations

import base64
import hashlib
import hmac
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
    return {'mode': mode, 'errors': errors, 'warnings': warnings}


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
            SELECT wm.user_id, wm.role, wm.created_at, u.email, u.full_name
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
                    'user_id': str(row['user_id']),
                    'email': str(row['email']),
                    'full_name': str(row['full_name']),
                    'role': _normalize_workspace_role(str(row['role'])),
                    'created_at': row['created_at'].isoformat() if hasattr(row['created_at'], 'isoformat') else str(row['created_at']),
                }
                for row in rows
            ],
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
                logger.exception('background job failed', extra={'event': 'jobs.failed', 'job_id': job_id, 'job_type': row['job_type']})
        connection.commit()
    return {'processed': processed, 'failed': failed}


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
    with pg_connection() as connection:
        ensure_pilot_schema(connection)
        user, workspace_context = _require_workspace_admin(connection, request)
        plan = connection.execute('SELECT plan_key, trial_days FROM plan_entitlements WHERE plan_key = %s', (plan_key,)).fetchone()
        if plan is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Unknown plan.')
        placeholder_subscription_id = f'sub_local_{uuid.uuid4().hex[:18]}'
        connection.execute(
            '''
            INSERT INTO billing_subscriptions (id, workspace_id, provider_subscription_id, plan_key, status, trial_ends_at, current_period_ends_at, metadata)
            VALUES (%s, %s, %s, %s, 'trialing', NOW() + (%s || ' days')::interval, NOW() + interval '1 month', %s::jsonb)
            ON CONFLICT (provider_subscription_id) DO NOTHING
            ''',
            (str(uuid.uuid4()), workspace_context['workspace_id'], placeholder_subscription_id, plan_key, int(plan['trial_days']), _json_dumps({'source': 'self_serve_checkout'})),
        )
        log_audit(connection, action='billing.checkout_session_created', entity_type='workspace', entity_id=workspace_context['workspace_id'], request=request, user_id=user['id'], workspace_id=workspace_context['workspace_id'], metadata={'plan_key': plan_key})
        connection.commit()
        return {
            'checkout_url': f"{os.getenv('APP_PUBLIC_URL', 'http://localhost:3000')}/settings?billing=success&plan={plan_key}",
            'plan_key': plan_key,
            'mode': 'placeholder',
            'message': 'Stripe checkout wiring placeholder created. Configure STRIPE_SECRET_KEY and Stripe price IDs for hosted checkout in production.',
        }


def create_portal_session(request: Request) -> dict[str, Any]:
    require_live_mode()
    with pg_connection() as connection:
        ensure_pilot_schema(connection)
        user, workspace_context = _require_workspace_admin(connection, request)
        return {
            'portal_url': f"{os.getenv('APP_PUBLIC_URL', 'http://localhost:3000')}/settings",
            'mode': 'placeholder',
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
        workspace_id = str(((payload.get('data') or {}).get('object') or {}).get('metadata', {}).get('workspace_id', '')).strip() or None
        connection.execute(
            '''
            INSERT INTO billing_events (id, provider, provider_event_id, workspace_id, event_type, payload, processing_status, processed_at)
            VALUES (%s, 'stripe', %s, %s, %s, %s::jsonb, 'processed', NOW())
            ''',
            (str(uuid.uuid4()), event_id, workspace_id, event_type, _json_dumps(payload)),
        )
        connection.commit()
        return {'received': True, 'duplicate': False, 'event_id': event_id, 'status': 'processed'}


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
            INSERT INTO workspace_webhooks (id, workspace_id, target_url, description, event_types, secret_hash, secret_last4, enabled, created_by_user_id)
            VALUES (%s, %s, %s, %s, %s::jsonb, %s, %s, TRUE, %s)
            ''',
            (
                webhook_id,
                workspace_context['workspace_id'],
                target_url,
                str(payload.get('description', '')).strip() or None,
                _json_dumps(event_types),
                hashlib.sha256(secret.encode('utf-8')).hexdigest(),
                secret[-4:],
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
            'UPDATE workspace_webhooks SET secret_hash = %s, secret_last4 = %s, updated_at = NOW() WHERE id = %s',
            (hashlib.sha256(secret.encode('utf-8')).hexdigest(), secret[-4:], webhook_id),
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
    return alert_id


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
