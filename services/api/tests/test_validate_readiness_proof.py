from __future__ import annotations

import json

from services.api.scripts import validate_readiness_proof


def _write_chain_artifacts(base, *, telemetry_source: str = 'guided_simulator') -> None:
    summary = {
        'live_successful_monitoring_demo': True,
        'simulator_successful_monitoring_demo': True,
        'telemetry_event_present': True,
        'detection_generated_from_telemetry': True,
        'alert_generated_from_detection': True,
        'incident_opened_from_alert': True,
        'response_action_recommended_or_executed': True,
        'evidence_package_exported': True,
        'billing_email_provider_checks_passing': True,
        'controlled_pilot_ready': True,
        'broad_self_serve_ready': True,
        'broad_self_serve_blocked_reason': None,
        'enterprise_procurement_ready': True,
        'onboarding_to_first_signal_complete': True,
        'production_validation_proof_bundle_complete': True,
        'telemetry_evidence_source': telemetry_source,
        'claim_ineligibility_reasons': [],
    }
    (base / 'summary.json').write_text(json.dumps(summary), encoding='utf-8')
    (base / 'telemetry_events.json').write_text(json.dumps([{'id': 'te-1'}]), encoding='utf-8')
    (base / 'detections.json').write_text(json.dumps([{'id': 'det-1', 'telemetry_event_id': 'te-1'}]), encoding='utf-8')
    (base / 'alerts.json').write_text(json.dumps([{'id': 'al-1', 'detection_id': 'det-1'}]), encoding='utf-8')
    (base / 'incidents.json').write_text(json.dumps([{'id': 'inc-1', 'alert_id': 'al-1'}]), encoding='utf-8')
    (base / 'response_actions.json').write_text(json.dumps([{'id': 'ra-1', 'incident_id': 'inc-1'}]), encoding='utf-8')
    (base / 'runs.json').write_text(json.dumps([{'id': 'run-1'}]), encoding='utf-8')
    (base / 'evidence.json').write_text(json.dumps({
        'workspace_id': 'ws-1',
        'evidence_source': telemetry_source,
        'chain': {
            'asset_id': 'asset-1',
            'target_id': 'target-1',
            'monitoring_config_id': 'cfg-1',
            'monitoring_run_id': 'run-1',
            'telemetry_event_id': 'te-1',
            'detection_id': 'det-1',
            'alert_id': 'al-1',
            'incident_id': 'inc-1',
            'response_action_id': 'ra-1',
            'evidence_package_id': 'pkg-1',
        },
        'assertions': {
            'telemetry_linked': True,
            'detection_linked': True,
            'alert_linked': True,
            'incident_linked': True,
            'response_linked': True,
        },
    }), encoding='utf-8')


def test_validator_requires_all_summary_readiness_fields(tmp_path, monkeypatch) -> None:
    _write_chain_artifacts(tmp_path)
    summary = json.loads((tmp_path / 'summary.json').read_text(encoding='utf-8'))
    summary.pop('onboarding_to_first_signal_complete')
    (tmp_path / 'summary.json').write_text(json.dumps(summary), encoding='utf-8')

    monkeypatch.setattr('sys.argv', ['validate_readiness_proof.py', '--summary-path', str(tmp_path / 'summary.json')])
    assert validate_readiness_proof.main() == 2


def test_validator_fails_on_empty_artifacts_and_simulator_mislabeled_live(tmp_path, monkeypatch) -> None:
    _write_chain_artifacts(tmp_path, telemetry_source='live')
    (tmp_path / 'runs.json').write_text('[]', encoding='utf-8')

    monkeypatch.setattr('sys.argv', ['validate_readiness_proof.py', '--summary-path', str(tmp_path / 'summary.json')])
    assert validate_readiness_proof.main() == 2


def test_validator_blocks_broad_self_serve_when_billing_email_provider_checks_fail(tmp_path, monkeypatch) -> None:
    _write_chain_artifacts(tmp_path)
    summary = json.loads((tmp_path / 'summary.json').read_text(encoding='utf-8'))
    summary['billing_email_provider_checks_passing'] = False
    summary['broad_self_serve_ready'] = True
    summary['claim_ineligibility_reasons'] = ['billing_runtime_unavailable', 'email_not_verified', 'provider_dependencies_unhealthy']
    (tmp_path / 'summary.json').write_text(json.dumps(summary), encoding='utf-8')

    monkeypatch.setattr('sys.argv', ['validate_readiness_proof.py', '--summary-path', str(tmp_path / 'summary.json')])
    assert validate_readiness_proof.main() == 2


def test_validator_passes_controlled_pilot_with_full_guided_simulator_chain(tmp_path, monkeypatch) -> None:
    _write_chain_artifacts(tmp_path, telemetry_source='guided_simulator')

    monkeypatch.setattr('sys.argv', ['validate_readiness_proof.py', '--summary-path', str(tmp_path / 'summary.json'), '--environment', 'test'])
    assert validate_readiness_proof.main() == 0


def test_validator_fails_when_proof_bundle_artifacts_are_empty(tmp_path, monkeypatch) -> None:
    _write_chain_artifacts(tmp_path)
    for filename in ('alerts.json', 'incidents.json', 'runs.json'):
        (tmp_path / filename).write_text('[]', encoding='utf-8')
    summary = json.loads((tmp_path / 'summary.json').read_text(encoding='utf-8'))
    summary['production_validation_proof_bundle_complete'] = False
    (tmp_path / 'summary.json').write_text(json.dumps(summary), encoding='utf-8')

    monkeypatch.setattr('sys.argv', ['validate_readiness_proof.py', '--summary-path', str(tmp_path / 'summary.json')])
    assert validate_readiness_proof.main() == 2


def test_validator_rejects_partial_top_level_evidence_refs_without_chain_structure(tmp_path, monkeypatch) -> None:
    _write_chain_artifacts(tmp_path)
    (tmp_path / 'evidence.json').write_text(json.dumps({
        'workspace_id': 'ws-1',
        'evidence_source': 'guided_simulator',
        'telemetry_event_id': 'te-1',
        'detection_id': 'det-1',
    }), encoding='utf-8')

    monkeypatch.setattr('sys.argv', ['validate_readiness_proof.py', '--summary-path', str(tmp_path / 'summary.json')])
    assert validate_readiness_proof.main() == 2
