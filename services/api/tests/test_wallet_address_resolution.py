"""Tests for monitored wallet address resolution and Base transfer detection.

Covers the reported failure where a wallet target polled Base correctly (real
block numbers, fresh heartbeat) but never produced wallet_transfer_detected
telemetry because the monitored wallet address was not extracted:

  A. resolve_monitored_wallet reads the canonical column and known fallbacks.
  B. A wallet target with no resolvable address fails closed (raises), instead
     of silently returning coverage-only rows.
  C. fetch_evm_activity detects outbound + inbound Base transfers and emits a
     payload carrying the fields persisted as wallet_transfer_detected.
  D. The persisted payload is searchable by tx_hash / from / to / block_number.
  E. explain_wallet_transfer_match (debug command) reports matched/not matched.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest

from services.api.app.evm_activity_provider import (
    MonitoredWalletNotConfigured,
    explain_wallet_transfer_match,
    fetch_evm_activity,
    resolve_monitored_wallet,
)

WALLET = '0xdead00000000000000000000000000000000beef'
COUNTERPARTY = '0xcafe00000000000000000000000000000000feed'


# ---------------------------------------------------------------------------
# A. resolve_monitored_wallet
# ---------------------------------------------------------------------------

def test_resolve_wallet_from_canonical_column():
    target = {'target_type': 'wallet', 'wallet_address': WALLET.upper()}
    assert resolve_monitored_wallet(target) == WALLET


def test_resolve_wallet_from_contract_identifier_fallback():
    # Address typed into the wrong field still resolves for a wallet target.
    target = {'target_type': 'wallet', 'wallet_address': None, 'contract_identifier': WALLET}
    assert resolve_monitored_wallet(target) == WALLET


def test_resolve_wallet_from_asset_context_fallback():
    target = {
        'target_type': 'wallet',
        'wallet_address': None,
        'contract_identifier': None,
        'asset_context': {'asset_identifier': WALLET, 'identifier': WALLET},
    }
    assert resolve_monitored_wallet(target) == WALLET


def test_resolve_wallet_from_target_metadata_fallback():
    target = {
        'target_type': 'wallet',
        'wallet_address': None,
        'target_metadata': {'monitored_wallet': WALLET},
    }
    assert resolve_monitored_wallet(target) == WALLET


def test_resolve_wallet_returns_none_when_missing():
    target = {'target_type': 'wallet', 'wallet_address': None, 'contract_identifier': None}
    assert resolve_monitored_wallet(target) is None


def test_resolve_wallet_rejects_non_address_values():
    target = {'target_type': 'wallet', 'wallet_address': 'not-an-address', 'contract_identifier': '0x1234'}
    assert resolve_monitored_wallet(target) is None


# ---------------------------------------------------------------------------
# B. Fail-closed when the wallet is missing
# ---------------------------------------------------------------------------

def test_fetch_evm_activity_raises_when_wallet_target_misconfigured(monkeypatch):
    monkeypatch.setenv('EVM_RPC_URL', 'http://rpc')
    monkeypatch.setenv('LIVE_MONITORING_CHAINS', 'base')

    target = {
        'id': str(uuid.uuid4()),
        'workspace_id': str(uuid.uuid4()),
        'chain_network': 'base',
        'target_type': 'wallet',
        'wallet_address': None,
        'contract_identifier': None,
    }

    class _Rpc:
        def call(self, method, params):
            return None

    # Resolve the module at call time. Other tests in the suite reload modules
    # that transitively duplicate this one; binding the function and its
    # exception class from the same live module avoids a stale-identity mismatch.
    import services.api.app.evm_activity_provider as _m
    with pytest.raises(_m.MonitoredWalletNotConfigured):
        _m.fetch_evm_activity(target, None, rpc_client=_Rpc())


# ---------------------------------------------------------------------------
# C. Base transfer detection (outbound + inbound)
# ---------------------------------------------------------------------------

class _BaseRpc:
    """Mock Base RPC: one transaction in block 16 involving the monitored wallet."""

    def __init__(self, *, from_addr: str, to_addr: str, tx_hash: str, value_wei_hex: str) -> None:
        self.from_addr = from_addr
        self.to_addr = to_addr
        self.tx_hash = tx_hash
        self.value_wei_hex = value_wei_hex

    def call(self, method, params):
        if method == 'eth_chainId':
            return '0x2105'  # 8453
        if method == 'eth_blockNumber':
            return hex(16)
        if method == 'eth_getLogs':
            return []
        if method == 'eth_getBlockByNumber':
            block_number = int(str(params[0]), 16)
            txs = []
            if block_number == 16:
                txs = [{
                    'hash': self.tx_hash,
                    'from': self.from_addr,
                    'to': self.to_addr,
                    'value': self.value_wei_hex,
                    'input': '0x',
                    'blockNumber': hex(block_number),
                    'blockHash': f'0xblock{block_number}',
                }]
            return {
                'hash': f'0xblock{block_number}',
                'timestamp': hex(int(datetime(2026, 6, 13, tzinfo=timezone.utc).timestamp())),
                'transactions': txs,
            }
        return {}


@pytest.fixture(autouse=True)
def _no_external_telemetry(monkeypatch):
    # Keep tests offline: don't reach out to market/oracle providers.
    monkeypatch.setattr('services.api.app.evm_activity_provider._fetch_market_observations', lambda target: [])
    monkeypatch.setattr('services.api.app.evm_activity_provider._fetch_oracle_observations', lambda target: [])


def _base_env(monkeypatch):
    monkeypatch.setenv('EVM_RPC_URL', 'http://rpc')
    monkeypatch.setenv('LIVE_MONITORING_CHAINS', 'base')
    monkeypatch.setenv('EVM_CONFIRMATIONS_REQUIRED', '0')
    monkeypatch.setenv('MONITOR_REPLAY_BLOCKS', '1')
    monkeypatch.setenv('MONITOR_BATCH_BLOCKS', '1')
    monkeypatch.delenv('EVM_WS_URL', raising=False)


def _wallet_target():
    return {
        'id': str(uuid.uuid4()),
        'workspace_id': str(uuid.uuid4()),
        'chain_network': 'base',
        'target_type': 'wallet',
        'wallet_address': WALLET,
        'contract_identifier': None,
        'monitoring_checkpoint_cursor': None,
    }


def _wallet_event(events):
    for event in events:
        if event.payload.get('wallet_transfer_direction'):
            return event
    return None


def test_outbound_base_transfer_detected(monkeypatch):
    _base_env(monkeypatch)
    tx_hash = '0x' + 'a1' * 32
    rpc = _BaseRpc(from_addr=WALLET, to_addr=COUNTERPARTY, tx_hash=tx_hash, value_wei_hex=hex(1500000000000000))
    events = fetch_evm_activity(_wallet_target(), None, rpc_client=rpc)
    event = _wallet_event(events)
    assert event is not None, 'outbound transfer from monitored wallet must be detected'
    payload = event.payload
    assert payload['wallet_transfer_direction'] == 'outbound'
    assert payload['from'] == WALLET
    assert payload['to'] == COUNTERPARTY
    assert payload['tx_hash'] == tx_hash
    assert payload['chain_id'] == 8453
    assert payload['block_number'] == 16
    assert payload['value_wei'] == 1500000000000000
    assert payload['value_eth'] == pytest.approx(0.0015)


def test_inbound_base_transfer_detected(monkeypatch):
    _base_env(monkeypatch)
    tx_hash = '0x' + 'b2' * 32
    rpc = _BaseRpc(from_addr=COUNTERPARTY, to_addr=WALLET, tx_hash=tx_hash, value_wei_hex=hex(2000000000000000))
    events = fetch_evm_activity(_wallet_target(), None, rpc_client=rpc)
    event = _wallet_event(events)
    assert event is not None, 'inbound transfer to monitored wallet must be detected'
    assert event.payload['wallet_transfer_direction'] == 'inbound'
    assert event.payload['from'] == COUNTERPARTY
    assert event.payload['to'] == WALLET
    assert event.payload['tx_hash'] == tx_hash


def test_unrelated_transfer_not_detected(monkeypatch):
    _base_env(monkeypatch)
    tx_hash = '0x' + 'c3' * 32
    rpc = _BaseRpc(
        from_addr='0x1111111111111111111111111111111111111111',
        to_addr='0x2222222222222222222222222222222222222222',
        tx_hash=tx_hash,
        value_wei_hex=hex(1),
    )
    events = fetch_evm_activity(_wallet_target(), None, rpc_client=rpc)
    assert _wallet_event(events) is None, 'transfer not involving the monitored wallet must not be detected'


def test_fetch_normalizes_wallet_from_fallback_onto_target(monkeypatch):
    """When the wallet lives only in a fallback location, fetch resolves it and
    writes it back to target['wallet_address'] so downstream code is consistent."""
    _base_env(monkeypatch)
    tx_hash = '0x' + 'd4' * 32
    target = _wallet_target()
    target['wallet_address'] = None
    target['contract_identifier'] = WALLET  # address in the wrong field
    rpc = _BaseRpc(from_addr=WALLET, to_addr=COUNTERPARTY, tx_hash=tx_hash, value_wei_hex=hex(1))
    events = fetch_evm_activity(target, None, rpc_client=rpc)
    assert target['wallet_address'] == WALLET
    assert _wallet_event(events) is not None


# ---------------------------------------------------------------------------
# D. tx_hash search would match the persisted payload
# ---------------------------------------------------------------------------

def test_detected_payload_is_searchable_by_tx_hash(monkeypatch):
    """list_target_telemetry searches payload_json->>'tx_hash'/'from'/'to'/'block_number'.
    Verify the detected payload carries those exact fields so search returns the row."""
    _base_env(monkeypatch)
    tx_hash = '0x' + 'e5' * 32
    rpc = _BaseRpc(from_addr=WALLET, to_addr=COUNTERPARTY, tx_hash=tx_hash, value_wei_hex=hex(1))
    events = fetch_evm_activity(_wallet_target(), None, rpc_client=rpc)
    payload = _wallet_event(events).payload

    def _match(query: str) -> bool:
        like = query.lower()
        return (
            like in str(payload.get('tx_hash') or '').lower()
            or like in str(payload.get('from') or '').lower()
            or like in str(payload.get('to') or '').lower()
            or like in str(payload.get('block_number') or '')
        )

    assert _match(tx_hash), 'search by full tx_hash must match'
    assert _match(tx_hash[2:10]), 'search by tx_hash prefix must match'
    assert _match('16'), 'search by block number must match'
    assert _match(WALLET), 'search by monitored wallet address must match'


# ---------------------------------------------------------------------------
# E. explain_wallet_transfer_match (debug command)
# ---------------------------------------------------------------------------

def test_explain_match_outbound():
    tx = {'hash': '0xabc', 'from': WALLET, 'to': COUNTERPARTY, 'value': hex(1000000000000000)}
    result = explain_wallet_transfer_match(WALLET, tx)
    assert result['matched'] is True
    assert result['wallet_transfer_direction'] == 'outbound'
    assert result['reason'] == 'wallet_transfer_outbound'
    assert result['value_eth'] == pytest.approx(0.001)


def test_explain_match_inbound():
    tx = {'hash': '0xabc', 'from': COUNTERPARTY, 'to': WALLET, 'value': hex(0)}
    result = explain_wallet_transfer_match(WALLET, tx)
    assert result['matched'] is True
    assert result['wallet_transfer_direction'] == 'inbound'


def test_explain_not_matched():
    tx = {'hash': '0xabc', 'from': '0x1111111111111111111111111111111111111111', 'to': COUNTERPARTY}
    result = explain_wallet_transfer_match(WALLET, tx)
    assert result['matched'] is False
    assert result['reason'] == 'wallet_not_in_from_or_to'


def test_explain_wallet_not_configured():
    result = explain_wallet_transfer_match(None, {'from': WALLET, 'to': COUNTERPARTY})
    assert result['matched'] is False
    assert result['reason'] == 'monitored_wallet_not_configured'


def test_explain_tx_not_found():
    result = explain_wallet_transfer_match(WALLET, None)
    assert result['matched'] is False
    assert result['reason'] == 'transaction_not_found'


def test_resolve_production_wallet_from_fallback():
    """resolve_monitored_wallet must return the production wallet from a known fallback location."""
    production_wallet = '0x5f6f35fd8b10c5576089f99c7c8c351deb851d1f'
    # Canonical column missing; wallet stored in asset_context.asset_identifier (fallback)
    target = {
        'target_type': 'wallet',
        'wallet_address': None,
        'asset_context': {'asset_identifier': production_wallet.upper()},
    }
    resolved = resolve_monitored_wallet(target)
    assert resolved == production_wallet, (
        f'expected {production_wallet!r} from asset_context fallback, got {resolved!r}'
    )


def test_outbound_transfer_telemetry_fields(monkeypatch):
    """Outbound transfer from production wallet must have all required telemetry fields."""
    import os
    monkeypatch.setenv('EVM_RPC_URL', 'http://rpc')
    monkeypatch.setenv('LIVE_MONITORING_CHAINS', 'ethereum')

    wallet = '0x5f6f35fd8b10c5576089f99c7c8c351deb851d1f'
    counterparty = '0xdeadbeefdeadbeefdeadbeefdeadbeefdeadbeef'
    tx_hash = '0xfeedcafe' + '00' * 28

    class _Rpc:
        def call(self, method: str, params: list) -> object:
            if method == 'eth_blockNumber':
                return hex(5000)
            if method == 'eth_getLogs':
                return []
            if method == 'eth_getBlockByNumber':
                block_num = int(str(params[0]), 16)
                return {
                    'hash': f'0xblk{block_num}',
                    'timestamp': hex(1_700_000_000 + block_num),
                    'transactions': [
                        {
                            'hash': tx_hash,
                            'from': wallet,
                            'to': counterparty,
                            'value': hex(500_000_000_000_000_000),
                            'input': '0x',
                            'blockNumber': hex(block_num),
                            'blockHash': f'0xblk{block_num}',
                        }
                    ],
                }
            return {}

    target = {
        'id': str(uuid.uuid4()),
        'target_type': 'wallet',
        'chain_network': 'ethereum',
        'wallet_address': wallet,
    }
    events = fetch_evm_activity(target, None, rpc_client=_Rpc())
    assert events, 'must detect at least one event'
    tx_events = [e for e in events if str(e.payload.get('tx_hash') or '') == tx_hash]
    assert tx_events, f'no event with tx_hash={tx_hash!r}'
    p = tx_events[0].payload
    assert p.get('tx_hash') == tx_hash, 'tx_hash must be persisted'
    assert str(p.get('from') or '').lower() == wallet, 'from must be the monitored wallet'
    assert str(p.get('to') or '').lower() == counterparty, 'to must be the counterparty'
    assert p.get('block_number') is not None, 'block_number must be persisted'
    assert p.get('wallet_transfer_direction') == 'outbound', 'direction must be outbound'
