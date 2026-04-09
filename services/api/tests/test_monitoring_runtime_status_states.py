from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from services.api.app import monitoring_runner


class _Result:
    def __init__(self, row=None):
        self._row = row or {}

    def fetchone(self):
        return self._row


class _Conn:
    def __init__(self, evidence_at: datetime | None):
        self.evidence_at = evidence_at

    def execute(self, query, params=None):
        q = ' '.join(str(query).split())
        if 'FROM alerts' in q:
            return _Result({'c': 1})
        if 'FROM incidents' in q:
            return _Result({'c': 1})
        if 'FROM ( FROM monitored_systems ms JOIN targets t ON t.id = ms.target_id WHERE t.deleted_at IS NULL ) scoped' in q and 'scoped.is_enabled = TRUE' in q and "scoped.runtime_status = 'active'" not in q and 'scoped.last_heartbeat IS NOT NULL' not in q:
            return _Result({'c': 2})
        if 'FROM ( FROM monitored_systems ms JOIN targets t ON t.id = ms.target_id WHERE t.deleted_at IS NULL ) scoped' in q and "scoped.runtime_status = 'active'" in q and 'COUNT(DISTINCT scoped.asset_id)' not in q:
            return _Result({'c': 2})
        if 'FROM ( FROM monitored_systems ms JOIN targets t ON t.id = ms.target_id WHERE t.deleted_at IS NULL ) scoped' in q and 'WHERE 1 = 1' in q:
            return _Result({'c': 3})
        if 'COUNT(DISTINCT scoped.asset_id)' in q:
            return _Result({'c': 2})
        if 'MAX(scoped.last_heartbeat)' in q:
            return _Result({'ts': datetime.now(timezone.utc).isoformat()})
        if 'scoped.last_heartbeat IS NOT NULL' in q:
            return _Result({'c': 2})
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
    monkeypatch.setattr(
        monitoring_runner,
        'get_monitoring_health',
        lambda: {'last_heartbeat_at': (now - timedelta(minutes=20)).isoformat(), 'last_cycle_at': (now - timedelta(minutes=20)).isoformat(), 'degraded': False, 'last_error': None, 'source_type': 'polling'},
    )
    monkeypatch.setattr(monitoring_runner, 'ensure_pilot_schema', lambda _c: None)
    monkeypatch.setattr(monitoring_runner, 'pg_connection', lambda: _fake_pg(_Conn(now - timedelta(seconds=30))))

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
            if 'scoped.is_enabled = TRUE' in q and "scoped.runtime_status = 'active'" not in q:
                return _Result({'c': 0})
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
            workspace_id = (params or (None,))[0]
            if 'FROM alerts' in q:
                return _Result({'c': 0})
            if 'FROM incidents' in q:
                return _Result({'c': 0})
            if 'scoped.is_enabled = TRUE' in q and "scoped.runtime_status = 'active'" not in q and 'scoped.last_heartbeat IS NOT NULL' not in q:
                return _Result({'c': 1 if workspace_id == 'ws-1' else 3})
            if "scoped.runtime_status = 'active'" in q and 'COUNT(DISTINCT scoped.asset_id)' not in q:
                return _Result({'c': 1 if workspace_id == 'ws-1' else 2})
            if 'WHERE 1 = 1' in q and 'FROM ( FROM monitored_systems ms JOIN targets t ON t.id = ms.target_id WHERE t.deleted_at IS NULL ) scoped' in q:
                return _Result({'c': 1 if workspace_id == 'ws-1' else 4})
            if 'COUNT(DISTINCT scoped.asset_id)' in q:
                return _Result({'c': 1 if workspace_id == 'ws-1' else 3})
            if 'MAX(scoped.last_heartbeat)' in q:
                return _Result({'ts': now.isoformat()})
            if 'scoped.last_heartbeat IS NOT NULL' in q:
                return _Result({'c': 1 if workspace_id == 'ws-1' else 3})
            if 'LEFT JOIN assets a' in q and 'FROM targets t' in q:
                return _Result({'c': 0})
            if 'FROM evidence e WHERE e.workspace_id = %s::uuid' in q:
                return _Result({'observed_at': now - timedelta(seconds=20), 'block_number': 42})
            return _Result({})

    monkeypatch.setattr(
        monitoring_runner,
        'get_monitoring_health',
        lambda: {'last_heartbeat_at': now.isoformat(), 'last_cycle_at': now.isoformat(), 'degraded': False, 'last_error': None, 'source_type': 'polling'},
    )
    monkeypatch.setattr(monitoring_runner, 'ensure_pilot_schema', lambda _c: None)
    monkeypatch.setattr(monitoring_runner, 'pg_connection', lambda: _fake_pg(_WorkspaceConn()))
    monkeypatch.setattr(monitoring_runner, 'authenticate_with_connection', lambda _c, _r: {'id': 'user-1'})
    monkeypatch.setattr(monitoring_runner, 'resolve_workspace', lambda _c, _u, _h: {'workspace_id': 'ws-1', 'workspace': {'id': 'ws-1'}})

    payload = monitoring_runner.monitoring_runtime_status(SimpleNamespace(headers={'x-workspace-id': 'ws-1'}))
    assert payload['monitored_systems'] == 1
    assert payload['enabled_systems'] == 1
    assert payload['active_systems'] == 1
    assert payload['monitoring_status'] == 'active'
    assert payload['counted_monitored_systems'] == 1
    assert payload['counted_enabled_systems'] == 1


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
