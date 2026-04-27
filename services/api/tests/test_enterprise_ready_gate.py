from services.api.app.monitoring_runner import _evaluate_enterprise_ready_gate


def test_enterprise_ready_gate_fails_continuity_slo_check():
    payload = _evaluate_enterprise_ready_gate(
        continuity_slo_pass=False,
        telemetry_freshness='fresh',
        ingestion_freshness='fresh',
        detection_pipeline_freshness='fresh',
        runtime_status='live',
        monitoring_status='live',
        reporting_systems_count=2,
        monitored_systems_count=2,
        contradiction_flags=[],
        guard_flags=[],
        active_incidents_count=1,
    )
    assert payload['enterprise_ready_pass'] is False
    assert 'continuity_slo_pass' in payload['failed_checks']


def test_enterprise_ready_gate_fails_linked_evidence_freshness_check():
    payload = _evaluate_enterprise_ready_gate(
        continuity_slo_pass=True,
        telemetry_freshness='stale',
        ingestion_freshness='fresh',
        detection_pipeline_freshness='fresh',
        runtime_status='live',
        monitoring_status='live',
        reporting_systems_count=2,
        monitored_systems_count=2,
        contradiction_flags=[],
        guard_flags=[],
        active_incidents_count=1,
    )
    assert payload['enterprise_ready_pass'] is False
    assert 'linked_evidence_freshness' in payload['failed_checks']


def test_enterprise_ready_gate_fails_stable_monitored_system_state_check():
    payload = _evaluate_enterprise_ready_gate(
        continuity_slo_pass=True,
        telemetry_freshness='fresh',
        ingestion_freshness='fresh',
        detection_pipeline_freshness='fresh',
        runtime_status='degraded',
        monitoring_status='limited',
        reporting_systems_count=0,
        monitored_systems_count=2,
        contradiction_flags=['live_monitoring_without_reporting_systems'],
        guard_flags=[],
        active_incidents_count=1,
    )
    assert payload['enterprise_ready_pass'] is False
    assert 'stable_monitored_system_state' in payload['failed_checks']


def test_enterprise_ready_gate_fails_live_action_capability_check():
    payload = _evaluate_enterprise_ready_gate(
        continuity_slo_pass=True,
        telemetry_freshness='fresh',
        ingestion_freshness='fresh',
        detection_pipeline_freshness='fresh',
        runtime_status='live',
        monitoring_status='live',
        reporting_systems_count=2,
        monitored_systems_count=2,
        contradiction_flags=[],
        guard_flags=[],
        active_incidents_count=0,
    )
    assert payload['enterprise_ready_pass'] is False
    assert 'live_action_capability_available' in payload['failed_checks']


def test_enterprise_ready_gate_fails_all_red_scenario():
    payload = _evaluate_enterprise_ready_gate(
        continuity_slo_pass=False,
        telemetry_freshness='stale',
        ingestion_freshness='missing',
        detection_pipeline_freshness='missing',
        runtime_status='offline',
        monitoring_status='offline',
        reporting_systems_count=0,
        monitored_systems_count=0,
        contradiction_flags=['offline_with_current_telemetry'],
        guard_flags=['telemetry_unavailable_with_high_confidence'],
        active_incidents_count=0,
    )
    assert payload['enterprise_ready_pass'] is False
    assert payload['failed_checks'] == [
        'continuity_slo_pass',
        'linked_evidence_freshness',
        'stable_monitored_system_state',
        'live_action_capability_available',
    ]


def test_enterprise_ready_gate_passes_all_green_scenario():
    payload = _evaluate_enterprise_ready_gate(
        continuity_slo_pass=True,
        telemetry_freshness='fresh',
        ingestion_freshness='fresh',
        detection_pipeline_freshness='fresh',
        runtime_status='live',
        monitoring_status='live',
        reporting_systems_count=2,
        monitored_systems_count=2,
        contradiction_flags=[],
        guard_flags=[],
        active_incidents_count=2,
    )
    assert payload['enterprise_ready_pass'] is True
    assert payload['failed_checks'] == []
