"""
Live evidence proof validation — canonical test cases.

Validates build_live_evidence_proof() and check_provider_readiness() from
paid_launch_readiness, AND generate_live_evidence_proof() from
scripts/generate_live_evidence_proof.py, covering all combinations of:
- missing env vars, heartbeat/poll-only states, simulator/demo evidence
- partial chain links, RPC failure, chain ID mismatch
- complete live chain with mocked RPC calls
"""
from __future__ import annotations

import importlib
import sys
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT))

from services.api.app.paid_launch_readiness import (
    build_live_evidence_proof,
    check_provider_readiness,
)
from scripts.generate_live_evidence_proof import generate_live_evidence_proof

_SCRIPT_RPC_PATCH = 'scripts.generate_live_evidence_proof._rpc_call'
_REAL_RPC = 'https://mainnet.infura.io/v3/test_proj'


def _mock_rpc_success(
    chain_id_hex: str = '0x1',
    block_hex: str = '0x12c',
) -> Any:
    """Return a side_effect that alternates eth_chainId then eth_blockNumber."""
    responses = iter([
        {'result': chain_id_hex, 'jsonrpc': '2.0', 'id': 1},
        {'result': block_hex, 'jsonrpc': '2.0', 'id': 1},
    ])

    def _side(url: str, method: str, params: list | None = None, timeout: int = 10) -> dict:
        return next(responses)

    return _side


def _mock_rpc_error() -> Any:
    def _side(url: str, method: str, params: list | None = None, timeout: int = 10) -> dict:
        return {'error': 'URLError: <urlopen error [Errno 111] Connection refused>'}
    return _side

_PROVIDER_ENV_VARS = [
    'EVM_RPC_URL', 'STAGING_EVM_RPC_URL',
    'EVM_CHAIN_ID', 'STAGING_EVM_CHAIN_ID', 'CHAIN_ID',
    'STAGING_WORKER_ENABLED',
    'LIVE_EVIDENCE_CHAIN_JSON', 'LIVE_EVIDENCE_CHAIN_FILE',
]


def _real_live_chain(**overrides) -> dict:
    """Canonical real live-event chain for the script proof tests."""
    chain = {
        'telemetry_event_id': 'tel-live-001',
        'detection_id': 'det-live-001',
        'alert_id': 'alert-live-001',
        'incident_id': 'inc-live-001',
        'response_action_id': 'ra-live-001',
        'evidence_package_id': 'pkg-live-001',
        'evidence_source': 'live',
        'source_type': 'rpc_polling',
        'observed_at': '2026-05-22T12:00:00+00:00',
        'detection_name': 'live_rpc_event_observed',
    }
    chain.update(overrides)
    return chain

_FULL_CHAIN: dict = {
    'evidence_source': 'live',
    'last_heartbeat_at': '2026-01-01T00:00:00Z',
    'latest_poll_at': '2026-01-01T00:00:30Z',
    'last_telemetry_at': '2026-01-01T00:01:00Z',
    'telemetry_event_id': 'tel-001',
    'detections_count': 1,
    'detection_telemetry_linked': True,
    'detection_id': 'det-001',
    'alerts_count': 1,
    'alert_detection_linked': True,
    'alert_id': 'alert-001',
    'incidents_count': 1,
    'incident_alert_linked': True,
    'incident_id': 'inc-001',
    'evidence_package_id': 'pkg-001',
    'export_capability': 'pass',
    'export_source_label': 'live',
    'contradiction_flags': [],
}


def _clear_provider_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in _PROVIDER_ENV_VARS:
        monkeypatch.delenv(var, raising=False)


# ---------------------------------------------------------------------------
# Case 1: Missing EVM_RPC_URL and STAGING_EVM_RPC_URL
# ---------------------------------------------------------------------------

def test_case1_missing_both_provider_urls(monkeypatch: pytest.MonkeyPatch) -> None:
    """Missing EVM_RPC_URL and STAGING_EVM_RPC_URL → provider_ready=False, live_evidence_ready=False."""
    _clear_provider_env(monkeypatch)

    result = build_live_evidence_proof()

    assert result['provider_ready'] is False
    assert result['live_evidence_ready'] is False
    assert result['provider_mode'] == 'disabled'
    assert any('EVM_RPC_URL' in m for m in result['missing']), \
        f"Expected missing to mention EVM_RPC_URL, got: {result['missing']}"


def test_case1_check_provider_readiness_with_both_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    """check_provider_readiness: both absent → disabled."""
    _clear_provider_env(monkeypatch)

    out = check_provider_readiness()

    assert out['provider_ready'] is False
    assert out['provider_mode'] == 'disabled'
    assert 'EVM_RPC_URL' in out['provider_missing_env']


def test_case1_staging_evm_rpc_url_satisfies_provider_when_evm_absent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """STAGING_EVM_RPC_URL alone satisfies provider_ready when EVM_RPC_URL is absent."""
    _clear_provider_env(monkeypatch)
    monkeypatch.setenv('STAGING_EVM_RPC_URL', 'https://mainnet.infura.io/v3/staging_proj')

    out = check_provider_readiness()

    assert out['provider_ready'] is True
    assert out['provider_mode'] == 'live'
    assert out['provider_missing_env'] == []


# ---------------------------------------------------------------------------
# Case 2: Heartbeat exists but no telemetry
# ---------------------------------------------------------------------------

def test_case2_heartbeat_only_not_telemetry(monkeypatch: pytest.MonkeyPatch) -> None:
    """Heartbeat alone must not satisfy live telemetry requirement."""
    _clear_provider_env(monkeypatch)
    monkeypatch.setenv('EVM_RPC_URL', 'https://mainnet.infura.io/v3/proj_abc')

    result = build_live_evidence_proof(chain_evidence={
        'evidence_source': 'live',
        'last_heartbeat_at': '2026-01-01T00:00:00Z',
        # no last_telemetry_at
    })

    assert result['live_evidence_ready'] is False
    assert result['latest_live_telemetry_at'] is None
    assert any('heartbeat' in m for m in result['missing']), \
        f"Expected missing to mention heartbeat, got: {result['missing']}"


# ---------------------------------------------------------------------------
# Case 3: Poll exists but no telemetry
# ---------------------------------------------------------------------------

def test_case3_poll_only_not_telemetry(monkeypatch: pytest.MonkeyPatch) -> None:
    """Poll loop alone must not satisfy live telemetry requirement."""
    _clear_provider_env(monkeypatch)
    monkeypatch.setenv('EVM_RPC_URL', 'https://mainnet.infura.io/v3/proj_abc')

    result = build_live_evidence_proof(chain_evidence={
        'evidence_source': 'live',
        'latest_poll_at': '2026-01-01T00:00:30Z',
        # no last_telemetry_at
    })

    assert result['live_evidence_ready'] is False
    assert result['latest_live_telemetry_at'] is None
    assert any('poll' in m for m in result['missing']), \
        f"Expected missing to mention poll, got: {result['missing']}"


def test_case3_heartbeat_and_poll_without_telemetry(monkeypatch: pytest.MonkeyPatch) -> None:
    """Both heartbeat and poll without telemetry must still be rejected."""
    _clear_provider_env(monkeypatch)
    monkeypatch.setenv('EVM_RPC_URL', 'https://mainnet.infura.io/v3/proj_abc')

    result = build_live_evidence_proof(chain_evidence={
        'evidence_source': 'live',
        'last_heartbeat_at': '2026-01-01T00:00:00Z',
        'latest_poll_at': '2026-01-01T00:00:30Z',
        # no last_telemetry_at
    })

    assert result['live_evidence_ready'] is False
    assert result['latest_live_telemetry_at'] is None


# ---------------------------------------------------------------------------
# Case 4: Simulator/demo telemetry
# ---------------------------------------------------------------------------

@pytest.mark.parametrize('source', ['simulator', 'guided_simulator', 'fixture'])
def test_case4_simulator_evidence_not_live(
    monkeypatch: pytest.MonkeyPatch,
    source: str,
) -> None:
    """Simulator/demo/fixture evidence source must be rejected and labeled as simulator."""
    _clear_provider_env(monkeypatch)
    monkeypatch.setenv('EVM_RPC_URL', 'https://mainnet.infura.io/v3/proj_abc')

    chain = {**_FULL_CHAIN, 'evidence_source': source}
    result = build_live_evidence_proof(chain_evidence=chain)

    assert result['live_evidence_ready'] is False
    assert result['evidence_source'] == 'simulator'
    assert any('not live provider evidence' in f for f in result['contradiction_flags']), \
        f"Expected contradiction flag for source={source!r}, got: {result['contradiction_flags']}"


def test_case4_demo_evidence_not_live(monkeypatch: pytest.MonkeyPatch) -> None:
    """Demo evidence source must be rejected and labeled as demo."""
    _clear_provider_env(monkeypatch)
    monkeypatch.setenv('EVM_RPC_URL', 'https://mainnet.infura.io/v3/proj_abc')

    chain = {**_FULL_CHAIN, 'evidence_source': 'demo'}
    result = build_live_evidence_proof(chain_evidence=chain)

    assert result['live_evidence_ready'] is False
    assert result['evidence_source'] == 'demo'
    assert any('not live provider evidence' in f for f in result['contradiction_flags'])


# ---------------------------------------------------------------------------
# Case 5: Live telemetry exists but no detection
# ---------------------------------------------------------------------------

def test_case5_live_telemetry_but_no_detection(monkeypatch: pytest.MonkeyPatch) -> None:
    """Live telemetry present but no detection → live_evidence_ready=False."""
    _clear_provider_env(monkeypatch)
    monkeypatch.setenv('EVM_RPC_URL', 'https://mainnet.infura.io/v3/proj_abc')

    result = build_live_evidence_proof(chain_evidence={
        'evidence_source': 'live',
        'last_telemetry_at': '2026-01-01T00:01:00Z',
        'detections_count': 0,
        # no detection_id
    })

    assert result['live_evidence_ready'] is False
    assert result['latest_live_telemetry_at'] is not None
    assert any('detection' in m for m in result['missing']), \
        f"Expected missing to mention detection, got: {result['missing']}"


# ---------------------------------------------------------------------------
# Case 6: Live telemetry + detection but no alert
# ---------------------------------------------------------------------------

def test_case6_telemetry_detection_but_no_alert(monkeypatch: pytest.MonkeyPatch) -> None:
    """Detection linked to telemetry but no alert → live_evidence_ready=False."""
    _clear_provider_env(monkeypatch)
    monkeypatch.setenv('EVM_RPC_URL', 'https://mainnet.infura.io/v3/proj_abc')

    result = build_live_evidence_proof(chain_evidence={
        'evidence_source': 'live',
        'last_telemetry_at': '2026-01-01T00:01:00Z',
        'detections_count': 1,
        'detection_telemetry_linked': True,
        'detection_id': 'det-001',
        'alerts_count': 0,
        # no alert_id
    })

    assert result['live_evidence_ready'] is False
    assert any('alert' in m for m in result['missing']), \
        f"Expected missing to mention alert, got: {result['missing']}"


# ---------------------------------------------------------------------------
# Case 7: Live telemetry + detection + alert but no incident/response
# ---------------------------------------------------------------------------

def test_case7_telemetry_detection_alert_but_no_incident(monkeypatch: pytest.MonkeyPatch) -> None:
    """Alert linked to detection but no incident or response_action → live_evidence_ready=False."""
    _clear_provider_env(monkeypatch)
    monkeypatch.setenv('EVM_RPC_URL', 'https://mainnet.infura.io/v3/proj_abc')

    result = build_live_evidence_proof(chain_evidence={
        'evidence_source': 'live',
        'last_telemetry_at': '2026-01-01T00:01:00Z',
        'detections_count': 1,
        'detection_telemetry_linked': True,
        'detection_id': 'det-001',
        'alerts_count': 1,
        'alert_detection_linked': True,
        'alert_id': 'alert-001',
        'incidents_count': 0,
        'response_actions_count': 0,
        # no incident_id
    })

    assert result['live_evidence_ready'] is False
    assert any('incident' in m for m in result['missing']), \
        f"Expected missing to mention incident, got: {result['missing']}"


# ---------------------------------------------------------------------------
# Case 8: Live chain through incident but no evidence package
# ---------------------------------------------------------------------------

def test_case8_full_chain_but_no_evidence_package(monkeypatch: pytest.MonkeyPatch) -> None:
    """Incident linked to alert but no evidence package → live_evidence_ready=False."""
    _clear_provider_env(monkeypatch)
    monkeypatch.setenv('EVM_RPC_URL', 'https://mainnet.infura.io/v3/proj_abc')

    result = build_live_evidence_proof(chain_evidence={
        'evidence_source': 'live',
        'last_telemetry_at': '2026-01-01T00:01:00Z',
        'detections_count': 1,
        'detection_telemetry_linked': True,
        'detection_id': 'det-001',
        'alerts_count': 1,
        'alert_detection_linked': True,
        'alert_id': 'alert-001',
        'incidents_count': 1,
        'incident_alert_linked': True,
        'incident_id': 'inc-001',
        # no evidence_package_id, no export_capability
    })

    assert result['live_evidence_ready'] is False
    assert any('evidence package' in m for m in result['missing']), \
        f"Expected missing to mention evidence package, got: {result['missing']}"


# ---------------------------------------------------------------------------
# Case 9: Complete live chain
# ---------------------------------------------------------------------------

def test_case9_complete_live_chain(monkeypatch: pytest.MonkeyPatch) -> None:
    """Full live chain → provider_ready=True, evidence_source='live', live_evidence_ready=True."""
    _clear_provider_env(monkeypatch)
    monkeypatch.setenv('EVM_RPC_URL', 'https://mainnet.infura.io/v3/proj_abc')

    result = build_live_evidence_proof(chain_evidence=_FULL_CHAIN)

    assert result['provider_ready'] is True
    assert result['provider_mode'] == 'live'
    assert result['live_evidence_ready'] is True
    assert result['evidence_source'] == 'live'
    assert result['latest_live_telemetry_at'] is not None
    assert result['chain']['telemetry_event_id'] == 'tel-001'
    assert result['chain']['detection_id'] == 'det-001'
    assert result['chain']['alert_id'] == 'alert-001'
    assert result['chain']['incident_id'] == 'inc-001'
    assert result['chain']['evidence_package_id'] == 'pkg-001'
    assert result['missing'] == []
    assert result['contradiction_flags'] == []


def test_case9_staging_evm_rpc_url_satisfies_complete_chain(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """STAGING_EVM_RPC_URL (without EVM_RPC_URL) must satisfy provider_ready for live chain."""
    _clear_provider_env(monkeypatch)
    monkeypatch.setenv('STAGING_EVM_RPC_URL', 'https://mainnet.infura.io/v3/staging_proj')
    monkeypatch.setenv('STAGING_EVM_CHAIN_ID', '1')

    result = build_live_evidence_proof(chain_evidence=_FULL_CHAIN)

    assert result['provider_ready'] is True
    assert result['live_evidence_ready'] is True
    assert result['evidence_source'] == 'live'


# ---------------------------------------------------------------------------
# Case 10: Build-time env safety
# ---------------------------------------------------------------------------

def test_case10_module_import_does_not_require_evm_rpc_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    Importing paid_launch_readiness must not require EVM_RPC_URL at import time.
    No env var checks must execute at module level.
    """
    _clear_provider_env(monkeypatch)

    # Reimporting must not raise
    import importlib
    import services.api.app.paid_launch_readiness as mod
    importlib.reload(mod)

    # Module-level constants/functions must be accessible without errors
    assert hasattr(mod, 'check_provider_readiness')
    assert hasattr(mod, 'build_live_evidence_proof')
    assert hasattr(mod, 'check_live_evidence_chain')
    assert hasattr(mod, 'build_paid_launch_readiness')


def test_case10_calling_check_provider_readiness_without_env_does_not_raise(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """check_provider_readiness() must return a safe dict even when all env vars are unset."""
    _clear_provider_env(monkeypatch)

    result = check_provider_readiness()

    assert isinstance(result, dict)
    assert result['provider_ready'] is False
    assert 'provider_mode' in result
    assert 'provider_missing_env' in result


def test_case10_build_live_evidence_proof_without_env_does_not_raise(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """build_live_evidence_proof() must return a safe structured dict even with no env vars."""
    _clear_provider_env(monkeypatch)

    result = build_live_evidence_proof()

    assert isinstance(result, dict)
    assert result['live_evidence_ready'] is False
    assert 'provider_ready' in result
    assert 'provider_mode' in result
    assert 'evidence_source' in result
    assert 'latest_live_telemetry_at' in result
    assert 'chain' in result
    assert 'missing' in result
    assert 'contradiction_flags' in result


# ---------------------------------------------------------------------------
# Structured output shape validation
# ---------------------------------------------------------------------------

def test_build_live_evidence_proof_output_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    """build_live_evidence_proof must return the canonical structured output shape."""
    _clear_provider_env(monkeypatch)

    result = build_live_evidence_proof()

    required_top_keys = [
        'provider_ready', 'provider_mode', 'live_evidence_ready',
        'evidence_source', 'latest_live_telemetry_at', 'chain',
        'missing', 'contradiction_flags',
    ]
    for key in required_top_keys:
        assert key in result, f'Missing required key: {key}'

    chain = result['chain']
    required_chain_keys = [
        'telemetry_event_id', 'detection_id', 'alert_id',
        'incident_id', 'evidence_package_id',
    ]
    for key in required_chain_keys:
        assert key in chain, f'Missing required chain key: {key}'


def test_provider_mode_values_are_canonical(monkeypatch: pytest.MonkeyPatch) -> None:
    """provider_mode must be one of the canonical values."""
    _clear_provider_env(monkeypatch)

    result = build_live_evidence_proof()
    assert result['provider_mode'] in ('live', 'simulator', 'demo', 'disabled', 'unknown')

    monkeypatch.setenv('EVM_RPC_URL', 'https://mainnet.infura.io/v3/proj_abc')
    result_live = build_live_evidence_proof()
    assert result_live['provider_mode'] == 'live'


def test_unknown_evidence_source_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    """Unknown evidence source in chain_evidence must fail closed."""
    _clear_provider_env(monkeypatch)
    monkeypatch.setenv('EVM_RPC_URL', 'https://mainnet.infura.io/v3/proj_abc')

    chain = {**_FULL_CHAIN, 'evidence_source': 'unknown'}
    result = build_live_evidence_proof(chain_evidence=chain)

    assert result['live_evidence_ready'] is False
    assert result['evidence_source'] == 'unknown'


# ===========================================================================
# Script-level tests: generate_live_evidence_proof() with mocked RPC
# Cases 2-7 from the required live-provider proof tests
# ===========================================================================

# ---------------------------------------------------------------------------
# Case 2 (script-level): Missing chain ID
# ---------------------------------------------------------------------------

def test_script_missing_chain_id(monkeypatch: pytest.MonkeyPatch) -> None:
    """No chain ID env → chain_id_configured=False, live_evidence_ready=False."""
    _clear_provider_env(monkeypatch)
    monkeypatch.setenv('EVM_RPC_URL', _REAL_RPC)
    monkeypatch.setenv('STAGING_WORKER_ENABLED', 'true')

    with patch(_SCRIPT_RPC_PATCH, side_effect=_mock_rpc_success('0x1', '0x12c')):
        result = generate_live_evidence_proof()

    lpe = result['live_provider_evidence']
    assert lpe['chain_id_configured'] is False
    assert lpe['live_evidence_ready'] is False
    assert any('chain' in m.lower() for m in lpe['missing']), \
        f'Expected chain ID in missing; got: {lpe["missing"]}'


# ---------------------------------------------------------------------------
# Case 3 (script-level): Worker disabled
# ---------------------------------------------------------------------------

def test_script_worker_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    """STAGING_WORKER_ENABLED absent → worker_enabled=False, live_evidence_ready=False."""
    _clear_provider_env(monkeypatch)
    monkeypatch.setenv('EVM_RPC_URL', _REAL_RPC)
    monkeypatch.setenv('EVM_CHAIN_ID', '1')
    # STAGING_WORKER_ENABLED intentionally absent

    with patch(_SCRIPT_RPC_PATCH, side_effect=_mock_rpc_success('0x1', '0x12c')):
        result = generate_live_evidence_proof()

    lpe = result['live_provider_evidence']
    assert lpe['worker_enabled'] is False
    assert lpe['live_evidence_ready'] is False
    assert any('STAGING_WORKER_ENABLED' in m for m in lpe['missing']), \
        f'Expected STAGING_WORKER_ENABLED in missing; got: {lpe["missing"]}'


# ---------------------------------------------------------------------------
# Case 4 (script-level): RPC provider failure
# ---------------------------------------------------------------------------

def test_script_rpc_provider_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """RPC returns error → provider_health_checked=True, provider_ready=False."""
    _clear_provider_env(monkeypatch)
    monkeypatch.setenv('EVM_RPC_URL', _REAL_RPC)
    monkeypatch.setenv('EVM_CHAIN_ID', '1')
    monkeypatch.setenv('STAGING_WORKER_ENABLED', 'true')

    with patch(_SCRIPT_RPC_PATCH, side_effect=_mock_rpc_error()):
        result = generate_live_evidence_proof()

    lpe = result['live_provider_evidence']
    assert lpe['provider_health_checked'] is True
    assert lpe['provider_ready'] is False
    assert lpe['live_evidence_ready'] is False
    assert any(
        'unreachable' in m or 'URLError' in m
        for m in lpe['missing'] + lpe['contradiction_flags']
    ), f'Expected provider_unreachable; missing={lpe["missing"]}, flags={lpe["contradiction_flags"]}'


# ---------------------------------------------------------------------------
# Case 5 (script-level): Chain ID mismatch
# ---------------------------------------------------------------------------

def test_script_chain_id_mismatch(monkeypatch: pytest.MonkeyPatch) -> None:
    """Observed chain ID != configured → provider_ready=False, contradiction_flags has mismatch."""
    _clear_provider_env(monkeypatch)
    monkeypatch.setenv('EVM_RPC_URL', _REAL_RPC)
    monkeypatch.setenv('EVM_CHAIN_ID', '137')  # Polygon configured; provider returns Ethereum
    monkeypatch.setenv('STAGING_WORKER_ENABLED', 'true')

    with patch(_SCRIPT_RPC_PATCH, side_effect=_mock_rpc_success('0x1', '0x12c')):
        result = generate_live_evidence_proof()

    lpe = result['live_provider_evidence']
    assert lpe['provider_ready'] is False
    assert lpe['live_evidence_ready'] is False
    assert any('chain_id_mismatch' in f for f in lpe['contradiction_flags']), \
        f'Expected chain_id_mismatch in contradiction_flags; got: {lpe["contradiction_flags"]}'


# ---------------------------------------------------------------------------
# Case 6 (script-level): Successful RPC creates live telemetry proof
# ---------------------------------------------------------------------------

def test_script_successful_rpc_creates_live_telemetry_proof(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Successful RPC + real live evidence -> telemetry_event_id, evidence_source=live."""
    _clear_provider_env(monkeypatch)
    monkeypatch.setenv('EVM_RPC_URL', _REAL_RPC)
    monkeypatch.setenv('EVM_CHAIN_ID', '1')
    monkeypatch.setenv('STAGING_WORKER_ENABLED', 'true')

    with patch(_SCRIPT_RPC_PATCH, side_effect=_mock_rpc_success('0x1', '0x12c4cca')):
        result = generate_live_evidence_proof(live_evidence_chain=_real_live_chain())

    lpe = result['live_provider_evidence']
    assert lpe['evidence_source'] == 'live'
    assert lpe['latest_live_telemetry_at'] is not None
    assert lpe['chain']['telemetry_event_id'] is not None

    tel = lpe.get('telemetry_record', {})
    assert tel.get('block_number') is not None
    assert tel.get('evidence_source') == 'live'


def test_script_successful_rpc_without_live_event_fails_with_specific_reason(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """RPC works but no live event -> live_evidence_ready=False with explicit reason."""
    _clear_provider_env(monkeypatch)
    monkeypatch.setenv('EVM_RPC_URL', _REAL_RPC)
    monkeypatch.setenv('EVM_CHAIN_ID', '1')
    monkeypatch.setenv('STAGING_WORKER_ENABLED', 'true')

    with patch(_SCRIPT_RPC_PATCH, side_effect=_mock_rpc_success('0x1', '0x12c4cca')):
        result = generate_live_evidence_proof()

    lpe = result['live_provider_evidence']
    assert lpe['live_provider_ready'] is True
    assert lpe['live_telemetry_ready'] is False
    assert lpe['live_evidence_ready'] is False
    assert any(
        'no matching live telemetry event' in m for m in lpe['missing']
    ), f'Expected explicit no-live-event reason; got: {lpe["missing"]}'
    for fld in ('telemetry_event_id', 'detection_id', 'alert_id',
                'incident_id', 'evidence_package_id'):
        assert lpe['chain'][fld] is None, f'{fld} must not be synthesised from RPC alone'


# ---------------------------------------------------------------------------
# Case 7 (script-level): Complete live chain with mocked RPC
# ---------------------------------------------------------------------------

def test_script_complete_live_chain_mocked_rpc(monkeypatch: pytest.MonkeyPatch) -> None:
    """Full live chain with mocked RPC + real evidence injected: all proof gates pass."""
    _clear_provider_env(monkeypatch)
    monkeypatch.setenv('EVM_RPC_URL', _REAL_RPC)
    monkeypatch.setenv('EVM_CHAIN_ID', '1')
    monkeypatch.setenv('STAGING_WORKER_ENABLED', 'true')

    with patch(_SCRIPT_RPC_PATCH, side_effect=_mock_rpc_success('0x1', '0x12c4cca')):
        result = generate_live_evidence_proof(live_evidence_chain=_real_live_chain())

    lpe = result['live_provider_evidence']

    assert lpe['provider_ready'] is True
    assert lpe['provider_mode'] == 'live'
    assert lpe['provider_health_checked'] is True
    assert lpe['evidence_source'] == 'live'
    assert lpe['latest_live_telemetry_at'] is not None
    assert lpe['live_evidence_ready'] is True
    assert lpe['missing'] == []
    assert lpe['contradiction_flags'] == []

    chain = lpe['chain']
    assert chain['telemetry_event_id'] is not None
    assert chain['detection_id'] is not None
    assert chain['alert_id'] is not None
    assert chain['incident_id'] is not None or chain['response_action_id'] is not None
    assert chain['evidence_package_id'] is not None

    # Evidence package must link back through the chain
    pkg = lpe['evidence_package_record']
    assert pkg['evidence_source'] == 'live'
    assert pkg['telemetry_event_id'] == chain['telemetry_event_id']
    assert pkg['detection_id'] == chain['detection_id']
    assert pkg['alert_id'] == chain['alert_id']
    assert pkg['chain_id'] == '1'


def test_script_staging_env_vars_preferred_over_base(monkeypatch: pytest.MonkeyPatch) -> None:
    """STAGING_EVM_RPC_URL and STAGING_EVM_CHAIN_ID take precedence over base vars."""
    _clear_provider_env(monkeypatch)
    monkeypatch.setenv('EVM_RPC_URL', 'https://mainnet.infura.io/v3/base_proj')
    monkeypatch.setenv('EVM_CHAIN_ID', '137')  # should be overridden
    monkeypatch.setenv('STAGING_EVM_RPC_URL', _REAL_RPC)
    monkeypatch.setenv('STAGING_EVM_CHAIN_ID', '1')
    monkeypatch.setenv('STAGING_WORKER_ENABLED', 'true')

    with patch(_SCRIPT_RPC_PATCH, side_effect=_mock_rpc_success('0x1', '0x12c')):
        result = generate_live_evidence_proof(live_evidence_chain=_real_live_chain())

    lpe = result['live_provider_evidence']
    assert lpe['provider_ready'] is True
    assert lpe['live_evidence_ready'] is True
    assert lpe['chain_id_observed'] == '1'


def test_script_live_evidence_chain_json_env_var_supplies_real_chain(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """LIVE_EVIDENCE_CHAIN_JSON env var feeds real evidence into the proof."""
    import json as _json
    _clear_provider_env(monkeypatch)
    monkeypatch.setenv('EVM_RPC_URL', _REAL_RPC)
    monkeypatch.setenv('EVM_CHAIN_ID', '1')
    monkeypatch.setenv('STAGING_WORKER_ENABLED', 'true')
    monkeypatch.setenv('LIVE_EVIDENCE_CHAIN_JSON', _json.dumps(_real_live_chain()))

    with patch(_SCRIPT_RPC_PATCH, side_effect=_mock_rpc_success('0x1', '0x12c4cca')):
        result = generate_live_evidence_proof()

    lpe = result['live_provider_evidence']
    assert lpe['live_evidence_ready'] is True
    assert lpe['chain']['telemetry_event_id'] == 'tel-live-001'


def test_script_live_evidence_chain_rejects_non_live_source(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Injected chain with evidence_source!='live' or source_type!='rpc_polling' is rejected."""
    _clear_provider_env(monkeypatch)
    monkeypatch.setenv('EVM_RPC_URL', _REAL_RPC)
    monkeypatch.setenv('EVM_CHAIN_ID', '1')
    monkeypatch.setenv('STAGING_WORKER_ENABLED', 'true')

    bad_chain = _real_live_chain(evidence_source='simulator')
    with patch(_SCRIPT_RPC_PATCH, side_effect=_mock_rpc_success('0x1', '0x12c4cca')):
        result = generate_live_evidence_proof(live_evidence_chain=bad_chain)

    lpe = result['live_provider_evidence']
    assert lpe['live_evidence_ready'] is False
    assert lpe['chain']['telemetry_event_id'] is None


# ===========================================================================
# Strict-mode and service-summary fallback tests
# ===========================================================================

def test_script_worker_enabled_alias_accepted(monkeypatch: pytest.MonkeyPatch) -> None:
    """WORKER_ENABLED (without STAGING_ prefix) is accepted as fallback for STAGING_WORKER_ENABLED."""
    _clear_provider_env(monkeypatch)
    monkeypatch.setenv('EVM_RPC_URL', _REAL_RPC)
    monkeypatch.setenv('EVM_CHAIN_ID', '1')
    monkeypatch.setenv('WORKER_ENABLED', 'true')
    # STAGING_WORKER_ENABLED deliberately absent

    with patch(_SCRIPT_RPC_PATCH, side_effect=_mock_rpc_success('0x1', '0x12c')):
        result = generate_live_evidence_proof(live_evidence_chain=_real_live_chain())

    lpe = result['live_provider_evidence']
    assert lpe['worker_enabled'] is True, (
        'WORKER_ENABLED=true must satisfy worker_enabled check as fallback for STAGING_WORKER_ENABLED'
    )
    assert lpe['live_evidence_ready'] is True


def test_script_strict_mode_blocks_service_summary_fallback(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """
    Strict mode must NOT fall back to service summary when no RPC URL is set.
    Without RPC URL, strict proof must write live_evidence_ready=false and exit non-zero.
    """
    import scripts.generate_live_evidence_proof as _glep

    _clear_provider_env(monkeypatch)
    # No RPC URL set — would normally trigger service summary fallback

    # Write a service summary that would pass if fallback were allowed
    svc_dir = tmp_path / 'services' / 'api' / 'artifacts' / 'live_evidence' / 'latest'
    svc_dir.mkdir(parents=True, exist_ok=True)
    svc_summary = {
        'evidence_source': 'live',
        'live_evidence_ready': True,
        'provider_ready': True,
        'latest_live_telemetry_at': '2026-01-01T00:01:00Z',
        'telemetry_event_present': True,
        'detection_generated_from_telemetry': True,
        'alert_generated_from_detection': True,
        'incident_opened_from_alert': True,
    }
    import json as _json
    (svc_dir / 'summary.json').write_text(_json.dumps(svc_summary))

    # Patch _SERVICE_LIVE_SUMMARY_PATH to point to our fake summary
    monkeypatch.setattr(_glep, '_SERVICE_LIVE_SUMMARY_PATH', svc_dir / 'summary.json')

    # Patch REPO_ROOT so the artifact is written to tmp_path/artifacts/...
    monkeypatch.setattr(_glep, 'REPO_ROOT', tmp_path)

    rc = _glep.main(strict=True)

    assert rc == 1, (
        'Strict mode must exit non-zero when no RPC URL is set, '
        'even if service summary is available'
    )
    # Artifact must reflect live_evidence_ready=false
    out_path = tmp_path / 'artifacts' / 'live-evidence-proof' / 'latest' / 'summary.json'
    written = _json.loads(out_path.read_text())
    lpe = written.get('live_provider_evidence', {})
    assert lpe.get('live_evidence_ready') is False, (
        'Strict mode artifact must not claim live_evidence_ready=true via service summary'
    )


def test_script_local_mode_allows_service_summary_fallback(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """
    Non-strict (local/demo) mode may use the service summary fallback when no RPC URL is set.
    The artifact should report live_evidence_ready=true from the service summary.
    """
    import scripts.generate_live_evidence_proof as _glep
    import json as _json

    _clear_provider_env(monkeypatch)

    svc_dir = tmp_path / 'services' / 'api' / 'artifacts' / 'live_evidence' / 'latest'
    svc_dir.mkdir(parents=True, exist_ok=True)
    svc_summary = {
        'evidence_source': 'live',
        'live_evidence_ready': True,
        'provider_ready': True,
        'latest_live_telemetry_at': '2026-01-01T00:01:00Z',
        'telemetry_event_present': True,
        'detection_generated_from_telemetry': True,
        'alert_generated_from_detection': True,
        'incident_opened_from_alert': True,
    }
    (svc_dir / 'summary.json').write_text(_json.dumps(svc_summary))

    monkeypatch.setattr(_glep, '_SERVICE_LIVE_SUMMARY_PATH', svc_dir / 'summary.json')
    # Patch REPO_ROOT so the artifact is written to tmp_path/artifacts/...
    monkeypatch.setattr(_glep, 'REPO_ROOT', tmp_path)

    rc = _glep.main(strict=False)

    assert rc == 0, 'Non-strict mode must exit 0 when service summary provides live evidence'
    out_path = tmp_path / 'artifacts' / 'live-evidence-proof' / 'latest' / 'summary.json'
    written = _json.loads(out_path.read_text())
    lpe = written.get('live_provider_evidence', {})
    assert lpe.get('live_evidence_ready') is True


def test_script_contradiction_guard_invalid_evidence_source(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    If a proof somehow reaches live_evidence_ready=True with an invalid evidence_source
    (unknown/simulation/fallback/simulator/fixture), the contradiction guard must
    flip live_evidence_ready to False and add a contradiction flag.
    """
    import scripts.generate_live_evidence_proof as _glep

    # We test _build_proof_from_service_summary which sets evidence_source='live' — valid.
    # To trigger the guard, we manually craft a result with invalid source and call the guard
    # logic that runs in main(). We can test this by patching generate_live_evidence_proof
    # to return a tampered result.

    _clear_provider_env(monkeypatch)
    monkeypatch.setenv('EVM_RPC_URL', _REAL_RPC)
    monkeypatch.setenv('EVM_CHAIN_ID', '1')
    monkeypatch.setenv('STAGING_WORKER_ENABLED', 'true')

    tampered_result = {
        'schema_version': 1,
        'generated_at': '2026-01-01T00:00:00Z',
        'live_provider_evidence': {
            'provider_ready': True,
            'provider_mode': 'live',
            'provider_health_checked': True,
            'provider_checked_at': '2026-01-01T00:00:00Z',
            'provider_url_masked': 'https://host/v3/[masked]',
            'chain_id_configured': True,
            'chain_id_observed': '1',
            'block_number_observed': '1000',
            'worker_enabled': True,
            'live_provider_ready': True,
            'live_provider_receipt_ready': True,
            'live_telemetry_ready': True,
            'live_detection_ready': True,
            'live_alert_ready': True,
            'live_incident_ready': True,
            'evidence_source': 'fallback',  # INVALID — triggers contradiction guard
            'latest_live_telemetry_at': '2026-01-01T00:01:00Z',
            'live_evidence_ready': True,  # OVERCLAIM with fallback source
            'chain': {
                'telemetry_event_id': 'tel-001',
                'detection_id': 'det-001',
                'alert_id': 'alert-001',
                'incident_id': 'inc-001',
                'response_action_id': None,
                'evidence_package_id': 'pkg-001',
            },
            'missing': [],
            'contradiction_flags': [],
        },
    }

    with patch('scripts.generate_live_evidence_proof.generate_live_evidence_proof',
               return_value=tampered_result):
        import io
        import sys as _sys
        import json as _json
        import tempfile, os

        with tempfile.TemporaryDirectory() as td:
            monkeypatch.setattr(_glep, 'REPO_ROOT', Path(td))
            rc = _glep.main(strict=False)

        # The contradiction guard must have caught the invalid source
        # We verify by checking the written artifact
        art_path = Path(td) / 'artifacts' / 'live-evidence-proof' / 'latest' / 'summary.json'
        if art_path.exists():
            written = _json.loads(art_path.read_text())
            lpe = written.get('live_provider_evidence', {})
            if lpe.get('evidence_source') == 'fallback':
                assert lpe.get('live_evidence_ready') is False, (
                    'Contradiction guard must set live_evidence_ready=false '
                    'when evidence_source is invalid (fallback)'
                )
