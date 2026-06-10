"""
Tests for staging validation marker scripts.

Covers:
  A. Healthy /health response creates runtime_validated and migrations_validated markers.
  B. Degraded Paddle billing does NOT create runtime_validated marker.
  C. Non-200 /health does NOT create runtime_validated marker.
  D. Live evidence proof creates live_evidence_validated marker.
  E. Missing/fail-closed live evidence does not create marker.
  F. final-readiness in staging mode can pass staging validation.
  G. final-readiness in ci mode still blocks broad selling.
  H. Stale runtime_validated marker is removed on /health failure.
"""
from __future__ import annotations

import json
import sys
import unittest.mock as mock
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT))

from scripts.staging.validate_staging_runtime import validate_health
from scripts.staging.validate_live_evidence_marker import main as live_evidence_marker_main
from scripts.validate_100_percent_readiness import build_final_readiness


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_healthy_response(
    billing_degraded: bool = False,
    paddle_price_ids: bool = True,
    db_configured: bool = True,
    status: str = 'ok',
    app_mode: str = 'production',
) -> bytes:
    billing_status = 'degraded' if billing_degraded else 'healthy'
    data = {
        'status': status,
        'app_mode': app_mode,
        'database_url_configured': db_configured,
        'billing': {
            'status': billing_status,
            'available': not billing_degraded,
        },
        'paddle_api_key_present': True,
        'paddle_price_ids_configured': paddle_price_ids,
    }
    return json.dumps(data).encode()


def _mock_urlopen(body: bytes, status: int = 200):
    """Return a context-manager mock for urllib.request.urlopen."""
    resp = mock.MagicMock()
    resp.status = status
    resp.read.return_value = body
    resp.__enter__ = lambda s: s
    resp.__exit__ = mock.MagicMock(return_value=False)
    return mock.MagicMock(return_value=resp)


def _write_live_evidence_proof(
    path: Path,
    provider_ready: bool = True,
    live_evidence_ready: bool = True,
    evidence_source: str = 'live',
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    _TARGET_ID = '0xdeadbeef000000000000000000000000deadbeef'
    proof = {
        'schema_version': 1,
        'generated_at': '2026-06-03T12:00:00+00:00',
        'live_provider_evidence': {
            'provider_ready': provider_ready,
            'live_evidence_ready': live_evidence_ready,
            'evidence_source': evidence_source,
            'chain': {
                'telemetry_event_id': 'tel-test-001',
                'detection_event_id': 'devet-test-001',
                'detection_id': 'det-test-001',
                'alert_id': 'alert-test-001',
                'incident_id': 'inc-test-001',
                'response_action_id': 'ra-test-001',
                'evidence_package_id': 'ep-test-001',
            },
            'telemetry_record': {
                'source_type': 'rpc_polling',
                'workspace_id': 'ws-test-001',
                'target_id': 'target-test-001',
                'target_identifier': _TARGET_ID,
                'target_configured': True,
                'provider_receipt': {'receipt_id': 'rpc-receipt-001'},
                'on_chain_activity': {
                    'matched': True,
                    'transaction_hash': '0x' + 'a' * 64,
                    'target_identifier': _TARGET_ID,
                },
            },
            'detection_record': {
                'detection_event_id': 'devet-test-001',
                'detection_name': 'supply_divergence',
                'severity': 'medium',
                'detector_result': {'triggered': True, 'status': 'triggered'},
            },
            'evidence_package_record': {
                'persisted_linkage': {
                    'persisted': True,
                    'telemetry_event_id': 'tel-test-001',
                    'detection_event_id': 'devet-test-001',
                    'detection_id': 'det-test-001',
                    'alert_id': 'alert-test-001',
                },
            },
        },
    }
    path.write_text(json.dumps(proof))


def _write_launch_proof(path: Path, **overrides: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    proof: dict[str, Any] = {
        'schema_version': 1,
        'generated_at': '2026-06-03T12:00:00+00:00',
        'pilot_ready': True,
        'readiness': {
            'billing_ready': True,
            'billing_webhook_ready': True,
            'email_ready': True,
            'provider_ready': True,
            'live_evidence_ready': True,
        },
    }
    proof.update(overrides)
    path.write_text(json.dumps(proof))


def _write_release_proof(path: Path, release_status: str = 'pass') -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    proof = {
        'schema_version': 1,
        'generated_at': '2026-06-03T12:00:00+00:00',
        'release_status': release_status,
        'ci_required_gates_ready': release_status == 'pass',
        'launch_proof_ready': True,
        'manifest_ready': True,
        'test_report_ready': release_status == 'pass',
        'blockers': [] if release_status == 'pass' else ['ci-required-gates not ready'],
    }
    path.write_text(json.dumps(proof))


def _write_ci_gates(path: Path, overall_status: str = 'pass') -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    gates = {
        'schema_version': 1,
        'generated_at': '2026-06-03T12:00:00+00:00',
        'overall_status': overall_status,
        'required_gates': {
            'saas_workflow_validation': {'status': 'pass'},
        },
        'blockers': [],
    }
    path.write_text(json.dumps(gates))


# ---------------------------------------------------------------------------
# A. Healthy /health response creates runtime_validated and migrations_validated
# ---------------------------------------------------------------------------

def test_a_healthy_health_response_creates_runtime_validated(tmp_path: Path) -> None:
    health_url = 'https://staging.example.com/health'
    healthy_body = _make_healthy_response()

    with mock.patch('urllib.request.urlopen', _mock_urlopen(healthy_body)):
        ok, details = validate_health(health_url)

    assert ok is True, f'Expected healthy check to pass: {details}'
    assert details.get('http_status') == 200
    assert details.get('status') == 'ok'
    assert details.get('database_url_configured') is True
    assert details.get('billing_status') == 'healthy'


def test_a_healthy_creates_both_markers(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from scripts.staging import validate_staging_runtime as vsr

    proof_dir = tmp_path / 'artifacts' / 'staging-proof' / 'latest'
    monkeypatch.setattr(vsr, 'PROOF_DIR', proof_dir)
    monkeypatch.setattr(vsr, '_RUNTIME_MARKER', proof_dir / 'runtime_validated')
    monkeypatch.setattr(vsr, '_MIGRATIONS_MARKER', proof_dir / 'migrations_validated')
    monkeypatch.setenv('STAGING_API_URL', 'https://staging.example.com')

    healthy_body = _make_healthy_response()
    with mock.patch('urllib.request.urlopen', _mock_urlopen(healthy_body)):
        rc = vsr.main()

    assert rc == 0
    assert (proof_dir / 'runtime_validated').exists()
    assert (proof_dir / 'migrations_validated').exists()

    content = json.loads((proof_dir / 'runtime_validated').read_text())
    assert content['health_status'] == 'ok'
    assert content['database_url_configured'] is True


# ---------------------------------------------------------------------------
# B. Degraded billing does NOT create runtime_validated marker
# ---------------------------------------------------------------------------

def test_b_degraded_billing_does_not_create_marker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from scripts.staging import validate_staging_runtime as vsr

    proof_dir = tmp_path / 'artifacts' / 'staging-proof' / 'latest'
    monkeypatch.setattr(vsr, 'PROOF_DIR', proof_dir)
    monkeypatch.setattr(vsr, '_RUNTIME_MARKER', proof_dir / 'runtime_validated')
    monkeypatch.setattr(vsr, '_MIGRATIONS_MARKER', proof_dir / 'migrations_validated')
    monkeypatch.setenv('STAGING_API_URL', 'https://staging.example.com')

    degraded_body = _make_healthy_response(billing_degraded=True)
    with mock.patch('urllib.request.urlopen', _mock_urlopen(degraded_body)):
        rc = vsr.main()

    assert rc == 1
    assert not (proof_dir / 'runtime_validated').exists()


def test_b_validate_health_degraded_billing_returns_false() -> None:
    degraded_body = _make_healthy_response(billing_degraded=True)
    with mock.patch('urllib.request.urlopen', _mock_urlopen(degraded_body)):
        ok, details = validate_health('https://example.com/health')
    assert ok is False
    assert any('billing' in f.lower() for f in details.get('validation_failures', []))


# ---------------------------------------------------------------------------
# C. Non-200 /health does NOT create runtime_validated marker
# ---------------------------------------------------------------------------

def test_c_non_200_response_does_not_create_marker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from scripts.staging import validate_staging_runtime as vsr

    proof_dir = tmp_path / 'artifacts' / 'staging-proof' / 'latest'
    monkeypatch.setattr(vsr, 'PROOF_DIR', proof_dir)
    monkeypatch.setattr(vsr, '_RUNTIME_MARKER', proof_dir / 'runtime_validated')
    monkeypatch.setattr(vsr, '_MIGRATIONS_MARKER', proof_dir / 'migrations_validated')
    monkeypatch.setenv('STAGING_API_URL', 'https://staging.example.com')

    err = urllib.error.HTTPError(
        url='https://staging.example.com/health',
        code=500,
        msg='Internal Server Error',
        hdrs=None,  # type: ignore[arg-type]
        fp=None,
    )
    with mock.patch('urllib.request.urlopen', side_effect=err):
        rc = vsr.main()

    assert rc == 1
    assert not (proof_dir / 'runtime_validated').exists()


def test_c_stale_marker_removed_on_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from scripts.staging import validate_staging_runtime as vsr

    proof_dir = tmp_path / 'artifacts' / 'staging-proof' / 'latest'
    proof_dir.mkdir(parents=True, exist_ok=True)
    runtime_marker = proof_dir / 'runtime_validated'
    migrations_marker = proof_dir / 'migrations_validated'
    runtime_marker.write_text('{}')
    migrations_marker.write_text('{}')

    monkeypatch.setattr(vsr, 'PROOF_DIR', proof_dir)
    monkeypatch.setattr(vsr, '_RUNTIME_MARKER', runtime_marker)
    monkeypatch.setattr(vsr, '_MIGRATIONS_MARKER', migrations_marker)
    monkeypatch.setenv('STAGING_API_URL', 'https://staging.example.com')

    degraded_body = _make_healthy_response(billing_degraded=True)
    with mock.patch('urllib.request.urlopen', _mock_urlopen(degraded_body)):
        rc = vsr.main()

    assert rc == 1
    assert not runtime_marker.exists(), 'Stale runtime_validated marker should be removed'
    assert not migrations_marker.exists(), 'Stale migrations_validated marker should be removed'


# ---------------------------------------------------------------------------
# D. Live evidence proof creates live_evidence_validated marker
# ---------------------------------------------------------------------------

def test_d_live_evidence_proof_creates_marker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from scripts.staging import validate_live_evidence_marker as vle

    lep_path = tmp_path / 'artifacts' / 'live-evidence-proof' / 'latest' / 'summary.json'
    proof_dir = tmp_path / 'artifacts' / 'staging-proof' / 'latest'
    _write_live_evidence_proof(lep_path, provider_ready=True, live_evidence_ready=True)

    monkeypatch.setattr(vle, '_LIVE_EVIDENCE_PROOF', lep_path)
    monkeypatch.setattr(vle, '_PROOF_DIR', proof_dir)
    monkeypatch.setattr(vle, '_MARKER', proof_dir / 'live_evidence_validated')

    rc = live_evidence_marker_main()

    assert rc == 0
    assert (proof_dir / 'live_evidence_validated').exists()
    content = json.loads((proof_dir / 'live_evidence_validated').read_text())
    assert content['live_evidence_ready'] is True
    assert content['evidence_source'] == 'live'


# ---------------------------------------------------------------------------
# E. Missing or fail-closed live evidence does not create marker
# ---------------------------------------------------------------------------

def test_e_missing_live_evidence_proof_no_marker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from scripts.staging import validate_live_evidence_marker as vle

    lep_path = tmp_path / 'nonexistent' / 'summary.json'
    proof_dir = tmp_path / 'artifacts' / 'staging-proof' / 'latest'

    monkeypatch.setattr(vle, '_LIVE_EVIDENCE_PROOF', lep_path)
    monkeypatch.setattr(vle, '_PROOF_DIR', proof_dir)
    monkeypatch.setattr(vle, '_MARKER', proof_dir / 'live_evidence_validated')

    rc = live_evidence_marker_main()

    assert rc == 0
    assert not (proof_dir / 'live_evidence_validated').exists()


def test_e_fail_closed_live_evidence_no_marker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from scripts.staging import validate_live_evidence_marker as vle

    lep_path = tmp_path / 'artifacts' / 'live-evidence-proof' / 'latest' / 'summary.json'
    proof_dir = tmp_path / 'artifacts' / 'staging-proof' / 'latest'
    # provider_ready=false → should not create marker
    _write_live_evidence_proof(lep_path, provider_ready=False, live_evidence_ready=False)

    monkeypatch.setattr(vle, '_LIVE_EVIDENCE_PROOF', lep_path)
    monkeypatch.setattr(vle, '_PROOF_DIR', proof_dir)
    monkeypatch.setattr(vle, '_MARKER', proof_dir / 'live_evidence_validated')

    rc = live_evidence_marker_main()

    assert rc == 0
    assert not (proof_dir / 'live_evidence_validated').exists()


def test_e_simulator_source_no_marker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from scripts.staging import validate_live_evidence_marker as vle

    lep_path = tmp_path / 'artifacts' / 'live-evidence-proof' / 'latest' / 'summary.json'
    proof_dir = tmp_path / 'artifacts' / 'staging-proof' / 'latest'
    _write_live_evidence_proof(
        lep_path, provider_ready=True, live_evidence_ready=True,
        evidence_source='simulator',
    )

    monkeypatch.setattr(vle, '_LIVE_EVIDENCE_PROOF', lep_path)
    monkeypatch.setattr(vle, '_PROOF_DIR', proof_dir)
    monkeypatch.setattr(vle, '_MARKER', proof_dir / 'live_evidence_validated')

    rc = live_evidence_marker_main()

    assert rc == 0
    assert not (proof_dir / 'live_evidence_validated').exists()


# ---------------------------------------------------------------------------
# F. final-readiness in staging mode can pass staging validation
# ---------------------------------------------------------------------------

def test_f_final_readiness_staging_mode_passes_staging_validation(
    tmp_path: Path,
) -> None:
    launch_dir = tmp_path / 'launch-proof' / 'latest'
    release_dir = tmp_path / 'release-proof' / 'latest'
    staging_dir = tmp_path / 'staging-proof' / 'latest'
    staging_dir.mkdir(parents=True, exist_ok=True)

    _write_launch_proof(launch_dir / 'summary.json')
    _write_release_proof(release_dir / 'summary.json', release_status='pass')
    _write_ci_gates(release_dir / 'ci-required-gates.json', overall_status='pass')

    # Write a staging proof that indicates staging_launch_ready=true
    (staging_dir / 'summary.json').write_text(json.dumps({
        'schema_version': 1,
        'generated_at': '2026-06-03T12:00:00+00:00',
        'staging_launch_ready': True,
        'broad_paid_saas_ready': False,
        'blockers': [],
    }))

    result = build_final_readiness(
        mode='staging',
        strict=True,
        launch_proof_dir=launch_dir,
        release_proof_dir=release_dir,
        staging_proof_dir=staging_dir,
    )

    # staging mode + strict + staging_launch_ready=true must resolve staging validation
    staging_gate = result.get('required_gates', {}).get('staging_validation', {})
    assert staging_gate.get('status') == 'pass', (
        f'Expected staging_validation=pass in staging mode, got: {staging_gate}. '
        f'Blockers: {result.get("blockers")}'
    )


# ---------------------------------------------------------------------------
# G. final-readiness in ci mode still blocks broad selling
# ---------------------------------------------------------------------------

def test_g_final_readiness_ci_mode_blocks_broad_selling(tmp_path: Path) -> None:
    launch_dir = tmp_path / 'launch-proof' / 'latest'
    release_dir = tmp_path / 'release-proof' / 'latest'
    staging_dir = tmp_path / 'staging-proof' / 'latest'
    staging_dir.mkdir(parents=True, exist_ok=True)

    _write_launch_proof(launch_dir / 'summary.json')
    _write_release_proof(release_dir / 'summary.json', release_status='pass')
    _write_ci_gates(release_dir / 'ci-required-gates.json', overall_status='pass')
    (staging_dir / 'summary.json').write_text(json.dumps({
        'schema_version': 1,
        'generated_at': '2026-06-03T12:00:00+00:00',
        'staging_launch_ready': True,
        'broad_paid_saas_ready': False,
        'blockers': [],
    }))

    result = build_final_readiness(
        mode='ci',  # ci mode should never allow broad selling
        strict=True,
        launch_proof_dir=launch_dir,
        release_proof_dir=release_dir,
        staging_proof_dir=staging_dir,
    )

    assert result['safe_to_sell_broadly_today'] is False, (
        'safe_to_sell_broadly_today must be False in ci mode'
    )
    assert result['broad_paid_saas_ready'] is False, (
        'broad_paid_saas_ready must be False in ci mode'
    )
    # Must have a reason explaining why it can't sell
    reason = result.get('safe_to_sell_reason', '')
    assert 'ci' in reason.lower() or 'staging' in reason.lower() or 'mode' in reason.lower(), (
        f'safe_to_sell_reason should mention mode, got: {reason!r}'
    )
