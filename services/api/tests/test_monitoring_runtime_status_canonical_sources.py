from datetime import datetime, timezone

from services.api.app import monitoring_runner
from services.api.tests.test_monitoring_runtime_status_states import _Conn, _fake_pg


class _CanonicalConn(_Conn):
    def execute(self, query, params=None):
        q = ' '.join(str(query).split())
        now = datetime.now(timezone.utc)
        if 'FROM target_coverage_records' in q and 'DISTINCT ON (target_id)' in q:
            return type('R', (), {'fetchall': lambda _s: [
                {'target_id': 'target-1', 'coverage_status': 'reporting', 'last_telemetry_at': now, 'evidence_source': 'live', 'computed_at': now}
            ]})()
        if 'FROM provider_health_records' in q and 'DISTINCT ON (provider_type' in q:
            return type('R', (), {'fetchall': lambda _s: [
                {'provider_type': 'rpc', 'target_id': 'target-1', 'status': 'healthy', 'checked_at': now, 'latency_ms': 15, 'error_message': None, 'evidence_source': 'live', 'metadata': {}}
            ]})()
        return super().execute(query, params)


def test_runtime_status_exposes_canonical_provider_and_coverage(monkeypatch):
    now = datetime.now(timezone.utc)
    monkeypatch.setattr(
        monitoring_runner,
        'get_monitoring_health',
        lambda: {'last_heartbeat_at': now.isoformat(), 'last_cycle_at': now.isoformat(), 'degraded': False, 'last_error': None, 'source_type': 'polling'},
    )
    monkeypatch.setattr(monitoring_runner, 'ensure_pilot_schema', lambda _c: None)
    monkeypatch.setattr(monitoring_runner, 'ensure_monitoring_runtime_schema_capabilities', lambda _c: None)
    monkeypatch.setattr(monitoring_runner, 'pg_connection', lambda: _fake_pg(_CanonicalConn(now)))

    payload = monitoring_runner.monitoring_runtime_status()

    assert isinstance(payload, dict)
    assert payload.get('workspace_monitoring_summary') is not None
    # Runtime payload remains stable and contains canonical summary object.


def test_runtime_status_reporting_systems_zero_without_telemetry_coverage(monkeypatch):
    class _StaleCoverageConn(_CanonicalConn):
        def execute(self, query, params=None):
            q = ' '.join(str(query).split())
            now = datetime.now(timezone.utc)
            if 'FROM target_coverage_records' in q and 'DISTINCT ON (target_id)' in q:
                return type('R', (), {'fetchall': lambda _s: [
                    {'target_id': 'target-1', 'coverage_status': 'stale', 'last_telemetry_at': None, 'evidence_source': 'none', 'computed_at': now}
                ]})()
            return super().execute(query, params)

    now = datetime.now(timezone.utc)
    monkeypatch.setattr(monitoring_runner, 'get_monitoring_health', lambda: {'last_heartbeat_at': now.isoformat(), 'last_cycle_at': now.isoformat(), 'degraded': False, 'last_error': None, 'source_type': 'polling'})
    monkeypatch.setattr(monitoring_runner, 'ensure_pilot_schema', lambda _c: None)
    monkeypatch.setattr(monitoring_runner, 'ensure_monitoring_runtime_schema_capabilities', lambda _c: None)
    monkeypatch.setattr(monitoring_runner, 'pg_connection', lambda: _fake_pg(_StaleCoverageConn(now)))
    payload = monitoring_runner.monitoring_runtime_status()
    assert int(payload.get('reporting_systems_count') or 0) == 0


def test_runtime_status_simulator_is_not_labeled_live(monkeypatch):
    now = datetime.now(timezone.utc)
    monkeypatch.setattr(
        monitoring_runner,
        'get_monitoring_health',
        lambda: {'last_heartbeat_at': now.isoformat(), 'last_cycle_at': now.isoformat(), 'degraded': False, 'last_error': None, 'source_type': 'polling', 'ingestion_mode': 'simulator'},
    )
    monkeypatch.setattr(monitoring_runner, 'ensure_pilot_schema', lambda _c: None)
    monkeypatch.setattr(monitoring_runner, 'ensure_monitoring_runtime_schema_capabilities', lambda _c: None)
    monkeypatch.setattr(monitoring_runner, 'pg_connection', lambda: _fake_pg(_CanonicalConn(now)))
    payload = monitoring_runner.monitoring_runtime_status()
    assert payload.get('evidence_source') != 'live'
