"""Runtime health semantics for the QuickNode Streams realtime path.

Production evidence that motivated these tests (Base Mainnet, real tx
0x5ae0…beef7 at block 50429022): the Stream delivered, telemetry persisted with
``detected_by=quicknode_stream`` / ``evidence_source=live``, Threat Monitoring
showed *Live / Fresh* — while the runtime status simultaneously logged::

    fresh_live_reporting_systems=1 reporting_systems=1 receipts_reporting_systems=1
    chosen_evidence_source=replay monitoring_status=limited
    downgrade_reasons=evidence_source_not_live,provider_degraded_or_unreachable

Root cause: the runtime evidence selector modelled exactly ONE ingestion path —
the stable RPC poll.

  * ``canonical_last_telemetry_at`` is filtered to ``event_type IN
    ('rpc_polling','live_provider') AND provider_type IN ('evm_rpc','live_provider')``,
    so QuickNode Stream rows (``provider_type='quicknode_stream'``,
    ``event_type='wallet_transfer_detected'``) can never contribute freshness.
  * ``provider_reachable`` was an RPC-only fact (an inline ``eth_chainId`` probe or
    a polling/WebSocket ``source_type``); the Streams path had no representation,
    so any fallback-leg degradation asserted ``provider_degraded_or_unreachable``.
  * ``canonical_reporting_systems`` (→ ``fresh_live_reporting_systems``) DOES count
    Stream telemetry — hence the contradiction.

The fix keeps the two paths as separate, separately-named facts (CLAUDE.md:
heartbeat / poll / telemetry are distinct proofs) and is fail-closed: no healthy
realtime evidence reproduces the previous verdict exactly.

Covered here:

  1-3. Fresh Stream near tip → live evidence may be selected; the 900s fallback
       cadence never invalidates it.
  4-5. Stream stopped/stale, or far behind the tip → truthful downgrade.
  6-7. Per-EVENT freshness stays time-based (an old row stays Stale).
  8.   A healthy Stream never makes an unrelated stale target report fresh.
  9.   Fallback RPC failure degrades the fallback leg only, without claiming the
       active Stream stopped.
  10.  No live Stream evidence → live status is never fabricated.
  14-16. Canary isolation, the 900s stable cadence, and the paused
       WebSocket/mempool subsystems are unchanged by this fix.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

import pytest

from services.api.app import monitoring_runner
from services.api.app import quicknode_streams as qn
from services.api.app.domains.threat_detection.endpoints import _row_freshness
from services.api.app.monitoring_truth import (
    REALTIME_INGESTION_DEGRADED,
    REALTIME_INGESTION_DISABLED,
    REALTIME_INGESTION_HEALTHY,
    REALTIME_INGESTION_NO_EVIDENCE,
    REALTIME_INGESTION_STALE,
    REALTIME_INGESTION_UNKNOWN,
    derive_realtime_ingestion_health,
)

NOW = datetime.now(timezone.utc)
WORKSPACE_ID = '00000000-0000-0000-0000-000000000001'
TARGET_ID = '00000000-0000-0000-0000-000000000002'
SYSTEM_ID = '00000000-0000-0000-0000-000000000003'
ASSET_ID = '00000000-0000-0000-0000-000000000004'

# Base tip numbers from the proven production run.
CHAIN_HEAD = 50429022
STREAM_STALE_SECONDS = monitoring_runner.QUICKNODE_STREAM_STALE_SECONDS


# ---------------------------------------------------------------------------
# Fake runtime-status connection: one enabled Base wallet target, a QuickNode
# live-lane checkpoint, and an independently configurable fallback RPC poll.
# ---------------------------------------------------------------------------

class _Result:
    def __init__(self, rows=None, row=None):
        self._rows = list(rows or [])
        self._row = row

    def fetchall(self):
        return list(self._rows)

    def fetchone(self):
        return self._row


class _RuntimeConn:
    def __init__(
        self,
        *,
        stream_checkpoint_age_seconds: int | None = 2,
        stream_lag_blocks: int = 0,
        stream_telemetry_age_seconds: int | None = 30,
        rpc_poll_age_seconds: int | None = 800,
        target_coverage_age_seconds: int | None = None,
    ):
        self.stream_checkpoint_at = (
            None if stream_checkpoint_age_seconds is None
            else NOW - timedelta(seconds=stream_checkpoint_age_seconds)
        )
        self.stream_lag_blocks = int(stream_lag_blocks)
        self.stream_telemetry_at = (
            None if stream_telemetry_age_seconds is None
            else NOW - timedelta(seconds=stream_telemetry_age_seconds)
        )
        self.rpc_ts = (
            None if rpc_poll_age_seconds is None else NOW - timedelta(seconds=rpc_poll_age_seconds)
        )
        self.target_coverage_at = (
            None if target_coverage_age_seconds is None
            else NOW - timedelta(seconds=target_coverage_age_seconds)
        )

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def commit(self):
        return None

    def rollback(self):
        return None

    # -- helpers -----------------------------------------------------------
    def _stream_has_any_fresh_telemetry(self) -> bool:
        return self.stream_telemetry_at is not None or self.rpc_ts is not None

    def execute(self, q, p=None):  # noqa: C901 - a router, deliberately flat
        qn_text = ' '.join(str(q).split())

        if 'FROM workspaces' in qn_text and 'slug' in qn_text:
            return _Result(row={'id': WORKSPACE_ID, 'slug': 'ws'})

        # QuickNode live/backfill lane checkpoints (canonical stream-health source).
        if 'FROM quicknode_stream_checkpoints' in qn_text and 'SELECT' in qn_text:
            key = str((p or ('',))[0])
            if key == qn.QUICKNODE_STREAM_KEY_BASE_LIVE and self.stream_checkpoint_at is not None:
                return _Result(row={
                    'stream_key': key,
                    'latest_stream_block': CHAIN_HEAD,
                    'last_processed_block': CHAIN_HEAD - self.stream_lag_blocks,
                    'missed_block_gap': 0,
                    'stream_started_at_block': CHAIN_HEAD - 5000,
                    'webhook_received_at': self.stream_checkpoint_at,
                })
            return _Result(row=None)

        # Freshest live quicknode_stream telemetry for this workspace.
        if (
            'FROM telemetry_events' in qn_text
            and 'MAX(observed_at) AS ts' in qn_text
            and 'provider_type = %s' in qn_text
        ):
            return _Result(row={'ts': self.stream_telemetry_at})

        # canonical_last_telemetry_at — stable RPC poll rows only.
        if (
            'FROM telemetry_events' in qn_text
            and 'MAX(observed_at) AS ts' in qn_text
            and "evidence_source = 'live'" in qn_text
        ):
            return _Result(row={'ts': self.rpc_ts})

        if 'SELECT t.id, t.asset_id' in qn_text and 'FROM targets t' in qn_text:
            return _Result(rows=[{'id': TARGET_ID, 'asset_id': ASSET_ID}])

        if 'FROM monitoring_configs' in qn_text and 'COUNT' in qn_text:
            return _Result(row={'c': 1})

        # canonical_reporting_systems: any telemetry row inside the window counts.
        if 'SELECT DISTINCT te.target_id' in qn_text and 'FROM telemetry_events te' in qn_text:
            return _Result(rows=[{'target_id': TARGET_ID}] if self._stream_has_any_fresh_telemetry() else [])

        if 'FROM monitored_systems' in qn_text:
            return _Result(rows=[{
                'id': SYSTEM_ID,
                'target_id': TARGET_ID,
                'asset_id': ASSET_ID,
                'is_enabled': True,
                'enabled': True,
                'monitoring_enabled': True,
                'runtime_status': 'healthy',
                'target_type': 'evm_wallet',
                'monitoring_interval_seconds': 900,
                'last_heartbeat': NOW - timedelta(seconds=60),
                'last_event_at': self.stream_telemetry_at,
                'last_coverage_telemetry_at': self.target_coverage_at,
            }])

        if 'FROM monitoring_event_receipts' in qn_text:
            return _Result(rows=[])
        if 'FROM target_coverage_records' in qn_text:
            return _Result(rows=[])
        if 'FROM monitoring_polls' in qn_text:
            return _Result(row={'ts': self.rpc_ts})
        if 'FROM monitoring_heartbeats' in qn_text:
            return _Result(row={'ts': NOW - timedelta(seconds=60)})

        if 'COUNT(' in qn_text:
            return _Result(row={'c': 0})
        if 'MAX(' in qn_text:
            return _Result(row={'ts': None})
        if 'SELECT' in qn_text:
            return _Result(rows=[], row={})
        return _Result(row={})


@pytest.fixture(autouse=True)
def _clear_runtime_status_caches():
    """The RUNTIME_STATUS_* caches are module-level and workspace-keyed, so a summary
    cached by one test would otherwise leak into the next test that uses the same
    workspace id. Clear on both sides of every test."""
    monitoring_runner.RUNTIME_STATUS_WORKSPACE_CACHE.clear()
    monitoring_runner.RUNTIME_STATUS_SUMMARY_CACHE.clear()
    yield
    monitoring_runner.RUNTIME_STATUS_WORKSPACE_CACHE.clear()
    monitoring_runner.RUNTIME_STATUS_SUMMARY_CACHE.clear()


class _State:
    pass


class _Req:
    def __init__(self):
        self.state = _State()
        self.headers = {'x-workspace-id': WORKSPACE_ID, 'x-workspace-slug': 'ws'}
        self.query_params = {}


DEFAULT_HEALTH = {
    'worker_running': True,
    'source_type': 'polling',
    'ingestion_mode': 'live',
    'last_heartbeat_at': NOW - timedelta(seconds=60),
    'last_cycle_at': NOW - timedelta(seconds=800),
}


def _runtime_payload(monkeypatch, conn, *, health=None, streams_enabled=True):
    monkeypatch.setenv('REALTIME_STREAMS_ENABLED', 'true' if streams_enabled else 'false')
    monkeypatch.setattr(
        monitoring_runner, 'resolve_workspace_context_for_request',
        lambda *a, **k: ({'id': 'u'}, {'workspace_id': WORKSPACE_ID, 'workspace': {'slug': 'ws'}}, True),
    )
    monkeypatch.setattr(monitoring_runner, 'ensure_pilot_schema', lambda _c: None)
    monkeypatch.setattr(monitoring_runner, 'ensure_monitoring_runtime_schema_capabilities', lambda *a, **k: None)
    monkeypatch.setattr(monitoring_runner, 'pg_connection', lambda: conn)
    resolved_health = dict(health or DEFAULT_HEALTH)
    monkeypatch.setattr(monitoring_runner, 'get_monitoring_health', lambda: resolved_health)
    monkeypatch.setattr(monitoring_runner, 'live_mode_enabled', lambda: True)
    monitoring_runner.RUNTIME_STATUS_WORKSPACE_CACHE.clear()
    monitoring_runner.RUNTIME_STATUS_SUMMARY_CACHE.clear()
    payload = monitoring_runner.monitoring_runtime_status(_Req())
    return payload.get('workspace_monitoring_summary') or payload


# ---------------------------------------------------------------------------
# 1. Fresh Stream delivery + healthy near-tip checkpoint → live is selectable
# ---------------------------------------------------------------------------

def test_fresh_stream_near_tip_selects_live_evidence(monkeypatch):
    summary = _runtime_payload(monkeypatch, _RuntimeConn(
        stream_checkpoint_age_seconds=2,
        stream_lag_blocks=0,
        stream_telemetry_age_seconds=30,
        rpc_poll_age_seconds=800,
    ))
    realtime = summary['realtime_ingestion']
    assert realtime['status'] == REALTIME_INGESTION_HEALTHY
    assert realtime['healthy'] is True
    assert realtime['lane_state'] == 'live'
    assert realtime['lag_blocks'] == 0
    assert summary['evidence_source'] == 'live'
    assert summary['source_of_evidence'] == 'live'


def test_fresh_stream_reports_fresh_live_reporting_systems(monkeypatch):
    """The contradiction itself: fresh_live_reporting_systems >= 1 must not coexist
    with a replay evidence source."""
    summary = _runtime_payload(monkeypatch, _RuntimeConn())
    assert summary['fresh_live_reporting_systems'] >= 1
    assert summary['evidence_source'] == 'live'
    assert summary['replay_only_systems'] == 0
    assert summary['reporting_systems_status_reason'].startswith('fresh_coverage_window_')


# ---------------------------------------------------------------------------
# 2. Fresh quicknode_stream telemetry → evidence does not fall back to replay
# ---------------------------------------------------------------------------

def test_fresh_stream_telemetry_is_not_downgraded_to_replay(monkeypatch):
    """The exact production shape: the ONLY fresh telemetry is the QuickNode Stream
    row; the stable RPC poll is far outside the freshness window."""
    summary = _runtime_payload(monkeypatch, _RuntimeConn(
        stream_telemetry_age_seconds=30,
        rpc_poll_age_seconds=None,
    ))
    assert summary['evidence_source'] == 'live'
    assert summary['source_of_evidence'] != 'replay_or_none'
    assert summary['realtime_ingestion']['live_evidence_fresh'] is True
    assert summary['telemetry_freshness'] == 'fresh'


# ---------------------------------------------------------------------------
# 3. The 900s RPC fallback cadence does not invalidate a healthy Stream
# ---------------------------------------------------------------------------

@pytest.mark.parametrize('rpc_poll_age_seconds', [300, 900, 1800, 3600, None])
def test_fallback_rpc_cadence_never_invalidates_healthy_stream(monkeypatch, rpc_poll_age_seconds):
    summary = _runtime_payload(monkeypatch, _RuntimeConn(
        stream_checkpoint_age_seconds=2,
        stream_telemetry_age_seconds=30,
        rpc_poll_age_seconds=rpc_poll_age_seconds,
    ))
    assert summary['realtime_ingestion']['healthy'] is True, rpc_poll_age_seconds
    assert summary['evidence_source'] == 'live', rpc_poll_age_seconds
    assert summary['provider_degraded_flag'] is False, rpc_poll_age_seconds


def test_stale_fallback_poll_alone_never_reports_provider_unreachable(monkeypatch):
    """Acceptance criterion: provider_degraded_or_unreachable must not be reported
    solely because the fallback RPC has not polled in the last 300 seconds."""
    summary = _runtime_payload(monkeypatch, _RuntimeConn(rpc_poll_age_seconds=3600))
    assert summary['provider_degraded_flag'] is False
    assert summary['realtime_ingestion']['healthy'] is True


# ---------------------------------------------------------------------------
# 4. Stream stopped / stale → truthful downgrade
# ---------------------------------------------------------------------------

def test_stopped_stream_downgrades_truthfully(monkeypatch):
    summary = _runtime_payload(monkeypatch, _RuntimeConn(
        stream_checkpoint_age_seconds=STREAM_STALE_SECONDS * 4,
        stream_telemetry_age_seconds=STREAM_STALE_SECONDS * 4,
        rpc_poll_age_seconds=None,
    ))
    realtime = summary['realtime_ingestion']
    assert realtime['healthy'] is False
    assert realtime['status'] == REALTIME_INGESTION_STALE
    assert realtime['live_evidence_fresh'] is False
    assert summary['evidence_source'] != 'live'
    assert summary['monitoring_status'] != 'live'


# ---------------------------------------------------------------------------
# 5. Stream far behind the chain tip → truthful degrade
# ---------------------------------------------------------------------------

def test_stream_far_behind_chain_tip_degrades(monkeypatch):
    summary = _runtime_payload(monkeypatch, _RuntimeConn(
        stream_checkpoint_age_seconds=2,
        stream_lag_blocks=qn.live_lag_threshold_blocks() + 500,
        stream_telemetry_age_seconds=30,
        rpc_poll_age_seconds=None,
    ))
    realtime = summary['realtime_ingestion']
    assert realtime['healthy'] is False
    assert realtime['status'] == REALTIME_INGESTION_DEGRADED
    assert realtime['lane_state'] == 'degraded'
    # A far-behind stream's rows are historical evidence, never current live proof.
    assert realtime['live_evidence_fresh'] is False
    assert summary['evidence_source'] != 'live'


# ---------------------------------------------------------------------------
# 6 & 7. Per-EVENT freshness stays time-based
# ---------------------------------------------------------------------------

def test_old_individual_telemetry_event_stays_stale():
    """An old row captured live is still Stale today, even while realtime ingestion
    is healthy — event freshness and ingestion health are different facts."""
    old = NOW - timedelta(days=3)
    assert _row_freshness(old, NOW, 900) == 'stale'


def test_fresh_individual_telemetry_event_stays_fresh():
    assert _row_freshness(NOW - timedelta(seconds=30), NOW, 900) == 'fresh'


def test_healthy_realtime_ingestion_does_not_refresh_old_events(monkeypatch):
    """Runtime realtime health is healthy while a 3-day-old event is still stale."""
    summary = _runtime_payload(monkeypatch, _RuntimeConn())
    assert summary['realtime_ingestion']['healthy'] is True
    assert _row_freshness(NOW - timedelta(days=3), NOW, 900) == 'stale'


# ---------------------------------------------------------------------------
# 8. A healthy Stream never makes unrelated stale coverage look healthy
# ---------------------------------------------------------------------------

def test_healthy_stream_does_not_refresh_stale_target_coverage(monkeypatch):
    """The monitored system's own stale coverage timestamp is not rewritten by a
    healthy Stream; only the workspace's freshest LIVE evidence advances."""
    stale_coverage_age = 7 * 24 * 3600
    conn = _RuntimeConn(
        stream_telemetry_age_seconds=30,
        rpc_poll_age_seconds=None,
        target_coverage_age_seconds=stale_coverage_age,
    )
    summary = _runtime_payload(monkeypatch, conn)
    # The stale per-target coverage row is untouched...
    assert conn.target_coverage_at == NOW - timedelta(seconds=stale_coverage_age)
    # ...and the workspace coverage timestamp is the Stream row, not the stale one.
    assert summary['last_coverage_telemetry_at'] == conn.stream_telemetry_at.isoformat()


def test_stream_healthy_with_no_stream_telemetry_is_not_live_evidence(monkeypatch):
    """Blocks flowing with no matched transfer keeps the PATH healthy but produces no
    live customer evidence (CLAUDE.md: poll != telemetry)."""
    summary = _runtime_payload(monkeypatch, _RuntimeConn(
        stream_telemetry_age_seconds=None,
        rpc_poll_age_seconds=None,
    ))
    realtime = summary['realtime_ingestion']
    assert realtime['healthy'] is True
    assert realtime['live_evidence_fresh'] is False
    assert summary['evidence_source'] != 'live'


# ---------------------------------------------------------------------------
# 9. Fallback RPC failure degrades the fallback leg only
# ---------------------------------------------------------------------------

def test_fallback_rpc_failure_does_not_claim_the_stream_stopped(monkeypatch):
    summary = _runtime_payload(
        monkeypatch,
        _RuntimeConn(stream_telemetry_age_seconds=30, rpc_poll_age_seconds=None),
        health={
            **DEFAULT_HEALTH,
            'source_type': 'unavailable',
            'degraded_reason': 'all_rpc_providers_unavailable',
            'last_error': 'rpc timeout',
        },
    )
    assert summary['fallback_rpc']['degraded_or_unreachable'] is True
    assert summary['realtime_ingestion']['healthy'] is True
    assert summary['realtime_ingestion']['status'] == REALTIME_INGESTION_HEALTHY
    # The provider verdict is not "unreachable" while the Stream is delivering.
    assert summary['provider_degraded_flag'] is False


def test_fallback_and_realtime_are_separately_named_facts(monkeypatch):
    """Task 6: the screens must be able to tell the two apart, not read one green blob."""
    summary = _runtime_payload(monkeypatch, _RuntimeConn())
    assert set(summary['realtime_ingestion']) >= {
        'streams_enabled', 'status', 'healthy', 'live_evidence_fresh',
        'lane_state', 'lag_blocks', 'checkpoint_age_seconds', 'last_live_telemetry_at',
    }
    assert set(summary['fallback_rpc']) >= {
        'degraded_or_unreachable', 'reachable', 'poll_interval_seconds', 'last_poll_at',
    }
    assert summary['fallback_rpc']['poll_interval_seconds'] == 900


# ---------------------------------------------------------------------------
# 10. No live Stream evidence → never fabricate live status
# ---------------------------------------------------------------------------

def test_no_stream_evidence_never_fabricates_live(monkeypatch):
    summary = _runtime_payload(monkeypatch, _RuntimeConn(
        stream_checkpoint_age_seconds=None,
        stream_telemetry_age_seconds=None,
        rpc_poll_age_seconds=None,
    ))
    realtime = summary['realtime_ingestion']
    assert realtime['status'] == REALTIME_INGESTION_NO_EVIDENCE
    assert realtime['healthy'] is False
    assert summary['evidence_source'] != 'live'
    assert summary['monitoring_status'] != 'live'


def test_streams_disabled_is_reported_as_disabled_not_healthy(monkeypatch):
    summary = _runtime_payload(
        monkeypatch, _RuntimeConn(rpc_poll_age_seconds=None), streams_enabled=False,
    )
    realtime = summary['realtime_ingestion']
    assert realtime['streams_enabled'] is False
    assert realtime['status'] == REALTIME_INGESTION_DISABLED
    assert realtime['healthy'] is False
    assert summary['evidence_source'] != 'live'


# ---------------------------------------------------------------------------
# The pure derivation helper, exercised directly (fail-closed matrix)
# ---------------------------------------------------------------------------

WINDOW = 1020
STALE = 300


def _derive(**kw):
    base = dict(
        streams_enabled=True,
        lane_state='live',
        lag_blocks=0,
        checkpoint_age_seconds=2,
        live_telemetry_age_seconds=30,
        checkpoint_stale_seconds=STALE,
        telemetry_window_seconds=WINDOW,
    )
    base.update(kw)
    return derive_realtime_ingestion_health(**base)


def test_helper_healthy_near_tip():
    result = _derive()
    assert result.status == REALTIME_INGESTION_HEALTHY
    assert result.healthy is True
    assert result.live_evidence_fresh is True


def test_helper_disabled_is_never_healthy():
    assert _derive(streams_enabled=False).status == REALTIME_INGESTION_DISABLED
    assert _derive(streams_enabled=False).healthy is False


@pytest.mark.parametrize(
    'lane_state,expected',
    [
        ('degraded', REALTIME_INGESTION_DEGRADED),
        ('stale', REALTIME_INGESTION_STALE),
        ('failed', REALTIME_INGESTION_DEGRADED),
        ('catching_up', REALTIME_INGESTION_DEGRADED),
        (None, REALTIME_INGESTION_UNKNOWN),
        ('', REALTIME_INGESTION_UNKNOWN),
    ],
)
def test_helper_non_live_lane_states_are_never_healthy(lane_state, expected):
    result = _derive(lane_state=lane_state)
    assert result.status == expected
    assert result.healthy is False
    # Fresh rows behind an unhealthy lane are historical evidence, not live proof.
    assert result.live_evidence_fresh is False


def test_helper_stale_checkpoint_overrides_a_live_lane_reading():
    result = _derive(checkpoint_age_seconds=STALE + 1)
    assert result.status == REALTIME_INGESTION_STALE
    assert result.healthy is False


def test_helper_missing_checkpoint_timestamp_is_unknown_not_healthy():
    result = _derive(checkpoint_age_seconds=None)
    assert result.status == REALTIME_INGESTION_UNKNOWN
    assert result.healthy is False


def test_helper_no_evidence_at_all():
    result = _derive(lane_state=None, live_telemetry_age_seconds=None)
    assert result.status == REALTIME_INGESTION_NO_EVIDENCE
    assert result.healthy is False


def test_helper_old_telemetry_is_not_fresh_live_evidence():
    result = _derive(live_telemetry_age_seconds=WINDOW + 1)
    assert result.healthy is True          # the PATH is delivering
    assert result.live_evidence_fresh is False  # but no fresh evidence arrived


def test_helper_ignores_the_fallback_rpc_cadence():
    """No fallback-RPC input exists on the helper's signature — the 900s cadence
    cannot reach this verdict at all."""
    import inspect

    params = set(inspect.signature(derive_realtime_ingestion_health).parameters)
    assert not {p for p in params if 'rpc' in p or 'poll' in p}


# ---------------------------------------------------------------------------
# 14-16. Preserved production posture
# ---------------------------------------------------------------------------

def test_stable_rpc_cadence_remains_900_seconds(monkeypatch):
    for var in ('EVM_POLLING_INTERVAL_SECONDS', 'MONITORING_WORKER_INTERVAL_SECONDS'):
        monkeypatch.delenv(var, raising=False)
    assert monitoring_runner.canonical_polling_interval_seconds() == 900


def test_websocket_and_mempool_remain_disabled_by_default(monkeypatch):
    from services.api.app.monitoring_runtime_mode import resolve_monitoring_runtime_mode

    for var in ('BASE_REALTIME_ENABLED', 'MEMPOOL_MONITORING_ENABLED'):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv('REALTIME_STREAMS_ENABLED', 'true')
    mode = resolve_monitoring_runtime_mode()
    assert mode.realtime_streams_enabled is True
    assert mode.websocket_enabled is False
    assert mode.mempool_enabled is False
    assert mode.scheduled_polling_enabled is True


def test_canary_allowlist_semantics_unchanged(monkeypatch):
    from services.api.app.monitoring_canary import resolve_canary_config

    monkeypatch.setenv('MONITORING_CANARY_ENABLED', 'true')
    monkeypatch.setenv('MONITORING_CANARY_TARGET_ALLOWLIST', TARGET_ID)
    config = resolve_canary_config()
    assert config.enabled is True
    assert config.allowed_target_ids == frozenset({TARGET_ID})
    assert config.allowed_target_count == 1


# ---------------------------------------------------------------------------
# Task 4: the periodic summary reports the Stream's ACTUAL current state
# ---------------------------------------------------------------------------

def test_periodic_summary_reports_real_stream_health(caplog):
    """quicknode_stream_periodic_summary logged health_status=unknown
    chain_head_status=unknown latest_stream_block=unknown while the concurrent
    quicknode_stream_batch lines reported lag_blocks=0 lag_status=live
    health_status=healthy. The aggregator now receives the same per-batch facts."""
    qn.reset_stream_activity()
    qn.record_stream_activity(
        blocks=1, transactions=12, matched=0, persisted=0,
        health_status='healthy', chain_head_status='known',
        latest_stream_block=CHAIN_HEAD, now_monotonic=0.0,
    )
    with caplog.at_level('INFO', logger=qn.logger.name):
        emitted = qn.record_stream_activity(
            blocks=1, transactions=9, matched=0, persisted=0,
            health_status='healthy', chain_head_status='known',
            latest_stream_block=CHAIN_HEAD + 1,
            now_monotonic=qn._STREAM_ACTIVITY_WINDOW_SECONDS + 1.0,
        )
    assert emitted is True
    text = '\n'.join(r.getMessage() for r in caplog.records)
    assert 'quicknode_stream_periodic_summary' in text
    assert 'health_status=healthy' in text
    assert 'chain_head_status=known' in text
    assert f'latest_stream_block={CHAIN_HEAD + 1}' in text
    assert 'health_status=unknown' not in text
    qn.reset_stream_activity()


def test_summary_response_forwards_stream_health_to_the_aggregator():
    """The live lane's _summary_response now carries the batch's health into the
    aggregator, which is what the periodic summary reads."""
    qn.reset_stream_activity()
    qn._summary_response(
        tx_count=5, targets_loaded=1, matched=0, persisted=0, duplicates=0,
        skipped=5, results=[], health_status='healthy', chain_head_status='known',
        latest_stream_block=CHAIN_HEAD,
    )
    snapshot = qn.stream_activity_snapshot()
    assert snapshot['health_status'] == 'healthy'
    assert snapshot['chain_head_status'] == 'known'
    assert snapshot['latest_stream_block'] == CHAIN_HEAD
    qn.reset_stream_activity()


def test_summary_response_without_health_keeps_last_known_state():
    """A caller with no live-health signal (legacy lane / ignored path) must never
    upgrade — or reset — the live lane's last known health."""
    qn.reset_stream_activity()
    qn._summary_response(
        tx_count=1, targets_loaded=1, matched=0, persisted=0, duplicates=0,
        skipped=1, results=[], health_status='degraded', chain_head_status='known',
        latest_stream_block=CHAIN_HEAD,
    )
    qn._summary_response(
        tx_count=1, targets_loaded=1, matched=0, persisted=0, duplicates=0,
        skipped=1, results=[],
    )
    snapshot = qn.stream_activity_snapshot()
    assert snapshot['health_status'] == 'degraded'
    qn.reset_stream_activity()
