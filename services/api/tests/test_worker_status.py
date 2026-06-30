"""Tests for the separated worker status helper (worker_status.py).

Acceptance focus:
  * With realtime disabled and stable polling active, the headline reads
    "Stable polling active. Realtime WebSocket paused." and realtime shows
    paused/disabled — never a generic "worker heartbeat is stale".
  * "heartbeat is stale" only appears when STABLE polling is actually stale.
  * A QuickNode WSS rate-limit surfaces as provider rate_limited / cooldown
    without marking the whole monitoring source dead.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from services.api.app.worker_status import build_worker_status, realtime_enabled


def _now() -> datetime:
    return datetime(2026, 6, 30, 12, 0, tzinfo=timezone.utc)


TTL = 180


# ---------------------------------------------------------------------------
# realtime_enabled() — fail-closed parsing
# ---------------------------------------------------------------------------

def test_realtime_enabled_defaults_false(monkeypatch):
    monkeypatch.delenv('BASE_REALTIME_ENABLED', raising=False)
    assert realtime_enabled() is False


def test_realtime_enabled_blank_is_false(monkeypatch):
    monkeypatch.setenv('BASE_REALTIME_ENABLED', '   ')
    assert realtime_enabled() is False


def test_realtime_enabled_true(monkeypatch):
    monkeypatch.setenv('BASE_REALTIME_ENABLED', 'true')
    assert realtime_enabled() is True


def test_realtime_enabled_unknown_value_is_false(monkeypatch):
    monkeypatch.setenv('BASE_REALTIME_ENABLED', 'maybe')
    assert realtime_enabled() is False


# ---------------------------------------------------------------------------
# Acceptance: realtime disabled + stable polling active
# ---------------------------------------------------------------------------

def test_realtime_disabled_stable_active_headline():
    now = _now()
    status = build_worker_status(
        now=now,
        realtime_is_enabled=False,
        stable_last_heartbeat_at=now - timedelta(seconds=20),
        stable_last_poll_at=now - timedelta(seconds=20),
        heartbeat_ttl_seconds=TTL,
        realtime_watcher=None,
    )
    assert status['headline'] == 'Stable polling active. Realtime WebSocket paused.'
    assert status['stable_polling']['state'] == 'active'
    assert status['stable_polling']['detection_supported'] is True
    assert status['realtime']['enabled'] is False
    assert status['realtime']['state'] == 'paused'
    assert status['realtime']['reason'] == 'BASE_REALTIME_ENABLED_not_true'
    assert status['monitoring_source_live'] is True
    # Must NOT claim a stale heartbeat when stable polling is fresh.
    assert 'stale' not in status['headline'].lower()


def test_realtime_disabled_provider_not_applicable_when_no_rate_limit():
    now = _now()
    status = build_worker_status(
        now=now,
        realtime_is_enabled=False,
        stable_last_heartbeat_at=now - timedelta(seconds=10),
        stable_last_poll_at=now - timedelta(seconds=10),
        heartbeat_ttl_seconds=TTL,
        realtime_watcher=None,
    )
    assert status['provider_realtime']['state'] == 'not_applicable'


# ---------------------------------------------------------------------------
# Stable polling staleness drives the "stale" message — nothing else does
# ---------------------------------------------------------------------------

def test_stale_heartbeat_only_when_stable_polling_actually_stale():
    now = _now()
    status = build_worker_status(
        now=now,
        realtime_is_enabled=False,
        stable_last_heartbeat_at=now - timedelta(seconds=TTL + 600),
        stable_last_poll_at=now - timedelta(seconds=TTL + 600),
        heartbeat_ttl_seconds=TTL,
        realtime_watcher=None,
    )
    assert status['stable_polling']['state'] == 'stale'
    assert status['stable_polling']['detection_supported'] is False
    assert status['headline'] == 'Stable RPC polling heartbeat is stale.'
    assert status['monitoring_source_live'] is False


def test_stable_polling_never_reported_is_offline_not_stale():
    now = _now()
    status = build_worker_status(
        now=now,
        realtime_is_enabled=False,
        stable_last_heartbeat_at=None,
        stable_last_poll_at=None,
        heartbeat_ttl_seconds=TTL,
        realtime_watcher=None,
    )
    assert status['stable_polling']['state'] == 'offline'
    assert status['headline'] == 'Stable RPC polling worker is not reporting.'


# ---------------------------------------------------------------------------
# Acceptance: QuickNode WSS rate-limited -> provider cooldown, source still live
# ---------------------------------------------------------------------------

def test_provider_rate_limited_cooldown_does_not_kill_source():
    now = _now()
    watcher = {
        'watcher_name': 'base-realtime-worker-abc',
        'source_status': 'provider_rate_limited',
        'degraded': True,
        'degraded_reason': 'provider_rate_limited',
        'metrics': {
            'rate_limited': True,
            'next_retry_at': (now + timedelta(minutes=5)).isoformat(),
            'active_provider_host': 'base-mainnet.example.com',
        },
    }
    status = build_worker_status(
        now=now,
        realtime_is_enabled=True,
        stable_last_heartbeat_at=now - timedelta(seconds=15),
        stable_last_poll_at=now - timedelta(seconds=15),
        heartbeat_ttl_seconds=TTL,
        realtime_watcher=watcher,
    )
    assert status['provider_realtime']['state'] == 'cooldown'
    assert status['provider_realtime']['rate_limited'] is True
    assert status['provider_realtime']['host'] == 'base-mainnet.example.com'
    assert status['realtime']['state'] == 'rate_limited'
    # Stable polling alive => source not dead.
    assert status['monitoring_source_live'] is True
    assert status['headline'] == (
        'Stable polling active. Realtime WebSocket rate limited (provider cooldown).'
    )


def test_provider_rate_limited_past_retry_is_rate_limited_not_cooldown():
    now = _now()
    watcher = {
        'source_status': 'provider_rate_limited',
        'degraded': True,
        'metrics': {
            'rate_limited': True,
            'next_retry_at': (now - timedelta(minutes=5)).isoformat(),
        },
    }
    status = build_worker_status(
        now=now,
        realtime_is_enabled=True,
        stable_last_heartbeat_at=now - timedelta(seconds=15),
        stable_last_poll_at=now - timedelta(seconds=15),
        heartbeat_ttl_seconds=TTL,
        realtime_watcher=watcher,
    )
    assert status['provider_realtime']['state'] == 'rate_limited'


def test_rate_limited_surfaces_even_when_realtime_now_disabled():
    """The realtime worker was turned off BECAUSE of the 429; the last watcher
    row still proves the provider was rate-limited. Surface it truthfully."""
    now = _now()
    watcher = {
        'source_status': 'provider_rate_limited',
        'degraded': True,
        'metrics': {
            'rate_limited': True,
            'next_retry_at': (now + timedelta(minutes=2)).isoformat(),
        },
    }
    status = build_worker_status(
        now=now,
        realtime_is_enabled=False,
        stable_last_heartbeat_at=now - timedelta(seconds=15),
        stable_last_poll_at=now - timedelta(seconds=15),
        heartbeat_ttl_seconds=TTL,
        realtime_watcher=watcher,
    )
    # Realtime itself is paused (disabled wins for the worker state)...
    assert status['realtime']['state'] == 'paused'
    # ...but the provider rate-limit fact is still shown.
    assert status['provider_realtime']['state'] == 'cooldown'
    assert status['monitoring_source_live'] is True


# ---------------------------------------------------------------------------
# Realtime enabled + healthy
# ---------------------------------------------------------------------------

def test_realtime_enabled_active_provider_healthy():
    now = _now()
    watcher = {
        'source_status': 'realtime_websocket',
        'degraded': False,
        'metrics': {'rate_limited': False, 'active_provider_host': 'wss.example.com'},
    }
    status = build_worker_status(
        now=now,
        realtime_is_enabled=True,
        stable_last_heartbeat_at=now - timedelta(seconds=15),
        stable_last_poll_at=now - timedelta(seconds=15),
        heartbeat_ttl_seconds=TTL,
        realtime_watcher=watcher,
        realtime_last_event_at=now - timedelta(seconds=5),
    )
    assert status['realtime']['state'] == 'active'
    assert status['provider_realtime']['state'] == 'healthy'
    assert status['realtime']['last_event_at'] is not None
    assert status['headline'] == 'Stable polling active. Realtime WebSocket active.'


def test_realtime_enabled_but_no_heartbeat_is_starting():
    now = _now()
    status = build_worker_status(
        now=now,
        realtime_is_enabled=True,
        stable_last_heartbeat_at=now - timedelta(seconds=15),
        stable_last_poll_at=now - timedelta(seconds=15),
        heartbeat_ttl_seconds=TTL,
        realtime_watcher=None,
    )
    assert status['realtime']['state'] == 'starting'
    assert status['provider_realtime']['state'] == 'unknown'
