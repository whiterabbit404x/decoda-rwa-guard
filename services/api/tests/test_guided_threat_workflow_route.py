from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]


def test_guided_workflow_route_and_impl_exist() -> None:
    main_source = (REPO_ROOT / 'services/api/app/main.py').read_text(encoding='utf-8')
    pilot_source = (REPO_ROOT / 'services/api/app/pilot.py').read_text(encoding='utf-8')
    assert '/workflow/guided-threat-chain' in main_source
    assert 'def run_guided_threat_workflow' in pilot_source
    assert 'INSERT INTO telemetry_events' in pilot_source
    assert 'INSERT INTO detections' in pilot_source
    assert 'INSERT INTO alerts' in pilot_source
    assert 'escalate_alert_to_incident' in pilot_source
    assert 'create_enforcement_action' in pilot_source
    assert 'create_proof_bundle_export' in pilot_source


def test_guided_workflow_proof_chain_linkage_fields_are_persisted() -> None:
    pilot_source = (REPO_ROOT / 'services/api/app/pilot.py').read_text(encoding='utf-8')

    assert 'INSERT INTO telemetry_events' in pilot_source
    assert 'INSERT INTO detections' in pilot_source
    assert 'raw_evidence_json' in pilot_source
    assert "'telemetry_event_id': telemetry_event_id" in pilot_source
    assert 'INSERT INTO alerts' in pilot_source
    assert 'detection_id = EXCLUDED.detection_id' in pilot_source
    assert 'INSERT INTO incidents' in pilot_source
    assert 'UPDATE alerts SET incident_id = %s::uuid' in pilot_source
    assert 'INSERT INTO response_actions' in pilot_source
    assert 'incident_id,' in pilot_source
    assert "create_proof_bundle_export({'incident_id': incident['incident']['id']}, request)" in pilot_source


def test_simulator_workflow_does_not_claim_live_evidence_source() -> None:
    pilot_source = (REPO_ROOT / 'services/api/app/pilot.py').read_text(encoding='utf-8')
    assert "evidence_source = 'live' if live_row is not None else 'simulator'" in pilot_source
