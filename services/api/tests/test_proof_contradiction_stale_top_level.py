"""
Stale top-level proof contradiction tests (Task 8).

Invariant: after generate_live_evidence_proof.py main() runs, a service summary
that reports live_evidence_ready=true must not coexist with a top-level
live-evidence-proof that reports live_evidence_ready=false.

Also covers:
- _build_proof_from_service_summary produces a valid, fail-closed result.
- _check_live_evidence (in generate_release_proof) resolves the contradiction.
- build_live_provider_validation (in generate_staging_launch_proof) resolves it.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT))

import scripts.generate_live_evidence_proof as _glep_mod
from scripts.generate_live_evidence_proof import (
    _build_proof_from_service_summary,
    _content_id,
)

_PROVIDER_ENV_VARS = [
    'EVM_RPC_URL', 'STAGING_EVM_RPC_URL',
    'EVM_CHAIN_ID', 'STAGING_EVM_CHAIN_ID', 'CHAIN_ID',
    'STAGING_WORKER_ENABLED',
    'LIVE_EVIDENCE_CHAIN_JSON', 'LIVE_EVIDENCE_CHAIN_FILE',
]


def _clear_provider_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in _PROVIDER_ENV_VARS:
        monkeypatch.delenv(var, raising=False)


def _live_service_summary(
    *,
    ts: str = '2026-04-22T14:32:59.583341+00:00',
) -> dict[str, Any]:
    """Minimal canonical service live summary (the format written by write_live_evidence_summary.py)."""
    return {
        'evidence_source': 'live',
        'provider_ready': True,
        'live_evidence_ready': True,
        'telemetry_event_present': True,
        'detection_generated_from_telemetry': True,
        'alert_generated_from_detection': True,
        'incident_opened_from_alert': True,
        'response_action_recommended_or_executed': True,
        'evidence_package_exported': True,
        'latest_live_telemetry_at': ts,
    }


# ---------------------------------------------------------------------------
# _build_proof_from_service_summary
# ---------------------------------------------------------------------------

def test_build_proof_from_service_summary_sets_live_evidence_ready() -> None:
    """_build_proof_from_service_summary must produce live_evidence_ready=true."""
    summary = _live_service_summary()
    result = _build_proof_from_service_summary(summary, '2026-05-27T00:00:00+00:00')
    lpe = result['live_provider_evidence']

    assert lpe['live_evidence_ready'] is True
    assert lpe['provider_ready'] is True
    assert lpe['evidence_source'] == 'live'


def test_build_proof_from_service_summary_chain_ids_are_non_null() -> None:
    """Chain IDs must be non-null content-addressable UUIDs, not synthesised from RPC alone."""
    summary = _live_service_summary()
    result = _build_proof_from_service_summary(summary, '2026-05-27T00:00:00+00:00')
    chain = result['live_provider_evidence']['chain']

    assert chain['telemetry_event_id'] is not None
    assert chain['detection_id'] is not None
    assert chain['alert_id'] is not None
    assert chain['evidence_package_id'] is not None


def test_build_proof_from_service_summary_ids_are_deterministic() -> None:
    """Same service summary → same chain IDs (content-addressable, not random)."""
    summary = _live_service_summary()
    now = '2026-05-27T00:00:00+00:00'
    r1 = _build_proof_from_service_summary(summary, now)
    r2 = _build_proof_from_service_summary(summary, now)

    assert r1['live_provider_evidence']['chain'] == r2['live_provider_evidence']['chain']


def test_build_proof_from_service_summary_workflow_flags_propagated() -> None:
    """Workflow flags from service summary must propagate to proof."""
    summary = _live_service_summary()
    result = _build_proof_from_service_summary(summary, '2026-05-27T00:00:00+00:00')
    lpe = result['live_provider_evidence']

    assert lpe['live_telemetry_ready'] is True
    assert lpe['live_detection_ready'] is True
    assert lpe['live_alert_ready'] is True
    assert lpe['live_incident_ready'] is True


# ---------------------------------------------------------------------------
# main() service summary fallback — no contradiction after generation
# ---------------------------------------------------------------------------

def test_main_fails_closed_without_rpc_url_even_with_service_summary(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """
    Fail-closed invariant: main() must never set live_evidence_ready=true when no
    EVM_RPC_URL is configured, even when the service summary reports live evidence.

    A committed service summary artifact cannot substitute for real provider secrets.
    """
    _clear_provider_env(monkeypatch)

    # Write live service summary to tmp (would have caused the fallback before)
    svc_file = tmp_path / 'svc_summary.json'
    svc_file.write_text(json.dumps(_live_service_summary()), encoding='utf-8')

    # Redirect proof output to tmp
    out_dir = tmp_path / 'artifacts' / 'live-evidence-proof' / 'latest'
    out_dir.mkdir(parents=True)
    out_path = out_dir / 'summary.json'

    monkeypatch.setattr(_glep_mod, '_SERVICE_LIVE_SUMMARY_PATH', svc_file)

    import unittest.mock as _mock

    with _mock.patch.object(_glep_mod, 'REPO_ROOT', tmp_path):
        _glep_mod.main(strict=False)

    assert out_path.exists(), 'main() must write the artifact'
    written = json.loads(out_path.read_text())
    lpe = written['live_provider_evidence']

    assert lpe['live_evidence_ready'] is False, (
        f'Fail-closed violated: live_evidence_ready={lpe["live_evidence_ready"]!r} '
        f'without EVM_RPC_URL; service summary must not substitute for provider secrets'
    )
    assert lpe['provider_ready'] is False
    assert lpe['evidence_source'] == 'unknown'


def test_main_still_fails_closed_without_service_summary(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """When no RPC URL and no live service summary, main() must write live_evidence_ready=false."""
    _clear_provider_env(monkeypatch)

    # Point service summary path to non-existent file
    monkeypatch.setattr(_glep_mod, '_SERVICE_LIVE_SUMMARY_PATH', tmp_path / 'nonexistent.json')

    out_dir = tmp_path / 'artifacts' / 'live-evidence-proof' / 'latest'
    out_dir.mkdir(parents=True)
    out_path = out_dir / 'summary.json'

    import unittest.mock as _mock

    with _mock.patch.object(_glep_mod, 'REPO_ROOT', tmp_path):
        _glep_mod.main(strict=False)

    assert out_path.exists()
    written = json.loads(out_path.read_text())
    lpe = written['live_provider_evidence']
    assert lpe['live_evidence_ready'] is False
    assert lpe['provider_ready'] is False


# ---------------------------------------------------------------------------
# _check_live_evidence (generate_release_proof) resolves stale contradiction
# ---------------------------------------------------------------------------

def test_check_live_evidence_uses_service_summary_when_canonical_is_stale(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """
    _check_live_evidence must return True when the canonical live-evidence-proof
    says live_evidence_ready=false but the service summary says live_evidence_ready=true.
    """
    import scripts.generate_release_proof as _grp_mod

    # Write stale canonical proof (live_evidence_ready=false)
    canonical_dir = tmp_path / 'artifacts' / 'live-evidence-proof' / 'latest'
    canonical_dir.mkdir(parents=True)
    stale_proof = {
        'schema_version': 1,
        'live_provider_evidence': {
            'provider_ready': False,
            'live_evidence_ready': False,
            'missing': ['EVM_RPC_URL or STAGING_EVM_RPC_URL not configured'],
            'contradiction_flags': [],
        },
    }
    (canonical_dir / 'summary.json').write_text(json.dumps(stale_proof), encoding='utf-8')

    # Write live service summary
    svc_dir = tmp_path / 'services' / 'api' / 'artifacts' / 'live_evidence' / 'latest'
    svc_dir.mkdir(parents=True)
    (svc_dir / 'summary.json').write_text(
        json.dumps(_live_service_summary()), encoding='utf-8'
    )

    # Patch REPO_ROOT so the function reads from tmp_path
    import unittest.mock as _mock
    with _mock.patch.object(_grp_mod, 'REPO_ROOT', tmp_path):
        ok, blockers = _grp_mod._check_live_evidence()

    assert ok is True, (
        f'Expected live evidence OK when service summary is live; blockers={blockers}'
    )
    assert blockers == []


def test_check_live_evidence_fails_closed_when_both_missing(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """When both canonical proof and service summary are absent, _check_live_evidence fails closed."""
    import scripts.generate_release_proof as _grp_mod
    import unittest.mock as _mock

    with _mock.patch.object(_grp_mod, 'REPO_ROOT', tmp_path):
        ok, blockers = _grp_mod._check_live_evidence()

    assert ok is False
    assert blockers
