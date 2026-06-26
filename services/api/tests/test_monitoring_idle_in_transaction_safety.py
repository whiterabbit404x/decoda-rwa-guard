"""
Regression tests for the monitoring worker idle-in-transaction timeout that crashed
the cycle *after* a successful Base wallet-transfer detection/alert.

Root cause: the whole cycle ran inside one DB transaction (the implicit transaction
held open by ``with pg_connection() as connection:``), so the slow RPC scan and
threat-engine calls happened while the worker connection sat idle-in-transaction.
Postgres terminated the connection; ``_persist_live_coverage_telemetry`` then failed
with ``the connection is closed`` and the error handler re-used the same dead
connection, cascading into ``error_handler_failed`` and a crashed cycle.

These tests pin the fix:

1. The RPC scan (``fetch_target_activity_result``) and ``process_monitoring_target``
   run with NO open DB transaction (autocommit + poll record committed first).
2. A detected wallet transfer commits its telemetry/alert BEFORE coverage telemetry.
3. A connection lost during coverage telemetry is retried once on a fresh connection.
4. A coverage write failure is isolated — it never crashes the target poll/cycle.
5. The per-target error handler never re-uses a dead connection (fresh connection),
   and a fresh-connection failure is logged once without crashing the cycle.
6. Duplicate retries do not create duplicate alerts (dedupe signature is reused).
7. The cursor is not advanced when the connection dies while persisting target state.
"""
from __future__ import annotations

import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import psycopg
import pytest

from services.api.app import monitoring_runner
from services.api.app.activity_providers import ActivityProviderResult


WALLET_ADDR = '0xdeadbeef00000000000000000000000000001234'
OTHER_ADDR = '0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'
TX_HASH = '0xabcdef1234567890abcdef1234567890abcdef1234567890abcdef1234567890ab'
BLOCK_NUM = 20_000_000
BASE_CHAIN_ID = 8453


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class _Rows:
    def __init__(self, rows=None):
        self._rows = list(rows or [])
        self.rowcount = len(self._rows)

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)


def _wallet_target(*, target_id=None, workspace_id=None, asset_id=None):
    return {
        'id': target_id or str(uuid.uuid4()),
        'workspace_id': workspace_id or str(uuid.uuid4()),
        'asset_id': asset_id or str(uuid.uuid4()),
        'monitored_system_id': str(uuid.uuid4()),
        'chain_network': 'base',
        'contract_identifier': None,
        'wallet_address': WALLET_ADDR,
        'name': 'Test Base Wallet',
        'target_type': 'wallet',
        'monitoring_checkpoint_cursor': None,
        'watcher_last_observed_block': 0,
        'severity_threshold': 'medium',
        'auto_create_alerts': True,
    }


def _wallet_event(*, target_id: str) -> monitoring_runner.ActivityEvent:
    payload = {
        'tx_hash': TX_HASH,
        'hash': TX_HASH,
        'from': WALLET_ADDR,
        'to': OTHER_ADDR,
        'value': hex(10 ** 17),
        'amount': str(10 ** 17),
        'block_number': BLOCK_NUM,
        'chain_id': BASE_CHAIN_ID,
        'event_type': 'transaction',
        'source_type': 'rpc_polling',
        'wallet_transfer_direction': 'outbound',
        'observed_at': _utcnow().isoformat(),
    }
    return monitoring_runner.ActivityEvent(
        event_id='evt-1',
        kind='transaction',
        observed_at=_utcnow(),
        ingestion_source='polling',
        cursor=f'{BLOCK_NUM}:{TX_HASH}:-1',
        payload=payload,
    )


def _live_provider_result(events) -> ActivityProviderResult:
    return ActivityProviderResult(
        mode='live',
        status='live',
        evidence_state='REAL_EVIDENCE',
        truthfulness_state='NOT_CLAIM_SAFE',
        synthetic=False,
        provider_name='evm_activity_provider',
        provider_kind='rpc',
        evidence_present=True,
        recent_real_event_count=len(events),
        last_real_event_at=_utcnow(),
        events=list(events),
        latest_block=BLOCK_NUM,
        checkpoint=f'{BLOCK_NUM}:{TX_HASH}:-1',
        checkpoint_age_seconds=5,
        degraded_reason=None,
        error_code=None,
        source_type='rpc_polling',
        reason_code=None,
        claim_safe=False,
        detection_outcome='NO_CONFIRMED_ANOMALY_FROM_REAL_EVIDENCE',
    )


class _DepthConn:
    """Fake psycopg connection that tracks open-transaction depth.

    ``transaction()`` increments depth on enter / decrements on exit so a test can
    assert that slow work (the RPC scan) ran at depth 0. ``error_on`` raises a chosen
    exception the first time a query containing the given substring is executed.
    """

    def __init__(self, *, error_on: str | None = None, error: Exception | None = None):
        self.txn_depth = 0
        self.max_depth = 0
        self.executed: list[str] = []
        self.autocommit = False
        self._error_on = (error_on or '').lower() or None
        self._error = error
        self._error_fired = False

    def execute(self, query, params=None):
        q = ' '.join(str(query).split())
        ql = q.lower()
        self.executed.append(q)
        if self._error_on and not self._error_fired and self._error_on in ql:
            self._error_fired = True
            raise self._error
        if ql.startswith('select') and 'from workspaces' in ql:
            return _Rows([{'id': 'ws', 'name': 'WS'}])
        if 'asset_registry' in ql:
            return _Rows([{'id': str(uuid.uuid4())}])
        if ql.startswith('select 1 from targets'):
            return _Rows([{'exists': 1}])
        if ql.startswith('select') and 'count(' in ql:
            return _Rows([{'c': 0, 'ts': None}])
        if ql.startswith('select'):
            return _Rows([])
        return _Rows([])

    @contextmanager
    def transaction(self):
        self.txn_depth += 1
        self.max_depth = max(self.max_depth, self.txn_depth)
        try:
            yield
        finally:
            self.txn_depth -= 1

    def commit(self):
        return None


def _patch_alert_helpers(stack_order: list[str] | None = None):
    """Patch the dedicated-connection helpers so process_monitoring_target does not
    open real pg_connection() sockets. Optionally record call order."""
    def _record(name, ret):
        def _fn(*_a, **_k):
            if stack_order is not None:
                stack_order.append(name)
            return ret
        return _fn

    return (
        patch.object(monitoring_runner, '_persist_raw_wallet_transfer_telemetry', _record('raw_telemetry', True)),
        patch.object(monitoring_runner, '_wallet_transfer_smoke_alert', _record('smoke_alert', str(uuid.uuid4()))),
        patch.object(monitoring_runner, '_strategic_infrastructure_guard_alert', _record('sig_alert', str(uuid.uuid4()))),
    )


# ---------------------------------------------------------------------------
# 1. RPC scan must run OUTSIDE any open DB transaction
# ---------------------------------------------------------------------------

def test_rpc_scan_runs_outside_open_db_transaction():
    """fetch_target_activity_result (the slow RPC scan) must execute with NO open
    transaction so the worker connection is never idle-in-transaction during it."""
    target = _wallet_target()
    conn = _DepthConn()
    depth_at_scan: list[int] = []

    def _capture_scan(_target, _checkpoint):
        depth_at_scan.append(conn.txn_depth)
        # Return no events: keep the rest of the function trivial for this assertion.
        return _live_provider_result([])

    with (
        patch.object(monitoring_runner, 'fetch_target_activity_result', _capture_scan),
        patch.object(monitoring_runner, '_load_checkpoint', return_value=0),
        patch.object(monitoring_runner, '_resolve_coverage_asset_id', return_value=None),
    ):
        monitoring_runner.process_monitoring_target(conn, target)

    assert depth_at_scan == [0], (
        f'RPC scan must run at transaction depth 0 (autocommit), got {depth_at_scan}'
    )


def test_run_cycle_calls_process_target_outside_transaction(monkeypatch):
    """run_monitoring_cycle must commit the poll record then call
    process_monitoring_target with NO open transaction (the call-site restructure)."""
    now = datetime.now(timezone.utc)
    candidate = {
        'id': 'tgt-1',
        'name': 'Target 1',
        'target_type': 'contract',
        'workspace_id': 'ws-1',
        'asset_id': None,
        'monitoring_enabled': True,
        'enabled': True,
        'is_active': True,
        'last_checked_at': None,
        'monitoring_interval_seconds': 300,
        'monitoring_dead_lettered_at': None,
        'chain_network': 'base',
        'created_at': now,
    }
    conn = _CycleConn([candidate])
    depth_at_process: list[int] = []

    def _process(_connection, target, monitoring_run_id=None, triggered_by_user_id=None):
        depth_at_process.append(conn.txn_depth)
        return _process_result(target)

    _install_cycle_patches(monkeypatch, conn, _process)
    monitoring_runner.run_monitoring_cycle(worker_name='test-worker', limit=10)

    assert depth_at_process == [0], (
        f'process_monitoring_target (and its RPC scan) must be called at txn depth 0, '
        f'got {depth_at_process}'
    )


# ---------------------------------------------------------------------------
# 2. Wallet-transfer detection commits BEFORE coverage telemetry
# ---------------------------------------------------------------------------

def test_wallet_transfer_detection_commits_before_coverage_telemetry():
    target = _wallet_target()
    event = _wallet_event(target_id=str(target['id']))
    order: list[str] = []
    raw_patch, smoke_patch, sig_patch = _patch_alert_helpers(order)

    def _coverage(_conn, *, target, provider_result, observed_at):
        order.append('coverage')

    _process_stub = MagicMock(return_value={
        'analysis_run_id': str(uuid.uuid4()),
        'monitoring_state': 'real_event_no_anomaly',
        'alert_id': None,
        'incident_id': None,
        'detection_id': None,
        'protected_asset_coverage_record': None,
    })

    with (
        patch.object(monitoring_runner, 'fetch_target_activity_result', return_value=_live_provider_result([event])),
        patch.object(monitoring_runner, '_load_checkpoint', return_value=0),
        patch.object(monitoring_runner, '_resolve_coverage_asset_id', return_value=None),
        patch.object(monitoring_runner, '_process_single_event', _process_stub),
        patch.object(monitoring_runner, '_persist_live_coverage_telemetry', _coverage),
        raw_patch, smoke_patch, sig_patch,
    ):
        monitoring_runner.process_monitoring_target(_DepthConn(), target)

    assert 'raw_telemetry' in order and 'coverage' in order
    assert order.index('raw_telemetry') < order.index('coverage'), (
        f'Wallet-transfer telemetry must be persisted before coverage telemetry; order={order}'
    )
    # Both alert rules must fire before coverage too (they are committed evidence).
    assert order.index('smoke_alert') < order.index('coverage')
    assert order.index('sig_alert') < order.index('coverage')


# ---------------------------------------------------------------------------
# 3. Coverage telemetry retries on a fresh connection when the connection is closed
# ---------------------------------------------------------------------------

def test_coverage_telemetry_retries_on_fresh_connection_when_closed():
    target = _wallet_target()
    provider_result = _live_provider_result([])
    used_connections: list[str] = []
    fresh_conn = _DepthConn()

    def _coverage(conn, *, target, provider_result, observed_at):
        used_connections.append(getattr(conn, 'label', 'unknown'))
        if getattr(conn, 'label', '') == 'dead':
            raise psycopg.OperationalError('terminating connection due to idle-in-transaction timeout; the connection is closed')
        # fresh connection: succeed
        return None

    dead_conn = _DepthConn()
    dead_conn.label = 'dead'
    fresh_conn.label = 'fresh'

    @contextmanager
    def _fake_pg():
        yield fresh_conn

    with (
        patch.object(monitoring_runner, '_persist_live_coverage_telemetry', _coverage),
        patch.object(monitoring_runner, 'pg_connection', _fake_pg),
    ):
        ok = monitoring_runner._persist_live_coverage_telemetry_resilient(
            dead_conn, target=target, provider_result=provider_result, observed_at=_utcnow(),
        )

    assert ok is True, 'Coverage write must succeed after retrying on a fresh connection'
    assert used_connections == ['dead', 'fresh'], (
        f'Must try the live connection, then retry on a fresh one; got {used_connections}'
    )
    assert fresh_conn.autocommit is True, 'Fresh recovery connection must be autocommit'


# ---------------------------------------------------------------------------
# 4. Coverage write failure is isolated (does not crash) — degraded/partial result
# ---------------------------------------------------------------------------

def test_coverage_write_failure_is_isolated_logs_and_returns_false(caplog):
    target = _wallet_target()
    provider_result = _live_provider_result([])

    def _coverage(_conn, *, target, provider_result, observed_at):
        # Non-connection error: must NOT retry, must be swallowed.
        raise ValueError('coverage boom')

    with patch.object(monitoring_runner, '_persist_live_coverage_telemetry', _coverage):
        with caplog.at_level('WARNING'):
            ok = monitoring_runner._persist_live_coverage_telemetry_resilient(
                _DepthConn(), target=target, provider_result=provider_result, observed_at=_utcnow(),
            )

    assert ok is False
    assert any('coverage_telemetry_write_failed' in r.message for r in caplog.records)


def test_target_poll_returns_degraded_summary_when_coverage_fails():
    """A coverage-write failure must NOT crash the target poll: the function returns a
    truthful degraded summary (no coverage heartbeat, no coverage timestamp)."""
    target = _wallet_target()
    conn = _DepthConn()

    def _coverage(_conn, *, target, provider_result, observed_at):
        raise RuntimeError('db write failed')

    with (
        patch.object(monitoring_runner, 'fetch_target_activity_result', return_value=_live_provider_result([])),
        patch.object(monitoring_runner, '_load_checkpoint', return_value=0),
        patch.object(monitoring_runner, '_resolve_coverage_asset_id', return_value=None),
        patch.object(monitoring_runner, '_persist_live_coverage_telemetry', _coverage),
    ):
        result = monitoring_runner.process_monitoring_target(conn, target)

    assert isinstance(result, dict), 'Coverage failure must not raise out of the target poll'
    assert result.get('coverage_heartbeat_count') == 0
    assert result.get('live_coverage_telemetry_at') is None, (
        'Truthfulness: a failed coverage write must not report a coverage timestamp'
    )


def test_full_cycle_emits_summary_when_coverage_write_fails(monkeypatch):
    """The cycle must still return its summary (and count the target as checked) even
    when coverage telemetry raises inside process_monitoring_target."""
    now = datetime.now(timezone.utc)
    candidate = {
        'id': 'tgt-cov', 'name': 'Cov Target', 'target_type': 'contract', 'workspace_id': 'ws-1',
        'asset_id': None, 'monitoring_enabled': True, 'enabled': True, 'is_active': True,
        'last_checked_at': None, 'monitoring_interval_seconds': 300,
        'monitoring_dead_lettered_at': None, 'chain_network': 'base', 'created_at': now,
    }
    conn = _CycleConn([candidate])

    def _process(_connection, target, monitoring_run_id=None, triggered_by_user_id=None):
        # Coverage failed inside, but the poll still produced a truthful partial result.
        res = _process_result(target)
        res['coverage_heartbeat_count'] = 0
        res['live_coverage_telemetry_at'] = None
        res['status'] = 'no_evidence'
        return res

    _install_cycle_patches(monkeypatch, conn, _process)
    summary = monitoring_runner.run_monitoring_cycle(worker_name='test-worker', limit=10)

    assert summary['checked'] == 1, 'Target must still be counted as checked'
    assert 'cycle_duration_ms' in summary, 'Cycle summary must be emitted normally'


# ---------------------------------------------------------------------------
# 5. Error handler must NOT reuse a dead connection
# ---------------------------------------------------------------------------

def test_error_handler_uses_fresh_connection_when_worker_connection_dead(monkeypatch):
    now = datetime.now(timezone.utc)
    candidate = {
        'id': 'tgt-dead', 'name': 'Dead Target', 'target_type': 'contract', 'workspace_id': 'ws-1',
        'asset_id': None, 'monitoring_enabled': True, 'enabled': True, 'is_active': True,
        'last_checked_at': None, 'monitoring_interval_seconds': 300,
        'monitoring_dead_lettered_at': None, 'chain_network': 'base', 'created_at': now,
    }
    main_conn = _CycleConn([candidate])
    fresh_conn = _CycleConn([])
    fresh_conn.label = 'fresh'
    main_conn.label = 'main'

    def _process(_connection, target, monitoring_run_id=None, triggered_by_user_id=None):
        raise psycopg.OperationalError('terminating connection due to idle-in-transaction timeout: the connection is closed')

    _install_cycle_patches(monkeypatch, main_conn, _process, extra_connections=[fresh_conn])
    monitoring_runner.run_monitoring_cycle(worker_name='test-worker', limit=10)

    fresh_error_updates = [q for q in fresh_conn.executed if 'update targets set' in q.lower() and 'last_run_status' in q.lower()]
    main_error_updates = [
        q for q in main_conn.executed
        if 'update targets set' in q.lower() and 'monitoring_delivery_attempts = monitoring_delivery_attempts + 1' in q.lower()
    ]
    assert fresh_error_updates, 'Error-status UPDATE must run on a FRESH connection when the worker connection is dead'
    assert not main_error_updates, 'Dead worker connection must NOT be reused for error bookkeeping'


def test_error_handler_failure_on_fresh_connection_does_not_crash_cycle(monkeypatch, caplog):
    now = datetime.now(timezone.utc)
    candidate = {
        'id': 'tgt-dead2', 'name': 'Dead Target 2', 'target_type': 'contract', 'workspace_id': 'ws-1',
        'asset_id': None, 'monitoring_enabled': True, 'enabled': True, 'is_active': True,
        'last_checked_at': None, 'monitoring_interval_seconds': 300,
        'monitoring_dead_lettered_at': None, 'chain_network': 'base', 'created_at': now,
    }
    main_conn = _CycleConn([candidate])
    # Fresh connection also fails on the error-status write.
    fresh_conn = _CycleConn([], error_on='update targets set', error=psycopg.OperationalError('connection is closed'))

    def _process(_connection, target, monitoring_run_id=None, triggered_by_user_id=None):
        raise psycopg.OperationalError('the connection is closed')

    _install_cycle_patches(monkeypatch, main_conn, _process, extra_connections=[fresh_conn])
    with caplog.at_level('WARNING'):
        summary = monitoring_runner.run_monitoring_cycle(worker_name='test-worker', limit=10)

    assert any('error_handler_failed' in r.message for r in caplog.records), (
        'A fresh-connection failure must be logged once as error_handler_failed'
    )
    assert 'cycle_duration_ms' in summary, 'Cycle must still emit its summary and not crash'


# ---------------------------------------------------------------------------
# 6. Duplicate retry does not create duplicate alerts (dedupe signature reused)
# ---------------------------------------------------------------------------

def test_dedupe_signature_is_deterministic_for_same_transaction():
    kwargs = dict(workspace_id='ws-1', target_id='tgt-1', chain_id=BASE_CHAIN_ID, tx_hash=TX_HASH)
    assert monitoring_runner._smoke_dedupe_signature(**kwargs) == monitoring_runner._smoke_dedupe_signature(**kwargs)
    assert monitoring_runner._sig_dedupe_signature(**kwargs) == monitoring_runner._sig_dedupe_signature(**kwargs)
    # A different tx_hash must produce a different signature (alerts are never collapsed).
    other = dict(kwargs, tx_hash='0x' + 'f' * 64)
    assert monitoring_runner._smoke_dedupe_signature(**kwargs) != monitoring_runner._smoke_dedupe_signature(**other)


def test_duplicate_retry_reuses_existing_alert_instead_of_inserting():
    """On retry with the same dedupe signature, _upsert_alert must UPDATE the existing
    alert (idempotent) rather than INSERT a second alert row."""
    signature = monitoring_runner._smoke_dedupe_signature(
        workspace_id='ws-1', target_id='tgt-1', chain_id=BASE_CHAIN_ID, tx_hash=TX_HASH,
    )
    existing_alert_id = str(uuid.uuid4())
    inserts: list[str] = []
    updates: list[str] = []

    class _AlertConn:
        def execute(self, query, params=None):
            q = ' '.join(str(query).split()).lower()
            if 'from alert_suppression_rules' in q:
                return _Rows([])  # not suppressed
            if q.startswith('select id, occurrence_count') and 'from alerts' in q:
                # Simulate the alert already existing within the dedupe window.
                return _Rows([{'id': existing_alert_id, 'occurrence_count': 1}])
            if q.startswith('insert into alerts'):
                inserts.append(q)
            if q.startswith('update alerts'):
                updates.append(q)
            return _Rows([])

        @contextmanager
        def transaction(self):
            yield

    out: dict = {}
    alert_id = monitoring_runner._upsert_alert(
        _AlertConn(),
        workspace_id='ws-1',
        user_id='user-1',
        target_id='tgt-1',
        analysis_run_id=None,
        title='Monitored wallet transfer',
        response={'severity': 'critical', 'explanation': 'x', 'reasons': [], 'matched_patterns': []},
        signature=signature,
        out=out,
    )

    assert alert_id == existing_alert_id
    assert out['created'] is False, 'Retry must not report a newly-created alert'
    assert not inserts, 'Duplicate retry must NOT INSERT a second alert row'
    assert updates, 'Duplicate retry must UPDATE (bump) the existing alert instead'


# ---------------------------------------------------------------------------
# 7. Cursor must not advance when the connection dies during state persist
# ---------------------------------------------------------------------------

def test_cursor_not_advanced_when_connection_dies_during_target_update():
    """If the connection dies while persisting target state (cursor write), the failure
    must propagate so the cycle treats the target as failed — the cursor write never
    silently commits a partially-advanced cursor."""
    target = _wallet_target()
    # Fail on the canonical target-state UPDATE that carries monitoring_checkpoint_cursor.
    conn = _DepthConn(
        error_on='monitoring_checkpoint_cursor',
        error=psycopg.OperationalError('the connection is closed'),
    )

    with (
        patch.object(monitoring_runner, 'fetch_target_activity_result', return_value=_live_provider_result([])),
        patch.object(monitoring_runner, '_load_checkpoint', return_value=0),
        patch.object(monitoring_runner, '_resolve_coverage_asset_id', return_value=None),
        patch.object(monitoring_runner, '_persist_live_coverage_telemetry', lambda *a, **k: None),
    ):
        with pytest.raises(psycopg.OperationalError):
            monitoring_runner.process_monitoring_target(conn, target)

    cursor_updates = [q for q in conn.executed if 'monitoring_checkpoint_cursor' in q.lower()]
    assert cursor_updates, 'The target-state UPDATE (cursor persist) must have been attempted'
    # The UPDATE raised (connection dead) — it never committed an advanced cursor.


def test_connection_death_during_event_processing_fails_target_without_advancing_cursor():
    """If the connection dies inside _process_single_event (after the wallet-transfer
    telemetry/alert already committed on their own connections), the target must fail
    fast — the canonical target-state UPDATE that persists the cursor must NEVER run."""
    target = _wallet_target()
    event = _wallet_event(target_id=str(target['id']))
    conn = _DepthConn()
    raw_patch, smoke_patch, sig_patch = _patch_alert_helpers()

    def _dead_event(*_a, **_k):
        raise psycopg.OperationalError('terminating connection due to idle-in-transaction timeout')

    with (
        patch.object(monitoring_runner, 'fetch_target_activity_result', return_value=_live_provider_result([event])),
        patch.object(monitoring_runner, '_load_checkpoint', return_value=0),
        patch.object(monitoring_runner, '_resolve_coverage_asset_id', return_value=None),
        patch.object(monitoring_runner, '_process_single_event', _dead_event),
        raw_patch, smoke_patch, sig_patch,
    ):
        with pytest.raises(psycopg.OperationalError):
            monitoring_runner.process_monitoring_target(conn, target)

    cursor_updates = [q for q in conn.executed if 'monitoring_checkpoint_cursor' in q.lower()]
    assert not cursor_updates, (
        'A connection death during event processing must NOT reach the cursor-advancing '
        'target-state UPDATE'
    )


def test_is_connection_lost_error_detects_idle_in_transaction_and_closed():
    assert monitoring_runner._is_connection_lost_error(
        psycopg.OperationalError('terminating connection due to idle-in-transaction timeout')
    ) is True
    assert monitoring_runner._is_connection_lost_error(
        psycopg.OperationalError('the connection is closed')
    ) is True
    # Wrapped (chained) cause must still be detected.
    try:
        try:
            raise psycopg.OperationalError('server closed the connection unexpectedly')
        except Exception as inner:
            raise RuntimeError('event_processing_failed') from inner
    except Exception as chained:
        assert monitoring_runner._is_connection_lost_error(chained) is True
    # An ordinary application error is NOT a connection-lost error.
    assert monitoring_runner._is_connection_lost_error(ValueError('boom')) is False


# ---------------------------------------------------------------------------
# Cycle-level fake connection + harness
# ---------------------------------------------------------------------------

def _process_result(target) -> dict:
    return {
        'target_id': str(target['id']),
        'target_type': str(target.get('target_type') or 'contract'),
        'monitoring_run_id': str(uuid.uuid4()),
        'runs': [],
        'alerts_generated': 0,
        'incidents_created': 0,
        'detections_created': 0,
        'events_ingested': 0,
        'real_events_detected': 0,
        'real_event_count': 0,
        'coverage_heartbeat_updates': 0,
        'coverage_heartbeat_count': 0,
        'telemetry_records_seen': 0,
        'status': 'no_evidence',
        'provider_status': 'no_evidence',
        'source_status': 'no_evidence',
        'last_event_at': None,
        'live_coverage_telemetry_at': None,
        'protected_asset_coverage_record': {},
    }


class _CycleConn:
    """Minimal run_monitoring_cycle connection double with txn-depth tracking,
    autocommit, and per-statement error injection."""

    def __init__(self, due_targets, *, error_on: str | None = None, error: Exception | None = None):
        self.due_targets = due_targets
        self.executed: list[str] = []
        self.txn_depth = 0
        self.autocommit = False
        self.health_row = None
        self.latest_health_row = None
        self.last_worker_state_update_params = None
        self._error_on = (error_on or '').lower() or None
        self._error = error
        self._error_fired = False

    def execute(self, query, params=None):
        q = ' '.join(str(query).split())
        ql = q.lower()
        self.executed.append(q)
        if self._error_on and not self._error_fired and self._error_on in ql:
            self._error_fired = True
            raise self._error
        if 'from monitored_systems ms join targets t on t.id = ms.target_id' in ql:
            rows = [
                {
                    'monitored_system_id': f"system-{t['id']}",
                    'workspace_id': t.get('workspace_id') or 'ws-1',
                    'target_id': t['id'],
                    'asset_id': t.get('asset_id'),
                    'monitored_system_enabled': True,
                    'monitored_system_runtime_status': 'active',
                    'monitored_system_last_heartbeat': None,
                    'last_checked_at': t.get('last_checked_at'),
                    'monitoring_interval_seconds': t.get('monitoring_interval_seconds'),
                    'monitoring_enabled': t.get('monitoring_enabled', True),
                    'enabled': t.get('enabled', True),
                    'is_active': t.get('is_active', True),
                    'created_at': t.get('created_at'),
                    'monitoring_dead_lettered_at': t.get('monitoring_dead_lettered_at'),
                    'chain_network': t.get('chain_network'),
                }
                for t in self.due_targets
            ]
            return _Rows(rows)
        if 'from targets' in ql and 'for update skip locked' in ql:
            due_ids = {str(item) for item in (params[0] or [])} if params else set()
            rows = []
            for t in self.due_targets:
                if due_ids and str(t.get('id')) not in due_ids:
                    continue
                row = dict(t)
                row.setdefault('workspace_id', 'ws-1')
                rows.append(row)
            return _Rows(rows)
        if ql.startswith('select 1 from targets where id'):
            return _Rows([{'exists': 1}])
        if ql.startswith('update monitoring_worker_state'):
            self.last_worker_state_update_params = params
            return _Rows([])
        if ql.startswith('select') and 'count(' in ql:
            return _Rows([{'c': 0}])
        if ql.startswith('select'):
            return _Rows([])
        return _Rows([])

    @contextmanager
    def transaction(self):
        self.txn_depth += 1
        try:
            yield
        finally:
            self.txn_depth -= 1

    def commit(self):
        return None


def _install_cycle_patches(monkeypatch, main_conn, process_fn, *, extra_connections=None):
    conns = [main_conn] + list(extra_connections or [])
    it = iter(conns)

    @contextmanager
    def _pg():
        try:
            conn = next(it)
        except StopIteration:
            conn = conns[-1]
        yield conn

    monkeypatch.setattr(monitoring_runner, 'live_mode_enabled', lambda: True)
    monkeypatch.setattr(monitoring_runner, 'ensure_pilot_schema', lambda _c: None)
    monkeypatch.setattr(monitoring_runner, '_verify_monitoring_fk_alignment', lambda _c: {})
    monkeypatch.setattr(monitoring_runner, '_telemetry_idempotency_index_guard', lambda _c: True)
    monkeypatch.setattr(monitoring_runner, 'monitoring_ingestion_runtime', lambda: {'source': 'live', 'mode': 'live', 'degraded': False})
    monkeypatch.setattr(monitoring_runner, '_derive_system_runtime_state', lambda *_a, **_k: ('healthy', 'fresh', 'high', None))
    monkeypatch.setattr(monitoring_runner, 'process_monitoring_target', process_fn)
    monkeypatch.setattr(monitoring_runner, 'pg_connection', _pg)
