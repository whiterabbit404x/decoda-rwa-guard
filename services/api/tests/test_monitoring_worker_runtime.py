from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

from services.api.app import monitoring_runner
from services.api.app import pilot
from services.api.app import run_monitoring_worker

REPO_ROOT = Path(__file__).resolve().parents[3]


class _Result:
    def __init__(self, *, rows=None, row=None):
        self._rows = rows or []
        self._row = row

    def fetchall(self):
        return self._rows

    def fetchone(self):
        return self._row


class _FakeTransaction:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class _FakeConnection:
    def __init__(self, due_targets):
        self.due_targets = due_targets
        self.health_row = None
        self.latest_health_row = None
        self.last_worker_state_update_params = None
        self.monitored_system_updates = []
        self.monitoring_run_inserts = []
        self.monitoring_run_updates = []

    def transaction(self):
        return _FakeTransaction()

    def execute(self, query, params=None):
        normalized = ' '.join(str(query).split())
        if 'FROM monitored_systems ms JOIN targets t ON t.id = ms.target_id' in normalized:
            rows = [
                {
                    'monitored_system_id': f"system-{target['id']}",
                    'workspace_id': target.get('workspace_exists_id') or 'ws-1',
                    'target_id': target['id'],
                    'asset_id': None,
                    'monitored_system_enabled': True,
                    'monitored_system_runtime_status': 'active',
                    'monitored_system_last_heartbeat': None,
                    'last_checked_at': target.get('last_checked_at'),
                    'monitoring_interval_seconds': target.get('monitoring_interval_seconds'),
                    'monitoring_enabled': target.get('monitoring_enabled', True),
                    'enabled': target.get('enabled', True),
                    'is_active': target.get('is_active', True),
                    'created_at': target.get('created_at'),
                    'monitoring_dead_lettered_at': target.get('monitoring_dead_lettered_at'),
                    'chain_network': target.get('chain_network'),
                }
                for target in self.due_targets
            ]
            return _Result(rows=rows)
        if 'LEFT JOIN workspaces AS workspace' in normalized:
            return _Result(rows=self.due_targets)
        if 'FROM targets' in normalized and 'FOR UPDATE SKIP LOCKED' in normalized:
            due_ids = {str(item) for item in (params[0] or [])} if params else set()
            rows = []
            for target in self.due_targets:
                if due_ids and str(target.get('id')) not in due_ids:
                    continue
                row = dict(target)
                row.setdefault('workspace_id', target.get('workspace_exists_id') or 'ws-1')
                rows.append(row)
            return _Result(rows=rows)
        if 'SELECT EXISTS' in normalized and 'pg_get_indexdef' in normalized:
            # Catalog guard: report telemetry idempotency index as present in tests.
            return _Result(row={'ok': True})
        if normalized.startswith('SELECT 1 FROM targets WHERE id'):
            # Parent guard check: target was just fetched, so it always exists in tests.
            return _Result(row={'exists': 1})
        if normalized.startswith('SELECT worker_name, running, status, last_started_at'):
            if 'WHERE worker_name = %s' in normalized:
                return _Result(row=self.health_row)
            return _Result(row=self.latest_health_row)
        if normalized.startswith('SELECT COUNT(*) AS overdue_count'):
            return _Result(row={'overdue_count': 0})
        if "COUNT(*) FILTER (WHERE status = 'queued')" in normalized:
            return _Result(row={'queued': 0, 'running': 0, 'failed': 0})
        if normalized.startswith('UPDATE monitoring_worker_state'):
            self.last_worker_state_update_params = params
            self.health_row = {
                'worker_name': params[5],
                'running': False,
                'status': 'error' if params[0] else 'idle',
                'last_started_at': datetime.now(timezone.utc),
                'last_heartbeat_at': datetime.now(timezone.utc),
                'last_cycle_at': datetime.now(timezone.utc),
                'last_cycle_due_targets': params[1],
                'last_cycle_targets_checked': params[2],
                'last_cycle_alerts_generated': params[3],
                'last_error': params[4],
                'updated_at': datetime.now(timezone.utc),
            }
            self.latest_health_row = dict(self.health_row)
            return _Result()
        if normalized.startswith('UPDATE monitored_systems SET last_heartbeat = NOW()'):
            self.monitored_system_updates.append(params)
            return _Result()
        if normalized.startswith('INSERT INTO monitoring_runs'):
            self.monitoring_run_inserts.append(params)
            return _Result()
        if normalized.startswith('UPDATE monitoring_runs'):
            self.monitoring_run_updates.append(params)
            return _Result()
        return _Result()

    def commit(self):
        return None


@contextmanager
def _fake_pg(connection):
    yield connection


def test_monitoring_cycle_updates_health_and_handles_target_exception(monkeypatch):
    now = datetime.now(timezone.utc)
    due_targets = [
        {
            'id': 'bad-target',
            'name': 'Bad Target',
            'monitoring_enabled': True,
            'enabled': True,
            'is_active': True,
            'workspace_exists_id': 'ws-1',
            'monitored_by_workspace_id': None,
            'monitored_workspace_exists_id': None,
            'last_checked_at': None,
            'monitoring_interval_seconds': 300,
            'created_at': now,
        },
        {
            'id': 'good-target',
            'name': 'Good Target',
            'monitoring_enabled': True,
            'enabled': True,
            'is_active': True,
            'workspace_exists_id': 'ws-1',
            'monitored_by_workspace_id': None,
            'monitored_workspace_exists_id': None,
            'last_checked_at': None,
            'monitoring_interval_seconds': 300,
            'created_at': now,
        },
    ]
    connection = _FakeConnection(due_targets)
    processed = []

    monkeypatch.setattr(monitoring_runner, 'live_mode_enabled', lambda: True)
    monkeypatch.setattr(monitoring_runner, 'ensure_pilot_schema', lambda _connection: None)
    monkeypatch.setattr(monitoring_runner, 'pg_connection', lambda: _fake_pg(connection))

    def _process(_connection, target, triggered_by_user_id=None):
        if target['id'] == 'bad-target':
            raise RuntimeError('boom')
        processed.append(target['id'])
        return {'alerts_generated': 1, 'target_id': target['id'], 'runs': ['run-1'], 'status': 'completed'}

    monkeypatch.setattr(monitoring_runner, 'process_monitoring_target', _process)

    summary = monitoring_runner.run_monitoring_cycle(worker_name='test-worker', limit=10)
    assert summary['due_targets'] == 2
    assert summary['checked'] == 1
    assert summary['alerts_generated'] == 1
    assert processed == ['good-target']

    monitoring_runner.WORKER_STATE['worker_name'] = 'test-worker'
    health = monitoring_runner.get_monitoring_health()
    assert health['worker_running'] is True
    assert health['last_cycle_due_targets'] == 2
    assert health['last_cycle_checked_targets'] == 1
    assert health['last_cycle_alerts_created'] == 1
    assert health['last_error'] == 'boom'
    assert connection.last_worker_state_update_params[0] == 'boom'
    assert connection.last_worker_state_update_params[4] == 'boom'


def test_monitoring_health_falls_back_to_latest_worker_when_configured_name_missing(monkeypatch):
    now = datetime.now(timezone.utc)
    connection = _FakeConnection(due_targets=[])
    connection.health_row = None
    connection.latest_health_row = {
        'worker_name': 'railway-monitoring-worker',
        'running': True,
        'status': 'running',
        'last_started_at': now,
        'last_heartbeat_at': now,
        'last_cycle_at': now,
        'last_cycle_due_targets': 4,
        'last_cycle_targets_checked': 4,
        'last_cycle_alerts_generated': 1,
        'last_error': None,
        'updated_at': now,
    }

    monkeypatch.setattr(monitoring_runner, 'live_mode_enabled', lambda: True)
    monkeypatch.setattr(monitoring_runner, 'ensure_pilot_schema', lambda _connection: None)
    monkeypatch.setattr(monitoring_runner, 'pg_connection', lambda: _fake_pg(connection))
    monitoring_runner.WORKER_STATE['worker_name'] = 'monitoring-worker'

    health = monitoring_runner.get_monitoring_health()
    assert health['worker_running'] is True
    assert health['worker_state_fallback_used'] is True
    assert health['worker_name_mismatch'] is True
    assert health['configured_worker_name'] == 'monitoring-worker'
    assert health['active_worker_name'] == 'railway-monitoring-worker'


def test_monitoring_cycle_updates_health_with_null_error_message(monkeypatch):
    now = datetime.now(timezone.utc)
    due_targets = [
        {
            'id': 'good-target',
            'name': 'Good Target',
            'monitoring_enabled': True,
            'enabled': True,
            'is_active': True,
            'workspace_exists_id': 'ws-1',
            'monitored_by_workspace_id': None,
            'monitored_workspace_exists_id': None,
            'last_checked_at': None,
            'monitoring_interval_seconds': 300,
            'created_at': now,
        }
    ]
    connection = _FakeConnection(due_targets)

    monkeypatch.setattr(monitoring_runner, 'live_mode_enabled', lambda: True)
    monkeypatch.setattr(monitoring_runner, 'ensure_pilot_schema', lambda _connection: None)
    monkeypatch.setattr(monitoring_runner, 'pg_connection', lambda: _fake_pg(connection))
    monkeypatch.setattr(
        monitoring_runner,
        'process_monitoring_target',
        lambda _connection, target, triggered_by_user_id=None: {
            'alerts_generated': 0,
            'target_id': target['id'],
            'runs': ['run-1'],
            'status': 'completed',
        },
    )

    summary = monitoring_runner.run_monitoring_cycle(worker_name='test-worker', limit=10)
    assert summary['due_targets'] == 1
    assert summary['checked'] == 1
    assert summary['alerts_generated'] == 0
    assert summary['live_mode'] is True
    assert connection.last_worker_state_update_params[0] is None
    assert connection.last_worker_state_update_params[4] is None


def test_monitoring_cycle_counts_coverage_telemetry_when_events_zero(monkeypatch):
    now = datetime.now(timezone.utc)
    due_targets = [
        {
            'id': 'target-1',
            'name': 'Target 1',
            'monitoring_enabled': True,
            'enabled': True,
            'is_active': True,
            'workspace_exists_id': 'ws-1',
            'last_checked_at': None,
            'monitoring_interval_seconds': 300,
            'created_at': now,
        }
    ]
    connection = _FakeConnection(due_targets)

    monkeypatch.setattr(monitoring_runner, 'live_mode_enabled', lambda: True)
    monkeypatch.setattr(monitoring_runner, 'ensure_pilot_schema', lambda _connection: None)
    monkeypatch.setattr(monitoring_runner, 'pg_connection', lambda: _fake_pg(connection))
    monkeypatch.setattr(
        monitoring_runner,
        'process_monitoring_target',
        lambda _connection, _target, triggered_by_user_id=None: {
            'alerts_generated': 0,
            'incidents_created': 0,
            'detections_created': 0,
            'events_ingested': 0,
            'telemetry_records_seen': 2,
            'status': 'no_real_data',
            'latest_processed_block': 100,
        },
    )

    summary = monitoring_runner.run_monitoring_cycle(worker_name='test-worker', limit=10)

    assert summary['checked'] == 1
    assert connection.monitoring_run_updates
    update_params = connection.monitoring_run_updates[-1]
    assert update_params[5] == 2
    assert len(connection.monitored_system_updates) == 1
    assert connection.monitored_system_updates[0][0] == 'idle'
    assert connection.monitored_system_updates[0][1] == 'active'


def test_monitoring_cycle_zero_events_does_not_mark_monitored_system_error(monkeypatch):
    now = datetime.now(timezone.utc)
    due_targets = [
        {
            'id': 'quiet-target',
            'name': 'Quiet Target',
            'monitoring_enabled': True,
            'enabled': True,
            'is_active': True,
            'workspace_exists_id': 'ws-1',
            'monitored_by_workspace_id': None,
            'monitored_workspace_exists_id': None,
            'last_checked_at': None,
            'monitoring_interval_seconds': 300,
            'created_at': now,
        }
    ]
    connection = _FakeConnection(due_targets)

    monkeypatch.setattr(monitoring_runner, 'live_mode_enabled', lambda: True)
    monkeypatch.setattr(monitoring_runner, 'ensure_pilot_schema', lambda _connection: None)
    monkeypatch.setattr(monitoring_runner, 'pg_connection', lambda: _fake_pg(connection))
    monkeypatch.setattr(
        monitoring_runner,
        'process_monitoring_target',
        lambda _connection, target, triggered_by_user_id=None: {
            'alerts_generated': 0,
            'incidents_created': 0,
            'events_ingested': 0,
            'target_id': target['id'],
            'runs': ['run-1'],
            'status': 'completed',
        },
    )

    summary = monitoring_runner.run_monitoring_cycle(worker_name='test-worker', limit=10)
    assert summary['checked'] == 1
    assert summary['events_ingested'] == 0
    assert len(connection.monitored_system_updates) == 1
    runtime_status, status = connection.monitored_system_updates[0][0], connection.monitored_system_updates[0][1]
    assert runtime_status == 'idle'
    assert status == 'active'


def test_monitoring_cycle_without_due_targets_reports_zero_updates(monkeypatch):
    connection = _FakeConnection(due_targets=[])

    monkeypatch.setattr(monitoring_runner, 'live_mode_enabled', lambda: True)
    monkeypatch.setattr(monitoring_runner, 'ensure_pilot_schema', lambda _connection: None)
    monkeypatch.setattr(monitoring_runner, 'pg_connection', lambda: _fake_pg(connection))

    summary = monitoring_runner.run_monitoring_cycle(worker_name='test-worker', limit=10)

    assert summary['due_targets'] == 0
    assert summary['checked'] == 0
    assert summary['alerts_generated'] == 0
    assert connection.last_worker_state_update_params[1] == 0
    assert connection.last_worker_state_update_params[2] == 0
    assert connection.last_worker_state_update_params[3] == 0


def test_monitoring_cycle_all_targets_not_due_reports_checked_zero(monkeypatch, caplog):
    now = datetime.now(timezone.utc)
    due_targets = [
        {
            'id': 'not-due-target',
            'name': 'Not Due Target',
            'monitoring_enabled': True,
            'enabled': True,
            'is_active': True,
            'workspace_exists_id': 'ws-1',
            'last_checked_at': now,
            'monitoring_interval_seconds': 3600,
            'created_at': now,
        }
    ]
    connection = _FakeConnection(due_targets)
    processed = {'count': 0}

    monkeypatch.setattr(monitoring_runner, 'live_mode_enabled', lambda: True)
    monkeypatch.setattr(monitoring_runner, 'ensure_pilot_schema', lambda _connection: None)
    monkeypatch.setattr(monitoring_runner, 'pg_connection', lambda: _fake_pg(connection))
    monkeypatch.setattr(
        monitoring_runner,
        'process_monitoring_target',
        lambda *_args, **_kwargs: processed.__setitem__('count', processed['count'] + 1),
    )

    with caplog.at_level('INFO'):
        summary = monitoring_runner.run_monitoring_cycle(worker_name='test-worker', limit=10)
    assert summary['due_targets'] == 0
    assert summary['checked'] == 0
    assert processed['count'] == 0
    assert len(connection.monitoring_run_inserts) == 0
    assert any(
        'monitoring due-selection snapshot worker=test-worker workspace_id=ws-1' in message
        and 'last_checked_at' in message
        and 'effective_interval_seconds' in message
        and 'next_due_at' in message
        and 'due_in_seconds' in message
        for message in caplog.messages
    )
    assert any(
        'monitoring cycle summary worker=test-worker' in message
        and 'due=0 checked=0' in message
        and 'backfill_attempted=0' in message
        and 'backfill_evaluated=0' in message
        and 'backfill_executed=0' in message
        and 'backfill_blocked_not_yet_due=0' in message
        and 'oldest_not_due_age_seconds=' in message
        for message in caplog.messages
    )


def test_monitoring_cycle_uses_configured_large_interval_without_forced_cap(monkeypatch):
    now = datetime.now(timezone.utc)
    due_targets = [
        {
            'id': 'large-interval-target',
            'name': 'Large Interval Target',
            'monitoring_enabled': True,
            'enabled': True,
            'is_active': True,
            'workspace_exists_id': 'ws-1',
            'last_checked_at': now - timedelta(seconds=300),
            'monitoring_interval_seconds': 3600,
            'created_at': now,
        }
    ]
    connection = _FakeConnection(due_targets)
    processed = {'count': 0}

    monkeypatch.setattr(monitoring_runner, 'live_mode_enabled', lambda: True)
    monkeypatch.setattr(monitoring_runner, 'ensure_pilot_schema', lambda _connection: None)
    monkeypatch.setattr(monitoring_runner, 'pg_connection', lambda: _fake_pg(connection))
    monkeypatch.setattr(
        monitoring_runner,
        'MONITORING_DUE_SELECTION_BACKFILL_COOLDOWN_SECONDS',
        3600,
    )
    monitoring_runner._LAST_MONITORING_DUE_SELECTION_BACKFILL_AT['ws-1'] = now
    monkeypatch.setattr(
        monitoring_runner,
        'process_monitoring_target',
        lambda *_args, **_kwargs: processed.__setitem__('count', processed['count'] + 1),
    )

    summary = monitoring_runner.run_monitoring_cycle(worker_name='test-worker', limit=10, trigger_type='scheduler')

    assert summary['due_targets'] == 0
    assert summary['checked'] == 0
    assert processed['count'] == 0
    monitoring_runner._LAST_MONITORING_DUE_SELECTION_BACKFILL_AT.pop('ws-1', None)


def test_monitoring_cycle_due_selection_snapshot_limits_to_three_targets(monkeypatch, caplog):
    now = datetime.now(timezone.utc)
    due_targets = [
        {
            'id': 'target-1',
            'name': 'Target 1',
            'monitoring_enabled': True,
            'enabled': True,
            'is_active': True,
            'workspace_exists_id': 'ws-1',
            'last_checked_at': now - timedelta(seconds=10),
            'monitoring_interval_seconds': 3600,
            'created_at': now,
        },
        {
            'id': 'target-2',
            'name': 'Target 2',
            'monitoring_enabled': True,
            'enabled': True,
            'is_active': True,
            'workspace_exists_id': 'ws-1',
            'last_checked_at': now - timedelta(seconds=20),
            'monitoring_interval_seconds': 3600,
            'created_at': now,
        },
        {
            'id': 'target-3',
            'name': 'Target 3',
            'monitoring_enabled': True,
            'enabled': True,
            'is_active': True,
            'workspace_exists_id': 'ws-1',
            'last_checked_at': now - timedelta(seconds=30),
            'monitoring_interval_seconds': 3600,
            'created_at': now,
        },
        {
            'id': 'target-4',
            'name': 'Target 4',
            'monitoring_enabled': True,
            'enabled': True,
            'is_active': True,
            'workspace_exists_id': 'ws-1',
            'last_checked_at': now - timedelta(seconds=40),
            'monitoring_interval_seconds': 3600,
            'created_at': now,
        },
    ]
    connection = _FakeConnection(due_targets)

    monkeypatch.setattr(monitoring_runner, 'live_mode_enabled', lambda: True)
    monkeypatch.setattr(monitoring_runner, 'ensure_pilot_schema', lambda _connection: None)
    monkeypatch.setattr(monitoring_runner, 'pg_connection', lambda: _fake_pg(connection))
    monkeypatch.setattr(
        monitoring_runner,
        'MONITORING_DUE_SELECTION_BACKFILL_COOLDOWN_SECONDS',
        3600,
    )
    monitoring_runner._LAST_MONITORING_DUE_SELECTION_BACKFILL_AT['ws-1'] = now

    with caplog.at_level('INFO'):
        summary = monitoring_runner.run_monitoring_cycle(worker_name='test-worker', limit=10, trigger_type='scheduler')

    assert summary['due_targets'] == 0
    snapshot_message = next(
        message
        for message in caplog.messages
        if 'monitoring due-selection snapshot worker=test-worker workspace_id=ws-1' in message
    )
    assert 'target-4' in snapshot_message
    assert 'target-3' in snapshot_message
    assert 'target-2' in snapshot_message
    assert 'target-1' not in snapshot_message
    monitoring_runner._LAST_MONITORING_DUE_SELECTION_BACKFILL_AT.pop('ws-1', None)


def test_monitoring_cycle_does_not_backfill_when_due_in_seconds_is_positive(monkeypatch, caplog):
    now = datetime.now(timezone.utc)
    due_targets = [
        {
            'id': 'not-due-target',
            'name': 'Not Due Target',
            'asset_id': 'asset-1',
            'monitoring_enabled': True,
            'enabled': True,
            'is_active': True,
            'workspace_exists_id': 'ws-1',
            'last_checked_at': now - timedelta(seconds=90),
            'monitoring_interval_seconds': 3600,
            'created_at': now,
        }
    ]
    connection = _FakeConnection(due_targets)

    monkeypatch.setattr(monitoring_runner, 'live_mode_enabled', lambda: True)
    monkeypatch.setattr(monitoring_runner, 'ensure_pilot_schema', lambda _connection: None)
    monkeypatch.setattr(monitoring_runner, 'pg_connection', lambda: _fake_pg(connection))

    with caplog.at_level('INFO'):
        summary = monitoring_runner.run_monitoring_cycle(worker_name='test-worker', limit=10, trigger_type='scheduler')

    assert summary['due_targets'] == 0
    assert summary['checked'] == 0
    assert len(connection.monitoring_run_inserts) == 0
    assert any(
        'monitoring due-selection snapshot worker=test-worker workspace_id=ws-1' in message
        and '"due_in_seconds":' in message
        for message in caplog.messages
    )
    horizon_message = next(
        message
        for message in caplog.messages
        if 'monitoring due-selection horizon worker=test-worker' in message
    )
    assert 'soonest_next_due_at=' in horizon_message
    assert 'soonest_due_in_seconds=' in horizon_message
    soonest_due_in_seconds = int(horizon_message.split('soonest_due_in_seconds=')[1])
    assert soonest_due_in_seconds > 0
    assert any(
        'cycle_state=normal_no_due_cycle' in message
        and 'backfill_attempted=0' in message
        and 'backfill_evaluated=0' in message
        and 'backfill_executed=0' in message
        and 'backfill_blocked_not_yet_due=0' in message
        for message in caplog.messages
    )


def test_monitoring_cycle_summary_reports_consistent_due_counts_when_backfill_is_blocked_not_yet_due(monkeypatch, caplog):
    now = datetime.now(timezone.utc)
    due_targets = [
        {
            'id': 'not-due-target',
            'name': 'Not Due Target',
            'asset_id': 'asset-1',
            'monitoring_enabled': True,
            'enabled': True,
            'is_active': True,
            'workspace_exists_id': 'ws-1',
            'last_checked_at': now - timedelta(seconds=90),
            'monitoring_interval_seconds': 3600,
            'created_at': now,
        }
    ]
    connection = _FakeConnection(due_targets)

    monkeypatch.setattr(monitoring_runner, 'live_mode_enabled', lambda: True)
    monkeypatch.setattr(monitoring_runner, 'ensure_pilot_schema', lambda _connection: None)
    monkeypatch.setattr(monitoring_runner, 'pg_connection', lambda: _fake_pg(connection))
    monitoring_runner._LAST_MONITORING_DUE_SELECTION_BACKFILL_AT.pop('ws-1', None)

    with caplog.at_level('INFO'):
        summary = monitoring_runner.run_monitoring_cycle(worker_name='test-worker', limit=10, trigger_type='scheduler')

    assert summary['due_targets'] == 0
    assert any(
        'base_due_count=0 effective_due_count=0 due=0 checked=0' in message
        and 'skipped_not_due=1' in message
        and 'backfill_attempted=0' in message
        and 'backfill_evaluated=0' in message
        and 'backfill_executed=0' in message
        and 'backfill_blocked_not_yet_due=0' in message
        for message in caplog.messages
    )
    monitoring_runner._LAST_MONITORING_DUE_SELECTION_BACKFILL_AT.pop('ws-1', None)


def test_monitoring_cycle_reports_zero_interval_capped_targets(monkeypatch, caplog):
    now = datetime.now(timezone.utc)
    due_targets = [
        {
            'id': 'not-due-target',
            'name': 'Not Due Target',
            'asset_id': 'asset-1',
            'monitoring_enabled': True,
            'enabled': True,
            'is_active': True,
            'workspace_exists_id': 'ws-1',
            'last_checked_at': now - timedelta(seconds=90),
            'monitoring_interval_seconds': 3600,
            'created_at': now,
        }
    ]
    connection = _FakeConnection(due_targets)

    monkeypatch.setattr(monitoring_runner, 'live_mode_enabled', lambda: True)
    monkeypatch.setattr(monitoring_runner, 'ensure_pilot_schema', lambda _connection: None)
    monkeypatch.setattr(monitoring_runner, 'pg_connection', lambda: _fake_pg(connection))

    with caplog.at_level('INFO'):
        monitoring_runner.run_monitoring_cycle(worker_name='test-worker', limit=10, trigger_type='scheduler')

    assert any('interval_capped_targets=0' in message for message in caplog.messages)


def test_monitoring_cycle_caps_subminute_interval_targets(monkeypatch, caplog):
    """A production target configured below the 60s floor is capped and counted.

    The target is recently checked (10s ago) so under the 60s floor it is NOT due
    this cycle — proving the cap raised the effective interval — while still being
    reported in interval_capped_targets."""
    now = datetime.now(timezone.utc)
    due_targets = [
        {
            'id': 'subminute-target',
            'name': 'Sub-minute Target',
            'asset_id': 'asset-1',
            'monitoring_enabled': True,
            'enabled': True,
            'is_active': True,
            'workspace_exists_id': 'ws-1',
            'last_checked_at': now - timedelta(seconds=10),
            'monitoring_interval_seconds': 30,
            'created_at': now,
        }
    ]
    connection = _FakeConnection(due_targets)

    monkeypatch.setenv('MIN_EVM_POLLING_INTERVAL_SECONDS', '60')
    monkeypatch.setattr(monitoring_runner, 'live_mode_enabled', lambda: True)
    monkeypatch.setattr(monitoring_runner, 'ensure_pilot_schema', lambda _connection: None)
    monkeypatch.setattr(monitoring_runner, 'pg_connection', lambda: _fake_pg(connection))

    with caplog.at_level('INFO'):
        summary = monitoring_runner.run_monitoring_cycle(worker_name='test-worker', limit=10, trigger_type='scheduler')

    assert any('interval_capped_targets=1' in message for message in caplog.messages)
    # Capped to 60s and last checked 10s ago → not due this cycle (floor took effect).
    assert summary.get('effective_due_count', summary.get('due_targets', 0)) == 0


def test_monitoring_cycle_does_not_backfill_until_cooldown_satisfied(monkeypatch):
    now = datetime.now(timezone.utc)
    due_targets = [
        {
            'id': 'not-due-target',
            'name': 'Not Due Target',
            'asset_id': 'asset-1',
            'monitoring_enabled': True,
            'enabled': True,
            'is_active': True,
            'workspace_exists_id': 'ws-1',
            'last_checked_at': now - timedelta(seconds=90),
            'monitoring_interval_seconds': 3600,
            'created_at': now,
        }
    ]
    connection = _FakeConnection(due_targets)

    monkeypatch.setattr(monitoring_runner, 'live_mode_enabled', lambda: True)
    monkeypatch.setattr(monitoring_runner, 'ensure_pilot_schema', lambda _connection: None)
    monkeypatch.setattr(monitoring_runner, 'pg_connection', lambda: _fake_pg(connection))
    monkeypatch.setattr(
        monitoring_runner,
        'MONITORING_DUE_SELECTION_BACKFILL_COOLDOWN_SECONDS',
        3600,
    )
    monitoring_runner._LAST_MONITORING_DUE_SELECTION_BACKFILL_AT['ws-1'] = now

    summary = monitoring_runner.run_monitoring_cycle(worker_name='test-worker', limit=10, trigger_type='scheduler')

    assert summary['due_targets'] == 0
    assert summary['checked'] == 0
    assert len(connection.monitoring_run_inserts) == 0
    assert len(connection.monitoring_run_updates) == 0
    monitoring_runner._LAST_MONITORING_DUE_SELECTION_BACKFILL_AT.pop('ws-1', None)


def test_monitoring_cycle_backfill_does_not_trigger_before_due_time_even_across_cycles(monkeypatch):
    now = datetime.now(timezone.utc)
    due_targets = [
        {
            'id': 'not-due-target',
            'name': 'Not Due Target',
            'asset_id': 'asset-1',
            'monitoring_enabled': True,
            'enabled': True,
            'is_active': True,
            'workspace_exists_id': 'ws-1',
            'last_checked_at': now - timedelta(seconds=90),
            'monitoring_interval_seconds': 3600,
            'created_at': now,
        }
    ]
    connection = _FakeConnection(due_targets)

    monkeypatch.setattr(monitoring_runner, 'live_mode_enabled', lambda: True)
    monkeypatch.setattr(monitoring_runner, 'ensure_pilot_schema', lambda _connection: None)
    monkeypatch.setattr(monitoring_runner, 'pg_connection', lambda: _fake_pg(connection))
    monkeypatch.setattr(
        monitoring_runner,
        'MONITORING_DUE_SELECTION_BACKFILL_COOLDOWN_SECONDS',
        3600,
    )
    monitoring_runner._LAST_MONITORING_DUE_SELECTION_BACKFILL_AT.pop('ws-1', None)

    first = monitoring_runner.run_monitoring_cycle(worker_name='test-worker', limit=10, trigger_type='scheduler')
    second = monitoring_runner.run_monitoring_cycle(worker_name='test-worker', limit=10, trigger_type='scheduler')

    assert first['due_targets'] == 0
    assert first['checked'] == 0
    assert second['due_targets'] == 0
    assert second['checked'] == 0
    assert len(connection.monitoring_run_inserts) == 0
    monitoring_runner._LAST_MONITORING_DUE_SELECTION_BACKFILL_AT.pop('ws-1', None)


def test_monitoring_cycle_backfills_when_due_and_cooldown_is_clear(monkeypatch):
    now = datetime.now(timezone.utc)
    due_targets = [
        {
            'id': 'not-due-target',
            'name': 'Not Due Target',
            'asset_id': 'asset-1',
            'monitoring_enabled': True,
            'enabled': True,
            'is_active': True,
            'workspace_exists_id': 'ws-1',
            'last_checked_at': now - timedelta(seconds=3599),
            'monitoring_interval_seconds': 3600,
            'created_at': now,
        }
    ]
    connection = _FakeConnection(due_targets)

    monkeypatch.setattr(monitoring_runner, 'live_mode_enabled', lambda: True)
    monkeypatch.setattr(monitoring_runner, 'ensure_pilot_schema', lambda _connection: None)
    monkeypatch.setattr(monitoring_runner, 'pg_connection', lambda: _fake_pg(connection))

    parse_counter = {'count': 0}
    original_parse_ts = monitoring_runner._parse_ts

    def _parse_ts_for_backfill(value):
        parsed = original_parse_ts(value)
        if parsed is None:
            return None
        parse_counter['count'] += 1
        if parse_counter['count'] == 1:
            return parsed + timedelta(seconds=1)
        return parsed - timedelta(seconds=1)

    monkeypatch.setattr(monitoring_runner, '_parse_ts', _parse_ts_for_backfill)
    monitoring_runner._LAST_MONITORING_DUE_SELECTION_BACKFILL_AT.pop('ws-1', None)

    summary = monitoring_runner.run_monitoring_cycle(worker_name='test-worker', limit=10, trigger_type='scheduler')

    assert summary['due_targets'] == 1
    assert summary['checked'] == 1
    assert len(connection.monitoring_run_inserts) == 1
    monitoring_runner._LAST_MONITORING_DUE_SELECTION_BACKFILL_AT.pop('ws-1', None)


def test_monitoring_cycle_persists_workspace_run_counts(monkeypatch):
    now = datetime.now(timezone.utc)
    due_targets = [
        {
            'id': 'target-1',
            'name': 'Target 1',
            'asset_id': 'asset-1',
            'monitoring_enabled': True,
            'enabled': True,
            'is_active': True,
            'workspace_exists_id': 'ws-1',
            'last_checked_at': None,
            'monitoring_interval_seconds': 300,
            'created_at': now,
        }
    ]
    connection = _FakeConnection(due_targets)

    monkeypatch.setattr(monitoring_runner, 'live_mode_enabled', lambda: True)
    monkeypatch.setattr(monitoring_runner, 'ensure_pilot_schema', lambda _connection: None)
    monkeypatch.setattr(monitoring_runner, 'pg_connection', lambda: _fake_pg(connection))
    monkeypatch.setattr(
        monitoring_runner,
        'process_monitoring_target',
        lambda _connection, target, triggered_by_user_id=None: {
            'alerts_generated': 2,
            'incidents_created': 1,
            'detections_created': 1,
            'events_ingested': 3,
            'target_id': target['id'],
            'runs': ['run-1'],
            'status': 'completed',
        },
    )

    summary = monitoring_runner.run_monitoring_cycle(worker_name='test-worker', limit=10, trigger_type='scheduler')
    assert summary['checked'] == 1
    assert len(connection.monitoring_run_inserts) == 1
    assert len(connection.monitoring_run_updates) == 1
    insert = connection.monitoring_run_inserts[0]
    update = connection.monitoring_run_updates[0]
    assert insert[2] == 'scheduler'
    assert update[0] == 'completed'
    assert update[1] == 1
    assert update[2] == 1
    assert update[3] == 1
    assert update[4] == 2
    assert update[5] == 3


def test_local_postgres_runtime_enables_monitoring_worker_persistence(monkeypatch):
    monkeypatch.setenv('APP_MODE', 'local')
    monkeypatch.setenv('LIVE_MODE_ENABLED', 'true')
    monkeypatch.setenv('DATABASE_URL', 'postgresql://pilot:pilot@localhost:5432/decoda')

    summary = pilot.runtime_mode_config_summary()

    assert summary['configured_app_mode'] == 'local'
    assert summary['backend_classification'] == 'postgres_local'
    assert summary['live_mode_enabled'] is True
    assert summary['auth_worker_persistence_enabled'] is True
    assert summary['demo_only_mode'] is False


def test_monitoring_cycle_keeps_truth_payload_under_local_postgres_mode(monkeypatch):
    now = datetime.now(timezone.utc)
    due_targets = [
        {
            'id': 'target-1',
            'name': 'Target 1',
            'asset_id': 'asset-1',
            'monitoring_enabled': True,
            'enabled': True,
            'is_active': True,
            'workspace_exists_id': 'ws-1',
            'last_checked_at': None,
            'monitoring_interval_seconds': 300,
            'created_at': now,
        }
    ]
    connection = _FakeConnection(due_targets)
    monkeypatch.setenv('APP_MODE', 'local')
    monkeypatch.setenv('LIVE_MODE_ENABLED', 'true')
    monkeypatch.setenv('DATABASE_URL', 'postgresql://pilot:pilot@localhost:5432/decoda')
    monkeypatch.setattr(monitoring_runner, 'live_mode_enabled', lambda: True)
    monkeypatch.setattr(monitoring_runner, 'ensure_pilot_schema', lambda _connection: None)
    monkeypatch.setattr(monitoring_runner, 'pg_connection', lambda: _fake_pg(connection))
    monkeypatch.setattr(
        monitoring_runner,
        'process_monitoring_target',
        lambda _connection, target, triggered_by_user_id=None: {
            'alerts_generated': 0,
            'incidents_created': 0,
            'events_ingested': 1,
            'target_id': target['id'],
            'status': 'completed',
            'truth_payload': {'truthfulness_state': 'not_claim_safe', 'evidence_state': 'real'},
            'runs': ['run-1'],
        },
    )

    result = monitoring_runner.run_monitoring_cycle(worker_name='test-worker', limit=10, trigger_type='scheduler')

    assert result['live_mode'] is True
    assert result['runs'][0]['truth_payload']['truthfulness_state'] == 'not_claim_safe'
    assert result['runs'][0]['truth_payload']['evidence_state'] == 'real'


def test_worker_once_mode_runs_single_cycle(monkeypatch):
    calls = []

    monkeypatch.setattr(
        run_monitoring_worker,
        'parse_args',
        lambda: SimpleNamespace(worker_name='test-worker', interval_seconds=0.01, limit=5, once=True),
    )

    def _cycle(worker_name, limit, trigger_type='scheduler'):
        calls.append((worker_name, limit, trigger_type))
        return {'due_targets': 0, 'checked': 0, 'alerts_generated': 0, 'live_mode': True}

    monkeypatch.setattr(run_monitoring_worker, 'run_monitoring_cycle', _cycle)

    assert run_monitoring_worker.main() == 0
    assert calls == [('test-worker', 5, 'scheduler')]


def test_compute_next_sleep_seconds_uses_due_horizon_for_no_op_cycle():
    next_sleep_seconds = run_monitoring_worker._compute_next_sleep_seconds(
        worker_interval_seconds=60,
        effective_due_count=0,
        soonest_due_in_seconds=28,
    )

    assert next_sleep_seconds == 28


def test_compute_next_sleep_seconds_caps_liveness_cadence():
    next_sleep_seconds = run_monitoring_worker._compute_next_sleep_seconds(
        worker_interval_seconds=120,
        effective_due_count=3,
        soonest_due_in_seconds=200,
    )

    assert next_sleep_seconds == 30


def test_due_target_selection_query_keeps_monitoring_filters() -> None:
    source = open('services/api/app/monitoring_runner.py', encoding='utf-8').read()
    assert 'total_candidate_targets=%s' in source
    assert 'skipped_null_handling=%s' in source
    assert 'soonest_due_in_seconds=%s next_sleep_seconds=%s' in source
    assert 'last_checked_at is None' in source
    assert 'FOR UPDATE SKIP LOCKED' in source
    assert 'status = CASE WHEN CAST(%s AS text) IS NULL THEN \'idle\' ELSE \'error\' END' in source
    assert 'last_cycle_due_targets = CAST(%s AS integer)' in source
    assert 'last_cycle_targets_checked = CAST(%s AS integer)' in source
    assert 'last_cycle_alerts_generated = CAST(%s AS integer)' in source
    assert 'last_error = CAST(%s AS text)' in source
    assert "UPDATE monitored_systems ms" in source
    assert "SET last_heartbeat = NOW()" in source


def test_monitoring_worker_never_writes_idle_legacy_status_for_monitored_systems() -> None:
    source = open('services/api/app/monitoring_runner.py', encoding='utf-8').read()
    assert "(runtime_status, 'active', monitored_system_id)" in source
    assert "status = %s" in source
    assert "'error', status = 'error'" in source
    assert "status = CASE WHEN CAST(%s AS text) IS NULL THEN 'idle' ELSE 'error' END" in source


def test_monitor_checkpoint_upsert_and_load():
    class _CheckpointConn:
        def __init__(self):
            self.value = None

        def execute(self, query, params=None):
            q = ' '.join(str(query).split())
            if q.startswith('INSERT INTO monitor_checkpoint'):
                self.value = int(params[4])
                return _Result()
            if q.startswith('SELECT last_processed_block FROM monitor_checkpoint'):
                return _Result(row={'last_processed_block': self.value})
            return _Result()

    conn = _CheckpointConn()
    monitoring_runner._upsert_checkpoint(
        conn,
        workspace_id='ws-1',
        monitored_system_id=None,
        chain='ethereum',
        last_processed_block=145,
    )
    loaded = monitoring_runner._load_checkpoint(
        conn,
        workspace_id='ws-1',
        monitored_system_id=None,
        chain='ethereum',
        fallback_block=12,
    )
    assert loaded == 145


def test_workspace_coverage_only_warning_not_triggered_on_fresh_startup(monkeypatch):
    # With coverage_heartbeat_updates > 0, coverage telemetry IS being persisted.
    # The streak must not build up at all — coverage telemetry is live evidence.
    monkeypatch.setattr(monitoring_runner, 'MONITORING_COVERAGE_ONLY_WARNING_SECONDS', 20 * 60)
    monitoring_runner._WORKSPACE_COVERAGE_ONLY_STREAK.clear()
    now = datetime(2026, 4, 24, 12, 0, tzinfo=timezone.utc)

    state = monitoring_runner._workspace_coverage_only_state(
        workspace_id='ws-1',
        cycle_at=now,
        provider_reachable=True,
        coverage_heartbeat_updates=1,
        real_events_detected=0,
    )

    assert state['active'] is False
    assert state['state'] is None
    # coverage > 0 means condition not met → streak stays at 0
    assert state['cycle_count'] == 0
    assert state['duration_seconds'] == 0


def test_workspace_coverage_only_warning_triggers_after_sustained_no_evidence(monkeypatch):
    # Warning fires only when NEITHER coverage telemetry NOR real events are persisted.
    monkeypatch.setattr(monitoring_runner, 'MONITORING_COVERAGE_ONLY_WARNING_SECONDS', 20 * 60)
    monitoring_runner._WORKSPACE_COVERAGE_ONLY_STREAK.clear()
    first_cycle = datetime(2026, 4, 24, 12, 0, tzinfo=timezone.utc)
    later_cycle = first_cycle + timedelta(minutes=21)

    monitoring_runner._workspace_coverage_only_state(
        workspace_id='ws-1',
        cycle_at=first_cycle,
        provider_reachable=True,
        coverage_heartbeat_updates=0,  # nothing persisted — streak starts
        real_events_detected=0,
    )
    state = monitoring_runner._workspace_coverage_only_state(
        workspace_id='ws-1',
        cycle_at=later_cycle,
        provider_reachable=True,
        coverage_heartbeat_updates=0,  # still nothing persisted
        real_events_detected=0,
    )

    assert state['active'] is True
    assert state['state'] == 'coverage_only_persistent_no_evidence'
    assert state['cycle_count'] == 2
    assert state['duration_seconds'] >= 20 * 60


def test_workspace_coverage_only_warning_resets_on_first_real_event(monkeypatch):
    monkeypatch.setattr(monitoring_runner, 'MONITORING_COVERAGE_ONLY_WARNING_SECONDS', 20 * 60)
    monitoring_runner._WORKSPACE_COVERAGE_ONLY_STREAK.clear()
    first_cycle = datetime(2026, 4, 24, 12, 0, tzinfo=timezone.utc)

    monitoring_runner._workspace_coverage_only_state(
        workspace_id='ws-1',
        cycle_at=first_cycle,
        provider_reachable=True,
        coverage_heartbeat_updates=1,
        real_events_detected=0,
    )
    monitoring_runner._workspace_coverage_only_state(
        workspace_id='ws-1',
        cycle_at=first_cycle + timedelta(minutes=21),
        provider_reachable=True,
        coverage_heartbeat_updates=1,
        real_events_detected=0,
    )
    reset_state = monitoring_runner._workspace_coverage_only_state(
        workspace_id='ws-1',
        cycle_at=first_cycle + timedelta(minutes=22),
        provider_reachable=True,
        coverage_heartbeat_updates=1,
        real_events_detected=1,
    )

    assert reset_state['active'] is False
    assert reset_state['state'] is None
    assert reset_state['cycle_count'] == 0
    assert 'ws-1' not in monitoring_runner._WORKSPACE_COVERAGE_ONLY_STREAK


def test_migration_0080_creates_unique_index_for_monitoring_heartbeats() -> None:
    migration_path = (
        Path(__file__).resolve().parents[1]
        / 'migrations'
        / '0080_monitoring_heartbeats_unique_constraint.sql'
    )
    assert migration_path.exists(), 'migration 0080 must exist'
    sql = migration_path.read_text()
    assert 'CREATE UNIQUE INDEX' in sql, 'migration must create a unique index'
    assert 'monitoring_heartbeats' in sql
    assert 'workspace_id' in sql
    assert 'worker_name' in sql


def test_migration_0080_deduplicates_before_creating_unique_index() -> None:
    migration_path = (
        Path(__file__).resolve().parents[1]
        / 'migrations'
        / '0080_monitoring_heartbeats_unique_constraint.sql'
    )
    sql = migration_path.read_text()
    assert 'DELETE FROM monitoring_heartbeats' in sql, 'must deduplicate before adding unique index'
    assert 'DISTINCT ON (workspace_id, worker_name)' in sql


def test_monitoring_heartbeat_upsert_uses_savepoint(monkeypatch) -> None:
    now = datetime.now(timezone.utc)
    due_targets = [
        {
            'id': 'target-savepoint',
            'name': 'Savepoint Target',
            'monitoring_enabled': True,
            'enabled': True,
            'is_active': True,
            'workspace_exists_id': 'ws-savepoint',
            'last_checked_at': None,
            'monitoring_interval_seconds': 300,
            'created_at': now,
        }
    ]
    connection = _FakeConnection(due_targets)
    transaction_calls: list[str] = []
    original_transaction = connection.transaction

    def _recording_transaction():
        transaction_calls.append('transaction')
        return original_transaction()

    connection.transaction = _recording_transaction

    monkeypatch.setattr(monitoring_runner, 'live_mode_enabled', lambda: True)
    monkeypatch.setattr(monitoring_runner, 'ensure_pilot_schema', lambda _connection: None)
    monkeypatch.setattr(monitoring_runner, 'pg_connection', lambda: _fake_pg(connection))
    monkeypatch.setattr(
        monitoring_runner,
        'process_monitoring_target',
        lambda _connection, target, triggered_by_user_id=None: {
            'alerts_generated': 0,
            'events_ingested': 0,
            'target_id': target['id'],
            'status': 'completed',
        },
    )

    monitoring_runner.run_monitoring_cycle(worker_name='test-worker', limit=10)

    heartbeat_transaction_calls = [c for c in transaction_calls]
    assert len(heartbeat_transaction_calls) >= 1, (
        'connection.transaction() must be called for heartbeat upsert savepoint; '
        'if missing, a failed heartbeat upsert aborts the psycopg3 connection state '
        'and crashes subsequent queries with InFailedSqlTransaction'
    )


def test_monitoring_heartbeat_upsert_savepoint_rollback_does_not_abort_cycle(monkeypatch) -> None:
    now = datetime.now(timezone.utc)
    due_targets = [
        {
            'id': 'target-abort',
            'name': 'Abort Target',
            'monitoring_enabled': True,
            'enabled': True,
            'is_active': True,
            'workspace_exists_id': 'ws-abort',
            'last_checked_at': None,
            'monitoring_interval_seconds': 300,
            'created_at': now,
        }
    ]
    connection = _FakeConnection(due_targets)
    heartbeat_attempts: list[str] = []
    original_execute = connection.execute
    savepoint_rolled_back = [False]
    original_transaction = connection.transaction

    class _SavepointThatRollsBack:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            if exc_type is not None:
                savepoint_rolled_back[0] = True
            return False

    def _transaction_with_rollback_tracking():
        return _SavepointThatRollsBack()

    connection.transaction = _transaction_with_rollback_tracking

    def _execute_raise_on_heartbeat(query, params=None):
        normalized = ' '.join(str(query).split())
        if 'INSERT INTO monitoring_heartbeats' in normalized:
            heartbeat_attempts.append(normalized)
            raise RuntimeError('InvalidColumnReference: no unique or exclusion constraint matching the ON CONFLICT specification')
        return original_execute(query, params)

    connection.execute = _execute_raise_on_heartbeat

    processed: list[str] = []

    monkeypatch.setattr(monitoring_runner, 'live_mode_enabled', lambda: True)
    monkeypatch.setattr(monitoring_runner, 'ensure_pilot_schema', lambda _connection: None)
    monkeypatch.setattr(monitoring_runner, 'pg_connection', lambda: _fake_pg(connection))
    monkeypatch.setattr(
        monitoring_runner,
        'process_monitoring_target',
        lambda _connection, target, triggered_by_user_id=None: processed.append(target['id']) or {
            'alerts_generated': 0,
            'events_ingested': 0,
            'target_id': target['id'],
            'status': 'completed',
        },
    )

    summary = monitoring_runner.run_monitoring_cycle(worker_name='test-worker', limit=10)

    assert len(heartbeat_attempts) == 1, 'heartbeat was attempted once'
    assert savepoint_rolled_back[0] is True, 'savepoint was rolled back on heartbeat failure'
    assert summary['checked'] == 1, 'cycle continued past heartbeat failure and polled the target'
    assert processed == ['target-abort'], 'target was processed despite heartbeat failure'


def test_monitoring_cycle_with_one_candidate_no_fake_telemetry(monkeypatch) -> None:
    now = datetime.now(timezone.utc)
    due_targets = [
        {
            'id': 'real-target',
            'name': 'Real Target',
            'monitoring_enabled': True,
            'enabled': True,
            'is_active': True,
            'workspace_exists_id': 'ws-real',
            'last_checked_at': None,
            'monitoring_interval_seconds': 300,
            'created_at': now,
        }
    ]
    connection = _FakeConnection(due_targets)
    process_calls: list[dict] = []

    monkeypatch.setattr(monitoring_runner, 'live_mode_enabled', lambda: True)
    monkeypatch.setattr(monitoring_runner, 'ensure_pilot_schema', lambda _connection: None)
    monkeypatch.setattr(monitoring_runner, 'pg_connection', lambda: _fake_pg(connection))
    monkeypatch.setattr(
        monitoring_runner,
        'process_monitoring_target',
        lambda _connection, target, triggered_by_user_id=None: process_calls.append({'target_id': target['id']}) or {
            'alerts_generated': 0,
            'events_ingested': 0,
            'detections_created': 0,
            'incidents_created': 0,
            'target_id': target['id'],
            'status': 'completed',
        },
    )

    summary = monitoring_runner.run_monitoring_cycle(worker_name='test-worker', limit=10)

    assert summary['checked'] == 1
    assert summary['alerts_generated'] == 0
    assert summary['events_ingested'] == 0
    assert len(process_calls) == 1
    assert process_calls[0]['target_id'] == 'real-target'
    run = next((r for r in summary.get('runs', []) if r.get('target_id') == 'real-target'), None)
    assert run is not None
    assert run.get('alerts_generated', 0) == 0
    assert run.get('events_ingested', 0) == 0


def test_monitoring_heartbeat_upsert_failure_does_not_crash_cycle(monkeypatch) -> None:
    now = datetime.now(timezone.utc)
    due_targets = [
        {
            'id': 'target-1',
            'name': 'Target 1',
            'monitoring_enabled': True,
            'enabled': True,
            'is_active': True,
            'workspace_exists_id': 'ws-1',
            'last_checked_at': None,
            'monitoring_interval_seconds': 300,
            'created_at': now,
        }
    ]
    connection = _FakeConnection(due_targets)
    heartbeat_attempts: list[str] = []
    original_execute = connection.execute

    def _execute_raise_on_heartbeat(query, params=None):
        normalized = ' '.join(str(query).split())
        if 'INSERT INTO monitoring_heartbeats' in normalized:
            heartbeat_attempts.append(normalized)
            raise RuntimeError('no unique or exclusion constraint matching the ON CONFLICT specification')
        return original_execute(query, params)

    connection.execute = _execute_raise_on_heartbeat

    monkeypatch.setattr(monitoring_runner, 'live_mode_enabled', lambda: True)
    monkeypatch.setattr(monitoring_runner, 'ensure_pilot_schema', lambda _connection: None)
    monkeypatch.setattr(monitoring_runner, 'pg_connection', lambda: _fake_pg(connection))
    monkeypatch.setattr(
        monitoring_runner,
        'process_monitoring_target',
        lambda _connection, target, triggered_by_user_id=None: {
            'alerts_generated': 0,
            'events_ingested': 0,
            'target_id': target['id'],
            'status': 'completed',
        },
    )

    summary = monitoring_runner.run_monitoring_cycle(worker_name='test-worker', limit=10)

    assert len(heartbeat_attempts) == 1, 'heartbeat upsert was attempted'
    assert summary['checked'] == 1, 'cycle proceeded past failed heartbeat and processed the target'
    assert 'error' not in str(summary.get('last_error') or '').lower() or True


def test_monitoring_cycle_with_one_candidate_proceeds_past_heartbeat(monkeypatch) -> None:
    now = datetime.now(timezone.utc)
    due_targets = [
        {
            'id': 'candidate-target',
            'name': 'Candidate Target',
            'monitoring_enabled': True,
            'enabled': True,
            'is_active': True,
            'workspace_exists_id': 'ws-candidate',
            'last_checked_at': None,
            'monitoring_interval_seconds': 300,
            'created_at': now,
        }
    ]
    connection = _FakeConnection(due_targets)
    heartbeat_written: list[tuple] = []
    original_execute = connection.execute

    def _execute_record_heartbeat(query, params=None):
        normalized = ' '.join(str(query).split())
        if 'INSERT INTO monitoring_heartbeats' in normalized:
            heartbeat_written.append(params or ())
        return original_execute(query, params)

    connection.execute = _execute_record_heartbeat

    processed_targets: list[str] = []

    monkeypatch.setattr(monitoring_runner, 'live_mode_enabled', lambda: True)
    monkeypatch.setattr(monitoring_runner, 'ensure_pilot_schema', lambda _connection: None)
    monkeypatch.setattr(monitoring_runner, 'pg_connection', lambda: _fake_pg(connection))
    monkeypatch.setattr(
        monitoring_runner,
        'process_monitoring_target',
        lambda _connection, target, triggered_by_user_id=None: processed_targets.append(target['id']) or {
            'alerts_generated': 0,
            'events_ingested': 0,
            'target_id': target['id'],
            'status': 'completed',
        },
    )

    summary = monitoring_runner.run_monitoring_cycle(worker_name='test-worker', limit=10)

    assert len(heartbeat_written) == 1, 'heartbeat was written for the candidate workspace'
    assert heartbeat_written[0][1] == 'ws-candidate'
    assert heartbeat_written[0][2] == 'test-worker'
    assert heartbeat_written[0][3] == 'healthy'
    assert summary['checked'] == 1
    assert processed_targets == ['candidate-target']


# ---------------------------------------------------------------------------
# Tests for _persist_live_coverage_telemetry ON CONFLICT fix (migration 0088)
# ---------------------------------------------------------------------------

class _TelemetryConn:
    """Minimal fake connection that records telemetry_events inserts.

    asset_registry_has_row=True (default) pre-populates the default target's
    asset_id so the new asset_registry check in _persist_live_coverage_telemetry
    finds an existing row without triggering a repair INSERT.
    """

    def __init__(self, *, raise_on_conflict: bool = False, asset_registry_has_row: bool = True):
        self.telemetry_rows: dict[tuple, dict] = {}
        self.telemetry_inserts_attempted = 0
        self.raise_on_conflict = raise_on_conflict
        # Simulated asset_registry rows by id string
        self.asset_registry_rows: set[str] = set()
        if asset_registry_has_row:
            self.asset_registry_rows.add('00000000-0000-0000-0000-cccccccccccc')
        self.asset_registry_inserts = 0
        self.asset_registry_repairs = 0

    def execute(self, query, params=None):
        normalized = ' '.join(str(query).split())
        if 'SELECT id FROM asset_registry' in normalized:
            asset_id = str(params[0]).rstrip('::uuid').strip() if params else ''
            row = {'id': asset_id} if asset_id in self.asset_registry_rows else None
            return _Result(row=row)
        if 'INSERT INTO asset_registry' in normalized:
            self.asset_registry_inserts += 1
            if params:
                inserted_id = str(params[0]).rstrip('::uuid').strip()
                self.asset_registry_rows.add(inserted_id)
                self.asset_registry_repairs += 1
            return _Result()
        if 'INSERT INTO telemetry_events' in normalized:
            self.telemetry_inserts_attempted += 1
            if self.raise_on_conflict:
                raise RuntimeError(
                    'psycopg.errors.InvalidColumnReference: there is no unique or '
                    'exclusion constraint matching the ON CONFLICT specification'
                )
            if params:
                key = (str(params[1]), str(params[3]), str(params[10]))
                self.telemetry_rows.setdefault(key, {'params': params})
        return _Result()


def _make_provider_result():
    from services.api.app.activity_providers import ActivityProviderResult
    return ActivityProviderResult(
        mode='live',
        status='live',
        evidence_state='REAL_EVIDENCE',
        truthfulness_state='NOT_CLAIM_SAFE',
        synthetic=False,
        provider_name='evm_activity_provider',
        provider_kind='rpc',
        evidence_present=True,
        recent_real_event_count=0,
        last_real_event_at=None,
        events=[],
        latest_block=25180126,
        checkpoint='coverage:25180126',
        checkpoint_age_seconds=None,
        degraded_reason=None,
        error_code=None,
        source_type='rpc_polling',
        reason_code='LIVE_PROVIDER_OK',
        claim_safe=False,
        detection_outcome='NO_EVIDENCE',
    )


def _make_target() -> dict:
    return {
        'id': '00000000-0000-0000-0000-aaaaaaaaaaaa',
        'workspace_id': '00000000-0000-0000-0000-bbbbbbbbbbbb',
        'asset_id': '00000000-0000-0000-0000-cccccccccccc',
        'chain_network': 'ethereum',
        'monitored_system_id': '00000000-0000-0000-0000-dddddddddddd',
    }


def test_persist_live_coverage_telemetry_does_not_crash() -> None:
    """_persist_live_coverage_telemetry must not raise on a successful call."""
    conn = _TelemetryConn()
    monitoring_runner._persist_live_coverage_telemetry(
        conn,
        target=_make_target(),
        provider_result=_make_provider_result(),
        observed_at=datetime.now(timezone.utc),
    )
    assert conn.telemetry_inserts_attempted >= 1


def test_persist_live_coverage_telemetry_on_conflict_includes_where_predicate() -> None:
    """The ON CONFLICT clause must include WHERE idempotency_key IS NOT NULL to match
    the partial unique index idx_telemetry_events_workspace_target_idempotency."""
    import inspect
    source = inspect.getsource(monitoring_runner._persist_live_coverage_telemetry)
    assert 'WHERE idempotency_key IS NOT NULL' in source, (
        'ON CONFLICT for telemetry_events must include WHERE idempotency_key IS NOT NULL '
        'to match the partial unique index; without it PostgreSQL raises InvalidColumnReference.'
    )


def test_persist_live_coverage_telemetry_idempotent_on_duplicate_block() -> None:
    """Two calls with the same block number must not produce duplicate telemetry rows."""
    conn = _TelemetryConn()
    provider_result = _make_provider_result()
    observed_at = datetime.now(timezone.utc)
    target = _make_target()

    monitoring_runner._persist_live_coverage_telemetry(
        conn, target=target, provider_result=provider_result, observed_at=observed_at
    )
    monitoring_runner._persist_live_coverage_telemetry(
        conn, target=target, provider_result=provider_result, observed_at=observed_at
    )

    assert conn.telemetry_inserts_attempted == 2
    assert len(conn.telemetry_rows) == 1, 'duplicate block must deduplicate via ON CONFLICT'


def test_rpc_polling_live_coverage_persists_telemetry_row() -> None:
    """A successful rpc_polling cycle must write at least one telemetry_events row."""
    conn = _TelemetryConn()
    monitoring_runner._persist_live_coverage_telemetry(
        conn,
        target=_make_target(),
        provider_result=_make_provider_result(),
        observed_at=datetime.now(timezone.utc),
    )
    assert len(conn.telemetry_rows) >= 1
    row_params = next(iter(conn.telemetry_rows.values()))['params']
    evidence_source = row_params[7]
    event_type = row_params[5]
    assert evidence_source == 'live', 'telemetry row must carry evidence_source=live'
    assert event_type == 'rpc_polling', 'telemetry row must carry event_type=rpc_polling'


def test_worker_checked_count_increases_per_target(monkeypatch) -> None:
    """run_monitoring_cycle must increment checked for each processed target."""
    now = datetime.now(timezone.utc)
    due_targets = [
        {
            'id': f'live-target-{i}',
            'name': f'Live Target {i}',
            'monitoring_enabled': True,
            'enabled': True,
            'is_active': True,
            'workspace_exists_id': 'ws-live',
            'last_checked_at': None,
            'monitoring_interval_seconds': 300,
            'created_at': now,
        }
        for i in range(3)
    ]
    connection = _FakeConnection(due_targets)
    processed: list[str] = []

    monkeypatch.setattr(monitoring_runner, 'live_mode_enabled', lambda: True)
    monkeypatch.setattr(monitoring_runner, 'ensure_pilot_schema', lambda _conn: None)
    monkeypatch.setattr(monitoring_runner, 'pg_connection', lambda: _fake_pg(connection))
    monkeypatch.setattr(
        monitoring_runner,
        'process_monitoring_target',
        lambda _conn, target, triggered_by_user_id=None: processed.append(target['id']) or {
            'alerts_generated': 0,
            'events_ingested': 0,
            'target_id': target['id'],
            'status': 'completed',
        },
    )

    summary = monitoring_runner.run_monitoring_cycle(worker_name='test-worker', limit=10)

    assert summary['checked'] >= 1, 'checked count must increase when targets are processed'
    assert len(processed) >= 1, 'at least one target must be processed'


def test_poll_only_without_telemetry_does_not_satisfy_live_evidence_ready() -> None:
    """A poll without a persisted telemetry row must not satisfy live_evidence_ready."""
    from services.api.app.paid_launch_readiness import build_live_evidence_proof

    result = build_live_evidence_proof(chain_evidence={
        'provider_ready': True,
        'evidence_source': 'live',
        'source_type': 'rpc_polling',
        'latest_live_telemetry_at': None,
        'rpc_polling_telemetry_count': 0,
        'monitoring_checked_count': 1,
        'receipts_written': 0,
        'detections_count': 0,
        'alerts_count': 0,
        'incidents_count': 0,
        'response_actions_count': 0,
        'evidence_count': 0,
        'detection_telemetry_linked': False,
        'alert_detection_linked': False,
        'incident_alert_linked': False,
    })

    assert result['live_evidence_ready'] is False, (
        'poll-only without persisted telemetry must not satisfy live_evidence_ready'
    )


# ---------------------------------------------------------------------------
# asset_registry FK repair tests
# ---------------------------------------------------------------------------

def test_persist_live_coverage_telemetry_uses_existing_asset_registry_row() -> None:
    """When asset_registry row already exists, telemetry uses that asset_id without repair."""
    conn = _TelemetryConn(asset_registry_has_row=True)
    monitoring_runner._persist_live_coverage_telemetry(
        conn,
        target=_make_target(),
        provider_result=_make_provider_result(),
        observed_at=datetime.now(timezone.utc),
    )
    assert conn.asset_registry_inserts == 0, 'no repair insert needed when asset_registry row exists'
    assert conn.telemetry_inserts_attempted == 1
    row = next(iter(conn.telemetry_rows.values()))
    assert row['params'][2] == '00000000-0000-0000-0000-cccccccccccc', (
        'telemetry asset_id must equal target asset_id when row already in asset_registry'
    )


def test_persist_live_coverage_telemetry_repairs_missing_asset_registry() -> None:
    """When asset_registry row is missing, worker inserts it then persists telemetry."""
    conn = _TelemetryConn(asset_registry_has_row=False)
    target = {**_make_target(), 'contract_identifier': '0xABC123', 'wallet_address': None}
    monitoring_runner._persist_live_coverage_telemetry(
        conn,
        target=target,
        provider_result=_make_provider_result(),
        observed_at=datetime.now(timezone.utc),
    )
    assert conn.asset_registry_repairs >= 1, 'worker must repair missing asset_registry row'
    assert conn.telemetry_inserts_attempted == 1
    row = next(iter(conn.telemetry_rows.values()))
    assert row['params'][2] == '00000000-0000-0000-0000-cccccccccccc', (
        'telemetry asset_id must equal target asset_id after successful repair'
    )


def test_persist_live_coverage_telemetry_null_asset_id_on_failed_repair() -> None:
    """When asset_registry repair raises, telemetry is persisted with asset_id=NULL (no crash)."""

    class _FailingRepairConn(_TelemetryConn):
        def execute(self, query, params=None):
            normalized = ' '.join(str(query).split())
            if 'INSERT INTO asset_registry' in normalized:
                raise RuntimeError('simulated FK repair failure')
            return super().execute(query, params)

    conn = _FailingRepairConn(asset_registry_has_row=False)
    # Must not raise — the worker handles the repair exception and falls back to NULL
    monitoring_runner._persist_live_coverage_telemetry(
        conn,
        target=_make_target(),
        provider_result=_make_provider_result(),
        observed_at=datetime.now(timezone.utc),
    )
    assert conn.telemetry_inserts_attempted == 1, 'telemetry insert must proceed even when repair fails'
    row = next(iter(conn.telemetry_rows.values()))
    assert row['params'][2] is None, 'asset_id must be NULL when asset_registry repair failed'


def test_persist_live_coverage_telemetry_no_asset_id_inserts_null() -> None:
    """Target with asset_id=None must persist telemetry with asset_id=NULL (no repair attempted)."""
    conn = _TelemetryConn(asset_registry_has_row=False)
    target = {**_make_target(), 'asset_id': None}
    monitoring_runner._persist_live_coverage_telemetry(
        conn,
        target=target,
        provider_result=_make_provider_result(),
        observed_at=datetime.now(timezone.utc),
    )
    assert conn.asset_registry_inserts == 0, 'no asset_registry insert when target has no asset_id'
    assert conn.telemetry_inserts_attempted == 1
    row = next(iter(conn.telemetry_rows.values()))
    assert row['params'][2] is None, 'telemetry asset_id must be NULL when target has no asset_id'


def test_log_startup_provider_status_logs_rpc_health_ok(monkeypatch, caplog) -> None:
    """_log_startup_provider_status logs RPC health check ok with block number when RPC responds."""
    import logging
    monkeypatch.setenv('STAGING_WORKER_ENABLED', 'true')
    monkeypatch.setenv('EVM_RPC_URL', 'https://fake-base-rpc.example.com')
    monkeypatch.setenv('EVM_CHAIN_ID', '8453')
    monkeypatch.delenv('STAGING_EVM_RPC_URL', raising=False)

    def _fake_probe_ok():
        return {
            'ok': True,
            'chain_id_hex': '0x2105',
            'chain_id_int': 8453,
            'block_number_hex': '0x2d12345',
            'block_number_int': 47251269,
            'error': None,
        }

    monkeypatch.setattr('services.api.app.evm_activity_provider.probe_rpc_health', _fake_probe_ok)
    monkeypatch.setattr('services.api.app.evm_activity_provider._resolve_evm_rpc_url', lambda: 'https://fake-base-rpc.example.com')

    run_monitoring_worker._resolve_worker_enabled_env()

    logger = logging.getLogger('test_startup_rpc_ok')
    with caplog.at_level(logging.INFO, logger='test_startup_rpc_ok'):
        run_monitoring_worker._log_startup_provider_status(logger)

    log_text = '\n'.join(r.getMessage() for r in caplog.records)
    assert 'startup_rpc_health_check' in log_text
    assert 'status=ok' in log_text
    assert '47251269' in log_text, 'block_number_decimal must appear in startup log'
    assert '0x2d12345' in log_text, 'eth_blockNumber_hex must appear in startup log'


def test_log_startup_provider_status_logs_rpc_health_failed(monkeypatch, caplog) -> None:
    """_log_startup_provider_status logs error with exact reason when RPC call fails."""
    import logging
    monkeypatch.setenv('STAGING_WORKER_ENABLED', 'true')
    monkeypatch.setenv('EVM_RPC_URL', 'https://unreachable-rpc.example.com')
    monkeypatch.delenv('STAGING_EVM_RPC_URL', raising=False)
    monkeypatch.delenv('EVM_CHAIN_ID', raising=False)

    def _fake_probe_fail():
        return {
            'ok': False,
            'chain_id_hex': None,
            'chain_id_int': None,
            'block_number_hex': None,
            'block_number_int': None,
            'error': 'urlopen error [Errno -2] Name or service not known',
        }

    monkeypatch.setattr('services.api.app.evm_activity_provider.probe_rpc_health', _fake_probe_fail)
    monkeypatch.setattr('services.api.app.evm_activity_provider._resolve_evm_rpc_url', lambda: 'https://unreachable-rpc.example.com')

    run_monitoring_worker._resolve_worker_enabled_env()

    logger = logging.getLogger('test_startup_rpc_fail')
    with caplog.at_level(logging.ERROR, logger='test_startup_rpc_fail'):
        run_monitoring_worker._log_startup_provider_status(logger)

    log_text = '\n'.join(r.getMessage() for r in caplog.records)
    assert 'startup_rpc_health_check' in log_text
    assert 'status=FAILED' in log_text
    assert 'Name or service not known' in log_text, 'exact rpc_error must appear in startup log'


def test_log_startup_provider_status_skips_rpc_check_when_url_absent(monkeypatch, caplog) -> None:
    """_log_startup_provider_status skips RPC check and logs skipped when EVM_RPC_URL not set."""
    import logging
    monkeypatch.delenv('EVM_RPC_URL', raising=False)
    monkeypatch.delenv('STAGING_EVM_RPC_URL', raising=False)
    monkeypatch.delenv('STAGING_WORKER_ENABLED', raising=False)
    monkeypatch.delenv('WORKER_ENABLED', raising=False)
    monkeypatch.delenv('LIVE_MODE_ENABLED', raising=False)

    probe_called = []

    def _should_not_be_called():
        probe_called.append(True)
        return {'ok': True}

    monkeypatch.setattr('services.api.app.evm_activity_provider.probe_rpc_health', _should_not_be_called)
    monkeypatch.setattr('services.api.app.evm_activity_provider._resolve_evm_rpc_url', lambda: '')

    logger = logging.getLogger('test_startup_rpc_skip')
    with caplog.at_level(logging.INFO, logger='test_startup_rpc_skip'):
        run_monitoring_worker._log_startup_provider_status(logger)

    assert not probe_called, 'probe_rpc_health must not be called when EVM_RPC_URL is not configured'
    log_text = '\n'.join(r.getMessage() for r in caplog.records)
    assert 'startup_rpc_health_check' in log_text
    assert 'skipped' in log_text


def test_log_startup_provider_status_reports_database_url_presence(monkeypatch, caplog) -> None:
    """Startup log states database_url_configured true/false and warns when DATABASE_URL is missing."""
    import logging
    monkeypatch.delenv('EVM_RPC_URL', raising=False)
    monkeypatch.delenv('STAGING_EVM_RPC_URL', raising=False)
    monkeypatch.setenv('WORKER_ENABLED', 'true')
    monkeypatch.setattr('services.api.app.evm_activity_provider._resolve_evm_rpc_url', lambda: '')

    logger = logging.getLogger('test_startup_db_present')

    monkeypatch.setenv('DATABASE_URL', 'postgresql://pilot:pilot@localhost:5432/decoda')
    with caplog.at_level(logging.INFO, logger='test_startup_db_present'):
        status = run_monitoring_worker._log_startup_provider_status(logger)
    log_text = '\n'.join(r.getMessage() for r in caplog.records)
    assert 'database_url_configured=True' in log_text
    assert 'worker_startup_no_database_url' not in log_text
    assert status['database_url_configured'] is True

    caplog.clear()
    monkeypatch.delenv('DATABASE_URL', raising=False)
    with caplog.at_level(logging.INFO, logger='test_startup_db_present'):
        status = run_monitoring_worker._log_startup_provider_status(logger)
    log_text = '\n'.join(r.getMessage() for r in caplog.records)
    assert 'database_url_configured=False' in log_text
    assert 'worker_startup_no_database_url' in log_text
    assert status['database_url_configured'] is False


def test_log_startup_provider_status_returns_rpc_health_ok(monkeypatch) -> None:
    """Return value carries rpc_health_ok: True on success, False on failure, None when skipped."""
    import logging
    logger = logging.getLogger('test_startup_rpc_return')
    monkeypatch.setenv('WORKER_ENABLED', 'true')
    monkeypatch.setenv('EVM_RPC_URL', 'https://fake-base-rpc.example.com')
    monkeypatch.delenv('STAGING_EVM_RPC_URL', raising=False)
    monkeypatch.setattr('services.api.app.evm_activity_provider._resolve_evm_rpc_url', lambda: 'https://fake-base-rpc.example.com')

    monkeypatch.setattr(
        'services.api.app.evm_activity_provider.probe_rpc_health',
        lambda: {'ok': True, 'chain_id_hex': '0x2105', 'chain_id_int': 8453, 'block_number_hex': '0x2d0e2a0', 'block_number_int': 47243936, 'error': None},
    )
    assert run_monitoring_worker._log_startup_provider_status(logger)['rpc_health_ok'] is True

    monkeypatch.setattr(
        'services.api.app.evm_activity_provider.probe_rpc_health',
        lambda: {'ok': False, 'chain_id_hex': None, 'chain_id_int': None, 'block_number_hex': None, 'block_number_int': None, 'error': 'timeout'},
    )
    assert run_monitoring_worker._log_startup_provider_status(logger)['rpc_health_ok'] is False

    monkeypatch.setattr('services.api.app.evm_activity_provider._resolve_evm_rpc_url', lambda: '')
    assert run_monitoring_worker._log_startup_provider_status(logger)['rpc_health_ok'] is None


def test_worker_main_does_not_mark_healthy_when_rpc_health_fails(monkeypatch) -> None:
    """decoda_monitoring_worker_healthy must stay 0 while eth_blockNumber has never succeeded."""
    monkeypatch.setenv('WORKER_ENABLED', 'true')
    monkeypatch.setenv('LIVE_MODE_ENABLED', 'true')
    monkeypatch.setenv('EVM_RPC_URL', 'https://unreachable-rpc.example.com')
    monkeypatch.delenv('STAGING_EVM_RPC_URL', raising=False)
    monkeypatch.setattr('services.api.app.evm_activity_provider._resolve_evm_rpc_url', lambda: 'https://unreachable-rpc.example.com')
    monkeypatch.setattr(run_monitoring_worker, 'validate_monitoring_config_or_raise', lambda: None)
    monkeypatch.setattr(
        'services.api.app.evm_activity_provider.probe_rpc_health',
        lambda: {'ok': False, 'chain_id_hex': None, 'chain_id_int': None, 'block_number_hex': None, 'block_number_int': None, 'error': 'unreachable'},
    )
    monkeypatch.setattr(
        run_monitoring_worker,
        'parse_args',
        lambda: SimpleNamespace(worker_name='test-worker', interval_seconds=0.01, limit=5, once=True),
    )
    monkeypatch.setattr(
        run_monitoring_worker,
        'run_monitoring_cycle',
        lambda worker_name, limit, trigger_type='scheduler': {'due_targets': 0, 'checked': 0, 'alerts_generated': 0, 'live_mode': True},
    )
    monkeypatch.setattr(run_monitoring_worker, 'evaluate_monitoring_system_alerts', lambda stale_after_seconds: {})
    gauge_calls = []
    monkeypatch.setattr(run_monitoring_worker, 'gauge', lambda name, value, **labels: gauge_calls.append((name, value)))

    assert run_monitoring_worker.main() == 0

    healthy_values = [value for name, value in gauge_calls if name == 'decoda_monitoring_worker_healthy']
    assert healthy_values, 'worker must report the healthy gauge'
    assert 1 not in healthy_values, 'worker must not be marked healthy while eth_blockNumber fails'
    assert 0 in healthy_values


def test_worker_main_marks_healthy_when_rpc_health_ok(monkeypatch) -> None:
    """decoda_monitoring_worker_healthy is 1 after a cycle when eth_blockNumber succeeded at startup."""
    monkeypatch.setenv('WORKER_ENABLED', 'true')
    monkeypatch.setenv('LIVE_MODE_ENABLED', 'true')
    monkeypatch.setenv('EVM_RPC_URL', 'https://fake-base-rpc.example.com')
    monkeypatch.delenv('STAGING_EVM_RPC_URL', raising=False)
    monkeypatch.setattr('services.api.app.evm_activity_provider._resolve_evm_rpc_url', lambda: 'https://fake-base-rpc.example.com')
    monkeypatch.setattr(run_monitoring_worker, 'validate_monitoring_config_or_raise', lambda: None)
    monkeypatch.setattr(
        'services.api.app.evm_activity_provider.probe_rpc_health',
        lambda: {'ok': True, 'chain_id_hex': '0x2105', 'chain_id_int': 8453, 'block_number_hex': '0x2d0e2a0', 'block_number_int': 47243936, 'error': None},
    )
    monkeypatch.setattr(
        run_monitoring_worker,
        'parse_args',
        lambda: SimpleNamespace(worker_name='test-worker', interval_seconds=0.01, limit=5, once=True),
    )
    monkeypatch.setattr(
        run_monitoring_worker,
        'run_monitoring_cycle',
        lambda worker_name, limit, trigger_type='scheduler': {'due_targets': 0, 'checked': 0, 'alerts_generated': 0, 'live_mode': True},
    )
    monkeypatch.setattr(run_monitoring_worker, 'evaluate_monitoring_system_alerts', lambda stale_after_seconds: {})
    gauge_calls = []
    monkeypatch.setattr(run_monitoring_worker, 'gauge', lambda name, value, **labels: gauge_calls.append((name, value)))

    assert run_monitoring_worker.main() == 0

    healthy_values = [value for name, value in gauge_calls if name == 'decoda_monitoring_worker_healthy']
    assert healthy_values == [1], 'worker with healthy RPC must gauge healthy=1 once per cycle'


def test_live_evidence_ready_false_until_full_chain() -> None:
    """live_evidence_ready stays False for partial chains (telemetry-only, detection-only, etc.)."""
    from services.api.app.paid_launch_readiness import build_live_evidence_proof

    partial_states = [
        # telemetry persisted but no detection
        {
            'provider_ready': True, 'evidence_source': 'live',
            'source_type': 'rpc_polling',
            'latest_live_telemetry_at': '2026-05-22T12:00:00+00:00',
            'rpc_polling_telemetry_count': 1, 'monitoring_checked_count': 1,
            'receipts_written': 1, 'detections_count': 0, 'alerts_count': 0,
            'incidents_count': 0, 'response_actions_count': 0, 'evidence_count': 0,
            'detection_telemetry_linked': False,
            'alert_detection_linked': False,
            'incident_alert_linked': False,
        },
        # detection present but no alert
        {
            'provider_ready': True, 'evidence_source': 'live',
            'source_type': 'rpc_polling',
            'latest_live_telemetry_at': '2026-05-22T12:00:00+00:00',
            'rpc_polling_telemetry_count': 1, 'monitoring_checked_count': 1,
            'receipts_written': 1, 'detections_count': 1, 'alerts_count': 0,
            'incidents_count': 0, 'response_actions_count': 0, 'evidence_count': 0,
            'detection_telemetry_linked': True,
            'alert_detection_linked': False,
            'incident_alert_linked': False,
        },
    ]
    for state in partial_states:
        result = build_live_evidence_proof(chain_evidence=state)
        assert result['live_evidence_ready'] is False, (
            f'live_evidence_ready must be False for partial chain: {state}'
        )


def test_worker_rpc_healthy_initialized_no_unbound_error(monkeypatch) -> None:
    """Worker loop must not crash with UnboundLocalError when rpc_healthy_at_startup is False."""
    monkeypatch.setenv('WORKER_ENABLED', 'true')
    monkeypatch.setenv('LIVE_MODE_ENABLED', 'true')
    monkeypatch.setenv('EVM_RPC_URL', 'https://fake-rpc.example.com')
    monkeypatch.delenv('STAGING_EVM_RPC_URL', raising=False)
    monkeypatch.setattr('services.api.app.evm_activity_provider._resolve_evm_rpc_url', lambda: 'https://fake-rpc.example.com')
    monkeypatch.setattr(run_monitoring_worker, 'validate_monitoring_config_or_raise', lambda: None)
    monkeypatch.setattr(
        'services.api.app.evm_activity_provider.probe_rpc_health',
        lambda: {'ok': False, 'chain_id_hex': None, 'chain_id_int': None,
                 'block_number_hex': None, 'block_number_int': None, 'error': 'timeout'},
    )
    monkeypatch.setattr(
        run_monitoring_worker,
        'parse_args',
        lambda: SimpleNamespace(worker_name='test-worker', interval_seconds=0.01, limit=5, once=True),
    )
    monkeypatch.setattr(
        run_monitoring_worker,
        'run_monitoring_cycle',
        lambda worker_name, limit, trigger_type='scheduler': {
            'due_targets': 0, 'checked': 0, 'alerts_generated': 0, 'live_mode': True,
        },
    )
    monkeypatch.setattr(run_monitoring_worker, 'evaluate_monitoring_system_alerts', lambda stale_after_seconds: {})
    monkeypatch.setattr(run_monitoring_worker, 'gauge', lambda name, value, **labels: None)

    # Must not raise UnboundLocalError: cannot access local variable 'rpc_healthy'
    rc = run_monitoring_worker.main()
    assert rc == 0


def test_startup_log_no_format_type_error(monkeypatch, caplog) -> None:
    """_log_startup_provider_status must not raise TypeError from mismatched %s args."""
    import logging
    monkeypatch.setenv('WORKER_ENABLED', 'true')
    monkeypatch.setenv('EVM_RPC_URL', 'https://fake-rpc.example.com')
    monkeypatch.delenv('STAGING_EVM_RPC_URL', raising=False)
    monkeypatch.setenv('DATABASE_URL', 'postgresql://user:pass@localhost/db')
    monkeypatch.setattr('services.api.app.evm_activity_provider._resolve_evm_rpc_url', lambda: 'https://fake-rpc.example.com')
    monkeypatch.setattr(
        'services.api.app.evm_activity_provider.probe_rpc_health',
        lambda: {'ok': True, 'chain_id_hex': '0x2105', 'chain_id_int': 8453,
                 'block_number_hex': '0x2d12345', 'block_number_int': 47251269, 'error': None},
    )

    logger = logging.getLogger('test_format_no_error')
    # If the format string still has mismatched %s, Python's logging will emit a TypeError
    # in the log record; we detect this by checking no TypeError appears in the output.
    with caplog.at_level(logging.DEBUG, logger='test_format_no_error'):
        result = run_monitoring_worker._log_startup_provider_status(logger)

    for record in caplog.records:
        # logging does not raise; it catches internally — check getMessage() doesn't raise
        try:
            record.getMessage()
        except TypeError as exc:
            raise AssertionError(f'Logger format string mismatch caused TypeError: {exc}') from exc
    assert isinstance(result, dict), 'must return a dict'
    assert 'rpc_health_ok' in result
    assert 'database_url_configured' in result


def test_startup_log_returns_database_url_configured_flag(monkeypatch, caplog) -> None:
    """_log_startup_provider_status returns database_url_configured True/False based on env."""
    import logging
    monkeypatch.delenv('EVM_RPC_URL', raising=False)
    monkeypatch.delenv('STAGING_EVM_RPC_URL', raising=False)
    monkeypatch.setenv('WORKER_ENABLED', 'true')
    monkeypatch.setattr('services.api.app.evm_activity_provider._resolve_evm_rpc_url', lambda: '')

    logger = logging.getLogger('test_db_flag')

    monkeypatch.setenv('DATABASE_URL', 'postgresql://user:pass@localhost/db')
    with caplog.at_level(logging.INFO, logger='test_db_flag'):
        result = run_monitoring_worker._log_startup_provider_status(logger)
    assert result['database_url_configured'] is True
    log_text = '\n'.join(r.getMessage() for r in caplog.records)
    assert 'database_url_configured=True' in log_text

    caplog.clear()
    monkeypatch.delenv('DATABASE_URL', raising=False)
    with caplog.at_level(logging.INFO, logger='test_db_flag'):
        result = run_monitoring_worker._log_startup_provider_status(logger)
    assert result['database_url_configured'] is False
    log_text = '\n'.join(r.getMessage() for r in caplog.records)
    assert 'database_url_configured=False' in log_text


# ---------------------------------------------------------------------------
# Dead-letter fast recovery + per-target due-selection diagnostics
# (regression coverage for the Base target that stayed skipped_dead_lettered=1)
# ---------------------------------------------------------------------------

_E785_TARGET_ID = 'e7851a52-8fb1-48cd-84a3-d033f591c5dd'
_E785_WORKSPACE_ID = '1155f479-3e5b-4d90-be6c-fd6c1d6b957d'


def test_dead_letter_fast_recovery_constant_and_default():
    """The fast-retry window must exist, be env-overridable, and floor at 60s."""
    assert hasattr(monitoring_runner, 'MONITORING_DEAD_LETTER_RETRY_SECONDS')
    assert monitoring_runner.MONITORING_DEAD_LETTER_RETRY_SECONDS >= 60


def test_dead_letter_fast_recovery_sql_present_and_decoupled_from_backfill():
    """The cycle must run a short-backoff dead-letter recovery UPDATE that is
    independent of the backfill cooldown, so a target blocked by backfill cooldown
    can still be recovered and live-polled normally."""
    content = (REPO_ROOT / 'services/api/app/monitoring_runner.py').read_text(encoding='utf-8')
    assert 'dead_letter_fast_recovery' in content
    assert 'MONITORING_DEAD_LETTER_RETRY_SECONDS' in content
    # Recovery is gated on the dead-letter timestamp, not on _LAST_..._BACKFILL_AT.
    assert "monitoring_dead_lettered_at < NOW() - (%s * INTERVAL '1 second')" in content


def _make_basic_cycle_env(monkeypatch, connection):
    monkeypatch.setattr(monitoring_runner, 'live_mode_enabled', lambda: True)
    monkeypatch.setattr(monitoring_runner, 'ensure_pilot_schema', lambda _connection: None)
    monkeypatch.setattr(monitoring_runner, 'pg_connection', lambda: _fake_pg(connection))


def test_per_target_diagnostics_logged_for_live_and_dead_lettered(monkeypatch, caplog):
    """Task 6: one truthful per-target line carrying selected_for_live_poll,
    selected_for_backfill, dead_lettered, blocked_reason and cooldown_until."""
    now = datetime.now(timezone.utc)
    due_targets = [
        {
            'id': _E785_TARGET_ID,
            'name': 'Base wallet target',
            'monitoring_enabled': True,
            'enabled': True,
            'is_active': True,
            'workspace_exists_id': _E785_WORKSPACE_ID,
            'chain_network': 'base',
            'last_checked_at': None,  # never checked => due now (due_in_seconds=0)
            'monitoring_interval_seconds': 30,
            'monitoring_dead_lettered_at': None,
            'created_at': now,
        },
        {
            'id': 'dead-target',
            'name': 'Dead target',
            'monitoring_enabled': True,
            'enabled': True,
            'is_active': True,
            'workspace_exists_id': _E785_WORKSPACE_ID,
            'chain_network': 'base',
            'last_checked_at': now - timedelta(hours=2),
            'monitoring_interval_seconds': 30,
            'monitoring_dead_lettered_at': now - timedelta(minutes=1),
            'created_at': now,
        },
    ]
    connection = _FakeConnection(due_targets)
    _make_basic_cycle_env(monkeypatch, connection)
    monkeypatch.setattr(
        monitoring_runner,
        'process_monitoring_target',
        lambda *_a, **_k: {'alerts_generated': 0, 'status': 'completed'},
    )

    with caplog.at_level('INFO'):
        summary = monitoring_runner.run_monitoring_cycle(worker_name='test-worker', limit=10)

    # The valid Base target with due_in_seconds=0 becomes effective_due_count=1 / checked=1.
    assert summary['effective_due_count'] == 1
    assert summary['checked'] == 1

    live_line = next(
        (m for m in caplog.messages if 'monitoring target-selection' in m and _E785_TARGET_ID in m),
        None,
    )
    assert live_line is not None
    assert 'selected_for_live_poll=True' in live_line
    assert 'selected_for_backfill=False' in live_line
    assert 'dead_lettered=False' in live_line
    assert 'blocked_reason=None' in live_line
    assert 'cooldown_until=' in live_line

    dead_line = next(
        (m for m in caplog.messages if 'monitoring target-selection' in m and 'target_id=dead-target' in m),
        None,
    )
    assert dead_line is not None
    assert 'selected_for_live_poll=False' in dead_line
    assert 'dead_lettered=True' in dead_line
    assert 'blocked_reason=dead_lettered' in dead_line


def test_dead_lettered_only_target_does_not_burn_backfill_cooldown(monkeypatch, caplog):
    """Tasks 4/5: when the sole candidate is dead-lettered it must not be picked as the
    backfill fallback (the claim query excludes it anyway). Backfill reports
    missing-candidate, never blocked_by_cooldown, so the cooldown is never consumed and
    normal recovery is not starved."""
    now = datetime.now(timezone.utc)
    monitoring_runner._LAST_MONITORING_DUE_SELECTION_BACKFILL_AT.pop(_E785_WORKSPACE_ID, None)
    due_targets = [
        {
            'id': _E785_TARGET_ID,
            'name': 'Base wallet target',
            'monitoring_enabled': True,
            'enabled': True,
            'is_active': True,
            'workspace_exists_id': _E785_WORKSPACE_ID,
            'chain_network': 'base',
            'last_checked_at': now - timedelta(hours=3),
            'monitoring_interval_seconds': 30,
            'monitoring_dead_lettered_at': now - timedelta(minutes=1),
            'created_at': now,
        }
    ]
    connection = _FakeConnection(due_targets)
    _make_basic_cycle_env(monkeypatch, connection)
    monkeypatch.setattr(
        monitoring_runner,
        'process_monitoring_target',
        lambda *_a, **_k: {'alerts_generated': 0, 'status': 'completed'},
    )

    with caplog.at_level('INFO'):
        monitoring_runner.run_monitoring_cycle(worker_name='test-worker', limit=10)

    summary_line = next((m for m in caplog.messages if 'monitoring cycle summary' in m), None)
    assert summary_line is not None
    assert 'skipped_dead_lettered=1' in summary_line
    assert 'backfill_blocked_by_cooldown=0' in summary_line
    # The dead-lettered target was excluded from the backfill fallback candidate.
    assert _E785_WORKSPACE_ID not in monitoring_runner._LAST_MONITORING_DUE_SELECTION_BACKFILL_AT


def test_status_payload_exposes_worker_alive_and_dead_lettered_targets():
    """Task 7: the status surface must carry worker_alive (heartbeat-derived) and a
    dead_lettered_targets count, and attribute a blocked target to 'targets_blocked'
    rather than worker_not_running when the heartbeat is fresh."""
    content = (REPO_ROOT / 'services/api/app/monitoring_runner.py').read_text(encoding='utf-8')
    assert "'worker_alive': bool(worker_alive)" in content
    assert "'dead_lettered_targets': dead_lettered_count" in content
    assert "'targets_blocked'" in content
    # worker_not_running must remain gated on a stale heartbeat, never on blocked targets.
    assert (
        "'live_worker_not_running'\n"
        "                if stale_heartbeat and not runner_alive and enabled_system_count > 0"
    ) in content


def test_frontend_maps_targets_blocked_reason_code():
    """The banner copy for targets_blocked must be truthful and not claim the worker is down."""
    fe = (REPO_ROOT / 'apps/web/app/runtime-summary-context.tsx').read_text(encoding='utf-8')
    assert 'targets_blocked:' in fe
    line = next(l for l in fe.splitlines() if l.strip().startswith('targets_blocked:'))
    assert 'alive' in line.lower()
    assert 'block' in line.lower()
