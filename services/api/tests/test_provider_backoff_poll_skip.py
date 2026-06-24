"""
Provider 429 backoff: the worker must SKIP the target polling flow instead of
walking through it and logging a poll that never happened.

Regression coverage for the cleanup where, during ``provider_backoff_active``,
``process_monitoring_target`` still logged polling_cycle_start / scan_cursor_persist /
receipt_persist_checkpoint / poll_completed / "checked target" — misleading because no
RPC poll occurred.

Required behavior while the provider backoff is active (no RPC call made this cycle):
  1. scan cursor is NOT persisted.
  2. receipt checkpoint is NOT written / coverage telemetry is NOT written.
  3. ``checked`` count is NOT incremented (counts as skipped_provider_backoff instead).
  4. a clear ``provider_poll_skipped`` line is logged (not poll_completed / checked target).
  5. the stale-telemetry self-monitoring alert is throttled (no per-cycle spam).
  6. no RPC call is made (fetch_target_activity_result short-circuits).
"""
from __future__ import annotations

import logging
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from services.api.app import monitoring_runner
from services.api.app.activity_providers import ActivityProviderResult

WALLET = '0x5f6f35fd8b10c5576089f99c7c8c351deb851d1f'
RUNNER_LOGGER = 'services.api.app.monitoring_runner'


# ---------------------------------------------------------------------------
# Shared fakes
# ---------------------------------------------------------------------------

class _FakeRows:
    def __init__(self, rows=None, row=None):
        self._rows = rows if rows is not None else ([] if row is None else [row])
        self._row = row
        self.rowcount = len(self._rows)

    def fetchone(self):
        if self._row is not None:
            return self._row
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)

    def __iter__(self):
        return iter(self._rows)


class _RecordingConn:
    """Connection stub that records every SQL statement + params executed."""

    def __init__(self, *, workspace_id: str, target_id: str):
        self.executed: list[tuple[str, tuple]] = []
        self._workspace_id = workspace_id
        self._target_id = target_id

    def execute(self, sql: str, params: tuple = ()) -> _FakeRows:
        self.executed.append((sql.strip(), params))
        low = sql.strip().lower()
        if 'select id, name from workspaces' in low:
            return _FakeRows(row={'id': self._workspace_id, 'name': 'Test Workspace'})
        if 'from monitor_checkpoint' in low:
            return _FakeRows(rows=[])
        if low.startswith('select'):
            return _FakeRows(rows=[])
        return _FakeRows()

    def commit(self):
        pass

    def rollback(self):
        pass

    class _Savepoint:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def transaction(self):
        return self._Savepoint()


def _backoff_provider_result() -> ActivityProviderResult:
    """Mirror the result activity_providers returns while the 429 backoff is active."""
    return ActivityProviderResult(
        mode='live',
        status='degraded',
        evidence_state='DEGRADED_EVIDENCE',
        truthfulness_state='UNKNOWN_RISK',
        synthetic=False,
        provider_name='evm_activity_provider',
        provider_kind='rpc',
        evidence_present=False,
        recent_real_event_count=0,
        last_real_event_at=None,
        events=[],
        latest_block=None,
        checkpoint=None,
        checkpoint_age_seconds=None,
        degraded_reason='provider_backoff_active',
        error_code=None,
        source_type='rpc_polling',
        reason_code='PROVIDER_BACKOFF_ACTIVE',
        claim_safe=False,
        detection_outcome='MONITORING_DEGRADED',
    )


def _backoff_target() -> dict:
    return {
        'id': str(uuid.uuid4()),
        'workspace_id': str(uuid.uuid4()),
        'name': 'Base Wallet',
        'target_type': 'wallet',
        'chain_network': 'base',
        'wallet_address': WALLET,
        'contract_identifier': None,
        'monitoring_checkpoint_cursor': None,
        'monitoring_checkpoint_at': None,
        'last_checked_at': None,
        'monitoring_interval_seconds': 300,
        'updated_by_user_id': str(uuid.uuid4()),
        'created_by_user_id': str(uuid.uuid4()),
        'asset_id': None,
        'monitoring_enabled': True,
        'monitoring_mode': 'live',
        'auto_create_alerts': True,
        'auto_create_incidents': False,
        'severity_threshold': 'low',
        'watcher_last_observed_block': 47_000_000,
        'monitored_system_id': None,
        'enabled': True,
        'is_active': True,
    }


@contextmanager
def _run_backoff_target(conn, target, caplog):
    """Drive process_monitoring_target with a backoff provider result + neutered helpers."""
    with (
        patch.object(monitoring_runner, 'fetch_target_activity_result', return_value=_backoff_provider_result()),
        patch.object(monitoring_runner, '_upsert_checkpoint', MagicMock()) as upsert,
        patch.object(monitoring_runner, '_persist_live_coverage_telemetry', MagicMock()) as cov,
        patch.object(monitoring_runner, '_persist_detection_evaluation_checkpoint', MagicMock()) as det_ckpt,
        patch.object(monitoring_runner, '_persist_no_threat_evaluation_marker', MagicMock()) as no_threat,
        patch.object(monitoring_runner, '_resolve_coverage_asset_id', MagicMock(return_value=None)),
    ):
        with caplog.at_level(logging.INFO, logger=RUNNER_LOGGER):
            result = monitoring_runner.process_monitoring_target(conn, target)
        yield result, {'upsert': upsert, 'cov': cov, 'det_ckpt': det_ckpt, 'no_threat': no_threat}


# ---------------------------------------------------------------------------
# 1. provider_backoff_active does not persist scan cursor
# ---------------------------------------------------------------------------

def test_provider_backoff_does_not_persist_scan_cursor(caplog):
    target = _backoff_target()
    conn = _RecordingConn(workspace_id=target['workspace_id'], target_id=target['id'])

    with _run_backoff_target(conn, target, caplog) as (result, mocks):
        assert result['provider_poll_skipped'] is True

    # The canonical checkpoint upsert must NOT run.
    assert mocks['upsert'].call_count == 0, 'scan cursor checkpoint must not be persisted during backoff'
    # No targets UPDATE may advance the scan cursor.
    cursor_writes = [
        sql for sql, _ in conn.executed
        if 'update targets' in sql.lower() and 'monitoring_checkpoint_cursor' in sql.lower()
    ]
    assert not cursor_writes, f'no scan-cursor write expected during backoff, got: {cursor_writes}'
    # scan_cursor_persist log line must not be emitted.
    assert 'scan_cursor_persist' not in caplog.text


# ---------------------------------------------------------------------------
# 2. provider_backoff_active does not write receipt checkpoint / coverage telemetry
# ---------------------------------------------------------------------------

def test_provider_backoff_does_not_write_receipt_checkpoint(caplog):
    target = _backoff_target()
    conn = _RecordingConn(workspace_id=target['workspace_id'], target_id=target['id'])

    with _run_backoff_target(conn, target, caplog) as (result, mocks):
        assert result['provider_poll_skipped'] is True

    assert 'receipt_persist_checkpoint' not in caplog.text, 'no receipt checkpoint may be logged during backoff'
    assert mocks['cov'].call_count == 0, 'coverage telemetry must not be persisted during backoff'
    coverage_writes = [sql for sql, _ in conn.executed if 'insert into target_coverage_records' in sql.lower()]
    assert not coverage_writes, 'no coverage record may be written during backoff'
    # Coverage heartbeat count is zero in the skip result.
    assert result['coverage_heartbeat_updates'] == 0
    assert result['telemetry_records_seen'] == 0


# ---------------------------------------------------------------------------
# 3. provider_backoff_active does not increment checked count (cycle level)
# ---------------------------------------------------------------------------

class _CycleResult:
    def __init__(self, *, rows=None, row=None):
        self._rows = rows or []
        self._row = row

    def fetchall(self):
        return self._rows

    def fetchone(self):
        return self._row


class _CycleTxn:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class _CycleConn:
    """Minimal connection that lets run_monitoring_cycle select one due target."""

    def __init__(self, due_targets):
        self.due_targets = due_targets
        self.health_row = None
        self.latest_health_row = None
        self.last_worker_state_update_params = None

    def transaction(self):
        return _CycleTxn()

    def execute(self, query, params=None):
        n = ' '.join(str(query).split())
        if 'FROM monitored_systems ms JOIN targets t ON t.id = ms.target_id' in n:
            rows = [
                {
                    'monitored_system_id': f"system-{t['id']}",
                    'workspace_id': t.get('workspace_exists_id') or 'ws-1',
                    'target_id': t['id'],
                    'asset_id': None,
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
            return _CycleResult(rows=rows)
        if 'FROM targets' in n and 'FOR UPDATE SKIP LOCKED' in n:
            due_ids = {str(item) for item in (params[0] or [])} if params else set()
            rows = []
            for t in self.due_targets:
                if due_ids and str(t.get('id')) not in due_ids:
                    continue
                row = dict(t)
                row.setdefault('workspace_id', t.get('workspace_exists_id') or 'ws-1')
                rows.append(row)
            return _CycleResult(rows=rows)
        if 'SELECT EXISTS' in n and 'pg_get_indexdef' in n:
            return _CycleResult(row={'ok': True})
        if n.startswith('SELECT 1 FROM targets WHERE id'):
            return _CycleResult(row={'exists': 1})
        if n.startswith('UPDATE monitoring_worker_state'):
            self.last_worker_state_update_params = params
            return _CycleResult()
        return _CycleResult()

    def commit(self):
        return None


@contextmanager
def _fake_pg(connection):
    yield connection


def test_provider_backoff_does_not_increment_checked_count(monkeypatch, caplog):
    now = datetime.now(timezone.utc)
    due_targets = [{
        'id': 'base-target',
        'name': 'Base Target',
        'monitoring_enabled': True,
        'enabled': True,
        'is_active': True,
        'workspace_exists_id': 'ws-1',
        'monitored_by_workspace_id': None,
        'last_checked_at': None,
        'monitoring_interval_seconds': 300,
        'created_at': now,
        'target_type': 'wallet',
        'chain_network': 'base',
    }]
    connection = _CycleConn(due_targets)

    monkeypatch.setattr(monitoring_runner, 'live_mode_enabled', lambda: True)
    monkeypatch.setattr(monitoring_runner, 'ensure_pilot_schema', lambda _c: None)
    monkeypatch.setattr(monitoring_runner, 'pg_connection', lambda: _fake_pg(connection))

    def _process(_c, target, *, monitoring_run_id=None, triggered_by_user_id=None):
        return {
            'target_id': target['id'],
            'target_type': 'wallet',
            'monitoring_run_id': monitoring_run_id,
            'runs': [],
            'alerts_generated': 0,
            'events_ingested': 0,
            'real_events_detected': 0,
            'coverage_heartbeat_updates': 0,
            'provider_poll_skipped': True,
            'provider_backoff_active': True,
            'backoff_until': '2026-06-24T00:01:00+00:00',
            'status': 'provider_backoff_skipped',
        }

    monkeypatch.setattr(monitoring_runner, 'process_monitoring_target', _process)

    with caplog.at_level(logging.INFO, logger=RUNNER_LOGGER):
        summary = monitoring_runner.run_monitoring_cycle(worker_name='test-worker', limit=10)

    assert summary['due_targets'] == 1
    assert summary['checked'] == 0, 'a backoff-skipped target must not be counted as checked'
    assert summary['skipped_provider_backoff'] == 1
    # Cycle summary log reflects the truthful counters.
    assert 'checked=0' in caplog.text
    assert 'skipped_provider_backoff=1' in caplog.text
    assert 'due=1' in caplog.text
    # No poll completion was logged for the skipped target.
    assert 'poll_completed' not in caplog.text


# ---------------------------------------------------------------------------
# 4. provider_backoff_active logs provider_poll_skipped (not poll_completed/checked)
# ---------------------------------------------------------------------------

def test_provider_backoff_logs_provider_poll_skipped(caplog):
    target = _backoff_target()
    conn = _RecordingConn(workspace_id=target['workspace_id'], target_id=target['id'])

    with _run_backoff_target(conn, target, caplog) as (result, _mocks):
        assert result['provider_poll_skipped'] is True
        assert result['status'] == 'provider_backoff_skipped'
        assert result['degraded_reason'] == 'provider_backoff_active'

    text = caplog.text
    assert 'provider_poll_skipped' in text
    assert f"target_id={target['id']}" in text
    assert 'reason=provider_backoff_active' in text
    # The misleading "real poll" lines must NOT appear.
    assert 'polling_cycle_start' not in text
    assert 'poll_completed' not in text
    assert 'checked target' not in text

    # The target was NOT marked checked (no last_checked_at write this cycle).
    checked_writes = [
        sql for sql, _ in conn.executed
        if 'update targets' in sql.lower() and 'last_checked_at' in sql.lower()
    ]
    assert not checked_writes, 'target must not be marked checked during backoff'
    # The worker claim/lease IS released so the next cycle can re-poll.
    lease_release = [
        sql for sql, _ in conn.executed
        if 'update targets' in sql.lower() and 'monitoring_lease_token = null' in sql.lower()
    ]
    assert lease_release, 'the worker lease must be released so the target is re-evaluated next cycle'


# ---------------------------------------------------------------------------
# 5. stale telemetry self-monitoring alert is throttled
# ---------------------------------------------------------------------------

def test_stale_telemetry_self_monitoring_alert_is_throttled(monkeypatch):
    from services.api.app import pilot

    class _PilotConn:
        def execute(self, query, params=None):
            q = ' '.join(str(query).split()).lower()
            if 'from telemetry_events' in q and 'group by workspace_id' in q:
                return _CycleResult(rows=[{
                    'fingerprint': 'ws-1:tgt-1',
                    'details': {'workspace_id': 'ws-1', 'target_id': 'tgt-1', 'latest_telemetry_at': None},
                }])
            if q.startswith('select external_delivery_status'):
                return _CycleResult(row=None)
            return _CycleResult(rows=[], row=None)

        def commit(self):
            pass

    monkeypatch.setenv('MONITORING_SELF_ALERT_THROTTLE_SECONDS', '900')
    monkeypatch.setattr(pilot, 'pg_connection', lambda: _fake_pg(_PilotConn()))
    monkeypatch.setattr(pilot, 'ensure_pilot_schema', lambda *_: None)
    pilot._SELF_MONITORING_ALERT_LAST_SENT.clear()

    calls: list[tuple] = []
    monkeypatch.setattr(
        pilot, 'send_external_oncall_alert',
        lambda *a, **kw: (calls.append((a, kw)), False)[1],
    )

    # Two consecutive worker cycles see the same persistently-stale target.
    pilot.evaluate_monitoring_system_alerts(stale_after_seconds=120)
    pilot.evaluate_monitoring_system_alerts(stale_after_seconds=120)

    stale_sends = [a for a, _kw in calls if a and a[0] == 'stale_telemetry']
    assert len(stale_sends) == 1, (
        f'stale_telemetry alert must fire at most once per throttle window, got {len(stale_sends)} sends'
    )


def test_self_monitoring_alert_fires_again_after_throttle_window(monkeypatch):
    """When the throttle window has elapsed, the alert is allowed to fire again."""
    from services.api.app import pilot

    class _PilotConn:
        def execute(self, query, params=None):
            q = ' '.join(str(query).split()).lower()
            if 'from telemetry_events' in q and 'group by workspace_id' in q:
                return _CycleResult(rows=[{
                    'fingerprint': 'ws-1:tgt-1',
                    'details': {'workspace_id': 'ws-1', 'target_id': 'tgt-1', 'latest_telemetry_at': None},
                }])
            if q.startswith('select external_delivery_status'):
                return _CycleResult(row=None)
            return _CycleResult(rows=[], row=None)

        def commit(self):
            pass

    monkeypatch.setenv('MONITORING_SELF_ALERT_THROTTLE_SECONDS', '900')
    monkeypatch.setattr(pilot, 'pg_connection', lambda: _fake_pg(_PilotConn()))
    monkeypatch.setattr(pilot, 'ensure_pilot_schema', lambda *_: None)
    pilot._SELF_MONITORING_ALERT_LAST_SENT.clear()
    # Pre-seed a last-sent timestamp older than the throttle window.
    from time import monotonic
    pilot._SELF_MONITORING_ALERT_LAST_SENT[('stale_telemetry', 'ws-1:tgt-1')] = monotonic() - 1000

    calls: list[tuple] = []
    monkeypatch.setattr(
        pilot, 'send_external_oncall_alert',
        lambda *a, **kw: (calls.append((a, kw)), False)[1],
    )

    pilot.evaluate_monitoring_system_alerts(stale_after_seconds=120)
    stale_sends = [a for a, _kw in calls if a and a[0] == 'stale_telemetry']
    assert len(stale_sends) == 1, 'alert must fire again once the throttle window has elapsed'


# ---------------------------------------------------------------------------
# 6. no RPC call is made while the provider backoff is active
# ---------------------------------------------------------------------------

def test_no_rpc_call_during_provider_backoff(monkeypatch):
    from services.api.app import activity_providers as ap
    from services.api.app import evm_activity_provider as eap

    for name in ('EVM_RPC_URL', 'STAGING_EVM_RPC_URL', 'LIVE_MONITORING_CHAINS', 'EVM_CHAIN_ID', 'EVM_WS_URL'):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv('MONITORING_INGESTION_MODE', 'live')
    monkeypatch.setenv('LIVE_MONITORING_ENABLED', 'true')
    monkeypatch.setenv('STAGING_EVM_RPC_URL', 'https://staging.rpc/v2/key')
    monkeypatch.setenv('EVM_CHAIN_ID', '8453')
    monkeypatch.setenv('LIVE_MONITORING_CHAINS', 'base')

    # Arm the process-wide backoff (a prior cycle hit HTTP 429).
    eap.reset_rpc_provider_state()
    eap.record_rpc_rate_limited(None)
    assert eap.rpc_provider_backoff_active() is True

    def _no_rpc(*_a, **_k):
        raise AssertionError('no RPC call may be made while the provider backoff is active')

    monkeypatch.setattr(ap, 'fetch_evm_activity', _no_rpc)
    monkeypatch.setattr(ap, 'probe_rpc_health', _no_rpc)

    target = {
        'id': str(uuid.uuid4()), 'workspace_id': str(uuid.uuid4()),
        'chain_network': 'base', 'target_type': 'wallet', 'wallet_address': WALLET,
    }
    result = ap.fetch_target_activity_result(target, None)

    assert result.reason_code == 'PROVIDER_BACKOFF_ACTIVE'
    assert result.status == 'degraded'
    assert result.evidence_present is False
    assert result.events == []
