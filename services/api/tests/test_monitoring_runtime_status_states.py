from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from services.api.app import monitoring_runner


class _Result:
    def __init__(self, row=None, rows=None):
        self._row = row or {}
        self._rows = rows or []

    def fetchone(self):
        return self._row

    def fetchall(self):
        return self._rows


class _Conn:
    def __init__(self, evidence_at: datetime | None):
        self.evidence_at = evidence_at

    def execute(self, query, params=None):
        q = ' '.join(str(query).split())
        if 'FROM alerts' in q:
            return _Result({'c': 1})
        if 'FROM incidents' in q:
            return _Result({'c': 1})
        if 'FROM monitored_systems ms' in q and 'ORDER BY ms.created_at DESC' in q:
            now = datetime.now(timezone.utc).isoformat()
            return _Result(
                rows=[
                    {'id': 'sys-1', 'workspace_id': 'ws-1', 'asset_id': 'asset-1', 'target_id': 'target-1', 'is_enabled': True, 'runtime_status': 'active', 'last_heartbeat': now, 'monitoring_interval_seconds': 30, 'created_at': now},
                    {'id': 'sys-2', 'workspace_id': 'ws-1', 'asset_id': 'asset-2', 'target_id': 'target-2', 'is_enabled': True, 'runtime_status': 'active', 'last_heartbeat': now, 'monitoring_interval_seconds': 30, 'created_at': now},
                    {'id': 'sys-3', 'workspace_id': 'ws-1', 'asset_id': 'asset-3', 'target_id': 'target-3', 'is_enabled': False, 'runtime_status': 'idle', 'last_heartbeat': now, 'monitoring_interval_seconds': 30, 'created_at': now},
                ]
            )
        if 'LEFT JOIN assets a' in q and 'FROM targets t' in q:
            return _Result({'c': 0})
        if 'FROM evidence' in q:
            return _Result({'observed_at': self.evidence_at, 'block_number': 123})
        return _Result({})


@contextmanager
def _fake_pg(conn):
    yield conn


def test_runtime_status_idle_when_worker_healthy_without_recent_evidence(monkeypatch):
    now = datetime.now(timezone.utc)
    monkeypatch.setattr(
        monitoring_runner,
        'get_monitoring_health',
        lambda: {'last_heartbeat_at': now.isoformat(), 'last_cycle_at': now.isoformat(), 'degraded': False, 'last_error': None, 'source_type': 'polling'},
    )
    monkeypatch.setattr(monitoring_runner, 'ensure_pilot_schema', lambda _c: None)
    monkeypatch.setattr(monitoring_runner, 'pg_connection', lambda: _fake_pg(_Conn(None)))

    payload = monitoring_runner.monitoring_runtime_status()
    assert payload['status'] == 'Idle'


def test_runtime_status_active_with_recent_evidence(monkeypatch):
    now = datetime.now(timezone.utc)
    monkeypatch.setattr(
        monitoring_runner,
        'get_monitoring_health',
        lambda: {'last_heartbeat_at': now.isoformat(), 'last_cycle_at': now.isoformat(), 'degraded': False, 'last_error': None, 'source_type': 'websocket'},
    )
    monkeypatch.setattr(monitoring_runner, 'ensure_pilot_schema', lambda _c: None)
    monkeypatch.setattr(monitoring_runner, 'pg_connection', lambda: _fake_pg(_Conn(now - timedelta(seconds=30))))

    payload = monitoring_runner.monitoring_runtime_status()
    assert payload['status'] == 'Active'
    assert payload['monitoring_status'] == 'active'
    assert payload['active_systems'] == 2
    assert payload['monitored_systems'] == 3
    assert payload['protected_assets'] == 2
    assert payload['telemetry_available'] is True
    assert payload['monitored_systems_count'] == 3
    assert payload['protected_assets_count'] == 2


def test_runtime_status_degraded_on_stale_heartbeat(monkeypatch):
    now = datetime.now(timezone.utc)

    class _StaleConn(_Conn):
        def execute(self, query, params=None):
            q = ' '.join(str(query).split())
            if 'FROM monitored_systems ms' in q and 'ORDER BY ms.created_at DESC' in q:
                rows = super().execute(query, params)._rows
                stale = now - timedelta(minutes=15)
                return _Result(rows=[{**row, 'last_heartbeat': stale.isoformat()} for row in rows])
            return super().execute(query, params)

    monkeypatch.setattr(
        monitoring_runner,
        'get_monitoring_health',
        lambda: {'last_heartbeat_at': (now - timedelta(minutes=20)).isoformat(), 'last_cycle_at': (now - timedelta(minutes=20)).isoformat(), 'degraded': False, 'last_error': None, 'source_type': 'polling'},
    )
    monkeypatch.setattr(monitoring_runner, 'ensure_pilot_schema', lambda _c: None)
    monkeypatch.setattr(monitoring_runner, 'pg_connection', lambda: _fake_pg(_StaleConn(now - timedelta(seconds=30))))

    payload = monitoring_runner.monitoring_runtime_status()
    assert payload['status'] == 'Degraded'


def test_runtime_status_degraded_when_enabled_targets_are_invalid(monkeypatch):
    now = datetime.now(timezone.utc)

    class _InvalidConn(_Conn):
        def execute(self, query, params=None):
            q = ' '.join(str(query).split())
            if 'LEFT JOIN assets a' in q and 'FROM targets t' in q:
                return _Result({'c': 2})
            return super().execute(query, params)

    monkeypatch.setattr(
        monitoring_runner,
        'get_monitoring_health',
        lambda: {'last_heartbeat_at': now.isoformat(), 'last_cycle_at': now.isoformat(), 'degraded': False, 'last_error': None, 'source_type': 'polling'},
    )
    monkeypatch.setattr(monitoring_runner, 'ensure_pilot_schema', lambda _c: None)
    monkeypatch.setattr(monitoring_runner, 'pg_connection', lambda: _fake_pg(_InvalidConn(now - timedelta(seconds=30))))

    payload = monitoring_runner.monitoring_runtime_status()
    assert payload['status'] == 'Degraded'
    assert payload['invalid_enabled_targets'] == 2


def test_runtime_status_offline_without_active_systems(monkeypatch):
    now = datetime.now(timezone.utc)

    class _OfflineConn(_Conn):
        def execute(self, query, params=None):
            q = ' '.join(str(query).split())
            if 'FROM monitored_systems ms' in q and 'ORDER BY ms.created_at DESC' in q:
                rows = super().execute(query, params)._rows
                return _Result(rows=[{**row, 'is_enabled': False, 'runtime_status': 'offline'} for row in rows])
            return super().execute(query, params)

    monkeypatch.setattr(
        monitoring_runner,
        'get_monitoring_health',
        lambda: {'last_heartbeat_at': now.isoformat(), 'last_cycle_at': now.isoformat(), 'degraded': False, 'last_error': None, 'source_type': 'polling', 'worker_running': False},
    )
    monkeypatch.setattr(monitoring_runner, 'ensure_pilot_schema', lambda _c: None)
    monkeypatch.setattr(monitoring_runner, 'pg_connection', lambda: _fake_pg(_OfflineConn(now - timedelta(seconds=30))))
    payload = monitoring_runner.monitoring_runtime_status()
    assert payload['monitoring_status'] == 'offline'
    assert payload['status'] == 'Offline'


def test_runtime_status_scopes_counts_to_active_workspace(monkeypatch):
    now = datetime.now(timezone.utc)

    class _WorkspaceConn:
        def execute(self, query, params=None):
            q = ' '.join(str(query).split())
            workspace_id = (params or (None,))[0] if params else None
            if 'FROM alerts' in q:
                return _Result({'c': 0})
            if 'FROM incidents' in q:
                return _Result({'c': 0})
            if 'FROM monitored_systems ms' in q and 'ORDER BY ms.created_at DESC' in q:
                if workspace_id == 'ws-1':
                    return _Result(
                        rows=[
                            {'id': 'sys-1', 'workspace_id': 'ws-1', 'asset_id': 'asset-1', 'target_id': 'target-1', 'is_enabled': True, 'runtime_status': 'active', 'last_heartbeat': now.isoformat(), 'monitoring_interval_seconds': 30, 'created_at': now.isoformat()}
                        ]
                    )
                return _Result(
                    rows=[
                        {'id': 'sys-1', 'workspace_id': 'ws-2', 'asset_id': 'asset-1', 'target_id': 'target-1', 'is_enabled': True, 'runtime_status': 'active', 'last_heartbeat': now.isoformat(), 'monitoring_interval_seconds': 30, 'created_at': now.isoformat()},
                        {'id': 'sys-2', 'workspace_id': 'ws-2', 'asset_id': 'asset-2', 'target_id': 'target-2', 'is_enabled': True, 'runtime_status': 'active', 'last_heartbeat': now.isoformat(), 'monitoring_interval_seconds': 30, 'created_at': now.isoformat()},
                        {'id': 'sys-3', 'workspace_id': 'ws-2', 'asset_id': 'asset-3', 'target_id': 'target-3', 'is_enabled': True, 'runtime_status': 'idle', 'last_heartbeat': now.isoformat(), 'monitoring_interval_seconds': 30, 'created_at': now.isoformat()},
                        {'id': 'sys-4', 'workspace_id': 'ws-2', 'asset_id': 'asset-4', 'target_id': 'target-4', 'is_enabled': False, 'runtime_status': 'offline', 'last_heartbeat': now.isoformat(), 'monitoring_interval_seconds': 30, 'created_at': now.isoformat()},
                    ]
                )
            if 'LEFT JOIN assets a' in q and 'FROM targets t' in q:
                return _Result({'c': 0})
            if 'FROM evidence e WHERE e.workspace_id = %s' in q:
                return _Result({'observed_at': now - timedelta(seconds=20), 'block_number': 42})
            return _Result({})

    monkeypatch.setattr(
        monitoring_runner,
        'get_monitoring_health',
        lambda: {'last_heartbeat_at': now.isoformat(), 'last_cycle_at': now.isoformat(), 'degraded': False, 'last_error': None, 'source_type': 'polling'},
    )
    monkeypatch.setattr(monitoring_runner, 'ensure_pilot_schema', lambda _c: None)
    monkeypatch.setattr(monitoring_runner, 'pg_connection', lambda: _fake_pg(_WorkspaceConn()))
    monkeypatch.setattr(
        monitoring_runner,
        'resolve_workspace_context_for_request',
        lambda _c, _r: ({'id': 'user-1'}, {'workspace_id': 'ws-1', 'workspace': {'id': 'ws-1'}}, True),
    )
    monkeypatch.setattr(
        monitoring_runner,
        'list_workspace_monitored_system_rows',
        lambda _c, _w: [
            {'id': 'sys-1', 'workspace_id': 'ws-1', 'asset_id': 'asset-1', 'target_id': 'target-1', 'is_enabled': True, 'runtime_status': 'active', 'last_heartbeat': now.isoformat(), 'monitoring_interval_seconds': 30, 'created_at': now.isoformat()}
        ],
    )

    payload = monitoring_runner.monitoring_runtime_status(SimpleNamespace(headers={'x-workspace-id': 'ws-1'}))
    assert payload['monitored_systems'] == 1
    assert payload['enabled_systems'] == 1
    assert payload['active_systems'] == 1
    assert payload['monitoring_status'] == 'active'
    assert payload['counted_monitored_systems'] == 1
    assert payload['counted_enabled_systems'] == 1
    assert payload['workspace_header_present'] is True
    assert payload['request_user_resolved'] is True


def test_runtime_status_not_offline_when_workspace_has_enabled_monitored_systems(monkeypatch):
    now = datetime.now(timezone.utc)
    monkeypatch.setattr(
        monitoring_runner,
        'get_monitoring_health',
        lambda: {'last_heartbeat_at': now.isoformat(), 'last_cycle_at': now.isoformat(), 'degraded': False, 'last_error': None, 'source_type': 'polling', 'worker_running': True},
    )
    monkeypatch.setattr(monitoring_runner, 'ensure_pilot_schema', lambda _c: None)
    monkeypatch.setattr(monitoring_runner, 'pg_connection', lambda: _fake_pg(_Conn(now - timedelta(seconds=15))))
    payload = monitoring_runner.monitoring_runtime_status()
    assert payload['monitored_systems'] > 0
    assert payload['enabled_systems'] > 0
    assert payload['monitoring_status'] != 'offline'


def test_runtime_status_and_monitored_system_listing_use_same_workspace_rows(monkeypatch):
    now = datetime.now(timezone.utc).isoformat()

    class _RowsConn:
        def execute(self, query, params=None):
            q = ' '.join(str(query).split())
            if 'FROM alerts' in q or 'FROM incidents' in q:
                return _Result({'c': 0})
            if 'LEFT JOIN assets a' in q and 'FROM targets t' in q:
                return _Result({'c': 0})
            if 'FROM evidence' in q:
                return _Result({'observed_at': datetime.now(timezone.utc), 'block_number': 1})
            if 'FROM monitored_systems ms' in q and 'ORDER BY ms.created_at DESC' in q:
                return _Result(rows=[{'id': 'sys-1', 'workspace_id': 'ws-1', 'asset_id': 'asset-1', 'target_id': 'target-1', 'is_enabled': True, 'runtime_status': 'active', 'last_heartbeat': now, 'monitoring_interval_seconds': 30, 'created_at': now}])
            return _Result({})

    conn = _RowsConn()
    monkeypatch.setattr(
        monitoring_runner,
        'get_monitoring_health',
        lambda: {'last_heartbeat_at': datetime.now(timezone.utc).isoformat(), 'last_cycle_at': datetime.now(timezone.utc).isoformat(), 'degraded': False, 'last_error': None, 'source_type': 'polling', 'worker_running': True},
    )
    monkeypatch.setattr(monitoring_runner, 'ensure_pilot_schema', lambda _c: None)
    monkeypatch.setattr(monitoring_runner, 'pg_connection', lambda: _fake_pg(conn))
    monkeypatch.setattr(
        monitoring_runner,
        'resolve_workspace_context_for_request',
        lambda _c, _r: ({'id': 'user-1'}, {'workspace_id': 'ws-1', 'workspace': {'id': 'ws-1'}}, True),
    )
    monkeypatch.setattr(monitoring_runner, 'list_workspace_monitored_system_rows', lambda _c, _w: conn.execute('SELECT ... FROM monitored_systems ms ORDER BY ms.created_at DESC').fetchall())

    payload = monitoring_runner.monitoring_runtime_status(SimpleNamespace(headers={'x-workspace-id': 'ws-1'}))
    assert payload['monitored_systems'] == 1
    assert len(payload['counted_monitored_system_ids']) == 1


def test_runtime_status_workspace_resolution_reports_header_presence(monkeypatch):
    now = datetime.now(timezone.utc).isoformat()

    class _HeaderConn(_Conn):
        def execute(self, query, params=None):
            q = ' '.join(str(query).split())
            if 'FROM monitored_systems ms' in q and 'ORDER BY ms.created_at DESC' in q:
                return _Result(rows=[{'id': 'sys-1', 'workspace_id': 'ws-current', 'asset_id': 'asset-1', 'target_id': 'target-1', 'is_enabled': True, 'runtime_status': 'active', 'last_heartbeat': now, 'monitoring_interval_seconds': 30, 'created_at': now}])
            return super().execute(query, params)

    monkeypatch.setattr(
        monitoring_runner,
        'get_monitoring_health',
        lambda: {'last_heartbeat_at': datetime.now(timezone.utc).isoformat(), 'last_cycle_at': datetime.now(timezone.utc).isoformat(), 'degraded': False, 'last_error': None, 'source_type': 'polling', 'worker_running': True},
    )
    monkeypatch.setattr(monitoring_runner, 'ensure_pilot_schema', lambda _c: None)
    monkeypatch.setattr(monitoring_runner, 'pg_connection', lambda: _fake_pg(_HeaderConn(datetime.now(timezone.utc))))
    monkeypatch.setattr(
        monitoring_runner,
        'resolve_workspace_context_for_request',
        lambda _c, _r: ({'id': 'user-1'}, {'workspace_id': 'ws-current', 'workspace': {'id': 'ws-current'}}, False),
    )
    monkeypatch.setattr(
        monitoring_runner,
        'list_workspace_monitored_system_rows',
        lambda _c, _w: [{'id': 'sys-1', 'workspace_id': 'ws-current', 'asset_id': 'asset-1', 'target_id': 'target-1', 'is_enabled': True, 'runtime_status': 'active', 'last_heartbeat': now, 'monitoring_interval_seconds': 30, 'created_at': now}],
    )

    payload = monitoring_runner.monitoring_runtime_status(SimpleNamespace(headers={}))
    assert payload['resolved_workspace_id'] == 'ws-current'
    assert payload['workspace_header_present'] is False
