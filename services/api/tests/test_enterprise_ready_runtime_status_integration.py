from fastapi.testclient import TestClient

from services.api.app import main as api_main


def test_runtime_status_enterprise_ready_gate_all_green(monkeypatch):
    client = TestClient(api_main.app)
    monkeypatch.setattr(api_main, 'with_auth_schema_json', lambda handler: handler())
    monkeypatch.setattr(
        api_main,
        'monitoring_runtime_status',
        lambda _request: {
            'workspace_monitoring_summary': {
                'enterprise_ready_pass': True,
                'failed_checks': [],
                'check_results': [
                    {'name': 'continuity_slo_pass', 'pass': True, 'remediation_url': '/threat#continuity-slo'},
                    {'name': 'linked_fresh_evidence', 'pass': True, 'remediation_url': '/threat#telemetry-freshness'},
                    {'name': 'stable_monitored_systems', 'pass': True, 'remediation_url': '/threat#monitored-system-state'},
                    {'name': 'live_action_capability_readiness', 'pass': True, 'remediation_url': '/threat#response-actions'},
                ],
                'remediation_links': {},
                'continuity_slo_pass': True,
                'runtime_status': 'live',
                'monitoring_status': 'live',
            },
            'enterprise_ready_pass': True,
            'failed_checks': [],
            'check_results': [
                {'name': 'continuity_slo_pass', 'pass': True, 'remediation_url': '/threat#continuity-slo'},
                {'name': 'linked_fresh_evidence', 'pass': True, 'remediation_url': '/threat#telemetry-freshness'},
                {'name': 'stable_monitored_systems', 'pass': True, 'remediation_url': '/threat#monitored-system-state'},
                {'name': 'live_action_capability_readiness', 'pass': True, 'remediation_url': '/threat#response-actions'},
            ],
            'remediation_links': {},
            'continuity_slo_pass': True,
            'runtime_status': 'live',
            'monitoring_status': 'live',
        },
    )
    response = client.get('/ops/monitoring/runtime-status')
    assert response.status_code == 200
    payload = response.json()
    assert payload['enterprise_ready_pass'] is True
    assert payload['failed_checks'] == []
    assert all(item['pass'] is True for item in payload['check_results'])


def test_runtime_status_enterprise_ready_gate_all_red(monkeypatch):
    client = TestClient(api_main.app)
    monkeypatch.setattr(api_main, 'with_auth_schema_json', lambda handler: handler())
    monkeypatch.setattr(
        api_main,
        'monitoring_runtime_status',
        lambda _request: {
            'workspace_monitoring_summary': {
                'enterprise_ready_pass': False,
                'failed_checks': [
                    'continuity_slo_pass',
                    'linked_fresh_evidence_chain',
                    'stable_monitored_systems',
                    'live_action_capability_readiness',
                ],
                'check_results': [
                    {'name': 'continuity_slo_pass', 'pass': False, 'remediation_url': '/threat#continuity-slo'},
                    {'name': 'linked_fresh_evidence_chain', 'pass': False, 'remediation_url': '/threat#telemetry-freshness'},
                    {'name': 'stable_monitored_systems', 'pass': False, 'remediation_url': '/threat#monitored-system-state'},
                    {'name': 'live_action_capability_readiness', 'pass': False, 'remediation_url': '/threat#response-actions'},
                ],
                'remediation_links': {
                    'linked_fresh_evidence_chain': '/threat#telemetry-freshness',
                    'continuity_slo_pass': '/threat#continuity-slo',
                    'stable_monitored_systems': '/threat#monitored-system-state',
                    'live_action_capability_readiness': '/threat#response-actions',
                },
                'continuity_slo_pass': False,
                'runtime_status': 'offline',
                'monitoring_status': 'offline',
            },
            'enterprise_ready_pass': False,
            'failed_checks': [
                'continuity_slo_pass',
                'linked_fresh_evidence_chain',
                'stable_monitored_systems',
                'live_action_capability_readiness',
            ],
            'check_results': [
                {'name': 'continuity_slo_pass', 'pass': False, 'remediation_url': '/threat#continuity-slo'},
                {'name': 'linked_fresh_evidence_chain', 'pass': False, 'remediation_url': '/threat#telemetry-freshness'},
                {'name': 'stable_monitored_systems', 'pass': False, 'remediation_url': '/threat#monitored-system-state'},
                {'name': 'live_action_capability_readiness', 'pass': False, 'remediation_url': '/threat#response-actions'},
            ],
            'remediation_links': {
                'linked_fresh_evidence_chain': '/threat#telemetry-freshness',
                'continuity_slo_pass': '/threat#continuity-slo',
                'stable_monitored_systems': '/threat#monitored-system-state',
                'live_action_capability_readiness': '/threat#response-actions',
            },
            'continuity_slo_pass': False,
            'runtime_status': 'offline',
            'monitoring_status': 'offline',
        },
    )
    response = client.get('/ops/monitoring/runtime-status')
    assert response.status_code == 200
    payload = response.json()
    assert payload['enterprise_ready_pass'] is False
    assert payload['failed_checks'] == [
        'continuity_slo_pass',
        'linked_fresh_evidence',
        'stable_monitored_systems',
        'live_action_capability_readiness',
    ]
    assert [item['name'] for item in payload['check_results']] == payload['failed_checks']
    assert payload['remediation_links']['linked_fresh_evidence'] == '/threat#telemetry-freshness'
