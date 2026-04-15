from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

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
        if 'COUNT(*) AS target_count' in q and 'COUNT(DISTINCT t.asset_id) AS asset_count' in q:
            return _Result({'target_count': 2, 'asset_count': 2})
        if 'SELECT t.id' in q and 'FROM targets t' in q and 'JOIN assets a' in q:
            return _Result(rows=[{'id': 'target-1'}, {'id': 'target-2'}])
        if 'FROM evidence' in q:
            return _Result({'observed_at': self.evidence_at, 'block_number': 123})
        if 'FROM analysis_runs' in q:
            return _Result(None)
        return _Result({})


@contextmanager
def _fake_pg(conn):
    yield conn


def _enable_live_mode(monkeypatch):
    monkeypatch.setattr(monitoring_runner, 'live_mode_enabled', lambda: True)
    monkeypatch.setattr(
        monitoring_runner,
        'production_claim_validator',
        lambda: {'checks': {'evm_rpc_reachable': True}, 'sales_claims_allowed': False, 'status': 'FAIL', 'recent_truthfulness_state': 'unknown_risk'},
    )


@pytest.fixture(autouse=True)
def _runtime_defaults(monkeypatch):
    _enable_live_mode(monkeypatch)


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
    assert payload['workspace_monitoring_summary']['runtime_status'] in {'idle', 'healthy'}
    assert payload['workspace_monitoring_summary']['coverage_state']['configured_systems'] == 2
    assert payload['workspace_monitoring_summary']['freshness_status'] in {'fresh', 'stale', 'unavailable'}
    assert payload['workspace_monitoring_summary']['contradiction_flags'] == []


def test_runtime_status_counts_protected_assets_from_enabled_systems_not_only_active(monkeypatch):
    now = datetime.now(timezone.utc)

    class _IdleEnabledConn(_Conn):
        def execute(self, query, params=None):
            q = ' '.join(str(query).split())
            if 'FROM monitored_systems ms' in q and 'ORDER BY ms.created_at DESC' in q:
                now_iso = now.isoformat()
                return _Result(
                    rows=[
                        {'id': 'sys-1', 'workspace_id': 'ws-1', 'asset_id': 'asset-1', 'target_id': 'target-1', 'is_enabled': True, 'runtime_status': 'idle', 'last_heartbeat': now_iso, 'monitoring_interval_seconds': 30, 'created_at': now_iso},
                        {'id': 'sys-2', 'workspace_id': 'ws-1', 'asset_id': 'asset-2', 'target_id': 'target-2', 'is_enabled': True, 'runtime_status': 'idle', 'last_heartbeat': now_iso, 'monitoring_interval_seconds': 30, 'created_at': now_iso},
                    ]
                )
            return super().execute(query, params)

    monkeypatch.setattr(
        monitoring_runner,
        'get_monitoring_health',
        lambda: {'last_heartbeat_at': now.isoformat(), 'last_cycle_at': now.isoformat(), 'degraded': False, 'last_error': None, 'source_type': 'polling'},
    )
    monkeypatch.setattr(monitoring_runner, 'ensure_pilot_schema', lambda _c: None)
    monkeypatch.setattr(monitoring_runner, 'pg_connection', lambda: _fake_pg(_IdleEnabledConn(None)))

    payload = monitoring_runner.monitoring_runtime_status()
    assert payload['protected_assets'] == 2
    assert payload['protected_assets_count'] == 2
    assert payload['active_systems'] == 0
    assert payload['status'] == 'Idle'
    assert payload['monitoring_status'] == 'idle'


def test_runtime_status_coverage_uses_recent_heartbeats(monkeypatch):
    now = datetime.now(timezone.utc)

    class _MixedHeartbeatConn(_Conn):
        def execute(self, query, params=None):
            q = ' '.join(str(query).split())
            if 'FROM monitored_systems ms' in q and 'ORDER BY ms.created_at DESC' in q:
                return _Result(
                    rows=[
                        {'id': 'sys-1', 'workspace_id': 'ws-1', 'asset_id': 'asset-1', 'target_id': 'target-1', 'is_enabled': True, 'runtime_status': 'idle', 'last_heartbeat': now.isoformat(), 'monitoring_interval_seconds': 30, 'created_at': now.isoformat()},
                        {'id': 'sys-2', 'workspace_id': 'ws-1', 'asset_id': 'asset-2', 'target_id': 'target-2', 'is_enabled': True, 'runtime_status': 'idle', 'last_heartbeat': (now - timedelta(minutes=5)).isoformat(), 'monitoring_interval_seconds': 30, 'created_at': now.isoformat()},
                    ]
                )
            return super().execute(query, params)

    monkeypatch.setattr(
        monitoring_runner,
        'get_monitoring_health',
        lambda: {'last_heartbeat_at': now.isoformat(), 'last_cycle_at': now.isoformat(), 'degraded': False, 'last_error': None, 'source_type': 'polling', 'worker_running': True},
    )
    monkeypatch.setattr(monitoring_runner, 'ensure_pilot_schema', lambda _c: None)
    monkeypatch.setattr(monitoring_runner, 'pg_connection', lambda: _fake_pg(_MixedHeartbeatConn(None)))

    payload = monitoring_runner.monitoring_runtime_status()
    assert payload['systems_with_recent_heartbeat'] == 1
    assert payload['monitored_systems'] == 2
    assert payload['status'] == 'Idle'


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


def test_runtime_status_not_degraded_solely_for_zero_event_idle_systems(monkeypatch):
    now = datetime.now(timezone.utc)

    class _ZeroEventHealthyConn(_Conn):
        def execute(self, query, params=None):
            q = ' '.join(str(query).split())
            if 'FROM monitored_systems ms' in q and 'ORDER BY ms.created_at DESC' in q:
                return _Result(
                    rows=[
                        {'id': 'sys-1', 'workspace_id': 'ws-1', 'asset_id': 'asset-1', 'target_id': 'target-1', 'is_enabled': True, 'runtime_status': 'idle', 'last_heartbeat': now.isoformat(), 'monitoring_interval_seconds': 30, 'created_at': now.isoformat()},
                    ]
                )
            return super().execute(query, params)

    monkeypatch.setattr(
        monitoring_runner,
        'get_monitoring_health',
        lambda: {'last_heartbeat_at': now.isoformat(), 'last_cycle_at': now.isoformat(), 'degraded': False, 'last_error': None, 'source_type': 'polling', 'worker_running': True},
    )
    monkeypatch.setattr(monitoring_runner, 'ensure_pilot_schema', lambda _c: None)
    monkeypatch.setattr(monitoring_runner, 'pg_connection', lambda: _fake_pg(_ZeroEventHealthyConn(None)))

    payload = monitoring_runner.monitoring_runtime_status()
    assert payload['monitoring_status'] == 'idle'
    assert payload['status'] == 'Idle'
    assert payload['workspace_monitoring_summary']['runtime_status'] in {'idle', 'degraded'}
    assert payload['workspace_monitoring_summary']['coverage_state']['reporting_systems'] == 0
    assert payload['workspace_monitoring_summary']['contradiction_flags'] == []


def test_runtime_status_workspace_unconfigured_false_when_coverage_exists(monkeypatch):
    now = datetime.now(timezone.utc)

    class _ConfiguredConn(_Conn):
        def execute(self, query, params=None):
            q = ' '.join(str(query).split())
            if 'FROM monitored_systems ms' in q and 'ORDER BY ms.created_at DESC' in q:
                return _Result(
                    rows=[
                        {'id': 'sys-1', 'workspace_id': 'ws-1', 'asset_id': 'asset-1', 'target_id': 'target-1', 'is_enabled': True, 'runtime_status': 'idle', 'last_heartbeat': now.isoformat(), 'last_event_at': None, 'monitoring_interval_seconds': 30, 'created_at': now.isoformat()},
                    ]
                )
            if 'COUNT(*) AS target_count' in q and 'COUNT(DISTINCT t.asset_id) AS asset_count' in q:
                return _Result({'target_count': 1, 'asset_count': 1})
            if 'SELECT t.id' in q and 'FROM targets t' in q and 'JOIN assets a' in q:
                return _Result(rows=[{'id': 'target-1'}])
            return super().execute(query, params)

    monkeypatch.setattr(
        monitoring_runner,
        'get_monitoring_health',
        lambda: {'last_heartbeat_at': now.isoformat(), 'last_cycle_at': now.isoformat(), 'degraded': False, 'last_error': None, 'source_type': 'polling', 'worker_running': True},
    )
    monkeypatch.setattr(monitoring_runner, 'ensure_pilot_schema', lambda _c: None)
    monkeypatch.setattr(monitoring_runner, 'pg_connection', lambda: _fake_pg(_ConfiguredConn(None)))

    payload = monitoring_runner.monitoring_runtime_status()
    assert payload['workspace_monitoring_summary']['workspace_configured'] is True
    assert 'workspace_unconfigured_with_coverage' not in payload['workspace_monitoring_summary']['contradiction_flags']


def test_runtime_status_includes_recent_successful_checkpoint_without_events(monkeypatch):
    now = datetime.now(timezone.utc)

    class _SuccessfulCycleConn(_Conn):
        def execute(self, query, params=None):
            q = ' '.join(str(query).split())
            if 'FROM monitored_systems ms' in q and 'ORDER BY ms.created_at DESC' in q:
                return _Result(
                    rows=[
                        {'id': 'sys-1', 'workspace_id': 'ws-1', 'asset_id': 'asset-1', 'target_id': 'target-1', 'is_enabled': True, 'runtime_status': 'idle', 'last_heartbeat': now.isoformat(), 'monitoring_interval_seconds': 30, 'created_at': now.isoformat()},
                    ]
                )
            if 'FROM analysis_runs' in q:
                return _Result(
                    {
                        'created_at': now - timedelta(seconds=45),
                        'response_payload': {
                            'metadata': {
                                'detection_outcome': 'NO_CONFIRMED_ANOMALY_FROM_REAL_EVIDENCE',
                                'evidence_state': 'real',
                                'confidence_basis': 'provider_evidence',
                                'recent_real_event_count': 0,
                            }
                        },
                    }
                )
            return super().execute(query, params)

    monkeypatch.setattr(
        monitoring_runner,
        'get_monitoring_health',
        lambda: {'last_heartbeat_at': now.isoformat(), 'last_cycle_at': now.isoformat(), 'degraded': False, 'last_error': None, 'source_type': 'polling', 'worker_running': True},
    )
    monkeypatch.setattr(monitoring_runner, 'ensure_pilot_schema', lambda _c: None)
    monkeypatch.setattr(monitoring_runner, 'pg_connection', lambda: _fake_pg(_SuccessfulCycleConn(None)))

    payload = monitoring_runner.monitoring_runtime_status()
    assert payload['successful_detection_evaluation'] is True
    assert payload['successful_detection_evaluation_recent'] is True
    assert payload['last_confirmed_checkpoint'] is not None


def test_runtime_status_counts_workspace_rows_even_when_target_join_metadata_is_missing(monkeypatch):
    now = datetime.now(timezone.utc)

    class _OrphanRowConn(_Conn):
        def execute(self, query, params=None):
            q = ' '.join(str(query).split())
            if 'FROM monitored_systems ms' in q and 'ORDER BY ms.created_at DESC' in q:
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
                            'last_heartbeat': now.isoformat(),
                            'monitoring_interval_seconds': 30,
                            'created_at': now.isoformat(),
                        }
                    ]
                )
            if 'COUNT(*) AS target_count' in q and 'COUNT(DISTINCT t.asset_id) AS asset_count' in q:
                return _Result({'target_count': 0, 'asset_count': 0})
            if 'SELECT t.id' in q and 'FROM targets t' in q and 'JOIN assets a' in q:
                return _Result(rows=[])
            return super().execute(query, params)

    monkeypatch.setattr(
        monitoring_runner,
        'get_monitoring_health',
        lambda: {'last_heartbeat_at': now.isoformat(), 'last_cycle_at': now.isoformat(), 'degraded': False, 'last_error': None, 'source_type': 'polling', 'worker_running': True},
    )
    monkeypatch.setattr(monitoring_runner, 'ensure_pilot_schema', lambda _c: None)
    monkeypatch.setattr(monitoring_runner, 'pg_connection', lambda: _fake_pg(_OrphanRowConn(None)))
    monkeypatch.setattr(
        monitoring_runner,
        'resolve_workspace_context_for_request',
        lambda *_a, **_k: ({'id': 'user-1'}, {'workspace_id': 'ws-1', 'workspace': {'id': 'ws-1'}}, True),
    )
    monkeypatch.setattr(
        monitoring_runner,
        'reconcile_enabled_targets_monitored_systems',
        lambda *_a, **_k: {'created_or_updated': 0, 'created_monitored_systems': 0, 'preserved_monitored_systems': 1, 'removed_monitored_systems': 0},
    )

    payload = monitoring_runner.monitoring_runtime_status(request=SimpleNamespace(headers={'authorization': 'Bearer token', 'x-workspace-id': 'ws-1'}))
    assert payload['monitored_systems'] == 1
    assert payload['protected_assets'] == 1
    assert payload['status'] != 'Offline'
    assert payload['monitoring_status'] != 'offline'
    assert payload['recent_real_event_count'] == 0


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


def test_runtime_status_stays_degraded_when_linked_asset_missing_exists(monkeypatch):
    now = datetime.now(timezone.utc)

    class _LinkedAssetMissingConn(_Conn):
        def execute(self, query, params=None):
            q = ' '.join(str(query).split())
            if 'LEFT JOIN assets a' in q and 'FROM targets t' in q:
                return _Result({'c': 1})
            return super().execute(query, params)

    monkeypatch.setattr(
        monitoring_runner,
        'get_monitoring_health',
        lambda: {'last_heartbeat_at': now.isoformat(), 'last_cycle_at': now.isoformat(), 'degraded': False, 'last_error': None, 'source_type': 'polling'},
    )
    monkeypatch.setattr(monitoring_runner, 'ensure_pilot_schema', lambda _c: None)
    monkeypatch.setattr(monitoring_runner, 'pg_connection', lambda: _fake_pg(_LinkedAssetMissingConn(now - timedelta(seconds=30))))

    payload = monitoring_runner.monitoring_runtime_status()
    assert payload['status'] == 'Degraded'
    assert payload['degraded_reason'] == 'invalid_enabled_targets'
    assert payload['invalid_enabled_targets'] == 1


def test_runtime_status_offline_without_active_systems(monkeypatch):
    now = datetime.now(timezone.utc)

    class _OfflineConn(_Conn):
        def execute(self, query, params=None):
            q = ' '.join(str(query).split())
            if 'FROM monitored_systems ms' in q and 'ORDER BY ms.created_at DESC' in q:
                rows = super().execute(query, params)._rows
                return _Result(rows=[{**row, 'is_enabled': False, 'runtime_status': 'offline'} for row in rows])
            if 'COUNT(*) AS target_count' in q and 'COUNT(DISTINCT t.asset_id) AS asset_count' in q:
                return _Result({'target_count': 0, 'asset_count': 0})
            return super().execute(query, params)

    monkeypatch.setattr(
        monitoring_runner,
        'get_monitoring_health',
        lambda: {'last_heartbeat_at': now.isoformat(), 'last_cycle_at': now.isoformat(), 'degraded': False, 'last_error': None, 'source_type': 'polling', 'worker_running': False},
    )
    monkeypatch.setattr(monitoring_runner, 'ensure_pilot_schema', lambda _c: None)
    monkeypatch.setattr(monitoring_runner, 'pg_connection', lambda: _fake_pg(_OfflineConn(now - timedelta(seconds=30))))
    payload = monitoring_runner.monitoring_runtime_status()
    assert payload['monitored_systems'] > 0
    assert payload['monitoring_status'] != 'offline'
    assert payload['status'] != 'Offline'


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


def test_runtime_status_not_offline_when_valid_enabled_targets_exist_without_rows(monkeypatch):
    now = datetime.now(timezone.utc)

    class _HealthyTargetsNoRowsConn(_Conn):
        def execute(self, query, params=None):
            q = ' '.join(str(query).split())
            if 'FROM monitored_systems ms' in q and 'ORDER BY ms.created_at DESC' in q:
                return _Result(rows=[])
            if 'COUNT(*) AS target_count' in q and 'COUNT(DISTINCT t.asset_id) AS asset_count' in q:
                return _Result({'target_count': 1, 'asset_count': 1})
            return super().execute(query, params)

    reconcile_calls: list[str | None] = []
    monkeypatch.setattr(
        monitoring_runner,
        'reconcile_enabled_targets_monitored_systems',
        lambda _c, workspace_id=None: reconcile_calls.append(workspace_id) or {'created_or_updated': 0},
    )
    monkeypatch.setattr(
        monitoring_runner,
        'get_monitoring_health',
        lambda: {'last_heartbeat_at': now.isoformat(), 'last_cycle_at': now.isoformat(), 'degraded': False, 'last_error': None, 'source_type': 'polling', 'worker_running': True},
    )
    monkeypatch.setattr(monitoring_runner, 'ensure_pilot_schema', lambda _c: None)
    monkeypatch.setattr(monitoring_runner, 'pg_connection', lambda: _fake_pg(_HealthyTargetsNoRowsConn(None)))

    payload = monitoring_runner.monitoring_runtime_status()
    assert reconcile_calls == [None]
    assert payload['monitoring_status'] != 'offline'
    assert payload['status'] != 'Offline'
    assert payload['monitored_systems'] >= 1
    assert payload['protected_assets'] >= 1


def test_runtime_status_not_offline_when_workspace_has_monitored_rows_but_no_enabled_targets(monkeypatch):
    now = datetime.now(timezone.utc)

    class _OnlyDisabledMonitoredRowsConn(_Conn):
        def execute(self, query, params=None):
            q = ' '.join(str(query).split())
            if 'FROM monitored_systems ms' in q and 'ORDER BY ms.created_at DESC' in q:
                return _Result(
                    rows=[
                        {'id': 'sys-disabled', 'workspace_id': 'ws-1', 'asset_id': 'asset-1', 'target_id': 'target-1', 'is_enabled': False, 'runtime_status': 'offline', 'last_heartbeat': now.isoformat(), 'monitoring_interval_seconds': 30, 'created_at': now.isoformat()},
                    ]
                )
            if 'COUNT(*) AS target_count' in q and 'COUNT(DISTINCT t.asset_id) AS asset_count' in q:
                return _Result({'target_count': 0, 'asset_count': 0})
            return super().execute(query, params)

    monkeypatch.setattr(
        monitoring_runner,
        'get_monitoring_health',
        lambda: {'last_heartbeat_at': now.isoformat(), 'last_cycle_at': now.isoformat(), 'degraded': False, 'last_error': None, 'source_type': 'polling', 'worker_running': True},
    )
    monkeypatch.setattr(monitoring_runner, 'ensure_pilot_schema', lambda _c: None)
    monkeypatch.setattr(monitoring_runner, 'pg_connection', lambda: _fake_pg(_OnlyDisabledMonitoredRowsConn(None)))

    payload = monitoring_runner.monitoring_runtime_status()
    assert payload['monitored_systems'] == 1
    assert payload['monitoring_status'] != 'offline'
    assert payload['status'] != 'Offline'


def test_runtime_status_triggers_reconcile_when_enabled_rows_missing_for_healthy_targets(monkeypatch):
    now = datetime.now(timezone.utc)

    class _OnlyDisabledRowsConn(_Conn):
        def execute(self, query, params=None):
            q = ' '.join(str(query).split())
            if 'FROM monitored_systems ms' in q and 'ORDER BY ms.created_at DESC' in q:
                return _Result(
                    rows=[
                        {'id': 'sys-disabled', 'workspace_id': 'ws-1', 'asset_id': 'asset-1', 'target_id': 'target-1', 'is_enabled': False, 'runtime_status': 'offline', 'last_heartbeat': now.isoformat(), 'monitoring_interval_seconds': 30, 'created_at': now.isoformat()},
                    ]
                )
            if 'COUNT(*) AS target_count' in q and 'COUNT(DISTINCT t.asset_id) AS asset_count' in q:
                return _Result({'target_count': 1, 'asset_count': 1})
            return super().execute(query, params)

    reconcile_calls: list[str | None] = []
    monkeypatch.setattr(
        monitoring_runner,
        'reconcile_enabled_targets_monitored_systems',
        lambda _c, workspace_id=None: reconcile_calls.append(workspace_id) or {'created_or_updated': 1, 'created_monitored_systems': 1, 'preserved_monitored_systems': 0, 'removed_monitored_systems': 0},
    )
    monkeypatch.setattr(
        monitoring_runner,
        'get_monitoring_health',
        lambda: {'last_heartbeat_at': now.isoformat(), 'last_cycle_at': now.isoformat(), 'degraded': False, 'last_error': None, 'source_type': 'polling', 'worker_running': True},
    )
    monkeypatch.setattr(monitoring_runner, 'ensure_pilot_schema', lambda _c: None)
    monkeypatch.setattr(monitoring_runner, 'pg_connection', lambda: _fake_pg(_OnlyDisabledRowsConn(None)))

    payload = monitoring_runner.monitoring_runtime_status()
    assert reconcile_calls == [None]
    assert payload['monitoring_status'] != 'offline'


def test_runtime_status_triggers_reconcile_when_healthy_target_ids_are_missing_even_if_counts_match(monkeypatch):
    now = datetime.now(timezone.utc)

    class _MismatchedTargetRowsConn(_Conn):
        def execute(self, query, params=None):
            q = ' '.join(str(query).split())
            if 'FROM monitored_systems ms' in q and 'ORDER BY ms.created_at DESC' in q:
                return _Result(
                    rows=[
                        {'id': 'sys-enabled', 'workspace_id': 'ws-1', 'asset_id': 'asset-9', 'target_id': 'target-stale', 'is_enabled': True, 'runtime_status': 'idle', 'last_heartbeat': now.isoformat(), 'monitoring_interval_seconds': 30, 'created_at': now.isoformat()},
                    ]
                )
            if 'COUNT(*) AS target_count' in q and 'COUNT(DISTINCT t.asset_id) AS asset_count' in q:
                return _Result({'target_count': 1, 'asset_count': 1})
            if 'SELECT t.id' in q and 'FROM targets t' in q and 'JOIN assets a' in q:
                return _Result(rows=[{'id': 'target-healthy'}])
            return super().execute(query, params)

    reconcile_calls: list[str | None] = []
    monkeypatch.setattr(
        monitoring_runner,
        'reconcile_enabled_targets_monitored_systems',
        lambda _c, workspace_id=None: reconcile_calls.append(workspace_id) or {'created_or_updated': 1, 'created_monitored_systems': 1, 'preserved_monitored_systems': 0, 'removed_monitored_systems': 0},
    )
    monkeypatch.setattr(
        monitoring_runner,
        'get_monitoring_health',
        lambda: {'last_heartbeat_at': now.isoformat(), 'last_cycle_at': now.isoformat(), 'degraded': False, 'last_error': None, 'source_type': 'polling', 'worker_running': True},
    )
    monkeypatch.setattr(monitoring_runner, 'ensure_pilot_schema', lambda _c: None)
    monkeypatch.setattr(monitoring_runner, 'pg_connection', lambda: _fake_pg(_MismatchedTargetRowsConn(None)))

    payload = monitoring_runner.monitoring_runtime_status()
    assert reconcile_calls == [None]
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


def test_runtime_status_workspace_scoped_path_uses_same_rows_as_monitored_systems_listing(monkeypatch):
    now = datetime.now(timezone.utc).isoformat()

    class _ScopedRowsConn(_Conn):
        def execute(self, query, params=None):
            q = ' '.join(str(query).split())
            if 'FROM monitored_systems ms' in q and 'ORDER BY ms.created_at DESC' in q:
                return _Result(
                    rows=[
                        {
                            'id': 'sys-1',
                            'workspace_id': 'ws-1',
                            'asset_id': 'asset-1',
                            'target_id': 'target-1',
                            'chain': 'ethereum-mainnet',
                            'is_enabled': True,
                            'runtime_status': 'idle',
                            'status': 'ready',
                            'last_heartbeat': now,
                            'monitoring_interval_seconds': 30,
                            'created_at': now,
                        }
                    ]
                )
            if 'FROM alerts' in q or 'FROM incidents' in q:
                return _Result({'c': 0})
            if 'COUNT(*) AS target_count' in q and 'COUNT(DISTINCT t.asset_id) AS asset_count' in q:
                return _Result({'target_count': 1, 'asset_count': 1})
            if 'SELECT t.id' in q and 'FROM targets t' in q and 'JOIN assets a' in q:
                return _Result(rows=[{'id': 'target-1'}])
            if 'FROM evidence e WHERE e.workspace_id = %s' in q:
                return _Result({'observed_at': datetime.now(timezone.utc), 'block_number': 5})
            return _Result({})

    monkeypatch.setattr(
        monitoring_runner,
        'get_monitoring_health',
        lambda: {'last_heartbeat_at': datetime.now(timezone.utc).isoformat(), 'last_cycle_at': datetime.now(timezone.utc).isoformat(), 'degraded': False, 'last_error': None, 'source_type': 'polling', 'worker_running': True},
    )
    monkeypatch.setattr(monitoring_runner, 'ensure_pilot_schema', lambda _c: None)
    monkeypatch.setattr(monitoring_runner, 'pg_connection', lambda: _fake_pg(_ScopedRowsConn(datetime.now(timezone.utc))))
    monkeypatch.setattr(
        monitoring_runner,
        'resolve_workspace_context_for_request',
        lambda _c, _r: ({'id': 'user-1'}, {'workspace_id': 'ws-1', 'workspace': {'id': 'ws-1'}}, True),
    )

    payload = monitoring_runner.monitoring_runtime_status(SimpleNamespace(headers={'x-workspace-id': 'ws-1'}))
    assert payload['monitored_systems'] == 1
    assert payload['protected_assets'] == 1
    assert payload['enabled_systems'] == 1
    assert payload['monitoring_status'] != 'offline'
