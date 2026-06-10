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
DEFAULT_MAX_LENGTH = 1000
DEFAULT_BLOCK_MS = 25_000

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


async def subscribe(
    workspace_id: str,
    *,
    last_event_id: str = '$',
    block_ms: int = DEFAULT_BLOCK_MS,
) -> AsyncIterator[tuple[str | None, dict[str, Any] | None]]:
    """Read a workspace stream, reconnecting with the last delivered ID."""
    cursor = last_event_id or '$'
    delay = 0.1
    connected = False
    _health_change(active_subscribers_delta=1)
    logger.info(
        'event=alert_stream_subscribe workspace_id=%s stream=%s cursor=%s',
        workspace_id, stream_key(workspace_id), cursor,
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
                        'event=alert_stream_connected workspace_id=%s stream=%s',
                        workspace_id, stream_key(workspace_id),
                    )
                rows = await client.xread({stream_key(workspace_id): cursor}, block=block_ms, count=100)
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
                            'event=alert_stream_delivery workspace_id=%s event_id=%s',
                            workspace_id, event_id,
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
                    'event=alert_stream_reconnect workspace_id=%s error=%s error_type=%s delay_seconds=%.1f',
                    workspace_id, str(exc)[:200], type(exc).__name__, delay,
                )
                await asyncio.sleep(delay)
                delay = min(delay * 2, 5.0)
                yield None, None
    finally:
        _health_change(active_subscribers_delta=-1)
        if connected:
            _health_change(connected_subscribers_delta=-1)
        logger.info('event=alert_stream_unsubscribe workspace_id=%s stream=%s', workspace_id, stream_key(workspace_id))
