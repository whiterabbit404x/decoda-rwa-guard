from services.api.app.monitoring_runner import _evaluate_enterprise_ready_gate


def test_enterprise_ready_gate_fails_continuity_slo_check():
    payload = _evaluate_enterprise_ready_gate(
        continuity_slo_pass=False,
        telemetry_freshness='fresh',
        ingestion_freshness='fresh',
        detection_pipeline_freshness='fresh',
        proof_chain_status='complete',
        proof_chain_missing_reason_codes=[],
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
        proof_chain_status='complete',
        proof_chain_missing_reason_codes=[],
        active_incidents_count=1,
    )
    assert payload['enterprise_ready_pass'] is False
    assert 'linked_evidence_freshness' in payload['failed_checks']


def test_enterprise_ready_gate_fails_open_proof_chain_gaps_check():
    payload = _evaluate_enterprise_ready_gate(
        continuity_slo_pass=True,
        telemetry_freshness='fresh',
        ingestion_freshness='fresh',
        detection_pipeline_freshness='fresh',
        proof_chain_status='incomplete',
        proof_chain_missing_reason_codes=['alerts_without_detection_evidence'],
        active_incidents_count=1,
    )
    assert payload['enterprise_ready_pass'] is False
    assert 'open_proof_chain_gaps' in payload['failed_checks']


def test_enterprise_ready_gate_fails_live_action_capability_check():
    payload = _evaluate_enterprise_ready_gate(
        continuity_slo_pass=True,
        telemetry_freshness='fresh',
        ingestion_freshness='fresh',
        detection_pipeline_freshness='fresh',
        proof_chain_status='complete',
        proof_chain_missing_reason_codes=[],
        active_incidents_count=0,
    )
    assert payload['enterprise_ready_pass'] is False
    assert 'live_action_capability_available' in payload['failed_checks']


def test_enterprise_ready_gate_passes_all_green_scenario():
    payload = _evaluate_enterprise_ready_gate(
        continuity_slo_pass=True,
        telemetry_freshness='fresh',
        ingestion_freshness='fresh',
        detection_pipeline_freshness='fresh',
        proof_chain_status='complete',
        proof_chain_missing_reason_codes=[],
        active_incidents_count=2,
    )
    assert payload['enterprise_ready_pass'] is True
    assert payload['failed_checks'] == []
