"""Audit log helpers extracted from pilot.py.

Provides utc_now, _json_safe_value, _json_dumps, and log_audit with no
reverse dependency on pilot.py.
"""
from __future__ import annotations

import hashlib as _hl
import json
import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import Request


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def utc_now_iso() -> str:
    return utc_now().isoformat()


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


def _json_dumps(value: Any) -> str:
    return json.dumps(_json_safe_value(value), separators=(',', ':'))


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
    from services.api.app.evidence_signing import compute_audit_row_hash, canonical_json as _cj

    safe_metadata = metadata or {}
    request_id = request.headers.get('x-request-id') if request else None
    _client = getattr(request, 'client', None) if request else None
    ip_address = _client.host if _client else None
    if request_id and not safe_metadata.get('request_id'):
        safe_metadata = {**safe_metadata, 'request_id': request_id}
    if ip_address and not safe_metadata.get('source_ip'):
        safe_metadata = {**safe_metadata, 'source_ip': ip_address}

    row_id = str(uuid.uuid4())
    now = utc_now()
    now_iso = now.isoformat()

    previous_row_hash: str | None = None
    try:
        prev_row = connection.execute(
            '''
            SELECT row_hash FROM audit_logs
            WHERE workspace_id %s
              AND row_hash IS NOT NULL
            ORDER BY created_at DESC, id DESC
            LIMIT 1
            ''' % ('= %s' if workspace_id else 'IS NULL',),
            ((workspace_id,) if workspace_id else ()),
        ).fetchone()
        if prev_row:
            previous_row_hash = str(prev_row['row_hash']) if prev_row.get('row_hash') else None
    except Exception:
        previous_row_hash = None

    try:
        metadata_sha256 = _hl.sha256(_cj(safe_metadata)).hexdigest()
        row_hash = compute_audit_row_hash(
            row_id=row_id,
            workspace_id=workspace_id,
            user_id=user_id,
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            created_at_iso=now_iso,
            metadata_sha256=metadata_sha256,
            previous_row_hash=previous_row_hash,
        )
    except Exception:
        row_hash = None

    connection.execute(
        '''
        INSERT INTO audit_logs (id, workspace_id, user_id, action, entity_type, entity_id, ip_address, metadata, created_at, row_hash, previous_row_hash, hash_algorithm, sealed_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s, %s, %s, %s)
        ''',
        (
            row_id,
            workspace_id,
            user_id,
            action,
            entity_type,
            entity_id,
            ip_address,
            _json_dumps(safe_metadata),
            now,
            row_hash,
            previous_row_hash,
            'sha256' if row_hash else None,
            now if row_hash else None,
        ),
    )
