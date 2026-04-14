from __future__ import annotations

from contextlib import contextmanager

from fastapi.testclient import TestClient

from services.api.app import main as api_main
from services.api.app import pilot


class _Result:
    def __init__(self, *, rows=None):
        self._rows = rows or []

    def fetchall(self):
        return self._rows


class _SchemaStrictConn:
    def __init__(self):
        self.commits = 0
        self.rows = [
            {
                'id': 'ms-1',
                'workspace_id': 'ws-1',
                'asset_id': 'asset-1',
                'target_id': 'target-1',
                'chain': 'ethereum-mainnet',
                'is_enabled': True,
                'runtime_status': 'active',
                'status': 'active',
                'last_heartbeat': None,
                'last_error_text': None,
                'created_at': '2026-04-10T00:00:00+00:00',
                'monitoring_interval_seconds': 45,
                'asset_name': 'Treasury',
                'target_name': 'Ops wallet',
            }
        ]

    def execute(self, query, params=None):
        normalized = ' '.join(str(query).split())
        if 'ms.monitoring_interval_seconds' in normalized:
            raise RuntimeError('UndefinedColumn: column ms.monitoring_interval_seconds does not exist')
        if 'FROM monitored_systems ms' in normalized and 'ORDER BY ms.created_at DESC' in normalized:
            return _Result(rows=[dict(row) for row in self.rows])
        return _Result(rows=[])

    def commit(self):
        self.commits += 1


class _DeletedTargetCompatibleConn:
    def __init__(self):
        self.query_text = ''

    def execute(self, query, params=None):
        normalized = ' '.join(str(query).split())
        self.query_text = normalized
        if 'FROM monitored_systems ms' in normalized and 'ORDER BY ms.created_at DESC' in normalized:
            return _Result(
                rows=[
                    {
                        'id': 'ms-orphan',
                        'workspace_id': 'ws-1',
                        'asset_id': 'asset-1',
                        'target_id': 'target-deleted',
                        'chain': 'ethereum-mainnet',
                        'is_enabled': True,
                        'runtime_status': 'idle',
                        'status': 'active',
                        'last_heartbeat': None,
                        'last_error_text': None,
                        'created_at': '2026-04-10T00:00:00+00:00',
                        'monitoring_interval_seconds': 30,
                        'asset_name': 'Treasury',
                        'target_name': None,
                    }
                ]
            )
        return _Result(rows=[])


@contextmanager
def _fake_pg(conn: _SchemaStrictConn):
    yield conn


class _Request:
    headers = {'authorization': 'Bearer token', 'x-workspace-id': 'ws-1'}


def _stub_workspace(*_args, **_kwargs):
    return {'workspace_id': 'ws-1', 'workspace': {'id': 'ws-1'}, 'role': 'owner'}


def test_list_workspace_monitored_system_rows_uses_target_interval_alias():
    conn = _SchemaStrictConn()

    rows = pilot.list_workspace_monitored_system_rows(conn, 'ws-1')

    assert len(rows) == 1
    assert rows[0]['id'] == 'ms-1'
    assert rows[0]['monitoring_interval_seconds'] == 45


def test_list_workspace_monitored_system_rows_does_not_filter_deleted_targets():
    conn = _DeletedTargetCompatibleConn()

    rows = pilot.list_workspace_monitored_system_rows(conn, 'ws-1')

    assert len(rows) == 1
    assert rows[0]['id'] == 'ms-orphan'
    assert rows[0]['target_name'] is None
    assert 'LEFT JOIN targets t' in conn.query_text
    assert 't.deleted_at IS NULL' not in conn.query_text


def test_reconcile_workspace_monitored_systems_passes_list_rows_stage(monkeypatch):
    conn = _SchemaStrictConn()
    request = _Request()

    monkeypatch.setattr(pilot, 'require_live_mode', lambda: None)
    monkeypatch.setattr(pilot, 'ensure_pilot_schema', lambda *_: None)
    monkeypatch.setattr(pilot, 'pg_connection', lambda: _fake_pg(conn))
    monkeypatch.setattr(pilot, '_require_workspace_admin', lambda *_a, **_k: ({'id': 'user-1'}, _stub_workspace()))
    monkeypatch.setattr(pilot, 'reconcile_enabled_targets_monitored_systems', lambda *_a, **_k: {'targets_scanned': 1, 'created_or_updated': 1})
    monkeypatch.setattr(pilot, 'log_audit', lambda *_a, **_k: None)

    payload = pilot.reconcile_workspace_monitored_systems(request)

    assert payload['monitored_systems_count'] == 1
    assert payload['systems'][0]['monitoring_interval_seconds'] == 45
    assert conn.commits == 1


def test_monitoring_routes_reconcile_then_list_return_rows_without_undefined_column(monkeypatch):
    conn = _SchemaStrictConn()
    client = TestClient(api_main.app)

    monkeypatch.setattr(api_main, 'with_auth_schema_json', lambda handler: handler())
    monkeypatch.setattr(api_main, 'reconcile_workspace_monitored_systems', pilot.reconcile_workspace_monitored_systems)
    monkeypatch.setattr(api_main, 'list_monitored_systems', pilot.list_monitored_systems)

    monkeypatch.setattr(pilot, 'require_live_mode', lambda: None)
    monkeypatch.setattr(pilot, 'ensure_pilot_schema', lambda *_: None)
    monkeypatch.setattr(pilot, 'pg_connection', lambda: _fake_pg(conn))
    monkeypatch.setattr(pilot, '_require_workspace_admin', lambda *_a, **_k: ({'id': 'user-1'}, _stub_workspace()))
    monkeypatch.setattr(pilot, 'authenticate_with_connection', lambda *_a, **_k: {'id': 'user-1'})
    monkeypatch.setattr(pilot, 'resolve_workspace', lambda *_a, **_k: _stub_workspace())
    monkeypatch.setattr(pilot, 'reconcile_enabled_targets_monitored_systems', lambda *_a, **_k: {'targets_scanned': 1, 'created_or_updated': 1})
    monkeypatch.setattr(pilot, 'log_audit', lambda *_a, **_k: None)

    headers = {'authorization': 'Bearer token', 'x-workspace-id': 'ws-1'}
    reconcile_response = client.post('/monitoring/systems/reconcile', headers=headers)
    list_response = client.get('/monitoring/systems', headers=headers)

    assert reconcile_response.status_code == 200
    assert reconcile_response.json()['monitored_systems_count'] == 1
    assert list_response.status_code == 200
    assert list_response.json()['systems'][0]['id'] == 'ms-1'
