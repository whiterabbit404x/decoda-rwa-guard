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

from services.api.app.worker_status import (
    build_worker_status,
    realtime_enabled,
    stable_poll_stale_threshold_seconds,
    DEFAULT_STABLE_POLL_STALE_SECONDS,
)


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


# ---------------------------------------------------------------------------
# Stable polling is active on EITHER a fresh heartbeat OR a fresh poll
# (heartbeat and poll are separate facts; either proves the stable loop is live)
# ---------------------------------------------------------------------------

def test_recent_rpc_polling_heartbeat_means_stable_active_even_if_poll_stale():
    """Requirement 2: a recent RPC polling heartbeat => stable polling active,
    even when the monitoring poll completion is stale/absent."""
    now = _now()
    status = build_worker_status(
        now=now,
        realtime_is_enabled=False,
        stable_last_heartbeat_at=now - timedelta(seconds=20),
        stable_last_poll_at=now - timedelta(seconds=TTL + 600),
        heartbeat_ttl_seconds=TTL,
        realtime_watcher=None,
    )
    assert status['stable_polling']['state'] == 'active'
    assert status['stable_polling']['active'] is True
    assert status['stable_polling']['heartbeat_fresh'] is True
    assert status['stable_polling']['poll_fresh'] is False
    assert status['stable_polling']['detection_supported'] is True
    assert status['monitoring_source_live'] is True
    assert 'stale' not in status['headline'].lower()


def test_recent_monitoring_poll_means_stable_active_even_if_heartbeat_stale():
    """Requirement 3: a recent monitoring poll completion => stable polling active,
    even when the RPC polling heartbeat writer lagged and is stale. This is the
    exact contradiction the fix targets: Telemetry shows a fresh stable poll while
    the heartbeat table is behind."""
    now = _now()
    status = build_worker_status(
        now=now,
        realtime_is_enabled=False,
        stable_last_heartbeat_at=now - timedelta(seconds=TTL + 600),
        stable_last_poll_at=now - timedelta(seconds=20),
        heartbeat_ttl_seconds=TTL,
        realtime_watcher=None,
    )
    assert status['stable_polling']['state'] == 'active'
    assert status['stable_polling']['active'] is True
    assert status['stable_polling']['heartbeat_fresh'] is False
    assert status['stable_polling']['poll_fresh'] is True
    assert status['stable_polling']['detection_supported'] is True
    assert status['monitoring_source_live'] is True
    # Must NOT claim the worker/heartbeat is stale while polling is fresh.
    assert 'stale' not in status['headline'].lower()
    # Acceptance headline (realtime disabled + stable active via a fresh poll).
    assert status['headline'] == 'Stable polling active. Realtime WebSocket paused.'


def test_recent_coverage_poll_telemetry_means_stable_active():
    """Requirement 1: the stable_polling verdict reads from the SAME canonical
    source the Telemetry worker-status card shows as "Last stable poll" (live
    rpc_polling coverage telemetry). A fresh coverage poll keeps stable polling
    active even when both the heartbeat and monitoring_polls are stale/absent, so
    the banner never contradicts the Telemetry card."""
    now = _now()
    status = build_worker_status(
        now=now,
        realtime_is_enabled=False,
        stable_last_heartbeat_at=now - timedelta(seconds=TTL + 600),
        stable_last_poll_at=None,
        stable_last_coverage_poll_at=now - timedelta(seconds=25),
        heartbeat_ttl_seconds=TTL,
        realtime_watcher=None,
    )
    assert status['stable_polling']['state'] == 'active'
    assert status['stable_polling']['active'] is True
    assert status['stable_polling']['poll_fresh'] is True
    assert status['stable_polling']['heartbeat_fresh'] is False
    assert status['headline'] == 'Stable polling active. Realtime WebSocket paused.'
    assert 'stale' not in status['headline'].lower()


def test_both_heartbeat_and_poll_stale_yields_stale_warning():
    """Requirement 4: only when the heartbeat AND both poll proofs (monitoring poll
    completion + coverage poll telemetry) are stale does the stable polling worker
    read as stale."""
    now = _now()
    status = build_worker_status(
        now=now,
        realtime_is_enabled=False,
        stable_last_heartbeat_at=now - timedelta(seconds=TTL + 600),
        stable_last_poll_at=now - timedelta(seconds=TTL + 600),
        stable_last_coverage_poll_at=now - timedelta(seconds=TTL + 600),
        heartbeat_ttl_seconds=TTL,
        realtime_watcher=None,
    )
    assert status['stable_polling']['state'] == 'stale'
    assert status['stable_polling']['active'] is False
    assert status['stable_polling']['heartbeat_fresh'] is False
    assert status['stable_polling']['poll_fresh'] is False
    assert status['stable_polling']['detection_supported'] is False
    assert status['monitoring_source_live'] is False
    assert status['headline'] == 'Stable RPC polling heartbeat is stale.'


def test_realtime_disabled_stable_active_via_poll_only_acceptance_headline():
    """Acceptance: realtime disabled + stable polling proven by a fresh poll (with a
    stale heartbeat) => 'Stable polling active. Realtime WebSocket paused.' and no
    'stale' wording anywhere in the headline."""
    now = _now()
    status = build_worker_status(
        now=now,
        realtime_is_enabled=False,
        stable_last_heartbeat_at=now - timedelta(seconds=TTL + 1200),
        stable_last_poll_at=now - timedelta(seconds=10),
        heartbeat_ttl_seconds=TTL,
        realtime_watcher=None,
    )
    assert status['headline'] == 'Stable polling active. Realtime WebSocket paused.'
    assert status['realtime']['enabled'] is False
    assert status['realtime']['state'] == 'paused'
    assert status['stable_polling']['active'] is True
    assert 'stale' not in status['headline'].lower()


# ---------------------------------------------------------------------------
# Stable poll stale threshold — forgiving multi-minute cadence, not the 180s TTL
# ---------------------------------------------------------------------------

def test_stable_poll_stale_threshold_default_is_900(monkeypatch):
    monkeypatch.delenv('MONITORING_STABLE_POLL_STALE_SECONDS', raising=False)
    assert DEFAULT_STABLE_POLL_STALE_SECONDS == 900
    # Small worker-loop interval keeps the 900s default (>= max(2*30, 600)).
    assert stable_poll_stale_threshold_seconds(30) == 900
    assert stable_poll_stale_threshold_seconds() == 900


def test_stable_poll_stale_threshold_floors_at_two_poll_cycles(monkeypatch):
    """Never stricter than two poll cycles: a 10-minute poll interval widens the
    threshold to 1200s even though that exceeds the 900s default."""
    monkeypatch.delenv('MONITORING_STABLE_POLL_STALE_SECONDS', raising=False)
    assert stable_poll_stale_threshold_seconds(600) == 1200


def test_stable_poll_stale_threshold_env_override(monkeypatch):
    monkeypatch.setenv('MONITORING_STABLE_POLL_STALE_SECONDS', '1800')
    assert stable_poll_stale_threshold_seconds(30) == 1800


def test_stable_poll_stale_threshold_env_floored_at_ten_minutes(monkeypatch):
    """Even a too-strict env value is floored at 600s (10 minutes) so stable polling
    is never flagged stale sooner than the requirement allows."""
    monkeypatch.setenv('MONITORING_STABLE_POLL_STALE_SECONDS', '120')
    assert stable_poll_stale_threshold_seconds(30) == 600


def test_stable_poll_stale_threshold_bad_env_falls_back_to_default(monkeypatch):
    monkeypatch.setenv('MONITORING_STABLE_POLL_STALE_SECONDS', 'not-an-int')
    assert stable_poll_stale_threshold_seconds(30) == 900
    monkeypatch.setenv('MONITORING_STABLE_POLL_STALE_SECONDS', '')
    assert stable_poll_stale_threshold_seconds(30) == 900


# ---------------------------------------------------------------------------
# The reported bug: a 4–5 minute-old heartbeat/poll at a 5-minute cadence is HEALTHY
# ---------------------------------------------------------------------------

def test_stable_poll_four_minutes_old_at_five_minute_interval_is_active(monkeypatch):
    """A stable poll 4 minutes old at a 5-minute polling interval must read ACTIVE.
    Under the old max(180s, 90s) threshold this wrongly flagged stale."""
    monkeypatch.delenv('MONITORING_STABLE_POLL_STALE_SECONDS', raising=False)
    now = _now()
    threshold = stable_poll_stale_threshold_seconds(300)  # 5-minute per-target interval
    status = build_worker_status(
        now=now,
        realtime_is_enabled=False,
        stable_last_heartbeat_at=None,
        stable_last_poll_at=now - timedelta(minutes=4),
        heartbeat_ttl_seconds=threshold,
        realtime_watcher=None,
    )
    assert status['stable_polling']['state'] == 'active'
    assert status['stable_polling']['active'] is True
    assert status['headline'] == 'Stable polling active. Realtime WebSocket paused.'
    assert 'stale' not in status['headline'].lower()


def test_heartbeat_five_minutes_old_with_fifteen_minute_threshold_is_active():
    """Heartbeat 5 minutes ago with a 15-minute (900s) threshold => active."""
    now = _now()
    status = build_worker_status(
        now=now,
        realtime_is_enabled=False,
        stable_last_heartbeat_at=now - timedelta(minutes=5),
        stable_last_poll_at=None,
        heartbeat_ttl_seconds=900,
        realtime_watcher=None,
    )
    assert status['stable_polling']['state'] == 'active'
    assert status['stable_polling']['heartbeat_fresh'] is True
    assert status['headline'] == 'Stable polling active. Realtime WebSocket paused.'


def test_heartbeat_sixteen_minutes_old_exceeds_threshold_is_stale():
    """Heartbeat 16 minutes ago exceeds the 900s threshold => stale (and only then)."""
    now = _now()
    status = build_worker_status(
        now=now,
        realtime_is_enabled=False,
        stable_last_heartbeat_at=now - timedelta(minutes=16),
        stable_last_poll_at=None,
        heartbeat_ttl_seconds=900,
        realtime_watcher=None,
    )
    assert status['stable_polling']['state'] == 'stale'
    assert status['stable_polling']['active'] is False
    assert status['headline'] == 'Stable RPC polling heartbeat is stale.'


def test_stable_polling_exposes_debug_threshold_age_and_status_fields():
    """Requirement 6: the stable_polling block carries the applied threshold, the
    freshest-proof age, and a status alias so runtime status can be reconciled."""
    now = _now()
    status = build_worker_status(
        now=now,
        realtime_is_enabled=False,
        stable_last_heartbeat_at=now - timedelta(minutes=5),
        stable_last_poll_at=now - timedelta(minutes=4),
        heartbeat_ttl_seconds=900,
        realtime_watcher=None,
    )
    sp = status['stable_polling']
    assert sp['stale_threshold_seconds'] == 900
    # Freshest proof is the 4-minute-old poll => 240s.
    assert sp['age_seconds'] == 240
    assert sp['status'] == 'active'
