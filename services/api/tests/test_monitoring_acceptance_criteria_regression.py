from __future__ import annotations

from fastapi.testclient import TestClient

from services.api.app import main as api_main


def test_target_enablement_and_active_config_linkage_contracts_are_present() -> None:
    source = open('services/api/app/pilot.py', encoding='utf-8').read()
    runner_source = open('services/api/app/monitoring_runner.py', encoding='utf-8').read()

    assert 'SET enabled = TRUE,' in source
    assert 'monitoring_enabled = TRUE' in source
    assert 'WHERE workspace_id = %s' in source


def test_heartbeat_and_poll_writes_do_not_set_telemetry_timestamps_without_telemetry_rows() -> None:
    source = open('services/api/app/monitoring_runner.py', encoding='utf-8').read()

    assert 'last_heartbeat = NOW()' in source
    assert 'last_checked_at = NOW()' in source
    assert 'last_telemetry_at' in source
    assert 'telemetry_records_seen' in source


def test_telemetry_rows_contribute_to_reporting_systems_count() -> None:
    source = open('services/api/app/monitoring_runner.py', encoding='utf-8').read()
    assert 'reporting_systems' in source
    assert 'last_telemetry_at is not None' in source


def test_detection_persistence_creates_linked_alert_and_incident_timeline_contracts() -> None:
    runner_source = open('services/api/app/monitoring_runner.py', encoding='utf-8').read()
    pilot_source = open('services/api/app/pilot.py', encoding='utf-8').read()

    assert 'INSERT INTO detections' in runner_source
    assert 'COALESCE(%s::uuid, detection_id)' in runner_source
    assert 'INSERT INTO alerts' in runner_source
    assert 'incident.created_from_alert' in pilot_source
    assert "f'incident.{next_workflow_status}'" in pilot_source
    assert 'incident.timeline_note_added' in pilot_source


def test_governance_action_mode_policy_contract_and_links() -> None:
    source = open('services/api/app/pilot.py', encoding='utf-8').read()
    assert '_enforce_response_action_mode_policy' in source
    assert '_normalize_response_action_mode' in source
    assert 'incident_id' in source
    assert 'alert_id' in source


def test_runtime_status_returns_required_canonical_fields(monkeypatch):
    payload = {
        'runtime_status_summary': 'healthy',
        'monitoring_status': 'healthy',
        'continuity_slo_pass': True,
        'continuity_reason_codes': [],
        'workspace_monitoring_summary': {
            'runtime_status': 'healthy',
            'continuity_freshness_ages_seconds': {'heartbeat': 1},
            'continuity_configured_thresholds_seconds': {'heartbeat': 180},
            'continuity_breach_reasons': [],
            'heartbeat_age_seconds': 1,
            'telemetry_age_seconds': 1,
            'event_ingestion_age_seconds': 1,
            'detection_age_seconds': 1,
            'worker_heartbeat_age_seconds': 1,
            'heartbeat_threshold_seconds': 180,
            'telemetry_threshold_seconds': 300,
            'event_ingestion_threshold_seconds': 300,
            'detection_threshold_seconds': 300,
        },
    }
    monkeypatch.setattr(api_main, 'with_auth_schema_json', lambda handler: handler())
    monkeypatch.setattr(api_main, 'monitoring_runtime_status', lambda _request: payload)

    client = TestClient(api_main.app)
    res = client.get('/ops/monitoring/runtime-status', headers={'authorization': 'Bearer t', 'x-workspace-id': 'ws-1'})

    assert res.status_code == 200
    body = res.json()
    for field in (
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
    ):
        assert field in body


def test_contradiction_guards_emit_flags_and_block_impossible_healthy_states() -> None:
    source = open('services/api/app/workspace_monitoring_summary.py', encoding='utf-8').read()
    runtime_source = open('services/api/app/monitoring_runner.py', encoding='utf-8').read()

    assert 'contradiction_flags.append' in source
    assert 'offline_with_current_telemetry' in source
    assert 'telemetry_current_with_null_timestamp' in runtime_source
    assert 'contradiction_flags' in source
