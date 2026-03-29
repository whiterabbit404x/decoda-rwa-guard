from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
from types import SimpleNamespace

from services.api.app import monitoring_runner
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

    def transaction(self):
        return _FakeTransaction()

    def execute(self, query, params=None):
        normalized = ' '.join(str(query).split())
        if 'LEFT JOIN workspaces AS workspace' in normalized:
            return _Result(rows=self.due_targets)
        if 'FROM targets' in normalized and 'FOR UPDATE SKIP LOCKED' in normalized:
            return _Result(rows=self.due_targets)
        if normalized.startswith('SELECT worker_name, running, last_cycle_at'):
            return _Result(row=self.health_row)
        if normalized.startswith('UPDATE monitoring_worker_state'):
            self.health_row = {
                'worker_name': params[4],
                'running': False,
                'last_cycle_at': datetime.now(timezone.utc),
                'last_cycle_due_targets': params[0],
                'last_cycle_targets_checked': params[1],
                'last_cycle_alerts_generated': params[2],
                'last_error': params[3],
                'updated_at': datetime.now(timezone.utc),
            }
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


def test_worker_once_mode_runs_single_cycle(monkeypatch):
    calls = []

    monkeypatch.setattr(
        run_monitoring_worker,
        'parse_args',
        lambda: SimpleNamespace(worker_name='test-worker', interval_seconds=0.01, limit=5, once=True),
    )

    def _cycle(worker_name, limit):
        calls.append((worker_name, limit))
        return {'due_targets': 0, 'checked': 0, 'alerts_generated': 0, 'live_mode': True}

    monkeypatch.setattr(run_monitoring_worker, 'run_monitoring_cycle', _cycle)

    assert run_monitoring_worker.main() == 0
    assert calls == [('test-worker', 5)]


def test_due_target_selection_query_keeps_monitoring_filters() -> None:
    source = open('services/api/app/monitoring_runner.py', encoding='utf-8').read()
    assert 'total_candidate_targets=%s' in source
    assert 'skipped_null_handling=%s' in source
    assert 'last_checked_at is None' in source
    assert 'FOR UPDATE SKIP LOCKED' in source
