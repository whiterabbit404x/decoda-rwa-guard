from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi import HTTPException, status
from psycopg.errors import SyntaxError as PsycopgSyntaxError

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
    monkeypatch.setattr(monitoring_runner, 'ensure_monitoring_runtime_schema_capabilities', lambda _c: None)


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
    assert payload['workspace_monitoring_summary']['last_heartbeat_at'] is not None
    assert payload['workspace_monitoring_summary']['field_reason_codes'].get('configured_systems') != ['query_failure']
    assert payload['workspace_monitoring_summary']['field_reason_codes'].get('protected_assets') != ['query_failure']


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
            if 'FROM analysis_runs' in q:
                return _Result({'created_at': now, 'response_payload': {'metadata': {'recent_real_event_count': 1, 'last_real_event_at': now.isoformat(), 'detection_outcome': 'NO_CONFIRMED_ANOMALY_FROM_REAL_EVIDENCE'}}})
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


def test_runtime_status_unconfigured_reason_codes_and_contract_keys_are_deterministic(monkeypatch):
    now = datetime.now(timezone.utc)

    class _UnconfiguredConn(_Conn):
        def execute(self, query, params=None):
            q = ' '.join(str(query).split())
            if 'FROM monitored_systems ms' in q and 'ORDER BY ms.created_at DESC' in q:
                return _Result(rows=[])
            if 'COUNT(*) AS target_count' in q and 'COUNT(DISTINCT t.asset_id) AS asset_count' in q:
                return _Result({'target_count': 0, 'asset_count': 0})
            if 'SELECT t.id' in q and 'FROM targets t' in q and 'JOIN assets a' in q:
                return _Result(rows=[])
            if 'FROM analysis_runs' in q:
                return _Result(None)
            return super().execute(query, params)

    monkeypatch.setattr(monitoring_runner, 'live_mode_enabled', lambda: True)
    monkeypatch.setattr(
        monitoring_runner,
        'get_monitoring_health',
        lambda: {
            'last_heartbeat_at': now.isoformat(),
            'last_cycle_at': now.isoformat(),
            'ingestion_mode': 'live',
            'operational_mode': 'LIVE',
            'degraded': False,
            'last_error': None,
            'source_type': 'polling',
            'worker_running': True,
        },
    )
    monkeypatch.setattr(monitoring_runner, 'ensure_pilot_schema', lambda *_: None)
    monkeypatch.setattr(monitoring_runner, 'pg_connection', lambda: _fake_pg(_UnconfiguredConn(None)))
    monkeypatch.setattr(
        monitoring_runner,
        'production_claim_validator',
        lambda: {
            'checks': {'evm_rpc_reachable': True},
            'sales_claims_allowed': False,
            'status': 'FAIL',
            'recent_truthfulness_state': 'unknown_risk',
            'recent_evidence_state': 'missing',
            'recent_real_event_count': 0,
        },
    )

    payload = monitoring_runner.monitoring_runtime_status()

    assert payload['workspace_configured'] is False
    assert payload['status_reason'] == 'workspace_configuration_invalid:no_valid_protected_assets'
    assert payload['configuration_reason_codes'] == [
        'no_valid_protected_assets',
        'no_linked_monitored_systems',
        'no_persisted_enabled_monitoring_config',
        'target_system_linkage_invalid',
    ]
    assert payload['workspace_monitoring_summary']['configuration_reason_codes'] == payload['configuration_reason_codes']
    assert isinstance(payload['count_reason_codes'], dict)
    for counter_key in (
        'raw_enabled_targets',
        'monitorable_enabled_targets',
        'valid_asset_linked_targets',
        'enabled_monitored_systems',
        'valid_target_system_links',
    ):
        assert counter_key in payload
    assert set(payload['workspace_monitoring_summary']['field_reason_codes'].keys()) == {
        'protected_assets',
        'configured_systems',
        'reporting_systems',
        'last_poll_at',
        'last_heartbeat_at',
        'last_telemetry_at',
    }
    assert payload['workspace_monitoring_summary']['field_reason_codes']['protected_assets'] == ['unconfigured_workspace']
    assert payload['workspace_monitoring_summary']['field_reason_codes']['configured_systems'] == ['unconfigured_workspace']
    assert payload['workspace_monitoring_summary']['field_reason_codes']['reporting_systems'] == ['unconfigured_workspace']


def test_runtime_status_promotes_to_reporting_system_with_simulator_coverage(monkeypatch):
    now = datetime.now(timezone.utc)

    class _SimulatorTelemetryConn(_Conn):
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
                            'is_enabled': True,
                            'runtime_status': 'healthy',
                            'last_heartbeat': now.isoformat(),
                            'last_event_at': None,
                            'last_coverage_telemetry_at': (now - timedelta(seconds=20)).isoformat(),
                            'monitoring_interval_seconds': 30,
                            'created_at': now.isoformat(),
                        },
                    ]
                )
            if 'COUNT(*) AS target_count' in q and 'COUNT(DISTINCT t.asset_id) AS asset_count' in q:
                return _Result({'target_count': 1, 'asset_count': 1})
            if 'SELECT t.id' in q and 'FROM targets t' in q and 'JOIN assets a' in q:
                return _Result(rows=[{'id': 'target-1', 'asset_id': 'asset-1'}])
            if 'FROM evidence' in q:
                return _Result({'observed_at': now - timedelta(seconds=20), 'block_number': 1})
            if 'FROM analysis_runs' in q:
                return _Result({'created_at': now, 'response_payload': {'metadata': {'recent_real_event_count': 0}}})
            return super().execute(query, params)

    monkeypatch.setattr(
        monitoring_runner,
        'get_monitoring_health',
        lambda: {
            'last_heartbeat_at': now.isoformat(),
            'last_cycle_at': now.isoformat(),
            'degraded': False,
            'last_error': None,
            'source_type': 'polling',
            'ingestion_mode': 'demo',
            'worker_running': True,
        },
    )
    monkeypatch.setattr(monitoring_runner, 'ensure_pilot_schema', lambda _c: None)
    monkeypatch.setattr(monitoring_runner, 'pg_connection', lambda: _fake_pg(_SimulatorTelemetryConn(now - timedelta(seconds=20))))

    payload = monitoring_runner.monitoring_runtime_status()
    summary = payload['workspace_monitoring_summary']
    assert summary['configured_systems'] >= 1
    assert summary['reporting_systems'] >= 1
    assert summary['runtime_status'] == 'idle'
    assert summary['evidence_source'] == 'simulator'
    assert summary['telemetry_kind'] == 'coverage'
    assert summary['last_telemetry_at'] is not None
    assert summary['confidence_status'] == 'unavailable'


def test_workspace_configuration_truth_asset_only_is_not_configured() -> None:
    configured, reason = monitoring_runner._workspace_configuration_truth(
        valid_protected_asset_count=1,
        linked_monitored_system_count=0,
        persisted_enabled_config_count=0,
        valid_target_system_link_count=0,
    )
    assert configured is False
    assert reason == 'no_linked_monitored_systems'


def test_workspace_configuration_truth_monitored_system_only_is_not_configured() -> None:
    configured, reason = monitoring_runner._workspace_configuration_truth(
        valid_protected_asset_count=0,
        linked_monitored_system_count=1,
        persisted_enabled_config_count=0,
        valid_target_system_link_count=0,
    )
    assert configured is False
    assert reason == 'no_valid_protected_assets'


def test_workspace_configuration_truth_without_persisted_enabled_config_is_not_configured() -> None:
    configured, reason = monitoring_runner._workspace_configuration_truth(
        valid_protected_asset_count=1,
        linked_monitored_system_count=1,
        persisted_enabled_config_count=0,
        valid_target_system_link_count=1,
    )
    assert configured is False
    assert reason == 'no_persisted_enabled_monitoring_config'


def test_workspace_configuration_truth_invalid_target_system_linkage_is_not_configured() -> None:
    configured, reason = monitoring_runner._workspace_configuration_truth(
        valid_protected_asset_count=1,
        linked_monitored_system_count=1,
        persisted_enabled_config_count=1,
        valid_target_system_link_count=0,
    )
    assert configured is False
    assert reason == 'target_system_linkage_invalid'


def test_workspace_configuration_truth_with_all_required_links_is_configured() -> None:
    configured, reason = monitoring_runner._workspace_configuration_truth(
        valid_protected_asset_count=2,
        linked_monitored_system_count=2,
        persisted_enabled_config_count=2,
        valid_target_system_link_count=2,
    )
    assert configured is True
    assert reason is None


def test_runtime_status_unconfigured_uses_primary_configuration_reason_for_status_reason(monkeypatch):
    now = datetime.now(timezone.utc)

    class _UnconfiguredAndDegradedConn(_Conn):
        def execute(self, query, params=None):
            q = ' '.join(str(query).split())
            if 'FROM monitored_systems ms' in q and 'ORDER BY ms.created_at DESC' in q:
                return _Result(rows=[])
            if 'COUNT(*) AS target_count' in q and 'COUNT(DISTINCT t.asset_id) AS asset_count' in q:
                return _Result({'target_count': 0, 'asset_count': 0})
            if 'SELECT t.id' in q and 'FROM targets t' in q and 'JOIN assets a' in q:
                return _Result(rows=[])
            return super().execute(query, params)

    monkeypatch.setattr(
        monitoring_runner,
        'get_monitoring_health',
        lambda: {
            'last_heartbeat_at': now.isoformat(),
            'last_cycle_at': now.isoformat(),
            'degraded': True,
            'degraded_reason': 'stale_heartbeat',
            'last_error': None,
            'source_type': 'polling',
            'worker_running': True,
        },
    )
    monkeypatch.setattr(monitoring_runner, 'ensure_pilot_schema', lambda _c: None)
    monkeypatch.setattr(monitoring_runner, 'pg_connection', lambda: _fake_pg(_UnconfiguredAndDegradedConn(None)))

    payload = monitoring_runner.monitoring_runtime_status()
    summary = payload['workspace_monitoring_summary']

    assert summary['workspace_configured'] is False
    assert summary['configuration_reason'] == 'no_valid_protected_assets'
    assert summary['configuration_reason_codes'] == [
        'no_valid_protected_assets',
        'no_linked_monitored_systems',
        'no_persisted_enabled_monitoring_config',
        'target_system_linkage_invalid',
    ]
    assert summary['status_reason'] == 'workspace_configuration_invalid:no_valid_protected_assets'
    assert payload['status_reason'] == 'workspace_configuration_invalid:no_valid_protected_assets'


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


def test_runtime_status_workspace_scoped_path_preserves_coverage_telemetry_field(monkeypatch):
    now = datetime.now(timezone.utc)
    coverage_at = now - timedelta(seconds=20)
    coverage_iso = coverage_at.isoformat()

    class _CoverageScopedConn(_Conn):
        def execute(self, query, params=None):
            q = ' '.join(str(query).split())
            if 'FROM monitored_systems ms' in q and 'ORDER BY ms.created_at DESC' in q:
                return _Result(
                    rows=[
                        {
                            'id': 'sys-coverage',
                            'workspace_id': 'ws-1',
                            'asset_id': 'asset-1',
                            'target_id': 'target-1',
                            'chain': 'ethereum-mainnet',
                            'is_enabled': True,
                            'runtime_status': 'healthy',
                            'status': 'ready',
                            'last_heartbeat': now.isoformat(),
                            'last_event_at': None,
                            'last_coverage_telemetry_at': coverage_iso,
                            'monitoring_interval_seconds': 30,
                            'created_at': now.isoformat(),
                        }
                    ]
                )
            if 'FROM alerts' in q or 'FROM incidents' in q:
                return _Result({'c': 0})
            if 'COUNT(*) AS target_count' in q and 'COUNT(DISTINCT t.asset_id) AS asset_count' in q:
                return _Result({'target_count': 1, 'asset_count': 1})
            if 'SELECT t.id' in q and 'FROM targets t' in q and 'JOIN assets a' in q:
                return _Result(rows=[{'id': 'target-1', 'asset_id': 'asset-1'}])
            if 'FROM analysis_runs' in q:
                return _Result(
                    {
                        'created_at': now,
                        'response_payload': {'metadata': {'recent_real_event_count': 0, 'detection_outcome': 'NO_CONFIRMED_ANOMALY_FROM_REAL_EVIDENCE'}},
                    }
                )
            return _Result({})

    conn = _CoverageScopedConn(None)
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
    monkeypatch.setattr(monitoring_runner, 'ensure_pilot_schema', lambda _c: None)
    monkeypatch.setattr(monitoring_runner, 'pg_connection', lambda: _fake_pg(conn))
    monkeypatch.setattr(
        monitoring_runner,
        'resolve_workspace_context_for_request',
        lambda _c, _r: ({'id': 'user-1'}, {'workspace_id': 'ws-1', 'workspace': {'id': 'ws-1'}}, True),
    )
    monkeypatch.setattr(
        monitoring_runner,
        'list_workspace_monitored_system_rows',
        lambda _c, _w: conn.execute('SELECT ... FROM monitored_systems ms ORDER BY ms.created_at DESC').fetchall(),
    )

    payload = monitoring_runner.monitoring_runtime_status(SimpleNamespace(headers={'x-workspace-id': 'ws-1'}))
    summary = payload['workspace_monitoring_summary']
    assert summary['reporting_systems'] > 0
    assert summary['runtime_status'] == 'healthy'
    assert summary['monitoring_mode'] == 'live'
    assert summary['telemetry_kind'] == 'coverage'
    assert summary['evidence_source'] == 'live'


def test_contradiction_guard_offline_runtime_clears_current_telemetry(monkeypatch):
    now = datetime.now(timezone.utc)

    class _OfflineTelemetryConn(_Conn):
        def execute(self, query, params=None):
            q = ' '.join(str(query).split())
            if 'FROM monitored_systems ms' in q and 'ORDER BY ms.created_at DESC' in q:
                return _Result(rows=[])
            if 'COUNT(*) AS target_count' in q and 'COUNT(DISTINCT t.asset_id) AS asset_count' in q:
                return _Result({'target_count': 0, 'asset_count': 0})
            if 'SELECT t.id' in q and 'FROM targets t' in q and 'JOIN assets a' in q:
                return _Result(rows=[])
            if 'FROM evidence' in q:
                return _Result({'observed_at': now, 'block_number': 12})
            if 'FROM analysis_runs' in q:
                return _Result({'created_at': now, 'response_payload': {'metadata': {'last_real_event_at': now.isoformat(), 'recent_real_event_count': 2, 'detection_outcome': 'NO_CONFIRMED_ANOMALY_FROM_REAL_EVIDENCE'}}})
            return super().execute(query, params)

    monkeypatch.setattr(
        monitoring_runner,
        'get_monitoring_health',
        lambda: {'last_heartbeat_at': now.isoformat(), 'last_cycle_at': now.isoformat(), 'degraded': False, 'last_error': None, 'source_type': 'polling', 'worker_running': True},
    )
    monkeypatch.setattr(monitoring_runner, 'ensure_pilot_schema', lambda _c: None)
    monkeypatch.setattr(monitoring_runner, 'pg_connection', lambda: _fake_pg(_OfflineTelemetryConn(now)))

    payload = monitoring_runner.monitoring_runtime_status()
    summary = payload['workspace_monitoring_summary']
    assert summary['runtime_status'] == 'offline'
    assert summary['last_telemetry_at'] is None
    assert summary['freshness_status'] == 'unavailable'
    assert 'offline_with_current_telemetry' not in summary['contradiction_flags']


def test_contradiction_guard_never_marks_healthy_without_reporting_systems(monkeypatch):
    now = datetime.now(timezone.utc)

    class _NoReportingConn(_Conn):
        def execute(self, query, params=None):
            q = ' '.join(str(query).split())
            if 'FROM monitored_systems ms' in q and 'ORDER BY ms.created_at DESC' in q:
                return _Result(rows=[{'id': 'sys-1', 'workspace_id': 'ws-1', 'asset_id': 'asset-1', 'target_id': 'target-1', 'is_enabled': True, 'runtime_status': 'idle', 'last_heartbeat': now.isoformat(), 'monitoring_interval_seconds': 30, 'created_at': now.isoformat(), 'last_event_at': None}])
            if 'COUNT(*) AS target_count' in q and 'COUNT(DISTINCT t.asset_id) AS asset_count' in q:
                return _Result({'target_count': 1, 'asset_count': 1})
            if 'SELECT t.id' in q and 'FROM targets t' in q and 'JOIN assets a' in q:
                return _Result(rows=[{'id': 'target-1'}])
            if 'FROM evidence' in q:
                return _Result({'observed_at': None, 'block_number': None})
            return super().execute(query, params)

    monkeypatch.setattr(
        monitoring_runner,
        'get_monitoring_health',
        lambda: {'last_heartbeat_at': now.isoformat(), 'last_cycle_at': now.isoformat(), 'degraded': False, 'last_error': None, 'source_type': 'polling', 'worker_running': True},
    )
    monkeypatch.setattr(monitoring_runner, 'ensure_pilot_schema', lambda _c: None)
    monkeypatch.setattr(monitoring_runner, 'pg_connection', lambda: _fake_pg(_NoReportingConn(None)))

    payload = monitoring_runner.monitoring_runtime_status()
    summary = payload['workspace_monitoring_summary']
    assert summary['coverage_state']['configured_systems'] > 0
    assert summary['coverage_state']['reporting_systems'] == 0
    assert summary['runtime_status'] != 'healthy'
    assert 'healthy_without_reporting_systems' not in summary['contradiction_flags']


def test_workspace_summary_stays_idle_until_first_reporting_telemetry(monkeypatch):
    now = datetime.now(timezone.utc)

    class _ConfiguredNoTelemetryConn(_Conn):
        def execute(self, query, params=None):
            q = ' '.join(str(query).split())
            if 'FROM monitored_systems ms' in q and 'ORDER BY ms.created_at DESC' in q:
                return _Result(rows=[{'id': 'sys-1', 'workspace_id': 'ws-1', 'asset_id': 'asset-1', 'target_id': 'target-1', 'is_enabled': True, 'runtime_status': 'degraded', 'last_heartbeat': now.isoformat(), 'monitoring_interval_seconds': 30, 'created_at': now.isoformat(), 'last_event_at': None}])
            if 'COUNT(*) AS target_count' in q and 'COUNT(DISTINCT t.asset_id) AS asset_count' in q:
                return _Result({'target_count': 1, 'asset_count': 1})
            if 'SELECT t.id' in q and 'FROM targets t' in q and 'JOIN assets a' in q:
                return _Result(rows=[{'id': 'target-1'}])
            if 'FROM evidence' in q:
                return _Result({'observed_at': None, 'block_number': None})
            return super().execute(query, params)

    monkeypatch.setattr(
        monitoring_runner,
        'get_monitoring_health',
        lambda: {'last_heartbeat_at': now.isoformat(), 'last_cycle_at': now.isoformat(), 'degraded': True, 'degraded_reason': 'provider_backpressure', 'last_error': None, 'source_type': 'polling', 'worker_running': True},
    )
    monkeypatch.setattr(monitoring_runner, 'ensure_pilot_schema', lambda _c: None)
    monkeypatch.setattr(monitoring_runner, 'pg_connection', lambda: _fake_pg(_ConfiguredNoTelemetryConn(None)))

    payload = monitoring_runner.monitoring_runtime_status()
    summary = payload['workspace_monitoring_summary']
    assert summary['coverage_state']['configured_systems'] == 1
    assert summary['coverage_state']['reporting_systems'] == 0
    assert summary['configured_systems'] == 1
    assert summary['reporting_systems'] == 0
    assert summary['runtime_status'] == 'idle'
    assert summary['last_telemetry_at'] is None
    assert payload['telemetry_available'] is False


def test_contradiction_guard_flags_heartbeat_without_telemetry(monkeypatch):
    now = datetime.now(timezone.utc)

    class _HeartbeatOnlyConn(_Conn):
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
                            'is_enabled': True,
                            'runtime_status': 'idle',
                            'last_heartbeat': now.isoformat(),
                            'monitoring_interval_seconds': 30,
                            'created_at': now.isoformat(),
                            'last_event_at': None,
                        }
                    ]
                )
            if 'COUNT(*) AS target_count' in q and 'COUNT(DISTINCT t.asset_id) AS asset_count' in q:
                return _Result({'target_count': 1, 'asset_count': 1})
            if 'SELECT t.id' in q and 'FROM targets t' in q and 'JOIN assets a' in q:
                return _Result(rows=[{'id': 'target-1'}])
            if 'FROM analysis_runs' in q:
                return _Result(
                    {
                        'created_at': now,
                        'response_payload': {
                            'metadata': {
                                'recent_real_event_count': 1,
                                'last_real_event_at': now.isoformat(),
                                'detection_outcome': 'NO_CONFIRMED_ANOMALY_FROM_REAL_EVIDENCE',
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
    monkeypatch.setattr(monitoring_runner, 'pg_connection', lambda: _fake_pg(_HeartbeatOnlyConn(None)))

    payload = monitoring_runner.monitoring_runtime_status()
    summary = payload['workspace_monitoring_summary']
    assert summary['last_heartbeat_at'] is not None
    assert summary['last_telemetry_at'] is None
    assert summary['runtime_status'] in {'idle', 'degraded'}
    assert summary['evidence_source'] != 'live'


def test_contradiction_guard_workspace_not_configured_with_monitored_systems_flagged(monkeypatch):
    now = datetime.now(timezone.utc)

    class _UnconfiguredCoverageConn(_Conn):
        def execute(self, query, params=None):
            q = ' '.join(str(query).split())
            if 'FROM monitored_systems ms' in q and 'ORDER BY ms.created_at DESC' in q:
                return _Result(rows=[{'id': 'sys-1', 'workspace_id': 'ws-1', 'asset_id': None, 'target_id': 'target-1', 'is_enabled': True, 'runtime_status': 'idle', 'last_heartbeat': now.isoformat(), 'monitoring_interval_seconds': 30, 'created_at': now.isoformat(), 'last_event_at': None}])
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
    monkeypatch.setattr(monitoring_runner, 'pg_connection', lambda: _fake_pg(_UnconfiguredCoverageConn(None)))

    payload = monitoring_runner.monitoring_runtime_status()
    summary = payload['workspace_monitoring_summary']
    assert summary['workspace_configured'] is False
    assert summary['coverage_state']['configured_systems'] > 0
    assert summary['configured_systems'] > 0
    assert summary['configuration_reason'] == 'no_valid_protected_assets'
    assert 'workspace_unconfigured_with_coverage' in summary['contradiction_flags']


def test_runtime_status_live_with_fresh_coverage_telemetry_without_target_events(monkeypatch):
    now = datetime.now(timezone.utc)

    class _CoverageOnlyConn(_Conn):
        def execute(self, query, params=None):
            q = ' '.join(str(query).split())
            if 'FROM monitored_systems ms' in q and 'ORDER BY ms.created_at DESC' in q:
                return _Result(rows=[{
                    'id': 'sys-1',
                    'workspace_id': 'ws-1',
                    'asset_id': 'asset-1',
                    'target_id': 'target-1',
                    'is_enabled': True,
                    'runtime_status': 'healthy',
                    'last_heartbeat': now.isoformat(),
                    'last_coverage_telemetry_at': (now - timedelta(seconds=20)).isoformat(),
                    'last_event_at': None,
                    'monitoring_interval_seconds': 30,
                    'created_at': now.isoformat(),
                }])
            if 'COUNT(*) AS target_count' in q and 'COUNT(DISTINCT t.asset_id) AS asset_count' in q:
                return _Result({'target_count': 1, 'asset_count': 1})
            if 'SELECT t.id' in q and 'FROM targets t' in q and 'JOIN assets a' in q:
                return _Result(rows=[{'id': 'target-1', 'asset_id': 'asset-1'}])
            if 'FROM analysis_runs' in q:
                return _Result({'created_at': now, 'response_payload': {'metadata': {'recent_real_event_count': 0, 'detection_outcome': 'NO_CONFIRMED_ANOMALY_FROM_REAL_EVIDENCE'}}})
            return super().execute(query, params)

    monkeypatch.setattr(monitoring_runner, 'get_monitoring_health', lambda: {'last_heartbeat_at': now.isoformat(), 'last_cycle_at': now.isoformat(), 'degraded': False, 'last_error': None, 'source_type': 'polling', 'worker_running': True})
    monkeypatch.setattr(monitoring_runner, 'ensure_pilot_schema', lambda _c: None)
    monkeypatch.setattr(monitoring_runner, 'pg_connection', lambda: _fake_pg(_CoverageOnlyConn(None)))

    payload = monitoring_runner.monitoring_runtime_status()
    summary = payload['workspace_monitoring_summary']
    assert summary['runtime_status'] == 'healthy'
    assert summary['freshness_status'] == 'fresh'
    assert summary['confidence_status'] == 'high'
    assert payload['confidence_status'] == summary['confidence_status']
    assert summary['telemetry_kind'] == 'coverage'
    assert summary['evidence_source'] == 'live'
    assert summary['last_detection_at'] is not None


def test_runtime_status_live_uses_fresh_coverage_receipts_fallback(monkeypatch):
    now = datetime.now(timezone.utc)

    class _CoverageReceiptsFallbackConn(_Conn):
        def execute(self, query, params=None):
            q = ' '.join(str(query).split())
            if 'FROM monitoring_event_receipts e' in q and "e.evidence_source = 'live'" in q and "e.telemetry_kind = 'coverage'" in q:
                return _Result(
                    rows=[
                        {
                            'processed_at': (now - timedelta(seconds=15)).isoformat(),
                            'target_id': 'target-1',
                            'monitored_system_id': 'sys-1',
                        }
                    ]
                )
            if 'FROM monitored_systems ms' in q and 'ORDER BY ms.created_at DESC' in q:
                return _Result(rows=[{
                    'id': 'sys-1',
                    'workspace_id': 'ws-1',
                    'asset_id': 'asset-1',
                    'target_id': 'target-1',
                    'is_enabled': True,
                    'runtime_status': 'healthy',
                    'last_heartbeat': now.isoformat(),
                    'last_coverage_telemetry_at': None,
                    'last_event_at': None,
                    'monitoring_interval_seconds': 30,
                    'created_at': now.isoformat(),
                }])
            if 'COUNT(*) AS target_count' in q and 'COUNT(DISTINCT t.asset_id) AS asset_count' in q:
                return _Result({'target_count': 1, 'asset_count': 1})
            if 'SELECT t.id' in q and 'FROM targets t' in q and 'JOIN assets a' in q:
                return _Result(rows=[{'id': 'target-1', 'asset_id': 'asset-1'}])
            if 'FROM analysis_runs' in q:
                return _Result({'created_at': now, 'response_payload': {'metadata': {'recent_real_event_count': 0, 'detection_outcome': 'NO_CONFIRMED_ANOMALY_FROM_REAL_EVIDENCE'}}})
            return super().execute(query, params)

    monkeypatch.setattr(monitoring_runner, 'get_monitoring_health', lambda: {'last_heartbeat_at': now.isoformat(), 'last_cycle_at': now.isoformat(), 'degraded': False, 'last_error': None, 'source_type': 'polling', 'worker_running': True})
    monkeypatch.setattr(monitoring_runner, 'ensure_pilot_schema', lambda _c: None)
    monkeypatch.setattr(monitoring_runner, 'pg_connection', lambda: _fake_pg(_CoverageReceiptsFallbackConn(None)))

    payload = monitoring_runner.monitoring_runtime_status()
    summary = payload['workspace_monitoring_summary']
    assert summary['runtime_status'] == 'healthy'
    assert summary['evidence_source'] == 'live'
    assert summary['reporting_systems'] > 0
    assert summary['telemetry_kind'] == 'coverage'


def test_runtime_status_treats_null_enabled_system_as_enabled_for_live_coverage(monkeypatch):
    now = datetime.now(timezone.utc)

    class _NullEnabledCoverageConn(_Conn):
        def execute(self, query, params=None):
            q = ' '.join(str(query).split())
            if 'FROM monitoring_event_receipts e' in q and "e.evidence_source = 'live'" in q and "e.telemetry_kind = 'coverage'" in q:
                if 'COALESCE(ms.is_enabled, TRUE) = TRUE' not in q:
                    return _Result(rows=[])
                return _Result(
                    rows=[
                        {
                            'processed_at': (now - timedelta(seconds=12)).isoformat(),
                            'target_id': 'target-1',
                            'monitored_system_id': 'sys-null-enabled',
                        }
                    ]
                )
            if 'FROM monitored_systems ms' in q and 'ORDER BY ms.created_at DESC' in q:
                if 'COALESCE(ms.is_enabled, TRUE) AS is_enabled' not in q:
                    return _Result(rows=[])
                return _Result(rows=[{
                    'id': 'sys-null-enabled',
                    'workspace_id': 'ws-1',
                    'asset_id': 'asset-1',
                    'target_id': 'target-1',
                    'is_enabled': None,
                    'runtime_status': 'healthy',
                    'last_heartbeat': now.isoformat(),
                    'last_coverage_telemetry_at': None,
                    'last_event_at': None,
                    'monitoring_interval_seconds': 30,
                    'created_at': now.isoformat(),
                }])
            if 'COUNT(*) AS target_count' in q and 'COUNT(DISTINCT t.asset_id) AS asset_count' in q:
                return _Result({'target_count': 1, 'asset_count': 1})
            if 'SELECT t.id' in q and 'FROM targets t' in q and 'JOIN assets a' in q:
                return _Result(rows=[{'id': 'target-1', 'asset_id': 'asset-1'}])
            if 'FROM analysis_runs' in q:
                return _Result({'created_at': now, 'response_payload': {'metadata': {'recent_real_event_count': 0, 'detection_outcome': 'NO_CONFIRMED_ANOMALY_FROM_REAL_EVIDENCE'}}})
            return super().execute(query, params)

    monkeypatch.setattr(monitoring_runner, 'get_monitoring_health', lambda: {'last_heartbeat_at': now.isoformat(), 'last_cycle_at': now.isoformat(), 'degraded': False, 'last_error': None, 'source_type': 'polling', 'worker_running': True})
    monkeypatch.setattr(monitoring_runner, 'ensure_pilot_schema', lambda _c: None)
    monkeypatch.setattr(monitoring_runner, 'pg_connection', lambda: _fake_pg(_NullEnabledCoverageConn(None)))

    payload = monitoring_runner.monitoring_runtime_status()
    summary = payload['workspace_monitoring_summary']
    assert payload['systems_with_recent_heartbeat'] == 1
    assert summary['reporting_systems'] == 1
    assert summary['runtime_status'] == 'healthy'
    assert summary['telemetry_kind'] == 'coverage'


def test_runtime_status_live_heartbeat_and_poll_without_coverage_keeps_confidence_unavailable(monkeypatch):
    now = datetime.now(timezone.utc)

    class _NoCoverageConn(_Conn):
        def execute(self, query, params=None):
            q = ' '.join(str(query).split())
            if 'FROM monitored_systems ms' in q and 'ORDER BY ms.created_at DESC' in q:
                return _Result(rows=[{
                    'id': 'sys-1',
                    'workspace_id': 'ws-1',
                    'asset_id': 'asset-1',
                    'target_id': 'target-1',
                    'is_enabled': True,
                    'runtime_status': 'idle',
                    'last_heartbeat': now.isoformat(),
                    'last_coverage_telemetry_at': None,
                    'last_event_at': None,
                    'monitoring_interval_seconds': 30,
                    'created_at': now.isoformat(),
                }])
            if 'COUNT(*) AS target_count' in q and 'COUNT(DISTINCT t.asset_id) AS asset_count' in q:
                return _Result({'target_count': 1, 'asset_count': 1})
            if 'SELECT t.id' in q and 'FROM targets t' in q and 'JOIN assets a' in q:
                return _Result(rows=[{'id': 'target-1', 'asset_id': 'asset-1'}])
            return super().execute(query, params)

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
    monkeypatch.setattr(monitoring_runner, 'ensure_pilot_schema', lambda _c: None)
    monkeypatch.setattr(monitoring_runner, 'pg_connection', lambda: _fake_pg(_NoCoverageConn(None)))

    payload = monitoring_runner.monitoring_runtime_status()
    summary = payload['workspace_monitoring_summary']
    assert summary['freshness_status'] == 'unavailable'
    assert summary['runtime_status'] == 'idle'
    assert summary['confidence_status'] == 'unavailable'
    assert payload['confidence_status'] == summary['confidence_status']
    assert summary['reporting_systems'] == 0
    assert summary['status_reason'] == 'no_fresh_live_coverage_telemetry'


def test_runtime_status_demo_coverage_does_not_count_as_live(monkeypatch):
    now = datetime.now(timezone.utc)

    class _DemoCoverageConn(_Conn):
        def execute(self, query, params=None):
            q = ' '.join(str(query).split())
            if 'FROM monitored_systems ms' in q and 'ORDER BY ms.created_at DESC' in q:
                return _Result(rows=[{
                    'id': 'sys-1', 'workspace_id': 'ws-1', 'asset_id': 'asset-1', 'target_id': 'target-1',
                    'is_enabled': True, 'runtime_status': 'healthy', 'last_heartbeat': now.isoformat(),
                    'last_coverage_telemetry_at': (now - timedelta(seconds=15)).isoformat(),
                    'last_event_at': None, 'monitoring_interval_seconds': 30, 'created_at': now.isoformat(),
                }])
            if 'COUNT(*) AS target_count' in q and 'COUNT(DISTINCT t.asset_id) AS asset_count' in q:
                return _Result({'target_count': 1, 'asset_count': 1})
            if 'SELECT t.id' in q and 'FROM targets t' in q and 'JOIN assets a' in q:
                return _Result(rows=[{'id': 'target-1', 'asset_id': 'asset-1'}])
            return super().execute(query, params)

    monkeypatch.setattr(monitoring_runner, 'get_monitoring_health', lambda: {'last_heartbeat_at': now.isoformat(), 'last_cycle_at': now.isoformat(), 'degraded': False, 'last_error': None, 'source_type': 'polling', 'ingestion_mode': 'demo', 'worker_running': True})
    monkeypatch.setattr(monitoring_runner, 'ensure_pilot_schema', lambda _c: None)
    monkeypatch.setattr(monitoring_runner, 'pg_connection', lambda: _fake_pg(_DemoCoverageConn(None)))

    payload = monitoring_runner.monitoring_runtime_status()
    summary = payload['workspace_monitoring_summary']
    assert summary['evidence_source'] == 'simulator'
    assert summary['monitoring_mode'] == 'simulator'
    assert summary['runtime_status'] == 'idle'
    assert summary['confidence_status'] == 'unavailable'


def test_runtime_status_replay_or_demo_receipts_do_not_count_as_live_coverage(monkeypatch):
    now = datetime.now(timezone.utc)

    class _ReplayReceiptConn(_Conn):
        def execute(self, query, params=None):
            q = ' '.join(str(query).split())
            if 'FROM monitoring_event_receipts e' in q and "e.evidence_source = 'live'" in q and "e.telemetry_kind = 'coverage'" in q:
                synthetic_sources = (params or [()])[0]
                if 'NOT IN %s' in q and 'demo' in synthetic_sources and 'replay' in synthetic_sources:
                    return _Result(rows=[])
                return _Result(
                    rows=[{
                        'processed_at': (now - timedelta(seconds=8)).isoformat(),
                        'target_id': 'target-1',
                        'monitored_system_id': 'sys-1',
                    }]
                )
            if 'FROM monitored_systems ms' in q and 'ORDER BY ms.created_at DESC' in q:
                return _Result(rows=[{
                    'id': 'sys-1', 'workspace_id': 'ws-1', 'asset_id': 'asset-1', 'target_id': 'target-1',
                    'is_enabled': True, 'runtime_status': 'healthy', 'last_heartbeat': now.isoformat(),
                    'last_coverage_telemetry_at': None, 'last_event_at': None, 'monitoring_interval_seconds': 30, 'created_at': now.isoformat(),
                }])
            if 'COUNT(*) AS target_count' in q and 'COUNT(DISTINCT t.asset_id) AS asset_count' in q:
                return _Result({'target_count': 1, 'asset_count': 1})
            if 'SELECT t.id' in q and 'FROM targets t' in q and 'JOIN assets a' in q:
                return _Result(rows=[{'id': 'target-1', 'asset_id': 'asset-1'}])
            return super().execute(query, params)

    monkeypatch.setattr(monitoring_runner, 'get_monitoring_health', lambda: {'last_heartbeat_at': now.isoformat(), 'last_cycle_at': now.isoformat(), 'degraded': False, 'last_error': None, 'source_type': 'polling', 'worker_running': True})
    monkeypatch.setattr(monitoring_runner, 'ensure_pilot_schema', lambda _c: None)
    monkeypatch.setattr(monitoring_runner, 'pg_connection', lambda: _fake_pg(_ReplayReceiptConn(None)))

    payload = monitoring_runner.monitoring_runtime_status()
    summary = payload['workspace_monitoring_summary']
    assert summary['reporting_systems'] == 0
    assert summary['runtime_status'] == 'idle'
    assert summary['evidence_source'] != 'live'


def test_runtime_status_live_coverage_with_historical_detections_stays_live(monkeypatch):
    now = datetime.now(timezone.utc)
    old_detection_at = now - timedelta(hours=3)

    class _CoverageWithHistoryConn(_Conn):
        def execute(self, query, params=None):
            q = ' '.join(str(query).split())
            if 'FROM monitored_systems ms' in q and 'ORDER BY ms.created_at DESC' in q:
                return _Result(rows=[{
                    'id': 'sys-1',
                    'workspace_id': 'ws-1',
                    'asset_id': 'asset-1',
                    'target_id': 'target-1',
                    'is_enabled': True,
                    'runtime_status': 'healthy',
                    'last_heartbeat': now.isoformat(),
                    'last_coverage_telemetry_at': (now - timedelta(seconds=10)).isoformat(),
                    'last_event_at': None,
                    'monitoring_interval_seconds': 30,
                    'created_at': now.isoformat(),
                }])
            if 'COUNT(*) AS target_count' in q and 'COUNT(DISTINCT t.asset_id) AS asset_count' in q:
                return _Result({'target_count': 1, 'asset_count': 1})
            if 'SELECT t.id' in q and 'FROM targets t' in q and 'JOIN assets a' in q:
                return _Result(rows=[{'id': 'target-1', 'asset_id': 'asset-1'}])
            if 'FROM analysis_runs' in q:
                return _Result({'created_at': old_detection_at, 'response_payload': {'metadata': {'recent_real_event_count': 0, 'detection_outcome': 'NO_CONFIRMED_ANOMALY_FROM_REAL_EVIDENCE'}}})
            return super().execute(query, params)

    monkeypatch.setattr(monitoring_runner, 'get_monitoring_health', lambda: {'last_heartbeat_at': now.isoformat(), 'last_cycle_at': now.isoformat(), 'degraded': False, 'last_error': None, 'source_type': 'polling', 'worker_running': True})
    monkeypatch.setattr(monitoring_runner, 'ensure_pilot_schema', lambda _c: None)
    monkeypatch.setattr(monitoring_runner, 'pg_connection', lambda: _fake_pg(_CoverageWithHistoryConn(None)))

    payload = monitoring_runner.monitoring_runtime_status()
    summary = payload['workspace_monitoring_summary']
    assert summary['runtime_status'] == 'healthy'
    assert summary['reporting_systems'] > 0
    assert summary['telemetry_kind'] == 'coverage'
    assert summary['confidence_status'] == 'high'


def test_derive_system_runtime_state_marks_unsupported_target_type_explicitly():
    runtime_status, freshness_status, confidence_status, coverage_reason = monitoring_runner._derive_system_runtime_state(
        {
            'target_type': 'oracle_feed',
            'provider_status': 'no_evidence',
            'source_status': 'no_evidence',
            'events_ingested': 0,
            'recent_real_event_count': 0,
            'degraded_reason': None,
        },
        is_enabled=True,
    )
    assert runtime_status == 'degraded'
    assert freshness_status == 'stale'
    assert confidence_status == 'low'
    assert coverage_reason == 'unsupported_target_type_for_live_coverage'


def test_runtime_status_summary_prefers_unsupported_target_type_reason(monkeypatch):
    now = datetime.now(timezone.utc)

    class _UnsupportedTypeConn(_Conn):
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
                            'target_type': 'oracle_feed',
                            'is_enabled': True,
                            'runtime_status': 'degraded',
                            'status': 'active',
                            'last_heartbeat': now.isoformat(),
                            'last_event_at': None,
                            'last_coverage_telemetry_at': None,
                            'coverage_reason': 'unsupported_target_type_for_live_coverage',
                            'monitoring_interval_seconds': 30,
                            'created_at': now.isoformat(),
                        }
                    ]
                )
            if 'COUNT(*) AS target_count' in q and 'COUNT(DISTINCT t.asset_id) AS asset_count' in q:
                return _Result({'target_count': 1, 'asset_count': 1})
            if 'SELECT t.id' in q and 'FROM targets t' in q and 'JOIN assets a' in q:
                return _Result(rows=[{'id': 'target-1'}])
            if 'FROM evidence' in q:
                return _Result({'observed_at': None, 'block_number': None})
            return super().execute(query, params)

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
    monkeypatch.setattr(monitoring_runner, 'ensure_pilot_schema', lambda _c: None)
    monkeypatch.setattr(monitoring_runner, 'pg_connection', lambda: _fake_pg(_UnsupportedTypeConn(None)))

    payload = monitoring_runner.monitoring_runtime_status()
    summary = payload['workspace_monitoring_summary']
    assert summary['status_reason'] == 'no_fresh_live_coverage_telemetry'
    assert payload['coverage_reason'] == 'no_evidence'


def test_runtime_status_workspace_configured_when_target_join_type_missing(monkeypatch):
    now = datetime.now(timezone.utc)

    class _MissingTargetTypeJoinConn(_Conn):
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
                            'target_type': None,
                            'is_enabled': True,
                            'runtime_status': 'healthy',
                            'last_heartbeat': now.isoformat(),
                            'last_coverage_telemetry_at': (now - timedelta(seconds=15)).isoformat(),
                            'monitoring_interval_seconds': 30,
                            'created_at': now.isoformat(),
                        }
                    ]
                )
            if 'COUNT(*) AS target_count' in q and 'COUNT(DISTINCT t.asset_id) AS asset_count' in q:
                return _Result({'target_count': 1, 'asset_count': 1})
            if 'SELECT t.id' in q and 'FROM targets t' in q and 'JOIN assets a' in q:
                return _Result(rows=[{'id': 'target-1', 'asset_id': 'asset-1'}])
            return super().execute(query, params)

    monkeypatch.setattr(
        monitoring_runner,
        'get_monitoring_health',
        lambda: {'last_heartbeat_at': now.isoformat(), 'last_cycle_at': now.isoformat(), 'degraded': False, 'last_error': None, 'source_type': 'polling', 'worker_running': True},
    )
    monkeypatch.setattr(monitoring_runner, 'ensure_pilot_schema', lambda _c: None)
    monkeypatch.setattr(monitoring_runner, 'pg_connection', lambda: _fake_pg(_MissingTargetTypeJoinConn(None)))

    payload = monitoring_runner.monitoring_runtime_status()
    summary = payload['workspace_monitoring_summary']
    assert summary['workspace_configured'] is True
    assert summary['configuration_reason'] is None
    assert summary['runtime_status'] == 'healthy'


def test_runtime_status_returns_explicit_configuration_and_evidence_fields(monkeypatch):
    now = datetime.now(timezone.utc)

    class _ConfiguredConn(_Conn):
        def execute(self, query, params=None):
            q = ' '.join(str(query).split())
            if 'FROM monitored_systems ms' in q and 'ORDER BY ms.created_at DESC' in q:
                return _Result(rows=[{
                    'id': 'sys-1',
                    'workspace_id': 'ws-1',
                    'asset_id': 'asset-1',
                    'target_id': 'target-1',
                    'is_enabled': True,
                    'runtime_status': 'healthy',
                    'last_heartbeat': now.isoformat(),
                    'last_coverage_telemetry_at': (now - timedelta(seconds=20)).isoformat(),
                    'monitoring_interval_seconds': 30,
                    'created_at': now.isoformat(),
                }])
            if 'COUNT(*) AS target_count' in q and 'COUNT(DISTINCT t.asset_id) AS asset_count' in q:
                return _Result({'target_count': 1, 'asset_count': 1})
            if 'SELECT t.id' in q and 'FROM targets t' in q and 'JOIN assets a' in q:
                return _Result(rows=[{'id': 'target-1', 'asset_id': 'asset-1'}])
            return super().execute(query, params)

    monkeypatch.setattr(monitoring_runner, 'get_monitoring_health', lambda: {'last_heartbeat_at': now.isoformat(), 'last_cycle_at': now.isoformat(), 'degraded': False, 'last_error': None, 'source_type': 'polling', 'worker_running': True})
    monkeypatch.setattr(monitoring_runner, 'ensure_pilot_schema', lambda _c: None)
    monkeypatch.setattr(monitoring_runner, 'pg_connection', lambda: _fake_pg(_ConfiguredConn(None)))

    payload = monitoring_runner.monitoring_runtime_status()
    summary = payload['workspace_monitoring_summary']
    assert summary['valid_protected_assets'] == 1
    assert summary['linked_monitored_systems'] == 1
    assert summary['enabled_configs'] == 1
    assert summary['valid_link_count'] == 1
    assert summary['source_of_evidence'] in {'live', 'replay_or_none', 'simulator'}
    assert payload['status_reason'] == summary['status_reason']


def test_runtime_status_not_offline_when_configured_but_no_fresh_coverage(monkeypatch):
    now = datetime.now(timezone.utc)

    class _StaleCoverageConn(_Conn):
        def execute(self, query, params=None):
            q = ' '.join(str(query).split())
            if 'FROM monitored_systems ms' in q and 'ORDER BY ms.created_at DESC' in q:
                return _Result(rows=[{
                    'id': 'sys-1',
                    'workspace_id': 'ws-1',
                    'asset_id': 'asset-1',
                    'target_id': 'target-1',
                    'is_enabled': True,
                    'runtime_status': 'idle',
                    'last_heartbeat': now.isoformat(),
                    'last_coverage_telemetry_at': None,
                    'monitoring_interval_seconds': 30,
                    'created_at': now.isoformat(),
                }])
            if 'COUNT(*) AS target_count' in q and 'COUNT(DISTINCT t.asset_id) AS asset_count' in q:
                return _Result({'target_count': 1, 'asset_count': 1})
            if 'SELECT t.id' in q and 'FROM targets t' in q and 'JOIN assets a' in q:
                return _Result(rows=[{'id': 'target-1', 'asset_id': 'asset-1'}])
            return super().execute(query, params)

    monkeypatch.setattr(monitoring_runner, 'get_monitoring_health', lambda: {'last_heartbeat_at': now.isoformat(), 'last_cycle_at': now.isoformat(), 'degraded': False, 'last_error': None, 'source_type': 'polling', 'worker_running': True})
    monkeypatch.setattr(monitoring_runner, 'ensure_pilot_schema', lambda _c: None)
    monkeypatch.setattr(monitoring_runner, 'pg_connection', lambda: _fake_pg(_StaleCoverageConn(None)))

    payload = monitoring_runner.monitoring_runtime_status()
    summary = payload['workspace_monitoring_summary']
    assert summary['workspace_configured'] is True
    assert summary['runtime_status'] in {'idle', 'degraded'}
    assert summary['runtime_status'] != 'offline'
    assert summary['status_reason'] == 'no_fresh_live_coverage_telemetry'


def test_runtime_status_includes_workspace_identity_fields(monkeypatch):
    now = datetime.now(timezone.utc)

    class _ConfiguredConn(_Conn):
        def execute(self, query, params=None):
            q = ' '.join(str(query).split())
            if 'FROM monitored_systems ms' in q and 'ORDER BY ms.created_at DESC' in q:
                return _Result(rows=[{
                    'id': 'sys-1',
                    'workspace_id': 'ws-prod',
                    'asset_id': 'asset-1',
                    'target_id': 'target-1',
                    'is_enabled': True,
                    'runtime_status': 'healthy',
                    'last_heartbeat': now.isoformat(),
                    'last_coverage_telemetry_at': now.isoformat(),
                    'monitoring_interval_seconds': 30,
                    'created_at': now.isoformat(),
                }])
            if 'COUNT(*) AS target_count' in q and 'COUNT(DISTINCT t.asset_id) AS asset_count' in q:
                return _Result({'target_count': 1, 'asset_count': 1})
            if 'SELECT t.id' in q and 'FROM targets t' in q and 'JOIN assets a' in q:
                return _Result(rows=[{'id': 'target-1', 'asset_id': 'asset-1'}])
            return super().execute(query, params)

    request = SimpleNamespace(headers={'x-workspace-id': 'ws-prod'}, state=SimpleNamespace())
    monkeypatch.setattr(monitoring_runner, 'get_monitoring_health', lambda: {'last_heartbeat_at': now.isoformat(), 'last_cycle_at': now.isoformat(), 'degraded': False, 'last_error': None, 'source_type': 'polling', 'worker_running': True})
    monkeypatch.setattr(monitoring_runner, 'ensure_pilot_schema', lambda _c: None)
    monkeypatch.setattr(
        monitoring_runner,
        'resolve_workspace_context_for_request',
        lambda _connection, _request: (
            {'id': 'user-1'},
            {'workspace_id': 'ws-prod', 'workspace': {'id': 'ws-prod', 'slug': 'prod-ops'}},
            True,
        ),
    )
    monkeypatch.setattr(monitoring_runner, 'pg_connection', lambda: _fake_pg(_ConfiguredConn(None)))

    payload = monitoring_runner.monitoring_runtime_status(request)
    assert payload['workspace_id'] == 'ws-prod'
    assert payload['workspace_slug'] == 'prod-ops'


def test_runtime_status_query_failure_keeps_workspace_identity_and_query_failure_reason_codes(monkeypatch):
    now = datetime.now(timezone.utc)

    class _SyntaxErrorConn(_Conn):
        def execute(self, query, params=None):
            q = ' '.join(str(query).split())
            if "COALESCE(LOWER(e.ingestion_source), '') = ANY(%s::text[])" in q:
                raise PsycopgSyntaxError('syntax error at or near "$1"')
            return super().execute(query, params)

    request = SimpleNamespace(headers={'x-workspace-id': 'ws-prod'}, state=SimpleNamespace())
    monkeypatch.setattr(
        monitoring_runner,
        'get_monitoring_health',
        lambda: {'last_heartbeat_at': now.isoformat(), 'last_cycle_at': now.isoformat(), 'degraded': False, 'last_error': None, 'source_type': 'polling', 'worker_running': True},
    )
    monkeypatch.setattr(monitoring_runner, 'ensure_pilot_schema', lambda _c: None)
    monkeypatch.setattr(
        monitoring_runner,
        'resolve_workspace_context_for_request',
        lambda _connection, _request: (
            {'id': 'user-1'},
            {'workspace_id': 'ws-prod', 'workspace': {'id': 'ws-prod', 'slug': 'prod-ops'}},
            True,
        ),
    )
    monkeypatch.setattr(monitoring_runner, 'pg_connection', lambda: _fake_pg(_SyntaxErrorConn(None)))

    payload = monitoring_runner.monitoring_runtime_status(request)
    assert payload['workspace_id'] == 'ws-prod'
    assert payload['workspace_slug'] == 'prod-ops'
    assert payload['configuration_reason'] == 'runtime_status_unavailable'
    assert payload['status_reason'] == 'runtime_status_degraded:database_error'
    assert payload['error']['code'] == 'runtime_status_db_error'
    assert payload['error']['type'] == 'SyntaxError'
    assert payload['field_reason_codes']['protected_assets'] == ['query_failure']
    assert payload['field_reason_codes']['configured_systems'] == ['query_failure']
    assert payload['field_reason_codes']['reporting_systems'] == ['query_failure']
    assert payload['field_reason_codes']['last_poll_at'] == ['query_failure']
    assert payload['field_reason_codes']['last_heartbeat_at'] == ['query_failure']
    assert payload['field_reason_codes']['last_telemetry_at'] == ['query_failure']
    assert payload['configuration_diagnostics']['reason_codes'] == ['runtime_status_unavailable']
    assert payload['workspace_monitoring_summary']['field_reason_codes']['protected_assets'] == ['query_failure']
    assert payload['workspace_monitoring_summary']['configuration_reason_codes'] == ['runtime_status_unavailable']


def test_runtime_status_workspace_unconfigured_path_uses_configuration_diagnostics(monkeypatch):
    now = datetime.now(timezone.utc)

    class _UnconfiguredConn(_Conn):
        def execute(self, query, params=None):
            q = ' '.join(str(query).split())
            if 'FROM monitored_systems ms' in q and 'ORDER BY ms.created_at DESC' in q:
                return _Result(rows=[])
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
    monkeypatch.setattr(monitoring_runner, 'pg_connection', lambda: _fake_pg(_UnconfiguredConn(None)))

    payload = monitoring_runner.monitoring_runtime_status()
    assert payload['workspace_configured'] is False
    assert payload['configuration_reason'] == 'no_valid_protected_assets'
    assert payload['configuration_diagnostics']['reason_codes'] == [
        'no_valid_protected_assets',
        'no_linked_monitored_systems',
        'no_persisted_enabled_monitoring_config',
        'target_system_linkage_invalid',
    ]
    assert payload['workspace_monitoring_summary']['field_reason_codes']['protected_assets'] == ['unconfigured_workspace']
    assert payload['workspace_monitoring_summary']['field_reason_codes']['configured_systems'] == ['unconfigured_workspace']
    assert payload['workspace_monitoring_summary']['field_reason_codes']['reporting_systems'] == ['unconfigured_workspace']


def test_runtime_debug_reports_configuration_reason_codes_in_production_when_workspace_unconfigured(monkeypatch):
    now = datetime.now(timezone.utc)

    class _NoBootstrapWorkspaceConn(_Conn):
        def execute(self, query, params=None):
            q = ' '.join(str(query).split())
            if 'FROM monitored_systems ms' in q and 'ORDER BY ms.created_at DESC' in q:
                return _Result(rows=[])
            if 'COUNT(*) AS target_count' in q and 'COUNT(DISTINCT t.asset_id) AS asset_count' in q:
                return _Result({'target_count': 0, 'asset_count': 0})
            if 'SELECT t.id' in q and 'FROM targets t' in q and 'JOIN assets a' in q:
                return _Result(rows=[])
            return super().execute(query, params)

    monkeypatch.setenv('APP_ENV', 'production')
    monkeypatch.setattr(
        monitoring_runner,
        'get_monitoring_health',
        lambda: {'last_heartbeat_at': now.isoformat(), 'last_cycle_at': now.isoformat(), 'degraded': False, 'last_error': None, 'source_type': 'polling', 'worker_running': True},
    )
    monkeypatch.setattr(monitoring_runner, 'ensure_pilot_schema', lambda _c: None)
    monkeypatch.setattr(monitoring_runner, 'pg_connection', lambda: _fake_pg(_NoBootstrapWorkspaceConn(None)))

    payload = monitoring_runner.monitoring_runtime_debug_payload()
    diagnostics = payload['configuration_diagnostics']
    assert payload['workspace_configured'] is False
    assert diagnostics['workspace_configured'] is False
    assert diagnostics['reason_codes'] == [
        'no_valid_protected_assets',
        'no_linked_monitored_systems',
        'no_persisted_enabled_monitoring_config',
        'target_system_linkage_invalid',
    ]


def test_runtime_debug_reports_workspace_configured_true_after_workspace_repaired(monkeypatch):
    now = datetime.now(timezone.utc)
    monkeypatch.setenv('APP_ENV', 'production')
    monkeypatch.setattr(
        monitoring_runner,
        'get_monitoring_health',
        lambda: {'last_heartbeat_at': now.isoformat(), 'last_cycle_at': now.isoformat(), 'degraded': False, 'last_error': None, 'source_type': 'polling', 'worker_running': True},
    )
    monkeypatch.setattr(monitoring_runner, 'ensure_pilot_schema', lambda _c: None)
    monkeypatch.setattr(monitoring_runner, 'pg_connection', lambda: _fake_pg(_Conn(now - timedelta(seconds=20))))

    payload = monitoring_runner.monitoring_runtime_debug_payload()
    diagnostics = payload['configuration_diagnostics']
    assert payload['workspace_configured'] is True
    assert diagnostics['workspace_configured'] is True
    assert diagnostics['reason_codes'] == []


def test_runtime_status_returns_schema_incomplete_payload_when_runtime_columns_missing(monkeypatch):
    now = datetime.now(timezone.utc)
    monkeypatch.setattr(
        monitoring_runner,
        'get_monitoring_health',
        lambda: {'last_heartbeat_at': now.isoformat(), 'last_cycle_at': now.isoformat(), 'degraded': False, 'last_error': None, 'source_type': 'polling'},
    )
    monkeypatch.setattr(monitoring_runner, 'ensure_pilot_schema', lambda _c: None)

    def _raise_schema_error(_connection):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                'code': 'runtime_schema_incomplete',
                'missing_columns': ['monitored_systems.last_coverage_telemetry_at'],
                'migration_hints': ['0036', '0037', '0038', '0039'],
            },
        )

    monkeypatch.setattr(monitoring_runner, 'ensure_monitoring_runtime_schema_capabilities', _raise_schema_error)
    monkeypatch.setattr(monitoring_runner, 'pg_connection', lambda: _fake_pg(_Conn(now - timedelta(seconds=30))))

    payload = monitoring_runner.monitoring_runtime_status()
    summary = payload['workspace_monitoring_summary']
    assert payload['configuration_reason'] == 'runtime_schema_incomplete'
    assert payload['status_reason'] == 'runtime_schema_column_missing:monitored_systems.last_coverage_telemetry_at'
    assert payload['error']['code'] == 'runtime_schema_incomplete'
    assert payload['error']['migration_hints'] == ['0036', '0037', '0038', '0039']
    assert summary['configuration_reason'] == 'runtime_schema_incomplete'
    assert summary['status_reason'] == 'runtime_schema_column_missing:monitored_systems.last_coverage_telemetry_at'
