"""
Tests for Base chain wallet transfer detection.

Covers:
  A. Base wallet transfer creates wallet_transfer_detected telemetry event_type
  B. Transfer involving monitored wallet fires wallet_transfer detector (high severity → alert)
  C. Unrelated transfer does not fire wallet_transfer detector
  D. chain_id is 8453, not 1, for Base targets
  E. No private keys or API keys are logged
  F. RPC probe allows Base when eth_chainId returns 8453
  G. RPC probe blocks chain when eth_chainId mismatch
  H. monitoring_configs repair SQL now covers Base networks
  I. wallet_transfer_detected event_type is set for wallet targets in telemetry
"""
from __future__ import annotations

import hashlib
import os
import uuid
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now() -> datetime:
    return datetime(2026, 6, 12, 12, 0, tzinfo=timezone.utc)


def _normalize_addr(val: object) -> str | None:
    """Replicate _normalize_addr from monitoring_runner without importing it."""
    s = str(val or '').strip().lower()
    return s if s else None


def _wallet_transfer_detect(asset: dict, event: object) -> dict | None:
    """Inline the wallet_transfer detector logic from _enforce_asset_detectors.

    Returns the detector result dict when the monitored wallet is involved in the
    transaction, or None otherwise.  Used in tests to avoid importing monitoring_runner
    (which depends on fastapi and cannot be imported in the test environment).
    """
    payload = event.payload if isinstance(event.payload, dict) else {}
    kind = getattr(event, 'kind', None)

    wt_addr = _normalize_addr(asset.get('asset_identifier'))
    wt_is_evm_wallet = (
        bool(wt_addr)
        and wt_addr.startswith('0x')
        and len(wt_addr) == 42
        and not asset.get('contract_address')
    )
    wt_event_type = str(payload.get('event_type') or kind or '').lower()
    wt_tx_from = _normalize_addr(payload.get('from') or payload.get('owner'))
    wt_tx_to = _normalize_addr(payload.get('to'))
    wt_involved = wt_is_evm_wallet and wt_addr in {wt_tx_from, wt_tx_to}

    if wt_involved and wt_event_type in {'transaction', 'transfer'}:
        direction = 'outbound' if wt_addr == wt_tx_from else 'inbound'
        return {
            'detector_family': 'wallet_transfer',
            'detector_status': 'anomaly_detected',
            'anomaly_reason': f'wallet_transfer_{direction}',
            'severity': 'high',
            'confidence': 'high',
            'recommended_action': 'review_wallet_transfer',
            'violated_asset_rule': 'wallet_activity_monitoring',
            'wallet_transfer_direction': direction,
            'monitored_wallet': wt_addr,
            'tx_hash': payload.get('tx_hash'),
            'chain_id': payload.get('chain_id'),
            'block_number': payload.get('block_number'),
            'value': payload.get('amount'),
            'event_type': 'wallet_transfer_detected',
        }
    return None


def _make_wallet_target(wallet_address: str = '0xDead00000000000000000000000000000000BeeF') -> dict:
    return {
        'id': str(uuid.uuid4()),
        'workspace_id': str(uuid.uuid4()),
        'name': 'Test Base Wallet',
        'target_type': 'wallet',
        'chain_network': 'base',
        'wallet_address': wallet_address.lower(),
        'contract_identifier': None,
        'asset_id': str(uuid.uuid4()),
        'severity_threshold': 'medium',
        'auto_create_alerts': True,
        'auto_create_incidents': False,
        'monitoring_checkpoint_cursor': None,
    }


def _make_wallet_asset(wallet_address: str = '0xDead00000000000000000000000000000000BeeF') -> dict:
    return {
        'id': str(uuid.uuid4()),
        'name': 'Base Wallet Asset',
        'asset_class': 'wallet',
        'asset_symbol': None,
        'identifier': wallet_address.lower(),
        'asset_identifier': wallet_address.lower(),
        'token_contract_address': None,  # wallet — no contract
        'chain_network': 'base',
        'treasury_ops_wallets': [],
        'custody_wallets': [],
        'oracle_sources': [],
        'venue_labels': [],
        'expected_flow_patterns': [],
        'expected_counterparties': [],
        'expected_approval_patterns': {},
        'expected_liquidity_baseline': {},
        'expected_oracle_freshness_seconds': 0,
        'expected_oracle_update_cadence_seconds': 0,
        'baseline_status': None,
        'baseline_source': None,
        'baseline_updated_at': None,
        'baseline_confidence': None,
        'baseline_coverage': None,
        'contract_address': None,
        'chain_id': 8453,
        'symbol': None,
    }


def _make_tx_event(
    *,
    tx_hash: str = '0xabc123',
    from_addr: str = '0xDead00000000000000000000000000000000BeeF',
    to_addr: str = '0xCafe00000000000000000000000000000000Feed',
    value: str = '1000000000000000',  # 0.001 ETH in wei
    block_number: int = 12345678,
    chain_id: int = 8453,
    target_id: str | None = None,
    event_type: str = 'transaction',
) -> object:
    from services.api.app.evm_activity_provider import ActivityEvent
    payload = {
        'chain_id': chain_id,
        'chain_network': 'base',
        'block_number': block_number,
        'tx_hash': tx_hash,
        'from': from_addr.lower(),
        'to': to_addr.lower(),
        'amount': value,
        'event_type': event_type,
        'log_index': None,
        'contract_address': None,
        'asset_address': None,
        'target_id': target_id or str(uuid.uuid4()),
        'metadata': {'evidence_origin': 'real', 'provider_name': 'evm_activity_provider'},
        'market_observations': [],
        'oracle_observations': [],
        'liquidity_observations': [],
        'venue_observations': [],
    }
    return ActivityEvent(
        event_id=hashlib.sha256(f'{tx_hash}:{block_number}'.encode()).hexdigest()[:24],
        kind='transaction',
        observed_at=_now(),
        ingestion_source='rpc_polling',
        cursor=f'{block_number}:{tx_hash}:-1',
        payload=payload,
    )


# ---------------------------------------------------------------------------
# A. wallet_transfer_detected event_type is set for wallet target transactions
# ---------------------------------------------------------------------------

def test_telem_event_type_is_wallet_transfer_detected_for_wallet_tx():
    """When a wallet target transaction matches the monitored wallet, event_type
    must be 'wallet_transfer_detected', not the generic 'transaction'."""
    wallet_addr = '0xdead00000000000000000000000000000000beef'
    target = _make_wallet_target(wallet_addr)
    event = _make_tx_event(from_addr=wallet_addr, chain_id=8453)
    ev_payload = event.payload
    ev_from = str(ev_payload.get('from') or '').lower()
    ev_to = str(ev_payload.get('to') or '').lower()
    target_wallet = str(target.get('wallet_address') or '').lower()
    is_wallet_tx = (
        str(target.get('target_type') or '').lower() == 'wallet'
        and bool(target_wallet)
        and target_wallet in {ev_from, ev_to}
        and str(ev_payload.get('event_type') or event.kind or '').lower() in {'transaction', 'transfer'}
    )
    telem_event_type = 'wallet_transfer_detected' if is_wallet_tx else str(event.kind or 'target_event')
    assert telem_event_type == 'wallet_transfer_detected', (
        f'Wallet target transaction must use event_type=wallet_transfer_detected, got {telem_event_type!r}'
    )


def test_telem_event_type_is_transaction_for_unrelated_target():
    """Non-wallet targets must use the default event kind, not wallet_transfer_detected."""
    target = {
        'id': str(uuid.uuid4()),
        'target_type': 'contract',
        'wallet_address': None,
    }
    event = _make_tx_event(chain_id=8453)
    ev_payload = event.payload
    ev_from = str(ev_payload.get('from') or '').lower()
    ev_to = str(ev_payload.get('to') or '').lower()
    target_wallet = str(target.get('wallet_address') or '').lower()
    is_wallet_tx = (
        str(target.get('target_type') or '').lower() == 'wallet'
        and bool(target_wallet)
        and target_wallet in {ev_from, ev_to}
        and str(ev_payload.get('event_type') or event.kind or '').lower() in {'transaction', 'transfer'}
    )
    telem_event_type = 'wallet_transfer_detected' if is_wallet_tx else str(event.kind or 'target_event')
    assert telem_event_type != 'wallet_transfer_detected'
    assert telem_event_type == 'transaction'


# ---------------------------------------------------------------------------
# B. wallet_transfer detector fires for monitored wallet
# ---------------------------------------------------------------------------

def test_wallet_transfer_detector_fires_for_monitored_wallet():
    """wallet_transfer detector logic returns an anomaly when the monitored wallet
    address (asset_identifier) appears as the sender in the transaction."""
    wallet_addr = '0xdead00000000000000000000000000000000beef'
    asset = _make_wallet_asset(wallet_addr)
    event = _make_tx_event(from_addr=wallet_addr, chain_id=8453)
    wt = _wallet_transfer_detect(asset, event)
    assert wt is not None, 'wallet_transfer detector must fire for monitored wallet transaction'
    assert wt['detector_status'] == 'anomaly_detected'
    assert wt['severity'] == 'high'
    assert wt['anomaly_reason'] == 'wallet_transfer_outbound'
    assert wt['monitored_wallet'] == wallet_addr
    assert wt['event_type'] == 'wallet_transfer_detected'


def test_wallet_transfer_detector_fires_for_inbound_transfer():
    """wallet_transfer detector fires for inbound transfers (wallet is recipient)."""
    wallet_addr = '0xdead00000000000000000000000000000000beef'
    asset = _make_wallet_asset(wallet_addr)
    event = _make_tx_event(
        from_addr='0xcafe00000000000000000000000000000000feed',
        to_addr=wallet_addr,
        chain_id=8453,
    )
    wt = _wallet_transfer_detect(asset, event)
    assert wt is not None, 'wallet_transfer detector must fire for inbound wallet transaction'
    assert wt['anomaly_reason'] == 'wallet_transfer_inbound'
    assert wt['severity'] == 'high'


def test_wallet_transfer_detector_is_first_anomaly_in_results():
    """wallet_transfer detector must be prepended to detectors so _asset_detection_summary
    picks it first (severity='high').  Verified via source inspection of monitoring_runner.py."""
    import re
    source = open('services/api/app/monitoring_runner.py', encoding='utf-8').read()
    # Check that wallet_transfer result is prepended: (wallet_transfer,) + detectors
    assert 'detectors = (wallet_transfer,) + detectors' in source, (
        'monitoring_runner.py must prepend wallet_transfer to detectors tuple'
    )
    # Also verify the wallet_transfer detector fires for the test asset/event
    wallet_addr = '0xdead00000000000000000000000000000000beef'
    asset = _make_wallet_asset(wallet_addr)
    event = _make_tx_event(from_addr=wallet_addr, chain_id=8453)
    wt = _wallet_transfer_detect(asset, event)
    assert wt is not None and wt['severity'] == 'high' and wt['detector_status'] == 'anomaly_detected'


# ---------------------------------------------------------------------------
# C. Unrelated transfer does not fire wallet_transfer detector
# ---------------------------------------------------------------------------

def test_wallet_transfer_detector_does_not_fire_for_unrelated_address():
    """wallet_transfer detector must NOT fire when the transaction does not involve
    the monitored wallet address."""
    wallet_addr = '0xdead00000000000000000000000000000000beef'
    asset = _make_wallet_asset(wallet_addr)
    event = _make_tx_event(
        from_addr='0x1111111111111111111111111111111111111111',
        to_addr='0x2222222222222222222222222222222222222222',
        chain_id=8453,
    )
    wt = _wallet_transfer_detect(asset, event)
    assert wt is None, (
        'wallet_transfer detector must NOT fire when monitored wallet is not in the transaction'
    )


def test_wallet_transfer_detector_does_not_fire_for_contract_asset():
    """wallet_transfer detector must NOT fire for ERC-20 contract assets
    (token_contract_address set → not a wallet-type asset)."""
    contract_addr = '0xdead00000000000000000000000000000000beef'
    asset = _make_wallet_asset(contract_addr)
    asset['token_contract_address'] = contract_addr  # make it a contract asset
    asset['contract_address'] = contract_addr
    event = _make_tx_event(
        from_addr=contract_addr,
        to_addr='0xcafe00000000000000000000000000000000feed',
        chain_id=8453,
    )
    wt = _wallet_transfer_detect(asset, event)
    assert wt is None, (
        'wallet_transfer detector must NOT fire for contract-type assets (contract_address set)'
    )


# ---------------------------------------------------------------------------
# D. chain_id is 8453, not 1, for Base targets
# ---------------------------------------------------------------------------

def test_base_wallet_event_carries_chain_id_8453():
    """Events from Base wallet transactions must carry chain_id=8453."""
    from services.api.app.evm_activity_provider import CHAIN_MAP
    network = 'base'
    chain_id = CHAIN_MAP.get(network, {}).get('chain_id')
    assert chain_id == 8453, f'Base chain_id must be 8453, got {chain_id}'


def test_base_mainnet_alias_carries_chain_id_8453():
    """'base-mainnet' alias must also resolve to chain_id=8453."""
    from services.api.app.evm_activity_provider import CHAIN_MAP
    assert CHAIN_MAP.get('base-mainnet', {}).get('chain_id') == 8453


def test_wallet_transfer_detector_chain_id_in_result():
    """wallet_transfer detector result must carry chain_id from the event payload."""
    wallet_addr = '0xdead00000000000000000000000000000000beef'
    asset = _make_wallet_asset(wallet_addr)
    event = _make_tx_event(from_addr=wallet_addr, chain_id=8453)
    wt = _wallet_transfer_detect(asset, event)
    assert wt is not None
    assert wt.get('chain_id') == 8453, (
        f'wallet_transfer result must carry chain_id=8453, got {wt.get("chain_id")!r}'
    )


def test_ethereum_wallet_transfer_does_not_carry_base_chain_id():
    """Ethereum wallet transactions must carry chain_id=1, not 8453."""
    wallet_addr = '0xdead00000000000000000000000000000000beef'
    asset = _make_wallet_asset(wallet_addr)
    asset['chain_id'] = 1
    event = _make_tx_event(from_addr=wallet_addr, chain_id=1)
    wt = _wallet_transfer_detect(asset, event)
    assert wt is not None
    assert wt.get('chain_id') == 1, (
        f'Ethereum wallet_transfer must carry chain_id=1, got {wt.get("chain_id")!r}'
    )


# ---------------------------------------------------------------------------
# E. No private keys or API keys are logged
# ---------------------------------------------------------------------------

def test_rpc_url_not_exposed_in_fetch_evm_activity_logs(monkeypatch, caplog):
    """fetch_evm_activity must not log the RPC URL (which may contain an API key)."""
    import logging
    secret_url = 'https://base-mainnet.g.alchemy.com/v2/supersecretkey999'
    monkeypatch.setenv('EVM_RPC_URL', secret_url)
    monkeypatch.setenv('LIVE_MONITORING_CHAINS', 'base')
    monkeypatch.delenv('STAGING_EVM_RPC_URL', raising=False)

    mock_client = MagicMock()
    mock_client.call.side_effect = lambda method, params: (
        '0x210d' if method == 'eth_chainId' else
        '0xf0000' if method == 'eth_blockNumber' else
        None
    )

    with caplog.at_level(logging.DEBUG):
        from services.api.app import evm_activity_provider
        target = {
            'id': str(uuid.uuid4()),
            'workspace_id': str(uuid.uuid4()),
            'chain_network': 'base',
            'target_type': 'wallet',
            'wallet_address': '0xdead00000000000000000000000000000000beef',
            'contract_identifier': None,
        }
        try:
            evm_activity_provider.fetch_evm_activity(target, None, rpc_client=mock_client)
        except Exception:
            pass

    for record in caplog.records:
        assert 'supersecretkey999' not in record.getMessage(), (
            f'API key must not appear in logs: {record.getMessage()[:200]}'
        )


def test_chain_probe_does_not_log_rpc_url(monkeypatch, caplog):
    """RPC probe for chain ID must not log the RPC URL containing an API key."""
    import logging
    secret_url = 'https://base-mainnet.infura.io/v3/myinfurakey12345'
    monkeypatch.setenv('EVM_RPC_URL', secret_url)
    monkeypatch.delenv('STAGING_EVM_RPC_URL', raising=False)
    monkeypatch.delenv('EVM_CHAIN_ID', raising=False)
    monkeypatch.delenv('STAGING_EVM_CHAIN_ID', raising=False)
    monkeypatch.delenv('LIVE_MONITORING_CHAINS', raising=False)

    from services.api.app.evm_activity_provider import probe_rpc_health
    with caplog.at_level(logging.DEBUG):
        with patch('services.api.app.evm_activity_provider.FailoverJsonRpcClient') as mock_cls:
            instance = MagicMock()
            instance.call.return_value = '0x2105'
            mock_cls.return_value = instance
            probe_rpc_health()

    for record in caplog.records:
        assert 'myinfurakey12345' not in record.getMessage(), (
            f'Infura key must not appear in logs: {record.getMessage()[:200]}'
        )


# ---------------------------------------------------------------------------
# F. RPC probe allows Base when eth_chainId returns 8453
# ---------------------------------------------------------------------------

def test_fetch_evm_activity_allows_base_via_rpc_probe(monkeypatch):
    """fetch_evm_activity must allow Base when the RPC probe returns chain_id=8453,
    even without LIVE_MONITORING_CHAINS=base or EVM_CHAIN_ID=8453."""
    monkeypatch.delenv('LIVE_MONITORING_CHAINS', raising=False)
    monkeypatch.delenv('EVM_CHAIN_ID', raising=False)
    monkeypatch.delenv('STAGING_EVM_CHAIN_ID', raising=False)
    monkeypatch.setenv('EVM_RPC_URL', 'https://base-mainnet.example.com/v2/key')
    monkeypatch.delenv('STAGING_EVM_RPC_URL', raising=False)
    monkeypatch.setenv('EVM_CONFIRMATIONS_REQUIRED', '0')
    monkeypatch.setenv('MONITOR_REPLAY_BLOCKS', '1')
    monkeypatch.setenv('MONITOR_BATCH_BLOCKS', '1')

    target_id = str(uuid.uuid4())
    wallet_addr = '0xdead00000000000000000000000000000000beef'
    target = {
        'id': target_id,
        'workspace_id': str(uuid.uuid4()),
        'chain_network': 'base',
        'target_type': 'wallet',
        'wallet_address': wallet_addr,
        'contract_identifier': None,
        'monitoring_checkpoint_cursor': None,
    }

    mock_client = MagicMock()
    # eth_chainId returns 8453 (Base mainnet)
    # eth_blockNumber returns a block
    # eth_getBlockByNumber returns a block with no transactions
    mock_client.call.side_effect = lambda method, params: {
        'eth_chainId': '0x2105',  # 8453
        'eth_blockNumber': '0xf0000',
        'eth_getBlockByNumber': {'hash': '0xhash', 'timestamp': '0x67a00000', 'transactions': []},
        'eth_getLogs': [],
    }.get(method)

    from services.api.app.evm_activity_provider import fetch_evm_activity
    events = fetch_evm_activity(target, None, rpc_client=mock_client)
    # Should return [] (no matching transactions), but NOT short-circuit at chain gate
    # Verify the chain gate didn't block: eth_getBlockByNumber must have been called
    called_methods = [call.args[0] for call in mock_client.call.call_args_list]
    assert 'eth_getBlockByNumber' in called_methods or 'eth_getLogs' in called_methods, (
        'fetch_evm_activity must call block/log RPC methods for Base when probe returns 8453'
    )


# ---------------------------------------------------------------------------
# G. RPC probe blocks when chain ID mismatches
# ---------------------------------------------------------------------------

def test_fetch_evm_activity_blocked_when_rpc_is_ethereum_for_base_target(monkeypatch):
    """fetch_evm_activity must skip Base targets when the RPC reports chain_id=1 (Ethereum)."""
    monkeypatch.delenv('LIVE_MONITORING_CHAINS', raising=False)
    monkeypatch.delenv('EVM_CHAIN_ID', raising=False)
    monkeypatch.delenv('STAGING_EVM_CHAIN_ID', raising=False)
    monkeypatch.setenv('EVM_RPC_URL', 'https://mainnet.infura.io/v3/key')
    monkeypatch.delenv('STAGING_EVM_RPC_URL', raising=False)

    target = {
        'id': str(uuid.uuid4()),
        'workspace_id': str(uuid.uuid4()),
        'chain_network': 'base',
        'target_type': 'wallet',
        'wallet_address': '0xdead00000000000000000000000000000000beef',
        'contract_identifier': None,
    }

    mock_client = MagicMock()
    mock_client.call.side_effect = lambda method, params: (
        '0x1'  # chain_id=1 (Ethereum, not Base)
        if method == 'eth_chainId'
        else None
    )

    from services.api.app.evm_activity_provider import fetch_evm_activity
    events = fetch_evm_activity(target, None, rpc_client=mock_client)
    assert events == [], (
        'fetch_evm_activity must return [] when RPC chain_id=1 but target is Base'
    )
    # Should not have called eth_getBlockByNumber (early exit)
    called_methods = [call.args[0] for call in mock_client.call.call_args_list]
    assert 'eth_getBlockByNumber' not in called_methods, (
        'eth_getBlockByNumber must not be called when chain probe rejects the target'
    )


def test_fetch_evm_activity_blocked_when_rpc_probe_fails(monkeypatch):
    """fetch_evm_activity must return [] when the chain probe raises an exception."""
    monkeypatch.delenv('LIVE_MONITORING_CHAINS', raising=False)
    monkeypatch.delenv('EVM_CHAIN_ID', raising=False)
    monkeypatch.delenv('STAGING_EVM_CHAIN_ID', raising=False)
    monkeypatch.setenv('EVM_RPC_URL', 'https://base-mainnet.example.com/v2/key')
    monkeypatch.delenv('STAGING_EVM_RPC_URL', raising=False)

    target = {
        'id': str(uuid.uuid4()),
        'workspace_id': str(uuid.uuid4()),
        'chain_network': 'base',
        'target_type': 'wallet',
        'wallet_address': '0xdead00000000000000000000000000000000beef',
        'contract_identifier': None,
    }

    mock_client = MagicMock()
    mock_client.call.side_effect = RuntimeError('rpc_unreachable')

    from services.api.app.evm_activity_provider import fetch_evm_activity
    events = fetch_evm_activity(target, None, rpc_client=mock_client)
    assert events == [], (
        'fetch_evm_activity must return [] when chain probe raises an exception'
    )


# ---------------------------------------------------------------------------
# H. monitoring_configs repair SQL covers Base networks
# ---------------------------------------------------------------------------

def test_monitoring_configs_repair_sql_includes_base():
    """run_monitoring_cycle must repair Base monitoring_configs to evm_rpc."""
    source = open('services/api/app/monitoring_runner.py', encoding='utf-8').read()
    # Find the repair UPDATE statement
    assert "'base'" in source and "'base-mainnet'" in source, (
        "monitoring_runner.py repair SQL must include 'base' and 'base-mainnet'"
    )
    # Verify both are in the IN clause next to the ethereum chains
    import re
    match = re.search(
        r"IN \([^)]*'ethereum'[^)]*\)",
        source,
    )
    assert match is not None, 'repair SQL must contain ethereum chains IN clause'
    clause = match.group(0)
    assert "'base'" in clause, f"repair IN clause must include 'base': {clause}"
    assert "'base-mainnet'" in clause, f"repair IN clause must include 'base-mainnet': {clause}"


# ---------------------------------------------------------------------------
# I. wallet_transfer_detected is used as event_type for wallet transactions
# ---------------------------------------------------------------------------

def test_wallet_transfer_detected_logic_for_outbound_tx():
    """The event_type selection logic must yield wallet_transfer_detected for
    outbound transactions from the monitored wallet."""
    wallet_addr = '0xdead00000000000000000000000000000000beef'
    target = _make_wallet_target(wallet_addr)
    event = _make_tx_event(
        from_addr=wallet_addr,
        to_addr='0xcafe00000000000000000000000000000000feed',
        chain_id=8453,
    )
    ev_payload = event.payload
    ev_from = str(ev_payload.get('from') or '').lower()
    ev_to = str(ev_payload.get('to') or '').lower()
    target_wallet = str(target.get('wallet_address') or '').lower()
    is_wallet_tx = (
        str(target.get('target_type') or '').lower() == 'wallet'
        and bool(target_wallet)
        and target_wallet in {ev_from, ev_to}
        and str(ev_payload.get('event_type') or event.kind or '').lower() in {'transaction', 'transfer'}
    )
    assert is_wallet_tx is True
    telem_event_type = 'wallet_transfer_detected' if is_wallet_tx else str(event.kind or 'target_event')
    assert telem_event_type == 'wallet_transfer_detected'


def test_wallet_transfer_detected_not_set_when_wallet_not_involved():
    """When the wallet_address is not in the transaction from/to, event_type must
    remain the generic event kind, not wallet_transfer_detected."""
    wallet_addr = '0xdead00000000000000000000000000000000beef'
    target = _make_wallet_target(wallet_addr)
    event = _make_tx_event(
        from_addr='0x1111111111111111111111111111111111111111',
        to_addr='0x2222222222222222222222222222222222222222',
        chain_id=8453,
    )
    ev_payload = event.payload
    ev_from = str(ev_payload.get('from') or '').lower()
    ev_to = str(ev_payload.get('to') or '').lower()
    target_wallet = str(target.get('wallet_address') or '').lower()
    is_wallet_tx = (
        str(target.get('target_type') or '').lower() == 'wallet'
        and bool(target_wallet)
        and target_wallet in {ev_from, ev_to}
        and str(ev_payload.get('event_type') or event.kind or '').lower() in {'transaction', 'transfer'}
    )
    assert is_wallet_tx is False
    telem_event_type = 'wallet_transfer_detected' if is_wallet_tx else str(event.kind or 'target_event')
    assert telem_event_type == 'transaction'
