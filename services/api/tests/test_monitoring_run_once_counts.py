from __future__ import annotations

from contextlib import contextmanager
from fastapi import Request

from services.api.app import monitoring_runner


class _Result:
    def __init__(self, *, row=None):
        self._row = row

    def fetchone(self):
        return self._row


class _Connection:
    def __init__(self):
        self.last_run_update_params = None

    def execute(self, query, params=None):
        normalized = ' '.join(str(query).split())
        if normalized.startswith('SELECT id, workspace_id, name, target_type'):
            return _Result(
                row={
                    'id': 'target-1',
                    'workspace_id': 'ws-1',
                    'name': 'Treasury Wallet',
                    'target_type': 'wallet',
                    'chain_network': 'ethereum',
                    'asset_id': 'asset-1',
                }
            )
        if normalized.startswith('UPDATE monitoring_runs'):
            self.last_run_update_params = params
        return _Result()

    def commit(self):
        return None


@contextmanager
def _fake_pg(connection):
    yield connection


def test_run_monitoring_once_persists_detection_and_telemetry_counts(monkeypatch):
    connection = _Connection()
    request = Request({'type': 'http', 'headers': []})

    monkeypatch.setattr(monitoring_runner, 'pg_connection', lambda: _fake_pg(connection))
    monkeypatch.setattr(monitoring_runner, 'ensure_pilot_schema', lambda _connection: None)
    monkeypatch.setattr(
        monitoring_runner,
        '_require_workspace_admin',
        lambda _connection, _request: ({'id': 'user-1'}, {'workspace_id': 'ws-1'}),
    )
    monkeypatch.setattr(
        monitoring_runner,
        'process_monitoring_target',
        lambda _connection, _target, triggered_by_user_id=None: {
            'events_ingested': 0,
            'telemetry_records_seen': 3,
            'detections_created': 1,
            'alerts_generated': 1,
        },
    )

    monitoring_runner.run_monitoring_once('target-1', request)

    assert connection.last_run_update_params is not None
    assert connection.last_run_update_params[1] == 1
    assert connection.last_run_update_params[3] == 3
