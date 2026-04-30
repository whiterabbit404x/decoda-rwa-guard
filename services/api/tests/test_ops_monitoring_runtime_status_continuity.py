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
        'worker_heartbeat_age_seconds': 15,
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
        'runtime_degraded_reason_codes': (
            ['runtime_status_degraded', 'live_mode_continuity_failed', 'continuity_slo_failed', *continuity_reason_codes]
            if not continuity_pass
            else []
        ),
        'runtime_status_reason_codes': (
            ['runtime_status_degraded', 'live_mode_continuity_failed', 'continuity_slo_failed', *continuity_reason_codes]
            if not continuity_pass
            else []
        ),
        'continuity_freshness_ages_seconds': {'heartbeat': 15, 'telemetry': 20, 'event_ingestion': 20, 'detection_eval': 40},
        'continuity_configured_thresholds_seconds': {'heartbeat': 180, 'event_ingestion': 300, 'detection_eval': 300},
        'continuity_failed_checks': list(continuity_reason_codes),
        'continuity_breach_reasons': (
            [{'code': continuity_reason_codes[0], 'check': 'telemetry_freshness', 'state': 'stale', 'age_seconds': 900, 'threshold_seconds': 300}]
            if continuity_reason_codes
            else []
        ),
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


def _enterprise_gate_payload(*, enterprise_ready_pass: bool, failed_checks: list[str]) -> dict[str, object]:
    base = _base_payload('live', enterprise_ready_pass, [])
    checks = [
        {'name': 'continuity_slo_pass', 'pass': 'continuity_slo_pass' not in failed_checks, 'remediation_url': '/threat#continuity-slo'},
        {'name': 'linked_fresh_evidence', 'pass': 'linked_fresh_evidence' not in failed_checks, 'remediation_url': '/threat#telemetry-freshness'},
        {'name': 'stable_monitored_systems', 'pass': 'stable_monitored_systems' not in failed_checks, 'remediation_url': '/threat#monitored-system-state'},
        {'name': 'live_action_capability_readiness', 'pass': 'live_action_capability_readiness' not in failed_checks, 'remediation_url': '/threat#response-actions'},
    ]
    base.update(
        {
            'enterprise_ready_pass': enterprise_ready_pass,
            'failed_checks': list(failed_checks),
            'check_results': checks,
            'remediation_links': {
                'continuity_slo_pass': '/threat#continuity-slo',
                'linked_fresh_evidence': '/threat#telemetry-freshness',
                'stable_monitored_systems': '/threat#monitored-system-state',
                'live_action_capability_readiness': '/threat#response-actions',
            },
        }
    )
    summary = dict(base['workspace_monitoring_summary'])
    summary.update(
        {
            'enterprise_ready_pass': enterprise_ready_pass,
            'failed_checks': list(failed_checks),
            'check_results': checks,
            'remediation_links': base['remediation_links'],
        }
    )
    base['workspace_monitoring_summary'] = summary
    return base


def test_ops_runtime_status_exposes_continuity_fields_for_healthy_stale_degraded_offline(monkeypatch):
    scenarios = {
        'healthy': _base_payload('healthy', True, []),
        'stale': _base_payload('stale', False, ['event_ingestion_stale']),
        'degraded': _base_payload('degraded', False, ['detection_pipeline_stale']),
        'offline': _base_payload('offline', False, ['worker_offline']),
    }

    monkeypatch.setattr(api_main, 'with_auth_schema_json', lambda handler: handler())
    monkeypatch.setattr(api_main, '_is_production_like_runtime', lambda: False)
    monkeypatch.setenv('MONITORING_RUNTIME_LEGACY_FIELDS', '1')
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
        assert body['continuity_slo']['worker_heartbeat_age_seconds'] == 15
        assert body['continuity_reason_codes'] == payload['continuity_reason_codes']
        assert body['continuity_slo']['reason_codes'] == payload['continuity_reason_codes']
        assert body['continuity_slo']['failed_checks'] == payload['continuity_reason_codes']
        assert isinstance(body['continuity'], dict)
        assert body['continuity']['status'] == body['continuity_status']
        assert body['continuity']['slo']['pass'] is payload['continuity_slo_pass']
        assert body['continuity']['freshness_ages_seconds'] == payload['workspace_monitoring_summary']['continuity_freshness_ages_seconds']
        assert body['continuity']['configured_thresholds_seconds'] == payload['workspace_monitoring_summary']['continuity_configured_thresholds_seconds']
        assert body['continuity']['breach_reasons'] == payload['workspace_monitoring_summary']['continuity_breach_reasons']
        assert body['heartbeat_age_seconds'] == 15
        assert body['telemetry_age_seconds'] == 20
        assert body['event_ingestion_age_seconds'] == 20
        assert body['worker_heartbeat_age_seconds'] == 15
        assert body['detection_age_seconds'] == 40
        assert body['detection_pipeline_age_seconds'] == 40
        assert body['continuity_failed_checks'] == payload['continuity_reason_codes']
        assert body['continuity']['failed_checks'] == payload['continuity_reason_codes']
        assert body['heartbeat_threshold_seconds'] == 180
        assert body['telemetry_threshold_seconds'] == 300
        assert body['event_ingestion_threshold_seconds'] == 300
        assert body['detection_threshold_seconds'] == 300

        if payload['continuity_slo_pass'] is False:
            assert body['runtime_degraded_reason_codes'][:3] == [
                'runtime_status_degraded',
                'live_mode_continuity_failed',
                'continuity_slo_failed',
            ]
            assert body['runtime_status_reason_codes'][:3] == [
                'runtime_status_degraded',
                'live_mode_continuity_failed',
                'continuity_slo_failed',
            ]


def test_ops_runtime_status_exposes_enterprise_ready_gate_all_green(monkeypatch):
    payload = _enterprise_gate_payload(enterprise_ready_pass=True, failed_checks=[])
    monkeypatch.setattr(api_main, 'with_auth_schema_json', lambda handler: handler())
    monkeypatch.setattr(api_main, '_is_production_like_runtime', lambda: False)
    monkeypatch.setenv('MONITORING_RUNTIME_LEGACY_FIELDS', '1')
    monkeypatch.setattr(api_main, 'monitoring_runtime_status', lambda _request: payload)
    client = TestClient(api_main.app)
    response = client.get('/ops/monitoring/runtime-status', headers={'authorization': 'Bearer test', 'x-workspace-id': 'ws-1'})
    assert response.status_code == 200
    body = response.json()
    assert body['enterprise_ready_pass'] is True
    assert body['failed_checks'] == []
    assert [check['name'] for check in body['check_results']] == [
        'continuity_slo_pass',
        'linked_fresh_evidence',
        'stable_monitored_systems',
        'live_action_capability_readiness',
    ]
    assert all(check['pass'] is True for check in body['check_results'])


def test_ops_runtime_status_exposes_enterprise_ready_gate_all_red(monkeypatch):
    failed_checks = [
        'continuity_slo_pass',
        'linked_fresh_evidence',
        'stable_monitored_systems',
        'live_action_capability_readiness',
    ]
    payload = _enterprise_gate_payload(enterprise_ready_pass=False, failed_checks=failed_checks)
    monkeypatch.setattr(api_main, 'with_auth_schema_json', lambda handler: handler())
    monkeypatch.setattr(api_main, '_is_production_like_runtime', lambda: False)
    monkeypatch.setenv('MONITORING_RUNTIME_LEGACY_FIELDS', '1')
    monkeypatch.setattr(api_main, 'monitoring_runtime_status', lambda _request: payload)
    client = TestClient(api_main.app)
    response = client.get('/ops/monitoring/runtime-status', headers={'authorization': 'Bearer test', 'x-workspace-id': 'ws-1'})
    assert response.status_code == 200
    body = response.json()
    assert body['enterprise_ready_pass'] is False
    assert body['failed_checks'] == failed_checks
    assert [check['name'] for check in body['check_results']] == failed_checks
    assert all(check['pass'] is False for check in body['check_results'])


def test_ops_runtime_status_canonical_contract_includes_provider_and_target_coverage(monkeypatch):
    payload = _base_payload('healthy', True, [])
    payload.update(
        {
            'workspace_configured': True,
            'configured_systems': 2,
            'reporting_systems': 1,
            'protected_assets': 1,
            'last_poll_at': '2026-04-29T00:00:00Z',
            'last_heartbeat_at': '2026-04-29T00:01:00Z',
            'last_telemetry_at': '2026-04-29T00:02:00Z',
            'last_detection_at': '2026-04-29T00:03:00Z',
            'freshness_status': 'fresh',
            'confidence_status': 'high',
            'evidence_source': 'live',
            'status_reason': 'healthy_live_coverage',
            'contradiction_flags': [],
            'provider_health': [{'provider_name': 'rpc', 'status': 'healthy'}],
            'target_coverage': [{'target_id': 'target-1', 'coverage_status': 'reporting'}],
        }
    )
    monkeypatch.setattr(api_main, 'with_auth_schema_json', lambda handler: handler())
    monkeypatch.setattr(api_main, 'monitoring_runtime_status', lambda _request: payload)
    monkeypatch.setattr(api_main, '_is_production_like_runtime', lambda: True)
    client = TestClient(api_main.app)
    response = client.get('/ops/monitoring/runtime-status', headers={'authorization': 'Bearer test', 'x-workspace-id': 'ws-1'})
    assert response.status_code == 200
    body = response.json()
    assert sorted(body.keys()) == sorted(
        [
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
    )
    assert body['provider_health'] == [{'provider_name': 'rpc', 'status': 'healthy'}]
    assert body['target_coverage'] == [{'target_id': 'target-1', 'coverage_status': 'reporting'}]
    assert body['provider_health_status'] == 'unknown'
    assert body['target_coverage_status'] == 'unknown'


def test_ops_runtime_status_production_ignores_legacy_field_flag(monkeypatch):
    payload = _base_payload('healthy', True, [])
    payload.update({'provider_health': [{'provider_name': 'rpc', 'status': 'degraded'}], 'target_coverage': [{'target_id': 'target-1', 'coverage_status': 'none'}], 'provider_health_status': 'degraded', 'target_coverage_status': 'none'})
    monkeypatch.setattr(api_main, 'with_auth_schema_json', lambda handler: handler())
    monkeypatch.setattr(api_main, 'monitoring_runtime_status', lambda _request: payload)
    monkeypatch.setattr(api_main, '_is_production_like_runtime', lambda: True)
    monkeypatch.setenv('MONITORING_RUNTIME_LEGACY_FIELDS', '1')
    client = TestClient(api_main.app)
    response = client.get('/ops/monitoring/runtime-status', headers={'authorization': 'Bearer test', 'x-workspace-id': 'ws-1'})
    assert response.status_code == 200
    body = response.json()
    assert 'canonical_monitoring_runtime' not in body
    assert body['provider_health'] == [{'provider_name': 'rpc', 'status': 'degraded'}]
    assert body['target_coverage'] == [{'target_id': 'target-1', 'coverage_status': 'none'}]
    assert body['provider_health_status'] == 'degraded'
    assert body['target_coverage_status'] == 'none'
