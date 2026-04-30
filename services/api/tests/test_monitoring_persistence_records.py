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
        CREATE TABLE provider_health_records (
            id TEXT PRIMARY KEY,
            workspace_id TEXT,
            provider_name TEXT,
            status TEXT,
            provider_type TEXT,
            target_id TEXT,
            checked_at TEXT,
            latency_ms INTEGER,
            error_message TEXT,
            evidence_source TEXT,
            metadata TEXT
        );
        CREATE TABLE target_coverage_records (
            id TEXT PRIMARY KEY,
            workspace_id TEXT,
            asset_id TEXT,
            target_id TEXT,
            coverage_status TEXT,
            last_poll_at TEXT,
            last_heartbeat_at TEXT,
            last_telemetry_at TEXT,
            last_detection_at TEXT,
            computed_at TEXT,
            telemetry_basis TEXT,
            telemetry_event_id TEXT,
            evidence_source TEXT,
            metadata TEXT
        );
        CREATE TABLE telemetry_events (
            id TEXT PRIMARY KEY,
            workspace_id TEXT,
            asset_id TEXT,
            target_id TEXT,
            provider_type TEXT,
            event_type TEXT,
            observed_at TEXT,
            ingested_at TEXT,
            evidence_source TEXT,
            payload_hash TEXT,
            payload_json TEXT
        );
        CREATE TABLE detection_events (
            id TEXT PRIMARY KEY,
            workspace_id TEXT,
            asset_id TEXT,
            target_id TEXT,
            telemetry_event_id TEXT,
            detection_type TEXT,
            severity TEXT,
            confidence REAL,
            evidence_summary TEXT,
            evidence_source TEXT,
            created_at TEXT
        );
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
        'INSERT INTO provider_health_records (id, provider_name, status, provider_type, checked_at, evidence_source, metadata) VALUES (?, ?, ?, ?, ?, ?, ?)',
        ('phr-1', 'rpc', 'live', 'rpc', now, 'live', '{}'),
    )
    conn.execute(
        'INSERT INTO target_coverage_records (id, target_id, coverage_status, last_poll_at, last_heartbeat_at, last_telemetry_at, last_detection_at, computed_at, telemetry_basis, telemetry_event_id, evidence_source, metadata) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
        ('tcr-1', 'target-1', 'reporting', now, now, now, now, now, 'telemetry', 'te-1', 'live', '{}'),
    )

    provider_row = conn.execute(
        'SELECT provider_name, status, provider_type, checked_at, evidence_source, metadata FROM provider_health_records WHERE id = ?',
        ('phr-1',),
    ).fetchone()
    coverage_row = conn.execute(
        'SELECT target_id, coverage_status, last_poll_at, last_heartbeat_at, last_telemetry_at, last_detection_at, computed_at, evidence_source, metadata FROM target_coverage_records WHERE id = ?',
        ('tcr-1',),
    ).fetchone()

    assert provider_row['provider_name'] == 'rpc'
    assert provider_row['status'] == 'live'
    assert coverage_row['target_id'] == 'target-1'
    assert coverage_row['coverage_status'] == 'reporting'
    assert coverage_row['evidence_source'] == 'live'
    assert provider_row['provider_type'] == 'rpc'
    assert provider_row['checked_at'] == now
    assert provider_row['metadata'] == '{}'
    assert coverage_row['last_poll_at'] == now
    assert coverage_row['last_heartbeat_at'] == now
    assert coverage_row['last_telemetry_at'] == now
    assert coverage_row['last_detection_at'] == now
    assert coverage_row['computed_at'] == now
    assert coverage_row['metadata'] == '{}'


def test_full_fk_chain_persists_end_to_end_ids():
    conn = _seed_runtime_chain_db()
    now = datetime.now(timezone.utc).isoformat()

    conn.execute('INSERT INTO telemetry_events (id, workspace_id, asset_id, target_id, provider_type, event_type, observed_at, ingested_at, evidence_source, payload_hash, payload_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)', ('te-1', 'ws-1', 'asset-1', 'target-1', 'rpc', 'heartbeat', now, now, 'live', 'h1', '{}'))
    conn.execute('INSERT INTO detection_events (id, workspace_id, asset_id, target_id, telemetry_event_id, detection_type, severity, confidence, evidence_summary, evidence_source, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)', ('de-1', 'ws-1', 'asset-1', 'target-1', 'te-1', 'anomaly', 'medium', 0.9, 'summary', 'live', now))
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
    assert payload['runtime_status'] in {'offline', 'fail'}
    assert payload['status_reason'] in {
        'runtime_status_degraded',
        'workspace_configuration_invalid:no_valid_protected_assets',
    } or payload['status_reason'].startswith('runtime_status_degraded')


_RUNTIME_STATUS_REQUIRED_TOP_LEVEL_KEYS = [
    'workspace_configured',
    'runtime_status',
    'configured_systems',
    'reporting_systems',
    'protected_assets',
    'last_poll_at',
    'last_heartbeat_at',
    'last_telemetry_at',
    'last_detection_at',
    'freshness_status',
    'confidence_status',
    'evidence_source',
    'status_reason',
    'contradiction_flags',
    'summary_generated_at',
    'provider_health',
    'target_coverage',
    'provider_health_records',
    'target_coverage_records',
    'provider_health_status',
    'target_coverage_status',
]


def _canonical_runtime_payload(**overrides):
    payload = {
        'workspace_configured': True,
        'runtime_status': 'degraded',
        'configured_systems': 1,
        'reporting_systems': 0,
        'protected_assets': 1,
        'last_poll_at': None,
        'last_heartbeat_at': None,
        'last_telemetry_at': None,
        'last_detection_at': None,
        'freshness_status': 'unavailable',
        'confidence_status': 'low',
        'evidence_source': 'none',
        'status_reason': 'no_fresh_live_coverage_telemetry',
        'contradiction_flags': [],
        'summary_generated_at': '2026-04-29T12:00:00Z',
        'provider_health': [],
        'target_coverage': [],
        'provider_health_records': [],
        'target_coverage_records': [],
    }
    payload.update(overrides)
    return payload


def test_runtime_status_behavioral_contract_and_single_source_timestamp_updates(monkeypatch):
    client = TestClient(api_main.app)
    monkeypatch.setattr(api_main, 'with_auth_schema_json', lambda handler: handler())
    monkeypatch.setattr(api_main, '_is_production_like_runtime', lambda: True)

    baseline = _canonical_runtime_payload()

    monkeypatch.setattr(api_main, 'monitoring_runtime_status', lambda _request: dict(baseline))
    res = client.get('/ops/monitoring/runtime-status', headers={'x-workspace-id': 'ws-1'})
    assert res.status_code == 200
    body = res.json()
    assert list(body.keys()) == _RUNTIME_STATUS_REQUIRED_TOP_LEVEL_KEYS

    poll_only = _canonical_runtime_payload(last_poll_at='2026-04-29T12:01:00Z')
    monkeypatch.setattr(api_main, 'monitoring_runtime_status', lambda _request: dict(poll_only))
    poll_body = client.get('/ops/monitoring/runtime-status', headers={'x-workspace-id': 'ws-1'}).json()
    changed = {k for k in _RUNTIME_STATUS_REQUIRED_TOP_LEVEL_KEYS if poll_body[k] != body[k]}
    assert changed == {'last_poll_at'}

    heartbeat_only = _canonical_runtime_payload(last_heartbeat_at='2026-04-29T12:02:00Z')
    monkeypatch.setattr(api_main, 'monitoring_runtime_status', lambda _request: dict(heartbeat_only))
    heartbeat_body = client.get('/ops/monitoring/runtime-status', headers={'x-workspace-id': 'ws-1'}).json()
    changed = {k for k in _RUNTIME_STATUS_REQUIRED_TOP_LEVEL_KEYS if heartbeat_body[k] != body[k]}
    assert changed == {'last_heartbeat_at'}

    telemetry_only = _canonical_runtime_payload(
        reporting_systems=1,
        last_telemetry_at='2026-04-29T12:03:00Z',
        target_coverage=[{'target_id': 'target-1', 'coverage_status': 'reporting', 'telemetry_basis': 'telemetry', 'telemetry_event_id': 'te-3'}],
        target_coverage_status='reporting',
        evidence_source='live',
        freshness_status='fresh',
    )
    monkeypatch.setattr(api_main, 'monitoring_runtime_status', lambda _request: dict(telemetry_only))
    telemetry_body = client.get('/ops/monitoring/runtime-status', headers={'x-workspace-id': 'ws-1'}).json()
    assert telemetry_body['last_telemetry_at'] == '2026-04-29T12:03:00Z'
    assert telemetry_body['reporting_systems'] == 1
    assert telemetry_body['target_coverage'][0]['coverage_status'] == 'reporting'
    assert telemetry_body['target_coverage_status'] == 'reporting'

    detection_only = _canonical_runtime_payload(last_detection_at='2026-04-29T12:04:00Z')
    monkeypatch.setattr(api_main, 'monitoring_runtime_status', lambda _request: dict(detection_only))
    detection_body = client.get('/ops/monitoring/runtime-status', headers={'x-workspace-id': 'ws-1'}).json()
    changed = {k for k in _RUNTIME_STATUS_REQUIRED_TOP_LEVEL_KEYS if detection_body[k] != body[k]}
    assert changed == {'last_detection_at'}


def test_runtime_status_reporting_systems_zero_with_only_heartbeat(monkeypatch):
    client = TestClient(api_main.app)
    monkeypatch.setattr(api_main, 'with_auth_schema_json', lambda handler: handler())
    monkeypatch.setattr(api_main, '_is_production_like_runtime', lambda: True)
    payload = _canonical_runtime_payload(last_heartbeat_at='2026-04-29T12:10:00Z')
    monkeypatch.setattr(api_main, 'monitoring_runtime_status', lambda _request: dict(payload))
    body = client.get('/ops/monitoring/runtime-status', headers={'x-workspace-id': 'ws-1'}).json()
    assert body['reporting_systems'] == 0
    assert body['last_heartbeat_at'] == '2026-04-29T12:10:00Z'
    assert body['last_poll_at'] is None
    assert body['last_telemetry_at'] is None


def test_runtime_status_reporting_systems_zero_with_only_poll(monkeypatch):
    client = TestClient(api_main.app)
    monkeypatch.setattr(api_main, 'with_auth_schema_json', lambda handler: handler())
    monkeypatch.setattr(api_main, '_is_production_like_runtime', lambda: True)
    payload = _canonical_runtime_payload(last_poll_at='2026-04-29T12:11:00Z')
    monkeypatch.setattr(api_main, 'monitoring_runtime_status', lambda _request: dict(payload))
    body = client.get('/ops/monitoring/runtime-status', headers={'x-workspace-id': 'ws-1'}).json()
    assert body['reporting_systems'] == 0
    assert body['last_poll_at'] == '2026-04-29T12:11:00Z'
    assert body['last_heartbeat_at'] is None
    assert body['last_telemetry_at'] is None


def test_runtime_status_reporting_stated_without_telemetry_link_keeps_reporting_systems_zero(monkeypatch):
    client = TestClient(api_main.app)
    monkeypatch.setattr(api_main, 'with_auth_schema_json', lambda handler: handler())
    monkeypatch.setattr(api_main, '_is_production_like_runtime', lambda: True)
    payload = _canonical_runtime_payload(
        reporting_systems=0,
        target_coverage=[{'target_id': 'target-1', 'coverage_status': 'reporting', 'telemetry_basis': 'telemetry', 'telemetry_event_id': None}],
        target_coverage_status='reporting',
    )
    monkeypatch.setattr(api_main, 'monitoring_runtime_status', lambda _request: dict(payload))
    body = client.get('/ops/monitoring/runtime-status', headers={'x-workspace-id': 'ws-1'}).json()
    assert body['target_coverage_status'] == 'reporting'
    assert body['reporting_systems'] == 0
    assert body['target_coverage'][0]['coverage_status'] == 'reporting'
    assert body['target_coverage'][0]['telemetry_event_id'] is None


def test_runtime_status_reporting_systems_requires_telemetry_basis_and_event_link(monkeypatch):
    client = TestClient(api_main.app)
    monkeypatch.setattr(api_main, 'with_auth_schema_json', lambda handler: handler())
    monkeypatch.setattr(api_main, '_is_production_like_runtime', lambda: True)
    payload = _canonical_runtime_payload(
        reporting_systems=1,
        evidence_source='live',
        target_coverage=[{'target_id': 'target-1', 'coverage_status': 'reporting', 'telemetry_basis': 'telemetry', 'telemetry_event_id': 'te-9'}],
        target_coverage_records=[{'target_id': 'target-1', 'coverage_status': 'reporting', 'telemetry_basis': 'telemetry', 'telemetry_event_id': 'te-9'}],
        last_telemetry_at='2026-04-29T12:12:00Z',
    )
    monkeypatch.setattr(api_main, 'monitoring_runtime_status', lambda _request: dict(payload))
    body = client.get('/ops/monitoring/runtime-status', headers={'x-workspace-id': 'ws-1'}).json()
    assert body['reporting_systems'] >= 1
    assert body['target_coverage'][0]['telemetry_basis'] == 'telemetry'
    assert body['target_coverage'][0]['telemetry_event_id'] == 'te-9'


def test_runtime_status_canonical_event_timestamps_and_non_live_simulator_replay(monkeypatch):
    client = TestClient(api_main.app)
    monkeypatch.setattr(api_main, 'with_auth_schema_json', lambda handler: handler())
    monkeypatch.setattr(api_main, '_is_production_like_runtime', lambda: True)
    payload = _canonical_runtime_payload(
        last_telemetry_at='2026-04-29T12:13:00Z',
        last_detection_at='2026-04-29T12:14:00Z',
        evidence_source='simulator',
        target_coverage=[{'target_id': 'target-1', 'coverage_status': 'reporting', 'last_telemetry_at': '2026-04-29T12:13:00Z', 'last_detection_at': '2026-04-29T12:14:00Z', 'evidence_source': 'replay'}],
    )
    monkeypatch.setattr(api_main, 'monitoring_runtime_status', lambda _request: dict(payload))
    body = client.get('/ops/monitoring/runtime-status', headers={'x-workspace-id': 'ws-1'}).json()
    assert body['last_telemetry_at'] == '2026-04-29T12:13:00Z'
    assert body['last_detection_at'] == '2026-04-29T12:14:00Z'
    assert body['evidence_source'] in {'simulator', 'replay'}
    assert body['evidence_source'] != 'live'
    assert body['target_coverage'][0]['last_telemetry_at'] == '2026-04-29T12:13:00Z'
    assert body['target_coverage'][0]['last_detection_at'] == '2026-04-29T12:14:00Z'


def test_runtime_status_ignores_legacy_demo_fallback_for_live_claims_and_uses_persisted_records(monkeypatch):
    client = TestClient(api_main.app)
    monkeypatch.setattr(api_main, 'with_auth_schema_json', lambda handler: handler())
    monkeypatch.setattr(api_main, '_is_production_like_runtime', lambda: True)

    persisted_provider_records = [{
        'provider_name': 'rpc',
        'status': 'degraded',
        'provider_type': 'rpc',
        'checked_at': '2026-04-29T12:05:00Z',
        'evidence_source': 'none',
        'metadata': {'error': 'legacy_rows_ignored'},
    }]
    persisted_coverage_records = [{
        'target_id': 'target-1',
        'coverage_status': 'none',
        'last_poll_at': '2026-04-29T12:05:00Z',
        'last_heartbeat_at': None,
        'last_telemetry_at': None,
        'last_detection_at': None,
        'computed_at': '2026-04-29T12:05:00Z',
        'telemetry_basis': 'none',
        'telemetry_event_id': None,
        'evidence_source': 'none',
        'metadata': {'source': 'persisted_runtime'},
    }]

    payload = _canonical_runtime_payload(
        runtime_status='degraded',
        evidence_source='none',
        provider_health=[{
            'provider_name': 'rpc',
            'status': 'degraded',
            'provider_type': 'rpc',
            'checked_at': '2026-04-29T12:05:00Z',
            'evidence_source': 'none',
            'metadata': {'error': 'legacy_rows_ignored'},
        }],
        target_coverage=[{
            'target_id': 'target-1',
            'coverage_status': 'none',
            'last_poll_at': '2026-04-29T12:05:00Z',
            'last_heartbeat_at': None,
            'last_telemetry_at': None,
            'last_detection_at': None,
            'computed_at': '2026-04-29T12:05:00Z',
            'evidence_source': 'none',
            'metadata': {'source': 'persisted_runtime'},
        }],
        provider_health_status='degraded',
        target_coverage_status='none',
        status_reason='runtime_status_degraded:legacy_demo_rows_ignored',
        contradiction_flags=['legacy_timeline_rows_present'],
        provider_health_records=persisted_provider_records,
        target_coverage_records=persisted_coverage_records,
    )
    monkeypatch.setattr(api_main, 'monitoring_runtime_status', lambda _request: dict(payload))

    response = client.get('/ops/monitoring/runtime-status', headers={'x-workspace-id': 'ws-1'})
    assert response.status_code == 200
    body = response.json()
    assert body['runtime_status'] != 'healthy'
    assert body['evidence_source'] != 'live'
    assert isinstance(body['provider_health'], list)
    assert isinstance(body['target_coverage'], list)
    assert isinstance(body['provider_health_records'][0], dict)
    assert isinstance(body['target_coverage_records'][0], dict)
    assert isinstance(body['provider_health_records'][0]['metadata'], dict)
    assert isinstance(body['target_coverage_records'][0]['metadata'], dict)
    assert body['provider_health_records'] == persisted_provider_records
    assert body['target_coverage_records'] == persisted_coverage_records
    assert body['provider_health'] == [{
        'provider_name': 'rpc',
        'status': 'degraded',
        'provider_type': 'rpc',
        'checked_at': '2026-04-29T12:05:00Z',
        'evidence_source': 'none',
        'metadata': {'error': 'legacy_rows_ignored'},
    }]
    assert body['target_coverage'] == [{
        'target_id': 'target-1',
        'coverage_status': 'none',
        'last_poll_at': '2026-04-29T12:05:00Z',
        'last_heartbeat_at': None,
        'last_telemetry_at': None,
        'last_detection_at': None,
        'computed_at': '2026-04-29T12:05:00Z',
        'evidence_source': 'none',
        'metadata': {'source': 'persisted_runtime'},
    }]
    assert body['provider_health_status'] == 'degraded'
    assert body['target_coverage_status'] == 'none'
    assert body['target_coverage'][0]['last_telemetry_at'] is None
    assert body['target_coverage'][0]['last_detection_at'] is None


def test_impossible_reporting_coverage_without_telemetry_is_detectable() -> None:
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

    impossible = build_workspace_monitoring_summary(
        **base,
        reporting_systems=1,
        last_poll_at=now,
        last_heartbeat_at=now,
        last_telemetry_at=None,
        last_coverage_telemetry_at=None,
        telemetry_kind=None,
        last_detection_at=None,
        evidence_source='none',
    )
    assert impossible['last_telemetry_at'] is None
    assert impossible['reporting_systems_count'] >= 1
    assert impossible['evidence_source_summary'] == 'none'


def test_simulator_and_replay_evidence_are_never_labeled_live() -> None:
    now = datetime.now(timezone.utc)
    common = dict(
        now=now,
        workspace_configured=True,
        configuration_reason_codes=[],
        query_failure_detected=False,
        schema_drift_detected=False,
        missing_telemetry_only=False,
        monitoring_mode='live',
        runtime_status='degraded',
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
        reporting_systems=1,
        last_poll_at=now,
        last_heartbeat_at=now,
        last_telemetry_at=now,
        last_coverage_telemetry_at=now,
        telemetry_kind='coverage',
        last_detection_at=now,
    )
    simulator = build_workspace_monitoring_summary(**common, evidence_source='simulator')
    replay = build_workspace_monitoring_summary(**common, evidence_source='replay')
    assert simulator['evidence_source_summary'] == 'simulator'
    assert replay['evidence_source_summary'] == 'replay'
    assert simulator['evidence_source_summary'] != 'live'
    assert replay['evidence_source_summary'] != 'live'
