"""Polling-only MVP: the QuickNode Streams webhook is authenticated then safely
ignored, and stable RPC polling health is never affected by stream lag/heartbeat.

  services/api/app/monitoring_runtime_mode.py  (the REALTIME_STREAMS_ENABLED switch)
  services/api/app/quicknode_streams.py         (process_quicknode_base_stream_webhook gate)
  services/api/app/worker_status.py             (stable-polling health separation)

Covers task test requirements:
   1  polling-only mode disables QuickNode stream processing
   2  the stream webhook returns safely without processing blocks
   3  stream lag does not affect polling provider health
   4  missing stream heartbeat does not reduce polling health
  14  polling-only mode is reversible through configuration
  15  existing real-time code remains intact
"""
from __future__ import annotations

import hashlib
import hmac
import json
import time
from datetime import datetime, timezone
from unittest.mock import patch

import pytest
from fastapi import HTTPException

from services.api.app import monitoring_health_engine as mhe
from services.api.app import monitoring_runtime_mode as mode
from services.api.app import quicknode_streams as qn
from services.api.app import worker_status as ws

WALLET_ADDR = '0x5f6f35fd8b10c5576089f99c7c8c351deb851d1f'
COUNTERPARTY = '0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'
TX_HASH = '0x42eb6fb953a32dc80fef0f62b4eadfa0fed18c7129d68924cd65bdb37e25a51'
SECRET = 'whsec_test_secret_123'
NONCE = 'test-nonce-abc123'
_QN_LOGGER = 'services.api.app.quicknode_streams'


def _sign(body: bytes, *, timestamp: str) -> str:
    return hmac.new(
        SECRET.encode('utf-8'), NONCE.encode('utf-8') + timestamp.encode('utf-8') + body, hashlib.sha256,
    ).hexdigest()


def _signed(body: bytes) -> tuple[str, str]:
    timestamp = str(int(time.time()))
    return _sign(body, timestamp=timestamp), timestamp


def _forbidden(*_a, **_k):
    """Any DB/RPC access in polling-only mode is a bug: the webhook must short-circuit
    before opening a connection or dialing a provider."""
    raise AssertionError('polling-only mode must not touch the database or RPC provider')


class _ReachedProcessing(Exception):
    """Sentinel proving the webhook proceeded PAST the polling-only gate (real-time mode)."""


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    monkeypatch.setenv('QUICKNODE_STREAMS_SECRET', SECRET)
    qn.reset_stream_ignored_state()
    qn.reset_quicknode_log_sampler_state()
    yield
    qn.reset_stream_ignored_state()
    qn.reset_quicknode_log_sampler_state()


def _polling_only(monkeypatch):
    monkeypatch.delenv('REALTIME_STREAMS_ENABLED', raising=False)
    assert mode.polling_only_mode() is True


# ---------------------------------------------------------------------------
# 1 & 2 — polling-only mode disables processing; the webhook returns safely
# ---------------------------------------------------------------------------

def test_polling_only_ignores_webhook_without_processing(monkeypatch):
    _polling_only(monkeypatch)
    # A body that WOULD match a monitored wallet — must still be ignored, not persisted.
    body = json.dumps({
        'hash': TX_HASH, 'from': WALLET_ADDR, 'to': COUNTERPARTY,
        'value': '1000000000000000000', 'block_number': 47286578, 'chain_id': 8453,
    }).encode('utf-8')
    signature, timestamp = _signed(body)
    # If the gate is bypassed, the processor opens a pg_connection / dials RPC and blows up.
    with patch.object(qn, 'pg_connection', _forbidden), \
         patch.object(qn, '_make_base_rpc_client', _forbidden):
        result = qn.process_quicknode_base_stream_webhook(
            raw_body=body, signature_header=signature, nonce_header=NONCE, timestamp_header=timestamp,
        )
    assert result['received'] is True
    assert result['ignored'] is True
    assert result['reason'] == 'polling_only_mode'
    assert result['mode'] == 'polling'
    assert result['persisted'] == 0
    assert result['matched'] == 0
    assert result['tx_count'] == 0
    assert result['results'] == []


@pytest.mark.parametrize('lane', [qn.LANE_BASE, qn.LANE_LIVE, qn.LANE_BACKFILL])
def test_polling_only_short_circuits_every_lane(monkeypatch, lane):
    _polling_only(monkeypatch)
    body = json.dumps({'hash': TX_HASH, 'from': WALLET_ADDR, 'block_number': 10}).encode('utf-8')
    signature, timestamp = _signed(body)
    with patch.object(qn, 'pg_connection', _forbidden), \
         patch.object(qn, '_make_base_rpc_client', _forbidden):
        result = qn.process_quicknode_base_stream_webhook(
            raw_body=body, signature_header=signature, nonce_header=NONCE,
            timestamp_header=timestamp, lane=lane,
        )
    assert result['ignored'] is True
    assert result['stream_lane'] == qn._normalize_lane(lane)


def test_polling_only_still_verifies_signature(monkeypatch):
    # Security policy is preserved: the gate is AFTER signature verification, so an
    # invalid signature is still rejected 401 — never a silent ignore of a forged POST.
    _polling_only(monkeypatch)
    body = json.dumps({'hash': TX_HASH}).encode('utf-8')
    timestamp = str(int(time.time()))
    with pytest.raises(HTTPException) as exc:
        qn.process_quicknode_base_stream_webhook(
            raw_body=body, signature_header='deadbeef', nonce_header=NONCE, timestamp_header=timestamp,
        )
    assert exc.value.status_code == 401


def test_polling_only_emits_rate_limited_ignored_log(monkeypatch, caplog):
    _polling_only(monkeypatch)
    body = json.dumps({'hash': TX_HASH}).encode('utf-8')
    signature, timestamp = _signed(body)
    with caplog.at_level('INFO', logger=_QN_LOGGER):
        with patch.object(qn, 'pg_connection', _forbidden):
            qn.process_quicknode_base_stream_webhook(
                raw_body=body, signature_header=signature, nonce_header=NONCE, timestamp_header=timestamp,
            )
    assert 'event=quicknode_stream_ignored reason=polling_only_mode' in caplog.text
    assert 'requests_received=1' in caplog.text
    assert 'window_seconds=' in caplog.text
    # The per-block processing/normalization logs must NOT appear.
    assert 'quicknode_stream_transactions_normalized' not in caplog.text
    assert 'quicknode_stream_targets_loaded' not in caplog.text


def test_ignored_log_is_rate_limited_and_counts(monkeypatch, caplog):
    # A still-configured (but paused) stream must collapse into one summary per window
    # carrying the request count — never a line per request.
    monkeypatch.delenv('QUICKNODE_STREAMS_LOG_SAMPLE_SECONDS', raising=False)  # default 60s
    qn.reset_stream_ignored_state()
    with caplog.at_level('INFO', logger=_QN_LOGGER):
        qn._record_stream_ignored_polling_only(now=1000.0)   # first of window -> emit count=1
        qn._record_stream_ignored_polling_only(now=1001.0)   # within window   -> suppressed
        qn._record_stream_ignored_polling_only(now=1002.0)   # within window   -> suppressed
        qn._record_stream_ignored_polling_only(now=1065.0)   # window elapsed   -> emit count=3
    emitted = [ln for ln in caplog.text.splitlines() if 'event=quicknode_stream_ignored' in ln]
    assert len(emitted) == 2
    assert 'requests_received=1' in emitted[0]
    assert 'requests_received=3' in emitted[1]
    assert 'window_seconds=60' in emitted[0]


# ---------------------------------------------------------------------------
# 14 — reversible: real-time mode reaches processing again
# ---------------------------------------------------------------------------

def test_realtime_mode_reaches_processing(monkeypatch):
    monkeypatch.setenv('REALTIME_STREAMS_ENABLED', 'true')
    assert mode.polling_only_mode() is False
    body = json.dumps({
        'hash': TX_HASH, 'from': WALLET_ADDR, 'to': COUNTERPARTY,
        'value': '1', 'block_number': 10, 'chain_id': 8453,
    }).encode('utf-8')
    signature, timestamp = _signed(body)

    def _reached():
        raise _ReachedProcessing()

    # With streams enabled the gate is NOT taken, so the processor advances to open a
    # DB connection — proving the same switch reverses the behavior end to end.
    with patch.object(qn, 'pg_connection', _reached):
        with pytest.raises(_ReachedProcessing):
            qn.process_quicknode_base_stream_webhook(
                raw_body=body, signature_header=signature, nonce_header=NONCE, timestamp_header=timestamp,
            )


# ---------------------------------------------------------------------------
# 3 & 4 — stream lag / missing stream heartbeat never reduce polling health
# ---------------------------------------------------------------------------

_NOW = datetime(2026, 7, 18, 12, 0, 0, tzinfo=timezone.utc)


def _degraded_lagging_stream_watcher() -> dict:
    return {
        'degraded': True,
        'degraded_reason': 'stream_lag_exceeds_threshold',
        'source_status': 'realtime_websocket',
        'metrics': {
            'provider_mode': 'realtime_websocket',
            'heads_received': 5,
            'rate_limited': False,
            'fallback_active': True,
        },
    }


def test_stream_lag_does_not_affect_polling_health():
    # Fresh stable heartbeat/poll + a DEGRADED (lagging) realtime stream watcher: the
    # canonical polling verdict must remain healthy/active.
    status = ws.build_worker_status(
        now=_NOW,
        realtime_is_enabled=True,
        stable_last_heartbeat_at=_NOW,
        stable_last_poll_at=_NOW,
        heartbeat_ttl_seconds=600,
        realtime_watcher=_degraded_lagging_stream_watcher(),
        stable_poll_succeeded=True,
    )
    assert status['stable_polling']['active'] is True
    assert status['stable_polling']['rpc_polling_available'] is True
    assert status['monitoring_source_live'] is True
    assert status['rpc_polling_unavailable'] is False


def test_missing_stream_heartbeat_does_not_reduce_polling_health():
    # No realtime watcher row at all (stream heartbeat missing) — stable polling stays
    # active and the source stays live.
    status = ws.build_worker_status(
        now=_NOW,
        realtime_is_enabled=False,
        stable_last_heartbeat_at=_NOW,
        stable_last_poll_at=_NOW,
        heartbeat_ttl_seconds=600,
        realtime_watcher=None,
        stable_poll_succeeded=True,
    )
    assert status['stable_polling']['active'] is True
    assert status['monitoring_source_live'] is True
    assert status['headline'].startswith('Stable polling active')


def test_polling_health_identical_with_and_without_lagging_stream():
    # The stable-polling verdict must be byte-for-byte independent of stream state.
    common = dict(
        now=_NOW,
        realtime_is_enabled=True,
        stable_last_heartbeat_at=_NOW,
        stable_last_poll_at=_NOW,
        heartbeat_ttl_seconds=600,
        stable_poll_succeeded=True,
    )
    healthy_stream = ws.build_worker_status(
        realtime_watcher={
            'degraded': False,
            'metrics': {'provider_mode': 'realtime_websocket', 'heads_received': 100, 'rate_limited': False},
        },
        **common,
    )
    lagging_stream = ws.build_worker_status(
        realtime_watcher=_degraded_lagging_stream_watcher(), **common,
    )
    assert healthy_stream['stable_polling'] == lagging_stream['stable_polling']
    assert healthy_stream['monitoring_source_live'] == lagging_stream['monitoring_source_live'] is True


# ---------------------------------------------------------------------------
# 15 — existing real-time code remains intact (reversibility support)
# ---------------------------------------------------------------------------

def test_existing_realtime_code_intact():
    import services.api.app.base_realtime_ingestor as bri
    import services.api.app.run_realtime_worker as rrw
    import services.api.app.run_quicknode_live_worker as rqlw

    assert hasattr(bri, 'BaseRealtimeIngestor')
    assert callable(rrw.main)
    assert callable(rqlw.run_one_tick)
    for name in (
        'process_quicknode_base_stream_webhook',
        'run_live_tip_ingest',
        'run_backfill_step',
        '_process_realtime_lane_batch',
        'build_quicknode_live_lane_status',
        'verify_quicknode_stream_signature',
    ):
        assert hasattr(qn, name), name
    assert qn.LANE_LIVE == 'live'
    assert qn.LANE_BACKFILL == 'backfill'
    # QuickNode Streams telemetry is still classified as a realtime detection path.
    assert 'quicknode_stream' in ws.REALTIME_DETECTED_BY


# ---------------------------------------------------------------------------
# 8 — the Source Optimization Agent must not recommend restoring real-time Streams
# ---------------------------------------------------------------------------

@pytest.mark.parametrize('action', [
    'restore_realtime_streams',
    'enable_realtime_streams',
    'enable_quicknode_streams',
    'restore_websocket',
])
def test_agent_cannot_recommend_restoring_realtime_streams(action):
    # "Restore/enable real-time Streams" is not in the grounded action vocabulary, so
    # the recommendation validator rejects it as unsupported: during the polling-only
    # MVP the agent can only explain engine facts and recommend polling-provider
    # actions, never turn real-time Streams back on on its own.
    result = mhe.validate_ai_recommendation(
        {'recommended_action': action, 'supporting_record_ids': []},
        known_provider_ids=[], known_record_ids=[],
    )
    assert result.valid is False
    assert any(reason.startswith('unsupported_action') for reason in result.rejected_reasons)


def test_agent_still_recommends_polling_provider_actions():
    # Polling-provider recommendations remain valid so the agent keeps optimizing the
    # canonical scheduled-polling path (switch primary / add approved fallback) — and
    # such changes still require explicit human approval.
    result = mhe.validate_ai_recommendation(
        {
            'recommended_action': 'add_fallback_provider',
            'provider_id': 'provider-1',
            'supporting_record_ids': ['record-1'],
        },
        known_provider_ids=['provider-1'],
        known_record_ids=['record-1'],
    )
    assert result.valid is True
    assert result.approval_required is True
