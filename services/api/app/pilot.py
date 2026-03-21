from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from time import monotonic
from typing import Any, Iterable

import importlib
from fastapi import HTTPException, Request, status

ROLE_VALUES = {'workspace_owner', 'workspace_admin', 'workspace_member'}
AUTH_WINDOW_SECONDS = 60
AUTH_MAX_ATTEMPTS = 10
_rate_limit_lock = threading.Lock()
_rate_limit_state: dict[str, list[float]] = {}


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
    return os.getenv('LIVE_MODE_ENABLED', 'false').strip().lower() in {'1', 'true', 'yes', 'on'} and database_url() is not None


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


def run_migrations() -> list[str]:
    require_live_mode()
    applied_versions: list[str] = []
    with pg_connection() as connection:
        ensure_migration_table(connection)
        already_applied = {
            row['version'] for row in connection.execute('SELECT version FROM schema_migrations').fetchall()
        }
        for path in sorted(migration_dir().glob('*.sql')):
            if path.name in already_applied:
                continue
            connection.execute(path.read_text())
            connection.execute('INSERT INTO schema_migrations (version) VALUES (%s)', (path.name,))
            applied_versions.append(path.name)
        connection.commit()
    return applied_versions


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


def create_access_token(user_id: str) -> str:
    payload = {
        'sub': user_id,
        'exp': int((utc_now() + timedelta(hours=24)).timestamp()),
        'iat': int(utc_now().timestamp()),
        'jti': str(uuid.uuid4()),
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


def enforce_auth_rate_limit(request: Request, action: str) -> None:
    client_host = request.client.host if request.client else 'unknown'
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
    return json.dumps(value, separators=(',', ':'), default=str)


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
        SELECT id, email, full_name, current_workspace_id, created_at, updated_at, last_sign_in_at
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
    membership_payload = [
        {
            'workspace_id': membership['workspace_id'],
            'role': membership['role'],
            'created_at': membership['created_at'].isoformat() if hasattr(membership['created_at'], 'isoformat') else str(membership['created_at']),
            'workspace': {
                'id': membership['workspace_id'],
                'name': membership['name'],
                'slug': membership['slug'],
            },
        }
        for membership in memberships
    ]
    current_workspace = next(
        (
            membership['workspace']
            for membership in membership_payload
            if membership['workspace_id'] == user['current_workspace_id']
        ),
        membership_payload[0]['workspace'] if membership_payload else None,
    )
    return {
        'id': user['id'],
        'email': user['email'],
        'full_name': user['full_name'],
        'created_at': user['created_at'].isoformat() if hasattr(user['created_at'], 'isoformat') else str(user['created_at']),
        'updated_at': user['updated_at'].isoformat() if hasattr(user['updated_at'], 'isoformat') else str(user['updated_at']),
        'last_sign_in_at': user['last_sign_in_at'].isoformat() if user['last_sign_in_at'] else None,
        'current_workspace': current_workspace,
        'memberships': membership_payload,
    }


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
    _require_password(password)
    full_name = str(payload.get('full_name', '')).strip() or email.split('@', 1)[0]
    workspace_name = str(payload.get('workspace_name', '')).strip() or f"{full_name}'s Workspace"
    password_hash = hash_password(password)
    with pg_connection() as connection:
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
            INSERT INTO users (id, email, password_hash, full_name, current_workspace_id, created_at, updated_at, last_sign_in_at)
            VALUES (%s, %s, %s, %s, %s, NOW(), NOW(), NOW())
            ''',
            (user_id, email, password_hash, full_name, workspace_id),
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
            (str(uuid.uuid4()), workspace_id, user_id, 'workspace_owner'),
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
        connection.commit()
        user = build_user_response(connection, user_id)
    return {'access_token': create_access_token(user_id), 'token_type': 'bearer', 'user': user}


def signin_user(payload: dict[str, Any], request: Request) -> dict[str, Any]:
    require_live_mode()
    email = _normalize_email(str(payload.get('email', '')))
    password = str(payload.get('password', ''))
    with pg_connection() as connection:
        user = connection.execute(
            'SELECT id, password_hash FROM users WHERE email = %s',
            (email,),
        ).fetchone()
        if user is None or not verify_password(password, user['password_hash']):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail='Invalid email or password.')
        connection.execute('UPDATE users SET last_sign_in_at = NOW(), updated_at = NOW() WHERE id = %s', (user['id'],))
        log_audit(
            connection,
            action='auth.signin',
            entity_type='user',
            entity_id=user['id'],
            request=request,
            user_id=user['id'],
            workspace_id=None,
            metadata={'email': email},
        )
        connection.commit()
        hydrated_user = build_user_response(connection, user['id'])
    return {'access_token': create_access_token(user['id']), 'token_type': 'bearer', 'user': hydrated_user}


def signout_user(request: Request) -> dict[str, Any]:
    require_live_mode()
    with pg_connection() as connection:
        user = authenticate_with_connection(connection, request)
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


def create_workspace_for_user(payload: dict[str, Any], request: Request) -> dict[str, Any]:
    require_live_mode()
    workspace_name = str(payload.get('name', '')).strip()
    if not workspace_name:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='Workspace name is required.')
    role = str(payload.get('role', 'workspace_owner')).strip() or 'workspace_owner'
    if role not in ROLE_VALUES:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='Invalid workspace role.')
    with pg_connection() as connection:
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
        return build_user_response(connection, user['id'])


def select_workspace_for_user(workspace_id: str, request: Request) -> dict[str, Any]:
    require_live_mode()
    with pg_connection() as connection:
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
        return build_user_response(connection, user['id'])


def list_user_workspaces(request: Request) -> dict[str, Any]:
    require_live_mode()
    with pg_connection() as connection:
        user = authenticate_with_connection(connection, request)
        return {'workspaces': user['memberships'], 'current_workspace': user['current_workspace']}


def seed_demo_workspace(email: str, password: str, workspace_name: str, full_name: str = 'Pilot Demo User') -> dict[str, Any]:
    require_live_mode()
    normalized_email = _normalize_email(email)
    _require_password(password)
    with pg_connection() as connection:
        existing = connection.execute('SELECT id FROM users WHERE email = %s', (normalized_email,)).fetchone()
        if existing is not None:
            user = build_user_response(connection, existing['id'])
            return {'seeded': False, 'user': user, 'email': normalized_email, 'password': '[unchanged]'}
    response = signup_user(
        {
            'email': normalized_email,
            'password': password,
            'full_name': full_name,
            'workspace_name': workspace_name,
        },
        Request({'type': 'http', 'client': ('seed-script', 0), 'headers': []}),
    )
    return {'seeded': True, 'user': response['user'], 'email': normalized_email, 'password': password}


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
                else:
                    item[key] = value
            serialized.append(item)
        return serialized

    return {
        'mode': 'live',
        'workspace': workspace_context['workspace'],
        'role': workspace_context['role'],
        'counts': counts,
        'analysis_runs': serialize(analysis_runs),
        'alerts': serialize(alerts),
        'governance_actions': serialize(governance_actions),
        'incidents': serialize(incidents),
        'audit_logs': serialize(audit_logs),
    }
