from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from services.api.app import monitoring_runner


class _Result:
    def __init__(self, row=None, rows=None):
        self._row = row
        self._rows = rows or []

    def fetchone(self):
        return self._row

    def fetchall(self):
        return self._rows


class _RuntimeStatusConn:
    def __init__(
        self,
        *,
        now: datetime,
        raw_open_alerts: int,
        chain_open_alerts: int,
        raw_open_incidents: int,
        chain_open_incidents: int,
        incidents_without_alert: int = 0,
        response_actions_without_incident: int = 0,
        latest_detection_at: datetime | None,
    ) -> None:
        self.now = now
        self.raw_open_alerts = raw_open_alerts
        self.chain_open_alerts = chain_open_alerts
        self.raw_open_incidents = raw_open_incidents
        self.chain_open_incidents = chain_open_incidents
        self.incidents_without_alert = incidents_without_alert
        self.response_actions_without_incident = response_actions_without_incident
        self.latest_detection_at = latest_detection_at

    def execute(self, query, params=None):
        q = ' '.join(str(query).split())
        if 'FROM monitoring_workspace_runtime_summary' in q:
            return _Result(None)
        if "FROM alerts WHERE status IN ('open','acknowledged','investigating')" in q and 'FROM alerts a' not in q:
            return _Result({'c': self.raw_open_alerts})
        if 'FROM alerts a' in q and 'JOIN detections d' in q and 'FROM detection_evidence de' in q:
            return _Result({'c': self.chain_open_alerts})
        if "FROM incidents WHERE status IN ('open','acknowledged')" in q and 'WITH proof_chain_alerts AS (' not in q:
            return _Result({'c': self.raw_open_incidents})
        if 'WITH proof_chain_alerts AS (' in q and 'SELECT COUNT(DISTINCT i.id) AS c' in q:
            return _Result({'c': self.chain_open_incidents})
        if 'FROM incidents i' in q and 'NOT EXISTS (' in q and 'FROM alerts a' in q:
            return _Result({'c': self.incidents_without_alert})
        if 'FROM response_actions ra' in q and 'ra.incident_id IS NULL' in q:
            return _Result({'c': self.response_actions_without_incident})
        if 'FROM targets t LEFT JOIN assets a' in q:
            return _Result({'c': 0})
        if 'SELECT COUNT(*) AS c FROM targets t WHERE t.deleted_at IS NULL AND t.enabled = TRUE' in q:
            return _Result({'c': 1})
        if 'SELECT t.id, t.asset_id FROM targets t JOIN assets a' in q:
            return _Result(rows=[{'id': 'target-1', 'asset_id': 'asset-1'}])
        if 'SELECT observed_at, block_number FROM evidence e' in q:
            return _Result({'observed_at': self.now - timedelta(seconds=30), 'block_number': 321})
        if 'FROM analysis_runs' in q:
            return _Result({'created_at': self.now - timedelta(seconds=20), 'response_payload': {'metadata': {'recent_real_event_count': 1, 'evidence_state': 'real'}}})
        if 'SELECT detected_at FROM detections' in q:
            return _Result({'detected_at': self.latest_detection_at} if self.latest_detection_at else None)
        if 'WITH filtered_receipts AS (' in q:
            return _Result(rows=[])
        return _Result(None)


@contextmanager
def _fake_pg(conn):
    yield conn


def _request() -> SimpleNamespace:
    return SimpleNamespace(headers={'x-workspace-id': '11111111-1111-1111-1111-111111111111'}, state=SimpleNamespace())


def _setup_runtime_status(monkeypatch, conn: _RuntimeStatusConn, now: datetime) -> None:
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
    monitored_rows = [{
        'id': 'sys-1',
        'workspace_id': '11111111-1111-1111-1111-111111111111',
        'asset_id': 'asset-1',
        'target_id': 'target-1',
        'is_enabled': True,
        'runtime_status': 'healthy',
        'last_heartbeat': now.isoformat(),
        'last_event_at': (now - timedelta(seconds=20)).isoformat(),
        'last_coverage_telemetry_at': (now - timedelta(seconds=20)).isoformat(),
        'monitoring_interval_seconds': 30,
        'target_type': 'wallet',
        'created_at': now.isoformat(),
    }]
    monkeypatch.setattr(monitoring_runner, 'list_workspace_monitored_system_rows', lambda _connection, _workspace_id: monitored_rows)
    monkeypatch.setattr(monitoring_runner, 'reconcile_enabled_targets_monitored_systems', lambda _connection, workspace_id: {'created_or_updated': 0})
    monkeypatch.setattr(
        monitoring_runner,
        'get_monitoring_health',
        lambda: {
            'worker_running': True,
            'degraded': False,
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
        lambda: {'checks': {'provider_reachable_or_backfilling': True, 'evm_rpc_reachable': True}, 'sales_claims_allowed': False, 'status': 'FAIL', 'recent_truthfulness_state': 'unknown_risk'},
    )


def test_runtime_status_proof_chain_complete_sets_detection_checkpoint_without_contradictions(monkeypatch):
    now = datetime(2026, 4, 25, 10, 0, tzinfo=timezone.utc)
    conn = _RuntimeStatusConn(
        now=now,
        raw_open_alerts=1,
        chain_open_alerts=1,
        raw_open_incidents=1,
        chain_open_incidents=1,
        latest_detection_at=now - timedelta(minutes=2),
    )
    _setup_runtime_status(monkeypatch, conn, now)

    payload = monitoring_runner.monitoring_runtime_status(_request())

    assert payload['proof_chain_status'] == 'complete'
    assert payload['last_detection_at'] is not None
    assert payload['raw_open_alerts'] == payload['active_alerts']
    assert payload['raw_open_incidents'] == payload['open_incidents']
    assert payload['open_alerts_without_detection_evidence'] == 0
    assert 'open_alerts_without_detection_evidence' not in payload['contradiction_flags']
    assert payload['status_reason'] != 'alerts_without_detection_evidence'


def test_runtime_status_proof_chain_alerts_without_evidence_sets_degraded_reason(monkeypatch):
    now = datetime(2026, 4, 25, 10, 5, tzinfo=timezone.utc)
    conn = _RuntimeStatusConn(
        now=now,
        raw_open_alerts=2,
        chain_open_alerts=0,
        raw_open_incidents=1,
        chain_open_incidents=0,
        latest_detection_at=None,
    )
    _setup_runtime_status(monkeypatch, conn, now)

    payload = monitoring_runner.monitoring_runtime_status(_request())

    assert payload['proof_chain_status'] == 'incomplete'
    assert payload['status_reason'] == 'alerts_without_detection_evidence'
    assert payload['workspace_monitoring_summary']['status_reason'] == 'alerts_without_detection_evidence'
    assert 'open_alerts_without_detection_evidence' in payload['contradiction_flags']
    assert 'open_alerts_without_detection_evidence' in payload['workspace_monitoring_summary']['contradiction_flags']


def test_runtime_status_proof_chain_impossible_states_emit_contradiction_flags(monkeypatch):
    now = datetime(2026, 4, 25, 10, 10, tzinfo=timezone.utc)
    conn = _RuntimeStatusConn(
        now=now,
        raw_open_alerts=3,
        chain_open_alerts=1,
        raw_open_incidents=2,
        chain_open_incidents=1,
        incidents_without_alert=1,
        response_actions_without_incident=1,
        latest_detection_at=now - timedelta(minutes=1),
    )
    _setup_runtime_status(monkeypatch, conn, now)

    payload = monitoring_runner.monitoring_runtime_status(_request())

    assert 'open_alerts_without_detection_evidence' in payload['contradiction_flags']
    assert 'incident_without_alert' in payload['contradiction_flags']
    assert 'response_action_without_incident' in payload['contradiction_flags']
