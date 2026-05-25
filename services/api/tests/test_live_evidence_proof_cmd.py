"""
Tests for scripts/generate_live_evidence_proof.py

14 canonical cases covering:
1.  Missing RPC env → fail closed
2.  Missing chain ID → fail closed
3.  Worker disabled → fail closed
4.  RPC provider error → fail closed
5.  Chain ID mismatch → fail closed
6.  Heartbeat-only (simulated via chain_evidence; command level: no telemetry)
7.  Poll-only (command level: no telemetry from RPC)
8.  Simulator/demo evidence → fail closed (validated via staging proof path)
9.  Live telemetry without detection chain → fail closed
10. Live telemetry + detection without alert → fail closed
11. Live telemetry + detection + alert without incident/response → fail closed
12. Live telemetry + detection + alert + incident without evidence package → fail closed
13. Complete live chain → all gates pass
14. Build-time safety: importing module does not require EVM_RPC_URL
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

from scripts.generate_live_evidence_proof import generate_live_evidence_proof, _rpc_call

_PROVIDER_ENV_VARS = [
    'EVM_RPC_URL', 'STAGING_EVM_RPC_URL',
    'EVM_CHAIN_ID', 'STAGING_EVM_CHAIN_ID', 'CHAIN_ID',
    'STAGING_WORKER_ENABLED',
    'LIVE_EVIDENCE_CHAIN_JSON', 'LIVE_EVIDENCE_CHAIN_FILE',
]


def _real_live_chain() -> dict[str, Any]:
    """Canonical real live-event chain captured by the monitoring worker."""
    return {
        'telemetry_event_id': 'tel-live-001',
        'detection_id': 'det-live-001',
        'alert_id': 'alert-live-001',
        'incident_id': 'inc-live-001',
        'response_action_id': 'ra-live-001',
        'evidence_package_id': 'pkg-live-001',
        'evidence_source': 'live',
        'source_type': 'rpc_polling',
        'observed_at': '2026-05-22T12:00:00+00:00',
    }

_REAL_RPC = 'https://mainnet.infura.io/v3/test_proj'


def _clear_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in _PROVIDER_ENV_VARS:
        monkeypatch.delenv(var, raising=False)


def _mock_rpc_success(chain_id_hex: str = '0x1', block_hex: str = '0x12c') -> dict[str, Any]:
    """Return a side_effect callable that alternates chain_id then block_number responses."""
    responses = iter([
        {'result': chain_id_hex, 'jsonrpc': '2.0', 'id': 1},
        {'result': block_hex, 'jsonrpc': '2.0', 'id': 1},
    ])

    def _side_effect(url: str, method: str, params: list | None = None, timeout: int = 10):
        return next(responses)

    return _side_effect


def _mock_rpc_error() -> Any:
    def _side_effect(url: str, method: str, params: list | None = None, timeout: int = 10):
        return {'error': 'URLError: <urlopen error [Errno 111] Connection refused>'}
    return _side_effect


# ---------------------------------------------------------------------------
# Case 1: Missing RPC env
# ---------------------------------------------------------------------------

def test_case1_missing_both_rpc_urls(monkeypatch: pytest.MonkeyPatch) -> None:
    """No RPC env vars → provider_ready=False, live_evidence_ready=False, missing includes RPC."""
    _clear_env(monkeypatch)

    result = generate_live_evidence_proof()
    lpe = result['live_provider_evidence']

    assert lpe['provider_ready'] is False
    assert lpe['live_evidence_ready'] is False
    assert lpe['provider_mode'] == 'disabled'
    assert lpe['provider_health_checked'] is False
    assert lpe['latest_live_telemetry_at'] is None
    assert any('EVM_RPC_URL' in m for m in lpe['missing']), \
        f"Expected missing to mention EVM_RPC_URL; got: {lpe['missing']}"


def test_case1_staging_rpc_url_alone_satisfies_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """STAGING_EVM_RPC_URL without EVM_RPC_URL should still configure provider."""
    _clear_env(monkeypatch)
    monkeypatch.setenv('STAGING_EVM_RPC_URL', _REAL_RPC)
    monkeypatch.setenv('STAGING_WORKER_ENABLED', 'true')

    with patch(
        'scripts.generate_live_evidence_proof._rpc_call',
        side_effect=_mock_rpc_success('0x1', '0x12c'),
    ):
        result = generate_live_evidence_proof()

    lpe = result['live_provider_evidence']
    assert lpe['provider_health_checked'] is True
    assert lpe['provider_ready'] is True


# ---------------------------------------------------------------------------
# Case 2: Missing chain ID
# ---------------------------------------------------------------------------

def test_case2_missing_chain_id(monkeypatch: pytest.MonkeyPatch) -> None:
    """No chain ID env → chain_id_configured=False, listed in missing."""
    _clear_env(monkeypatch)
    monkeypatch.setenv('EVM_RPC_URL', _REAL_RPC)
    monkeypatch.setenv('STAGING_WORKER_ENABLED', 'true')

    with patch(
        'scripts.generate_live_evidence_proof._rpc_call',
        side_effect=_mock_rpc_success('0x1', '0x12c'),
    ):
        result = generate_live_evidence_proof()

    lpe = result['live_provider_evidence']
    assert lpe['chain_id_configured'] is False
    assert lpe['live_evidence_ready'] is False
    assert any('chain' in m.lower() for m in lpe['missing']), \
        f"Expected chain ID in missing; got: {lpe['missing']}"


# ---------------------------------------------------------------------------
# Case 3: Worker disabled
# ---------------------------------------------------------------------------

def test_case3_worker_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    """STAGING_WORKER_ENABLED not set → live_evidence_ready=False, missing includes worker."""
    _clear_env(monkeypatch)
    monkeypatch.setenv('EVM_RPC_URL', _REAL_RPC)
    monkeypatch.setenv('EVM_CHAIN_ID', '1')
    # STAGING_WORKER_ENABLED intentionally absent

    with patch(
        'scripts.generate_live_evidence_proof._rpc_call',
        side_effect=_mock_rpc_success('0x1', '0x12c'),
    ):
        result = generate_live_evidence_proof()

    lpe = result['live_provider_evidence']
    assert lpe['worker_enabled'] is False
    assert lpe['live_evidence_ready'] is False
    assert any('STAGING_WORKER_ENABLED' in m for m in lpe['missing']), \
        f"Expected STAGING_WORKER_ENABLED in missing; got: {lpe['missing']}"


def test_case3_worker_false_string(monkeypatch: pytest.MonkeyPatch) -> None:
    """STAGING_WORKER_ENABLED=false → worker_enabled=False."""
    _clear_env(monkeypatch)
    monkeypatch.setenv('EVM_RPC_URL', _REAL_RPC)
    monkeypatch.setenv('EVM_CHAIN_ID', '1')
    monkeypatch.setenv('STAGING_WORKER_ENABLED', 'false')

    with patch(
        'scripts.generate_live_evidence_proof._rpc_call',
        side_effect=_mock_rpc_success('0x1', '0x12c'),
    ):
        result = generate_live_evidence_proof()

    lpe = result['live_provider_evidence']
    assert lpe['worker_enabled'] is False
    assert lpe['live_evidence_ready'] is False


# ---------------------------------------------------------------------------
# Case 4: RPC provider error (connection refused, timeout, etc.)
# ---------------------------------------------------------------------------

def test_case4_rpc_provider_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """RPC call returns error → provider_health_checked=True, provider_ready=False."""
    _clear_env(monkeypatch)
    monkeypatch.setenv('EVM_RPC_URL', _REAL_RPC)
    monkeypatch.setenv('EVM_CHAIN_ID', '1')
    monkeypatch.setenv('STAGING_WORKER_ENABLED', 'true')

    with patch(
        'scripts.generate_live_evidence_proof._rpc_call',
        side_effect=_mock_rpc_error(),
    ):
        result = generate_live_evidence_proof()

    lpe = result['live_provider_evidence']
    assert lpe['provider_health_checked'] is True
    assert lpe['provider_ready'] is False
    assert lpe['live_evidence_ready'] is False
    assert any('unreachable' in m or 'URLError' in m for m in lpe['missing'] + lpe['contradiction_flags']), \
        f"Expected provider_unreachable flag; got missing={lpe['missing']}, flags={lpe['contradiction_flags']}"


# ---------------------------------------------------------------------------
# Case 5: Chain ID mismatch
# ---------------------------------------------------------------------------

def test_case5_chain_id_mismatch(monkeypatch: pytest.MonkeyPatch) -> None:
    """Observed chain ID != configured → provider_ready=False, contradiction_flags has mismatch."""
    _clear_env(monkeypatch)
    monkeypatch.setenv('EVM_RPC_URL', _REAL_RPC)
    monkeypatch.setenv('EVM_CHAIN_ID', '137')  # Polygon, but provider returns Ethereum mainnet
    monkeypatch.setenv('STAGING_WORKER_ENABLED', 'true')

    with patch(
        'scripts.generate_live_evidence_proof._rpc_call',
        side_effect=_mock_rpc_success('0x1', '0x12c'),  # returns chain ID 1 (Ethereum mainnet)
    ):
        result = generate_live_evidence_proof()

    lpe = result['live_provider_evidence']
    assert lpe['provider_ready'] is False
    assert lpe['live_evidence_ready'] is False
    assert any('chain_id_mismatch' in f for f in lpe['contradiction_flags']), \
        f"Expected chain_id_mismatch in contradiction_flags; got: {lpe['contradiction_flags']}"
    assert any('mismatch' in m for m in lpe['missing']), \
        f"Expected mismatch in missing; got: {lpe['missing']}"


# ---------------------------------------------------------------------------
# Case 6: Heartbeat-only (no real block telemetry)
# Note: at the command level, "heartbeat" is the worker being reachable but
# returning no telemetry. We simulate this via the paid_launch_readiness path.
# At the generate_live_evidence_proof level, any missing chain item → fail.
# ---------------------------------------------------------------------------

def test_case6_heartbeat_only_via_paid_launch_readiness(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Heartbeat-only state via build_live_evidence_proof: live_evidence_ready=False."""
    from services.api.app.paid_launch_readiness import build_live_evidence_proof

    _clear_env(monkeypatch)
    monkeypatch.setenv('EVM_RPC_URL', _REAL_RPC)

    result = build_live_evidence_proof(chain_evidence={
        'evidence_source': 'live',
        'last_heartbeat_at': '2026-01-01T00:00:00Z',
        # no last_telemetry_at
    })

    assert result['live_evidence_ready'] is False
    assert result['latest_live_telemetry_at'] is None
    assert any('heartbeat' in m for m in result['missing'])


# ---------------------------------------------------------------------------
# Case 7: Poll-only (no real telemetry)
# ---------------------------------------------------------------------------

def test_case7_poll_only_via_paid_launch_readiness(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Poll-only state via build_live_evidence_proof: live_evidence_ready=False."""
    from services.api.app.paid_launch_readiness import build_live_evidence_proof

    _clear_env(monkeypatch)
    monkeypatch.setenv('EVM_RPC_URL', _REAL_RPC)

    result = build_live_evidence_proof(chain_evidence={
        'evidence_source': 'live',
        'latest_poll_at': '2026-01-01T00:00:30Z',
        # no last_telemetry_at
    })

    assert result['live_evidence_ready'] is False
    assert any('poll' in m for m in result['missing'])


# ---------------------------------------------------------------------------
# Case 8: Simulator/demo evidence → rejected
# ---------------------------------------------------------------------------

@pytest.mark.parametrize('source', ['simulator', 'guided_simulator', 'fixture', 'demo'])
def test_case8_simulator_evidence_rejected(
    monkeypatch: pytest.MonkeyPatch,
    source: str,
) -> None:
    """Simulator/demo/fixture evidence_source → live_evidence_ready=False."""
    from services.api.app.paid_launch_readiness import build_live_evidence_proof

    _clear_env(monkeypatch)
    monkeypatch.setenv('EVM_RPC_URL', _REAL_RPC)

    result = build_live_evidence_proof(chain_evidence={
        'evidence_source': source,
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
    })

    assert result['live_evidence_ready'] is False
    assert any('not live provider evidence' in f for f in result['contradiction_flags']), \
        f"Expected 'not live provider evidence' for source={source!r}; got: {result['contradiction_flags']}"


# ---------------------------------------------------------------------------
# Case 9: Live telemetry without detection
# ---------------------------------------------------------------------------

def test_case9_live_telemetry_no_detection(monkeypatch: pytest.MonkeyPatch) -> None:
    """Live telemetry present but no detection → live_evidence_ready=False."""
    from services.api.app.paid_launch_readiness import build_live_evidence_proof

    _clear_env(monkeypatch)
    monkeypatch.setenv('EVM_RPC_URL', _REAL_RPC)

    result = build_live_evidence_proof(chain_evidence={
        'evidence_source': 'live',
        'last_telemetry_at': '2026-01-01T00:01:00Z',
        'detections_count': 0,
    })

    assert result['live_evidence_ready'] is False
    assert result['latest_live_telemetry_at'] is not None
    assert any('detection' in m for m in result['missing'])


# ---------------------------------------------------------------------------
# Case 10: Live telemetry + detection but no alert
# ---------------------------------------------------------------------------

def test_case10_telemetry_detection_no_alert(monkeypatch: pytest.MonkeyPatch) -> None:
    """Detection exists but no alert → live_evidence_ready=False."""
    from services.api.app.paid_launch_readiness import build_live_evidence_proof

    _clear_env(monkeypatch)
    monkeypatch.setenv('EVM_RPC_URL', _REAL_RPC)

    result = build_live_evidence_proof(chain_evidence={
        'evidence_source': 'live',
        'last_telemetry_at': '2026-01-01T00:01:00Z',
        'detections_count': 1,
        'detection_telemetry_linked': True,
        'detection_id': 'det-001',
        'alerts_count': 0,
    })

    assert result['live_evidence_ready'] is False
    assert any('alert' in m for m in result['missing'])


# ---------------------------------------------------------------------------
# Case 11: Live telemetry + detection + alert but no incident/response
# ---------------------------------------------------------------------------

def test_case11_telemetry_detection_alert_no_incident(monkeypatch: pytest.MonkeyPatch) -> None:
    """Alert exists but no incident or response_action → live_evidence_ready=False."""
    from services.api.app.paid_launch_readiness import build_live_evidence_proof

    _clear_env(monkeypatch)
    monkeypatch.setenv('EVM_RPC_URL', _REAL_RPC)

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
    })

    assert result['live_evidence_ready'] is False
    assert any('incident' in m for m in result['missing'])


# ---------------------------------------------------------------------------
# Case 12: Full chain through incident but no evidence package
# ---------------------------------------------------------------------------

def test_case12_full_chain_no_evidence_package(monkeypatch: pytest.MonkeyPatch) -> None:
    """Incident exists but no evidence package → live_evidence_ready=False."""
    from services.api.app.paid_launch_readiness import build_live_evidence_proof

    _clear_env(monkeypatch)
    monkeypatch.setenv('EVM_RPC_URL', _REAL_RPC)

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
        # no evidence_package_id
    })

    assert result['live_evidence_ready'] is False
    assert any('evidence package' in m for m in result['missing'])


# ---------------------------------------------------------------------------
# Case 13: Complete live chain via generate_live_evidence_proof (with mocked RPC)
# ---------------------------------------------------------------------------

def test_case13_complete_live_chain_with_real_rpc_mock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Full live chain with mocked successful RPC + real injected evidence: all gates pass."""
    _clear_env(monkeypatch)
    monkeypatch.setenv('EVM_RPC_URL', _REAL_RPC)
    monkeypatch.setenv('EVM_CHAIN_ID', '1')
    monkeypatch.setenv('STAGING_WORKER_ENABLED', 'true')

    with patch(
        'scripts.generate_live_evidence_proof._rpc_call',
        side_effect=_mock_rpc_success('0x1', '0x12c4cca'),
    ):
        result = generate_live_evidence_proof(live_evidence_chain=_real_live_chain())

    lpe = result['live_provider_evidence']

    assert lpe['provider_ready'] is True
    assert lpe['provider_mode'] == 'live'
    assert lpe['provider_health_checked'] is True
    assert lpe['evidence_source'] == 'live'
    assert lpe['latest_live_telemetry_at'] is not None
    assert lpe['live_evidence_ready'] is True
    assert lpe['chain_id_observed'] == '1'
    assert lpe['chain_id_configured'] is True
    assert lpe['worker_enabled'] is True
    assert lpe['missing'] == []
    assert lpe['contradiction_flags'] == []

    chain = lpe['chain']
    assert chain['telemetry_event_id'] is not None
    assert chain['detection_id'] is not None
    assert chain['alert_id'] is not None
    assert chain['incident_id'] is not None
    assert chain['response_action_id'] is not None
    assert chain['evidence_package_id'] is not None

    # Verify evidence package links back through chain
    pkg = lpe['evidence_package_record']
    assert pkg['evidence_source'] == 'live'
    assert pkg['telemetry_event_id'] == chain['telemetry_event_id']
    assert pkg['detection_id'] == chain['detection_id']
    assert pkg['alert_id'] == chain['alert_id']
    assert pkg['incident_id'] == chain['incident_id']
    assert pkg['chain_id'] == '1'


def test_case13_staging_env_vars_preferred(monkeypatch: pytest.MonkeyPatch) -> None:
    """STAGING_EVM_RPC_URL and STAGING_EVM_CHAIN_ID take precedence over base vars."""
    _clear_env(monkeypatch)
    monkeypatch.setenv('EVM_RPC_URL', 'https://mainnet.infura.io/v3/base_proj')
    monkeypatch.setenv('EVM_CHAIN_ID', '137')  # This should be overridden
    monkeypatch.setenv('STAGING_EVM_RPC_URL', _REAL_RPC)
    monkeypatch.setenv('STAGING_EVM_CHAIN_ID', '1')
    monkeypatch.setenv('STAGING_WORKER_ENABLED', 'true')

    with patch(
        'scripts.generate_live_evidence_proof._rpc_call',
        side_effect=_mock_rpc_success('0x1', '0x12c'),
    ):
        result = generate_live_evidence_proof(live_evidence_chain=_real_live_chain())

    lpe = result['live_provider_evidence']
    assert lpe['provider_ready'] is True
    assert lpe['live_evidence_ready'] is True
    assert lpe['chain_id_observed'] == '1'


# ---------------------------------------------------------------------------
# Case 14: Build-time safety
# ---------------------------------------------------------------------------

def test_case14_import_does_not_require_evm_rpc_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Importing generate_live_evidence_proof must not require EVM_RPC_URL."""
    for var in _PROVIDER_ENV_VARS:
        monkeypatch.delenv(var, raising=False)

    import importlib
    import scripts.generate_live_evidence_proof as mod
    importlib.reload(mod)

    assert hasattr(mod, 'generate_live_evidence_proof')
    assert hasattr(mod, '_rpc_call')
    assert hasattr(mod, 'main')


def test_case14_generate_without_env_does_not_raise(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """generate_live_evidence_proof() must return a safe dict even with no env vars."""
    for var in _PROVIDER_ENV_VARS:
        monkeypatch.delenv(var, raising=False)

    result = generate_live_evidence_proof()

    assert isinstance(result, dict)
    lpe = result.get('live_provider_evidence', {})
    assert lpe.get('live_evidence_ready') is False
    assert lpe.get('provider_ready') is False
    assert isinstance(lpe.get('missing'), list)
    assert isinstance(lpe.get('contradiction_flags'), list)
    assert isinstance(lpe.get('chain'), dict)


def test_case14_output_schema_complete(monkeypatch: pytest.MonkeyPatch) -> None:
    """generate_live_evidence_proof must always return the required schema shape."""
    for var in _PROVIDER_ENV_VARS:
        monkeypatch.delenv(var, raising=False)

    result = generate_live_evidence_proof()

    assert 'schema_version' in result
    assert 'generated_at' in result
    assert 'live_provider_evidence' in result

    lpe = result['live_provider_evidence']
    required_fields = [
        'provider_ready', 'provider_mode', 'provider_health_checked',
        'provider_checked_at', 'provider_url_masked',
        'chain_id_configured', 'chain_id_observed',
        'worker_enabled', 'evidence_source', 'latest_live_telemetry_at',
        'live_evidence_ready', 'chain', 'missing', 'contradiction_flags',
    ]
    for field in required_fields:
        assert field in lpe, f'Missing required field: {field}'

    chain = lpe['chain']
    required_chain_keys = [
        'telemetry_event_id', 'detection_id', 'alert_id',
        'incident_id', 'response_action_id', 'evidence_package_id',
    ]
    for key in required_chain_keys:
        assert key in chain, f'Missing required chain key: {key}'
