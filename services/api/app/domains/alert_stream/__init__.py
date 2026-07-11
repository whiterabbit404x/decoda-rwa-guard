"""Redis Streams transport for workspace-scoped real-time alerts."""
from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
from datetime import datetime, timezone
from typing import Any, AsyncIterator

import redis
import redis.asyncio as aioredis

logger = logging.getLogger(__name__)

STREAM_PREFIX = 'decoda:workspace:'
STREAM_SUFFIX = ':alerts'
# Separate workspace-scoped stream for real-time telemetry rows. Kept distinct
# from ':alerts' so a telemetry subscriber never receives alert/incident events
# (and vice versa) and each stream is bounded independently — the task's
# "telemetry event type clearly distinguishable from alerts/incidents" and
# "no cross-workspace leakage" requirements. Same transport, reconnect, and
# health machinery are reused for both.
TELEMETRY_STREAM_SUFFIX = ':telemetry'
# Dedicated workspace-scoped stream for incident AI-investigation events
# (triage queued/running/completed/failed, report generated, recommendation
# reviewed). Kept distinct from ':alerts' and ':telemetry' so an incident-page
# subscriber never receives unrelated envelopes and each stream is bounded
# independently. Same transport/health machinery is reused.
INCIDENTS_STREAM_SUFFIX = ':incidents'
DEFAULT_MAX_LENGTH = 1000
# Redis xread block interval == the SSE heartbeat cadence: when no event arrives
# within this window the subscriber yields a ``(None, None)`` tick and the SSE
# endpoint writes a ``: heartbeat`` comment. The task requires a heartbeat at least
# every 15–25s so an idle-timeout proxy (Railway edge / Next.js fetch) never treats
# a quiet-but-healthy stream as dead and forces a reconnect. 25s sat at the very
# edge of that window (the production "Reconnecting…" flap); 15s keeps every idle
# connection well inside it. Overridable for ops tuning via SSE_HEARTBEAT_INTERVAL_SECONDS.
DEFAULT_BLOCK_MS = 15_000


def heartbeat_block_ms() -> int:
    """SSE heartbeat/xread block interval in ms, clamped to the 5–25s safe window."""
    raw = (os.getenv('SSE_HEARTBEAT_INTERVAL_SECONDS', '') or '').strip()
    if not raw:
        return DEFAULT_BLOCK_MS
    try:
        seconds = int(raw)
    except ValueError:
        return DEFAULT_BLOCK_MS
    return max(5, min(25, seconds)) * 1000

_sync_client: Any | None = None
_async_client: Any | None = None
_client_lock = threading.Lock()
_health_lock = threading.Lock()
_health: dict[str, Any] = {
    'active_subscribers': 0,
    'connected_subscribers': 0,
    'reconnects_total': 0,
    'subscriber_errors_total': 0,
    'last_subscriber_error': None,
    'last_message_at': None,
}


def redis_url() -> str | None:
    return os.getenv('REDIS_URL', '').strip() or None


def stream_key(workspace_id: str) -> str:
    return f'{STREAM_PREFIX}{workspace_id}{STREAM_SUFFIX}'


def telemetry_stream_key(workspace_id: str) -> str:
    return f'{STREAM_PREFIX}{workspace_id}{TELEMETRY_STREAM_SUFFIX}'


def incidents_stream_key(workspace_id: str) -> str:
    return f'{STREAM_PREFIX}{workspace_id}{INCIDENTS_STREAM_SUFFIX}'


def stream_max_length() -> int:
    try:
        return max(10, int(os.getenv('ALERT_STREAM_MAX_LENGTH', str(DEFAULT_MAX_LENGTH))))
    except ValueError:
        return DEFAULT_MAX_LENGTH


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _get_sync_client() -> Any:
    global _sync_client
    url = redis_url()
    if not url:
        raise RuntimeError('REDIS_URL is required for alert streaming')
    with _client_lock:
        if _sync_client is None:
            _sync_client = redis.Redis.from_url(
                url, decode_responses=True, socket_connect_timeout=2, socket_timeout=2
            )
    return _sync_client


def _get_async_client() -> Any:
    global _async_client
    url = redis_url()
    if not url:
        raise RuntimeError('REDIS_URL is required for alert streaming')
    if _async_client is None:
        _async_client = aioredis.from_url(
            url, decode_responses=True, socket_connect_timeout=2, socket_timeout=30
        )
    return _async_client


def publish(workspace_id: str, alert_data: dict[str, Any]) -> str:
    """Append an alert to the bounded workspace stream and return its Redis ID."""
    try:
        event_id = str(
            _get_sync_client().xadd(
                stream_key(workspace_id),
                {'payload': json.dumps(alert_data, separators=(',', ':'))},
                maxlen=stream_max_length(),
                approximate=False,
            )
        )
        logger.info(
            'event=alert_stream_publish workspace_id=%s stream=%s event_id=%s',
            workspace_id, stream_key(workspace_id), event_id,
        )
        return event_id
    except Exception as exc:
        logger.error(
            'event=alert_stream_publish_failure workspace_id=%s error=%s error_type=%s',
            workspace_id, str(exc)[:200], type(exc).__name__,
        )
        raise


def publish_telemetry(workspace_id: str, telemetry_data: dict[str, Any]) -> str:
    """Append a telemetry event to the bounded workspace :telemetry stream.

    Mirrors :func:`publish` but targets the workspace's telemetry stream so a
    live wallet-transfer row can be pushed to the Target Telemetry page without a
    refetch. Same bounded ``maxlen`` and JSON envelope. The caller is expected to
    invoke this only AFTER the telemetry row is durably committed, and to treat a
    raised exception as non-fatal (persistence already succeeded).
    """
    stream = telemetry_stream_key(workspace_id)
    event_id = str(
        _get_sync_client().xadd(
            stream,
            {'payload': json.dumps(telemetry_data, separators=(',', ':'))},
            maxlen=stream_max_length(),
            approximate=False,
        )
    )
    # Canonical structured evidence (task "Redis publication requirements"):
    # event=telemetry_redis_publish with telemetry_id / stream_key / success /
    # redis_event_id so a delivered row is provable from Railway logs alone. No
    # secrets — only the opaque telemetry_id, the workspace stream key, and the
    # Redis-assigned event id.
    logger.info(
        'event=telemetry_redis_publish telemetry_id=%s workspace_id=%s stream_key=%s success=true redis_event_id=%s',
        telemetry_data.get('telemetry_id'), workspace_id, stream, event_id,
    )
    return event_id


def publish_incident(workspace_id: str, incident_data: dict[str, Any]) -> str:
    """Append an incident AI-investigation event to the workspace :incidents stream.

    Mirrors :func:`publish` but targets the incidents stream so the incident
    details page can update the AI Investigation section in real time. The caller
    invokes this only AFTER the database commit and treats a raised exception as
    non-fatal (the stored analysis is already durable; the page recovers on its
    next HTTP fetch).
    """
    stream = incidents_stream_key(workspace_id)
    event_id = str(
        _get_sync_client().xadd(
            stream,
            {'payload': json.dumps(incident_data, separators=(',', ':'))},
            maxlen=stream_max_length(),
            approximate=False,
        )
    )
    logger.info(
        'event=incident_redis_publish incident_id=%s workspace_id=%s stream_key=%s success=true redis_event_id=%s event_type=%s',
        incident_data.get('incident_id'), workspace_id, stream, event_id, incident_data.get('event_type'),
    )
    return event_id


def connectivity_sync() -> dict[str, Any]:
    """Synchronous connectivity probe for operational health endpoints."""
    configured = redis_url() is not None
    if not configured:
        return {'configured': False, 'connected': False, 'status': 'not_configured'}
    try:
        _get_sync_client().ping()
        return {'configured': True, 'connected': True, 'status': 'healthy'}
    except Exception as exc:
        return {
            'configured': True, 'connected': False, 'status': 'unavailable',
            'error': type(exc).__name__,
        }


async def connectivity() -> dict[str, Any]:
    """Return a safe shared-backend connectivity snapshot."""
    configured = redis_url() is not None
    if not configured:
        return {'configured': False, 'connected': False, 'status': 'not_configured'}
    try:
        await _get_async_client().ping()
        return {'configured': True, 'connected': True, 'status': 'healthy'}
    except Exception as exc:
        return {
            'configured': True,
            'connected': False,
            'status': 'unavailable',
            'error': type(exc).__name__,
        }


def subscriber_health() -> dict[str, Any]:
    with _health_lock:
        snapshot = dict(_health)
    snapshot.update(
        {
            'backend': 'redis_streams',
            'workspace_scoped': True,
            'bounded_max_length': stream_max_length(),
        }
    )
    return snapshot


def _health_change(**changes: Any) -> None:
    with _health_lock:
        for key, value in changes.items():
            if key.endswith('_delta'):
                target = key[:-6]
                _health[target] = max(0, int(_health.get(target, 0)) + int(value))
            else:
                _health[key] = value


async def _subscribe_stream(
    key: str,
    *,
    workspace_id: str,
    kind: str,
    last_event_id: str = '$',
    block_ms: int | None = None,
) -> AsyncIterator[tuple[str | None, dict[str, Any] | None]]:
    """Read a bounded workspace stream, reconnecting with the last delivered ID.

    Shared by :func:`subscribe` (``:alerts``) and :func:`subscribe_telemetry`
    (``:telemetry``); ``kind`` only tags the log lines so the two streams are
    distinguishable in Railway logs while reusing one reconnect/health path.

    ``block_ms`` defaults to :func:`heartbeat_block_ms` so a quiet stream still
    ticks a heartbeat inside the 15–25s proxy-idle window.
    """
    if block_ms is None:
        block_ms = heartbeat_block_ms()
    cursor = last_event_id or '$'
    delay = 0.1
    connected = False
    _health_change(active_subscribers_delta=1)
    logger.info(
        'event=%s_stream_subscribe workspace_id=%s stream=%s cursor=%s',
        kind, workspace_id, key, cursor,
    )
    try:
        while True:
            try:
                client = _get_async_client()
                if not connected:
                    await client.ping()
                    connected = True
                    _health_change(connected_subscribers_delta=1)
                    logger.info(
                        'event=%s_stream_connected workspace_id=%s stream=%s',
                        kind, workspace_id, key,
                    )
                rows = await client.xread({key: cursor}, block=block_ms, count=100)
                delay = 0.1
                if not rows:
                    yield None, None
                    continue
                for _, messages in rows:
                    for event_id, fields in messages:
                        cursor = str(event_id)
                        raw = fields.get('payload')
                        if raw is None:
                            continue
                        try:
                            payload = json.loads(raw)
                        except (TypeError, json.JSONDecodeError):
                            continue
                        _health_change(last_message_at=_utc_now())
                        logger.debug(
                            'event=%s_stream_delivery workspace_id=%s event_id=%s',
                            kind, workspace_id, event_id,
                        )
                        yield cursor, payload
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                if connected:
                    connected = False
                    _health_change(connected_subscribers_delta=-1)
                _health_change(
                    reconnects_total_delta=1,
                    subscriber_errors_total_delta=1,
                    last_subscriber_error={'at': _utc_now(), 'type': type(exc).__name__},
                )
                logger.warning(
                    'event=%s_stream_reconnect workspace_id=%s error=%s error_type=%s delay_seconds=%.1f',
                    kind, workspace_id, str(exc)[:200], type(exc).__name__, delay,
                )
                await asyncio.sleep(delay)
                delay = min(delay * 2, 5.0)
                yield None, None
    finally:
        _health_change(active_subscribers_delta=-1)
        if connected:
            _health_change(connected_subscribers_delta=-1)
        logger.info('event=%s_stream_unsubscribe workspace_id=%s stream=%s', kind, workspace_id, key)


async def subscribe(
    workspace_id: str,
    *,
    last_event_id: str = '$',
    block_ms: int | None = None,
) -> AsyncIterator[tuple[str | None, dict[str, Any] | None]]:
    """Read a workspace ALERT stream, reconnecting with the last delivered ID."""
    async for item in _subscribe_stream(
        stream_key(workspace_id), workspace_id=workspace_id, kind='alert',
        last_event_id=last_event_id, block_ms=block_ms,
    ):
        yield item


async def subscribe_telemetry(
    workspace_id: str,
    *,
    last_event_id: str = '$',
    block_ms: int | None = None,
) -> AsyncIterator[tuple[str | None, dict[str, Any] | None]]:
    """Read a workspace TELEMETRY stream, reconnecting with the last delivered ID."""
    async for item in _subscribe_stream(
        telemetry_stream_key(workspace_id), workspace_id=workspace_id, kind='telemetry',
        last_event_id=last_event_id, block_ms=block_ms,
    ):
        yield item
