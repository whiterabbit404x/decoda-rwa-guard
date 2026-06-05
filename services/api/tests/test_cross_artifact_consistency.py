"""
Cross-artifact consistency tests.

Fails if:
1. launch-proof says paid_launch_ready=false while final-readiness says
   production_100_percent_ready=true.
2. Any latest artifact says broad/paid/enterprise false while final-readiness
   says the corresponding flag is true.
3. services/api live_evidence latest summary is older than
   artifacts/live-evidence-proof latest (i.e. still references stale telemetry).
4. services/api live_evidence says enterprise_procurement_ready=false while
   final-readiness says enterprise_procurement_ready=true.
5. Any latest artifact still references April 2026 telemetry as current evidence.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]

FINAL_READINESS_PATH = (
    REPO_ROOT / 'artifacts' / 'final-readiness' / 'latest' / 'summary.json'
)
LAUNCH_PROOF_PATH = (
    REPO_ROOT / 'artifacts' / 'launch-proof' / 'latest' / 'summary.json'
)
RELEASE_PROOF_PATH = (
    REPO_ROOT / 'artifacts' / 'release-proof' / 'latest' / 'summary.json'
)
CI_GATES_PATH = (
    REPO_ROOT / 'artifacts' / 'release-proof' / 'latest' / 'ci-required-gates.json'
)
LIVE_EVIDENCE_PROOF_PATH = (
    REPO_ROOT / 'artifacts' / 'live-evidence-proof' / 'latest' / 'summary.json'
)
API_LIVE_EVIDENCE_PATH = (
    REPO_ROOT
    / 'services'
    / 'api'
    / 'artifacts'
    / 'live_evidence'
    / 'latest'
    / 'summary.json'
)


def _load(path: Path) -> dict | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except (json.JSONDecodeError, OSError):
        return None


def _parse_dt(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(str(s).replace('Z', '+00:00'))
    except (ValueError, AttributeError):
        return None


# ---------------------------------------------------------------------------
# 1. launch-proof paid_launch_ready must match final-readiness
# ---------------------------------------------------------------------------
def test_launch_proof_paid_launch_ready_matches_final_readiness() -> None:
    """
    If final-readiness says production_100_percent_ready=true,
    launch-proof must not say paid_launch_ready=false.
    """
    final = _load(FINAL_READINESS_PATH)
    if final is None:
        pytest.skip('final-readiness artifact not found')

    launch = _load(LAUNCH_PROOF_PATH)
    if launch is None:
        pytest.skip('launch-proof artifact not found')

    if final.get('production_100_percent_ready') is True:
        assert launch.get('paid_launch_ready') is True, (
            'CONTRADICTION: final-readiness says production_100_percent_ready=true but '
            'launch-proof says paid_launch_ready=false. '
            'Regenerate launch-proof in staging/production mode.'
        )


# ---------------------------------------------------------------------------
# 2. broad_paid_saas_ready must be consistent across all latest artifacts
# ---------------------------------------------------------------------------
def test_broad_paid_saas_consistent_across_artifacts() -> None:
    """
    If final-readiness says broad_paid_saas_ready=true, then launch-proof,
    release-proof, and ci-required-gates must not contradict it.
    """
    final = _load(FINAL_READINESS_PATH)
    if final is None:
        pytest.skip('final-readiness artifact not found')

    if not final.get('broad_paid_saas_ready'):
        return  # constraint not applicable

    launch = _load(LAUNCH_PROOF_PATH)
    if launch is not None:
        assert launch.get('broad_paid_saas_ready') is True, (
            'CONTRADICTION: final-readiness says broad_paid_saas_ready=true but '
            'launch-proof says broad_paid_saas_ready=false.'
        )

    release = _load(RELEASE_PROOF_PATH)
    if release is not None:
        assert release.get('paid_launch_ready') is True, (
            'CONTRADICTION: final-readiness says broad_paid_saas_ready=true but '
            'release-proof says paid_launch_ready=false.'
        )

    ci_gates = _load(CI_GATES_PATH)
    if ci_gates is not None:
        assert ci_gates.get('broad_paid_launch_ready') is not False, (
            'CONTRADICTION: final-readiness says broad_paid_saas_ready=true but '
            'ci-required-gates says broad_paid_launch_ready=false.'
        )


# ---------------------------------------------------------------------------
# 3. enterprise_procurement_ready must match final-readiness
# ---------------------------------------------------------------------------
def test_enterprise_procurement_consistent_with_final_readiness() -> None:
    """
    If final-readiness says enterprise_procurement_ready=true,
    services/api live_evidence must not say enterprise_procurement_ready=false.
    """
    final = _load(FINAL_READINESS_PATH)
    if final is None:
        pytest.skip('final-readiness artifact not found')

    if not final.get('enterprise_procurement_ready'):
        return  # constraint not applicable

    api_ev = _load(API_LIVE_EVIDENCE_PATH)
    if api_ev is None:
        pytest.skip('services/api live_evidence summary not found')

    assert api_ev.get('enterprise_procurement_ready') is True, (
        'CONTRADICTION: final-readiness says enterprise_procurement_ready=true but '
        'services/api/artifacts/live_evidence/latest/summary.json says '
        'enterprise_procurement_ready=false. '
        'Regenerate from the live-evidence-proof chain.'
    )


# ---------------------------------------------------------------------------
# 4. services/api live_evidence must not be older than live-evidence-proof
# ---------------------------------------------------------------------------
def test_api_live_evidence_not_older_than_proof() -> None:
    """
    services/api/artifacts/live_evidence/latest/summary.json must not reference
    telemetry older than the latest in artifacts/live-evidence-proof/latest/.
    """
    proof = _load(LIVE_EVIDENCE_PROOF_PATH)
    if proof is None:
        pytest.skip('live-evidence-proof artifact not found')

    api_ev = _load(API_LIVE_EVIDENCE_PATH)
    if api_ev is None:
        pytest.skip('services/api live_evidence summary not found')

    lpe = proof.get('live_provider_evidence', {})
    proof_telemetry_at = _parse_dt(lpe.get('latest_live_telemetry_at'))
    api_telemetry_at = _parse_dt(api_ev.get('latest_live_telemetry_at'))

    if proof_telemetry_at is None or api_telemetry_at is None:
        pytest.skip('Cannot parse telemetry timestamps from artifacts')

    assert api_telemetry_at >= proof_telemetry_at, (
        f'STALE: services/api live_evidence latest telemetry is {api_telemetry_at.isoformat()} '
        f'but live-evidence-proof has fresher telemetry at {proof_telemetry_at.isoformat()}. '
        'Regenerate services/api/artifacts/live_evidence/latest/summary.json from '
        'the live-evidence-proof chain.'
    )


# ---------------------------------------------------------------------------
# 5. No April 2026 telemetry may remain as the latest live evidence
# ---------------------------------------------------------------------------
def test_no_stale_april_2026_telemetry_in_api_live_evidence() -> None:
    """
    No latest artifact may still reference April 2026 telemetry when
    June 2026 live evidence exists.
    """
    api_ev = _load(API_LIVE_EVIDENCE_PATH)
    if api_ev is None:
        pytest.skip('services/api live_evidence summary not found')

    latest_telemetry = str(api_ev.get('latest_live_telemetry_at', ''))
    assert '2026-04' not in latest_telemetry, (
        f'STALE: services/api live_evidence still references April 2026 telemetry '
        f'({latest_telemetry}) as latest evidence. '
        'Regenerate from the current live-evidence-proof chain.'
    )

    freshness = api_ev.get('live_evidence_freshness_check', {})
    stale_ts = str(freshness.get('latest_live_telemetry_at', ''))
    assert '2026-04' not in stale_ts, (
        f'STALE: live_evidence_freshness_check still references April 2026 telemetry '
        f'({stale_ts}). Regenerate from the current live-evidence-proof chain.'
    )
