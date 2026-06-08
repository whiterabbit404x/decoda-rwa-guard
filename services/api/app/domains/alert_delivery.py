"""Transactional outbox and durable Redis Streams alert delivery workers."""
from __future__ import annotations

import json
import os
import socket
import uuid
from datetime import datetime, timezone
from typing import Any

from services.api.app.domains import alert_stream

BUS_STREAM = 'decoda:alert-events'
DEAD_LETTER_STREAM = 'decoda:alert-events:dead-letter'
DEFAULT_CONSUMER_GROUP = 'decoda-alert-delivery-v1'


def _int_env(name: str, default: int, minimum: int = 1) -> int:
    try:
        return max(minimum, int(os.getenv(name, str(default))))
    except ValueError:
        return default


def consumer_group() -> str:
    return os.getenv('ALERT_EVENT_CONSUMER_GROUP', DEFAULT_CONSUMER_GROUP).strip() or DEFAULT_CONSUMER_GROUP


def worker_identity(kind: str) -> str:
    configured = os.getenv('ALERT_EVENT_WORKER_ID', '').strip()
    return f'{kind}:{configured or socket.gethostname()}:{os.getpid()}'


def enqueue_alert_event(
    connection: Any,
    *,
    workspace_id: str,
    alert_id: str | None,
    event_type: str,
    payload: dict[str, Any],
    idempotency_key: str | None = None,
) -> str:
    """Insert an event in the caller's transaction; no event is visible before commit."""
    event_id = str(uuid.uuid4())
    key = idempotency_key or f'alert:{alert_id or event_id}:{event_type}'
    row = connection.execute(
        '''
        INSERT INTO alert_event_outbox
            (id, workspace_id, alert_id, event_type, payload, idempotency_key, max_attempts)
        VALUES (%s, %s, %s, %s, %s::jsonb, %s, %s)
        ON CONFLICT (idempotency_key) DO UPDATE SET idempotency_key = EXCLUDED.idempotency_key
        RETURNING id
        ''',
        (event_id, workspace_id, alert_id, event_type, json.dumps(payload, separators=(',', ':')), key,
         _int_env('ALERT_EVENT_MAX_ATTEMPTS', 5)),
    ).fetchone()
    return str(row['id'] if isinstance(row, dict) else row[0])


def _heartbeat(connection: Any, *, name: str, kind: str, success: bool, summary: dict[str, Any], error: Exception | None = None) -> None:
    lease_seconds = _int_env('ALERT_EVENT_WORKER_LEASE_SECONDS', 30)
    connection.execute(
        '''
        INSERT INTO alert_event_worker_state
            (worker_name, worker_kind, consumer_group, consumer_name, heartbeat_at, lease_expires_at,
             last_success_at, last_failure_at, consecutive_failures, last_error, last_summary, updated_at)
        VALUES (%s, %s, %s, %s, NOW(), NOW() + (%s * INTERVAL '1 second'),
                CASE WHEN %s THEN NOW() END, CASE WHEN %s THEN NULL ELSE NOW() END,
                CASE WHEN %s THEN 0 ELSE 1 END, %s, %s::jsonb, NOW())
        ON CONFLICT (worker_name) DO UPDATE SET
            heartbeat_at = NOW(), lease_expires_at = EXCLUDED.lease_expires_at,
            last_success_at = CASE WHEN %s THEN NOW() ELSE alert_event_worker_state.last_success_at END,
            last_failure_at = CASE WHEN %s THEN alert_event_worker_state.last_failure_at ELSE NOW() END,
            consecutive_failures = CASE WHEN %s THEN 0 ELSE alert_event_worker_state.consecutive_failures + 1 END,
            last_error = EXCLUDED.last_error, last_summary = EXCLUDED.last_summary, updated_at = NOW()
        ''',
        (name, kind, consumer_group() if kind == 'stream_consumer' else None, name if kind == 'stream_consumer' else None,
         lease_seconds, success, success, success, None if error is None else f'{type(error).__name__}: {str(error)[:1000]}',
         json.dumps(summary, separators=(',', ':')), success, success, success),
    )


def _claim_outbox(connection: Any, *, worker_id: str, limit: int) -> list[dict[str, Any]]:
    lease_seconds = _int_env('ALERT_EVENT_WORKER_LEASE_SECONDS', 30)
    rows = connection.execute(
        '''
        WITH candidates AS (
            SELECT id FROM alert_event_outbox
            WHERE (status IN ('pending', 'retry') OR (status = 'leased' AND lease_expires_at < NOW()))
              AND next_attempt_at <= NOW()
            ORDER BY created_at
            FOR UPDATE SKIP LOCKED
            LIMIT %s
        )
        UPDATE alert_event_outbox outbox
        SET status = 'leased', lease_owner = %s,
            lease_expires_at = NOW() + (%s * INTERVAL '1 second'), updated_at = NOW()
        FROM candidates WHERE outbox.id = candidates.id
        RETURNING outbox.*
        ''',
        (limit, worker_id, lease_seconds),
    ).fetchall()
    return [dict(row) for row in rows]


def publish_outbox_batch(connection: Any, *, worker_id: str | None = None, limit: int = 100, client: Any | None = None) -> dict[str, int]:
    """Lease committed rows and append them to the shared bus; expired leases are recoverable."""
    name = worker_id or worker_identity('outbox')
    redis_client = client or alert_stream._get_sync_client()
    summary = {'claimed': 0, 'published': 0, 'retried': 0, 'dead_lettered': 0}
    try:
        for row in _claim_outbox(connection, worker_id=name, limit=max(1, min(limit, 500))):
            summary['claimed'] += 1
            try:
                bus_id = redis_client.xadd(BUS_STREAM, {
                    'outbox_id': str(row['id']), 'workspace_id': str(row['workspace_id']),
                    'event_type': str(row['event_type']), 'idempotency_key': str(row['idempotency_key']),
                    'payload': json.dumps(row['payload']) if not isinstance(row['payload'], str) else row['payload'],
                })
                connection.execute(
                    '''UPDATE alert_event_outbox SET status = 'published', bus_event_id = %s,
                       published_at = NOW(), lease_owner = NULL, lease_expires_at = NULL, last_error = NULL,
                       updated_at = NOW() WHERE id = %s AND lease_owner = %s''',
                    (str(bus_id), row['id'], name),
                )
                summary['published'] += 1
            except Exception as exc:
                attempts = int(row.get('attempt_count') or 0) + 1
                terminal = attempts >= int(row.get('max_attempts') or 5)
                connection.execute(
                    '''UPDATE alert_event_outbox SET status = %s, attempt_count = %s,
                       next_attempt_at = NOW() + (LEAST(300, POWER(2, %s)) * INTERVAL '1 second'),
                       lease_owner = NULL, lease_expires_at = NULL, last_error = %s, updated_at = NOW()
                       WHERE id = %s AND lease_owner = %s''',
                    ('dead_letter' if terminal else 'retry', attempts, attempts, f'{type(exc).__name__}: {str(exc)[:1000]}', row['id'], name),
                )
                summary['dead_lettered' if terminal else 'retried'] += 1
        _heartbeat(connection, name=name, kind='outbox_publisher', success=True, summary=summary)
        return summary
    except Exception as exc:
        _heartbeat(connection, name=name, kind='outbox_publisher', success=False, summary=summary, error=exc)
        raise


def ensure_consumer_group(client: Any) -> None:
    try:
        client.xgroup_create(BUS_STREAM, consumer_group(), id='0-0', mkstream=True)
    except Exception as exc:
        if 'BUSYGROUP' not in str(exc):
            raise


def _deliver_idempotently(client: Any, row: dict[str, Any]) -> str:
    marker = f"decoda:alert-delivered:{row['idempotency_key']}"
    script = '''
    if redis.call('EXISTS', KEYS[1]) == 1 then return redis.call('GET', KEYS[1]) end
    local event_id = redis.call('XADD', KEYS[2], 'MAXLEN', '=', ARGV[1], '*', 'payload', ARGV[2], 'outbox_id', ARGV[3])
    redis.call('SET', KEYS[1], event_id)
    return event_id
    '''
    return str(client.eval(script, 2, marker, alert_stream.stream_key(str(row['workspace_id'])),
                           alert_stream.stream_max_length(), row['payload'], row['outbox_id']))


def consume_bus_batch(connection: Any, *, consumer_id: str | None = None, limit: int = 100, client: Any | None = None) -> dict[str, int]:
    """Process group messages, ACKing only after idempotent workspace delivery succeeds."""
    name = consumer_id or worker_identity('consumer')
    redis_client = client or alert_stream._get_sync_client()
    summary = {'received': 0, 'delivered': 0, 'retried': 0, 'dead_lettered': 0, 'recovered': 0}
    ensure_consumer_group(redis_client)
    try:
        min_idle = _int_env('ALERT_EVENT_RECOVERY_IDLE_MS', 30_000)
        recovered = redis_client.xautoclaim(BUS_STREAM, consumer_group(), name, min_idle, '0-0', count=limit)
        pending_messages = recovered[1] if recovered and len(recovered) > 1 else []
        summary['recovered'] = len(pending_messages)
        fresh = redis_client.xreadgroup(consumer_group(), name, {BUS_STREAM: '>'}, count=limit, block=1)
        messages = list(pending_messages)
        for _, batch in fresh or []:
            messages.extend(batch)
        for message_id, fields in messages[:limit]:
            summary['received'] += 1
            outbox_id = fields.get('outbox_id')
            try:
                _deliver_idempotently(redis_client, fields)
                connection.execute(
                    "UPDATE alert_event_outbox SET status = 'delivered', delivered_at = NOW(), updated_at = NOW() WHERE id = %s",
                    (outbox_id,),
                )
                connection.commit()
                redis_client.xack(BUS_STREAM, consumer_group(), message_id)
                summary['delivered'] += 1
            except Exception as exc:
                row = connection.execute(
                    'SELECT attempt_count, max_attempts FROM alert_event_outbox WHERE id = %s FOR UPDATE', (outbox_id,)
                ).fetchone()
                attempts = int((row or {}).get('attempt_count') or 0) + 1
                terminal = attempts >= int((row or {}).get('max_attempts') or 5)
                connection.execute(
                    'UPDATE alert_event_outbox SET status = %s, attempt_count = %s, last_error = %s, updated_at = NOW() WHERE id = %s',
                    ('dead_letter' if terminal else 'published', attempts, f'{type(exc).__name__}: {str(exc)[:1000]}', outbox_id),
                )
                if terminal:
                    redis_client.xadd(DEAD_LETTER_STREAM, {**fields, 'source_event_id': str(message_id), 'error': type(exc).__name__})
                    connection.commit()
                    redis_client.xack(BUS_STREAM, consumer_group(), message_id)
                    summary['dead_lettered'] += 1
                else:
                    summary['retried'] += 1
        _heartbeat(connection, name=name, kind='stream_consumer', success=True, summary=summary)
        return summary
    except Exception as exc:
        _heartbeat(connection, name=name, kind='stream_consumer', success=False, summary=summary, error=exc)
        raise


def health_snapshot(connection: Any | None = None) -> dict[str, Any]:
    """Return event bus, queue depth, DLQ, and durable worker lease health."""
    bus = alert_stream.connectivity_sync()
    snapshot: dict[str, Any] = {
        'backend': 'redis_streams', 'consumer_group': consumer_group(), 'bus': bus,
        'outbox': {'pending': None, 'published': None, 'dead_letter': None},
        'workers': {'outbox_publisher': False, 'stream_consumer': False},
        'ready': False,
    }
    if connection is None:
        return snapshot
    counts = connection.execute(
        "SELECT status, COUNT(*) AS count FROM alert_event_outbox GROUP BY status"
    ).fetchall()
    by_status = {str(row['status']): int(row['count']) for row in counts}
    snapshot['outbox'] = {
        'pending': sum(by_status.get(key, 0) for key in ('pending', 'retry', 'leased')),
        'published': by_status.get('published', 0), 'dead_letter': by_status.get('dead_letter', 0),
    }
    workers = connection.execute(
        '''SELECT worker_kind, BOOL_OR(heartbeat_at >= NOW() - INTERVAL '90 seconds' AND lease_expires_at >= NOW()) AS healthy
           FROM alert_event_worker_state GROUP BY worker_kind'''
    ).fetchall()
    for row in workers:
        snapshot['workers'][str(row['worker_kind'])] = bool(row['healthy'])
    snapshot['ready'] = bool(bus.get('connected') and all(snapshot['workers'].values()))
    return snapshot
