from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from services.api.app import monitoring_runner
from services.api.app import pilot
from services.api.app import run_monitoring_worker


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


def test_monitoring_cycle_backfills_oldest_target_when_all_targets_are_within_interval(monkeypatch):
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

    summary = monitoring_runner.run_monitoring_cycle(worker_name='test-worker', limit=10, trigger_type='scheduler')

    assert summary['due_targets'] == 1
    assert summary['checked'] == 1
    assert len(connection.monitoring_run_inserts) == 1
    assert len(connection.monitoring_run_updates) == 1
    insert = connection.monitoring_run_inserts[0]
    update = connection.monitoring_run_updates[0]
    assert insert[2] == 'scheduler'
    assert update[0] == 'completed'
    assert update[1] == 1
    assert update[2] == 1
    assert update[3] == 0
    assert update[4] == 0
    assert update[5] == 0


def test_monitoring_cycle_summary_reports_consistent_due_counts_when_backfill_promotes_target(monkeypatch, caplog):
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
        'MONITORING_DUE_SELECTION_BACKFILL_MIN_AGE_SECONDS',
        30,
    )
    monitoring_runner._LAST_MONITORING_DUE_SELECTION_BACKFILL_AT.pop('ws-1', None)

    with caplog.at_level('INFO'):
        summary = monitoring_runner.run_monitoring_cycle(worker_name='test-worker', limit=10, trigger_type='scheduler')

    assert summary['due_targets'] == 1
    assert any(
        'base_due_count=0 effective_due_count=1 due=1 checked=1' in message and 'skipped_not_due=0' in message
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


def test_monitoring_cycle_backfill_triggers_once_when_threshold_met(monkeypatch):
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

    assert first['due_targets'] == 1
    assert first['checked'] == 1
    assert second['due_targets'] == 0
    assert second['checked'] == 0
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


def test_due_target_selection_query_keeps_monitoring_filters() -> None:
    source = open('services/api/app/monitoring_runner.py', encoding='utf-8').read()
    assert 'total_candidate_targets=%s' in source
    assert 'skipped_null_handling=%s' in source
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
