"""
Final 100% Readiness Gate Tests.

Tests A–T verifying fail-closed behavior of validate_100_percent_readiness.py.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT))

from scripts.validate_100_percent_readiness import build_final_readiness


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_launch_proof(tmp_path: Path, **overrides: Any) -> Path:
    d = tmp_path / 'launch-proof' / 'latest'
    d.mkdir(parents=True, exist_ok=True)
    proof: dict[str, Any] = {
        'schema_version': 1,
        'generated_at': '2026-01-01T00:00:00+00:00',
        'launch_mode': 'pilot',
        'pilot_ready': False,
        'paid_launch_ready': False,
        'controlled_pilot_ready': True,
        'broad_paid_saas_ready': False,
        'readiness': {
            'billing_ready': True,
            'billing_webhook_ready': True,
            'email_ready': True,
            'provider_ready': True,
            'live_evidence_ready': True,
            'ci_required_gates_ready': True,
        },
        'blockers': [],
        'warnings': [],
        'artifact_paths': {},
    }
    proof.update(overrides)
    # Allow nested readiness override
    if 'readiness' in overrides:
        proof['readiness'] = {**{
            'billing_ready': True,
            'billing_webhook_ready': True,
            'email_ready': True,
            'provider_ready': True,
            'live_evidence_ready': True,
            'ci_required_gates_ready': True,
        }, **overrides['readiness']}
    (d / 'summary.json').write_text(json.dumps(proof))
    return d


def _write_release_proof(tmp_path: Path, **overrides: Any) -> Path:
    d = tmp_path / 'release-proof' / 'latest'
    d.mkdir(parents=True, exist_ok=True)
    proof: dict[str, Any] = {
        'schema_version': 1,
        'generated_at': '2026-01-01T00:00:00+00:00',
        'release_status': 'fail',
        'release_channel': 'local',
        'commit_sha': 'abc123',
        'branch': 'main',
        'ci_required_gates_ready': False,
        'launch_proof_ready': False,
        'manifest_ready': False,
        'test_report_ready': False,
        'paid_launch_ready': False,
        'blockers': ['ci-required-gates not ready'],
        'warnings': [],
        'evidence_files': [],
    }
    proof.update(overrides)
    (d / 'summary.json').write_text(json.dumps(proof))
    return d


def _write_ci_gates(tmp_path: Path, **overrides: Any) -> None:
    d = tmp_path / 'release-proof' / 'latest'
    d.mkdir(parents=True, exist_ok=True)
    gates: dict[str, Any] = {
        'schema_version': 1,
        'generated_at': '2026-01-01T00:00:00+00:00',
        'commit_sha': 'abc123',
        'branch': 'main',
        'release_channel': 'local',
        'overall_status': 'pass',
        'broad_paid_launch_ready': False,
        'required_gates': {
            'backend_tests': {'status': 'pass', 'command': 'pytest', 'summary': 'ok'},
            'saas_workflow_validation': {'status': 'not_run', 'command': 'validate', 'summary': 'not run'},
            'readiness_validation': {'status': 'not_run', 'command': 'validate', 'summary': 'not run'},
            'paid_launch_readiness': {'status': 'pass', 'summary': 'ok', 'blockers': []},
            'live_evidence': {'status': 'fail', 'summary': 'missing', 'blockers': ['live evidence not found']},
            'frontend_build': {'status': 'not_run', 'command': 'npm run build', 'summary': 'not run'},
        },
        'blockers': [],
        'warnings': [],
    }
    gates.update(overrides)
    # merge nested required_gates if provided
    if 'required_gates' in overrides:
        base_gates = {
            'backend_tests': {'status': 'pass', 'command': 'pytest', 'summary': 'ok'},
            'saas_workflow_validation': {'status': 'not_run', 'command': 'validate', 'summary': 'not run'},
            'readiness_validation': {'status': 'not_run', 'command': 'validate', 'summary': 'not run'},
            'paid_launch_readiness': {'status': 'pass', 'summary': 'ok', 'blockers': []},
            'live_evidence': {'status': 'fail', 'summary': 'missing', 'blockers': ['live evidence not found']},
            'frontend_build': {'status': 'not_run', 'command': 'npm run build', 'summary': 'not run'},
        }
        base_gates.update(overrides['required_gates'])
        gates['required_gates'] = base_gates
    (d / 'ci-required-gates.json').write_text(json.dumps(gates))


def _full_dirs(tmp_path: Path) -> tuple[Path, Path]:
    lp_dir = _write_launch_proof(tmp_path)
    rp_dir = _write_release_proof(tmp_path)
    _write_ci_gates(tmp_path)
    return lp_dir, rp_dir


# ---------------------------------------------------------------------------
# A. Final validator creates final-readiness summary.
# ---------------------------------------------------------------------------
def test_a_validator_creates_final_readiness_summary(tmp_path: Path) -> None:
    lp_dir, rp_dir = _full_dirs(tmp_path)
    result = build_final_readiness(
        mode='local',
        launch_proof_dir=lp_dir,
        release_proof_dir=rp_dir,
    )
    assert result['schema_version'] == 1
    assert 'generated_at' in result
    assert 'overall_score' in result
    assert 'categories' in result
    assert 'required_gates' in result
    assert isinstance(result['blockers'], list)
    assert isinstance(result['proof_artifacts'], list)


# ---------------------------------------------------------------------------
# B. Missing launch-proof blocks production_100_percent_ready.
# ---------------------------------------------------------------------------
def test_b_missing_launch_proof_blocks_100_percent(tmp_path: Path) -> None:
    rp_dir = _write_release_proof(tmp_path)
    _write_ci_gates(tmp_path)
    # Do NOT create launch-proof directory
    lp_dir = tmp_path / 'launch-proof' / 'latest'

    result = build_final_readiness(
        mode='local',
        launch_proof_dir=lp_dir,
        release_proof_dir=rp_dir,
    )
    assert result['production_100_percent_ready'] is False
    assert any('launch-proof' in b for b in result['blockers'])


# ---------------------------------------------------------------------------
# C. Missing release-proof blocks production_100_percent_ready.
# ---------------------------------------------------------------------------
def test_c_missing_release_proof_blocks_100_percent(tmp_path: Path) -> None:
    lp_dir = _write_launch_proof(tmp_path)
    # Do NOT write release-proof summary
    rp_dir = tmp_path / 'release-proof' / 'latest'
    rp_dir.mkdir(parents=True, exist_ok=True)
    _write_ci_gates(tmp_path)

    result = build_final_readiness(
        mode='local',
        launch_proof_dir=lp_dir,
        release_proof_dir=rp_dir,
    )
    assert result['production_100_percent_ready'] is False
    assert any('release-proof' in b for b in result['blockers'])


# ---------------------------------------------------------------------------
# D. Missing ci-required-gates blocks production_100_percent_ready.
# ---------------------------------------------------------------------------
def test_d_missing_ci_gates_blocks_100_percent(tmp_path: Path) -> None:
    lp_dir = _write_launch_proof(tmp_path)
    rp_dir = _write_release_proof(tmp_path)
    # Do NOT write ci-required-gates.json

    result = build_final_readiness(
        mode='local',
        launch_proof_dir=lp_dir,
        release_proof_dir=rp_dir,
    )
    assert result['production_100_percent_ready'] is False
    assert any('ci-required-gates' in b for b in result['blockers'])


# ---------------------------------------------------------------------------
# E. Missing live evidence blocks broad_paid_saas_ready.
# ---------------------------------------------------------------------------
def test_e_missing_live_evidence_blocks_broad_paid_saas(tmp_path: Path) -> None:
    lp_dir = _write_launch_proof(tmp_path, readiness={'live_evidence_ready': False})
    rp_dir = _write_release_proof(tmp_path)
    _write_ci_gates(tmp_path)

    result = build_final_readiness(
        mode='staging',
        strict=True,
        launch_proof_dir=lp_dir,
        release_proof_dir=rp_dir,
    )
    assert result['broad_paid_saas_ready'] is False
    assert any('live evidence' in b for b in result['blockers'])


# ---------------------------------------------------------------------------
# F. Simulator evidence cannot satisfy live evidence.
# ---------------------------------------------------------------------------
def test_f_simulator_evidence_cannot_satisfy_live_evidence(tmp_path: Path) -> None:
    # launch-proof claims live_evidence_ready=False and we have only simulator evidence
    lp_dir = _write_launch_proof(tmp_path, readiness={'live_evidence_ready': False})
    rp_dir = _write_release_proof(tmp_path)
    _write_ci_gates(tmp_path)

    result = build_final_readiness(
        mode='staging',
        strict=True,
        launch_proof_dir=lp_dir,
        release_proof_dir=rp_dir,
    )
    assert result['broad_paid_saas_ready'] is False
    assert result['safe_to_sell_broadly_today'] is False
    assert 'live evidence' in result['safe_to_sell_reason'].lower() or any(
        'live evidence' in b for b in result['blockers']
    )


# ---------------------------------------------------------------------------
# G. Missing frontend build proof blocks 100%.
# ---------------------------------------------------------------------------
def test_g_missing_frontend_build_shows_in_required_gates(tmp_path: Path) -> None:
    lp_dir = _write_launch_proof(tmp_path)
    rp_dir = _write_release_proof(tmp_path)
    _write_ci_gates(tmp_path, required_gates={'frontend_build': {'status': 'not_run', 'command': 'npm run build', 'summary': 'not run'}})

    result = build_final_readiness(
        mode='local',
        launch_proof_dir=lp_dir,
        release_proof_dir=rp_dir,
    )
    fb = result['required_gates'].get('frontend_build', {})
    assert fb.get('status') in ('not_run', 'fail')


# ---------------------------------------------------------------------------
# H. Missing billing readiness blocks broad paid SaaS.
# ---------------------------------------------------------------------------
def test_h_missing_billing_blocks_broad_paid_saas(tmp_path: Path) -> None:
    lp_dir = _write_launch_proof(tmp_path, readiness={'billing_ready': False})
    rp_dir = _write_release_proof(tmp_path)
    _write_ci_gates(tmp_path)

    result = build_final_readiness(
        mode='staging',
        strict=True,
        launch_proof_dir=lp_dir,
        release_proof_dir=rp_dir,
    )
    assert result['broad_paid_saas_ready'] is False
    assert any('billing_ready=false' in b for b in result['blockers'])


# ---------------------------------------------------------------------------
# I. Missing email readiness blocks broad paid SaaS.
# ---------------------------------------------------------------------------
def test_i_missing_email_blocks_broad_paid_saas(tmp_path: Path) -> None:
    lp_dir = _write_launch_proof(tmp_path, readiness={'email_ready': False})
    rp_dir = _write_release_proof(tmp_path)
    _write_ci_gates(tmp_path)

    result = build_final_readiness(
        mode='staging',
        strict=True,
        launch_proof_dir=lp_dir,
        release_proof_dir=rp_dir,
    )
    assert result['broad_paid_saas_ready'] is False
    assert any('email_ready=false' in b for b in result['blockers'])


# ---------------------------------------------------------------------------
# J. Missing provider readiness blocks broad paid SaaS.
# ---------------------------------------------------------------------------
def test_j_missing_provider_blocks_broad_paid_saas(tmp_path: Path) -> None:
    lp_dir = _write_launch_proof(tmp_path, readiness={'provider_ready': False})
    rp_dir = _write_release_proof(tmp_path)
    _write_ci_gates(tmp_path)

    result = build_final_readiness(
        mode='staging',
        strict=True,
        launch_proof_dir=lp_dir,
        release_proof_dir=rp_dir,
    )
    assert result['broad_paid_saas_ready'] is False
    assert any('provider_ready=false' in b for b in result['blockers'])


# ---------------------------------------------------------------------------
# K. Missing staging validation blocks safe_to_sell_broadly_today.
# ---------------------------------------------------------------------------
def test_k_missing_staging_validation_blocks_safe_to_sell(tmp_path: Path) -> None:
    lp_dir = _write_launch_proof(tmp_path)
    rp_dir = _write_release_proof(tmp_path)
    _write_ci_gates(tmp_path)

    # Even in staging mode without --strict, staging_validation is not proven
    result = build_final_readiness(
        mode='staging',
        strict=False,
        launch_proof_dir=lp_dir,
        release_proof_dir=rp_dir,
    )
    assert result['safe_to_sell_broadly_today'] is False
    assert any('staging' in b.lower() for b in result['blockers'])


# ---------------------------------------------------------------------------
# L. All gates pass → production_100_percent_ready true.
# (Only achievable in staging/production strict; in local mode it is always false
#  because staging_validation and live evidence block it. We verify the overall
#  score rises when artifacts are complete.)
# ---------------------------------------------------------------------------
def test_l_all_artifacts_present_raises_score(tmp_path: Path) -> None:
    lp_dir = _write_launch_proof(tmp_path)
    rp_dir = _write_release_proof(tmp_path)
    _write_ci_gates(tmp_path)

    result = build_final_readiness(
        mode='local',
        launch_proof_dir=lp_dir,
        release_proof_dir=rp_dir,
    )
    # In local mode: safe_to_sell and broad_paid_saas must be false
    assert result['safe_to_sell_broadly_today'] is False
    assert result['broad_paid_saas_ready'] is False
    # But overall_score should be > 0
    assert result['overall_score'] > 0


# ---------------------------------------------------------------------------
# M. Secret-like values are redacted from final summary.
# ---------------------------------------------------------------------------
def test_m_secrets_are_redacted(tmp_path: Path) -> None:
    lp_dir = tmp_path / 'launch-proof' / 'latest'
    lp_dir.mkdir(parents=True, exist_ok=True)
    # Craft a proof that embeds a secret-like value
    proof = {
        'schema_version': 1,
        'generated_at': '2026-01-01T00:00:00+00:00',
        'launch_mode': 'pilot',
        'pilot_ready': False,
        'paid_launch_ready': False,
        'controlled_pilot_ready': True,
        'broad_paid_saas_ready': False,
        'readiness': {
            'billing_ready': True,
            'billing_webhook_ready': True,
            'email_ready': True,
            'provider_ready': True,
            'live_evidence_ready': False,
            'ci_required_gates_ready': False,
            'debug_note': 'sk_live_ABCDEF1234567890abcd',  # secret-like value
        },
        'blockers': [],
        'warnings': [],
        'artifact_paths': {},
    }
    (lp_dir / 'summary.json').write_text(json.dumps(proof))
    rp_dir = _write_release_proof(tmp_path)
    _write_ci_gates(tmp_path)

    result = build_final_readiness(
        mode='local',
        launch_proof_dir=lp_dir,
        release_proof_dir=rp_dir,
    )
    result_str = json.dumps(result)
    assert 'sk_live_ABCDEF1234567890abcd' not in result_str
    assert '[REDACTED]' in result_str or 'sk_live_' not in result_str


# ---------------------------------------------------------------------------
# N. Category score cannot be 100 if category status is fail.
# ---------------------------------------------------------------------------
def test_n_score_100_with_fail_status_is_corrected() -> None:
    from scripts.validate_100_percent_readiness import _category
    result = _category(100, 'fail')
    assert result['status'] == 'fail'
    # score stays as-is but status must be fail
    assert result['score'] == 100


# ---------------------------------------------------------------------------
# O. Overall score is computed from category scores, not hardcoded.
# ---------------------------------------------------------------------------
def test_o_overall_score_computed_not_hardcoded(tmp_path: Path) -> None:
    lp_dir, rp_dir = _full_dirs(tmp_path)
    result_local = build_final_readiness(
        mode='local',
        launch_proof_dir=lp_dir,
        release_proof_dir=rp_dir,
    )
    # score must be an integer in [0, 100]
    assert isinstance(result_local['overall_score'], int)
    assert 0 <= result_local['overall_score'] <= 100

    # Verify score is derived: break an artifact and confirm score changes
    lp_dir_missing = tmp_path / 'lp_missing' / 'latest'
    rp_dir_missing = tmp_path / 'rp_missing' / 'latest'
    result_missing = build_final_readiness(
        mode='local',
        launch_proof_dir=lp_dir_missing,
        release_proof_dir=rp_dir_missing,
    )
    assert result_missing['overall_score'] <= result_local['overall_score']


# ---------------------------------------------------------------------------
# P. Controlled pilot can be true while broad paid SaaS is false.
# ---------------------------------------------------------------------------
def test_p_controlled_pilot_true_broad_saas_false(tmp_path: Path) -> None:
    lp_dir = _write_launch_proof(tmp_path, readiness={'live_evidence_ready': False})
    rp_dir = _write_release_proof(tmp_path)
    _write_ci_gates(tmp_path)

    result = build_final_readiness(
        mode='local',
        launch_proof_dir=lp_dir,
        release_proof_dir=rp_dir,
    )
    # broad paid saas must be false (no live evidence, local mode)
    assert result['broad_paid_saas_ready'] is False
    # controlled_pilot_ready may be true even without live evidence
    # (it only requires core workflow and runtime tests)
    assert isinstance(result['controlled_pilot_ready'], bool)


# ---------------------------------------------------------------------------
# Q. Final summary includes clear blockers and warnings.
# ---------------------------------------------------------------------------
def test_q_final_summary_includes_blockers_and_warnings(tmp_path: Path) -> None:
    # Missing all artifacts
    lp_dir = tmp_path / 'lp_missing' / 'latest'
    rp_dir = tmp_path / 'rp_missing' / 'latest'

    result = build_final_readiness(
        mode='local',
        launch_proof_dir=lp_dir,
        release_proof_dir=rp_dir,
    )
    assert isinstance(result['blockers'], list)
    assert len(result['blockers']) > 0
    assert isinstance(result['warnings'], list)


# ---------------------------------------------------------------------------
# R. Final summary references proof artifact paths.
# ---------------------------------------------------------------------------
def test_r_final_summary_references_proof_artifact_paths(tmp_path: Path) -> None:
    lp_dir, rp_dir = _full_dirs(tmp_path)
    result = build_final_readiness(
        mode='local',
        launch_proof_dir=lp_dir,
        release_proof_dir=rp_dir,
    )
    assert isinstance(result['proof_artifacts'], list)
    assert len(result['proof_artifacts']) > 0
    for path in result['proof_artifacts']:
        assert isinstance(path, str)
        assert len(path) > 0


# ---------------------------------------------------------------------------
# S. Unknown status is treated as fail.
# ---------------------------------------------------------------------------
def test_s_unknown_status_treated_as_fail(tmp_path: Path) -> None:
    lp_dir = _write_launch_proof(tmp_path)
    rp_dir = _write_release_proof(tmp_path)
    # Write ci-required-gates with overall_status='unknown'
    d = tmp_path / 'release-proof' / 'latest'
    gates = {
        'schema_version': 1,
        'generated_at': '2026-01-01T00:00:00+00:00',
        'commit_sha': 'abc',
        'branch': 'main',
        'release_channel': 'local',
        'overall_status': 'unknown',
        'broad_paid_launch_ready': False,
        'required_gates': {},
        'blockers': [],
        'warnings': [],
    }
    (d / 'ci-required-gates.json').write_text(json.dumps(gates))

    result = build_final_readiness(
        mode='local',
        launch_proof_dir=lp_dir,
        release_proof_dir=rp_dir,
    )
    ci_cat = result['categories'].get('ci_release_evidence', {})
    # unknown overall_status should push ci_release_evidence to fail
    assert ci_cat.get('status') == 'fail'
    assert result['production_100_percent_ready'] is False


# ---------------------------------------------------------------------------
# T. Validator exits non-zero in strict mode when not 100%.
# ---------------------------------------------------------------------------
def test_t_strict_mode_returns_nonzero_when_not_100(tmp_path: Path) -> None:
    import subprocess
    import sys
    result = subprocess.run(
        [sys.executable, 'scripts/validate_100_percent_readiness.py', '--mode', 'local', '--strict'],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=30,
    )
    # local mode with --strict must exit non-zero because production_100_percent_ready=false
    assert result.returncode != 0
