"""Healthy QuickNode Stream blocks refresh MONITORING COVERAGE (matched=0 included).

Production evidence that motivated these tests. The realtime path was proven healthy —

    quicknode_stream_batch stream_lane=live checkpoint_identity=quicknode:base:live
        lag_blocks=0 lag_status=live health_status=healthy matched=0
    quicknode_stream_periodic_summary health_status=healthy chain_head_status=known

— and the runtime status STILL reported, roughly five minutes after each poll:

    monitoring_reporting_systems reporting_systems=0 fresh_live_reporting_systems=0
    monitoring_runtime_evidence_selection chosen_evidence_source=replay
        monitoring_status=limited

Root cause: monitoring coverage had exactly ONE writer — the fallback RPC poll
(monitoring_runner._persist_live_coverage_telemetry), on the canonical 900s cadence.
The runtime freshness window floors at 300s. So coverage expired ~5 minutes after
every poll, and a workspace carried entirely by a healthy Stream fell back to
replay/limited even while the Stream sat at the chain tip evaluating every block
against the monitored wallet. matched=0 wrote nothing at all.

The fix: an accepted healthy near-tip live-lane block, whose monitored target was
loaded and evaluated, refreshes that target's monitoring coverage through the SAME
canonical coverage artifacts the RPC poll writes — throttled, and on collapsed
upsert keys so steady-state row growth is zero.

Coverage evidence stays strictly separate from security evidence: matched=0 creates
no wallet-transfer telemetry, no detection, no alert, and no incident.

Covered here (numbered per the task's required proofs):

  1-2.  A healthy near-tip block refreshes per-target coverage, matched=0 included.
  3-4.  matched=0 creates no security telemetry, detection, or alert.
  5.    A real matched transfer still creates the telemetry/alert chain exactly once.
  6-7.  Fresh Stream coverage yields fresh_live_reporting_systems >= 1 and allows
        chosen_evidence_source=live.
  8.    The 900s fallback RPC cadence never downgrades healthy Stream coverage.
  9-10. A stopped or far-behind Stream expires coverage and downgrades truthfully.
  11-12. Replay-only evidence never becomes live; old security telemetry stays stale.
  13.   GET /ops/monitoring/runtime-status performs no DB writes, and every stage
        reads ONE monitored_systems model.
  14-16. Canary isolation, the 900s cadence, and paused WebSocket/mempool unchanged.

  Plus: write-amplification bounds, the receipts-coverage semantics, and the periodic
  summary, which must not regress.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from services.api.app import monitoring_runner
from services.api.app import quicknode_streams as qn
from services.api.app.domains.threat_detection import config as tdc
from services.api.app.domains.threat_detection.endpoints import _row_freshness
from services.api.app.monitoring_truth import (
    REALTIME_INGESTION_DEGRADED,
    REALTIME_INGESTION_HEALTHY,
    REALTIME_INGESTION_STALE,
    derive_realtime_ingestion_health,
)
from services.api.app.worker_status import (
    QUICKNODE_STREAM_COVERAGE_EVENT_TYPE,
    QUICKNODE_STREAM_COVERAGE_PROVIDER_TYPE,
    QUICKNODE_STREAM_DETECTED_BY,
)

# Reuse the sibling module's runtime-status fake rather than forking a second one:
# it already models the canonical QuickNode live-lane checkpoint plus an independently
# configurable fallback RPC poll, which is exactly the state space under test here.
from services.api.tests.test_quicknode_stream_runtime_health_semantics import (  # noqa: E501
    CHAIN_HEAD,
    NOW,
    TARGET_ID,
    WORKSPACE_ID,
    _Result,
    _RuntimeConn,
    _runtime_payload,
)

WALLET = '0x5f6f35fd8b10c5576089f99c7c8c351deb851d1f'
COUNTERPARTY = '0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'
UNRELATED = '0xbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb'
SECRET = 'whsec_coverage_test_secret'
NONCE = 'coverage-nonce-1'
LIVE_KEY = qn.QUICKNODE_STREAM_KEY_BASE_LIVE

# The runtime freshness window floors at 300s while the fallback RPC poll runs every
# 900s — the exact production mismatch this fix addresses.
RUNTIME_WINDOW_SECONDS = 300


# ---------------------------------------------------------------------------
# Webhook-side fake connection: records the coverage artifacts separately from
# security telemetry, so a test can prove the two never blur.
# ---------------------------------------------------------------------------

class _Rows:
    def __init__(self, rows=None):
        self._rows = list(rows or [])

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)


class _WebhookConn:
    def __init__(self, *, targets=None, existing_telemetry=None):
        self.targets = list(targets or [])
        self.existing_telemetry = existing_telemetry
        self.checkpoints: dict[str, dict] = {}
        self.security_telemetry_inserts: list[tuple] = []
        self.coverage_telemetry_upserts: list[tuple] = []
        self.coverage_receipt_upserts: list[tuple] = []
        self.monitored_system_updates: list[tuple] = []
        self.target_coverage_record_inserts: list[tuple] = []
        self.evidence_inserts: list[tuple] = []
        self.statements: list[str] = []
        self.commit_calls = 0

    def execute(self, query, params=None):
        q = ' '.join(str(query or '').split())
        self.statements.append(q)
        low = q.strip().lower()
        if low.startswith('create table'):
            return _Rows([])
        if 'pg_try_advisory_lock' in low:
            return _Rows([{'acquired': True}])
        if 'from quicknode_stream_checkpoints' in low:
            cp = self.checkpoints.get(params[0])
            return _Rows([cp] if cp else [])
        if low.startswith('insert into quicknode_stream_checkpoints'):
            p = list(params)
            self.checkpoints[p[0]] = {
                'stream_key': p[0], 'latest_stream_block': p[1], 'last_processed_block': p[2],
            }
            return _Rows([])
        if 'from targets' in low:
            return _Rows(self.targets)
        if 'from asset_registry' in low:
            return _Rows([])
        if 'from assets' in low:
            return _Rows([])
        if 'from telemetry_events' in low and low.startswith('select'):
            return _Rows([self.existing_telemetry] if self.existing_telemetry else [])
        if low.startswith('insert into telemetry_events'):
            row = tuple(params or ())
            if len(row) > 5 and row[5] == QUICKNODE_STREAM_COVERAGE_EVENT_TYPE:
                self.coverage_telemetry_upserts.append(row)
            else:
                self.security_telemetry_inserts.append(row)
            return _Rows([])
        if low.startswith('insert into monitoring_event_receipts'):
            self.coverage_receipt_upserts.append(tuple(params or ()))
            return _Rows([])
        if low.startswith('insert into target_coverage_records'):
            self.target_coverage_record_inserts.append(tuple(params or ()))
            return _Rows([])
        if low.startswith('insert into evidence'):
            self.evidence_inserts.append(tuple(params or ()))
            return _Rows([])
        if low.startswith('update monitored_systems'):
            self.monitored_system_updates.append(tuple(params or ()))
            return _Rows([])
        return _Rows([])

    def commit(self):
        self.commit_calls += 1

    def rollback(self):
        return None


class _FakeRpc:
    def __init__(self, head):
        self.head = head
        self.calls: list[str] = []

    def call(self, method, _params=None):
        self.calls.append(method)
        if method == 'eth_blockNumber':
            return hex(self.head) if self.head is not None else None
        return None


@contextmanager
def _mock_pg(conn):
    yield conn


def _make_target(*, wallet: str | None = WALLET, target_id: str | None = None) -> dict:
    return {
        'id': target_id or str(uuid.uuid4()),
        'workspace_id': WORKSPACE_ID,
        'name': 'Treasury Base Wallet',
        'target_type': 'wallet',
        'chain_network': 'base',
        'chain_id': 8453,
        'wallet_address': wallet,
        'contract_identifier': None,
        'asset_id': None,
        'target_metadata': {},
        'monitoring_enabled': True,
        'enabled': True,
        'is_active': True,
        'monitored_system_id': str(uuid.uuid4()),
    }


def _body(*, tx_hash: str, tx_from: str, tx_to: str, block: int) -> bytes:
    return json.dumps({
        'tx_hash': tx_hash, 'from': tx_from, 'to': tx_to,
        'value': '1000000000000000000', 'block_number': block, 'chain_id': 8453,
    }).encode()


def _post_live_block(*, conn, monkeypatch, block, chain_head, tx_hash=None,
                     tx_from=UNRELATED, tx_to=COUNTERPARTY, alert_chain=None):
    """Drive one signed live-lane webhook end to end."""
    monkeypatch.setenv('QUICKNODE_STREAMS_SECRET', SECRET)
    monkeypatch.setenv('REALTIME_STREAMS_ENABLED', 'true')
    body = _body(
        tx_hash=tx_hash or ('0x' + uuid.uuid4().hex + uuid.uuid4().hex),
        tx_from=tx_from, tx_to=tx_to, block=block,
    )
    timestamp = str(int(time.time()))
    signature = hmac.new(
        SECRET.encode(), NONCE.encode() + timestamp.encode() + body, hashlib.sha256,
    ).hexdigest()
    rpc = _FakeRpc(chain_head)
    patches = [
        patch.object(qn, 'pg_connection', lambda: _mock_pg(conn)),
        patch.object(qn, 'ensure_pilot_schema', lambda _c: None),
        patch.object(qn, '_make_base_rpc_client', lambda: rpc),
    ]
    if alert_chain is not None:
        patches.append(patch.object(qn, '_create_wallet_transfer_alert_chain', alert_chain))
    with patches[0], patches[1], patches[2]:
        if alert_chain is not None:
            with patches[3]:
                return qn.process_quicknode_base_stream_webhook(
                    raw_body=body, signature_header=signature, nonce_header=NONCE,
                    timestamp_header=timestamp, lane='live',
                )
        return qn.process_quicknode_base_stream_webhook(
            raw_body=body, signature_header=signature, nonce_header=NONCE,
            timestamp_header=timestamp, lane='live',
        )


@pytest.fixture(autouse=True)
def _isolated_stream_state():
    """The coverage-refresh throttle, chain-head cache, target cache, log sampler and
    runtime-status caches are all process-global. Clear them on both sides of every
    test so write counts and evidence selection stay deterministic."""
    def _reset():
        qn.reset_stream_coverage_refresh_state()
        qn.reset_chain_head_cache()
        qn.reset_base_wallet_target_cache()
        qn.reset_quicknode_log_sampler_state()
        qn.reset_stream_activity()
        monitoring_runner.RUNTIME_STATUS_WORKSPACE_CACHE.clear()
        monitoring_runner.RUNTIME_STATUS_SUMMARY_CACHE.clear()

    _reset()
    yield
    _reset()


# ---------------------------------------------------------------------------
# 1 & 2. A healthy near-tip block refreshes per-target coverage — matched=0 included
# ---------------------------------------------------------------------------

def test_healthy_near_tip_block_refreshes_target_coverage(monkeypatch):
    target = _make_target()
    conn = _WebhookConn(targets=[target])
    result = _post_live_block(
        conn=conn, monkeypatch=monkeypatch, block=CHAIN_HEAD, chain_head=CHAIN_HEAD,
        tx_from=WALLET, tx_to=COUNTERPARTY,
    )
    assert result['matched'] == 1
    assert len(conn.coverage_telemetry_upserts) == 1
    row = conn.coverage_telemetry_upserts[0]
    assert row[3] == target['id']
    assert row[4] == QUICKNODE_STREAM_COVERAGE_PROVIDER_TYPE
    assert row[5] == QUICKNODE_STREAM_COVERAGE_EVENT_TYPE
    assert row[7] == 'live'


def test_matched_zero_still_refreshes_monitoring_coverage(monkeypatch):
    """The core requirement: a block that matched NOTHING still proves the target was
    monitored, so it refreshes coverage."""
    target = _make_target()
    conn = _WebhookConn(targets=[target])
    result = _post_live_block(
        conn=conn, monkeypatch=monkeypatch, block=CHAIN_HEAD, chain_head=CHAIN_HEAD,
        tx_from=UNRELATED, tx_to=COUNTERPARTY,
    )
    assert result['matched'] == 0
    assert result['persisted'] == 0
    assert len(conn.coverage_telemetry_upserts) == 1
    payload = json.loads(conn.coverage_telemetry_upserts[0][9])
    # Self-describing and honest: coverage telemetry, nothing matched.
    assert payload['telemetry_kind'] == 'coverage'
    assert payload['matched'] == 0
    assert payload['block_number'] == CHAIN_HEAD
    assert payload['chain_head'] == CHAIN_HEAD
    assert payload['lag_blocks'] == 0
    # No detected_by: nothing was detected, so nothing may claim a detector.
    assert 'detected_by' not in payload


def test_coverage_refresh_writes_the_canonical_coverage_artifacts(monkeypatch):
    target = _make_target()
    conn = _WebhookConn(targets=[target])
    _post_live_block(conn=conn, monkeypatch=monkeypatch, block=CHAIN_HEAD, chain_head=CHAIN_HEAD)

    # The canonical coverage receipt, in the same shape the RPC coverage path writes.
    assert len(conn.coverage_receipt_upserts) == 1
    # params: id, workspace_id, target_id, event_id, event_cursor, block_number,
    #         ingestion_source, processed_at, receipt_kind, evidence_source, telemetry_kind
    receipt = conn.coverage_receipt_upserts[0]
    assert receipt[8] == 'coverage_telemetry'    # receipt_kind
    assert receipt[9] == 'live'                  # evidence_source
    assert receipt[10] == 'coverage'             # telemetry_kind
    assert receipt[3] == f'coverage:{qn.QUICKNODE_STREAM_SOURCE}:{target["id"]}'

    # monitored_systems coverage columns move; last_event_at deliberately does not.
    assert len(conn.monitored_system_updates) == 1
    update_sql = next(s for s in conn.statements if s.lower().startswith('update monitored_systems'))
    assert 'last_coverage_telemetry_at' in update_sql
    assert 'last_event_at' not in update_sql


def test_coverage_refresh_is_skipped_for_a_target_without_a_monitored_wallet(monkeypatch):
    """Loaded but unevaluable: no wallet to match, so no coverage may be claimed."""
    conn = _WebhookConn(targets=[_make_target(wallet=None)])
    _post_live_block(conn=conn, monkeypatch=monkeypatch, block=CHAIN_HEAD, chain_head=CHAIN_HEAD)
    assert conn.coverage_telemetry_upserts == []
    assert conn.coverage_receipt_upserts == []


# ---------------------------------------------------------------------------
# 3 & 4. matched=0 creates no security telemetry, detection, alert, or incident
# ---------------------------------------------------------------------------

def test_matched_zero_creates_no_security_telemetry(monkeypatch):
    conn = _WebhookConn(targets=[_make_target()])
    _post_live_block(
        conn=conn, monkeypatch=monkeypatch, block=CHAIN_HEAD, chain_head=CHAIN_HEAD,
        tx_from=UNRELATED, tx_to=COUNTERPARTY,
    )
    assert conn.security_telemetry_inserts == []
    inserted_event_types = {row[5] for row in conn.coverage_telemetry_upserts}
    assert inserted_event_types == {QUICKNODE_STREAM_COVERAGE_EVENT_TYPE}
    assert 'wallet_transfer_detected' not in inserted_event_types


def test_coverage_event_type_is_runtime_telemetry_never_security_telemetry():
    """The single place the security boundary is defined must classify the coverage
    row as ingestion/runtime, so the detector scan excludes it at the source and no
    detection or alert can ever be raised from it."""
    assert tdc.is_runtime_event_type(QUICKNODE_STREAM_COVERAGE_EVENT_TYPE) is True
    assert tdc.is_security_event_type(QUICKNODE_STREAM_COVERAGE_EVENT_TYPE) is False
    assert QUICKNODE_STREAM_COVERAGE_EVENT_TYPE in tdc.runtime_event_types()


def test_matched_zero_never_invokes_the_alert_chain(monkeypatch):
    calls: list[dict] = []

    def _record(**kwargs):
        calls.append(kwargs)
        return {'smoke_alert_id': None, 'sig_alert_id': None}

    conn = _WebhookConn(targets=[_make_target()])
    _post_live_block(
        conn=conn, monkeypatch=monkeypatch, block=CHAIN_HEAD, chain_head=CHAIN_HEAD,
        tx_from=UNRELATED, tx_to=COUNTERPARTY, alert_chain=_record,
    )
    assert calls == []
    assert conn.coverage_telemetry_upserts != []


def test_coverage_refresh_writes_no_append_only_history_rows(monkeypatch):
    """Coverage evidence must not fabricate customer-facing evidence rows, and must
    not grow the append-only history tables the 900s poll cycle owns."""
    conn = _WebhookConn(targets=[_make_target()])
    _post_live_block(conn=conn, monkeypatch=monkeypatch, block=CHAIN_HEAD, chain_head=CHAIN_HEAD)
    assert conn.evidence_inserts == []
    assert conn.target_coverage_record_inserts == []


# ---------------------------------------------------------------------------
# 5. A real matched transfer still creates the telemetry + alert chain exactly once
# ---------------------------------------------------------------------------

def test_matched_transfer_still_creates_telemetry_and_alert_chain_exactly_once(monkeypatch):
    calls: list[dict] = []

    def _record(**kwargs):
        calls.append(kwargs)
        return {'smoke_alert_id': 'alert-1', 'sig_alert_id': 'alert-2'}

    target = _make_target()
    conn = _WebhookConn(targets=[target])
    tx_hash = '0x' + '5a' * 32
    result = _post_live_block(
        conn=conn, monkeypatch=monkeypatch, block=CHAIN_HEAD, chain_head=CHAIN_HEAD,
        tx_hash=tx_hash, tx_from=WALLET, tx_to=COUNTERPARTY, alert_chain=_record,
    )
    assert result['matched'] == 1
    assert result['persisted'] == 1
    assert len(calls) == 1
    assert len(conn.security_telemetry_inserts) == 1
    security_row = conn.security_telemetry_inserts[0]
    assert security_row[4] == QUICKNODE_STREAM_DETECTED_BY
    assert security_row[5] == 'wallet_transfer_detected'
    # Coverage rides alongside it, once, and does not duplicate the security row.
    assert len(conn.coverage_telemetry_upserts) == 1

    # A duplicate delivery of the same tx re-runs neither the persist nor the chain.
    conn.existing_telemetry = {'id': str(uuid.uuid4()), 'detected_by': QUICKNODE_STREAM_DETECTED_BY}
    _post_live_block(
        conn=conn, monkeypatch=monkeypatch, block=CHAIN_HEAD, chain_head=CHAIN_HEAD,
        tx_hash=tx_hash, tx_from=WALLET, tx_to=COUNTERPARTY, alert_chain=_record,
    )
    assert len(calls) == 1
    assert len(conn.security_telemetry_inserts) == 1


# ---------------------------------------------------------------------------
# Write amplification: bounded by an upsert model plus a per-target throttle
# ---------------------------------------------------------------------------

def test_coverage_refresh_interval_is_half_the_canonical_stale_window(monkeypatch):
    monkeypatch.delenv('QUICKNODE_STREAM_COVERAGE_REFRESH_SECONDS', raising=False)
    monkeypatch.delenv('QUICKNODE_LIVE_STALE_SECONDS', raising=False)
    assert qn.live_stale_seconds() == 300
    assert qn.stream_coverage_refresh_seconds() == 150.0
    # Strictly inside the runtime freshness window, so coverage cannot expire between
    # two successful refreshes.
    assert qn.stream_coverage_refresh_seconds() < RUNTIME_WINDOW_SECONDS


def test_coverage_refresh_interval_cannot_be_configured_to_let_coverage_expire(monkeypatch):
    monkeypatch.delenv('QUICKNODE_LIVE_STALE_SECONDS', raising=False)
    monkeypatch.setenv('QUICKNODE_STREAM_COVERAGE_REFRESH_SECONDS', '86400')
    assert qn.stream_coverage_refresh_seconds() == 150.0
    monkeypatch.setenv('QUICKNODE_STREAM_COVERAGE_REFRESH_SECONDS', 'not-a-number')
    assert qn.stream_coverage_refresh_seconds() == 150.0


def test_consecutive_blocks_do_not_write_coverage_per_block(monkeypatch):
    """Base produces a block every ~2s. Only the first block in a refresh window may
    write; the rest are throttled."""
    target = _make_target()
    conn = _WebhookConn(targets=[target])
    for offset in range(5):
        _post_live_block(
            conn=conn, monkeypatch=monkeypatch,
            block=CHAIN_HEAD + offset, chain_head=CHAIN_HEAD + offset,
        )
    assert len(conn.coverage_telemetry_upserts) == 1
    assert len(conn.coverage_receipt_upserts) == 1
    assert len(conn.monitored_system_updates) == 1


def test_coverage_refresh_resumes_after_the_throttle_window_elapses(monkeypatch):
    target = _make_target()
    conn = _WebhookConn(targets=[target])
    _post_live_block(conn=conn, monkeypatch=monkeypatch, block=CHAIN_HEAD, chain_head=CHAIN_HEAD)
    assert len(conn.coverage_telemetry_upserts) == 1
    # Simulate the refresh interval elapsing.
    qn._mark_stream_coverage_refreshed(
        target['id'], now_mono=time.monotonic() - qn.stream_coverage_refresh_seconds() - 1,
    )
    _post_live_block(conn=conn, monkeypatch=monkeypatch, block=CHAIN_HEAD + 1, chain_head=CHAIN_HEAD + 1)
    assert len(conn.coverage_telemetry_upserts) == 2


def test_coverage_writes_are_collapsed_upserts_so_rows_do_not_grow(monkeypatch):
    """Both coverage writes target a single collapsed key per target, so repeated
    refreshes UPDATE one row instead of appending history."""
    target = _make_target()
    conn = _WebhookConn(targets=[target])
    _post_live_block(conn=conn, monkeypatch=monkeypatch, block=CHAIN_HEAD, chain_head=CHAIN_HEAD)
    telemetry_sql = next(
        s for s in conn.statements if s.lower().startswith('insert into telemetry_events')
    )
    assert 'ON CONFLICT (workspace_id, target_id, idempotency_key)' in telemetry_sql
    assert 'DO UPDATE SET' in telemetry_sql
    assert conn.coverage_telemetry_upserts[0][10] == f'{WORKSPACE_ID}:{target["id"]}:stream_coverage'
    receipt_sql = next(
        s for s in conn.statements if s.lower().startswith('insert into monitoring_event_receipts')
    )
    assert 'ON CONFLICT (target_id, event_id)' in receipt_sql
    assert 'DO UPDATE SET' in receipt_sql


def test_a_failed_coverage_write_does_not_start_the_throttle_window(monkeypatch):
    """A failure must retry on the next block, not be silently throttled out for a
    full window — and must never fail the webhook."""
    target = _make_target()

    class _FailingConn(_WebhookConn):
        def __init__(self, **kw):
            super().__init__(**kw)
            self.fail_coverage = True

        def execute(self, query, params=None):
            low = ' '.join(str(query or '').split()).strip().lower()
            if self.fail_coverage and low.startswith('insert into telemetry_events'):
                row = tuple(params or ())
                if len(row) > 5 and row[5] == QUICKNODE_STREAM_COVERAGE_EVENT_TYPE:
                    raise RuntimeError('coverage write failed')
            return super().execute(query, params)

    conn = _FailingConn(targets=[target])
    result = _post_live_block(conn=conn, monkeypatch=monkeypatch, block=CHAIN_HEAD, chain_head=CHAIN_HEAD)
    assert result['ok'] is True                      # the webhook still returns 200
    assert conn.coverage_telemetry_upserts == []
    conn.fail_coverage = False
    _post_live_block(conn=conn, monkeypatch=monkeypatch, block=CHAIN_HEAD + 1, chain_head=CHAIN_HEAD + 1)
    assert len(conn.coverage_telemetry_upserts) == 1


# ---------------------------------------------------------------------------
# 10 (write side). An unhealthy lane writes NO coverage
# ---------------------------------------------------------------------------

def test_far_behind_lane_writes_no_coverage(monkeypatch):
    """Lag beyond the threshold means the Stream is not at the tip: it is not proof
    of current monitoring, so coverage must not be refreshed."""
    conn = _WebhookConn(targets=[_make_target()])
    behind = CHAIN_HEAD - (qn.live_lag_threshold_blocks() + 50)
    _post_live_block(conn=conn, monkeypatch=monkeypatch, block=behind, chain_head=CHAIN_HEAD)
    assert conn.coverage_telemetry_upserts == []


def test_unknown_chain_head_writes_no_coverage(monkeypatch):
    """An unknown head means lag is unknown, so the lane is not provably at the tip."""
    conn = _WebhookConn(targets=[_make_target()])
    _post_live_block(conn=conn, monkeypatch=monkeypatch, block=CHAIN_HEAD, chain_head=None)
    assert conn.coverage_telemetry_upserts == []


def test_backfill_lane_never_writes_live_coverage(monkeypatch):
    """Historical backfill is not proof of live monitoring."""
    monkeypatch.setenv('QUICKNODE_STREAMS_SECRET', SECRET)
    monkeypatch.setenv('REALTIME_STREAMS_ENABLED', 'true')
    conn = _WebhookConn(targets=[_make_target()])
    body = _body(tx_hash='0x' + 'b1' * 32, tx_from=UNRELATED, tx_to=COUNTERPARTY, block=1000)
    timestamp = str(int(time.time()))
    signature = hmac.new(
        SECRET.encode(), NONCE.encode() + timestamp.encode() + body, hashlib.sha256,
    ).hexdigest()
    with patch.object(qn, 'pg_connection', lambda: _mock_pg(conn)), \
            patch.object(qn, 'ensure_pilot_schema', lambda _c: None), \
            patch.object(qn, '_make_base_rpc_client', lambda: _FakeRpc(CHAIN_HEAD)):
        qn.process_quicknode_base_stream_webhook(
            raw_body=body, signature_header=signature, nonce_header=NONCE,
            timestamp_header=timestamp, lane='backfill',
        )
    assert conn.coverage_telemetry_upserts == []


def test_refresh_helper_is_fail_closed_on_every_precondition():
    calls: list[str] = []

    class _SpyConn:
        def execute(self, *_a, **_k):
            calls.append('execute')
            return _Rows([])

        def commit(self):
            calls.append('commit')

    for kwargs in (
        {'health_status': 'degraded', 'block_number': CHAIN_HEAD},
        {'health_status': 'unknown', 'block_number': CHAIN_HEAD},
        {'health_status': None, 'block_number': CHAIN_HEAD},
        {'health_status': qn.STREAM_HEALTH_HEALTHY, 'block_number': None},
    ):
        stats = qn.refresh_live_stream_target_coverage(
            _SpyConn(), targets=[_make_target()], chain_head=CHAIN_HEAD, lag_blocks=0,
            evaluated_tx_count=3, observed_at=datetime.now(timezone.utc), **kwargs,
        )
        assert stats['refreshed'] == 0
    assert calls == []


# ---------------------------------------------------------------------------
# Runtime-status side: a window-aware fake so the freshness cutoff is honoured
# ---------------------------------------------------------------------------

class _CoverageRuntimeConn(_RuntimeConn):
    """``_RuntimeConn`` plus the two facts this fix turns on:

      * a QuickNode Stream COVERAGE timestamp (its own provider_type/event_type), and
      * the canonical reporting-systems query actually applying its freshness cutoff,
        so an expired 900s poll no longer counts as a fresh reporting system.

    ``monitoring_interval_seconds=60`` reproduces the production window
    (telemetry_window_seconds=300) that the 900s poll cadence falls outside of.
    """

    def __init__(self, *, stream_coverage_age_seconds=None, monitoring_interval_seconds=60, **kwargs):
        super().__init__(**kwargs)
        self.stream_coverage_at = (
            None if stream_coverage_age_seconds is None
            else NOW - timedelta(seconds=stream_coverage_age_seconds)
        )
        self.monitoring_interval_seconds = monitoring_interval_seconds
        self.write_statements: list[str] = []

    def execute(self, q, p=None):
        text = ' '.join(str(q).split())
        if text.strip().lower().split(' ')[0] in {'insert', 'update', 'delete'}:
            self.write_statements.append(text)
        if 'FROM monitored_systems' in text:
            rows = super().execute(q, p).fetchall()
            for row in rows:
                row['monitoring_interval_seconds'] = self.monitoring_interval_seconds
                row['last_coverage_telemetry_at'] = self.target_coverage_at
            return _Result(rows=rows)
        # The stream COVERAGE read (provider_type + event_type), distinct from the
        # stream SECURITY telemetry read (provider_type only).
        if (
            'FROM telemetry_events' in text
            and 'MAX(observed_at) AS ts' in text
            and 'event_type = %s' in text
        ):
            return _Result(row={'ts': self.stream_coverage_at})
        if 'SELECT DISTINCT te.target_id' in text and 'FROM telemetry_events te' in text:
            cutoff = (p or (None, None))[1]
            fresh = [
                ts for ts in (self.stream_telemetry_at, self.rpc_ts, self.stream_coverage_at)
                if ts is not None and (cutoff is None or ts >= cutoff)
            ]
            return _Result(rows=[{'target_id': TARGET_ID}] if fresh else [])
        return super().execute(q, p)


def _quiet_stream_conn(**overrides):
    """The production shape: healthy Stream at the tip, matched=0 for longer than the
    runtime window, and the 900s fallback poll outside that window."""
    kwargs = {
        'stream_checkpoint_age_seconds': 2,
        'stream_lag_blocks': 0,
        'stream_telemetry_age_seconds': None,
        'rpc_poll_age_seconds': 800,
        'target_coverage_age_seconds': 800,
        'stream_coverage_age_seconds': 40,
    }
    kwargs.update(overrides)
    return _CoverageRuntimeConn(**kwargs)


# ---------------------------------------------------------------------------
# 6 & 7. Fresh Stream coverage → fresh_live_reporting_systems >= 1 and live evidence
# ---------------------------------------------------------------------------

def test_the_production_downgrade_is_fixed(monkeypatch):
    """The exact reported failure: healthy Stream, matched=0, 900s poll expired."""
    summary = _runtime_payload(monkeypatch, _quiet_stream_conn())
    assert summary['realtime_ingestion']['healthy'] is True
    assert summary['reporting_systems'] >= 1
    assert summary['fresh_live_reporting_systems'] >= 1
    assert summary['evidence_source'] == 'live'
    assert summary['source_of_evidence'] == 'live'
    assert summary['replay_only_systems'] == 0
    assert summary['reporting_systems_status_reason'].startswith('fresh_coverage_window_')


def test_stream_coverage_is_named_as_coverage_not_as_a_detected_event(monkeypatch):
    """Live evidence carried by coverage must say so: it proves monitoring, never
    that an on-chain event arrived."""
    realtime = _runtime_payload(monkeypatch, _quiet_stream_conn())['realtime_ingestion']
    assert realtime['live_evidence_fresh'] is True
    assert realtime['live_coverage_fresh'] is True
    assert realtime['live_security_telemetry_fresh'] is False
    assert realtime['live_evidence_kind'] == 'coverage'
    assert realtime['last_live_telemetry_at'] is None
    assert realtime['reason'] == 'stream_near_chain_tip_with_fresh_coverage'


def test_matched_transfer_evidence_is_named_as_security_telemetry(monkeypatch):
    realtime = _runtime_payload(
        monkeypatch, _quiet_stream_conn(stream_telemetry_age_seconds=30),
    )['realtime_ingestion']
    assert realtime['live_security_telemetry_fresh'] is True
    assert realtime['live_evidence_kind'] == 'security_telemetry'


# ---------------------------------------------------------------------------
# 8. The 900s fallback RPC cadence never downgrades healthy Stream coverage
# ---------------------------------------------------------------------------

@pytest.mark.parametrize('rpc_poll_age_seconds', [300, 900, 1800, 3600, None])
def test_fallback_rpc_cadence_never_downgrades_stream_coverage(monkeypatch, rpc_poll_age_seconds):
    summary = _runtime_payload(monkeypatch, _quiet_stream_conn(
        rpc_poll_age_seconds=rpc_poll_age_seconds,
        target_coverage_age_seconds=rpc_poll_age_seconds,
    ))
    assert summary['realtime_ingestion']['healthy'] is True, rpc_poll_age_seconds
    assert summary['evidence_source'] == 'live', rpc_poll_age_seconds
    assert summary['fresh_live_reporting_systems'] >= 1, rpc_poll_age_seconds
    assert summary['provider_degraded_flag'] is False, rpc_poll_age_seconds


def test_stable_polling_interval_floor_is_untouched(monkeypatch):
    """The fix must not have moved the fallback cadence to make the UI green."""
    for var in ('EVM_POLLING_INTERVAL_SECONDS', 'MONITORING_WORKER_INTERVAL_SECONDS'):
        monkeypatch.delenv(var, raising=False)
    assert monitoring_runner.canonical_polling_interval_seconds() == 900


# ---------------------------------------------------------------------------
# 9 & 10. Coverage expires when the Stream stops or falls behind
# ---------------------------------------------------------------------------

def test_stopped_stream_expires_coverage_and_downgrades(monkeypatch):
    """No new blocks: the checkpoint goes stale, coverage ages out of the window, and
    the runtime downgrades truthfully instead of holding a stale green."""
    stale_age = monitoring_runner.QUICKNODE_STREAM_STALE_SECONDS + 60
    summary = _runtime_payload(monkeypatch, _quiet_stream_conn(
        stream_checkpoint_age_seconds=stale_age,
        stream_coverage_age_seconds=stale_age,
        rpc_poll_age_seconds=None,
        target_coverage_age_seconds=None,
    ))
    realtime = summary['realtime_ingestion']
    assert realtime['status'] == REALTIME_INGESTION_STALE
    assert realtime['healthy'] is False
    assert realtime['live_coverage_fresh'] is False
    assert realtime['live_evidence_fresh'] is False
    assert summary['evidence_source'] != 'live'
    assert summary['fresh_live_reporting_systems'] == 0


def test_far_behind_stream_degrades_truthfully(monkeypatch):
    """Fresh coverage rows behind a far-behind lane are historical, not proof of
    current monitoring."""
    summary = _runtime_payload(monkeypatch, _quiet_stream_conn(
        stream_lag_blocks=5000, stream_coverage_age_seconds=10,
    ))
    realtime = summary['realtime_ingestion']
    assert realtime['status'] == REALTIME_INGESTION_DEGRADED
    assert realtime['healthy'] is False
    assert realtime['live_coverage_fresh'] is False
    assert realtime['live_evidence_kind'] == 'none'


def test_coverage_older_than_the_window_is_not_fresh_evidence():
    result = derive_realtime_ingestion_health(
        streams_enabled=True, lane_state='live', lag_blocks=0, checkpoint_age_seconds=2,
        live_coverage_age_seconds=RUNTIME_WINDOW_SECONDS + 1,
        checkpoint_stale_seconds=300, telemetry_window_seconds=RUNTIME_WINDOW_SECONDS,
    )
    assert result.status == REALTIME_INGESTION_HEALTHY   # the PATH still delivers
    assert result.live_coverage_fresh is False           # but its coverage aged out
    assert result.live_evidence_fresh is False
    assert result.live_evidence_kind == 'none'


# ---------------------------------------------------------------------------
# 11 & 12. Replay-only evidence never becomes live; old security rows stay stale
# ---------------------------------------------------------------------------

def test_replay_only_evidence_never_becomes_live(monkeypatch):
    """No Stream checkpoint and no Stream coverage: only historical rows remain."""
    summary = _runtime_payload(monkeypatch, _quiet_stream_conn(
        stream_checkpoint_age_seconds=None,
        stream_coverage_age_seconds=None,
        stream_telemetry_age_seconds=None,
        rpc_poll_age_seconds=None,
        target_coverage_age_seconds=None,
    ))
    assert summary['evidence_source'] != 'live'
    assert summary['source_of_evidence'] != 'live'
    assert summary['realtime_ingestion']['live_evidence_fresh'] is False


def test_simulator_ingestion_mode_is_never_upgraded_by_stream_coverage(monkeypatch):
    summary = _runtime_payload(
        monkeypatch, _quiet_stream_conn(),
        health={
            'worker_running': True, 'source_type': 'polling', 'ingestion_mode': 'simulator',
            'last_heartbeat_at': NOW - timedelta(seconds=60),
            'last_cycle_at': NOW - timedelta(seconds=60),
        },
    )
    assert summary['evidence_source'] == 'simulator'
    assert summary['source_of_evidence'] == 'simulator'


def test_old_security_telemetry_stays_stale_while_coverage_is_fresh():
    """Per-row freshness is time-based. A fresh coverage refresh must never make an
    old wallet-transfer row read as fresh."""
    old_row_at = NOW - timedelta(seconds=RUNTIME_WINDOW_SECONDS * 10)
    assert _row_freshness(old_row_at, NOW, RUNTIME_WINDOW_SECONDS) == 'stale'
    assert _row_freshness(NOW - timedelta(seconds=5), NOW, RUNTIME_WINDOW_SECONDS) == 'fresh'


def test_coverage_only_workspace_reports_no_real_events(monkeypatch):
    """Coverage proves monitoring, never that something happened: the workspace must
    not be presented as having recent real security evidence."""
    summary = _runtime_payload(monkeypatch, _quiet_stream_conn())
    assert int(summary.get('real_event_count') or 0) == 0
    assert int(summary.get('recent_real_event_count') or 0) == 0


# ---------------------------------------------------------------------------
# Task 4. Receipts coverage semantics
# ---------------------------------------------------------------------------

def test_stream_coverage_receipt_matches_the_rpc_coverage_receipt_shape(monkeypatch):
    """``receipts_reporting_systems`` counts monitored systems with a fresh LIVE
    coverage receipt in monitoring_event_receipts (Decoda's processing-receipt ledger
    — unrelated to EVM transaction receipts). A stream coverage refresh is a genuine
    coverage receipt of exactly that kind, so it counts under the existing semantics
    with no query change."""
    conn = _WebhookConn(targets=[_make_target()])
    _post_live_block(conn=conn, monkeypatch=monkeypatch, block=CHAIN_HEAD, chain_head=CHAIN_HEAD)
    receipt = conn.coverage_receipt_upserts[0]
    receipt_kind, evidence_source, telemetry_kind = receipt[8], receipt[9], receipt[10]
    assert (receipt_kind, evidence_source, telemetry_kind) == ('coverage_telemetry', 'live', 'coverage')
    # ingestion_source must not fall into the excluded demo/simulator/replay family.
    assert receipt[6] == qn.QUICKNODE_STREAM_SOURCE
    assert receipt[6] not in {'demo', 'simulator', 'replay', 'synthetic', 'fallback'}


# ---------------------------------------------------------------------------
# 13. Runtime-status GET stays read-only and reads ONE monitored_systems model
# ---------------------------------------------------------------------------

def test_runtime_status_get_performs_no_database_writes(monkeypatch):
    conn = _quiet_stream_conn()
    reconcile_calls: list[object] = []
    monkeypatch.setattr(
        monitoring_runner, 'reconcile_enabled_targets_monitored_systems',
        lambda *a, **k: reconcile_calls.append(a) or {},
    )
    _runtime_payload(monkeypatch, conn)
    assert reconcile_calls == []
    assert conn.write_statements == []


def test_every_runtime_stage_reads_one_monitored_systems_model(monkeypatch, caplog):
    """Production logs disagreed across stages of the SAME request:
    healthy_enabled_targets=1 with enabled_monitored_rows_before=0, then
    enabled_monitored_systems=1 / enabled_rows=1. The rows are now loaded once,
    before any stage counts them."""
    with caplog.at_level('INFO', logger=monitoring_runner.logger.name):
        _runtime_payload(monkeypatch, _quiet_stream_conn())
    text = '\n'.join(record.getMessage() for record in caplog.records)
    assert 'monitoring_runtime_status_data_path' in text
    assert 'enabled_monitored_rows_before=0' not in text
    assert 'monitoring_runtime_status_reconcile_skipped_read_only' not in text


def test_request_less_runtime_status_also_loads_rows_before_counting(monkeypatch):
    """The request-less (worker / internal) path used to load monitored_systems ~700
    lines after the counts were taken; both callers now share one read."""
    conn = _quiet_stream_conn()
    monkeypatch.setattr(monitoring_runner, 'ensure_pilot_schema', lambda _c: None)
    monkeypatch.setattr(
        monitoring_runner, 'ensure_monitoring_runtime_schema_capabilities', lambda *a, **k: None,
    )
    monkeypatch.setattr(monitoring_runner, 'pg_connection', lambda: conn)
    monkeypatch.setattr(monitoring_runner, 'live_mode_enabled', lambda: True)
    monkeypatch.setattr(monitoring_runner, 'get_monitoring_health', lambda: {
        'worker_running': True, 'source_type': 'polling', 'ingestion_mode': 'live',
        'last_heartbeat_at': NOW - timedelta(seconds=60),
        'last_cycle_at': NOW - timedelta(seconds=800),
    })
    monitoring_runner.RUNTIME_STATUS_WORKSPACE_CACHE.clear()
    monitoring_runner.RUNTIME_STATUS_SUMMARY_CACHE.clear()
    payload = monitoring_runner.monitoring_runtime_status()
    assert payload['enabled_monitored_systems'] == 1
    assert conn.write_statements == []


# ---------------------------------------------------------------------------
# 14-16. Preserved production posture
# ---------------------------------------------------------------------------

def test_canary_allowlist_semantics_unchanged(monkeypatch):
    from services.api.app.monitoring_canary import resolve_canary_config

    monkeypatch.setenv('MONITORING_CANARY_ENABLED', 'true')
    monkeypatch.setenv('MONITORING_CANARY_TARGET_ALLOWLIST', TARGET_ID)
    config = resolve_canary_config()
    assert config.enabled is True
    assert config.allowed_target_ids == frozenset({TARGET_ID})
    assert config.allowed_target_count == 1


def test_websocket_and_mempool_remain_disabled(monkeypatch):
    from services.api.app.monitoring_runtime_mode import resolve_monitoring_runtime_mode

    for var in ('BASE_REALTIME_ENABLED', 'MEMPOOL_MONITORING_ENABLED'):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv('REALTIME_STREAMS_ENABLED', 'true')
    mode = resolve_monitoring_runtime_mode()
    assert mode.realtime_streams_enabled is True
    assert mode.websocket_enabled is False
    assert mode.mempool_enabled is False
    assert mode.scheduled_polling_enabled is True


def test_polling_only_mode_still_ignores_stream_blocks_without_writing_coverage(monkeypatch):
    """With REALTIME_STREAMS_ENABLED unset the webhook is safely ignored — the
    coverage refresh must not resurrect processing behind that switch."""
    monkeypatch.setenv('QUICKNODE_STREAMS_SECRET', SECRET)
    monkeypatch.delenv('REALTIME_STREAMS_ENABLED', raising=False)
    conn = _WebhookConn(targets=[_make_target()])
    body = _body(tx_hash='0x' + 'c1' * 32, tx_from=WALLET, tx_to=COUNTERPARTY, block=CHAIN_HEAD)
    timestamp = str(int(time.time()))
    signature = hmac.new(
        SECRET.encode(), NONCE.encode() + timestamp.encode() + body, hashlib.sha256,
    ).hexdigest()
    with patch.object(qn, 'pg_connection', lambda: _mock_pg(conn)), \
            patch.object(qn, 'ensure_pilot_schema', lambda _c: None), \
            patch.object(qn, '_make_base_rpc_client', lambda: _FakeRpc(CHAIN_HEAD)):
        result = qn.process_quicknode_base_stream_webhook(
            raw_body=body, signature_header=signature, nonce_header=NONCE,
            timestamp_header=timestamp, lane='live',
        )
    assert result['ok'] is True
    assert conn.coverage_telemetry_upserts == []
    assert conn.security_telemetry_inserts == []


# ---------------------------------------------------------------------------
# Task 6. The periodic Stream summary must not regress
# ---------------------------------------------------------------------------

def test_periodic_summary_still_reports_healthy_and_known_head(monkeypatch, caplog):
    target = _make_target()
    conn = _WebhookConn(targets=[target])
    _post_live_block(conn=conn, monkeypatch=monkeypatch, block=CHAIN_HEAD, chain_head=CHAIN_HEAD)
    snapshot = qn.stream_activity_snapshot()
    assert snapshot['health_status'] == 'healthy'
    assert snapshot['chain_head_status'] == 'known'
    assert snapshot['latest_stream_block'] == CHAIN_HEAD


def test_batch_and_coverage_logs_prove_the_refresh_from_railway_logs(monkeypatch, caplog):
    conn = _WebhookConn(targets=[_make_target()])
    with caplog.at_level('INFO', logger=qn.logger.name):
        _post_live_block(conn=conn, monkeypatch=monkeypatch, block=CHAIN_HEAD, chain_head=CHAIN_HEAD)
    text = caplog.text
    assert 'event=quicknode_stream_batch' in text
    assert 'health_status=healthy' in text
    assert 'event=quicknode_stream_coverage_refresh' in text
    assert 'coverage_refreshed=1' in text
    assert f'checkpoint_identity={LIVE_KEY}' in text
