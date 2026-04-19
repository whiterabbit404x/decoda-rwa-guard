from __future__ import annotations

from contextlib import contextmanager
from fastapi import Request

from services.api.app import pilot


class _Result:
    def __init__(self, rows=None):
        self._rows = rows or []

    def fetchall(self):
        return self._rows

    def fetchone(self):
        return self._rows[0] if self._rows else None


class _Conn:
    def __init__(self):
        self.query_params = None

    def execute(self, query, params=None):
        normalized = ' '.join(str(query).split())
        if normalized.startswith('SELECT id, workspace_id, started_at, completed_at, status, trigger_type,'):
            self.query_params = params
            return _Result(
                rows=[
                    {
                        'id': 'run-1',
                        'workspace_id': 'ws-1',
                        'started_at': '2026-04-18T00:00:00Z',
                        'completed_at': '2026-04-18T00:00:01Z',
                        'status': 'completed',
                        'trigger_type': 'scheduler',
                        'systems_checked_count': 2,
                        'assets_checked_count': 1,
                        'detections_created_count': 3,
                        'alerts_created_count': 1,
                        'telemetry_records_seen_count': 3,
                        'notes': 'worker_name=monitoring-worker',
                    }
                ]
            )
        if 'FROM monitoring_runs WHERE id = %s::uuid AND workspace_id = %s::uuid LIMIT 1' in normalized:
            self.query_params = params
            return _Result(
                rows=[
                    {
                        'id': 'run-1',
                        'workspace_id': 'ws-1',
                        'started_at': '2026-04-18T00:00:00Z',
                        'completed_at': '2026-04-18T00:00:01Z',
                        'status': 'completed',
                        'trigger_type': 'scheduler',
                        'systems_checked_count': 2,
                        'assets_checked_count': 1,
                        'detections_created_count': 3,
                        'alerts_created_count': 1,
                        'telemetry_records_seen_count': 3,
                        'notes': 'worker_name=monitoring-worker',
                    }
                ]
            )
        return _Result()


@contextmanager
def _fake_pg(connection):
    yield connection


def test_list_monitoring_runs_returns_workspace_rows(monkeypatch):
    connection = _Conn()
    request = Request({'type': 'http', 'headers': []})

    monkeypatch.setattr(pilot, 'require_live_mode', lambda: None)
    monkeypatch.setattr(pilot, 'pg_connection', lambda: _fake_pg(connection))
    monkeypatch.setattr(pilot, 'ensure_pilot_schema', lambda _connection: None)
    monkeypatch.setattr(
        pilot,
        'resolve_workspace_context_for_request',
        lambda _connection, _request: ({'id': 'user-1'}, {'workspace_id': 'ws-1', 'workspace': {'id': 'ws-1', 'name': 'Demo'}}, False),
    )

    payload = pilot.list_monitoring_runs(request, limit=12)

    assert payload['workspace']['id'] == 'ws-1'
    assert len(payload['runs']) == 1
    assert payload['runs'][0]['trigger_type'] == 'scheduler'
    assert payload['runs'][0]['systems_checked_count'] == 2
    assert connection.query_params == ('ws-1', 12)


def test_get_monitoring_run_returns_workspace_row(monkeypatch):
    connection = _Conn()
    request = Request({'type': 'http', 'headers': []})

    monkeypatch.setattr(pilot, 'require_live_mode', lambda: None)
    monkeypatch.setattr(pilot, 'pg_connection', lambda: _fake_pg(connection))
    monkeypatch.setattr(pilot, 'ensure_pilot_schema', lambda _connection: None)
    monkeypatch.setattr(
        pilot,
        'resolve_workspace_context_for_request',
        lambda _connection, _request: ({'id': 'user-1'}, {'workspace_id': 'ws-1', 'workspace': {'id': 'ws-1', 'name': 'Demo'}}, False),
    )

    payload = pilot.get_monitoring_run('run-1', request)

    assert payload['workspace']['id'] == 'ws-1'
    assert payload['run']['id'] == 'run-1'
    assert payload['run']['status'] == 'completed'
    assert connection.query_params == ('run-1', 'ws-1')
