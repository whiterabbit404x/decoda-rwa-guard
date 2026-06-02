"""
Cross-validation tests: launch-proof/latest/summary.json must be consistent
with live-evidence-proof, sell-now-proof, and the NIW positioning validator.

These tests fail if:
  1. live-evidence-proof says live evidence is ready but launch-proof says
     live_provider_evidence_ready=false
  2. NIW validator passes but launch-proof says niw_positioning_ready=false
  3. broad_paid_saas_ready=true while any staging/billing/email gate is false
  4. sell-now-proof says managed ready but launch-proof says managed_pilot_ready=false
  5. allowed_claims or prohibited_claims fields are missing
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
LAUNCH_PROOF_PATH = REPO_ROOT / 'artifacts' / 'launch-proof' / 'latest' / 'summary.json'
LIVE_EVIDENCE_PATH = REPO_ROOT / 'artifacts' / 'live-evidence-proof' / 'latest' / 'summary.json'
SELL_NOW_PATH = REPO_ROOT / 'artifacts' / 'sell-now-proof' / 'latest' / 'summary.json'


def _load_json(path: Path) -> dict | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except (json.JSONDecodeError, OSError):
        return None


def _launch_proof_has_readiness_categories(launch: dict) -> bool:
    return 'readiness_categories' in launch


# ---------------------------------------------------------------------------
# 1. launch-proof must have readiness_categories with all required keys
# ---------------------------------------------------------------------------
def test_launch_proof_has_granular_readiness_categories() -> None:
    launch = _load_json(LAUNCH_PROOF_PATH)
    if launch is None:
        pytest.skip('launch-proof artifact not yet generated; run run_no_billing_launch_proof.py')

    assert 'readiness_categories' in launch, (
        'launch-proof/latest/summary.json must have readiness_categories. '
        'Run: python scripts/staging/run_no_billing_launch_proof.py'
    )
    cats = launch['readiness_categories']
    required_keys = [
        'live_provider_evidence_ready',
        'managed_pilot_ready',
        'niw_positioning_ready',
        'broad_paid_saas_ready',
        'ci_required_gates_ready',
    ]
    for key in required_keys:
        assert key in cats, (
            f'readiness_categories missing key: {key!r}. '
            'Each readiness category must be independently reported.'
        )


# ---------------------------------------------------------------------------
# 2. live_provider_evidence_ready must match live-evidence-proof artifact
# ---------------------------------------------------------------------------
def test_live_evidence_proof_consistent_with_launch_proof() -> None:
    """
    If live-evidence-proof says provider_ready=true, provider_mode=live,
    live_evidence_ready=true, evidence_source=live, then launch-proof must
    say live_provider_evidence_ready=true.
    """
    live_ev = _load_json(LIVE_EVIDENCE_PATH)
    if live_ev is None:
        pytest.skip('live-evidence-proof artifact not found')

    launch = _load_json(LAUNCH_PROOF_PATH)
    if launch is None:
        pytest.skip('launch-proof artifact not yet generated')
    if not _launch_proof_has_readiness_categories(launch):
        pytest.skip('launch-proof does not yet have readiness_categories; run updated script')

    lpe = live_ev.get('live_provider_evidence', {})
    live_ev_says_ready = (
        lpe.get('provider_ready') is True
        and lpe.get('provider_mode') == 'live'
        and lpe.get('live_evidence_ready') is True
        and lpe.get('evidence_source') == 'live'
    )

    launch_claims_ready = launch['readiness_categories'].get('live_provider_evidence_ready', False)

    if live_ev_says_ready:
        assert launch_claims_ready, (
            'CONTRADICTION: live-evidence-proof says live evidence is ready '
            '(provider_ready=true, provider_mode=live, live_evidence_ready=true, evidence_source=live) '
            'but launch-proof says live_provider_evidence_ready=false. '
            'Run: python scripts/staging/run_no_billing_launch_proof.py'
        )

    # Reverse: if launch claims ready, live-evidence-proof must confirm
    if launch_claims_ready and not live_ev_says_ready:
        pytest.fail(
            'OVERCLAIM: launch-proof says live_provider_evidence_ready=true but '
            'live-evidence-proof does not confirm all required fields '
            '(provider_ready=true, provider_mode=live, live_evidence_ready=true, evidence_source=live).'
        )


# ---------------------------------------------------------------------------
# 3. niw_positioning_ready must match validate_niw_positioning.py output
# ---------------------------------------------------------------------------
def test_niw_validator_consistent_with_launch_proof() -> None:
    """
    If scripts/validate_niw_positioning.py exits 0, launch-proof must say
    niw_positioning_ready=true.
    """
    result = subprocess.run(
        ['python', 'scripts/validate_niw_positioning.py'],
        cwd=REPO_ROOT,
        capture_output=True,
        encoding='utf-8',
        timeout=60,
    )
    niw_passes = result.returncode == 0

    launch = _load_json(LAUNCH_PROOF_PATH)
    if launch is None:
        pytest.skip('launch-proof artifact not yet generated')
    if not _launch_proof_has_readiness_categories(launch):
        pytest.skip('launch-proof does not yet have readiness_categories; run updated script')

    launch_niw_ready = launch['readiness_categories'].get('niw_positioning_ready', False)

    if niw_passes:
        assert launch_niw_ready, (
            'CONTRADICTION: validate_niw_positioning.py passes but launch-proof says '
            'niw_positioning_ready=false. '
            'Run: python scripts/staging/run_no_billing_launch_proof.py'
        )

    if launch_niw_ready and not niw_passes:
        pytest.fail(
            'OVERCLAIM: launch-proof says niw_positioning_ready=true but '
            'validate_niw_positioning.py exits non-zero. '
            f'Validator output:\n{result.stdout}{result.stderr}'
        )


# ---------------------------------------------------------------------------
# 4. broad_paid_saas_ready=true requires all gates confirmed in broad_paid_saas
# ---------------------------------------------------------------------------
def test_broad_paid_saas_not_overclaimed() -> None:
    """
    If launch-proof claims broad_paid_saas_ready=true, every gate in the
    broad_paid_saas section must also be true. Staging can be false while
    live evidence is true, but the reverse must never happen.
    """
    launch = _load_json(LAUNCH_PROOF_PATH)
    if launch is None:
        pytest.skip('launch-proof artifact not yet generated')
    if not _launch_proof_has_readiness_categories(launch):
        pytest.skip('launch-proof does not yet have readiness_categories; run updated script')

    broad_ready = launch['readiness_categories'].get('broad_paid_saas_ready', False)
    if not broad_ready:
        return  # No overclaim to check

    broad_saas = launch.get('broad_paid_saas', {})
    required_gates = [
        'staging_api_configured',
        'staging_app_configured',
        'staging_database_configured',
        'staging_worker_enabled',
        'billing_configured',
        'email_configured',
        'auth_secret_configured',
    ]
    failed_gates = [g for g in required_gates if not broad_saas.get(g, False)]
    assert not failed_gates, (
        f'OVERCLAIM: launch-proof claims broad_paid_saas_ready=true but these '
        f'gates are false: {failed_gates}. broad_paid_saas_ready must not be true '
        'while any staging/billing/email gate remains unproven.'
    )


# ---------------------------------------------------------------------------
# 5. managed_pilot_ready must match sell-now-proof
# ---------------------------------------------------------------------------
def test_sell_now_proof_consistent_with_launch_proof() -> None:
    """
    If sell-now-proof says sell_now_managed_ready=true, launch-proof must
    say managed_pilot_ready=true.
    """
    sell_now = _load_json(SELL_NOW_PATH)
    if sell_now is None:
        pytest.skip('sell-now-proof artifact not found')

    launch = _load_json(LAUNCH_PROOF_PATH)
    if launch is None:
        pytest.skip('launch-proof artifact not yet generated')
    if not _launch_proof_has_readiness_categories(launch):
        pytest.skip('launch-proof does not yet have readiness_categories; run updated script')

    sell_now_managed = sell_now.get('sell_now_managed_ready', False)
    launch_managed = launch['readiness_categories'].get('managed_pilot_ready', False)

    if sell_now_managed:
        assert launch_managed, (
            'CONTRADICTION: sell-now-proof says sell_now_managed_ready=true but '
            'launch-proof says managed_pilot_ready=false. '
            'Run: python scripts/staging/run_no_billing_launch_proof.py'
        )


# ---------------------------------------------------------------------------
# 6. allowed_claims and prohibited_claims must be present and non-empty
# ---------------------------------------------------------------------------
def test_launch_proof_has_allowed_and_prohibited_claims() -> None:
    launch = _load_json(LAUNCH_PROOF_PATH)
    if launch is None:
        pytest.skip('launch-proof artifact not yet generated')
    if not _launch_proof_has_readiness_categories(launch):
        pytest.skip('launch-proof does not yet have readiness_categories; run updated script')

    assert 'allowed_claims' in launch, 'launch-proof must have allowed_claims field'
    assert 'prohibited_claims' in launch, 'launch-proof must have prohibited_claims field'
    assert isinstance(launch['allowed_claims'], list), 'allowed_claims must be a list'
    assert isinstance(launch['prohibited_claims'], list), 'prohibited_claims must be a list'
    assert len(launch['allowed_claims']) >= 1, 'allowed_claims must not be empty'
    assert len(launch['prohibited_claims']) >= 1, 'prohibited_claims must not be empty'

    prohibited = launch['prohibited_claims']
    assert any('broad paid SaaS' in c for c in prohibited), (
        'prohibited_claims must include a "broad paid SaaS" prohibition'
    )


# ---------------------------------------------------------------------------
# 7. Unit tests for the readiness helper functions (no artifact files needed)
# ---------------------------------------------------------------------------
def test_live_evidence_readiness_from_missing_artifact(tmp_path) -> None:
    """_read_live_evidence_readiness returns False when artifact is missing."""
    import scripts.staging.run_no_billing_launch_proof as _mod

    original = _mod.REPO_ROOT
    try:
        _mod.REPO_ROOT = tmp_path
        result = _mod._read_live_evidence_readiness()
    finally:
        _mod.REPO_ROOT = original

    assert result['live_provider_evidence_ready'] is False
    assert result['artifact_available'] is False


def test_live_evidence_readiness_from_valid_artifact(tmp_path) -> None:
    """_read_live_evidence_readiness returns True when artifact is live-confirmed."""
    import scripts.staging.run_no_billing_launch_proof as _mod

    artifact_dir = tmp_path / 'artifacts' / 'live-evidence-proof' / 'latest'
    artifact_dir.mkdir(parents=True)
    (artifact_dir / 'summary.json').write_text(json.dumps({
        'live_provider_evidence': {
            'provider_ready': True,
            'provider_mode': 'live',
            'live_evidence_ready': True,
            'evidence_source': 'live',
        }
    }), encoding='utf-8')

    original = _mod.REPO_ROOT
    try:
        _mod.REPO_ROOT = tmp_path
        result = _mod._read_live_evidence_readiness()
    finally:
        _mod.REPO_ROOT = original

    assert result['live_provider_evidence_ready'] is True
    assert result['artifact_available'] is True


def test_live_evidence_readiness_rejects_simulator(tmp_path) -> None:
    """_read_live_evidence_readiness returns False for simulator evidence."""
    import scripts.staging.run_no_billing_launch_proof as _mod

    artifact_dir = tmp_path / 'artifacts' / 'live-evidence-proof' / 'latest'
    artifact_dir.mkdir(parents=True)
    (artifact_dir / 'summary.json').write_text(json.dumps({
        'live_provider_evidence': {
            'provider_ready': True,
            'provider_mode': 'live',
            'live_evidence_ready': True,
            'evidence_source': 'simulator',  # must not pass
        }
    }), encoding='utf-8')

    original = _mod.REPO_ROOT
    try:
        _mod.REPO_ROOT = tmp_path
        result = _mod._read_live_evidence_readiness()
    finally:
        _mod.REPO_ROOT = original

    assert result['live_provider_evidence_ready'] is False


def test_broad_paid_saas_gates_fail_closed(monkeypatch) -> None:
    """_check_broad_paid_saas_gates returns False when no staging env vars set."""
    for var in (
        'STAGING_API_URL', 'STAGING_APP_URL', 'STAGING_DATABASE_URL',
        'STAGING_WORKER_ENABLED', 'BILLING_PROVIDER', 'EMAIL_PROVIDER',
        'STAGING_AUTH_TOKEN_SECRET',
    ):
        monkeypatch.delenv(var, raising=False)

    from scripts.staging.run_no_billing_launch_proof import _check_broad_paid_saas_gates

    result = _check_broad_paid_saas_gates()
    assert result['broad_paid_saas_ready'] is False
    assert len(result['blockers']) > 0


def test_broad_paid_saas_staging_false_does_not_affect_live_evidence() -> None:
    """
    broad_paid_saas_ready=false must not prevent live_provider_evidence_ready=true.
    Each readiness category is independent.
    """
    from scripts.staging.run_no_billing_launch_proof import (
        _check_broad_paid_saas_gates,
        _derive_allowed_claims,
    )

    broad_saas = _check_broad_paid_saas_gates()
    assert broad_saas['broad_paid_saas_ready'] is False

    # Live evidence can still be ready even when broad SaaS gates fail
    live_evidence = {'live_provider_evidence_ready': True}
    managed_pilot = {'managed_pilot_ready': True}
    niw = {'niw_positioning_ready': True}

    claims = _derive_allowed_claims(live_evidence, managed_pilot, niw)
    assert 'live provider evidence ready' in claims
    assert 'not broad paid SaaS ready' in claims
