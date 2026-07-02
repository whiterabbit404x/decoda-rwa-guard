"""Tests for realtime target loading resolving the monitored wallet address.

Production bug: the realtime WebSocket worker loaded the target id but logged
``monitored_address_full=none`` for Base target
``e7851a52-8fb1-48cd-84a3-d033f591c5dd`` (wallet
``0x5f6f35fd8b10c5576089f99c7c8c351deb851d1f``), so it could not detect native
ETH transfers even though the WSS was healthy (heads increasing, degraded=False).
Root cause: realtime only read ``targets.wallet_address`` /
``contract_identifier`` directly, while the stable RPC polling worker resolves the
address via :func:`resolve_monitored_wallet`, which also reads the linked asset's
identifier and ``target_metadata``.

These tests lock in the fix:
  * realtime target loading resolves the monitored address via the SAME resolver
    stable polling uses (canonical column + all known fallbacks)
  * a target with no resolvable address is excluded from matching and marks
    target loading degraded (reason=missing_monitored_address)
  * realtime and stable polling resolve the identical monitored address
  * native ETH FROM / TO the watched wallet are detected once the address resolves
  * the realtime_targets_loaded / realtime_target_diagnostics logs carry the real
    address and address_count (no more monitored_address_full=none)
"""
from __future__ import annotations

import asyncio
import logging
import uuid

import pytest

from services.api.app.evm_activity_provider import resolve_monitored_wallet


# The exact production wallet from migration 0114 / the reported diagnostics.
PROD_WALLET = '0x5f6f35fd8b10c5576089f99c7c8c351deb851d1f'
COUNTERPARTY = '0xcafe00000000000000000000000000000000feed'


def _make_ingestor():
    from services.api.app.base_realtime_ingestor import BaseRealtimeIngestor
    return BaseRealtimeIngestor(
        rpc_url='http://rpc', ws_url='ws://ws', watcher_name='test-watcher',
        confirmations_required=1, max_events_per_minute=1000,
    )


def _target(**overrides) -> dict:
    base = {
        'id': str(uuid.uuid4()),
        'workspace_id': str(uuid.uuid4()),
        'name': 'Base Wallet',
        'target_type': 'wallet',
        'chain_network': 'base',
        'chain_id': 8453,
        'wallet_address': None,
        'contract_identifier': None,
        'asset_id': None,
        'asset_context': None,
        'target_metadata': None,
        'monitoring_enabled': True,
        'enabled': True,
        'is_active': True,
    }
    base.update(overrides)
    return base


def _native_tx(*, tx_hash: str, from_addr: str, to_addr: str, value_wei: int = 10 ** 15) -> dict:
    return {
        'hash': tx_hash, 'from': from_addr, 'to': to_addr,
        'value': hex(value_wei), 'input': '0x', 'blockNumber': hex(100),
    }


def _block_with(txs: list[dict], *, number: int = 100) -> dict:
    return {
        'hash': f'0xblock{number:064x}'[:66], 'number': hex(number),
        'timestamp': hex(1_700_000_000), 'transactions': txs,
    }


# ---------------------------------------------------------------------------
# 1. Realtime target loading includes the monitored address
# ---------------------------------------------------------------------------

def test_target_loading_includes_monitored_address_canonical_column(monkeypatch):
    ing = _make_ingestor()
    target = _target(wallet_address=PROD_WALLET)
    monkeypatch.setattr(ing, '_watched_targets', lambda: [target])

    pairs = ing._watched_wallet_pairs()

    assert len(pairs) == 1
    _t, addr = pairs[0]
    assert addr == PROD_WALLET  # normalized lowercase 0x
    assert ing.state['metrics']['targets_with_address'] == 1
    assert ing.state['metrics']['targets_missing_address'] == 0


def test_target_loading_resolves_from_asset_identifier_fallback(monkeypatch):
    """The production case: wallet_address column empty, address in the linked
    asset's identifier (exposed as asset_context)."""
    ing = _make_ingestor()
    target = _target(
        wallet_address=None,
        asset_context={'asset_identifier': PROD_WALLET.upper()},
    )
    monkeypatch.setattr(ing, '_watched_targets', lambda: [target])

    pairs = ing._watched_wallet_pairs()

    assert [addr for _t, addr in pairs] == [PROD_WALLET]


def test_target_loading_resolves_from_contract_identifier_fallback(monkeypatch):
    ing = _make_ingestor()
    target = _target(wallet_address=None, contract_identifier=PROD_WALLET)
    monkeypatch.setattr(ing, '_watched_targets', lambda: [target])

    assert [addr for _t, addr in ing._watched_wallet_pairs()] == [PROD_WALLET]


def test_target_loading_resolves_from_target_metadata_fallback(monkeypatch):
    ing = _make_ingestor()
    target = _target(wallet_address=None, target_metadata={'monitored_wallet': PROD_WALLET})
    monkeypatch.setattr(ing, '_watched_targets', lambda: [target])

    assert [addr for _t, addr in ing._watched_wallet_pairs()] == [PROD_WALLET]


def test_target_loading_loads_asset_context_on_demand(monkeypatch):
    """When wallet_address is empty and asset_context is not yet attached, the
    linked asset context is loaded on demand (same as stable polling)."""
    ing = _make_ingestor()
    target = _target(wallet_address=None, asset_id=str(uuid.uuid4()))
    monkeypatch.setattr(ing, '_watched_targets', lambda: [target])
    # Simulate _load_target_asset_context returning the asset whose identifier is the wallet.
    monkeypatch.setattr(
        ing, '_load_asset_context_for',
        lambda t: {'asset_identifier': PROD_WALLET},
    )

    pairs = ing._watched_wallet_pairs()

    assert [addr for _t, addr in pairs] == [PROD_WALLET]
    # Resolved context is cached back onto the target dict.
    assert target['asset_context'] == {'asset_identifier': PROD_WALLET}


# ---------------------------------------------------------------------------
# 2. Missing address → excluded + target loading degraded
# ---------------------------------------------------------------------------

def test_missing_address_excludes_target_and_marks_degraded(monkeypatch):
    ing = _make_ingestor()
    target = _target(wallet_address=None, contract_identifier=None, asset_id=None)
    monkeypatch.setattr(ing, '_watched_targets', lambda: [target])

    pairs = ing._watched_wallet_pairs()

    assert pairs == []  # excluded from realtime matching
    assert ing._target_loading_degraded is True
    assert ing._target_loading_degraded_reason == 'missing_monitored_address'
    assert ing.state['metrics']['targets_missing_address'] == 1
    assert ing.state['metrics']['target_loading_degraded'] is True
    assert ing.state['metrics']['target_loading_degraded_reason'] == 'missing_monitored_address'


def test_healthy_load_clears_target_loading_degraded(monkeypatch):
    ing = _make_ingestor()
    ing._target_loading_degraded = True
    ing._target_loading_degraded_reason = 'missing_monitored_address'
    target = _target(wallet_address=PROD_WALLET)
    monkeypatch.setattr(ing, '_watched_targets', lambda: [target])

    ing._watched_wallet_pairs()

    assert ing._target_loading_degraded is False
    assert ing._target_loading_degraded_reason is None


def test_mixed_load_excludes_only_missing_target(monkeypatch):
    ing = _make_ingestor()
    good = _target(wallet_address=PROD_WALLET)
    bad = _target(wallet_address=None, contract_identifier=None, asset_id=None)
    monkeypatch.setattr(ing, '_watched_targets', lambda: [good, bad])

    pairs = ing._watched_wallet_pairs()

    assert [addr for _t, addr in pairs] == [PROD_WALLET]
    assert ing.state['metrics']['targets_loaded'] == 2
    assert ing.state['metrics']['targets_with_address'] == 1
    assert ing.state['metrics']['targets_missing_address'] == 1
    assert ing._target_loading_degraded is True


# ---------------------------------------------------------------------------
# 3. Stable polling and realtime resolve the SAME monitored address
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    'overrides',
    [
        {'wallet_address': PROD_WALLET},
        {'wallet_address': None, 'contract_identifier': PROD_WALLET},
        {'wallet_address': None, 'asset_context': {'asset_identifier': PROD_WALLET}},
        {'wallet_address': None, 'target_metadata': {'monitored_wallet': PROD_WALLET}},
    ],
)
def test_stable_and_realtime_resolve_same_address(overrides):
    ing = _make_ingestor()
    target = _target(**overrides)
    stable = resolve_monitored_wallet(dict(target))
    realtime = ing._resolve_target_address(dict(target))
    assert stable == realtime == PROD_WALLET


# ---------------------------------------------------------------------------
# 4. Native ETH transfer detected (FROM and TO) once the address resolves
# ---------------------------------------------------------------------------

def test_native_eth_from_watched_wallet_detected_after_resolution(monkeypatch):
    """Outbound native ETH (tx.from == watched wallet resolved from a fallback) is
    detected by the realtime head scan, tagged detected_by=realtime_websocket."""
    ing = _make_ingestor()  # confirmations_required=1
    target = _target(wallet_address=None, asset_context={'asset_identifier': PROD_WALLET})
    monkeypatch.setattr(ing, '_watched_targets', lambda: [target])
    block = _block_with(
        [_native_tx(tx_hash='0xout', from_addr=PROD_WALLET.upper().replace('0X', '0x'), to_addr=COUNTERPARTY)],
        number=100,
    )
    monkeypatch.setattr(
        ing, '_rpc_call',
        lambda method, params: block if method == 'eth_getBlockByNumber' else None,
    )
    persisted: list = []
    monkeypatch.setattr(
        ing, '_persist_event',
        lambda _t, e: (persisted.append(e), {'status': 'processed', 'event_id': e.event_id})[1],
    )
    ing.state['last_processed_block'] = 99

    processed = asyncio.run(ing._scan_head_native_transfers(101))  # safe_to=100

    assert processed == 1
    assert persisted[0].payload['wallet_transfer_direction'] == 'outbound'
    assert persisted[0].payload['detected_by'] == 'realtime_websocket'
    assert persisted[0].payload['tx_hash'] == '0xout'


def test_native_eth_to_watched_wallet_detected_after_resolution(monkeypatch):
    """Inbound native ETH (tx.to == watched wallet) is detected after resolution."""
    ing = _make_ingestor()
    target = _target(wallet_address=None, contract_identifier=PROD_WALLET)
    monkeypatch.setattr(ing, '_watched_targets', lambda: [target])
    block = _block_with(
        [_native_tx(tx_hash='0xin', from_addr=COUNTERPARTY, to_addr=PROD_WALLET.upper().replace('0X', '0x'))],
        number=100,
    )
    monkeypatch.setattr(
        ing, '_rpc_call',
        lambda method, params: block if method == 'eth_getBlockByNumber' else None,
    )
    persisted: list = []
    monkeypatch.setattr(
        ing, '_persist_event',
        lambda _t, e: (persisted.append(e), {'status': 'processed', 'event_id': e.event_id})[1],
    )
    ing.state['last_processed_block'] = 99

    processed = asyncio.run(ing._scan_head_native_transfers(101))

    assert processed == 1
    assert persisted[0].payload['wallet_transfer_direction'] == 'inbound'
    assert persisted[0].payload['detected_by'] == 'realtime_websocket'


# ---------------------------------------------------------------------------
# 5. Logs: realtime_targets_loaded address_count + full-address diagnostics
# ---------------------------------------------------------------------------

def test_realtime_targets_loaded_logs_address_count(monkeypatch, caplog):
    ing = _make_ingestor()
    monkeypatch.setattr(ing, '_watched_targets', lambda: [_target(wallet_address=PROD_WALLET)])

    with caplog.at_level(logging.INFO, logger='services.api.app.base_realtime_ingestor'):
        ing._watched_wallet_pairs(log_summary=True)

    msgs = [r.getMessage() for r in caplog.records]
    assert any('realtime_targets_loaded' in m and 'count=1' in m and 'address_count=1' in m for m in msgs), msgs


def test_realtime_target_address_missing_logged(monkeypatch, caplog):
    ing = _make_ingestor()
    bad = _target(wallet_address=None, contract_identifier=None, asset_id=None)
    monkeypatch.setattr(ing, '_watched_targets', lambda: [bad])

    with caplog.at_level(logging.INFO, logger='services.api.app.base_realtime_ingestor'):
        ing._watched_wallet_pairs(log_summary=True)

    msgs = [r.getMessage() for r in caplog.records]
    assert any(f"realtime_target_address_missing target_id={bad['id']}" in m for m in msgs), msgs
    assert any('address_count=0' in m for m in msgs), msgs


def test_target_diagnostics_shows_full_address_not_none(monkeypatch, caplog):
    """Acceptance: logs no longer show monitored_address_full=none once the address
    resolves from a fallback location."""
    ing = _make_ingestor()
    target = _target(wallet_address=None, asset_context={'asset_identifier': PROD_WALLET})

    with caplog.at_level(logging.INFO, logger='services.api.app.base_realtime_ingestor'):
        ing._log_target_diagnostics([target])

    msgs = [r.getMessage() for r in caplog.records]
    diag = [m for m in msgs if 'realtime_target_diagnostics' in m]
    assert diag, msgs
    assert f'monitored_address_full={PROD_WALLET}' in diag[0]
    assert f'normalized_address_lowercase={PROD_WALLET}' in diag[0]
    assert 'monitored_address_full=none' not in diag[0]
