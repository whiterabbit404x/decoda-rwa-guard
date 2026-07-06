"""TLS provider-failure breaker, provider circuit, and fallback-truth tests.

Covers the production incident where the QuickNode WSS kept terminating the TLS
handshake with `[SSL: TLSV1_ALERT_INTERNAL_ERROR] tlsv1 alert internal error`
while the worker reconnected forever (reconnect_count 35 -> 100+), heartbeats
published provider_mode=degraded with fallback_active=False, and the UI showed
"Paused / Disabled".

Required behaviours:
  1. Repeated TLSV1_ALERT_INTERNAL_ERROR marks the WSS provider unhealthy
     (realtime_ws_provider_unhealthy reason=tls_internal_error) and stops the
     reconnect loop against that endpoint (provider circuit opens).
  2. fallback_active becomes True whenever the WSS is degraded past its breaker
     thresholds, with a canonical fallback provider_mode
     (quicknode_http_fast_tail or stable_rpc_polling_fallback).
  3. The secondary WSS is tried before the HTTP fast-tail fallback.
  4. The status layer / UI never claims realtime active while degraded.
  5. Stable polling remains the active detection path while realtime is degraded.
  6. No endless reconnect loop to the same failing provider (circuit open for
     10-30 minutes, then a single half-open probe).
  7. Realtime proof requires detected_by in (realtime_websocket,
     realtime_backfill) — never stable_rpc_polling or fallback tags.
"""
from __future__ import annotations

import asyncio
import logging
import ssl
import time
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

PRIMARY_WS = 'wss://primary.example.com/ws'
SECONDARY_WS = 'wss://secondary.example.com/ws'

TLS_ERROR_TEXT = (
    '[SSL: TLSV1_ALERT_INTERNAL_ERROR] tlsv1 alert internal error (_ssl.c:1006)'
)


class _LogCapture(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.messages: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.messages.append(record.getMessage())


def _make_ingestor(**kwargs):
    from services.api.app.base_realtime_ingestor import BaseRealtimeIngestor

    defaults = dict(
        rpc_url='https://rpc.example/v1/key',
        ws_url=PRIMARY_WS,
        watcher_name='base-realtime-worker',
        subscriptions='newHeads_only',
    )
    defaults.update(kwargs)
    ingestor = BaseRealtimeIngestor(**defaults)
    # Skip the checkpoint bootstrap / eth_blockNumber path in loop tests.
    ingestor.state['last_head_block'] = 1000
    ingestor._last_head_block_at = time.monotonic()
    ingestor.state['last_processed_block'] = 1000
    ingestor._compute_reconnect_sleep = lambda *a, **k: 0.0  # type: ignore[method-assign]
    ingestor._record_heartbeat = lambda: None  # type: ignore[method-assign]
    return ingestor


# ---------------------------------------------------------------------------
# 1. TLS error classification
# ---------------------------------------------------------------------------

def test_is_tls_error_matches_sslerror_and_production_string():
    from services.api.app.base_realtime_ingestor import BaseRealtimeIngestor

    assert BaseRealtimeIngestor._is_tls_error(ssl.SSLError(1, TLS_ERROR_TEXT)) is True
    # Wrapped TLS failure text (e.g. re-raised by a connect helper).
    assert BaseRealtimeIngestor._is_tls_error(RuntimeError(TLS_ERROR_TEXT)) is True
    assert BaseRealtimeIngestor._is_tls_error(
        RuntimeError('TLSV1_ALERT_INTERNAL_ERROR during handshake')
    ) is True
    # Non-TLS failures must NOT be classified as provider TLS failures.
    assert BaseRealtimeIngestor._is_tls_error(RuntimeError('connection refused')) is False
    assert BaseRealtimeIngestor._is_tls_error(
        Exception('ConnectionClosedOK: code=1001 going away')
    ) is False
    assert BaseRealtimeIngestor._is_tls_error(
        RuntimeError('server rejected WebSocket connection: HTTP 429')
    ) is False


def test_tls_failure_threshold_trips_after_more_than_three():
    ingestor = _make_ingestor()
    # Failures 1-3: below the "more than 3 times" threshold.
    assert ingestor._note_tls_failure() is False
    assert ingestor._note_tls_failure() is False
    assert ingestor._note_tls_failure() is False
    # 4th consecutive TLS failure trips the breaker.
    assert ingestor._note_tls_failure() is True
    assert ingestor.state['metrics']['tls_failures'] == 4


# ---------------------------------------------------------------------------
# 2. Repeated TLSV1_ALERT_INTERNAL_ERROR marks the WSS provider unhealthy and
#    the reconnect loop against that endpoint stops (no endless loop).
# ---------------------------------------------------------------------------

def test_repeated_tls_internal_error_marks_provider_unhealthy_and_stops_loop():
    from services.api.app import base_realtime_ingestor as mod

    ingestor = _make_ingestor()

    ws_calls = [0]

    async def _mock_ws_subscribe():
        ws_calls[0] += 1
        raise ssl.SSLError(1, TLS_ERROR_TEXT)

    fast_tail_calls = [0]

    async def _mock_fast_tail(stop_when=None):
        fast_tail_calls[0] += 1
        raise asyncio.CancelledError()

    ingestor._ws_subscribe = _mock_ws_subscribe  # type: ignore[method-assign]
    ingestor._run_http_fast_tail = _mock_fast_tail  # type: ignore[method-assign]

    capture = _LogCapture()
    mod.logger.addHandler(capture)
    mod.logger.setLevel(logging.DEBUG)
    try:
        with pytest.raises(asyncio.CancelledError):
            asyncio.run(ingestor.run_forever())
    finally:
        mod.logger.removeHandler(capture)

    # The 4th consecutive TLS failure trips the breaker: exactly 4 connect
    # attempts, then no more reconnects to the same failing endpoint.
    assert ws_calls[0] == 4, f'WSS reconnect loop must stop after the trip; got {ws_calls[0]}'
    assert ingestor.state['metrics']['ws_reconnects'] == 4
    assert ingestor._ws_circuit_state(PRIMARY_WS) == 'open'
    assert fast_tail_calls[0] == 1, 'the fallback must take over exactly once'
    # The circuit is a cooldown, not a permanent disable: realtime resumes later.
    assert ingestor._wss_permanently_disabled is False

    unhealthy = next((m for m in capture.messages if 'realtime_ws_provider_unhealthy' in m), None)
    assert unhealthy is not None, f'unhealthy marker expected; got: {capture.messages}'
    assert 'reason=tls_internal_error' in unhealthy
    circuit_open = next((m for m in capture.messages if 'provider_circuit_open' in m), None)
    assert circuit_open is not None, 'provider_circuit_open must be logged'
    assert 'provider=primary.example.com' in circuit_open
    fallback = next((m for m in capture.messages if 'realtime_fallback_activated' in m), None)
    assert fallback is not None, 'fallback activation must be logged'
    assert 'provider_mode=quicknode_http_fast_tail' in fallback
    assert 'fallback_active=True' in fallback


def test_tls_circuit_open_duration_clamped_to_10_30_minutes(monkeypatch):
    from services.api.app.base_realtime_ingestor import BaseRealtimeIngestor

    monkeypatch.delenv('BASE_REALTIME_PROVIDER_CIRCUIT_SECONDS', raising=False)
    default = BaseRealtimeIngestor(
        rpc_url='https://rpc', ws_url=PRIMARY_WS, watcher_name='w',
    )
    assert default.provider_circuit_seconds == 900

    monkeypatch.setenv('BASE_REALTIME_PROVIDER_CIRCUIT_SECONDS', '60')
    low = BaseRealtimeIngestor(rpc_url='https://rpc', ws_url=PRIMARY_WS, watcher_name='w')
    assert low.provider_circuit_seconds == 600, 'must clamp to the 10-minute floor'

    monkeypatch.setenv('BASE_REALTIME_PROVIDER_CIRCUIT_SECONDS', '7200')
    high = BaseRealtimeIngestor(rpc_url='https://rpc', ws_url=PRIMARY_WS, watcher_name='w')
    assert high.provider_circuit_seconds == 1800, 'must clamp to the 30-minute cap'


def test_circuit_half_open_allows_single_probe_and_reopens_on_failure():
    from services.api.app import base_realtime_ingestor as mod

    ingestor = _make_ingestor()
    ingestor._mark_ws_provider_unhealthy('tls_internal_error')
    assert ingestor._ws_circuit_state(PRIMARY_WS) == 'open'
    # No endpoint is selectable while the circuit is open.
    assert ingestor._select_ws_url() is None

    # Window elapses -> half-open: exactly one probe allowed, logged canonically.
    ingestor._ws_circuits[PRIMARY_WS]['open_until'] = time.monotonic() - 1.0
    assert ingestor._ws_circuit_state(PRIMARY_WS) == 'half_open'

    capture = _LogCapture()
    mod.logger.addHandler(capture)
    mod.logger.setLevel(logging.DEBUG)
    try:
        selected = ingestor._select_ws_url()
    finally:
        mod.logger.removeHandler(capture)
    assert selected == PRIMARY_WS
    assert any('provider_circuit_half_open' in m for m in capture.messages)

    # A TLS failure during the probe is one-strike: the breaker trips immediately.
    assert ingestor._note_tls_failure() is True

    # Real data closes the circuit and clears the TLS counter.
    ingestor._note_ws_provider_recovered(PRIMARY_WS)
    assert ingestor._ws_circuit_state(PRIMARY_WS) == 'closed'
    assert ingestor._tls_failure_count == 0


# ---------------------------------------------------------------------------
# 3. Secondary WSS is tried before the HTTP fast-tail fallback
# ---------------------------------------------------------------------------

def test_secondary_wss_tried_before_http_fallback():
    from services.api.app import base_realtime_ingestor as mod

    ingestor = _make_ingestor(ws_url_secondary=SECONDARY_WS)

    urls_used: list[str] = []

    async def _mock_ws_subscribe():
        urls_used.append(ingestor._current_ws_url)
        raise ssl.SSLError(1, TLS_ERROR_TEXT)

    fast_tail_calls = [0]

    async def _mock_fast_tail(stop_when=None):
        fast_tail_calls[0] += 1
        raise asyncio.CancelledError()

    ingestor._ws_subscribe = _mock_ws_subscribe  # type: ignore[method-assign]
    ingestor._run_http_fast_tail = _mock_fast_tail  # type: ignore[method-assign]

    capture = _LogCapture()
    mod.logger.addHandler(capture)
    mod.logger.setLevel(logging.DEBUG)
    try:
        with pytest.raises(asyncio.CancelledError):
            asyncio.run(ingestor.run_forever())
    finally:
        mod.logger.removeHandler(capture)

    # 4 TLS failures on the primary, failover, 4 on the secondary, then fallback.
    assert urls_used == [PRIMARY_WS] * 4 + [SECONDARY_WS] * 4, urls_used
    assert ingestor._ws_circuit_state(PRIMARY_WS) == 'open'
    assert ingestor._ws_circuit_state(SECONDARY_WS) == 'open'
    assert fast_tail_calls[0] == 1

    failover_idx = next(
        (i for i, m in enumerate(capture.messages)
         if 'realtime_provider_failover' in m and 'reason=provider_circuit' in m),
        None,
    )
    fallback_idx = next(
        (i for i, m in enumerate(capture.messages) if 'realtime_fallback_activated' in m),
        None,
    )
    assert failover_idx is not None, 'WSS failover must be logged'
    assert 'old_host=primary.example.com' in capture.messages[failover_idx]
    assert 'new_host=secondary.example.com' in capture.messages[failover_idx]
    assert fallback_idx is not None
    assert failover_idx < fallback_idx, (
        'secondary WSS must be tried BEFORE the HTTP fast-tail fallback'
    )


# ---------------------------------------------------------------------------
# 4. fallback_active becomes True when the WebSocket is degraded
# ---------------------------------------------------------------------------

def _heartbeat_log(ingestor) -> str:
    from services.api.app import base_realtime_ingestor as mod

    capture = _LogCapture()
    mod.logger.addHandler(capture)
    mod.logger.setLevel(logging.DEBUG)
    try:
        with (
            patch('services.api.app.base_realtime_ingestor.pg_connection') as mock_pg,
            patch('services.api.app.base_realtime_ingestor.ensure_pilot_schema', lambda c: None),
        ):
            mock_conn = MagicMock()
            mock_conn.__enter__ = lambda s: s
            mock_conn.__exit__ = MagicMock(return_value=False)
            mock_pg.return_value = mock_conn
            ingestor._record_heartbeat()
    finally:
        mod.logger.removeHandler(capture)
    hb = next((m for m in capture.messages if 'realtime_worker_heartbeat' in m), None)
    assert hb is not None, 'realtime_worker_heartbeat must be logged'
    return hb


def test_heartbeat_publishes_fallback_active_true_with_fast_tail_mode():
    from services.api.app.base_realtime_ingestor import BaseRealtimeIngestor

    ingestor = BaseRealtimeIngestor(
        rpc_url='https://rpc.example/v1/key', ws_url=PRIMARY_WS, watcher_name='w',
    )
    ingestor._mark_ws_provider_unhealthy('tls_internal_error')

    assert ingestor._fallback_is_active() is True
    hb = _heartbeat_log(ingestor)
    assert 'provider_mode=quicknode_http_fast_tail' in hb, hb
    assert 'fallback_active=True' in hb, hb
    assert 'degraded_reason=tls_internal_error' in hb, hb
    # The exact production contradiction must be impossible now.
    assert 'provider_mode=degraded' not in hb, hb


def test_heartbeat_publishes_stable_polling_fallback_when_no_http_rpc():
    from services.api.app.base_realtime_ingestor import (
        BaseRealtimeIngestor,
        STABLE_POLLING_FALLBACK_MODE,
    )

    ingestor = BaseRealtimeIngestor(rpc_url='', ws_url=PRIMARY_WS, watcher_name='w')
    ingestor._mark_ws_provider_unhealthy('tls_internal_error')

    assert ingestor._circuit_fallback_mode() == STABLE_POLLING_FALLBACK_MODE
    assert ingestor._fallback_is_active() is True
    hb = _heartbeat_log(ingestor)
    assert 'provider_mode=stable_rpc_polling_fallback' in hb, hb
    assert 'fallback_active=True' in hb, hb
    # No realtime path scans in stable-only fallback — say so canonically.
    assert 'realtime_scanning_active=False' in hb, hb


def test_below_threshold_tls_failures_do_not_activate_fallback():
    ingestor = _make_ingestor()
    ingestor._note_tls_failure()
    ingestor._note_tls_failure()
    ingestor._note_tls_failure()
    assert ingestor._fallback_is_active() is False, (
        'fallback must not activate below the TLS failure threshold'
    )
    assert ingestor._ws_circuit_state(PRIMARY_WS) == 'closed'


# ---------------------------------------------------------------------------
# 4b. Heartbeat consistency guard: a degraded WSS never publishes the
#     provider_mode=degraded + fallback_active=False + realtime_scanning_active=True
#     contradiction, even BEFORE the circuit trips (requirements 1 & 4).
# ---------------------------------------------------------------------------

def test_heartbeat_degraded_wss_below_trip_publishes_fallback_not_contradiction():
    from services.api.app.base_realtime_ingestor import (
        BaseRealtimeIngestor,
        STABLE_POLLING_FALLBACK_MODE,
    )

    ingestor = BaseRealtimeIngestor(
        rpc_url='https://rpc.example/v1/key', ws_url=PRIMARY_WS, watcher_name='w',
    )
    # The below-trip degraded window: a single TLS failure has set the degraded state
    # + source_status='degraded' but has NOT opened the circuit (1 < the >3 threshold).
    ingestor.state['degraded'] = True
    ingestor.state['degraded_reason'] = 'tls_internal_error'
    ingestor.state['source_status'] = 'degraded'
    ingestor._note_tls_failure()
    assert ingestor._fallback_is_active() is False
    assert ingestor._ws_circuit_state(PRIMARY_WS) == 'closed'

    hb = _heartbeat_log(ingestor)
    # The exact production contradiction must be impossible in a persisted heartbeat.
    assert 'provider_mode=degraded ' not in hb, hb
    assert f'provider_mode={STABLE_POLLING_FALLBACK_MODE}' in hb, hb
    assert 'fallback_active=True' in hb, hb
    assert 'realtime_scanning_active=False' in hb, hb
    assert 'degraded_reason=tls_internal_error' in hb, hb


def test_heartbeat_cold_start_does_not_publish_false_fallback():
    """Before the first connection the worker is STARTING, not degraded: the initial
    source_status is the 'degraded' sentinel but state['degraded'] is False, so the
    consistency guard must NOT fire and claim a fallback is covering detection."""
    from services.api.app.base_realtime_ingestor import BaseRealtimeIngestor

    ingestor = BaseRealtimeIngestor(
        rpc_url='https://rpc.example/v1/key', ws_url=PRIMARY_WS, watcher_name='w',
    )
    assert ingestor.state['degraded'] is False
    hb = _heartbeat_log(ingestor)
    assert 'fallback_active=False' in hb, hb
    assert 'stable_rpc_polling_fallback' not in hb, hb


def test_worker_status_reads_degraded_column_with_fallback_metrics():
    """Fix A persists the source_status column as 'degraded' while the metrics carry
    provider_mode=stable_rpc_polling_fallback + fallback_active=True. The status layer
    must read the fallback facts and render the truthful headline — never claim
    realtime active (requirement 1: UI and backend agree on one fact)."""
    from services.api.app.worker_status import (
        build_worker_status,
        realtime_active_by_watcher_facts,
    )

    watcher = {
        'watcher_name': 'base-realtime-worker',
        'source_status': 'degraded',
        'degraded': True,
        'degraded_reason': 'tls_internal_error',
        'metrics': {
            'heads_received': 935,
            'events_ingested': 20,
            'ws_reconnects': 40,
            'rate_limited': False,
            'provider_mode': 'stable_rpc_polling_fallback',
            'fallback_active': True,
            'realtime_scanning_active': False,
        },
    }
    assert realtime_active_by_watcher_facts(watcher) is False

    now = datetime(2026, 7, 6, 12, 0, tzinfo=timezone.utc)
    status = build_worker_status(
        now=now,
        realtime_is_enabled=True,
        stable_last_heartbeat_at=now - timedelta(seconds=30),
        stable_last_poll_at=now - timedelta(seconds=30),
        heartbeat_ttl_seconds=900,
        realtime_watcher=watcher,
    )
    assert status['realtime']['state'] == 'degraded'
    assert status['realtime']['fallback_active'] is True
    assert status['realtime']['provider_mode'] == 'stable_rpc_polling_fallback'
    assert status['headline'] == (
        'Stable polling active. Realtime degraded — stable polling fallback active.'
    )
    assert status['monitoring_source_live'] is True


# ---------------------------------------------------------------------------
# 9b. Silent (open-but-no-heads) WSS session: no fake recovery, breaker trips.
#     A provider that accepts the connection and ACKs the subscription but delivers
#     no newHeads must never read as recovered/active, and must fail over instead of
#     looping forever (requirement 4 + acceptance: reconnect_count no longer grows).
# ---------------------------------------------------------------------------

def test_no_data_session_threshold_trips_after_more_than_three():
    ingestor = _make_ingestor()
    assert ingestor._note_no_data_session() is False
    assert ingestor._note_no_data_session() is False
    assert ingestor._note_no_data_session() is False
    assert ingestor._note_no_data_session() is True
    assert ingestor.state['metrics']['no_data_sessions'] == 4


def test_provider_recovered_resets_no_data_session_count():
    ingestor = _make_ingestor()
    ingestor._note_no_data_session()
    ingestor._note_no_data_session()
    assert ingestor._no_data_session_count == 2
    # Real chain data closes the loop: the silent-session counter resets to zero.
    ingestor._note_ws_provider_recovered(PRIMARY_WS)
    assert ingestor._no_data_session_count == 0


def test_silent_wss_session_no_fake_recovery_and_trips_breaker():
    from services.api.app import base_realtime_ingestor as mod

    # No secondary WSS, no HTTP RPC -> the breaker trips straight to the stable-polling
    # fallback once the endpoint is marked unhealthy.
    ingestor = _make_ingestor(rpc_url='')
    ingestor.heartbeat_seconds = 0.02
    ingestor._throttled_block_number = lambda: None  # type: ignore[method-assign]

    ws_calls = [0]

    async def _silent_ws_subscribe():
        # Connected but silent: block past the heartbeat window (so wait_for times
        # out) and deliver NO heads (never touch heads_received).
        ws_calls[0] += 1
        await asyncio.sleep(1.0)

    async def _mock_fallback():
        # The endpoint is unhealthy and its circuit opened — end the test here.
        raise asyncio.CancelledError()

    ingestor._ws_subscribe = _silent_ws_subscribe  # type: ignore[method-assign]
    ingestor._run_circuit_open_fallback = _mock_fallback  # type: ignore[method-assign]

    capture = _LogCapture()
    mod.logger.addHandler(capture)
    mod.logger.setLevel(logging.DEBUG)
    try:
        with pytest.raises(asyncio.CancelledError):
            asyncio.run(ingestor.run_forever())
    finally:
        mod.logger.removeHandler(capture)

    # Exactly 4 silent sessions (> the >3 threshold), then no more reconnects.
    assert ws_calls[0] == 4, f'silent session loop must stop after the trip; got {ws_calls[0]}'
    assert ingestor.state['metrics']['ws_reconnects'] == 4
    assert ingestor._ws_circuit_state(PRIMARY_WS) == 'open'
    # It is a cooldown, not a permanent disable — realtime resumes after the probe.
    assert ingestor._wss_permanently_disabled is False

    # A socket that delivered zero heads must NEVER be logged as recovered/healthy.
    assert not any('realtime_recovered' in m for m in capture.messages), capture.messages
    assert any('realtime_ws_no_data_session' in m for m in capture.messages)
    unhealthy = next(
        (m for m in capture.messages if 'realtime_ws_provider_unhealthy' in m), None,
    )
    assert unhealthy is not None, f'unhealthy marker expected; got: {capture.messages}'
    assert 'reason=wss_no_heads' in unhealthy


def test_head_delivering_session_recovers_normally():
    """A session that DID deliver heads during the window is genuine recovery: the
    silent-session breaker must not fire and realtime_recovered is logged once."""
    from services.api.app import base_realtime_ingestor as mod

    ingestor = _make_ingestor()
    ingestor.heartbeat_seconds = 0.02
    ingestor._throttled_block_number = lambda: None  # type: ignore[method-assign]
    ingestor.state['degraded'] = True  # start degraded so 'realtime_recovered' can log

    calls = [0]

    async def _ws_subscribe_delivers_head():
        calls[0] += 1
        # Deliver a real head this window, then block so wait_for times out.
        ingestor.state['metrics']['heads_received'] += 1
        ingestor.state['degraded'] = False
        if calls[0] >= 2:
            raise asyncio.CancelledError()
        await asyncio.sleep(1.0)

    ingestor._ws_subscribe = _ws_subscribe_delivers_head  # type: ignore[method-assign]

    capture = _LogCapture()
    mod.logger.addHandler(capture)
    mod.logger.setLevel(logging.DEBUG)
    try:
        with pytest.raises(asyncio.CancelledError):
            asyncio.run(ingestor.run_forever())
    finally:
        mod.logger.removeHandler(capture)

    assert ingestor._no_data_session_count == 0, 'a head-delivering window is not silent'
    assert ingestor.state['metrics'].get('no_data_sessions', 0) == 0
    assert any('realtime_recovered' in m for m in capture.messages), capture.messages
    assert not any('realtime_ws_no_data_session' in m for m in capture.messages)


# ---------------------------------------------------------------------------
# 5. Status layer / UI truth: degraded never reads as active; stable polling
#    keeps detecting while realtime is degraded.
# ---------------------------------------------------------------------------

def _degraded_watcher_row() -> dict:
    return {
        'watcher_name': 'base-realtime-worker',
        'source_status': 'quicknode_http_fast_tail',
        'degraded': True,
        'degraded_reason': 'tls_internal_error',
        'metrics': {
            'heads_received': 935,
            'events_ingested': 20,
            'ws_reconnects': 40,
            'rate_limited': False,
            'provider_mode': 'quicknode_http_fast_tail',
            'fallback_active': True,
        },
    }


def test_worker_status_degraded_with_fallback_never_claims_realtime_active():
    from services.api.app.worker_status import (
        build_worker_status,
        realtime_active_by_watcher_facts,
    )

    watcher = _degraded_watcher_row()
    assert realtime_active_by_watcher_facts(watcher) is False, (
        'a degraded fallback-mode watcher must never prove realtime active'
    )

    now = datetime(2026, 7, 5, 12, 0, tzinfo=timezone.utc)
    status = build_worker_status(
        now=now,
        realtime_is_enabled=True,
        stable_last_heartbeat_at=now - timedelta(seconds=30),
        stable_last_poll_at=now - timedelta(seconds=30),
        heartbeat_ttl_seconds=900,
        realtime_watcher=watcher,
    )
    assert status['realtime']['state'] == 'degraded'
    assert status['realtime']['fallback_active'] is True
    assert status['realtime']['provider_mode'] == 'quicknode_http_fast_tail'
    assert status['headline'] == (
        'Stable polling active. Realtime degraded — stable polling fallback active.'
    )
    # Stable polling remains the active detection path (acceptance rule 5).
    assert status['stable_polling']['state'] == 'active'
    assert status['stable_polling']['detection_supported'] is True
    assert status['monitoring_source_live'] is True


def test_worker_status_degraded_without_fallback_keeps_plain_degraded_headline():
    from services.api.app.worker_status import build_worker_status

    watcher = _degraded_watcher_row()
    watcher['source_status'] = 'degraded'
    watcher['metrics']['provider_mode'] = 'degraded'
    watcher['metrics']['fallback_active'] = False

    now = datetime(2026, 7, 5, 12, 0, tzinfo=timezone.utc)
    status = build_worker_status(
        now=now,
        realtime_is_enabled=True,
        stable_last_heartbeat_at=now - timedelta(seconds=30),
        stable_last_poll_at=now - timedelta(seconds=30),
        heartbeat_ttl_seconds=900,
        realtime_watcher=watcher,
    )
    assert status['realtime']['state'] == 'degraded'
    assert status['realtime']['fallback_active'] is False
    assert status['headline'] == 'Stable polling active. Realtime WebSocket degraded.'


def test_system_health_degraded_with_fallback_label(monkeypatch):
    monkeypatch.setenv('BASE_REALTIME_ENABLED', 'true')
    monkeypatch.setenv('BASE_WS_RPC_URL', PRIMARY_WS)

    from services.api.app.system_health import _build_realtime_ingestion_status

    fresh_hb = datetime.now(timezone.utc) - timedelta(seconds=5)

    class FakeRow(dict):
        def keys(self):
            return super().keys()

    fake_row = FakeRow({
        'watcher_name': 'base-realtime-worker',
        'source_status': 'quicknode_http_fast_tail',
        'degraded': True,
        'degraded_reason': 'tls_internal_error',
        'last_heartbeat_at': fresh_hb,
        'metrics': (
            '{"events_ingested": 20, "ws_reconnects": 40, '
            '"provider_mode": "quicknode_http_fast_tail", "fallback_active": true}'
        ),
    })

    execute_result = MagicMock()
    execute_result.fetchone.return_value = fake_row
    conn_mock = MagicMock()
    conn_mock.execute.return_value = execute_result

    status = _build_realtime_ingestion_status(conn_mock)

    assert status['status'] == 'degraded'
    assert status['fallback_active'] is True
    assert status['worker_provider_mode'] == 'quicknode_http_fast_tail'
    assert status['label'] == 'Realtime: Degraded — stable polling fallback active'
    assert status['stable_polling_active'] is True
    # The watcher query must keep matching the row after the worker switched
    # ingestion_mode to a fallback value.
    executed_sql = conn_mock.execute.call_args[0][0]
    assert 'http_fast_tail' in executed_sql
    assert 'stable_rpc_polling_fallback' in executed_sql


def test_telemetry_page_renders_degraded_fallback_state():
    """UI truth: the Telemetry header must have an explicit degraded branch that
    names the stable polling fallback, and must not fall through to 'Enabled' /
    'Paused / Disabled' when realtime_state=degraded."""
    from pathlib import Path

    repo_root = Path(__file__).resolve().parents[3]
    page = (
        repo_root / 'apps' / 'web' / 'app' / '(product)' / 'monitoring-sources'
        / '[targetId]' / 'telemetry' / 'page.tsx'
    ).read_text(encoding='utf-8')
    assert "realtimeState === 'degraded'" in page
    assert 'Realtime degraded — stable polling fallback active' in page
    assert 'realtime_fallback_active' in page


# ---------------------------------------------------------------------------
# 6. Realtime proof contract: only realtime_websocket / realtime_backfill count
# ---------------------------------------------------------------------------

def test_realtime_proof_requires_realtime_websocket_or_backfill():
    from services.api.app.worker_status import (
        is_realtime_detection_proof,
        REALTIME_PROOF_DETECTED_BY,
    )

    assert REALTIME_PROOF_DETECTED_BY == ('realtime_websocket', 'realtime_backfill')
    assert is_realtime_detection_proof('realtime_websocket') is True
    assert is_realtime_detection_proof('realtime_backfill') is True
    # Stable polling and the fallback/recovery paths are NOT realtime proof.
    assert is_realtime_detection_proof('stable_rpc_polling') is False
    assert is_realtime_detection_proof('quicknode_http_fast_tail') is False
    assert is_realtime_detection_proof('realtime_tx_import') is False
    assert is_realtime_detection_proof('') is False
    assert is_realtime_detection_proof(None) is False
    assert is_realtime_detection_proof('unknown') is False


# ---------------------------------------------------------------------------
# 7. Secondary provider env vars + failover config
# ---------------------------------------------------------------------------

def _clear_rpc_env(monkeypatch):
    for name in (
        'BASE_WS_RPC_URL', 'BASE_WS_RPC_URL_8453', 'BASE_WS_RPC_URL_PRIMARY',
        'BASE_WS_RPC_URL_SECONDARY', 'BASE_HTTP_RPC_URL_PRIMARY',
        'BASE_HTTP_RPC_URL_SECONDARY', 'EVM_RPC_URL_8453', 'BASE_EVM_RPC_URL',
        'EVM_RPC_URL',
    ):
        monkeypatch.delenv(name, raising=False)


def test_config_resolves_http_primary_and_secondary_env_vars(monkeypatch):
    _clear_rpc_env(monkeypatch)
    monkeypatch.setenv('BASE_REALTIME_ENABLED', 'true')
    monkeypatch.setenv('BASE_WS_RPC_URL_PRIMARY', PRIMARY_WS)
    monkeypatch.setenv('BASE_WS_RPC_URL_SECONDARY', SECONDARY_WS)
    monkeypatch.setenv('BASE_HTTP_RPC_URL_PRIMARY', 'https://http-primary.example/v1/k')
    monkeypatch.setenv('BASE_HTTP_RPC_URL_SECONDARY', 'https://http-secondary.example/v1/k')

    from services.api.app.run_realtime_worker import _resolve_config

    config = _resolve_config()
    assert config['ws_url'] == PRIMARY_WS
    assert config['ws_url_secondary'] == SECONDARY_WS
    assert config['rpc_url'] == 'https://http-primary.example/v1/k'
    assert config['rpc_url_secondary'] == 'https://http-secondary.example/v1/k'
    assert config['base_http_rpc_url_primary_present'] is True
    assert config['base_http_rpc_url_secondary_present'] is True
    # Hosts only — never the full URL/key — in the loggable fields.
    assert config['rpc_url_secondary_host'] == 'http-secondary.example'


def test_config_derives_secondary_http_from_secondary_ws(monkeypatch):
    _clear_rpc_env(monkeypatch)
    monkeypatch.setenv('BASE_REALTIME_ENABLED', 'true')
    monkeypatch.setenv('BASE_WS_RPC_URL_PRIMARY', PRIMARY_WS)
    monkeypatch.setenv('BASE_WS_RPC_URL_SECONDARY', SECONDARY_WS)

    from services.api.app.run_realtime_worker import _resolve_config

    config = _resolve_config()
    assert config['rpc_url'] == 'https://primary.example.com/ws'
    assert config['rpc_url_secondary'] == 'https://secondary.example.com/ws'


def test_config_drops_secondary_http_identical_to_primary(monkeypatch):
    _clear_rpc_env(monkeypatch)
    monkeypatch.setenv('BASE_REALTIME_ENABLED', 'true')
    monkeypatch.setenv('BASE_WS_RPC_URL_PRIMARY', PRIMARY_WS)
    monkeypatch.setenv('BASE_HTTP_RPC_URL_PRIMARY', 'https://same.example/v1/k')
    monkeypatch.setenv('BASE_HTTP_RPC_URL_SECONDARY', 'https://same.example/v1/k')

    from services.api.app.run_realtime_worker import _resolve_config

    config = _resolve_config()
    assert config['rpc_url'] == 'https://same.example/v1/k'
    assert config['rpc_url_secondary'] == '', 'identical secondary is not a failover target'


# ---------------------------------------------------------------------------
# 8. Fallback provider pressure: 429 backoff + HTTP secondary failover
# ---------------------------------------------------------------------------

def test_fast_tail_backs_off_exponentially_on_429():
    ingestor = _make_ingestor()
    assert ingestor._fast_tail_effective_interval(60.0) == 60.0
    ingestor._fast_tail_rate_limit_strikes = 1
    assert ingestor._fast_tail_effective_interval(60.0) == 120.0
    ingestor._fast_tail_rate_limit_strikes = 2
    assert ingestor._fast_tail_effective_interval(60.0) == 240.0
    ingestor._fast_tail_rate_limit_strikes = 10
    assert ingestor._fast_tail_effective_interval(60.0) == 600.0, (
        'backoff must cap so the fallback still recovers'
    )


def test_fast_tail_fails_over_to_secondary_http_after_repeated_failures():
    from services.api.app import base_realtime_ingestor as mod

    ingestor = _make_ingestor(rpc_url_secondary='https://http-secondary.example/v1/k')

    capture = _LogCapture()
    mod.logger.addHandler(capture)
    mod.logger.setLevel(logging.DEBUG)
    try:
        ingestor._fast_tail_consecutive_failures = 2
        ingestor._maybe_failover_http_rpc()
        assert ingestor.rpc_url == 'https://rpc.example/v1/key', (
            'must not fail over below the threshold'
        )
        ingestor._fast_tail_consecutive_failures = 3
        ingestor._maybe_failover_http_rpc()
    finally:
        mod.logger.removeHandler(capture)

    assert ingestor.rpc_url == 'https://http-secondary.example/v1/k'
    assert ingestor._http_failover_done is True
    failover = next(
        (m for m in capture.messages if 'realtime_http_provider_failover' in m), None,
    )
    assert failover is not None
    assert 'old_host=rpc.example' in failover
    assert 'new_host=http-secondary.example' in failover
    # Never the full URL (which may embed a key) in logs.
    assert '/v1/k' not in failover


def test_fast_tail_without_secondary_never_swaps():
    ingestor = _make_ingestor()
    ingestor._fast_tail_consecutive_failures = 10
    ingestor._maybe_failover_http_rpc()
    assert ingestor.rpc_url == 'https://rpc.example/v1/key'
    assert ingestor._http_failover_done is False


def test_fast_tail_stop_when_returns_before_any_rpc_call():
    """The circuit-open fast-tail must return for the WSS probe without touching
    the provider once stop_when fires (no fallback RPC spam past the window)."""
    from services.api.app import base_realtime_ingestor as mod

    ingestor = _make_ingestor()

    def _fail_rpc(method, params):
        raise AssertionError(f'no RPC call expected after stop_when fires: {method}')

    ingestor._rpc_call = _fail_rpc  # type: ignore[method-assign]
    ingestor._watched_targets = lambda: []  # type: ignore[method-assign]

    capture = _LogCapture()
    mod.logger.addHandler(capture)
    mod.logger.setLevel(logging.DEBUG)
    try:
        asyncio.run(ingestor._run_http_fast_tail(stop_when=lambda: True))
    finally:
        mod.logger.removeHandler(capture)

    assert any('realtime_fast_tail_stopped' in m for m in capture.messages)


def test_circuit_reopens_probe_after_window_and_resumes_wss():
    """End-to-end circuit lifecycle: TLS trip -> stable-polling fallback wait ->
    window elapses -> half-open probe reconnects the WSS (no permanent disable)."""
    from services.api.app import base_realtime_ingestor as mod

    # No HTTP RPC -> the circuit-open fallback idles in stable-polling mode.
    ingestor = _make_ingestor(rpc_url='')
    ingestor.provider_circuit_seconds = 1  # shrink the window for the test

    ws_calls = [0]

    async def _mock_ws_subscribe():
        ws_calls[0] += 1
        if ws_calls[0] <= 4:
            raise ssl.SSLError(1, TLS_ERROR_TEXT)
        # The half-open probe reached the WSS again — end the test.
        raise asyncio.CancelledError()

    ingestor._ws_subscribe = _mock_ws_subscribe  # type: ignore[method-assign]

    capture = _LogCapture()
    mod.logger.addHandler(capture)
    mod.logger.setLevel(logging.DEBUG)
    try:
        with pytest.raises(asyncio.CancelledError):
            asyncio.run(ingestor.run_forever())
    finally:
        mod.logger.removeHandler(capture)

    assert ws_calls[0] == 5, (
        'the WSS must be probed exactly once after the circuit window elapses; '
        f'got {ws_calls[0]} connect attempts'
    )
    assert any('realtime_fallback_activated' in m
               and 'provider_mode=stable_rpc_polling_fallback' in m
               for m in capture.messages)
    assert any('realtime_fallback_ended' in m for m in capture.messages)
    assert any('provider_circuit_half_open' in m for m in capture.messages)
    assert ingestor._wss_permanently_disabled is False


# ---------------------------------------------------------------------------
# 9. No reconnect-gap backfill spam while the TLS handshake keeps failing
# ---------------------------------------------------------------------------

def test_tls_failures_do_not_trigger_reconnect_backfill():
    ingestor = _make_ingestor()

    ws_calls = [0]

    async def _mock_ws_subscribe():
        ws_calls[0] += 1
        raise ssl.SSLError(1, TLS_ERROR_TEXT)

    backfill_calls = [0]

    async def _mock_backfill(a: int, b: int) -> int:
        backfill_calls[0] += 1
        return 0

    async def _mock_fast_tail(stop_when=None):
        raise asyncio.CancelledError()

    ingestor._ws_subscribe = _mock_ws_subscribe  # type: ignore[method-assign]
    ingestor._backfill = _mock_backfill  # type: ignore[method-assign]
    ingestor._run_http_fast_tail = _mock_fast_tail  # type: ignore[method-assign]

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(ingestor.run_forever())

    assert backfill_calls[0] == 0, (
        'a failed TLS handshake must not trigger eth_getLogs/eth_getBlockByNumber '
        'backfill scans on every retry (provider pressure)'
    )


# ---------------------------------------------------------------------------
# 10. TLS host-safety: the HTTP fast-tail must NEVER poll the SAME host whose WSS
#     just failed the TLS handshake (the production http_fast_tail_error
#     TLSV1_ALERT_INTERNAL_ERROR loop). A TLS internal error is a whole-host
#     failure — the same host's HTTPS RPC fails the identical handshake — so the
#     failover ladder is: primary WSS -> secondary WSS -> HTTP fast-tail on a
#     DIFFERENT (healthy) host -> stable RPC polling only.
# ---------------------------------------------------------------------------

# A single QuickNode-style provider whose WSS and derived HTTPS RPC share ONE
# host: a TLS internal error on the WSS means that host's HTTPS RPC is broken too.
QUICKNODE_WS = 'wss://alpha.quiknode.example/abc123'
QUICKNODE_HTTP = 'https://alpha.quiknode.example/abc123'
# A second, independent provider on a DIFFERENT host — a real failover target.
ALT_HTTP = 'https://base-mainnet.alt-provider.example/xyz789'


def test_host_level_failed_hosts_only_tracks_tls_reason():
    # A WSS-only failure (silent session) is NOT a host-level failure: the host's
    # HTTPS RPC stays usable, so it must not exclude the fast-tail.
    silent = _make_ingestor(ws_url=QUICKNODE_WS, rpc_url=QUICKNODE_HTTP)
    silent._mark_ws_provider_unhealthy('wss_no_heads')
    assert silent._host_level_failed_hosts() == set()
    assert silent._fast_tail_rpc_candidate() == QUICKNODE_HTTP

    # A TLS internal error IS a host-level failure: the same-host HTTPS RPC is out.
    tls = _make_ingestor(ws_url=QUICKNODE_WS, rpc_url=QUICKNODE_HTTP)
    tls._mark_ws_provider_unhealthy('tls_internal_error')
    assert tls._host_level_failed_hosts() == {'alpha.quiknode.example'}
    assert tls._fast_tail_rpc_candidate() is None


def test_same_host_tls_no_secondary_falls_back_to_stable_polling_only():
    """Requirement 4: primary WSS TLS internal error and the ONLY HTTP RPC is the
    same broken host -> stable RPC polling fallback, never a doomed fast-tail."""
    from services.api.app.base_realtime_ingestor import (
        BaseRealtimeIngestor,
        STABLE_POLLING_FALLBACK_MODE,
    )

    ingestor = BaseRealtimeIngestor(
        rpc_url=QUICKNODE_HTTP, ws_url=QUICKNODE_WS, watcher_name='w',
    )
    ingestor._mark_ws_provider_unhealthy('tls_internal_error')

    assert ingestor._circuit_fallback_mode() == STABLE_POLLING_FALLBACK_MODE
    # source_status/ingestion_mode reflect stable-polling-only, never fast-tail.
    assert ingestor.state['source_status'] == STABLE_POLLING_FALLBACK_MODE
    assert ingestor._ingestion_mode == STABLE_POLLING_FALLBACK_MODE
    assert ingestor._fallback_is_active() is True

    hb = _heartbeat_log(ingestor)
    assert 'provider_mode=stable_rpc_polling_fallback' in hb, hb
    assert 'fallback_active=True' in hb, hb
    # UI truth (requirement 4-5): no realtime path scans in stable-only fallback.
    assert 'realtime_scanning_active=False' in hb, hb
    # Never claim the fast-tail is active against the broken host.
    assert 'provider_mode=quicknode_http_fast_tail' not in hb, hb
    assert 'degraded_reason=tls_internal_error' in hb, hb


def test_same_host_tls_uses_secondary_http_when_configured():
    """Requirement 6: with the secondary WSS missing but a secondary HTTP RPC on a
    DIFFERENT host configured, the fast-tail uses it (not the broken primary)."""
    from services.api.app.base_realtime_ingestor import (
        BaseRealtimeIngestor,
        HTTP_FAST_TAIL_SOURCE,
    )

    ingestor = BaseRealtimeIngestor(
        rpc_url=QUICKNODE_HTTP, ws_url=QUICKNODE_WS, watcher_name='w',
        rpc_url_secondary=ALT_HTTP,
    )
    ingestor._mark_ws_provider_unhealthy('tls_internal_error')

    assert ingestor._host_level_failed_hosts() == {'alpha.quiknode.example'}
    # The healthy secondary HTTP host is the fast-tail target — not the broken one.
    assert ingestor._fast_tail_rpc_candidate() == ALT_HTTP
    assert ingestor._circuit_fallback_mode() == HTTP_FAST_TAIL_SOURCE
    assert ingestor.state['source_status'] == HTTP_FAST_TAIL_SOURCE

    hb = _heartbeat_log(ingestor)
    assert 'provider_mode=quicknode_http_fast_tail' in hb, hb
    assert 'fallback_active=True' in hb, hb
    assert 'realtime_scanning_active=True' in hb, hb


def test_fast_tail_switches_off_tls_broken_host_before_polling():
    """Requirement 3: _run_http_fast_tail must fail the HTTP RPC over to the healthy
    secondary host BEFORE its first poll — it must never call the TLS-broken host."""
    from services.api.app import base_realtime_ingestor as mod
    from services.api.app.base_realtime_ingestor import _ws_url_host

    ingestor = _make_ingestor(
        ws_url=QUICKNODE_WS, rpc_url=QUICKNODE_HTTP, rpc_url_secondary=ALT_HTTP,
    )
    ingestor._mark_ws_provider_unhealthy('tls_internal_error')
    ingestor._watched_targets = lambda: []  # type: ignore[method-assign]

    def _guarded_rpc(method, params):
        # An RPC against the broken primary host would reproduce the TLS loop.
        assert _ws_url_host(ingestor.rpc_url) != 'alpha.quiknode.example', (
            f'fast-tail must not call the TLS-broken host: {method}'
        )
        raise asyncio.CancelledError()

    ingestor._rpc_call = _guarded_rpc  # type: ignore[method-assign]

    capture = _LogCapture()
    mod.logger.addHandler(capture)
    mod.logger.setLevel(logging.DEBUG)
    try:
        # The first eth_blockNumber runs against the SWITCHED (healthy) host, then
        # CancelledError ends the loop.
        with pytest.raises(asyncio.CancelledError):
            asyncio.run(ingestor._run_http_fast_tail())
    finally:
        mod.logger.removeHandler(capture)

    assert ingestor.rpc_url == ALT_HTTP, 'fast-tail must switch to the secondary HTTP host'
    failover = next(
        (m for m in capture.messages if 'realtime_http_provider_failover' in m
         and 'reason=primary_host_tls_failure' in m), None,
    )
    assert failover is not None, f'host failover must be logged; got {capture.messages}'
    assert 'old_host=alpha.quiknode.example' in failover
    assert 'new_host=base-mainnet.alt-provider.example' in failover
    # The fast-tail start line names the healthy host, never the broken one.
    started = next((m for m in capture.messages if 'realtime_http_fast_tail_started' in m), None)
    assert started is not None
    assert 'rpc_host=base-mainnet.alt-provider.example' in started


def test_circuit_open_fallback_same_host_tls_runs_stable_polling_not_fast_tail():
    """Requirement 3-4: with the WSS circuit TLS-open and the only HTTP RPC on the
    same broken host, the circuit-open fallback runs stable RPC polling only — the
    HTTP fast-tail is NEVER started against the failed provider."""
    from services.api.app import base_realtime_ingestor as mod
    from services.api.app.base_realtime_ingestor import STABLE_POLLING_FALLBACK_MODE

    ingestor = _make_ingestor(ws_url=QUICKNODE_WS, rpc_url=QUICKNODE_HTTP)
    ingestor._mark_ws_provider_unhealthy('tls_internal_error')
    assert ingestor._all_ws_circuits_open() is True

    async def _fast_tail_must_not_run(stop_when=None):
        raise AssertionError('fast-tail must not run against the TLS-broken host')

    ingestor._run_http_fast_tail = _fast_tail_must_not_run  # type: ignore[method-assign]
    # The circuit has half-opened, so the fallback returns immediately for a WSS
    # probe without idling (keeps the test fast, no real sleep).
    ingestor._ws_probe_due = lambda: True  # type: ignore[method-assign]
    ingestor._seconds_until_ws_probe = lambda: 0.0  # type: ignore[method-assign]

    capture = _LogCapture()
    mod.logger.addHandler(capture)
    mod.logger.setLevel(logging.DEBUG)
    try:
        asyncio.run(ingestor._run_circuit_open_fallback())
    finally:
        mod.logger.removeHandler(capture)

    assert ingestor._ingestion_mode == STABLE_POLLING_FALLBACK_MODE
    assert any(
        'realtime_fallback_activated' in m
        and 'provider_mode=stable_rpc_polling_fallback' in m
        for m in capture.messages
    ), capture.messages
    assert any('realtime_fallback_ended' in m for m in capture.messages)


def test_realtime_telemetry_classification_counts_fast_tail_never_stable_polling():
    """Acceptance: a transfer counts as realtime telemetry when Detected By is
    Realtime WebSocket / Realtime Backfill / Fast Tail — and stable_rpc_polling is
    never realtime. (The strict WSS *proof* set stays WSS/backfill-only via
    is_realtime_detection_proof; this is the realtime telemetry classification the
    UI/telemetry list uses.)"""
    from services.api.app.worker_status import (
        REALTIME_DETECTED_BY,
        STABLE_DETECTED_BY,
        detected_by_from_ingestion_source,
    )

    for tag in ('realtime_websocket', 'realtime_backfill', 'quicknode_http_fast_tail'):
        assert tag in REALTIME_DETECTED_BY
        assert detected_by_from_ingestion_source(tag) == tag
    # Stable RPC polling is NEVER counted as realtime telemetry.
    assert STABLE_DETECTED_BY == 'stable_rpc_polling'
    assert STABLE_DETECTED_BY not in REALTIME_DETECTED_BY
    assert detected_by_from_ingestion_source('rpc_polling') == STABLE_DETECTED_BY
