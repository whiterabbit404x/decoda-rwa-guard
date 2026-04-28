from __future__ import annotations

from copy import deepcopy

from fastapi.testclient import TestClient

from services.api.app import main as api_main


def _base_summary(runtime_status: str, continuity_pass: bool, continuity_reason_codes: list[str]) -> dict[str, object]:
    return {
        'runtime_status': runtime_status,
        'monitoring_status': runtime_status,
        'continuity_slo_pass': continuity_pass,
        'continuity_reason_codes': list(continuity_reason_codes),
        'heartbeat_age_seconds': 15,
        'telemetry_age_seconds': 20,
        'event_ingestion_age_seconds': 20,
        'detection_age_seconds': 40,
        'detection_pipeline_age_seconds': 40,
        'detection_eval_age_seconds': 40,
        'heartbeat_threshold_seconds': 180,
        'telemetry_threshold_seconds': 300,
        'event_ingestion_threshold_seconds': 300,
        'detection_threshold_seconds': 300,
        'thresholds_seconds': {'heartbeat': 180, 'event_ingestion': 300, 'detection_eval': 300},
        'required_thresholds_seconds': {'heartbeat': 180, 'event_ingestion': 300, 'detection_eval': 300},
        'continuity_thresholds_seconds': {'heartbeat': 180, 'event_ingestion': 300, 'detection_eval': 300},
        'runtime_degraded_reason_codes': ['continuity_slo_failed', *continuity_reason_codes] if not continuity_pass else [],
        'runtime_status_reason_codes': ['continuity_slo_failed', *continuity_reason_codes] if not continuity_pass else [],
    }


def _base_payload(runtime_status: str, continuity_pass: bool, continuity_reason_codes: list[str]) -> dict[str, object]:
    summary = _base_summary(runtime_status, continuity_pass, continuity_reason_codes)
    return {
        'status': runtime_status.capitalize(),
        'monitoring_status': runtime_status,
        'runtime_status_summary': runtime_status,
        'continuity_slo_pass': continuity_pass,
        'continuity_status': 'continuous_live' if runtime_status != 'offline' else 'offline',
        'continuity_reason_codes': list(continuity_reason_codes),
        'workspace_monitoring_summary': summary,
    }


def test_ops_runtime_status_exposes_continuity_fields_for_healthy_stale_degraded_offline(monkeypatch):
    scenarios = {
        'healthy': _base_payload('healthy', True, []),
        'stale': _base_payload('stale', False, ['event_ingestion_stale']),
        'degraded': _base_payload('degraded', False, ['detection_pipeline_stale']),
        'offline': _base_payload('offline', False, ['worker_offline']),
    }

    monkeypatch.setattr(api_main, 'with_auth_schema_json', lambda handler: handler())
    client = TestClient(api_main.app)

    for expected_status, scenario_payload in scenarios.items():
        payload = deepcopy(scenario_payload)
        monkeypatch.setattr(api_main, 'monitoring_runtime_status', lambda _request, payload=payload: payload)
        response = client.get('/ops/monitoring/runtime-status', headers={'authorization': 'Bearer test', 'x-workspace-id': 'ws-1'})
        assert response.status_code == 200
        body = response.json()

        assert body['runtime_status_summary'] == expected_status
        assert body['continuity_slo_pass'] is payload['continuity_slo_pass']
        assert body['continuity_slo']['pass'] is payload['continuity_slo_pass']
        assert body['continuity_reason_codes'] == payload['continuity_reason_codes']
        assert body['continuity_slo']['reason_codes'] == payload['continuity_reason_codes']
        assert body['heartbeat_age_seconds'] == 15
        assert body['telemetry_age_seconds'] == 20
        assert body['event_ingestion_age_seconds'] == 20
        assert body['detection_age_seconds'] == 40
        assert body['detection_pipeline_age_seconds'] == 40
        assert body['heartbeat_threshold_seconds'] == 180
        assert body['telemetry_threshold_seconds'] == 300
        assert body['event_ingestion_threshold_seconds'] == 300
        assert body['detection_threshold_seconds'] == 300

        if payload['continuity_slo_pass'] is False:
            assert body['runtime_degraded_reason_codes'][0] == 'continuity_slo_failed'
            assert body['runtime_status_reason_codes'][0] == 'continuity_slo_failed'
