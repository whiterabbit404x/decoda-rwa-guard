"""
Proof consistency tests.

Verifies that proof artifacts agree across final-readiness, ci-required-gates,
live-evidence-proof, and the live_evidence summary.  All tests must fail when
artifacts contain contradictions or overclaims.

Rules enforced:
  - frontend_build not_run → production_100_percent_ready must be False
  - readiness_validation not_run → production_100_percent_ready must be False
  - stale live telemetry → live_evidence_ready=False; broad_paid_saas_ready=False
  - enterprise_procurement_ready consistent across all artifacts
  - on-disk artifacts must not contradict each other
"""
from __future__ import annotations

import json
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT))

from scripts.validate_100_percent_readiness import (
    LIVE_EVIDENCE_FRESHNESS_WINDOW_DAYS,
    _check_telemetry_freshness,
    build_final_readiness,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ts(offset_days: int = 0) -> str:
    return (datetime.now(timezone.utc) + timedelta(days=offset_days)).isoformat()


def _write_launch_proof(tmp_path: Path, **overrides: Any) -> Path:
    d = tmp_path / 'launch-proof' / 'latest'
    d.mkdir(parents=True, exist_ok=True)
    proof: dict[str, Any] = {
        'schema_version': 1,
        'generated_at': _ts(),
        'launch_mode': 'paid_saas',
        'proof_mode': 'staging',
        'pilot_ready': True,
        'paid_launch_ready': True,
        'controlled_pilot_ready': True,
        'broad_paid_saas_ready': True,
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
    if 'readiness' in overrides:
        base = {
            'billing_ready': True, 'billing_webhook_ready': True,
            'email_ready': True, 'provider_ready': True,
            'live_evidence_ready': True, 'ci_required_gates_ready': True,
        }
        base.update(overrides['readiness'])
        proof['readiness'] = base
    (d / 'summary.json').write_text(json.dumps(proof))
    return d


def _write_release_proof(tmp_path: Path, **overrides: Any) -> Path:
    d = tmp_path / 'release-proof' / 'latest'
    d.mkdir(parents=True, exist_ok=True)
    proof: dict[str, Any] = {
        'schema_version': 1,
        'generated_at': _ts(),
        'release_status': 'pass',
        'release_channel': 'staging',
        'commit_sha': 'abc123',
        'branch': 'main',
        'ci_required_gates_ready': True,
        'launch_proof_ready': True,
        'manifest_ready': True,
        'test_report_ready': True,
        'paid_launch_ready': False,
        'blockers': [],
        'warnings': [],
    }
    proof.update(overrides)
    (d / 'summary.json').write_text(json.dumps(proof))
    return d


def _write_ci_gates(
    tmp_path: Path,
    frontend_build_status: str = 'pass',
    readiness_validation_status: str = 'pass',
    **overrides: Any,
) -> None:
    d = tmp_path / 'release-proof' / 'latest'
    d.mkdir(parents=True, exist_ok=True)
    gates: dict[str, Any] = {
        'schema_version': 1,
        'generated_at': _ts(),
        'commit_sha': 'abc123',
        'branch': 'main',
        'release_channel': 'staging',
        'overall_status': 'pass',
        'broad_paid_launch_ready': False,
        'required_gates': {
            'backend_tests': {'status': 'pass'},
            'saas_workflow_validation': {'status': 'pass'},
            'readiness_validation': {'status': readiness_validation_status},
            'paid_launch_readiness': {'status': 'pass', 'blockers': []},
            'live_evidence': {'status': 'pass', 'blockers': []},
            'frontend_build': {'status': frontend_build_status},
        },
        'blockers': [],
        'warnings': [],
    }
    gates.update(overrides)
    if 'required_gates' in overrides:
        base = {
            'backend_tests': {'status': 'pass'},
            'saas_workflow_validation': {'status': 'pass'},
            'readiness_validation': {'status': readiness_validation_status},
            'paid_launch_readiness': {'status': 'pass', 'blockers': []},
            'live_evidence': {'status': 'pass', 'blockers': []},
            'frontend_build': {'status': frontend_build_status},
        }
        base.update(overrides['required_gates'])
        gates['required_gates'] = base
    (d / 'ci-required-gates.json').write_text(json.dumps(gates))


def _write_staging_proof(tmp_path: Path, *, staging_launch_ready: bool = True) -> Path:
    d = tmp_path / 'staging-proof' / 'latest'
    d.mkdir(parents=True, exist_ok=True)
    proof: dict[str, Any] = {
        'schema_version': 1,
        'generated_at': _ts(),
        'mode': 'staging',
        'staging_launch_ready': staging_launch_ready,
        'blockers': [] if staging_launch_ready else ['staging_runtime_reachable=false'],
        'warnings': [],
    }
    (d / 'summary.json').write_text(json.dumps(proof))
    return d


def _write_live_evidence_proof(
    tmp_path: Path,
    *,
    live_evidence_ready: bool = True,
    telemetry_age_days: int = 0,
) -> Path:
    d = tmp_path / 'live-evidence-proof' / 'latest'
    d.mkdir(parents=True, exist_ok=True)
    now_str = _ts()
    telemetry_str = _ts(-telemetry_age_days)
    tid = str(uuid.uuid4())
    did = str(uuid.uuid4())
    aid = str(uuid.uuid4())
    iid = str(uuid.uuid4())
    raid = str(uuid.uuid4())
    pid = str(uuid.uuid4())
    proof: dict[str, Any] = {
        'schema_version': 1,
        'generated_at': now_str,
        'live_provider_evidence': {
            'provider_ready': live_evidence_ready,
            'provider_mode': 'live' if live_evidence_ready else 'disabled',
            'provider_health_checked': live_evidence_ready,
            'evidence_source': 'live' if live_evidence_ready else 'unknown',
            'latest_live_telemetry_at': telemetry_str if live_evidence_ready else None,
            'live_evidence_ready': live_evidence_ready,
            'chain': {
                'telemetry_event_id': tid if live_evidence_ready else None,
                'detection_id': did if live_evidence_ready else None,
                'alert_id': aid if live_evidence_ready else None,
                'incident_id': iid if live_evidence_ready else None,
                'response_action_id': raid if live_evidence_ready else None,
                'evidence_package_id': pid if live_evidence_ready else None,
            },
            'missing': [] if live_evidence_ready else ['EVM_RPC_URL not configured'],
            'contradiction_flags': [],
        },
    }
    (d / 'summary.json').write_text(json.dumps(proof))
    return d


def _write_stale_live_evidence_proof(tmp_path: Path, age_days: int = 43) -> Path:
    """Write a live-evidence-proof with live_evidence_ready=True but stale telemetry."""
    d = tmp_path / 'live-evidence-proof' / 'latest'
    d.mkdir(parents=True, exist_ok=True)
    now_str = _ts()
    stale_str = _ts(-age_days)
    tid = str(uuid.uuid4())
    did = str(uuid.uuid4())
    aid = str(uuid.uuid4())
    iid = str(uuid.uuid4())
    raid = str(uuid.uuid4())
    pid = str(uuid.uuid4())
    proof: dict[str, Any] = {
        'schema_version': 1,
        'generated_at': now_str,
        'live_provider_evidence': {
            'provider_ready': True,
            'provider_mode': 'live',
            'provider_health_checked': True,
            'evidence_source': 'live',
            'latest_live_telemetry_at': stale_str,
            'live_evidence_ready': True,
            'chain': {
                'telemetry_event_id': tid,
                'detection_id': did,
                'alert_id': aid,
                'incident_id': iid,
                'response_action_id': raid,
                'evidence_package_id': pid,
            },
            'missing': [],
            'contradiction_flags': [],
        },
    }
    (d / 'summary.json').write_text(json.dumps(proof))
    return d


# ---------------------------------------------------------------------------
# Unit tests for _check_telemetry_freshness
# ---------------------------------------------------------------------------

def test_freshness_helper_passes_when_fresh() -> None:
    now = datetime.now(timezone.utc)
    recent = (now - timedelta(days=5)).isoformat()
    ok, blockers = _check_telemetry_freshness(recent, now.isoformat(), window_days=30)
    assert ok is True
    assert blockers == []


def test_freshness_helper_fails_when_stale() -> None:
    now = datetime.now(timezone.utc)
    stale = (now - timedelta(days=43)).isoformat()
    ok, blockers = _check_telemetry_freshness(stale, now.isoformat(), window_days=30)
    assert ok is False
    assert len(blockers) == 1
    assert 'stale' in blockers[0]
    assert '43' in blockers[0]


def test_freshness_helper_fails_when_telemetry_missing() -> None:
    ok, blockers = _check_telemetry_freshness(None, _ts(), window_days=30)
    assert ok is False
    assert blockers


def test_freshness_helper_exact_boundary_passes() -> None:
    now = datetime.now(timezone.utc)
    boundary = (now - timedelta(days=30)).isoformat()
    ok, _ = _check_telemetry_freshness(boundary, now.isoformat(), window_days=30)
    assert ok is True  # 30 days is not > 30


def test_freshness_helper_one_over_boundary_fails() -> None:
    now = datetime.now(timezone.utc)
    over = (now - timedelta(days=31)).isoformat()
    ok, _ = _check_telemetry_freshness(over, now.isoformat(), window_days=30)
    assert ok is False


# ---------------------------------------------------------------------------
# Test 1: frontend_build not_run blocks production_100_percent_ready
# ---------------------------------------------------------------------------

def test_frontend_build_not_run_blocks_production_ready(tmp_path: Path) -> None:
    lp_dir = _write_launch_proof(tmp_path)
    rp_dir = _write_release_proof(tmp_path)
    _write_ci_gates(tmp_path, frontend_build_status='not_run', readiness_validation_status='pass')
    lep_dir = _write_live_evidence_proof(tmp_path, live_evidence_ready=True)
    sp_dir = _write_staging_proof(tmp_path, staging_launch_ready=True)

    result = build_final_readiness(
        mode='staging',
        strict=True,
        launch_proof_dir=lp_dir,
        release_proof_dir=rp_dir,
        staging_proof_dir=sp_dir,
        live_evidence_proof_dir=lep_dir,
    )
    assert result['production_100_percent_ready'] is False, (
        'production_100_percent_ready must be False when frontend_build=not_run'
    )
    assert any('frontend_build' in b for b in result['blockers']), (
        f'Expected frontend_build blocker, got: {result["blockers"]}'
    )


def test_frontend_build_not_run_blocks_broad_paid_saas(tmp_path: Path) -> None:
    lp_dir = _write_launch_proof(tmp_path)
    rp_dir = _write_release_proof(tmp_path)
    _write_ci_gates(tmp_path, frontend_build_status='not_run', readiness_validation_status='pass')
    lep_dir = _write_live_evidence_proof(tmp_path, live_evidence_ready=True)
    sp_dir = _write_staging_proof(tmp_path, staging_launch_ready=True)

    result = build_final_readiness(
        mode='staging',
        strict=True,
        launch_proof_dir=lp_dir,
        release_proof_dir=rp_dir,
        staging_proof_dir=sp_dir,
        live_evidence_proof_dir=lep_dir,
    )
    assert result['broad_paid_saas_ready'] is False, (
        'broad_paid_saas_ready must be False when frontend_build=not_run'
    )


# ---------------------------------------------------------------------------
# Test 2: readiness_validation not_run blocks production_100_percent_ready
# ---------------------------------------------------------------------------

def test_readiness_validation_not_run_blocks_production_ready(tmp_path: Path) -> None:
    lp_dir = _write_launch_proof(tmp_path)
    rp_dir = _write_release_proof(tmp_path)
    _write_ci_gates(tmp_path, frontend_build_status='pass', readiness_validation_status='not_run')
    lep_dir = _write_live_evidence_proof(tmp_path, live_evidence_ready=True)
    sp_dir = _write_staging_proof(tmp_path, staging_launch_ready=True)

    result = build_final_readiness(
        mode='staging',
        strict=True,
        launch_proof_dir=lp_dir,
        release_proof_dir=rp_dir,
        staging_proof_dir=sp_dir,
        live_evidence_proof_dir=lep_dir,
    )
    assert result['production_100_percent_ready'] is False, (
        'production_100_percent_ready must be False when readiness_validation=not_run'
    )
    assert any('readiness_validation' in b for b in result['blockers']), (
        f'Expected readiness_validation blocker, got: {result["blockers"]}'
    )


def test_readiness_validation_not_run_blocks_broad_paid_saas(tmp_path: Path) -> None:
    lp_dir = _write_launch_proof(tmp_path)
    rp_dir = _write_release_proof(tmp_path)
    _write_ci_gates(tmp_path, frontend_build_status='pass', readiness_validation_status='not_run')
    lep_dir = _write_live_evidence_proof(tmp_path, live_evidence_ready=True)
    sp_dir = _write_staging_proof(tmp_path, staging_launch_ready=True)

    result = build_final_readiness(
        mode='staging',
        strict=True,
        launch_proof_dir=lp_dir,
        release_proof_dir=rp_dir,
        staging_proof_dir=sp_dir,
        live_evidence_proof_dir=lep_dir,
    )
    assert result['broad_paid_saas_ready'] is False, (
        'broad_paid_saas_ready must be False when readiness_validation=not_run'
    )


# ---------------------------------------------------------------------------
# Test 3: both gates not_run
# ---------------------------------------------------------------------------

def test_both_gates_not_run_blocks_enterprise_ready(tmp_path: Path) -> None:
    lp_dir = _write_launch_proof(tmp_path)
    rp_dir = _write_release_proof(tmp_path)
    _write_ci_gates(tmp_path, frontend_build_status='not_run', readiness_validation_status='not_run')
    lep_dir = _write_live_evidence_proof(tmp_path, live_evidence_ready=True)
    sp_dir = _write_staging_proof(tmp_path, staging_launch_ready=True)

    result = build_final_readiness(
        mode='staging',
        strict=True,
        launch_proof_dir=lp_dir,
        release_proof_dir=rp_dir,
        staging_proof_dir=sp_dir,
        live_evidence_proof_dir=lep_dir,
    )
    assert result['enterprise_procurement_ready'] is False, (
        'enterprise_procurement_ready must be False when frontend_build and readiness_validation not_run'
    )
    assert result['safe_to_sell_broadly_today'] is False


# ---------------------------------------------------------------------------
# Test 4: stale live telemetry fails freshness gate
# ---------------------------------------------------------------------------

def test_stale_telemetry_fails_live_evidence(tmp_path: Path) -> None:
    lp_dir = _write_launch_proof(tmp_path)
    rp_dir = _write_release_proof(tmp_path)
    _write_ci_gates(tmp_path, frontend_build_status='pass', readiness_validation_status='pass')
    sp_dir = _write_staging_proof(tmp_path, staging_launch_ready=True)
    # live-evidence-proof claims live_evidence_ready=True but telemetry is 43 days old
    lep_dir = _write_stale_live_evidence_proof(tmp_path, age_days=43)

    result = build_final_readiness(
        mode='staging',
        strict=True,
        launch_proof_dir=lp_dir,
        release_proof_dir=rp_dir,
        staging_proof_dir=sp_dir,
        live_evidence_proof_dir=lep_dir,
    )
    assert result['broad_paid_saas_ready'] is False, (
        'broad_paid_saas_ready must be False when live telemetry is stale'
    )
    assert result['production_100_percent_ready'] is False
    stale_blockers = [b for b in result['blockers'] if 'stale' in b or 'fresh' in b.lower()]
    assert stale_blockers, (
        f'Expected staleness blocker, got: {result["blockers"]}'
    )


def test_stale_telemetry_explicitly_false_fails(tmp_path: Path) -> None:
    """live_evidence_ready=False in proof with staleness reason also blocks."""
    lp_dir = _write_launch_proof(tmp_path, readiness={'live_evidence_ready': False})
    rp_dir = _write_release_proof(tmp_path)
    _write_ci_gates(tmp_path, frontend_build_status='pass', readiness_validation_status='pass')
    sp_dir = _write_staging_proof(tmp_path, staging_launch_ready=True)
    # live-evidence-proof says live_evidence_ready=False explicitly
    lep_dir = _write_live_evidence_proof(tmp_path, live_evidence_ready=False)

    result = build_final_readiness(
        mode='staging',
        strict=True,
        launch_proof_dir=lp_dir,
        release_proof_dir=rp_dir,
        staging_proof_dir=sp_dir,
        live_evidence_proof_dir=lep_dir,
    )
    assert result['broad_paid_saas_ready'] is False
    assert result['enterprise_procurement_ready'] is False
    assert any('live evidence' in b for b in result['blockers'])


def test_fresh_telemetry_passes_within_window(tmp_path: Path) -> None:
    """Fresh telemetry within window does not add a staleness blocker."""
    lp_dir = _write_launch_proof(tmp_path)
    rp_dir = _write_release_proof(tmp_path)
    _write_ci_gates(tmp_path, frontend_build_status='pass', readiness_validation_status='pass')
    sp_dir = _write_staging_proof(tmp_path, staging_launch_ready=True)
    lep_dir = _write_live_evidence_proof(tmp_path, live_evidence_ready=True, telemetry_age_days=5)

    result = build_final_readiness(
        mode='staging',
        strict=True,
        launch_proof_dir=lp_dir,
        release_proof_dir=rp_dir,
        staging_proof_dir=sp_dir,
        live_evidence_proof_dir=lep_dir,
    )
    stale_blockers = [b for b in result['blockers'] if 'stale' in b or 'fresh' in b.lower()]
    assert not stale_blockers, (
        f'Unexpected staleness blocker with fresh (5 day) telemetry: {result["blockers"]}'
    )


# ---------------------------------------------------------------------------
# Test 5: enterprise ready agreement across artifacts
# ---------------------------------------------------------------------------

def test_enterprise_ready_requires_live_evidence_and_gates(tmp_path: Path) -> None:
    """enterprise_procurement_ready=True requires live evidence AND all gates to pass."""
    lp_dir = _write_launch_proof(tmp_path)
    rp_dir = _write_release_proof(tmp_path)
    # Both gates not_run — should block enterprise
    _write_ci_gates(tmp_path, frontend_build_status='not_run', readiness_validation_status='not_run')
    lep_dir = _write_live_evidence_proof(tmp_path, live_evidence_ready=True)
    sp_dir = _write_staging_proof(tmp_path, staging_launch_ready=True)

    result = build_final_readiness(
        mode='staging',
        strict=True,
        launch_proof_dir=lp_dir,
        release_proof_dir=rp_dir,
        staging_proof_dir=sp_dir,
        live_evidence_proof_dir=lep_dir,
    )
    assert result['enterprise_procurement_ready'] is False


def test_enterprise_and_production_ready_require_all_gates(tmp_path: Path) -> None:
    """Neither enterprise_procurement_ready nor production_100_percent_ready can be True with not_run gates."""
    lp_dir = _write_launch_proof(tmp_path)
    rp_dir = _write_release_proof(tmp_path)
    _write_ci_gates(tmp_path, frontend_build_status='not_run', readiness_validation_status='not_run')
    lep_dir = _write_live_evidence_proof(tmp_path, live_evidence_ready=True)
    sp_dir = _write_staging_proof(tmp_path, staging_launch_ready=True)

    result = build_final_readiness(
        mode='staging',
        strict=True,
        launch_proof_dir=lp_dir,
        release_proof_dir=rp_dir,
        staging_proof_dir=sp_dir,
        live_evidence_proof_dir=lep_dir,
    )
    # If production_100_percent_ready=True, then frontend_build/readiness_validation must NOT be not_run
    if result['production_100_percent_ready']:
        assert result['required_gates']['frontend_build']['status'] not in ('not_run',)
        assert result['required_gates']['readiness_validation']['status'] not in ('not_run',)
    # In this test they ARE not_run → production must be False
    assert result['production_100_percent_ready'] is False
    assert result['enterprise_procurement_ready'] is False


# ---------------------------------------------------------------------------
# Test 6: on-disk artifact consistency check
# ---------------------------------------------------------------------------

def test_on_disk_final_readiness_consistent_with_ci_gates() -> None:
    """
    On-disk final-readiness must not claim production/enterprise/broad ready
    when ci-required-gates has frontend_build=not_run or readiness_validation=not_run.
    """
    final_readiness_path = REPO_ROOT / 'artifacts' / 'final-readiness' / 'latest' / 'summary.json'
    ci_gates_path = REPO_ROOT / 'artifacts' / 'release-proof' / 'latest' / 'ci-required-gates.json'

    if not final_readiness_path.exists() or not ci_gates_path.exists():
        pytest.skip('on-disk artifacts not present')

    with open(final_readiness_path) as f:
        final = json.load(f)
    with open(ci_gates_path) as f:
        gates = json.load(f)

    rg = gates.get('required_gates', {})
    fb_status = rg.get('frontend_build', {}).get('status', 'not_run')
    rv_status = rg.get('readiness_validation', {}).get('status', 'not_run')

    if fb_status == 'not_run' or rv_status == 'not_run':
        assert final.get('production_100_percent_ready') is not True, (
            f'final-readiness says production_100_percent_ready=True '
            f'but ci-required-gates has frontend_build={fb_status!r}, '
            f'readiness_validation={rv_status!r}'
        )
        assert final.get('broad_paid_saas_ready') is not True, (
            f'final-readiness says broad_paid_saas_ready=True '
            f'but ci-required-gates has unrun gates: '
            f'frontend_build={fb_status!r}, readiness_validation={rv_status!r}'
        )


def test_on_disk_final_readiness_consistent_with_live_evidence() -> None:
    """
    On-disk final-readiness enterprise_procurement_ready must agree with
    services/api/artifacts/live_evidence/latest/summary.json.
    """
    final_path = REPO_ROOT / 'artifacts' / 'final-readiness' / 'latest' / 'summary.json'
    live_ev_path = (
        REPO_ROOT / 'services' / 'api' / 'artifacts' / 'live_evidence' / 'latest' / 'summary.json'
    )

    if not final_path.exists() or not live_ev_path.exists():
        pytest.skip('on-disk artifacts not present')

    with open(final_path) as f:
        final = json.load(f)
    with open(live_ev_path) as f:
        live_ev = json.load(f)

    final_enterprise = final.get('enterprise_procurement_ready')
    live_enterprise = live_ev.get('enterprise_procurement_ready')

    if final_enterprise is True and live_enterprise is False:
        raise AssertionError(
            'CONTRADICTION: final-readiness says enterprise_procurement_ready=True '
            'but services/api/artifacts/live_evidence/latest/summary.json says False; '
            'regenerate live_evidence summary from current billing/email/live evidence proof'
        )


def test_on_disk_live_evidence_not_stale_when_enterprise_claimed() -> None:
    """
    If final-readiness claims enterprise_procurement_ready=True, the
    live_evidence must have a fresh telemetry timestamp (within 30 days).
    """
    final_path = REPO_ROOT / 'artifacts' / 'final-readiness' / 'latest' / 'summary.json'
    live_ev_proof_path = (
        REPO_ROOT / 'artifacts' / 'live-evidence-proof' / 'latest' / 'summary.json'
    )

    if not final_path.exists() or not live_ev_proof_path.exists():
        pytest.skip('on-disk artifacts not present')

    with open(final_path) as f:
        final = json.load(f)

    if not final.get('enterprise_procurement_ready'):
        return  # nothing to check if not claiming enterprise ready

    with open(live_ev_proof_path) as f:
        lep = json.load(f)

    lpe = lep.get('live_provider_evidence', {})
    telemetry_at = lpe.get('latest_live_telemetry_at')
    generated_at = lep.get('generated_at')

    ok, blockers = _check_telemetry_freshness(telemetry_at, generated_at)
    assert ok, (
        f'final-readiness claims enterprise_procurement_ready=True '
        f'but live-evidence-proof telemetry is stale: {blockers}'
    )


# ---------------------------------------------------------------------------
# Cross-artifact consistency tests (Task — final-readiness must be authoritative)
#
# These tests fail if any "latest" summary contradicts final-readiness.
# ---------------------------------------------------------------------------

_FINAL_READINESS_PATH = REPO_ROOT / 'artifacts' / 'final-readiness' / 'latest' / 'summary.json'
_LAUNCH_PROOF_PATH = REPO_ROOT / 'artifacts' / 'launch-proof' / 'latest' / 'summary.json'
_CI_GATES_PATH = REPO_ROOT / 'artifacts' / 'release-proof' / 'latest' / 'ci-required-gates.json'
_SVC_LIVE_EVIDENCE_PATH = (
    REPO_ROOT / 'services' / 'api' / 'artifacts' / 'live_evidence' / 'latest' / 'summary.json'
)
_LIVE_EVIDENCE_PROOF_PATH = (
    REPO_ROOT / 'artifacts' / 'live-evidence-proof' / 'latest' / 'summary.json'
)


def _load_artifact(path: Path) -> dict | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except Exception:
        return None


def test_cross_artifact_launch_proof_consistent_with_final_readiness() -> None:
    """
    If final-readiness says broad_paid_saas_ready=True, launch-proof must
    also say broad_paid_saas_ready=True and paid_launch_ready=True.
    """
    final = _load_artifact(_FINAL_READINESS_PATH)
    launch = _load_artifact(_LAUNCH_PROOF_PATH)
    if final is None or launch is None:
        pytest.skip('on-disk artifacts not present')

    if not final.get('broad_paid_saas_ready'):
        return  # final says not ready — no contradiction to check

    assert launch.get('broad_paid_saas_ready') is True, (
        'CONTRADICTION: final-readiness says broad_paid_saas_ready=True '
        'but launch-proof/latest/summary.json says broad_paid_saas_ready=False; '
        'regenerate launch-proof with proof_mode=staging'
    )
    assert launch.get('paid_launch_ready') is True, (
        'CONTRADICTION: final-readiness says broad_paid_saas_ready=True '
        'but launch-proof/latest/summary.json says paid_launch_ready=False; '
        'regenerate launch-proof with proof_mode=staging'
    )


def test_cross_artifact_ci_gates_consistent_with_final_readiness() -> None:
    """
    If final-readiness says broad_paid_saas_ready=True, ci-required-gates
    must say broad_paid_launch_ready=True.
    """
    final = _load_artifact(_FINAL_READINESS_PATH)
    gates = _load_artifact(_CI_GATES_PATH)
    if final is None or gates is None:
        pytest.skip('on-disk artifacts not present')

    if not final.get('broad_paid_saas_ready'):
        return  # final says not ready — no contradiction to check

    assert gates.get('broad_paid_launch_ready') is True, (
        'CONTRADICTION: final-readiness says broad_paid_saas_ready=True '
        'but release-proof/latest/ci-required-gates.json says broad_paid_launch_ready=False; '
        'regenerate ci-required-gates with staging release_channel and all gates passing'
    )


def test_cross_artifact_svc_live_evidence_consistent_with_final_readiness() -> None:
    """
    services/api/artifacts/live_evidence/latest/summary.json must agree with
    final-readiness on enterprise_procurement_ready. Stale April telemetry must
    not be present as the latest live evidence when final-readiness claims enterprise ready.
    """
    final = _load_artifact(_FINAL_READINESS_PATH)
    svc = _load_artifact(_SVC_LIVE_EVIDENCE_PATH)
    if final is None or svc is None:
        pytest.skip('on-disk artifacts not present')

    if not final.get('enterprise_procurement_ready'):
        return  # not claiming enterprise ready — nothing to contradict

    assert svc.get('enterprise_procurement_ready') is True, (
        'CONTRADICTION: final-readiness says enterprise_procurement_ready=True '
        'but services/api/artifacts/live_evidence/latest/summary.json says False; '
        'regenerate services live_evidence summary from the current live-evidence-proof chain'
    )
    assert svc.get('live_evidence_ready') is True, (
        'CONTRADICTION: final-readiness says enterprise_procurement_ready=True '
        'but services/api/artifacts/live_evidence/latest/summary.json says live_evidence_ready=False'
    )


def test_cross_artifact_no_stale_april_telemetry_in_any_latest_summary() -> None:
    """
    No "latest" artifact summary may reference April 2026 telemetry as the
    current live evidence when final-readiness claims enterprise/broad ready.

    Stale April 2026 timestamp: 2026-04-22T* must not appear in latest_live_telemetry_at
    of any proof artifact when the system claims to be enterprise-ready.
    """
    final = _load_artifact(_FINAL_READINESS_PATH)
    if final is None:
        pytest.skip('final-readiness artifact not present')

    if not final.get('enterprise_procurement_ready'):
        return

    stale_month_prefix = '2026-04-'

    for name, path in [
        ('live-evidence-proof', _LIVE_EVIDENCE_PROOF_PATH),
        ('services/api live_evidence', _SVC_LIVE_EVIDENCE_PATH),
    ]:
        artifact = _load_artifact(path)
        if artifact is None:
            continue

        # Check top-level and nested telemetry timestamps
        top_ts = artifact.get('latest_live_telemetry_at', '')
        lpe_ts = (artifact.get('live_provider_evidence') or {}).get('latest_live_telemetry_at', '')
        for ts_value in (top_ts, lpe_ts):
            if ts_value and str(ts_value).startswith(stale_month_prefix):
                raise AssertionError(
                    f'STALE TELEMETRY: {name} has latest_live_telemetry_at={ts_value!r} '
                    f'which is April 2026 data used as current live evidence '
                    f'while final-readiness claims enterprise_procurement_ready=True; '
                    f'regenerate from fresh June 2026 live RPC evidence'
                )


def test_cross_artifact_launch_proof_has_no_local_mode_blocker_when_broad_ready() -> None:
    """
    When final-readiness says broad_paid_saas_ready=True, launch-proof must not
    contain a 'local mode' blocker — that would contradict the staging proof mode.
    """
    final = _load_artifact(_FINAL_READINESS_PATH)
    launch = _load_artifact(_LAUNCH_PROOF_PATH)
    if final is None or launch is None:
        pytest.skip('on-disk artifacts not present')

    if not final.get('broad_paid_saas_ready'):
        return

    blockers = launch.get('blockers', [])
    local_mode_blockers = [b for b in blockers if 'local mode' in str(b).lower()]
    assert not local_mode_blockers, (
        f'CONTRADICTION: final-readiness says broad_paid_saas_ready=True '
        f'but launch-proof has local-mode blockers: {local_mode_blockers}; '
        f'regenerate launch-proof with proof_mode=staging'
    )
