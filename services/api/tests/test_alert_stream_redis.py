from __future__ import annotations

import asyncio
import json

import pytest
from fastapi import HTTPException, Request

from services.api.app.domains import alert_stream, rate_limit


class SharedRedis:
    def __init__(self):
        self.values: dict[str, int] = {}
        self.streams: dict[str, list[tuple[str, dict[str, str]]]] = {}
        self.fail = False

    def ping(self):
        if self.fail:
            raise ConnectionError('down')
        return True

    def incr(self, key):
        if self.fail:
            raise ConnectionError('down')
        self.values[key] = self.values.get(key, 0) + 1
        return self.values[key]

    def expire(self, key, ttl):
        return True

    def xadd(self, key, fields, maxlen, approximate):
        messages = self.streams.setdefault(key, [])
        event_id = f'{len(messages) + 1}-0'
        messages.append((event_id, fields))
        if len(messages) > maxlen:
            del messages[:-maxlen]
        return event_id


class AsyncSharedRedis:
    def __init__(self, shared: SharedRedis):
        self.shared = shared

    async def ping(self):
        return self.shared.ping()

    async def xread(self, streams, block, count):
        key, cursor = next(iter(streams.items()))
        messages = self.shared.streams.get(key, [])
        if cursor == '$':
            return []
        selected = [(event_id, fields) for event_id, fields in messages if event_id > cursor][:count]
        return [(key, selected)] if selected else []


def request() -> Request:
    return Request({'type': 'http', 'method': 'POST', 'path': '/auth/signin', 'headers': [], 'client': ('198.51.100.4', 443)})


def test_workspace_stream_is_scoped_and_bounded(monkeypatch):
    shared = SharedRedis()
    monkeypatch.setenv('REDIS_URL', 'redis://shared')
    monkeypatch.setenv('ALERT_STREAM_MAX_LENGTH', '10')
    monkeypatch.setattr(alert_stream, '_sync_client', shared)

    for number in range(12):
        alert_stream.publish('workspace-a', {'number': number})
    alert_stream.publish('workspace-b', {'number': 99})

    assert alert_stream.stream_key('workspace-a') != alert_stream.stream_key('workspace-b')
    assert len(shared.streams[alert_stream.stream_key('workspace-a')]) == 10
    assert len(shared.streams[alert_stream.stream_key('workspace-b')]) == 1


def test_multi_replica_subscribers_receive_same_workspace_event(monkeypatch):
    async def scenario():
        shared = SharedRedis()
        async_client = AsyncSharedRedis(shared)
        monkeypatch.setenv('REDIS_URL', 'redis://shared')
        monkeypatch.setattr(alert_stream, '_sync_client', shared)
        monkeypatch.setattr(alert_stream, '_async_client', async_client)
        event_id = alert_stream.publish('workspace-a', {'type': 'alert'})

        replica_one = alert_stream.subscribe('workspace-a', last_event_id='0-0', block_ms=1)
        replica_two = alert_stream.subscribe('workspace-a', last_event_id='0-0', block_ms=1)
        first = await anext(replica_one)
        second = await anext(replica_two)
        await replica_one.aclose()
        await replica_two.aclose()

        assert first == (event_id, {'type': 'alert'})
        assert second == first
    asyncio.run(scenario())

def test_reconnect_cursor_resumes_after_last_event_id(monkeypatch):
    async def scenario():
        shared = SharedRedis()
        monkeypatch.setenv('REDIS_URL', 'redis://shared')
        monkeypatch.setattr(alert_stream, '_sync_client', shared)
        monkeypatch.setattr(alert_stream, '_async_client', AsyncSharedRedis(shared))
        first_id = alert_stream.publish('workspace-a', {'number': 1})
        second_id = alert_stream.publish('workspace-a', {'number': 2})

        subscriber = alert_stream.subscribe('workspace-a', last_event_id=first_id, block_ms=1)
        assert await anext(subscriber) == (second_id, {'number': 2})
        await subscriber.aclose()
    asyncio.run(scenario())

def test_auth_and_api_key_limits_share_backend_across_instances(monkeypatch):
    shared = SharedRedis()
    monkeypatch.setenv('APP_ENV', 'production')
    monkeypatch.setenv('REDIS_URL', 'redis://shared')
    monkeypatch.setattr(rate_limit, '_redis_rate_limiter', shared)

    for _ in range(rate_limit.AUTH_MAX_ATTEMPTS):
        rate_limit.enforce_auth_rate_limit(request(), 'api_key', 'same-api-key')
    with pytest.raises(HTTPException) as exc_info:
        rate_limit.enforce_auth_rate_limit(request(), 'api_key', 'same-api-key')
    assert exc_info.value.status_code == 429


def test_shared_rate_limit_backend_failure_fails_closed(monkeypatch):
    shared = SharedRedis()
    shared.fail = True
    monkeypatch.setenv('APP_ENV', 'production')
    monkeypatch.setenv('REDIS_URL', 'redis://shared')
    monkeypatch.setattr(rate_limit, '_redis_rate_limiter', shared)

    with pytest.raises(HTTPException) as exc_info:
        rate_limit.enforce_auth_rate_limit(request(), 'signin', 'user@example.com')
    assert exc_info.value.status_code == 503


def test_subscriber_reconnects_after_transient_backend_error(monkeypatch):
    class FlakyAsyncRedis(AsyncSharedRedis):
        def __init__(self, shared):
            super().__init__(shared)
            self.calls = 0

        async def xread(self, streams, block, count):
            self.calls += 1
            if self.calls == 1:
                raise ConnectionError('transient')
            return await super().xread(streams, block, count)

    async def scenario():
        shared = SharedRedis()
        monkeypatch.setenv('REDIS_URL', 'redis://shared')
        monkeypatch.setattr(alert_stream, '_sync_client', shared)
        monkeypatch.setattr(alert_stream, '_async_client', FlakyAsyncRedis(shared))

        async def no_delay(_seconds):
            return None

        monkeypatch.setattr(alert_stream.asyncio, 'sleep', no_delay)
        event_id = alert_stream.publish('workspace-a', {'number': 3})
        before = alert_stream.subscriber_health()['reconnects_total']
        subscriber = alert_stream.subscribe('workspace-a', last_event_id='0-0', block_ms=1)

        assert await anext(subscriber) == (None, None)
        assert await anext(subscriber) == (event_id, {'number': 3})
        await subscriber.aclose()
        assert alert_stream.subscriber_health()['reconnects_total'] == before + 1

    asyncio.run(scenario())
