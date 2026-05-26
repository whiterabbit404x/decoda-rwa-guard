"""
Criterion 3 split proof state tests.

Covers the new live_provider_ready / live_provider_receipt_ready /
live_telemetry_ready / live_detection_ready / live_alert_ready /
live_incident_ready flags added to build_live_evidence_proof() and
generate_live_evidence_proof().

Key invariants:
- STAGING_EVM_RPC_URL alone satisfies provider_ready (no false "not configured").
- checked=1 + receipt checkpoint => live_provider_ready=True.
- event_count=0 (no rpc_polling events) => live_evidence_ready=False.
- live telemetry (source_type=rpc_polling) creates live_detection_ready.
- detection => live_alert_ready via alert.
- alert => live_incident_ready via incident.
- evidence export must carry all live IDs.
- Simulated data never satisfies live_evidence_ready.
"""
from __future__ import annotations

import sys
from pathlib import Path
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
_REAL_RPC = 'https://mainnet.infura.io/v3/test_proj_abc123'

_PROVIDER_ENV_VARS = [
    'EVM_RPC_URL', 'STAGING_EVM_RPC_URL',
    'EVM_CHAIN_ID', 'STAGING_EVM_CHAIN_ID', 'CHAIN_ID',
    'STAGING_WORKER_ENABLED',
]


def _clear_provider_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in _PROVIDER_ENV_VARS:
        monkeypatch.delenv(var, raising=False)


def _mock_rpc_success(chain_id_hex: str = '0x1', block_hex: str = '0x12c4cca'):
    """Two-response mock: eth_chainId then eth_blockNumber. 3rd call fails gracefully."""
    import itertools
    responses = iter([
        {'result': chain_id_hex, 'jsonrpc': '2.0', 'id': 1},
        {'result': block_hex, 'jsonrpc': '2.0', 'id': 1},
    ])

    def _side(url, method, params=None, timeout=10):
        return next(responses)

    return _side


def _real_live_chain(**overrides) -> dict:
    """A canonical real live-event chain (telemetry -> ... -> evidence package).

    Represents evidence captured by the monitoring worker (source_type=rpc_polling,
    evidence_source=live). Tests use this to satisfy live_evidence_ready=True
    without faking IDs from eth_chainId or eth_blockNumber alone.
    """
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


# ---------------------------------------------------------------------------
# Task 2 — env detection: STAGING_EVM_RPC_URL prevents false "not configured"
# ---------------------------------------------------------------------------

def test_staging_rpc_url_satisfies_provider_no_not_configured_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """STAGING_EVM_RPC_URL set alone must not produce 'EVM_RPC_URL not configured'."""
    _clear_provider_env(monkeypatch)
    monkeypatch.setenv('STAGING_EVM_RPC_URL', 'https://mainnet.infura.io/v3/stg123')

    out = check_provider_readiness()

    assert out['provider_ready'] is True
    assert out['provider_mode'] == 'live'
    assert out['provider_missing_env'] == []
    assert 'not configured' not in out.get('provider_reason', '').lower() or \
        'STAGING_EVM_RPC_URL' in out.get('provider_reason', '')


def test_staging_rpc_url_with_staging_chain_id_provider_ready(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """STAGING_EVM_RPC_URL + STAGING_EVM_CHAIN_ID => provider_ready=True."""
    _clear_provider_env(monkeypatch)
    monkeypatch.setenv('STAGING_EVM_RPC_URL', 'https://mainnet.infura.io/v3/stg123')
    monkeypatch.setenv('STAGING_EVM_CHAIN_ID', '1')

    out = check_provider_readiness()

    assert out['provider_ready'] is True
    assert out['chain_id_configured'] is True
    assert out['provider_missing_env'] == []


def test_missing_both_rpc_urls_reports_missing_correctly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When both EVM_RPC_URL and STAGING_EVM_RPC_URL are absent, provider is disabled."""
    _clear_provider_env(monkeypatch)

    out = check_provider_readiness()

    assert out['provider_ready'] is False
    assert out['provider_mode'] == 'disabled'
    # Must mention both options in the reason (not just EVM_RPC_URL silently)
    combined = out.get('provider_reason', '') + ' '.join(out.get('provider_missing_env', []))
    assert 'EVM_RPC_URL' in combined


# ---------------------------------------------------------------------------
# Task 3 — split proof states
# ---------------------------------------------------------------------------

def test_checked_1_and_receipt_sets_live_provider_ready(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """checked=1 + receipts_written=1 (worker evidence) => live_provider_ready=True."""
    _clear_provider_env(monkeypatch)
    monkeypatch.setenv('EVM_RPC_URL', 'https://mainnet.infura.io/v3/proj_abc')

    result = build_live_evidence_proof(chain_evidence={
        'evidence_source': 'live',
        'monitoring_checked_count': 1,
        'receipts_written': 1,
        # no actual telemetry events
    })

    assert result['live_provider_ready'] is True
    assert result['live_provider_receipt_ready'] is True


def test_receipt_checkpoint_alone_sets_live_provider_receipt_ready(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """receipt_checkpoint=True in chain_evidence => live_provider_receipt_ready=True."""
    _clear_provider_env(monkeypatch)
    monkeypatch.setenv('EVM_RPC_URL', 'https://mainnet.infura.io/v3/proj_abc')

    result = build_live_evidence_proof(chain_evidence={
        'evidence_source': 'live',
        'receipt_checkpoint': True,
    })

    assert result['live_provider_receipt_ready'] is True


def test_event_count_0_does_not_set_live_evidence_ready(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Worker event_count=0: no telemetry events => live_evidence_ready=False."""
    _clear_provider_env(monkeypatch)
    monkeypatch.setenv('EVM_RPC_URL', 'https://mainnet.infura.io/v3/proj_abc')

    result = build_live_evidence_proof(chain_evidence={
        'evidence_source': 'live',
        'monitoring_checked_count': 1,
        'receipts_written': 1,
        'event_count': 0,
        # no telemetry_event_id, detections_count=0
    })

    assert result['live_evidence_ready'] is False
    assert result['live_provider_ready'] is True    # provider IS live
    assert result['live_provider_receipt_ready'] is True
    # No rpc_polling telemetry event exists
    assert result['live_telemetry_ready'] is False


def test_event_count_0_does_not_set_live_telemetry_ready(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Coverage telemetry (coverage_persisted=True) without rpc_polling events: live_telemetry_ready=False."""
    _clear_provider_env(monkeypatch)
    monkeypatch.setenv('EVM_RPC_URL', 'https://mainnet.infura.io/v3/proj_abc')

    result = build_live_evidence_proof(chain_evidence={
        'evidence_source': 'live',
        'monitoring_checked_count': 1,
        'receipts_written': 1,
        # last_telemetry_at can be set from coverage timestamp
        'last_telemetry_at': '2026-01-01T00:01:00Z',
        # but no telemetry_event_id (event_count=0) and no source_type
    })

    # Coverage timestamp does not make live_telemetry_ready=True
    assert result['live_telemetry_ready'] is False
    # live_evidence_ready still False (no detection chain)
    assert result['live_evidence_ready'] is False


def test_rpc_polling_source_type_sets_live_telemetry_ready(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """source_type='rpc_polling' in chain_evidence => live_telemetry_ready=True."""
    _clear_provider_env(monkeypatch)
    monkeypatch.setenv('EVM_RPC_URL', 'https://mainnet.infura.io/v3/proj_abc')

    result = build_live_evidence_proof(chain_evidence={
        'evidence_source': 'live',
        'last_telemetry_at': '2026-01-01T00:01:00Z',
        'source_type': 'rpc_polling',
    })

    assert result['live_telemetry_ready'] is True


def test_live_telemetry_creates_detection_ready(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Live rpc_polling telemetry + detection_id => live_detection_ready=True."""
    _clear_provider_env(monkeypatch)
    monkeypatch.setenv('EVM_RPC_URL', 'https://mainnet.infura.io/v3/proj_abc')

    result = build_live_evidence_proof(chain_evidence={
        'evidence_source': 'live',
        'last_telemetry_at': '2026-01-01T00:01:00Z',
        'source_type': 'rpc_polling',
        'telemetry_event_id': 'tel-001',
        'detections_count': 1,
        'detection_telemetry_linked': True,
        'detection_id': 'det-001',
        # no alert yet
    })

    assert result['live_telemetry_ready'] is True
    assert result['live_detection_ready'] is True
    assert result['live_alert_ready'] is False


def test_detection_creates_alert_ready(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Detection + alert_id => live_alert_ready=True."""
    _clear_provider_env(monkeypatch)
    monkeypatch.setenv('EVM_RPC_URL', 'https://mainnet.infura.io/v3/proj_abc')

    result = build_live_evidence_proof(chain_evidence={
        'evidence_source': 'live',
        'last_telemetry_at': '2026-01-01T00:01:00Z',
        'source_type': 'rpc_polling',
        'telemetry_event_id': 'tel-001',
        'detections_count': 1,
        'detection_telemetry_linked': True,
        'detection_id': 'det-001',
        'alerts_count': 1,
        'alert_detection_linked': True,
        'alert_id': 'alert-001',
        # no incident yet
    })

    assert result['live_detection_ready'] is True
    assert result['live_alert_ready'] is True
    assert result['live_incident_ready'] is False


def test_alert_creates_incident_ready(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Alert + incident_id => live_incident_ready=True."""
    _clear_provider_env(monkeypatch)
    monkeypatch.setenv('EVM_RPC_URL', 'https://mainnet.infura.io/v3/proj_abc')

    result = build_live_evidence_proof(chain_evidence={
        'evidence_source': 'live',
        'last_telemetry_at': '2026-01-01T00:01:00Z',
        'source_type': 'rpc_polling',
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
        # no evidence_package yet
    })

    assert result['live_alert_ready'] is True
    assert result['live_incident_ready'] is True


def test_evidence_export_includes_all_live_ids(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Complete chain: all IDs present in chain dict."""
    _clear_provider_env(monkeypatch)
    monkeypatch.setenv('EVM_RPC_URL', 'https://mainnet.infura.io/v3/proj_abc')

    result = build_live_evidence_proof(chain_evidence={
        'evidence_source': 'live',
        'last_telemetry_at': '2026-01-01T00:01:00Z',
        'source_type': 'rpc_polling',
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

    assert result['live_evidence_ready'] is True
    chain = result['chain']
    assert chain['telemetry_event_id'] == 'tel-001'
    assert chain['detection_id'] == 'det-001'
    assert chain['alert_id'] == 'alert-001'
    assert chain['incident_id'] == 'inc-001'
    assert chain['evidence_package_id'] == 'pkg-001'

    # All split state flags must be True
    assert result['live_provider_ready'] is True
    assert result['live_provider_receipt_ready'] is False  # no receipts_written in this evidence
    assert result['live_telemetry_ready'] is True
    assert result['live_detection_ready'] is True
    assert result['live_alert_ready'] is True
    assert result['live_incident_ready'] is True


def test_simulated_data_cannot_satisfy_live_evidence_ready(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Simulator evidence_source => live_evidence_ready=False regardless of chain."""
    _clear_provider_env(monkeypatch)
    monkeypatch.setenv('EVM_RPC_URL', 'https://mainnet.infura.io/v3/proj_abc')

    full_chain_simulator = {
        'evidence_source': 'simulator',
        'last_telemetry_at': '2026-01-01T00:01:00Z',
        'telemetry_event_id': 'tel-sim-001',
        'detections_count': 1,
        'detection_telemetry_linked': True,
        'detection_id': 'det-sim-001',
        'alerts_count': 1,
        'alert_detection_linked': True,
        'alert_id': 'alert-sim-001',
        'incidents_count': 1,
        'incident_alert_linked': True,
        'incident_id': 'inc-sim-001',
        'evidence_package_id': 'pkg-sim-001',
        'export_capability': 'pass',
        'export_source_label': 'live',
        'contradiction_flags': [],
    }

    result = build_live_evidence_proof(chain_evidence=full_chain_simulator)

    assert result['live_evidence_ready'] is False
    assert result['evidence_source'] == 'simulator'
    assert any('not live provider evidence' in f for f in result['contradiction_flags']), \
        f'Expected contradiction flag, got: {result["contradiction_flags"]}'


# ---------------------------------------------------------------------------
# Task 9 — proof output shape includes all required keys
# ---------------------------------------------------------------------------

def test_build_live_evidence_proof_output_includes_all_required_flags(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """build_live_evidence_proof must include all Criterion 3 required output keys."""
    _clear_provider_env(monkeypatch)

    result = build_live_evidence_proof()

    required = [
        'provider_ready', 'provider_mode',
        'live_provider_ready', 'live_provider_receipt_ready',
        'live_telemetry_ready', 'live_detection_ready',
        'live_alert_ready', 'live_incident_ready',
        'live_evidence_ready', 'evidence_source',
        'latest_live_telemetry_at', 'missing', 'contradiction_flags',
    ]
    for key in required:
        assert key in result, f'Missing required output key: {key}'

    chain = result['chain']
    for key in ('telemetry_event_id', 'detection_id', 'alert_id', 'incident_id', 'evidence_package_id'):
        assert key in chain, f'Missing required chain key: {key}'


# ---------------------------------------------------------------------------
# Script-level: generate_live_evidence_proof new flags
# ---------------------------------------------------------------------------

def test_script_successful_rpc_alone_sets_only_provider_flags_true(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Successful RPC alone => live_provider_ready=True, downstream flags False.

    RPC health alone (eth_chainId + eth_blockNumber) must not fake a full chain.
    Without a real live telemetry event, only the provider/receipt flags flip true.
    """
    _clear_provider_env(monkeypatch)
    monkeypatch.setenv('EVM_RPC_URL', _REAL_RPC)
    monkeypatch.setenv('EVM_CHAIN_ID', '1')
    monkeypatch.setenv('STAGING_WORKER_ENABLED', 'true')

    with patch(_SCRIPT_RPC_PATCH, side_effect=_mock_rpc_success('0x1', '0x12c4cca')):
        result = generate_live_evidence_proof()

    lpe = result['live_provider_evidence']
    assert lpe['live_provider_ready'] is True
    assert lpe['live_provider_receipt_ready'] is True
    # No real live telemetry event was supplied -> downstream flags must be False.
    assert lpe['live_telemetry_ready'] is False
    assert lpe['live_detection_ready'] is False
    assert lpe['live_alert_ready'] is False
    assert lpe['live_incident_ready'] is False
    assert lpe['live_evidence_ready'] is False
    # Chain IDs are not synthesised from eth_chainId or eth_blockNumber alone.
    chain = lpe['chain']
    for fld in ('telemetry_event_id', 'detection_id', 'alert_id',
                'incident_id', 'evidence_package_id'):
        assert chain[fld] is None
    # The reason is explicit and operator-facing.
    assert any(
        'no matching live telemetry event' in m
        for m in lpe['missing']
    ), f"Expected no-live-event reason; got: {lpe['missing']}"


def test_script_real_evidence_satisfies_full_chain(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Real live evidence injected via param => all flags true, chain populated."""
    _clear_provider_env(monkeypatch)
    monkeypatch.setenv('EVM_RPC_URL', _REAL_RPC)
    monkeypatch.setenv('EVM_CHAIN_ID', '1')
    monkeypatch.setenv('STAGING_WORKER_ENABLED', 'true')

    with patch(_SCRIPT_RPC_PATCH, side_effect=_mock_rpc_success('0x1', '0x12c4cca')):
        result = generate_live_evidence_proof(live_evidence_chain=_real_live_chain())

    lpe = result['live_provider_evidence']
    assert lpe['live_provider_ready'] is True
    assert lpe['live_provider_receipt_ready'] is True
    assert lpe['live_telemetry_ready'] is True
    assert lpe['live_detection_ready'] is True
    assert lpe['live_alert_ready'] is True
    assert lpe['live_incident_ready'] is True
    assert lpe['live_evidence_ready'] is True
    assert lpe['chain']['telemetry_event_id'] == 'tel-live-001'


def test_script_telemetry_record_has_rpc_polling_source_type(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When real live evidence is supplied, telemetry_record carries rpc_polling source_type."""
    _clear_provider_env(monkeypatch)
    monkeypatch.setenv('EVM_RPC_URL', _REAL_RPC)
    monkeypatch.setenv('EVM_CHAIN_ID', '1')
    monkeypatch.setenv('STAGING_WORKER_ENABLED', 'true')

    with patch(_SCRIPT_RPC_PATCH, side_effect=_mock_rpc_success('0x1', '0x12c4cca')):
        result = generate_live_evidence_proof(live_evidence_chain=_real_live_chain())

    lpe = result['live_provider_evidence']
    tel = lpe.get('telemetry_record', {})
    assert tel.get('source_type') == 'rpc_polling'
    assert tel.get('provider_mode') == 'live'
    assert tel.get('evidence_source') == 'live'
    assert tel.get('raw_rpc_response_hash') is not None


def test_script_evidence_package_has_evidence_source_live(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Evidence package record must label evidence_source='live' and carry all chain IDs."""
    _clear_provider_env(monkeypatch)
    monkeypatch.setenv('EVM_RPC_URL', _REAL_RPC)
    monkeypatch.setenv('EVM_CHAIN_ID', '1')
    monkeypatch.setenv('STAGING_WORKER_ENABLED', 'true')

    with patch(_SCRIPT_RPC_PATCH, side_effect=_mock_rpc_success('0x1', '0x12c4cca')):
        result = generate_live_evidence_proof(live_evidence_chain=_real_live_chain())

    lpe = result['live_provider_evidence']
    pkg = lpe.get('evidence_package_record', {})
    chain = lpe['chain']

    assert pkg['evidence_source'] == 'live'
    assert pkg['provider_mode'] == 'live'
    assert pkg['source_type'] == 'rpc_polling'
    assert pkg['telemetry_event_id'] == chain['telemetry_event_id']
    assert pkg['detection_id'] == chain['detection_id']
    assert pkg['alert_id'] == chain['alert_id']
    assert pkg['incident_id'] == chain['incident_id']
    assert pkg.get('raw_rpc_response_hash') is not None


def test_script_detection_name_is_live_rpc_event_observed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Detection record must use name 'live_rpc_event_observed' and source_type='rpc_polling'."""
    _clear_provider_env(monkeypatch)
    monkeypatch.setenv('EVM_RPC_URL', _REAL_RPC)
    monkeypatch.setenv('EVM_CHAIN_ID', '1')
    monkeypatch.setenv('STAGING_WORKER_ENABLED', 'true')

    with patch(_SCRIPT_RPC_PATCH, side_effect=_mock_rpc_success('0x1', '0x12c4cca')):
        result = generate_live_evidence_proof(live_evidence_chain=_real_live_chain())

    det = result['live_provider_evidence'].get('detection_record', {})
    assert det.get('detection_name') == 'live_rpc_event_observed'
    assert det.get('source_type') == 'rpc_polling'
    assert det.get('evidence_source') == 'live'


def test_script_fail_result_has_new_flags(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fail-closed results must include the new split proof state flags."""
    _clear_provider_env(monkeypatch)
    # No EVM_RPC_URL → early fail
    result = generate_live_evidence_proof()

    lpe = result['live_provider_evidence']
    for key in (
        'live_provider_ready', 'live_provider_receipt_ready',
        'live_telemetry_ready', 'live_detection_ready',
        'live_alert_ready', 'live_incident_ready',
    ):
        assert key in lpe, f'Missing key in fail result: {key}'
        assert lpe[key] is False


def test_script_rpc_unreachable_sets_live_provider_ready_false(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """RPC unreachable => live_provider_ready=False, live_provider_receipt_ready=False."""
    _clear_provider_env(monkeypatch)
    monkeypatch.setenv('EVM_RPC_URL', _REAL_RPC)
    monkeypatch.setenv('EVM_CHAIN_ID', '1')
    monkeypatch.setenv('STAGING_WORKER_ENABLED', 'true')

    def _error_side(url, method, params=None, timeout=10):
        return {'error': 'URLError: Connection refused'}

    with patch(_SCRIPT_RPC_PATCH, side_effect=_error_side):
        result = generate_live_evidence_proof()

    lpe = result['live_provider_evidence']
    assert lpe['live_provider_ready'] is False
    assert lpe['live_provider_receipt_ready'] is False
    assert lpe['live_telemetry_ready'] is False
    assert lpe['live_evidence_ready'] is False


def test_script_chain_id_not_configured_live_provider_ready_true(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """RPC healthy but no chain ID => live_provider_ready=True (RPC reached), live_evidence_ready=False."""
    _clear_provider_env(monkeypatch)
    monkeypatch.setenv('EVM_RPC_URL', _REAL_RPC)
    monkeypatch.setenv('STAGING_WORKER_ENABLED', 'true')
    # chain ID intentionally absent

    with patch(_SCRIPT_RPC_PATCH, side_effect=_mock_rpc_success('0x1', '0x12c4cca')):
        result = generate_live_evidence_proof()

    lpe = result['live_provider_evidence']
    # RPC health check passed and we got block data → live_provider_ready
    assert lpe['live_provider_ready'] is True
    assert lpe['live_provider_receipt_ready'] is True
    # But chain ID missing blocks full proof
    assert lpe['live_evidence_ready'] is False
    assert lpe['live_telemetry_ready'] is False


def test_script_staging_env_vars_create_live_provider_ready(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """STAGING_EVM_RPC_URL + STAGING_EVM_CHAIN_ID => live_provider_ready=True.

    live_evidence_ready=True requires a real live event chain on top of RPC health.
    """
    _clear_provider_env(monkeypatch)
    monkeypatch.setenv('STAGING_EVM_RPC_URL', _REAL_RPC)
    monkeypatch.setenv('STAGING_EVM_CHAIN_ID', '1')
    monkeypatch.setenv('STAGING_WORKER_ENABLED', 'true')

    with patch(_SCRIPT_RPC_PATCH, side_effect=_mock_rpc_success('0x1', '0x12c4cca')):
        result = generate_live_evidence_proof(live_evidence_chain=_real_live_chain())

    lpe = result['live_provider_evidence']
    assert lpe['provider_ready'] is True
    assert lpe['live_provider_ready'] is True
    assert lpe['live_evidence_ready'] is True

    # The "not configured" message must not appear when STAGING_EVM_RPC_URL is set.
    joined = ' '.join(lpe.get('missing') or [])
    assert 'not configured' not in joined


# ---------------------------------------------------------------------------
# Poll-only without telemetry does not pass blocker 3
# ---------------------------------------------------------------------------

def test_poll_only_without_telemetry_does_not_pass_blocker_3(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A worker poll without rpc_polling telemetry must not satisfy criterion 3.

    Even if checked=1 and receipts_written=1, live_evidence_ready requires
    an actual live telemetry event (source_type=rpc_polling).  A coverage-only
    poll (no rpc_polling telemetry event) must leave live_evidence_ready=False.
    """
    _clear_provider_env(monkeypatch)
    monkeypatch.setenv('EVM_RPC_URL', 'https://mainnet.infura.io/v3/proj_abc')

    result = build_live_evidence_proof(chain_evidence={
        'evidence_source': 'live',
        'monitoring_checked_count': 1,
        'receipts_written': 1,
        # coverage timestamp exists but no rpc_polling source_type and no telemetry_event_id
        'last_telemetry_at': '2026-01-01T00:01:00Z',
    })

    assert result['live_evidence_ready'] is False, (
        'Poll-only coverage (no rpc_polling telemetry event) must not pass blocker 3; '
        f'got live_evidence_ready={result["live_evidence_ready"]}'
    )
    assert result['live_provider_ready'] is True
    assert result['live_telemetry_ready'] is False, (
        'live_telemetry_ready must be False when source_type=rpc_polling event is absent'
    )


def test_worker_poll_with_live_rpc_telemetry_increments_live_targets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A live rpc_polling telemetry event satisfies live_telemetry_ready=True.

    When the worker persists a telemetry_events row with source_type=rpc_polling
    and evidence_source=live, the proof chain's live_telemetry_ready flag must
    flip to True.  This is distinct from just having checked>0.
    """
    _clear_provider_env(monkeypatch)
    monkeypatch.setenv('EVM_RPC_URL', 'https://mainnet.infura.io/v3/proj_abc')

    result = build_live_evidence_proof(chain_evidence={
        'evidence_source': 'live',
        'monitoring_checked_count': 1,
        'receipts_written': 1,
        'last_telemetry_at': '2026-01-01T00:01:00Z',
        'source_type': 'rpc_polling',
        'telemetry_event_id': 'tel-rpc-001',
    })

    assert result['live_provider_ready'] is True
    assert result['live_telemetry_ready'] is True, (
        'A rpc_polling telemetry event must set live_telemetry_ready=True'
    )
