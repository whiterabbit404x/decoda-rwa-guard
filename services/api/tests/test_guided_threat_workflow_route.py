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
    assert 'create_proof_bundle_export(' in pilot_source
    assert "'detection_id': detection_id" in pilot_source
    assert "'alert_id': alert_id" in pilot_source
    assert "'incident_id': incident['incident']['id']" in pilot_source
    assert "'response_action_id': executed_action['action']['id']" in pilot_source


def test_guided_workflow_builds_telemetry_detection_alert_incident_response_evidence_chain() -> None:
    pilot_source = (REPO_ROOT / 'services/api/app/pilot.py').read_text(encoding='utf-8')

    assert "'first_telemetry_ingestion': {'status': 'ingested'" in pilot_source
    assert "'rule_evaluation_detection_creation': {'status': 'created'" in pilot_source
    assert "'alert_creation': {'status': 'created'" in pilot_source
    assert "'incident_creation': {'status': 'created'" in pilot_source
    assert "'response_action_recommendation_execution': {'status': executed_action['action']['status']" in pilot_source
    assert "'evidence_package_generation_export': {'status': evidence_export.get('status')" in pilot_source


def test_simulator_workflow_does_not_claim_live_evidence_source() -> None:
    pilot_source = (REPO_ROOT / 'services/api/app/pilot.py').read_text(encoding='utf-8')
    assert "raw_evidence_source = 'guided_simulator'" in pilot_source
    assert "evidence_source = canonicalize_evidence_source(raw_evidence_source)" in pilot_source
    assert "raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='live mode requires live_provider ingestion provenance.')" in pilot_source



def test_guided_workflow_creates_workspace_scoped_asset_monitoring_source_and_telemetry() -> None:
    pilot_source = (REPO_ROOT / 'services/api/app/pilot.py').read_text(encoding='utf-8')

    assert "asset = create_asset(" in pilot_source
    assert "workspace_id=workspace_context['workspace_id']" in pilot_source
    assert "target = create_target(" in pilot_source
    assert "'monitoring_source_creation': {'status': 'created', 'target_id': target['target']['id']}" in pilot_source
    assert 'INSERT INTO telemetry_events' in pilot_source
    assert "'first_telemetry_ingestion': {'status': 'ingested'" in pilot_source


def test_guided_workflow_chain_references_ids_across_detection_alert_incident_response_and_evidence() -> None:
    pilot_source = (REPO_ROOT / 'services/api/app/pilot.py').read_text(encoding='utf-8')

    assert "'telemetry_event_id': telemetry_event_id" in pilot_source
    assert "'detection_id': detection_id" in pilot_source
    assert "'alert_id': alert_id" in pilot_source
    assert "'incident_id': incident['incident']['id']" in pilot_source
    assert "'response_action_id': executed_action['action']['id']" in pilot_source
    assert "'evidence_package_id': str(evidence_package_id or '')" in pilot_source


def test_guided_workflow_evidence_export_has_controlled_pilot_schema() -> None:
    pilot_source = (REPO_ROOT / 'services/api/app/pilot.py').read_text(encoding='utf-8')

    assert "'evidence.json': {" in pilot_source
    assert "'mode': 'controlled_pilot'" in pilot_source
    assert "'evidence_source': 'simulator'" in pilot_source
    assert "'chain': {" in pilot_source
    assert "'monitoring_run_id': str(chain_ids.get('monitoring_run_id') or '')" in pilot_source
    assert "'telemetry_event_id': str(chain_ids.get('telemetry_event_id') or '')" in pilot_source
    assert "'detection_id': str(chain_ids.get('detection_id') or '')" in pilot_source
    assert "'alert_id': str(chain_ids.get('alert_id') or '')" in pilot_source
    assert "'incident_id': str(chain_ids.get('incident_id') or '')" in pilot_source
    assert "'response_action_id': str(chain_ids.get('response_action_id') or '')" in pilot_source
    assert "'evidence_package_id': str(chain_ids.get('evidence_package_id') or '')" in pilot_source
    assert "'assertions': {" in pilot_source
    assert "'evidence_package_exported': bool(chain_ids.get('evidence_package_id'))" in pilot_source


def test_guided_workflow_evidence_export_keeps_export_jobs_rows_separate() -> None:
    pilot_source = (REPO_ROOT / 'services/api/app/pilot.py').read_text(encoding='utf-8')

    assert "'evidence_package_rows.json': [" in pilot_source
    assert "FROM export_jobs" in pilot_source
    assert "f\"- evidence_package_rows: {len(datasets['evidence_package_rows.json'])}\"" in pilot_source


def test_guided_workflow_live_mode_uses_live_provider_only_with_live_provenance() -> None:
    pilot_source = (REPO_ROOT / 'services/api/app/pilot.py').read_text(encoding='utf-8')
    assert "if requested_live and not has_live_provenance:" in pilot_source
    assert "raw_evidence_source = 'live_provider'" in pilot_source


def test_guided_workflow_export_uses_canonical_evidence_source_labels() -> None:
    pilot_source = (REPO_ROOT / 'services/api/app/pilot.py').read_text(encoding='utf-8')
    assert "'telemetry_evidence_source': 'simulator'" in pilot_source
    assert 'This proof uses simulator evidence and does not claim live provider monitoring.' in pilot_source
    assert "def canonicalize_evidence_source" in pilot_source
