from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone

from fastapi.testclient import TestClient

from services.api.app import main as api_main
from services.api.app import monitoring_runner, pilot


class _Result:
    def __init__(self, row=None, rows=None):
        self._row = row or {}
        self._rows = rows or []

    def fetchone(self):
        return self._row

    def fetchall(self):
        return self._rows


class _Conn:
    def __init__(self):
        self.commits = 0
        now = datetime.now(timezone.utc).isoformat()
        self._runtime_rows = [
            {
                'id': 'ms-1',
                'workspace_id': 'ws-legacy',
                'asset_id': 'asset-1',
                'target_id': 'target-1',
                'chain': 'ethereum-mainnet',
                'is_enabled': True,
                'runtime_status': 'idle',
                'status': 'active',
                'last_heartbeat': now,
                'monitoring_interval_seconds': 30,
                'created_at': now,
                'asset_name': 'Asset 1',
                'target_name': 'Target 1',
            },
            {
                'id': 'ms-2',
                'workspace_id': 'ws-legacy',
                'asset_id': 'asset-2',
                'target_id': 'target-2',
                'chain': 'ethereum-mainnet',
                'is_enabled': True,
                'runtime_status': 'idle',
                'status': 'active',
                'last_heartbeat': now,
                'monitoring_interval_seconds': 30,
                'created_at': now,
                'asset_name': 'Asset 2',
                'target_name': 'Target 2',
            },
            {
                'id': 'ms-3',
                'workspace_id': 'ws-legacy',
                'asset_id': 'asset-3',
                'target_id': 'target-3',
                'chain': 'ethereum-mainnet',
                'is_enabled': None,
                'runtime_status': 'idle',
                'status': 'active',
                'last_heartbeat': now,
                'monitoring_interval_seconds': 30,
                'created_at': now,
                'asset_name': 'Asset 3',
                'target_name': 'Target 3',
            },
        ]

    def execute(self, query, params=None):
        q = ' '.join(str(query).split())
        if 'FROM alerts' in q:
            return _Result({'c': 0})
        if 'FROM incidents' in q:
            return _Result({'c': 0})
        if 'FROM evidence' in q:
            return _Result({'observed_at': datetime.now(timezone.utc).isoformat(), 'block_number': 321})
        if 'FROM analysis_runs' in q and "analysis_type LIKE 'monitoring_%'" in q:
            return _Result(None)
        if 'LEFT JOIN assets a' in q and 'FROM targets t' in q and 'COUNT(*) AS c' in q:
            return _Result({'c': 0})
        if 'COUNT(*) AS target_count' in q and 'COUNT(DISTINCT t.asset_id) AS asset_count' in q:
            return _Result({'target_count': 3, 'asset_count': 3})
        if 'SELECT t.id' in q and 'FROM targets t' in q and 'JOIN assets a' in q:
            return _Result(rows=[{'id': 'target-1'}, {'id': 'target-2'}, {'id': 'target-3'}])
        if 'FROM monitored_systems ms' in q and 'ORDER BY ms.created_at DESC' in q:
            return _Result(rows=[dict(row) for row in self._runtime_rows])
        if q.startswith('SELECT id, workspace_id, asset_id, enabled, monitoring_enabled, deleted_at FROM targets'):
            return _Result(
                rows=[
                    {'id': 'target-1', 'workspace_id': 'ws-legacy', 'asset_id': 'asset-1', 'enabled': True, 'monitoring_enabled': True, 'deleted_at': None},
                    {'id': 'target-2', 'workspace_id': 'ws-legacy', 'asset_id': 'asset-2', 'enabled': True, 'monitoring_enabled': True, 'deleted_at': None},
                    {'id': 'target-3', 'workspace_id': 'ws-legacy', 'asset_id': 'asset-3', 'enabled': True, 'monitoring_enabled': True, 'deleted_at': None},
                ]
            )
        if q.startswith('SELECT t.id, t.workspace_id, t.asset_id, t.enabled, t.monitoring_enabled FROM targets t JOIN assets a'):
            return _Result(
                rows=[
                    {'id': 'target-1', 'workspace_id': 'ws-legacy', 'asset_id': 'asset-1', 'enabled': True, 'monitoring_enabled': True},
                    {'id': 'target-2', 'workspace_id': 'ws-legacy', 'asset_id': 'asset-2', 'enabled': True, 'monitoring_enabled': True},
                    {'id': 'target-3', 'workspace_id': 'ws-legacy', 'asset_id': 'asset-3', 'enabled': True, 'monitoring_enabled': True},
                ]
            )
        if q.startswith('SELECT id, workspace_id, target_id, asset_id, is_enabled, runtime_status, status FROM monitored_systems'):
            return _Result(rows=[{k: row[k] for k in ('id', 'workspace_id', 'target_id', 'asset_id', 'is_enabled', 'runtime_status', 'status')} for row in self._runtime_rows])
        return _Result({})

    def commit(self):
        self.commits += 1


@contextmanager
def _fake_pg(conn: _Conn):
    yield conn


class _WorkspaceRequest:
    def __init__(self):
        self.headers = {'authorization': 'Bearer token', 'x-workspace-id': 'ws-legacy'}



def _workspace_context():
    return {'workspace_id': 'ws-legacy', 'workspace': {'id': 'ws-legacy', 'name': 'Legacy Workspace', 'slug': 'legacy'}, 'role': 'owner'}


def test_monitoring_list_runtime_debug_and_reconcile_stay_consistent(monkeypatch):
    conn = _Conn()
    client = TestClient(api_main.app)
    now = datetime.now(timezone.utc)

    monkeypatch.setattr(api_main, 'with_auth_schema_json', lambda handler: handler())
    monkeypatch.setattr(pilot, 'require_live_mode', lambda: None)
    monkeypatch.setattr(pilot, 'ensure_pilot_schema', lambda *_: None)
    monkeypatch.setattr(monitoring_runner, 'ensure_pilot_schema', lambda *_: None)
    monkeypatch.setattr(pilot, 'pg_connection', lambda: _fake_pg(conn))
    monkeypatch.setattr(monitoring_runner, 'pg_connection', lambda: _fake_pg(conn))

    monkeypatch.setattr(pilot, 'authenticate_with_connection', lambda *_a, **_k: {'id': 'user-1'})
    monkeypatch.setattr(pilot, 'resolve_workspace', lambda *_a, **_k: _workspace_context())
    monkeypatch.setattr(
        monitoring_runner,
        'resolve_workspace_context_for_request',
        lambda *_a, **_k: ({'id': 'user-1'}, _workspace_context(), True),
    )
    monkeypatch.setattr(pilot, '_require_workspace_admin', lambda *_a, **_k: ({'id': 'user-1'}, _workspace_context()))
    monkeypatch.setattr(pilot, 'log_audit', lambda *_a, **_k: None)
    monkeypatch.setattr(
        pilot,
        'reconcile_enabled_targets_monitored_systems',
        lambda *_a, **_k: {'targets_scanned': 3, 'created_or_updated': 0, 'repaired_monitored_system_ids': []},
    )
    monkeypatch.setattr(
        monitoring_runner,
        'reconcile_enabled_targets_monitored_systems',
        lambda *_a, **_k: {'targets_scanned': 3, 'created_or_updated': 0, 'created_monitored_systems': 0, 'preserved_monitored_systems': 3, 'removed_monitored_systems': 0},
    )
    monkeypatch.setattr(
        monitoring_runner,
        'get_monitoring_health',
        lambda: {
            'last_heartbeat_at': now.isoformat(),
            'last_cycle_at': now.isoformat(),
            'degraded': False,
            'last_error': None,
            'source_type': 'polling',
            'worker_running': True,
        },
    )

    headers = {'authorization': 'Bearer token', 'x-workspace-id': 'ws-legacy'}
    listed = client.get('/monitoring/systems', headers=headers)
    runtime = client.get('/ops/monitoring/runtime-status', headers=headers)
    debug = client.get('/monitoring/workspace-debug', headers=headers)
    repair = client.post('/monitoring/systems/reconcile', headers=headers)

    assert listed.status_code == 200
    assert len(listed.json()['systems']) == 3

    assert runtime.status_code == 200
    runtime_payload = runtime.json()
    assert runtime_payload['monitored_systems'] == 3
    assert runtime_payload['protected_assets'] == 3
    assert runtime_payload['monitoring_status'] != 'offline'
    assert runtime_payload['status'] != 'Offline'
    assert str(runtime_payload['systems_with_recent_heartbeat']) != '0'
    assert runtime_payload['enabled_systems'] == 3

    assert debug.status_code == 200
    debug_payload = debug.json()
    assert debug_payload['list_route_snapshot']['monitored_systems_count'] == 3
    assert debug_payload['list_route_snapshot']['enabled_monitored_systems_count'] == 3
    assert debug_payload['status_decision_inputs']['monitored_systems_count'] == 3
    assert debug_payload['status_decision_inputs']['protected_assets_count'] == 3
    assert debug_payload['status_decision_inputs']['list_route_monitored_systems_count'] == 3
    assert debug_payload['status_decision_inputs']['list_route_enabled_monitored_systems_count'] == 3
    assert debug_payload['status_decision_inputs']['list_route_protected_asset_count'] == 3
    assert debug_payload['status_decision_inputs']['runtime_enabled_systems_count'] == 3

    assert repair.status_code == 200
    repair_payload = repair.json()
    assert repair_payload['monitored_systems_count'] == 3
    assert repair_payload.get('stage') != 'unhandled_route_exception'


def test_monitored_system_row_enabled_treats_idle_null_enabled_rows_as_configured():
    assert pilot.monitored_system_row_enabled({'is_enabled': None}) is True
    assert pilot.monitored_system_row_enabled({'is_enabled': 'true'}) is True
    assert pilot.monitored_system_row_enabled({'is_enabled': 'false'}) is False
    assert pilot.monitored_system_row_enabled({'is_enabled': 0}) is False


def test_runtime_status_keeps_list_path_counts_when_raw_workspace_query_fails(monkeypatch):
    class _RawQueryFailConn(_Conn):
        def execute(self, query, params=None):
            q = ' '.join(str(query).split())
            if 'FROM monitored_systems ms' in q and 'WHERE ms.workspace_id = %s ORDER BY ms.created_at DESC' in q and 'ms.last_error_text' not in q:
                raise RuntimeError('raw workspace loader failed')
            return super().execute(query, params)

    conn = _RawQueryFailConn()
    client = TestClient(api_main.app)
    now = datetime.now(timezone.utc)

    monkeypatch.setattr(api_main, 'with_auth_schema_json', lambda handler: handler())
    monkeypatch.setattr(pilot, 'ensure_pilot_schema', lambda *_: None)
    monkeypatch.setattr(monitoring_runner, 'ensure_pilot_schema', lambda *_: None)
    monkeypatch.setattr(pilot, 'pg_connection', lambda: _fake_pg(conn))
    monkeypatch.setattr(monitoring_runner, 'pg_connection', lambda: _fake_pg(conn))
    monkeypatch.setattr(
        monitoring_runner,
        'resolve_workspace_context_for_request',
        lambda *_a, **_k: ({'id': 'user-1'}, _workspace_context(), True),
    )
    monkeypatch.setattr(
        monitoring_runner,
        'get_monitoring_health',
        lambda: {
            'last_heartbeat_at': now.isoformat(),
            'last_cycle_at': now.isoformat(),
            'degraded': False,
            'last_error': None,
            'source_type': 'polling',
            'worker_running': True,
        },
    )

    response = client.get('/ops/monitoring/runtime-status', headers={'authorization': 'Bearer token', 'x-workspace-id': 'ws-legacy'})
    assert response.status_code == 200
    payload = response.json()
    assert payload['monitored_systems'] == 3
    assert payload['protected_assets'] == 3


def test_runtime_status_uses_parameterized_detection_query_and_keeps_idle_systems_online(monkeypatch):
    class _StrictPlaceholderConn(_Conn):
        def execute(self, query, params=None):
            q = ' '.join(str(query).split())
            if "analysis_type LIKE 'monitoring_%'" in q and params:
                raise RuntimeError('unsafe percent placeholder pattern')
            return super().execute(query, params)

    conn = _StrictPlaceholderConn()
    client = TestClient(api_main.app)
    now = datetime.now(timezone.utc)

    monkeypatch.setattr(api_main, 'with_auth_schema_json', lambda handler: handler())
    monkeypatch.setattr(pilot, 'ensure_pilot_schema', lambda *_: None)
    monkeypatch.setattr(monitoring_runner, 'ensure_pilot_schema', lambda *_: None)
    monkeypatch.setattr(pilot, 'pg_connection', lambda: _fake_pg(conn))
    monkeypatch.setattr(monitoring_runner, 'pg_connection', lambda: _fake_pg(conn))
    monkeypatch.setattr(
        monitoring_runner,
        'resolve_workspace_context_for_request',
        lambda *_a, **_k: ({'id': 'user-1'}, _workspace_context(), True),
    )
    monkeypatch.setattr(
        monitoring_runner,
        'get_monitoring_health',
        lambda: {
            'last_heartbeat_at': now.isoformat(),
            'last_cycle_at': now.isoformat(),
            'degraded': False,
            'last_error': None,
            'source_type': 'polling',
            'worker_running': True,
        },
    )

    response = client.get('/ops/monitoring/runtime-status', headers={'authorization': 'Bearer token', 'x-workspace-id': 'ws-legacy'})
    assert response.status_code == 200
    payload = response.json()
    assert payload['monitored_systems'] > 0
    assert payload['protected_assets'] > 0
    assert payload['enabled_systems'] > 0
    assert payload['monitoring_status'] != 'offline'
    assert payload['status'] != 'Offline'


def test_ops_runtime_debug_returns_canonical_runtime_summary_fields_with_healthy_live_semantics(monkeypatch):
    client = TestClient(api_main.app)
    now = datetime.now(timezone.utc).isoformat()
    expected_keys = {
        'workspace_id',
        'workspace_slug',
        'workspace_configured',
        'configuration_reason',
        'configuration_diagnostics',
        'status_reason',
        'valid_protected_assets',
        'linked_monitored_systems',
        'enabled_configs',
        'valid_link_count',
        'configured_systems',
        'reporting_systems',
        'last_poll_at',
        'last_heartbeat_at',
        'last_coverage_telemetry_at',
        'last_telemetry_at',
        'telemetry_kind',
        'evidence_source',
        'confidence_status',
        'runtime_status_summary',
    }

    monkeypatch.setattr(api_main, 'with_auth_schema_json', lambda handler: handler())
    monkeypatch.setattr(
        monitoring_runner,
        'monitoring_runtime_status',
        lambda _request=None: {
            'workspace_id': 'ws-healthy',
            'workspace_slug': 'healthy-workspace',
            'workspace_monitoring_summary': {
                'workspace_configured': True,
                'configuration_reason': None,
                'status_reason': None,
                'valid_protected_assets': 2,
                'linked_monitored_systems': 2,
                'enabled_configs': 2,
                'valid_link_count': 2,
                'configured_systems': 2,
                'reporting_systems': 2,
                'last_poll_at': now,
                'last_heartbeat_at': now,
                'last_coverage_telemetry_at': now,
                'last_telemetry_at': now,
                'telemetry_kind': 'coverage',
                'evidence_source': 'live',
                'confidence_status': 'high',
                'runtime_status': 'healthy',
            },
        },
    )

    response = client.get('/ops/monitoring/runtime-debug', headers={'authorization': 'Bearer token', 'x-workspace-id': 'ws-healthy'})
    assert response.status_code == 200
    payload = response.json()

    assert set(payload.keys()) == expected_keys
    assert payload['workspace_id'] == 'ws-healthy'
    assert payload['workspace_slug'] == 'healthy-workspace'
    assert payload['workspace_configured'] is True
    assert payload['evidence_source'] == 'live'
    assert payload['confidence_status'] == 'high'
    assert payload['runtime_status_summary'] == 'healthy'
    assert payload['configuration_diagnostics']['workspace_configured'] is True
    assert payload['configuration_diagnostics']['reason_codes'] == []


def test_monitoring_workspace_debug_surfaces_configuration_diagnostics(monkeypatch):
    client = TestClient(api_main.app)

    monkeypatch.setattr(api_main, 'with_auth_schema_json', lambda handler: handler())
    monkeypatch.setattr(
        api_main,
        'get_workspace_monitoring_debug',
        lambda _request=None: {
            'workspace': {'id': 'ws-1'},
            'list_route_snapshot': {
                'resolved_workspace_id': 'ws-1',
                'monitored_systems_count': 0,
                'enabled_monitored_systems_count': 0,
                'protected_asset_count': 0,
            },
        },
    )
    monkeypatch.setattr(
        api_main,
        'monitoring_runtime_status',
        lambda _request=None: {
            'status': 'Offline',
            'monitoring_status': 'offline',
            'configuration_diagnostics': {
                'valid_protected_assets': 0,
                'linked_monitored_systems': 0,
                'enabled_configs': 0,
                'valid_link_count': 0,
                'workspace_configured': False,
                'configuration_reason': 'no_valid_protected_assets',
                'reason_codes': ['no_valid_protected_assets'],
            },
        },
    )

    response = client.get('/monitoring/workspace-debug', headers={'authorization': 'Bearer token', 'x-workspace-id': 'ws-1'})
    assert response.status_code == 200
    payload = response.json()
    assert payload['configuration_diagnostics']['workspace_configured'] is False
    assert payload['configuration_diagnostics']['reason_codes'] == ['no_valid_protected_assets']
