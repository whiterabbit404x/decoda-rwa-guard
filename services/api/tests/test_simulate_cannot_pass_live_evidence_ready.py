"""
Criterion 3 guard: simulated/demo data must never satisfy live_evidence_ready.

Tests that validate_100_percent_readiness._check_live_evidence and
generate_live_evidence_proof() both fail closed when:
  - evidence_source is simulator, demo, fixture, guided_simulator, or unknown
  - live_evidence_ready=true is set but evidence_source != 'live'
  - live_evidence_ready=true is set but required chain IDs are missing
  - IDs are derived from content-addressable data, not purely from random uuid4()
"""
from __future__ import annotations

import json
import sys
import uuid
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT))

from scripts.validate_100_percent_readiness import _check_live_evidence
from scripts.generate_live_evidence_proof import generate_live_evidence_proof, _content_id

_SCRIPT_RPC_PATCH = 'scripts.generate_live_evidence_proof._rpc_call'
_REAL_RPC = 'https://mainnet.infura.io/v3/test_proj'

_PROVIDER_ENV_VARS = [
    'EVM_RPC_URL', 'STAGING_EVM_RPC_URL',
    'EVM_CHAIN_ID', 'STAGING_EVM_CHAIN_ID', 'CHAIN_ID',
    'STAGING_WORKER_ENABLED',
    'LIVE_EVIDENCE_CHAIN_JSON', 'LIVE_EVIDENCE_CHAIN_FILE',
]


def _real_live_chain(telemetry_id: str = 'tel-live-001') -> dict[str, Any]:
    """Canonical real live-event chain (source_type=rpc_polling, evidence_source=live)."""
    return {
        'telemetry_event_id': telemetry_id,
        'detection_id': 'det-live-001',
        'alert_id': 'alert-live-001',
        'incident_id': 'inc-live-001',
        'response_action_id': 'ra-live-001',
        'evidence_package_id': 'pkg-live-001',
        'evidence_source': 'live',
        'source_type': 'rpc_polling',
        'observed_at': '2026-05-22T12:00:00+00:00',
    }

_ALL_CHAIN_IDS = {
    'telemetry_event_id': 'tel-live-001',
    'detection_id': 'det-live-001',
    'alert_id': 'alert-live-001',
    'incident_id': 'inc-live-001',
    'response_action_id': 'ra-live-001',
    'evidence_package_id': 'pkg-live-001',
}


def _clear_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in _PROVIDER_ENV_VARS:
        monkeypatch.delenv(var, raising=False)


def _mock_rpc_success(chain_id_hex: str = '0x1', block_hex: str = '0x12c') -> Any:
    responses = iter([
        {'result': chain_id_hex, 'jsonrpc': '2.0', 'id': 1},
        {'result': block_hex, 'jsonrpc': '2.0', 'id': 1},
    ])

    def _side(url: str, method: str, params: list | None = None, timeout: int = 10) -> dict:
        return next(responses)

    return _side


def _write_artifact(tmp_path: Path, lpe: dict[str, Any]) -> Path:
    """Write a live-evidence-proof summary.json to a temp directory."""
    artifact_dir = tmp_path / 'live-evidence-proof' / 'latest'
    artifact_dir.mkdir(parents=True)
    artifact_path = artifact_dir / 'summary.json'
    artifact_path.write_text(json.dumps({'live_provider_evidence': lpe}))
    return artifact_dir


# ---------------------------------------------------------------------------
# _check_live_evidence: simulator evidence must not pass
# ---------------------------------------------------------------------------

@pytest.mark.parametrize('source', ['simulator', 'guided_simulator', 'fixture', 'demo', 'unknown'])
def test_check_live_evidence_rejects_non_live_source(
    tmp_path: Path,
    source: str,
) -> None:
    """
    _check_live_evidence must reject live_evidence_ready=true when evidence_source != 'live'.
    Setting live_evidence_ready=true in an artifact is not sufficient; the source must be live.
    """
    lpe = {
        'live_evidence_ready': True,
        'evidence_source': source,
        'chain': _ALL_CHAIN_IDS,
        'missing': [],
        'contradiction_flags': [],
    }
    artifact_dir = _write_artifact(tmp_path, lpe)

    ok, blockers = _check_live_evidence(None, 'staging', live_evidence_proof_dir=artifact_dir)

    assert ok is False, f'Expected rejection for evidence_source={source!r}'
    assert blockers, 'Expected at least one blocker'
    assert any('simulated' in b or source in b for b in blockers), \
        f'Expected blocker mentioning source or simulation; got: {blockers}'


def test_check_live_evidence_rejects_missing_telemetry_event_id(tmp_path: Path) -> None:
    """Missing telemetry_event_id with live_evidence_ready=true must be rejected."""
    lpe = {
        'live_evidence_ready': True,
        'evidence_source': 'live',
        'chain': {**_ALL_CHAIN_IDS, 'telemetry_event_id': None},
        'missing': [],
    }
    artifact_dir = _write_artifact(tmp_path, lpe)

    ok, blockers = _check_live_evidence(None, 'staging', live_evidence_proof_dir=artifact_dir)

    assert ok is False
    assert any('telemetry_event_id' in b for b in blockers), \
        f'Expected blocker mentioning telemetry_event_id; got: {blockers}'


def test_check_live_evidence_rejects_missing_detection_id(tmp_path: Path) -> None:
    """Missing detection_id must be rejected even when live_evidence_ready=true."""
    lpe = {
        'live_evidence_ready': True,
        'evidence_source': 'live',
        'chain': {**_ALL_CHAIN_IDS, 'detection_id': ''},
        'missing': [],
    }
    artifact_dir = _write_artifact(tmp_path, lpe)

    ok, blockers = _check_live_evidence(None, 'staging', live_evidence_proof_dir=artifact_dir)

    assert ok is False
    assert any('detection_id' in b for b in blockers)


def test_check_live_evidence_rejects_missing_evidence_package_id(tmp_path: Path) -> None:
    """Missing evidence_package_id must be rejected."""
    lpe = {
        'live_evidence_ready': True,
        'evidence_source': 'live',
        'chain': {**_ALL_CHAIN_IDS, 'evidence_package_id': None},
        'missing': [],
    }
    artifact_dir = _write_artifact(tmp_path, lpe)

    ok, blockers = _check_live_evidence(None, 'staging', live_evidence_proof_dir=artifact_dir)

    assert ok is False
    assert any('evidence_package_id' in b for b in blockers)


def test_check_live_evidence_accepts_complete_live_artifact(tmp_path: Path) -> None:
    """A complete live artifact (correct source + all chain IDs) must be accepted."""
    lpe = {
        'live_evidence_ready': True,
        'evidence_source': 'live',
        'chain': _ALL_CHAIN_IDS,
        'missing': [],
        'contradiction_flags': [],
        'latest_live_telemetry_at': '2026-06-05T00:00:00+00:00',
    }
    artifact_dir = _write_artifact(tmp_path, lpe)

    ok, blockers = _check_live_evidence(None, 'staging', live_evidence_proof_dir=artifact_dir)

    assert ok is True
    assert blockers == []


def test_check_live_evidence_rejects_false_live_evidence_ready(tmp_path: Path) -> None:
    """live_evidence_ready=false with correct source and all IDs must still be rejected."""
    lpe = {
        'live_evidence_ready': False,
        'evidence_source': 'live',
        'chain': _ALL_CHAIN_IDS,
        'missing': ['something missing'],
    }
    artifact_dir = _write_artifact(tmp_path, lpe)

    ok, _blockers = _check_live_evidence(None, 'staging', live_evidence_proof_dir=artifact_dir)

    assert ok is False


# ---------------------------------------------------------------------------
# generate_live_evidence_proof: IDs must be content-addressable (not random uuid4)
# ---------------------------------------------------------------------------

def test_ids_are_deterministic_for_same_rpc_data(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    With the same real live-event chain injected twice, IDs must match exactly
    (no random uuid4(); the chain comes from the worker, not from synthesis).
    """
    _clear_env(monkeypatch)
    monkeypatch.setenv('EVM_RPC_URL', _REAL_RPC)
    monkeypatch.setenv('EVM_CHAIN_ID', '1')
    monkeypatch.setenv('STAGING_WORKER_ENABLED', 'true')

    chain = _real_live_chain()

    mock = _mock_rpc_success('0x1', '0x112358')
    with patch(_SCRIPT_RPC_PATCH, side_effect=mock):
        result1 = generate_live_evidence_proof(live_evidence_chain=chain)

    mock2 = _mock_rpc_success('0x1', '0x112358')
    with patch(_SCRIPT_RPC_PATCH, side_effect=mock2):
        result2 = generate_live_evidence_proof(live_evidence_chain=chain)

    chain1 = result1['live_provider_evidence']['chain']
    chain2 = result2['live_provider_evidence']['chain']

    assert chain1['telemetry_event_id'] == chain2['telemetry_event_id']
    assert chain1['detection_id'] == chain2['detection_id']
    assert chain1['alert_id'] == chain2['alert_id']
    assert chain1['incident_id'] == chain2['incident_id']
    assert chain1['evidence_package_id'] == chain2['evidence_package_id']


def test_ids_differ_for_different_live_events(monkeypatch: pytest.MonkeyPatch) -> None:
    """Different real live-event chains must produce different telemetry IDs in the proof."""
    _clear_env(monkeypatch)
    monkeypatch.setenv('EVM_RPC_URL', _REAL_RPC)
    monkeypatch.setenv('EVM_CHAIN_ID', '1')
    monkeypatch.setenv('STAGING_WORKER_ENABLED', 'true')

    chain_a = _real_live_chain(telemetry_id='tel-live-A')
    chain_b = _real_live_chain(telemetry_id='tel-live-B')

    mock1 = _mock_rpc_success('0x1', '0x112358')
    with patch(_SCRIPT_RPC_PATCH, side_effect=mock1):
        result_a = generate_live_evidence_proof(live_evidence_chain=chain_a)

    mock2 = _mock_rpc_success('0x1', '0x112358')
    with patch(_SCRIPT_RPC_PATCH, side_effect=mock2):
        result_b = generate_live_evidence_proof(live_evidence_chain=chain_b)

    tel_a = result_a['live_provider_evidence']['chain']['telemetry_event_id']
    tel_b = result_b['live_provider_evidence']['chain']['telemetry_event_id']

    assert tel_a != tel_b
    assert tel_a == 'tel-live-A'
    assert tel_b == 'tel-live-B'


def test_rpc_alone_does_not_fake_chain_ids(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    Successful RPC without a real live event must NOT fake any chain IDs.
    All chain IDs must be None and live_evidence_ready must be False.
    """
    _clear_env(monkeypatch)
    monkeypatch.setenv('EVM_RPC_URL', _REAL_RPC)
    monkeypatch.setenv('EVM_CHAIN_ID', '1')
    monkeypatch.setenv('STAGING_WORKER_ENABLED', 'true')

    with patch(_SCRIPT_RPC_PATCH, side_effect=_mock_rpc_success('0x1', '0x12c')):
        result = generate_live_evidence_proof()

    lpe = result['live_provider_evidence']
    assert lpe['live_evidence_ready'] is False
    chain = lpe['chain']
    for field in ('telemetry_event_id', 'detection_id', 'alert_id', 'incident_id', 'evidence_package_id'):
        assert chain[field] is None, \
            f'{field} must not be synthesised from eth_chainId/eth_blockNumber alone'


def test_simulator_evidence_does_not_produce_live_evidence_ready(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    generate_live_evidence_proof must not produce live_evidence_ready=true
    when no real RPC URL is configured (simulator/disabled path).
    """
    _clear_env(monkeypatch)
    # No RPC URL → disabled mode

    result = generate_live_evidence_proof()
    lpe = result['live_provider_evidence']

    assert lpe['live_evidence_ready'] is False
    assert lpe['provider_mode'] == 'disabled'
    # All chain IDs must be null
    chain = lpe['chain']
    for field in ('telemetry_event_id', 'detection_id', 'alert_id', 'incident_id', 'evidence_package_id'):
        assert chain[field] is None, f'{field} must be None in disabled mode; got {chain[field]}'


def test_content_id_is_uuid5_not_uuid4() -> None:
    """_content_id must return a UUID version 5, not version 4."""
    result = _content_id('telemetry', '1', '12345678', 'deadbeef')
    parsed = uuid.UUID(result)
    assert parsed.version == 5, f'Expected uuid5, got version {parsed.version}'


def test_content_id_is_deterministic() -> None:
    """_content_id must return the same value for the same inputs."""
    id1 = _content_id('telemetry', '1', '12345678', 'deadbeef')
    id2 = _content_id('telemetry', '1', '12345678', 'deadbeef')
    assert id1 == id2, '_content_id must be deterministic'


def test_content_id_differs_for_different_inputs() -> None:
    """_content_id must produce distinct values for distinct inputs."""
    id1 = _content_id('telemetry', '1', '12345678', 'aaaa')
    id2 = _content_id('telemetry', '1', '12345678', 'bbbb')
    assert id1 != id2, '_content_id must produce distinct IDs for distinct inputs'
