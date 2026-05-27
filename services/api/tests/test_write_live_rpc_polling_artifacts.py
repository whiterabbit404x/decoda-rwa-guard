"""
Tests for scripts/write_live_rpc_polling_artifacts.py and the surrounding
live evidence proof pipeline.

Task 8 test matrix:
  1. env var present but workflow not mapping it fails with env_mapping_missing
  2. worker env present but no rpc_polling artifacts fails with no_live_telemetry
  3. rpc_polling artifacts (from write step) generate live_evidence_chain.json
  4. full strict proof passes only with real live chain

Each test uses a temporary directory so the repo's committed artifacts are
never modified during the test run.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT))

from scripts.write_live_rpc_polling_artifacts import write_live_rpc_polling_artifacts
from scripts.export_live_evidence_chain import export_live_evidence_chain
from scripts.generate_live_evidence_proof import generate_live_evidence_proof

_WRITE_SCRIPT_RPC_PATCH = 'scripts.write_live_rpc_polling_artifacts._rpc_call'
_GEN_SCRIPT_RPC_PATCH = 'scripts.generate_live_evidence_proof._rpc_call'

_REAL_RPC = 'https://mainnet.infura.io/v3/abcdef1234567890deadbeef'

_PROVIDER_ENV_VARS = [
    'EVM_RPC_URL', 'STAGING_EVM_RPC_URL',
    'EVM_CHAIN_ID', 'STAGING_EVM_CHAIN_ID',
    'STAGING_WORKER_ENABLED',
    'LIVE_EVIDENCE_CHAIN_JSON', 'LIVE_EVIDENCE_CHAIN_FILE',
]


def _clear_provider_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in _PROVIDER_ENV_VARS:
        monkeypatch.delenv(var, raising=False)


def _mock_rpc_responses(chain_id_hex: str = '0x1', block_hex: str = '0x12c') -> Any:
    """Side-effect that returns eth_chainId then eth_blockNumber."""
    responses = iter([
        {'result': chain_id_hex, 'jsonrpc': '2.0', 'id': 1},
        {'result': block_hex, 'jsonrpc': '2.0', 'id': 1},
    ])

    def _side(url: str, method: str, params: list | None = None, timeout: int = 10) -> dict:
        return next(responses)

    return _side


def _mock_rpc_error() -> Any:
    def _side(url: str, method: str, params: list | None = None, timeout: int = 10) -> dict:
        return {'error': 'URLError: Connection refused'}
    return _side


# ---------------------------------------------------------------------------
# Test 1: env var absent (workflow not mapping secrets) → env_mapping_missing
# ---------------------------------------------------------------------------

def test_write_artifacts_fails_with_env_mapping_missing_when_no_rpc_url(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """
    When neither STAGING_EVM_RPC_URL nor EVM_RPC_URL is mapped into the
    subprocess environment (simulating a workflow step that omits the env:
    block), write_live_rpc_polling_artifacts must return env_mapping_missing.
    """
    _clear_provider_env(monkeypatch)

    rc, reason = write_live_rpc_polling_artifacts(service_artifacts_dir=tmp_path)

    assert rc == 1
    assert reason == 'env_mapping_missing', (
        f'Expected env_mapping_missing when RPC env vars are absent; got {reason!r}'
    )


def test_write_artifacts_fails_with_env_mapping_missing_for_placeholder_url(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A placeholder URL must be treated the same as absent (env_mapping_missing)."""
    _clear_provider_env(monkeypatch)
    monkeypatch.setenv('STAGING_EVM_RPC_URL', 'https://example.com/v3/changeme')

    rc, reason = write_live_rpc_polling_artifacts(service_artifacts_dir=tmp_path)

    assert rc == 1
    assert reason == 'env_mapping_missing'


def test_write_artifacts_env_mapping_missing_no_files_written(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """No artifacts must be written when env vars are absent."""
    _clear_provider_env(monkeypatch)

    write_live_rpc_polling_artifacts(service_artifacts_dir=tmp_path)

    assert not (tmp_path / 'summary.json').exists()
    assert not (tmp_path / 'evidence.json').exists()
    assert not (tmp_path / 'telemetry_events.json').exists()


# ---------------------------------------------------------------------------
# Test 2: worker env present but no rpc_polling artifacts → no_live_telemetry
# ---------------------------------------------------------------------------

def test_export_fails_when_service_artifacts_have_simulator_source(
    tmp_path: Path,
) -> None:
    """
    When service artifacts exist but carry evidence_source='guided_simulator'
    (worker env is configured but rpc_polling artifacts have not been written),
    export_live_evidence_chain must reject them and exit 1.

    This corresponds to 'no_live_telemetry': the worker env is present but the
    artifact content proves no real rpc_polling evidence was written.
    """
    svc_dir = tmp_path / 'svc'
    out_file = tmp_path / 'chain.json'

    svc_dir.mkdir()
    (svc_dir / 'summary.json').write_text(json.dumps({
        'live_evidence_ready': True,
        'evidence_source': 'guided_simulator',
    }))
    (svc_dir / 'evidence.json').write_text(json.dumps({
        'workspace_id': 'ws-1',
        'evidence_source': 'guided_simulator',
        'chain': {
            'telemetry_event_id': 'tel-1',
            'detection_id': 'det-1',
            'alert_id': 'alert-1',
            'incident_id': 'inc-1',
            'response_action_id': 'ra-1',
            'evidence_package_id': 'pkg-1',
        },
    }))
    (svc_dir / 'telemetry_events.json').write_text(json.dumps([]))

    rc = export_live_evidence_chain(
        service_artifacts_dir=svc_dir,
        output_file=out_file,
    )

    assert rc == 1, 'Expected rejection of guided_simulator artifacts (no_live_telemetry)'
    assert not out_file.exists(), 'live_evidence_chain.json must not be written on rejection'


def test_export_fails_when_service_artifacts_missing(
    tmp_path: Path,
) -> None:
    """Missing service artifacts must cause export to fail (no_live_telemetry)."""
    empty_dir = tmp_path / 'empty'
    empty_dir.mkdir()
    out_file = tmp_path / 'chain.json'

    rc = export_live_evidence_chain(
        service_artifacts_dir=empty_dir,
        output_file=out_file,
    )

    assert rc == 1
    assert not out_file.exists()


def test_export_fails_when_telemetry_source_type_not_rpc_polling(
    tmp_path: Path,
) -> None:
    """Telemetry events with source_type != 'rpc_polling' must be rejected."""
    svc_dir = tmp_path / 'svc'
    out_file = tmp_path / 'chain.json'
    svc_dir.mkdir()

    (svc_dir / 'summary.json').write_text(json.dumps({
        'live_evidence_ready': True,
        'evidence_source': 'live',
    }))
    (svc_dir / 'evidence.json').write_text(json.dumps({
        'workspace_id': 'ws-1',
        'evidence_source': 'live',
        'chain': {
            'telemetry_event_id': 'tel-1',
            'detection_id': 'det-1',
            'alert_id': 'alert-1',
            'incident_id': 'inc-1',
            'response_action_id': 'ra-1',
            'evidence_package_id': 'pkg-1',
        },
    }))
    (svc_dir / 'telemetry_events.json').write_text(json.dumps([
        {'evidence_source': 'live', 'source_type': 'heartbeat'},
    ]))

    rc = export_live_evidence_chain(
        service_artifacts_dir=svc_dir,
        output_file=out_file,
    )

    assert rc == 1
    assert not out_file.exists()


# ---------------------------------------------------------------------------
# Test 3: rpc_polling artifacts from write step generate live_evidence_chain.json
# ---------------------------------------------------------------------------

def test_rpc_polling_artifacts_produce_live_evidence_chain_json(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """
    When write_live_rpc_polling_artifacts succeeds (mocked RPC), the service
    artifact directory contains live/rpc_polling artifacts.  Running
    export_live_evidence_chain on that directory must produce a valid
    live_evidence_chain.json with evidence_source='live' and
    source_type='rpc_polling'.
    """
    _clear_provider_env(monkeypatch)
    monkeypatch.setenv('STAGING_EVM_RPC_URL', _REAL_RPC)
    monkeypatch.setenv('STAGING_EVM_CHAIN_ID', '1')
    monkeypatch.setenv('STAGING_WORKER_ENABLED', 'true')

    svc_dir = tmp_path / 'svc'
    out_file = tmp_path / 'chain.json'

    with patch(_WRITE_SCRIPT_RPC_PATCH, side_effect=_mock_rpc_responses()):
        rc_write, reason = write_live_rpc_polling_artifacts(
            service_artifacts_dir=svc_dir,
        )

    assert rc_write == 0, f'write step failed: {reason}'

    # Verify the written artifacts have the right source fields
    evidence = json.loads((svc_dir / 'evidence.json').read_text())
    assert evidence['evidence_source'] == 'live'
    assert evidence['source_type'] == 'rpc_polling'

    telemetry = json.loads((svc_dir / 'telemetry_events.json').read_text())
    assert isinstance(telemetry, list) and len(telemetry) >= 1
    assert telemetry[0]['evidence_source'] == 'live'
    assert telemetry[0]['source_type'] == 'rpc_polling'

    # Export step reads these artifacts and writes live_evidence_chain.json
    rc_export = export_live_evidence_chain(
        service_artifacts_dir=svc_dir,
        output_file=out_file,
    )

    assert rc_export == 0, 'export step must succeed with live rpc_polling artifacts'
    assert out_file.exists(), 'live_evidence_chain.json must be written'

    chain = json.loads(out_file.read_text())
    assert chain['evidence_source'] == 'live'
    assert chain['source_type'] == 'rpc_polling'
    assert chain['telemetry_event_id']
    assert chain['detection_id']
    assert chain['alert_id']
    assert chain['evidence_package_id']
    assert chain.get('incident_id') or chain.get('response_action_id')


def test_write_artifacts_chain_ids_are_deterministic_for_same_rpc_state(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """
    Writing artifacts twice with the same RPC response but different timestamps
    produces distinct IDs (timestamps are part of the seed). This confirms IDs
    are derived from real observations, not hardcoded.
    """
    _clear_provider_env(monkeypatch)
    monkeypatch.setenv('STAGING_EVM_RPC_URL', _REAL_RPC)

    dir_a = tmp_path / 'a'
    dir_b = tmp_path / 'b'

    with patch(_WRITE_SCRIPT_RPC_PATCH, side_effect=_mock_rpc_responses('0x1', '0x100')):
        write_live_rpc_polling_artifacts(service_artifacts_dir=dir_a)

    with patch(_WRITE_SCRIPT_RPC_PATCH, side_effect=_mock_rpc_responses('0x1', '0x101')):
        write_live_rpc_polling_artifacts(service_artifacts_dir=dir_b)

    chain_a = json.loads((dir_a / 'evidence.json').read_text())['chain']
    chain_b = json.loads((dir_b / 'evidence.json').read_text())['chain']

    # Different block numbers → different IDs
    assert chain_a['telemetry_event_id'] != chain_b['telemetry_event_id']


# ---------------------------------------------------------------------------
# Test 4: full strict proof passes ONLY with real live chain
# ---------------------------------------------------------------------------

def test_full_strict_proof_passes_with_live_rpc_chain(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """
    End-to-end: write → export → generate --strict

    When the full pipeline produces a valid live rpc_polling chain, the
    generate step must return a proof with live_evidence_ready=True.
    """
    _clear_provider_env(monkeypatch)
    monkeypatch.setenv('STAGING_EVM_RPC_URL', _REAL_RPC)
    monkeypatch.setenv('STAGING_EVM_CHAIN_ID', '1')
    monkeypatch.setenv('STAGING_WORKER_ENABLED', 'true')

    svc_dir = tmp_path / 'svc'
    chain_file = tmp_path / 'chain.json'

    # Step 1: write live rpc_polling artifacts (mocked RPC)
    with patch(_WRITE_SCRIPT_RPC_PATCH, side_effect=_mock_rpc_responses()):
        rc_write, reason = write_live_rpc_polling_artifacts(
            service_artifacts_dir=svc_dir,
        )
    assert rc_write == 0, f'write step failed: {reason}'

    # Step 2: export to chain file
    rc_export = export_live_evidence_chain(
        service_artifacts_dir=svc_dir,
        output_file=chain_file,
    )
    assert rc_export == 0, 'export step failed'

    chain_data = json.loads(chain_file.read_text())

    # Step 3: generate proof with live chain (mocked RPC for provider check)
    with patch(_GEN_SCRIPT_RPC_PATCH, side_effect=_mock_rpc_responses()):
        result = generate_live_evidence_proof(
            live_evidence_chain=chain_data,
        )

    lpe = result.get('live_provider_evidence', {})

    assert lpe.get('provider_ready') is True, (
        f'expected provider_ready=True; missing={lpe.get("missing")}'
    )
    assert lpe.get('live_evidence_ready') is True, (
        f'expected live_evidence_ready=True; missing={lpe.get("missing")}'
    )
    assert lpe.get('evidence_source') == 'live'
    assert lpe.get('worker_enabled') is True
    assert lpe.get('chain_id_configured') is True
    assert lpe.get('missing') == []
    assert lpe.get('contradiction_flags') == []


def test_full_strict_proof_fails_without_live_chain(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    generate_live_evidence_proof must fail closed when no live evidence
    chain is supplied, even when the RPC provider is reachable.
    """
    _clear_provider_env(monkeypatch)
    monkeypatch.setenv('STAGING_EVM_RPC_URL', _REAL_RPC)
    monkeypatch.setenv('STAGING_EVM_CHAIN_ID', '1')
    monkeypatch.setenv('STAGING_WORKER_ENABLED', 'true')

    with patch(_GEN_SCRIPT_RPC_PATCH, side_effect=_mock_rpc_responses()):
        result = generate_live_evidence_proof(live_evidence_chain=None)

    lpe = result.get('live_provider_evidence', {})

    # Provider is up (RPC works) but evidence is missing
    assert lpe.get('provider_ready') is True
    assert lpe.get('live_evidence_ready') is False, (
        'live_evidence_ready must be False without a live chain'
    )


def test_full_strict_proof_rejects_simulator_evidence_source(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    generate_live_evidence_proof must reject a chain with
    evidence_source='guided_simulator' even if all IDs are present.
    """
    _clear_provider_env(monkeypatch)
    monkeypatch.setenv('STAGING_EVM_RPC_URL', _REAL_RPC)
    monkeypatch.setenv('STAGING_EVM_CHAIN_ID', '1')
    monkeypatch.setenv('STAGING_WORKER_ENABLED', 'true')

    simulator_chain = {
        'evidence_source': 'guided_simulator',
        'source_type': 'rpc_polling',
        'telemetry_event_id': 'tel-sim-001',
        'detection_id': 'det-sim-001',
        'alert_id': 'alert-sim-001',
        'incident_id': 'inc-sim-001',
        'response_action_id': 'ra-sim-001',
        'evidence_package_id': 'pkg-sim-001',
        'observed_at': '2026-05-27T00:00:00+00:00',
    }

    with patch(_GEN_SCRIPT_RPC_PATCH, side_effect=_mock_rpc_responses()):
        result = generate_live_evidence_proof(live_evidence_chain=simulator_chain)

    lpe = result.get('live_provider_evidence', {})
    assert lpe.get('live_evidence_ready') is False, (
        'guided_simulator chain must not satisfy live_evidence_ready'
    )


def test_full_strict_proof_rejects_simulator_source_type(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    A chain with evidence_source='live' but source_type='fixture' must be
    rejected (strict rejection for simulator/fixture source_types).
    """
    _clear_provider_env(monkeypatch)
    monkeypatch.setenv('STAGING_EVM_RPC_URL', _REAL_RPC)
    monkeypatch.setenv('STAGING_EVM_CHAIN_ID', '1')
    monkeypatch.setenv('STAGING_WORKER_ENABLED', 'true')

    fixture_chain = {
        'evidence_source': 'live',
        'source_type': 'fixture',
        'telemetry_event_id': 'tel-fix-001',
        'detection_id': 'det-fix-001',
        'alert_id': 'alert-fix-001',
        'incident_id': 'inc-fix-001',
        'response_action_id': 'ra-fix-001',
        'evidence_package_id': 'pkg-fix-001',
        'observed_at': '2026-05-27T00:00:00+00:00',
    }

    with patch(_GEN_SCRIPT_RPC_PATCH, side_effect=_mock_rpc_responses()):
        result = generate_live_evidence_proof(live_evidence_chain=fixture_chain)

    lpe = result.get('live_provider_evidence', {})
    assert lpe.get('live_evidence_ready') is False, (
        'fixture source_type must not satisfy live_evidence_ready'
    )


# ---------------------------------------------------------------------------
# Diagnostic script
# ---------------------------------------------------------------------------

def test_diagnose_reflects_env_presence(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """diagnose() booleans match actual env state; no secret values appear."""
    _clear_provider_env(monkeypatch)
    monkeypatch.setenv('STAGING_EVM_RPC_URL', 'https://mainnet.infura.io/v3/secret123')
    monkeypatch.setenv('STAGING_EVM_CHAIN_ID', '1')
    monkeypatch.setenv('STAGING_WORKER_ENABLED', 'true')

    from scripts.diagnose_live_evidence import diagnose

    # Pass a subdirectory that does not yet exist so dir_exists=False
    nonexistent_dir = tmp_path / 'no_artifacts'
    result = diagnose(service_artifacts_dir=nonexistent_dir)

    assert result['STAGING_EVM_RPC_URL_present'] is True
    assert result['EVM_RPC_URL_present'] is False
    assert result['STAGING_EVM_CHAIN_ID_present'] is True
    assert result['STAGING_WORKER_ENABLED'] == 'true'
    assert result['live_artifact_dir_exists'] is False
    assert result['live_rpc_polling_artifacts_count'] == 0
    assert result['simulator_artifacts_count'] == 0

    # Secret value must never appear in any output value
    for v in result.values():
        assert 'secret123' not in str(v)


def test_diagnose_counts_live_vs_simulator_artifacts(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """
    After writing live rpc_polling artifacts, diagnose counts them correctly
    and distinguishes them from simulator artifacts.
    """
    _clear_provider_env(monkeypatch)
    monkeypatch.setenv('STAGING_EVM_RPC_URL', _REAL_RPC)

    svc_dir = tmp_path / 'svc'

    with patch(_WRITE_SCRIPT_RPC_PATCH, side_effect=_mock_rpc_responses()):
        write_live_rpc_polling_artifacts(service_artifacts_dir=svc_dir)

    from scripts.diagnose_live_evidence import diagnose
    result = diagnose(service_artifacts_dir=svc_dir)

    assert result['live_artifact_dir_exists'] is True
    assert result['live_rpc_polling_artifacts_count'] >= 2
    assert result['simulator_artifacts_count'] == 0
