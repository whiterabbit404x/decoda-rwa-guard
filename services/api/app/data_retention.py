from __future__ import annotations

import hashlib
import json
import os
import secrets
import uuid
from datetime import datetime
from typing import Any

from services.api.app.export_storage import load_export_storage

DATA_TARGETS = {
    'telemetry': ('telemetry_events', 'observed_at'),
    'detections': ('detections', 'detected_at'),
    'incidents': ('incidents', 'created_at'),
    'audit_logs': ('audit_logs', 'created_at'),
}
ANONYMIZE_SQL = {
    'telemetry': "UPDATE telemetry_events SET payload_json = '{}'::jsonb, payload_hash = NULL WHERE workspace_id = %s AND observed_at < %s",
    'detections': "UPDATE detections SET title = '[retained detection]', evidence_summary = '[anonymized by retention policy]', raw_evidence_json = '{}'::jsonb, updated_at = NOW() WHERE workspace_id = %s AND detected_at < %s",
    'incidents': "UPDATE incidents SET summary = '[retained incident]', payload = '{}'::jsonb, updated_at = NOW() WHERE workspace_id = %s AND created_at < %s",
    'audit_logs': "UPDATE audit_logs SET user_id = NULL, ip_address = NULL, metadata = jsonb_build_object('_retention_anonymized', true) WHERE workspace_id = %s AND created_at < %s",
}


def _json(value: Any) -> str:
    from services.api.app.pilot import _json_dumps
    return _json_dumps(value)


def _safe(value: Any) -> Any:
    from services.api.app.pilot import _json_safe_value
    return _json_safe_value(value)


def blocking_holds(connection: Any, *, workspace_id: str, data_classes: list[str], subject_user_id: str | None) -> list[str]:
    if subject_user_id:
        rows = connection.execute(
            """SELECT id, data_classes FROM workspace_legal_holds
               WHERE workspace_id = %s AND status = 'active'
                 AND (subject_user_id IS NULL OR subject_user_id = %s)""",
            (workspace_id, subject_user_id),
        ).fetchall()
    else:
        # Conservatively fence a whole-class sweep if any subject in that class is held.
        rows = connection.execute(
            "SELECT id, data_classes FROM workspace_legal_holds WHERE workspace_id = %s AND status = 'active'",
            (workspace_id,),
        ).fetchall()
    requested = set(data_classes)
    return sorted(str(row['id']) for row in rows if set(row.get('data_classes') or []) & requested)


def write_event(connection: Any, *, request_id: str, workspace_id: str, data_class: str,
                operation: str, records_affected: int = 0, details: dict[str, Any] | None = None,
                anchor_before: str | None = None, anchor_after: str | None = None,
                suffix: str = 'database') -> None:
    connection.execute(
        """INSERT INTO data_deletion_events
           (id, request_id, workspace_id, data_class, operation, records_affected,
            chain_anchor_before, chain_anchor_after, details, idempotency_key)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s)
           ON CONFLICT (idempotency_key) WHERE idempotency_key IS NOT NULL DO NOTHING""",
        (str(uuid.uuid4()), request_id, workspace_id, data_class, operation,
         max(int(records_affected), 0), anchor_before, anchor_after, _json(details or {}),
         f'{request_id}:{data_class}:{operation}:{suffix}'),
    )


def delete_registered_artifacts(connection: Any, *, request_id: str, workspace_id: str,
                                data_class: str, cutoff: datetime) -> int:
    rows = connection.execute(
        """SELECT id, provider, object_key, source_table, source_id
           FROM retention_external_artifacts
           WHERE workspace_id = %s AND data_class = %s AND created_at < %s AND deleted_at IS NULL
           ORDER BY created_at, id""",
        (workspace_id, data_class, cutoff),
    ).fetchall()
    if not rows:
        return 0
    storage = load_export_storage()
    for row in rows:
        provider = str(row.get('provider') or '').strip().lower()
        if provider not in {'export_storage', 'local', 's3'}:
            raise RuntimeError(f'Unsupported retention artifact provider: {provider}')
        key = str(row.get('object_key') or '').strip()
        if key:
            storage.delete_bytes(object_key=key)
        connection.execute(
            "UPDATE retention_external_artifacts SET deleted_at = NOW(), deletion_error = NULL WHERE id = %s AND workspace_id = %s AND deleted_at IS NULL",
            (row['id'], workspace_id),
        )
        write_event(connection, request_id=request_id, workspace_id=workspace_id, data_class=data_class,
                    operation='storage_delete', records_affected=1, suffix=f'artifact:{row["id"]}',
                    details={'artifact_id': str(row['id']), 'provider': provider, 'object_key': key,
                             'source_table': row.get('source_table'), 'source_id': _safe(row.get('source_id')),
                             'cutoff_at': _safe(cutoff)})
    return len(rows)


def execute_request(connection: Any, deletion: Any, *, worker_name: str) -> dict[str, Any]:
    from services.api.app.pilot import hash_password, utc_now, _validate_sql_identifier

    request_id = str(deletion['id'])
    workspace_id = str(deletion['workspace_id'])
    classes = [str(item) for item in (deletion.get('data_classes') or [])]
    subject = str(deletion.get('subject_user_id') or '').strip() or None
    holds = blocking_holds(connection, workspace_id=workspace_id, data_classes=classes, subject_user_id=subject)
    if holds:
        connection.execute(
            """UPDATE data_deletion_requests SET status = 'blocked_by_legal_hold', result = %s::jsonb,
               error_message = NULL, lease_owner = NULL, lease_expires_at = NULL, updated_at = NOW()
               WHERE id = %s AND workspace_id = %s""",
            (_json({'blocking_legal_hold_ids': holds}), request_id, workspace_id),
        )
        for data_class in classes:
            write_event(connection, request_id=request_id, workspace_id=workspace_id, data_class=data_class,
                        operation='blocked', suffix='legal-hold',
                        details={'blocking_legal_hold_ids': holds, 'worker_name': worker_name})
        return {'id': request_id, 'status': 'blocked_by_legal_hold', 'blocking_legal_hold_ids': holds}

    cutoff = deletion.get('cutoff_at') or utc_now()
    modes = (deletion.get('result') or {}).get('deletion_modes') or {}
    operations: dict[str, Any] = {}
    for data_class in classes:
        mode = str(modes.get(data_class) or ('anonymize' if data_class == 'user_data' else 'hard_delete'))
        affected = 0
        before = after = None
        if data_class in DATA_TARGETS:
            table, timestamp = DATA_TARGETS[data_class]
            if data_class == 'audit_logs':
                connection.execute("SELECT set_config('app.retention_worker', 'on', true)")
            _validate_sql_identifier(table, 'retention table')
            _validate_sql_identifier(timestamp, 'retention timestamp column')
            if data_class == 'audit_logs':
                row = connection.execute(
                    "SELECT row_hash FROM audit_logs WHERE workspace_id = %s AND created_at < %s AND row_hash IS NOT NULL ORDER BY created_at DESC, id DESC LIMIT 1",
                    (workspace_id, cutoff),
                ).fetchone()
                before = str(row['row_hash']) if row and row.get('row_hash') else None
            cursor = connection.execute(ANONYMIZE_SQL[data_class], (workspace_id, cutoff)) if mode == 'anonymize' else connection.execute(
                f'DELETE FROM {table} WHERE workspace_id = %s AND {timestamp} < %s', (workspace_id, cutoff))
            affected = max(int(cursor.rowcount or 0), 0)
            if data_class == 'audit_logs':
                row = connection.execute(
                    "SELECT row_hash FROM audit_logs WHERE workspace_id = %s AND row_hash IS NOT NULL ORDER BY created_at, id LIMIT 1",
                    (workspace_id,),
                ).fetchone()
                after = str(row['row_hash']) if row and row.get('row_hash') else None
        elif data_class == 'exports':
            rows = connection.execute(
                """SELECT id, storage_backend, storage_object_key FROM export_jobs
                   WHERE workspace_id = %s AND created_at < %s AND deleted_at IS NULL ORDER BY created_at, id""",
                (workspace_id, cutoff),
            ).fetchall()
            storage = load_export_storage()
            for row in rows:
                key = str(row.get('storage_object_key') or '').strip()
                if key:
                    storage.delete_bytes(object_key=key)
                connection.execute(
                    """UPDATE export_jobs SET deleted_at = NOW(), output_path = NULL, storage_object_key = NULL, updated_at = NOW()
                       WHERE id = %s AND workspace_id = %s AND deleted_at IS NULL""",
                    (row['id'], workspace_id),
                )
                affected += 1
                write_event(connection, request_id=request_id, workspace_id=workspace_id, data_class='exports',
                            operation='storage_delete', records_affected=1, suffix=f'export:{row["id"]}',
                            details={'export_job_id': str(row['id']), 'storage_backend': row.get('storage_backend'),
                                     'object_key': key, 'cutoff_at': _safe(cutoff)})
        elif data_class == 'user_data':
            if not subject:
                raise ValueError('user_data deletion requires subject_user_id')
            membership = connection.execute(
                'SELECT 1 AS present FROM workspace_members WHERE workspace_id = %s AND user_id = %s',
                (workspace_id, subject),
            ).fetchone()
            if membership:
                connection.execute('DELETE FROM auth_sessions WHERE user_id = %s', (subject,))
                connection.execute("UPDATE auth_tokens SET revoked_at = COALESCE(revoked_at, NOW()) WHERE user_id = %s", (subject,))
                cursor = connection.execute(
                    """UPDATE users SET email = %s, full_name = 'Deleted user', password_hash = %s,
                       current_workspace_id = NULL, deleted_at = COALESCE(deleted_at, NOW()), updated_at = NOW()
                       WHERE id = %s AND EXISTS (SELECT 1 FROM workspace_members wm
                                                 WHERE wm.user_id = users.id AND wm.workspace_id = %s)""",
                    (f'deleted+{subject}@invalid.local', hash_password(secrets.token_urlsafe(48)), subject, workspace_id),
                )
                affected = max(int(cursor.rowcount or 0), 0)
        external = delete_registered_artifacts(connection, request_id=request_id, workspace_id=workspace_id,
                                               data_class=data_class, cutoff=cutoff)
        operations[data_class] = {'records_affected': affected, 'external_artifacts_deleted': external, 'mode': mode}
        write_event(connection, request_id=request_id, workspace_id=workspace_id, data_class=data_class,
                    operation='anonymize' if mode == 'anonymize' else 'hard_delete', records_affected=affected,
                    anchor_before=before, anchor_after=after,
                    details={'cutoff_at': _safe(cutoff), 'worker_name': worker_name,
                             'external_artifacts_deleted': external, 'subject_user_id': subject})
    report = {
        'request_id': request_id,
        'workspace_id': workspace_id,
        'cutoff_at': _safe(cutoff),
        'operations': operations,
        'worker_name': worker_name,
    }
    report_sha256 = hashlib.sha256(json.dumps(report, sort_keys=True, separators=(',', ':')).encode('utf-8')).hexdigest()
    result = {'operations': operations, 'deletion_report': report, 'deletion_report_sha256': report_sha256}
    connection.execute(
        """UPDATE data_deletion_requests SET status = 'completed', result = %s::jsonb, error_message = NULL,
           completed_at = NOW(), lease_owner = NULL, lease_expires_at = NULL, updated_at = NOW()
           WHERE id = %s AND workspace_id = %s""",
        (_json(result), request_id, workspace_id),
    )
    return {'id': request_id, 'status': 'completed', **result}


def schedule_requests(connection: Any) -> int:
    cursor = connection.execute(
        """INSERT INTO data_deletion_requests
           (id, workspace_id, request_type, data_classes, cutoff_at, status, reason,
            requested_by_user_id, result, idempotency_key, next_attempt_at)
           SELECT gen_random_uuid(), p.workspace_id, 'retention_sweep', jsonb_build_array(p.data_class),
                  NOW() - make_interval(days => p.retention_days), 'approved',
                  'Scheduled workspace retention policy sweep', actor.user_id,
                  jsonb_build_object('deletion_modes', jsonb_build_object(p.data_class, p.deletion_mode),
                                     'retention_days', p.retention_days),
                  concat('retention:', p.workspace_id, ':', p.data_class, ':', CURRENT_DATE::text), NOW()
           FROM workspace_retention_policies p
           JOIN LATERAL (SELECT wm.user_id FROM workspace_members wm WHERE wm.workspace_id = p.workspace_id
                         ORDER BY CASE WHEN wm.user_id = p.updated_by_user_id THEN 0
                                       WHEN wm.role IN ('owner', 'workspace_owner') THEN 1 ELSE 2 END, wm.created_at LIMIT 1) actor ON TRUE
           WHERE p.enabled = TRUE
           ON CONFLICT (idempotency_key) WHERE idempotency_key IS NOT NULL DO NOTHING"""
    )
    return max(int(cursor.rowcount or 0), 0)


def claim_request(connection: Any, *, worker_name: str) -> Any | None:
    return connection.execute(
        """WITH candidate AS (SELECT id FROM data_deletion_requests
               WHERE status IN ('approved', 'running') AND next_attempt_at <= NOW()
                 AND attempt_count < max_attempts
                 AND (status <> 'running' OR lease_expires_at IS NULL OR lease_expires_at < NOW())
               ORDER BY requested_at, id FOR UPDATE SKIP LOCKED LIMIT 1)
           UPDATE data_deletion_requests d SET status = 'running', attempt_count = d.attempt_count + 1,
               last_attempt_at = NOW(), started_at = COALESCE(d.started_at, NOW()), lease_owner = %s,
               lease_expires_at = NOW() + INTERVAL '15 minutes', updated_at = NOW()
           FROM candidate WHERE d.id = candidate.id RETURNING d.*""",
        (worker_name,),
    ).fetchone()


def record_failure(request_id: str, *, worker_name: str, error: Exception) -> bool:
    from services.api.app.pilot import pg_connection
    with pg_connection() as connection:
        row = connection.execute(
            """UPDATE data_deletion_requests
               SET status = CASE WHEN attempt_count >= max_attempts THEN 'failed' ELSE 'approved' END,
                   error_message = %s,
                   next_attempt_at = NOW() + make_interval(secs => LEAST(3600, 30 * power(2, GREATEST(attempt_count - 1, 0)))::int),
                   lease_owner = NULL, lease_expires_at = NULL, updated_at = NOW()
               WHERE id = %s AND lease_owner = %s RETURNING status""",
            (f'{type(error).__name__}: {str(error)[:1500]}', request_id, worker_name),
        ).fetchone()
        return bool(row and row['status'] == 'failed')


def worker_health(*, stale_after_seconds: int | None = None) -> dict[str, Any]:
    from services.api.app.pilot import database_url, pg_connection
    threshold = stale_after_seconds or int(os.getenv('RETENTION_WORKER_STALE_AFTER_SECONDS', '900'))
    base = {'fresh': False, 'stale_after_seconds': threshold, 'workers': [],
            'most_recent_completed_sweep_at': None, 'failures': 0}
    if not database_url():
        return {'status': 'not_configured', **base}
    try:
        with pg_connection() as connection:
            rows = connection.execute(
                """SELECT worker_name, heartbeat_at, last_completed_sweep_at, last_failure_at,
                          consecutive_failures, last_error, last_summary,
                          heartbeat_at >= NOW() - make_interval(secs => %s) AS fresh
                   FROM retention_worker_state ORDER BY heartbeat_at DESC""", (threshold,)).fetchall()
    except Exception as exc:
        return {'status': 'unavailable', **base, 'error': f'{type(exc).__name__}: {str(exc)[:300]}'}
    workers = [_safe(dict(row)) for row in rows]
    fresh = bool(workers and any(bool(row.get('fresh')) for row in rows))
    latest = max((row.get('last_completed_sweep_at') for row in rows if row.get('last_completed_sweep_at')), default=None)
    failures = sum(int(row.get('consecutive_failures') or 0) for row in rows)
    return {'status': 'healthy' if fresh and failures == 0 else ('degraded' if workers else 'not_running'),
            'fresh': fresh, 'stale_after_seconds': threshold, 'workers': workers,
            'most_recent_completed_sweep_at': _safe(latest), 'failures': failures}
