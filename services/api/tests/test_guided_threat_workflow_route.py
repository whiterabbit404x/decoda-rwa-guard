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
