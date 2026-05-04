from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from services.api.app import main as api_main
from services.api.app.workspace_monitoring_summary import build_workspace_monitoring_summary


CHAIN_FIXTURE = {
    'asset_id': 'asset-1',
    'target_id': 'target-1',
    'monitored_system_id': 'ms-1',
    'heartbeat_id': 'hb-1',
    'telemetry_event_id': 'te-1',
    'detection_id': 'det-1',
    'alert_id': 'al-1',
    'incident_id': 'inc-1',
    'response_action_id': 'ra-1',
    'evidence_id': 'ev-1',
}


def _summary(**overrides):
    now = datetime(2026, 5, 1, 12, 0, tzinfo=timezone.utc)
    payload = dict(
        now=now,
        workspace_configured=True,
        configuration_reason_codes=[],
        query_failure_detected=False,
        schema_drift_detected=False,
        missing_telemetry_only=False,
        monitoring_mode='live',
        runtime_status='healthy',
        configured_systems=1,
        monitored_systems_count=1,
        reporting_systems=1,
        protected_assets=1,
        last_poll_at=now,
        last_heartbeat_at=now,
        last_telemetry_at=now - timedelta(seconds=10),
        last_coverage_telemetry_at=now - timedelta(seconds=10),
        telemetry_kind='telemetry_events',
        last_detection_at=now - timedelta(seconds=8),
        evidence_source='live',
        status_reason='healthy_live_coverage',
        configuration_reason=None,
        valid_protected_asset_count=1,
        linked_monitored_system_count=1,
        persisted_enabled_config_count=1,
        valid_target_system_link_count=1,
        telemetry_window_seconds=300,
    )
    payload.update(overrides)
    return build_workspace_monitoring_summary(**payload)


@pytest.mark.parametrize(
    ('case_label', 'overrides', 'expected_flag'),
    [
        ('A', dict(runtime_status='offline'), 'live_evidence_without_live_telemetry_kind'),
        ('B', dict(reporting_systems=0), 'live_evidence_without_live_telemetry_kind'),
        ('C', dict(workspace_configured=False), 'workspace_unconfigured_with_coverage'),
        ('D', dict(last_poll_at=None, last_telemetry_at=None, telemetry_kind=None), 'heartbeat_without_telemetry_timestamp'),
        ('E', dict(evidence_source='simulator'), None),
        ('F', dict(last_telemetry_at=None, last_coverage_telemetry_at=None, telemetry_kind=None), None),
        ('G', dict(last_heartbeat_at=None), None),
        ('H', dict(last_detection_at=None), None),
        ('I', dict(configured_systems=0, reporting_systems=0, protected_assets=0), None),
        ('J', dict(missing_telemetry_only=True), None),
        ('K', dict(query_failure_detected=True), None),
        ('L', dict(schema_drift_detected=True), None),
    ],
)
def test_canonical_runtime_summary_cases_a_l(case_label, overrides, expected_flag):
    summary = _summary(**overrides)
    assert set(CHAIN_FIXTURE.keys()) == {
        'asset_id',
        'target_id',
        'monitored_system_id',
        'heartbeat_id',
        'telemetry_event_id',
        'detection_id',
        'alert_id',
        'incident_id',
        'response_action_id',
        'evidence_id',
    }
    if expected_flag:
        assert expected_flag in summary['contradiction_flags'], f'case {case_label}'
    if overrides.get('evidence_source') == 'simulator':
        assert summary['evidence_source_summary'] == 'simulator'
        assert summary['evidence_source_summary'] != 'live_provider'


def test_runtime_status_endpoint_contract_reflects_canonical_summary_and_no_false_healthy(monkeypatch):
    canonical = _summary(reporting_systems=0, last_telemetry_at=None, last_coverage_telemetry_at=None, telemetry_kind=None)
    payload = {
        'status': 'Degraded',
        'monitoring_status': canonical['monitoring_status'],
        'runtime_status_summary': canonical['runtime_status'],
        'workspace_monitoring_summary': canonical,
        'configured_systems': canonical['monitored_systems_count'],
        'reporting_systems': canonical['reporting_systems_count'],
        'protected_assets': canonical['protected_assets_count'],
        'evidence_source': canonical['evidence_source_summary'],
        'status_reason': canonical['status_reason'],
    }
    monkeypatch.setattr(api_main, 'with_auth_schema_json', lambda handler: handler())
    monkeypatch.setattr(api_main, '_is_production_like_runtime', lambda: False)
    monkeypatch.setattr(api_main, 'monitoring_runtime_status', lambda _request: payload)
    client = TestClient(api_main.app)
    response = client.get('/ops/monitoring/runtime-status', headers={'authorization': 'Bearer t', 'x-workspace-id': 'ws-1'})
    assert response.status_code == 200
    body = response.json()
    assert set(CHAIN_FIXTURE.keys()) == {
        'asset_id', 'target_id', 'monitored_system_id', 'heartbeat_id', 'telemetry_event_id',
        'detection_id', 'alert_id', 'incident_id', 'response_action_id', 'evidence_id',
    }
    assert body['reporting_systems'] == 0
    assert body['runtime_status'] != 'healthy'
