from __future__ import annotations

from datetime import datetime, timezone

from fastapi.testclient import TestClient

from services.api.app import main as api_main
from services.api.app.workspace_monitoring_summary import build_workspace_monitoring_summary


def _summary_base(now: datetime) -> dict:
    return dict(
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


def test_heartbeat_poll_without_telemetry_keeps_reporting_systems_zero_and_no_telemetry_timestamp() -> None:
    now = datetime.now(timezone.utc)
    payload = build_workspace_monitoring_summary(
        **_summary_base(now),
        reporting_systems=0,
        last_poll_at=now,
        last_heartbeat_at=now,
        last_telemetry_at=None,
        last_coverage_telemetry_at=None,
        telemetry_kind=None,
        last_detection_at=None,
        evidence_source='live',
    )
    assert payload['last_telemetry_at'] is None
    assert payload['reporting_systems_count'] == 0


def test_telemetry_and_detection_timestamps_are_set_only_when_present() -> None:
    now = datetime.now(timezone.utc)
    payload = build_workspace_monitoring_summary(
        **_summary_base(now),
        reporting_systems=1,
        last_poll_at=now,
        last_heartbeat_at=now,
        last_telemetry_at=now,
        last_coverage_telemetry_at=now,
        telemetry_kind='coverage',
        last_detection_at=now,
        evidence_source='live',
    )
    assert payload['last_telemetry_at'] is not None
    assert payload['detection_pipeline_freshness'] in {'fresh', 'stale', 'unavailable', 'missing'}
    assert payload['reporting_systems_count'] >= 1


def test_runtime_status_contract_includes_persisted_provider_and_coverage_records(monkeypatch) -> None:
    client = TestClient(api_main.app)
    monkeypatch.setattr(api_main, 'with_auth_schema_json', lambda handler: handler())
    monkeypatch.setattr(api_main, '_is_production_like_runtime', lambda: True)
    now = '2026-04-29T12:00:00Z'
    monkeypatch.setattr(
        api_main,
        'monitoring_runtime_status',
        lambda _request: {
            'workspace_configured': True,
            'runtime_status': 'degraded',
            'configured_systems': 1,
            'reporting_systems': 0,
            'protected_assets': 1,
            'last_poll_at': now,
            'last_heartbeat_at': now,
            'last_telemetry_at': None,
            'last_detection_at': None,
            'freshness_status': 'stale',
            'confidence_status': 'low',
            'evidence_source': 'none',
            'status_reason': 'no_reporting_systems',
            'contradiction_flags': ['no_reporting_systems'],
            'summary_generated_at': now,
            'provider_health': 'degraded',
            'target_coverage': 'none',
            'provider_health_records': [{'provider_name': 'rpc', 'status': 'degraded', 'observed_at': now}],
            'target_coverage_records': [{'target_id': 'target-1', 'coverage_status': 'none', 'evidence_source': 'none', 'observed_at': now}],
        },
    )

    response = client.get('/ops/monitoring/runtime-status', headers={'x-workspace-id': 'ws-1'})
    assert response.status_code == 200
    body = response.json()
    assert body['provider_health_records'][0]['provider_name'] == 'rpc'
    assert body['target_coverage_records'][0]['target_id'] == 'target-1'
    assert body['runtime_status'] != 'healthy'
