"""Tests for export_live_proof_artifact_set._write_summary()."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT))

# Patch the pilot import before importing the module under test.
_mock_pilot = MagicMock()
_mock_pilot._submit_freeze_wallet_governance_action.return_value = {
    'action_id': 'test-action-001',
    'status': 'submitted',
    'attestation_hash': '0xdeadbeef',
}

with patch.dict('sys.modules', {
    'services.api.app': MagicMock(),
    'services.api.app.pilot': _mock_pilot,
}):
    import importlib
    import services.api.scripts.export_live_proof_artifact_set as _elas


_CHAIN = {
    'evidence': {
        'id': 'evidence-live-1',
        'origin': 'live',
        'tx_hash': '0xabc',
        'block_number': 123,
        'detector_kind': 'counterparty-anomaly',
    },
    'detection': {'id': 'det-1', 'detector_kind': 'counterparty-anomaly'},
    'alert': {'id': 'alert-1', 'detection_id': 'det-1', 'incident_id': 'inc-1'},
    'incident': {'id': 'inc-1', 'source_alert_id': 'alert-1', 'linked_detection_id': 'det-1'},
}

_ACTION_RESULT = {
    'executed_at': '2026-05-27T12:00:00+00:00',
    'action_type': 'freeze_wallet',
    'execution_path': 'governance_submission',
    'status': 'submitted',
    'external_reference': 'test-action-001',
    'governance_response': {'action_id': 'test-action-001', 'attestation_hash': '0xdeadbeef'},
}


def _write_proof_records(artifact_dir: Path) -> None:
    artifact_dir.mkdir(parents=True, exist_ok=True)
    for name in _elas._REQUIRED_PROOF_RECORDS:
        (artifact_dir / name).write_text(json.dumps({'record': name}), encoding='utf-8')


def test_write_summary_creates_summary_json(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    artifact_dir = tmp_path / 'live_proof'
    _write_proof_records(artifact_dir)
    monkeypatch.setattr(_elas, 'ARTIFACT_DIR', artifact_dir)

    _elas._write_summary(_CHAIN, _ACTION_RESULT)

    summary_path = tmp_path / 'summary.json'
    assert summary_path.exists(), 'summary.json must be written to live_evidence/latest/'

    summary = json.loads(summary_path.read_text(encoding='utf-8'))
    assert summary['live_evidence_ready'] is True
    assert summary['evidence_source'] == 'live'
    assert summary['provider_ready'] is True
    assert summary['missing_reasons'] == []
    assert summary['latest_live_telemetry_at'] == '2026-05-27T12:00:00+00:00'
    assert summary['live_successful_monitoring_demo'] is True
    assert summary['simulator_successful_monitoring_demo'] is False
    assert summary['telemetry_event_present'] is True
    assert summary['detection_generated_from_telemetry'] is True
    assert summary['alert_generated_from_detection'] is True
    assert summary['incident_opened_from_alert'] is True
    assert summary['response_action_recommended_or_executed'] is True
    assert summary['evidence_package_exported'] is True
    # Billing fields must remain false (not confirmed by this script)
    assert summary['billing_email_provider_checks_passing'] is False
    assert summary['broad_self_serve_ready'] is False
    assert summary['paid_launch_readiness']['paid_launch_ready'] is False
    assert isinstance(summary['paid_launch_readiness']['blockers'], list)
    assert len(summary['paid_launch_readiness']['blockers']) > 0


def test_write_summary_fails_closed_when_proof_records_missing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    artifact_dir = tmp_path / 'live_proof'
    artifact_dir.mkdir(parents=True, exist_ok=True)
    # Write only one record, leave the rest missing
    (artifact_dir / 'chain_evidence_detection_alert_incident.json').write_text(
        json.dumps({'record': 'chain'}), encoding='utf-8'
    )
    monkeypatch.setattr(_elas, 'ARTIFACT_DIR', artifact_dir)

    _elas._write_summary(_CHAIN, _ACTION_RESULT)

    summary_path = tmp_path / 'summary.json'
    assert summary_path.exists()
    summary = json.loads(summary_path.read_text(encoding='utf-8'))

    assert summary['live_evidence_ready'] is False
    assert summary['provider_ready'] is False
    assert summary['evidence_package_exported'] is False
    assert len(summary['missing_reasons']) > 0
    missing_str = ' '.join(summary['missing_reasons'])
    assert 'evidence_metadata_verification.json' in missing_str


def test_write_summary_fails_closed_when_record_not_parseable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    artifact_dir = tmp_path / 'live_proof'
    _write_proof_records(artifact_dir)
    # Corrupt one record
    (artifact_dir / 'live_action_execution.json').write_text('NOT VALID JSON', encoding='utf-8')
    monkeypatch.setattr(_elas, 'ARTIFACT_DIR', artifact_dir)

    _elas._write_summary(_CHAIN, _ACTION_RESULT)

    summary = json.loads((tmp_path / 'summary.json').read_text(encoding='utf-8'))
    assert summary['live_evidence_ready'] is False
    assert any('parse_failure:live_action_execution.json' in r for r in summary['missing_reasons'])


def test_write_summary_required_presence_fields(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """All fields required by validate_readiness_proof.py must be present."""
    artifact_dir = tmp_path / 'live_proof'
    _write_proof_records(artifact_dir)
    monkeypatch.setattr(_elas, 'ARTIFACT_DIR', artifact_dir)

    _elas._write_summary(_CHAIN, _ACTION_RESULT)

    summary = json.loads((tmp_path / 'summary.json').read_text(encoding='utf-8'))
    required_fields = (
        'live_successful_monitoring_demo',
        'simulator_successful_monitoring_demo',
        'telemetry_event_present',
        'detection_generated_from_telemetry',
        'alert_generated_from_detection',
        'incident_opened_from_alert',
        'response_action_recommended_or_executed',
        'evidence_package_exported',
        'billing_email_provider_checks_passing',
        'onboarding_to_first_signal_complete',
        'production_validation_proof_bundle_complete',
        'controlled_pilot_ready',
        'enterprise_procurement_ready',
        'broad_self_serve_ready',
        'broad_self_serve_blocked_reason',
        'telemetry_evidence_source',
        'paid_launch_readiness',
    )
    for field in required_fields:
        assert field in summary, f'summary.json missing required field: {field}'
