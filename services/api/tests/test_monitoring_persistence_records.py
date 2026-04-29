from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from fastapi.testclient import TestClient

from services.api.app import main as api_main
from services.api.app import monitoring_runner
from services.api.app.workspace_monitoring_summary import build_workspace_monitoring_summary


@contextmanager
def _fake_pg(connection):
    yield connection


def _seed_runtime_chain_db() -> sqlite3.Connection:
    conn = sqlite3.connect(':memory:')
    conn.row_factory = sqlite3.Row
    conn.executescript(
        '''
        CREATE TABLE provider_health_records (id TEXT PRIMARY KEY, provider_name TEXT, status TEXT, observed_at TEXT);
        CREATE TABLE target_coverage_records (id TEXT PRIMARY KEY, target_id TEXT, telemetry_kind TEXT, observed_at TEXT, evidence_source TEXT);
        CREATE TABLE telemetry_events (id TEXT PRIMARY KEY, observed_at TEXT, kind TEXT, source TEXT);
        CREATE TABLE detection_events (id TEXT PRIMARY KEY, telemetry_event_id TEXT, created_at TEXT);
        CREATE TABLE alerts (id TEXT PRIMARY KEY, detection_event_id TEXT, status TEXT);
        CREATE TABLE incidents (id TEXT PRIMARY KEY, alert_id TEXT, status TEXT);
        CREATE TABLE incident_timeline (id TEXT PRIMARY KEY, incident_id TEXT, event_type TEXT);
        CREATE TABLE governance_actions (id TEXT PRIMARY KEY, alert_id TEXT, incident_id TEXT, action_type TEXT);
        '''
    )
    return conn


def test_provider_and_target_loops_write_records_with_real_inserts():
    conn = _seed_runtime_chain_db()
    now = datetime.now(timezone.utc).isoformat()

    conn.execute(
        'INSERT INTO provider_health_records (id, provider_name, status, observed_at) VALUES (?, ?, ?, ?)',
        ('phr-1', 'rpc', 'live', now),
    )
    conn.execute(
        'INSERT INTO target_coverage_records (id, target_id, telemetry_kind, observed_at, evidence_source) VALUES (?, ?, ?, ?, ?)',
        ('tcr-1', 'target-1', 'coverage', now, 'live'),
    )

    provider_row = conn.execute('SELECT provider_name, status FROM provider_health_records WHERE id = ?', ('phr-1',)).fetchone()
    coverage_row = conn.execute('SELECT target_id, telemetry_kind, evidence_source FROM target_coverage_records WHERE id = ?', ('tcr-1',)).fetchone()

    assert provider_row['provider_name'] == 'rpc'
    assert provider_row['status'] == 'live'
    assert coverage_row['target_id'] == 'target-1'
    assert coverage_row['telemetry_kind'] == 'coverage'
    assert coverage_row['evidence_source'] == 'live'


def test_full_fk_chain_persists_end_to_end_ids():
    conn = _seed_runtime_chain_db()
    now = datetime.now(timezone.utc).isoformat()

    conn.execute('INSERT INTO telemetry_events (id, observed_at, kind, source) VALUES (?, ?, ?, ?)', ('te-1', now, 'target_event', 'websocket'))
    conn.execute('INSERT INTO detection_events (id, telemetry_event_id, created_at) VALUES (?, ?, ?)', ('de-1', 'te-1', now))
    conn.execute('INSERT INTO alerts (id, detection_event_id, status) VALUES (?, ?, ?)', ('al-1', 'de-1', 'open'))
    conn.execute('INSERT INTO incidents (id, alert_id, status) VALUES (?, ?, ?)', ('in-1', 'al-1', 'open'))
    conn.execute('INSERT INTO incident_timeline (id, incident_id, event_type) VALUES (?, ?, ?)', ('it-1', 'in-1', 'created'))
    conn.execute('INSERT INTO governance_actions (id, alert_id, incident_id, action_type) VALUES (?, ?, ?, ?)', ('ga-1', 'al-1', 'in-1', 'notify'))

    row = conn.execute(
        '''
        SELECT te.id telemetry_id, de.telemetry_event_id, a.detection_event_id, i.alert_id, it.incident_id, ga.alert_id ga_alert, ga.incident_id ga_incident
        FROM telemetry_events te
        JOIN detection_events de ON de.telemetry_event_id = te.id
        JOIN alerts a ON a.detection_event_id = de.id
        JOIN incidents i ON i.alert_id = a.id
        JOIN incident_timeline it ON it.incident_id = i.id
        JOIN governance_actions ga ON ga.alert_id = a.id AND ga.incident_id = i.id
        WHERE te.id = ?
        ''',
        ('te-1',),
    ).fetchone()

    assert row['telemetry_id'] == 'te-1'
    assert row['telemetry_event_id'] == 'te-1'
    assert row['detection_event_id'] == 'de-1'
    assert row['alert_id'] == 'al-1'
    assert row['incident_id'] == 'in-1'
    assert row['ga_alert'] == 'al-1'
    assert row['ga_incident'] == 'in-1'


def test_summary_timestamps_reporting_and_live_evidence_rules():
    now = datetime.now(timezone.utc)
    base = dict(
        now=now,
        workspace_configured=True,
        configuration_reason_codes=[],
        query_failure_detected=False,
        schema_drift_detected=False,
        missing_telemetry_only=False,
        monitoring_mode='live',
        runtime_status='live',
        configured_systems=1,
        monitored_systems_count=1,
        protected_assets=1,
        status_reason=None,
        configuration_reason=None,
        valid_protected_asset_count=1,
        linked_monitored_system_count=1,
        persisted_enabled_config_count=1,
        valid_target_system_link_count=1,
        telemetry_window_seconds=300,
    )

    heartbeat_poll_only = build_workspace_monitoring_summary(
        **base,
        reporting_systems=0,
        last_poll_at=now,
        last_heartbeat_at=now,
        last_telemetry_at=None,
        last_coverage_telemetry_at=None,
        telemetry_kind=None,
        last_detection_at=None,
        evidence_source='live',
    )
    assert heartbeat_poll_only['last_telemetry_at'] is None
    assert heartbeat_poll_only['reporting_systems_count'] == 0

    telemetry_backed = build_workspace_monitoring_summary(
        **base,
        reporting_systems=1,
        last_poll_at=now,
        last_heartbeat_at=now,
        last_telemetry_at=now,
        last_coverage_telemetry_at=now,
        telemetry_kind='coverage',
        last_detection_at=now,
        evidence_source='live',
    )
    assert telemetry_backed['reporting_systems_count'] == 1
    assert telemetry_backed['last_telemetry_at'] is not None

    simulator_summary = build_workspace_monitoring_summary(
        **base,
        reporting_systems=1,
        last_poll_at=now,
        last_heartbeat_at=now,
        last_telemetry_at=now,
        last_coverage_telemetry_at=now,
        telemetry_kind='coverage',
        last_detection_at=now,
        evidence_source='simulator',
    )
    assert simulator_summary['evidence_source_summary'] == 'simulator'


def test_runtime_status_endpoint_includes_detection_and_contradiction_guards(monkeypatch):
    now = datetime(2026, 4, 29, 12, 0, tzinfo=timezone.utc)

    class _Conn:
        def execute(self, query, params=None):
            q = ' '.join(str(query).split())
            class _R:
                def __init__(self, row=None, rows=None):
                    self.row, self.rows = row, rows or []
                def fetchone(self): return self.row
                def fetchall(self): return self.rows
            if 'FROM monitoring_workspace_runtime_summary' in q:
                return _R(None)
            if 'SELECT COUNT(*) AS c FROM targets t WHERE t.deleted_at IS NULL AND t.enabled = TRUE' in q:
                return _R({'c': 1})
            if 'SELECT t.id, t.asset_id FROM targets t JOIN assets a' in q:
                return _R(rows=[{'id': 'target-1', 'asset_id': 'asset-1'}])
            if 'SELECT observed_at, block_number FROM evidence e' in q:
                return _R({'observed_at': now - timedelta(seconds=10), 'block_number': 9})
            if 'FROM analysis_runs' in q:
                return _R({'created_at': now, 'response_payload': {'metadata': {'recent_real_event_count': 1, 'evidence_state': 'real'}}})
            if 'SELECT detected_at FROM detections' in q:
                return _R({'detected_at': now})
            if 'FROM alerts' in q or 'FROM incidents' in q or 'FROM targets t LEFT JOIN assets a' in q:
                return _R({'c': 0})
            if 'WITH filtered_receipts AS (' in q:
                return _R(rows=[])
            return _R(None)

    monitoring_runner.RUNTIME_STATUS_WORKSPACE_CACHE.clear()
    monitoring_runner.RUNTIME_STATUS_SUMMARY_CACHE.clear()
    monkeypatch.setattr(monitoring_runner, 'utc_now', lambda: now)
    monkeypatch.setattr(monitoring_runner, 'live_mode_enabled', lambda: True)
    monkeypatch.setattr(monitoring_runner, 'ensure_pilot_schema', lambda _c: None)
    monkeypatch.setattr(monitoring_runner, 'ensure_monitoring_runtime_schema_capabilities', lambda _c: None)
    monkeypatch.setattr(monitoring_runner, 'pg_connection', lambda: _fake_pg(_Conn()))
    monkeypatch.setattr(monitoring_runner, 'resolve_workspace_context_for_request', lambda *_: ({'id': 'u'}, {'workspace_id': 'ws-1', 'workspace': {'slug': 'acme'}}, True))
    monkeypatch.setattr(monitoring_runner, 'list_workspace_monitored_system_rows', lambda *_: [])
    monkeypatch.setattr(monitoring_runner, 'reconcile_enabled_targets_monitored_systems', lambda *_: {'created_or_updated': 0})
    monkeypatch.setattr(monitoring_runner, 'get_monitoring_health', lambda: {'worker_running': True, 'degraded': False, 'source_type': 'polling', 'mode': 'live', 'operational_mode': 'LIVE', 'ingestion_mode': 'live', 'last_heartbeat_at': now.isoformat(), 'last_cycle_at': now.isoformat(), 'last_error': None})
    monkeypatch.setattr(monitoring_runner, 'production_claim_validator', lambda: {'checks': {'provider_reachable_or_backfilling': True, 'evm_rpc_reachable': True}, 'sales_claims_allowed': False, 'status': 'FAIL', 'recent_truthfulness_state': 'unknown_risk'})

    client = TestClient(api_main.app)
    response = client.get('/ops/monitoring/runtime-status', headers={'x-workspace-id': 'ws-1'})

    assert response.status_code == 200
    payload = response.json()
    assert payload['runtime_status'] == 'offline'
    assert payload['status_reason'].startswith('runtime_status_degraded')
