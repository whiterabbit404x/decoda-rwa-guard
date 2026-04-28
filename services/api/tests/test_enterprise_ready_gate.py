from services.api.app.monitoring_runner import _evaluate_enterprise_ready_gate
from services.api.app import monitoring_runner


def test_enterprise_ready_gate_fails_continuity_slo_check():
    payload = _evaluate_enterprise_ready_gate(
        continuity_slo_pass=False,
        telemetry_freshness='fresh',
        ingestion_freshness='fresh',
        detection_pipeline_freshness='fresh',
        proof_chain_status='complete',
        runtime_status='live',
        monitoring_status='live',
        reporting_systems_count=2,
        monitored_systems_count=2,
        contradiction_flags=[],
        guard_flags=[],
    )
    assert payload['enterprise_ready_pass'] is False
    assert 'continuity_slo_pass' in payload['failed_checks']


def test_enterprise_ready_gate_fails_linked_fresh_evidence_chain_check():
    payload = _evaluate_enterprise_ready_gate(
        continuity_slo_pass=True,
        telemetry_freshness='stale',
        ingestion_freshness='fresh',
        detection_pipeline_freshness='fresh',
        proof_chain_status='incomplete',
        runtime_status='live',
        monitoring_status='live',
        reporting_systems_count=2,
        monitored_systems_count=2,
        contradiction_flags=[],
        guard_flags=[],
    )
    assert payload['enterprise_ready_pass'] is False
    assert 'linked_fresh_evidence' in payload['failed_checks']


def test_enterprise_ready_gate_fails_stable_monitored_systems_check():
    payload = _evaluate_enterprise_ready_gate(
        continuity_slo_pass=True,
        telemetry_freshness='fresh',
        ingestion_freshness='fresh',
        detection_pipeline_freshness='fresh',
        proof_chain_status='complete',
        runtime_status='degraded',
        monitoring_status='limited',
        reporting_systems_count=0,
        monitored_systems_count=2,
        contradiction_flags=['live_monitoring_without_reporting_systems'],
        guard_flags=[],
    )
    assert payload['enterprise_ready_pass'] is False
    assert 'stable_monitored_systems' in payload['failed_checks']


def test_enterprise_ready_gate_fails_live_action_capability_readiness_check(monkeypatch):
    monkeypatch.setattr(
        monitoring_runner,
        'resolve_response_action_capability',
        lambda _action, _mode: {'supports_mode': False, 'live_execution_path': 'unsupported'},
    )
    payload = _evaluate_enterprise_ready_gate(
        continuity_slo_pass=True,
        telemetry_freshness='fresh',
        ingestion_freshness='fresh',
        detection_pipeline_freshness='fresh',
        proof_chain_status='complete',
        runtime_status='live',
        monitoring_status='live',
        reporting_systems_count=2,
        monitored_systems_count=2,
        contradiction_flags=[],
        guard_flags=[],
    )
    assert payload['enterprise_ready_pass'] is False
    assert 'live_action_capability_readiness' in payload['failed_checks']


def test_enterprise_ready_gate_fails_all_red_scenario(monkeypatch):
    monkeypatch.setattr(
        monitoring_runner,
        'resolve_response_action_capability',
        lambda _action, _mode: {'supports_mode': False, 'live_execution_path': 'unsupported'},
    )
    payload = _evaluate_enterprise_ready_gate(
        continuity_slo_pass=False,
        telemetry_freshness='stale',
        ingestion_freshness='missing',
        detection_pipeline_freshness='missing',
        proof_chain_status='incomplete',
        runtime_status='offline',
        monitoring_status='offline',
        reporting_systems_count=0,
        monitored_systems_count=0,
        contradiction_flags=['offline_with_current_telemetry'],
        guard_flags=['telemetry_unavailable_with_high_confidence'],
    )
    assert payload['enterprise_ready_pass'] is False
    assert payload['failed_checks'] == [
        'continuity_slo_pass',
        'linked_fresh_evidence',
        'stable_monitored_systems',
        'live_action_capability_readiness',
    ]
    assert payload['check_results'] == [
        {'name': 'continuity_slo_pass', 'pass': False, 'remediation_url': '/threat#continuity-slo'},
        {'name': 'linked_fresh_evidence', 'pass': False, 'remediation_url': '/threat#telemetry-freshness'},
        {'name': 'stable_monitored_systems', 'pass': False, 'remediation_url': '/threat#monitored-system-state'},
        {'name': 'live_action_capability_readiness', 'pass': False, 'remediation_url': '/threat#response-actions'},
    ]
    assert payload['enterprise_criteria_pass'] is False
    assert payload['enterprise_criteria_failed'] == [
        'criterion_b_continuity_slos',
        'criterion_c_reconcile_stability',
        'criterion_d_evidence_chain_hydration',
        'criterion_e_live_action_governance',
        'criterion_f_state_model_ux',
        'hidden_architecture',
    ]


def test_enterprise_ready_gate_passes_all_green_scenario(monkeypatch):
    monkeypatch.setattr(
        monitoring_runner,
        'resolve_response_action_capability',
        lambda _action, _mode: {'supports_mode': True, 'live_execution_path': 'governance'},
    )
    payload = _evaluate_enterprise_ready_gate(
        continuity_slo_pass=True,
        telemetry_freshness='fresh',
        ingestion_freshness='fresh',
        detection_pipeline_freshness='fresh',
        proof_chain_status='complete',
        runtime_status='live',
        monitoring_status='live',
        reporting_systems_count=2,
        monitored_systems_count=2,
        contradiction_flags=[],
        guard_flags=[],
    )
    assert payload['enterprise_ready_pass'] is True
    assert payload['failed_checks'] == []
    assert payload['validated_live_action_paths'] != []
    assert [check['name'] for check in payload['check_results']] == [
        'continuity_slo_pass',
        'linked_fresh_evidence',
        'stable_monitored_systems',
        'live_action_capability_readiness',
    ]
    assert all(check['pass'] is True for check in payload['check_results'])
    assert payload['enterprise_criteria_pass'] is True
    assert payload['enterprise_criteria_failed'] == []
    assert [check['name'] for check in payload['enterprise_criteria']] == [
        'criterion_b_continuity_slos',
        'criterion_c_reconcile_stability',
        'criterion_d_evidence_chain_hydration',
        'criterion_e_live_action_governance',
        'criterion_f_state_model_ux',
        'hidden_architecture',
    ]
    assert all(check['requires_measurable_evidence'] is True for check in payload['enterprise_criteria'])
    assert all(check['pass'] is True for check in payload['enterprise_criteria'])
