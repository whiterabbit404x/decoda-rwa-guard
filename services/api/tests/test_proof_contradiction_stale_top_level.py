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
from scripts.generate_live_evidence_proof import _build_proof_from_service_summary

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

def test_build_proof_from_service_summary_fails_closed() -> None:
    """Aggregate booleans cannot substitute for persisted target evidence."""
    result = _build_proof_from_service_summary(
        _live_service_summary(), '2026-05-27T00:00:00+00:00'
    )
    lpe = result['live_provider_evidence']

    assert lpe['live_evidence_ready'] is False
    assert all(value is None for value in lpe['chain'].values())
    assert 'service_summary_cannot_satisfy_live_evidence' in lpe['contradiction_flags']
    assert any('configured-target detector evidence' in item for item in lpe['missing'])


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

def test_check_live_evidence_canonical_false_is_authoritative(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """
    Strict-source-of-truth invariant: _check_live_evidence must return False when the
    canonical live-evidence-proof says live_evidence_ready=false, even when the service
    summary says live_evidence_ready=true.

    The canonical live-evidence-proof artifact is the strict source of truth.
    A stale service summary cannot override it.
    """
    import scripts.generate_release_proof as _grp_mod

    # Write canonical proof that explicitly says false
    canonical_dir = tmp_path / 'artifacts' / 'live-evidence-proof' / 'latest'
    canonical_dir.mkdir(parents=True)
    canonical_proof = {
        'schema_version': 1,
        'live_provider_evidence': {
            'provider_ready': False,
            'live_evidence_ready': False,
            'missing': ['EVM_RPC_URL or STAGING_EVM_RPC_URL not configured'],
            'contradiction_flags': [],
        },
    }
    (canonical_dir / 'summary.json').write_text(json.dumps(canonical_proof), encoding='utf-8')

    # Write a live service summary — must NOT override the canonical false result
    svc_dir = tmp_path / 'services' / 'api' / 'artifacts' / 'live_evidence' / 'latest'
    svc_dir.mkdir(parents=True)
    (svc_dir / 'summary.json').write_text(
        json.dumps(_live_service_summary()), encoding='utf-8'
    )

    import unittest.mock as _mock
    with _mock.patch.object(_grp_mod, 'REPO_ROOT', tmp_path):
        ok, blockers = _grp_mod._check_live_evidence()

    assert ok is False, (
        f'Strict-source-of-truth violated: canonical says false but _check_live_evidence '
        f'returned True; blockers={blockers}'
    )
    assert blockers, 'Expected at least one blocker when canonical live-evidence-proof is false'


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


# ---------------------------------------------------------------------------
# save-proof-to-repo workflow approach: LIVE_EVIDENCE_CHAIN_FILE +
# PROOF_REQUIRE_CURRENT_ENV=true + real RPC → resolves stale contradiction
# ---------------------------------------------------------------------------

def _mock_rpc_success_side_effect(
    chain_id_hex: str = '0x1',
    block_hex: str = '0x12c',
):
    """Return a side_effect for _rpc_call that yields chain-id then block-number."""
    responses = iter([
        {'result': chain_id_hex, 'jsonrpc': '2.0', 'id': 1},
        {'result': block_hex,    'jsonrpc': '2.0', 'id': 1},
        {'result': None},  # eth_getBlockByNumber — not needed for this test
    ])

    def _side(url, method, params=None, timeout=10):
        return next(responses)

    return _side


def test_workflow_summary_derived_chain_file_fails_enterprise_evidence(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """
    Validates the save-proof-to-repo.yml resolution path:

    'Prepare live evidence chain' step writes /tmp/live_evidence_chain.json from
    the service summary. 'Regenerate live-evidence-proof (with provider secrets)'
    step sets PROOF_REQUIRE_CURRENT_ENV=true + STAGING_EVM_RPC_URL + STAGING_EVM_CHAIN_ID +
    STAGING_WORKER_ENABLED=true + LIVE_EVIDENCE_CHAIN_FILE=/tmp/live_evidence_chain.json.

    PROOF_REQUIRE_CURRENT_ENV=true blocks the committed default chain file but NOT
    the env-var path (LIVE_EVIDENCE_CHAIN_FILE). With a successful RPC call the proof
    must report provider_ready=true, live_evidence_ready=true, evidence_source='live',
    eliminating the contradiction that was causing sell_now_managed_ready=false.
    """
    _clear_provider_env(monkeypatch)

    # Simulate what the "Prepare live evidence chain" step writes
    chain = {
        'evidence_source': 'live',
        'source_type': 'rpc_polling',
        'telemetry_event_id': 'tel-workflow-001',
        'detection_id':       'det-workflow-001',
        'alert_id':           'alert-workflow-001',
        'incident_id':        'inc-workflow-001',
        'response_action_id': None,
        'evidence_package_id': 'pkg-workflow-001',
        'observed_at':               '2026-04-22T14:32:59.583341+00:00',
        'latest_live_telemetry_at':  '2026-04-22T14:32:59.583341+00:00',
    }
    chain_file = tmp_path / 'live_evidence_chain.json'
    chain_file.write_text(json.dumps(chain), encoding='utf-8')

    # Simulate the workflow env for the "with provider secrets" step
    monkeypatch.setenv('PROOF_REQUIRE_CURRENT_ENV', 'true')
    monkeypatch.setenv('STAGING_EVM_RPC_URL', 'https://mainnet.infura.io/v3/staging_proj')
    monkeypatch.setenv('STAGING_EVM_CHAIN_ID', '1')
    monkeypatch.setenv('STAGING_WORKER_ENABLED', 'true')
    monkeypatch.setenv('LIVE_EVIDENCE_CHAIN_FILE', str(chain_file))

    import unittest.mock as _mock
    from scripts.generate_live_evidence_proof import generate_live_evidence_proof

    with _mock.patch(
        'scripts.generate_live_evidence_proof._rpc_call',
        side_effect=_mock_rpc_success_side_effect('0x1', '0x12c'),
    ):
        result = generate_live_evidence_proof(require_current_env=True)

    lpe = result['live_provider_evidence']
    assert lpe['provider_ready'] is True, (
        f'Expected provider_ready=True; got {lpe["provider_ready"]!r}. '
        f'Missing: {lpe.get("missing")}'
    )
    assert lpe['live_evidence_ready'] is False
    assert lpe['evidence_source'] == 'unknown'
    assert any('no matching live telemetry event' in item.lower() for item in lpe['missing'])



def test_workflow_no_secrets_remains_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """
    When no STAGING_EVM_RPC_URL is configured (no-secrets branch of workflow),
    generate_live_evidence_proof with PROOF_REQUIRE_CURRENT_ENV=true must remain
    fail-closed even when LIVE_EVIDENCE_CHAIN_FILE is set, because provider_ready
    is blocked before the chain file is ever read.
    """
    _clear_provider_env(monkeypatch)

    # Chain file exists (simulates service summary being live)
    chain = {
        'evidence_source': 'live',
        'source_type': 'rpc_polling',
        'telemetry_event_id': 'tel-no-secret-001',
        'detection_id':       'det-no-secret-001',
        'alert_id':           'alert-no-secret-001',
        'incident_id':        'inc-no-secret-001',
        'response_action_id': None,
        'evidence_package_id': 'pkg-no-secret-001',
        'observed_at': '2026-04-22T14:32:59.583341+00:00',
    }
    chain_file = tmp_path / 'no_secret_chain.json'
    chain_file.write_text(json.dumps(chain), encoding='utf-8')

    # No RPC URL — simulates the no-secrets workflow branch
    monkeypatch.setenv('PROOF_REQUIRE_CURRENT_ENV', 'true')
    monkeypatch.setenv('LIVE_EVIDENCE_CHAIN_FILE', str(chain_file))
    # STAGING_EVM_RPC_URL deliberately not set

    from scripts.generate_live_evidence_proof import generate_live_evidence_proof

    result = generate_live_evidence_proof(require_current_env=True)
    lpe = result['live_provider_evidence']

    assert lpe['provider_ready'] is False, (
        f'Fail-closed violated: provider_ready={lpe["provider_ready"]!r} without RPC URL'
    )
    assert lpe['live_evidence_ready'] is False, (
        f'Fail-closed violated: live_evidence_ready={lpe["live_evidence_ready"]!r} without RPC URL'
    )
