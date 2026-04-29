from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import psycopg

from services.api.app import monitoring_runner


class _Result:
    def __init__(self, row=None, rows=None):
        self._row = row
        self._rows = rows or []

    def fetchone(self):
        return self._row

    def fetchall(self):
        return self._rows


class _FailureInjectionConn:
    def __init__(
        self,
        *,
        now: datetime,
        fail_alerts_query: bool = False,
        stale_telemetry: bool = False,
        response_actions_without_incident: int = 0,
        target_coverage_rows: list[dict] | None = None,
    ) -> None:
        self.now = now
        self.fail_alerts_query = fail_alerts_query
        self.stale_telemetry = stale_telemetry
        self.response_actions_without_incident = response_actions_without_incident
        self.target_coverage_rows = target_coverage_rows

    def execute(self, query, params=None):
        q = ' '.join(str(query).split())
        if 'FROM monitoring_workspace_runtime_summary' in q:
            return _Result(None)
        if self.fail_alerts_query and "FROM alerts WHERE status IN ('open','acknowledged','investigating')" in q and 'FROM alerts a' not in q:
            raise psycopg.OperationalError('failure-injection: alerts table temporarily unavailable')
        if "FROM alerts WHERE status IN ('open','acknowledged','investigating')" in q and 'FROM alerts a' not in q:
            return _Result({'c': 1})
        if 'FROM alerts a' in q and 'JOIN detections d' in q and 'FROM detection_evidence de' in q:
            return _Result({'c': 1})
        if "FROM incidents WHERE status IN ('open','acknowledged')" in q and 'WITH proof_chain_alerts AS (' not in q:
            return _Result({'c': 1})
        if 'WITH proof_chain_alerts AS (' in q and 'SELECT COUNT(DISTINCT i.id) AS c' in q:
            return _Result({'c': 1})
        if 'FROM incidents i' in q and 'NOT EXISTS (' in q and 'FROM alerts a' in q:
            return _Result({'c': 0})
        if 'FROM response_actions ra' in q and 'ra.incident_id IS NULL' in q:
            return _Result({'c': self.response_actions_without_incident})
        if 'FROM targets t LEFT JOIN assets a' in q:
            return _Result({'c': 0})
        if 'SELECT COUNT(*) AS c FROM targets t WHERE t.deleted_at IS NULL AND t.enabled = TRUE' in q:
            return _Result({'c': 1})
        if 'SELECT t.id, t.asset_id FROM targets t JOIN assets a' in q:
            return _Result(rows=[{'id': 'target-1', 'asset_id': 'asset-1'}])
        if 'SELECT observed_at, block_number FROM evidence e' in q:
            observed = self.now - timedelta(minutes=20) if self.stale_telemetry else self.now - timedelta(seconds=20)
            return _Result({'observed_at': observed, 'block_number': 123})
        if 'FROM analysis_runs' in q:
            created = self.now - (timedelta(minutes=15) if self.stale_telemetry else timedelta(seconds=15))
            return _Result({'created_at': created, 'response_payload': {'metadata': {'recent_real_event_count': 1, 'evidence_state': 'real'}}})
        if 'SELECT detected_at FROM detections' in q:
            detected = self.now - (timedelta(minutes=20) if self.stale_telemetry else timedelta(seconds=30))
            return _Result({'detected_at': detected})
        if 'WITH filtered_receipts AS (' in q:
            return _Result(rows=[])
        if 'SELECT DISTINCT ON (target_id)' in q and 'FROM target_coverage_records' in q:
            return _Result(rows=self.target_coverage_rows or [])
        return _Result(None)


@contextmanager
def _fake_pg(conn):
    yield conn


def _request() -> SimpleNamespace:
    return SimpleNamespace(headers={'x-workspace-id': '11111111-1111-1111-1111-111111111111'}, state=SimpleNamespace())


def _setup(monkeypatch, conn: _FailureInjectionConn, now: datetime, *, health_degraded_reason: str | None = None) -> None:
    monitoring_runner.RUNTIME_STATUS_WORKSPACE_CACHE.clear()
    monitoring_runner.RUNTIME_STATUS_SUMMARY_CACHE.clear()
    monkeypatch.setattr(monitoring_runner, 'utc_now', lambda: now)
    monkeypatch.setattr(monitoring_runner, 'live_mode_enabled', lambda: True)
    monkeypatch.setattr(monitoring_runner, 'ensure_pilot_schema', lambda _c: None)
    monkeypatch.setattr(monitoring_runner, 'ensure_monitoring_runtime_schema_capabilities', lambda _c: None)
    monkeypatch.setattr(monitoring_runner, 'pg_connection', lambda: _fake_pg(conn))
    monkeypatch.setattr(
        monitoring_runner,
        'resolve_workspace_context_for_request',
        lambda _connection, _request: ({'id': 'user-1'}, {'workspace_id': '11111111-1111-1111-1111-111111111111', 'workspace': {'slug': 'acme'}}, True),
    )
    monkeypatch.setattr(
        monitoring_runner,
        'list_workspace_monitored_system_rows',
        lambda _connection, _workspace_id: [{
            'id': 'sys-1',
            'workspace_id': '11111111-1111-1111-1111-111111111111',
            'asset_id': 'asset-1',
            'target_id': 'target-1',
            'is_enabled': True,
            'runtime_status': 'healthy',
            'last_heartbeat': now.isoformat(),
            'last_event_at': (now - (timedelta(minutes=20) if conn.stale_telemetry else timedelta(seconds=20))).isoformat(),
            'last_coverage_telemetry_at': (now - (timedelta(minutes=20) if conn.stale_telemetry else timedelta(seconds=20))).isoformat(),
            'monitoring_interval_seconds': 30,
            'target_type': 'wallet',
            'created_at': now.isoformat(),
        }],
    )
    monkeypatch.setattr(monitoring_runner, 'reconcile_enabled_targets_monitored_systems', lambda _connection, workspace_id: {'created_or_updated': 0})
    monkeypatch.setattr(
        monitoring_runner,
        'get_monitoring_health',
        lambda: {
            'worker_running': True,
            'degraded': bool(health_degraded_reason),
            'degraded_reason': health_degraded_reason,
            'last_error': None,
            'source_type': 'polling',
            'mode': 'live',
            'operational_mode': 'LIVE',
            'ingestion_mode': 'live',
            'last_heartbeat_at': now.isoformat(),
            'last_cycle_at': now.isoformat(),
        },
    )
    monkeypatch.setattr(
        monitoring_runner,
        'production_claim_validator',
        lambda: {'checks': {'provider_reachable_or_backfilling': not bool(health_degraded_reason), 'evm_rpc_reachable': True}, 'sales_claims_allowed': False, 'status': 'FAIL', 'recent_truthfulness_state': 'unknown_risk'},
    )


def test_failure_injection_db_degradation_and_partial_query_failure(monkeypatch):
    now = datetime(2026, 4, 28, 8, 0, tzinfo=timezone.utc)
    conn = _FailureInjectionConn(now=now, fail_alerts_query=True)
    _setup(monkeypatch, conn, now)

    payload = monitoring_runner.monitoring_runtime_status(_request())

    assert payload['runtime_degraded_reason'] == 'partial_query_failure'
    assert payload['status_reason'] in {'runtime_status_degraded:partial_query_failure', 'no_fresh_live_coverage_telemetry'}
    assert payload['field_reason_codes']['active_alerts_count'] == ['optional_table_unavailable']


def test_failure_injection_provider_unreachable_sets_degraded_reason(monkeypatch):
    now = datetime(2026, 4, 28, 8, 5, tzinfo=timezone.utc)
    conn = _FailureInjectionConn(now=now)
    _setup(monkeypatch, conn, now, health_degraded_reason='provider_unreachable')

    payload = monitoring_runner.monitoring_runtime_status(_request())

    assert payload['degraded_reason'] == 'provider_unreachable'
    assert payload['monitoring_status'] in {'degraded', 'limited'}


def test_failure_injection_stale_telemetry_downgrades_freshness(monkeypatch):
    now = datetime(2026, 4, 28, 8, 10, tzinfo=timezone.utc)
    conn = _FailureInjectionConn(now=now, stale_telemetry=True)
    _setup(monkeypatch, conn, now)

    payload = monitoring_runner.monitoring_runtime_status(_request())

    assert payload['telemetry_freshness'] in {'stale', 'unavailable'}
    assert payload['workspace_monitoring_summary']['telemetry_freshness'] in {'stale', 'unavailable'}


def test_chain_integrity_hidden_problem_flags_action_without_incident(monkeypatch):
    now = datetime(2026, 4, 28, 8, 15, tzinfo=timezone.utc)
    conn = _FailureInjectionConn(now=now, response_actions_without_incident=2)
    _setup(monkeypatch, conn, now)

    payload = monitoring_runner.monitoring_runtime_status(_request())

    assert 'response_action_without_incident' in payload['contradiction_flags']
