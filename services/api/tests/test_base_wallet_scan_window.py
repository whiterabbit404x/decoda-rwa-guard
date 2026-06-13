"""
Tests for Base wallet monitoring scan window and cursor persistence.

Covers:
  A. 5-minute polling interval scans enough Base blocks (no cursor, initial backfill)
  B. scan_to_block is persisted on target after an empty scan (no events)
  C. No block gaps between consecutive polls (cursor advances correctly)
  D. Outbound wallet transfer inside the scanned range is detected
  E. tx_hash inside the scanned range is found and wallet_transfer_detected fires
"""
from __future__ import annotations

import hashlib
import os
import uuid
from datetime import datetime, timezone

import pytest


WALLET_ADDR = '0x5f6f35fd8b10c5576089f99c7c8c351deb851d1f'
BASE_LATEST_BLOCK = 47_276_500
BASE_CONFIRMATIONS = 3
BASE_SAFE_TO = BASE_LATEST_BLOCK - BASE_CONFIRMATIONS  # 47_276_497


def _now() -> datetime:
    return datetime(2026, 6, 13, 9, 0, tzinfo=timezone.utc)


def _make_target(cursor: str | None = None) -> dict:
    return {
        'id': str(uuid.uuid4()),
        'workspace_id': str(uuid.uuid4()),
        'name': 'Base Wallet Target',
        'target_type': 'wallet',
        'chain_network': 'base',
        'wallet_address': WALLET_ADDR,
        'contract_identifier': None,
        'monitoring_checkpoint_cursor': cursor,
        'monitoring_interval_seconds': 300,
    }


class _BaseRpc:
    """Minimal RPC stub: empty blocks, no transactions."""

    def __init__(self, latest: int = BASE_LATEST_BLOCK) -> None:
        self.latest = latest
        self.calls: list[tuple[str, list]] = []

    def call(self, method: str, params: list) -> object:
        self.calls.append((method, params))
        if method == 'eth_chainId':
            return hex(8453)
        if method == 'eth_blockNumber':
            return hex(self.latest)
        if method == 'eth_getBlockByNumber':
            block_number = int(str(params[0]), 16)
            return {
                'hash': f'0xblock{block_number}',
                'number': hex(block_number),
                'timestamp': hex(int(_now().timestamp()) + block_number),
                'transactions': [],
            }
        if method == 'eth_getLogs':
            return []
        return {}


class _RpcWithTransfer(_BaseRpc):
    """RPC stub that returns one outbound transaction from WALLET_ADDR in block tx_block."""

    def __init__(self, tx_block: int, tx_hash: str = '0xdeadbeef01') -> None:
        super().__init__()
        self.tx_block = tx_block
        self.tx_hash = tx_hash

    def call(self, method: str, params: list) -> object:
        self.calls.append((method, params))
        if method == 'eth_chainId':
            return hex(8453)
        if method == 'eth_blockNumber':
            return hex(self.latest)
        if method == 'eth_getBlockByNumber':
            block_number = int(str(params[0]), 16)
            txs = []
            if block_number == self.tx_block:
                txs = [
                    {
                        'hash': self.tx_hash,
                        'from': WALLET_ADDR,
                        'to': '0xcafe00000000000000000000000000000000feed',
                        'value': hex(10 ** 18),
                        'input': '0x',
                        'blockNumber': hex(block_number),
                        'blockHash': f'0xblock{block_number}',
                    }
                ]
            return {
                'hash': f'0xblock{block_number}',
                'number': hex(block_number),
                'timestamp': hex(int(_now().timestamp()) + block_number),
                'transactions': txs,
            }
        if method == 'eth_getLogs':
            return []
        return {}


# ---------------------------------------------------------------------------
# A. Initial scan window covers at least one polling interval on Base
# ---------------------------------------------------------------------------

def test_base_no_cursor_initial_window_covers_300s_polling_interval(monkeypatch):
    """Without a prior cursor, the first Base scan must cover at least 150 blocks
    (300 s / 2 s per block) so no transaction sent during the polling interval is missed.
    The implementation uses a safe_backfill_window of 250 for Base."""
    monkeypatch.setenv('EVM_RPC_URL', 'http://rpc')
    monkeypatch.setenv('LIVE_MONITORING_CHAINS', 'base')
    monkeypatch.setenv('EVM_CONFIRMATIONS_REQUIRED', str(BASE_CONFIRMATIONS))
    monkeypatch.setenv('MONITOR_REPLAY_BLOCKS', '25')
    monkeypatch.delenv('MONITOR_SAFE_BACKFILL', raising=False)

    from services.api.app.evm_activity_provider import fetch_evm_activity

    rpc = _BaseRpc(latest=BASE_LATEST_BLOCK)
    target = _make_target(cursor=None)
    fetch_evm_activity(target, None, rpc_client=rpc)

    block_calls = [int(str(p[0]), 16) for m, p in rpc.calls if m == 'eth_getBlockByNumber']
    assert block_calls, 'scan must request blocks via eth_getBlockByNumber'
    min_block = min(block_calls)
    max_block = max(block_calls)
    blocks_scanned = max_block - min_block + 1

    # 300 s / 2 s per block = 150 blocks minimum
    assert blocks_scanned >= 150, (
        f'Base initial scan must cover ≥150 blocks for a 300-second polling interval; '
        f'got {blocks_scanned} (min={min_block}, max={max_block})'
    )


def test_base_no_cursor_backfill_window_default_is_250(monkeypatch):
    """Default safe_backfill_window for Base is 250 blocks (covers ~8 minutes of blocks)."""
    monkeypatch.setenv('EVM_RPC_URL', 'http://rpc')
    monkeypatch.setenv('LIVE_MONITORING_CHAINS', 'base')
    monkeypatch.setenv('EVM_CONFIRMATIONS_REQUIRED', str(BASE_CONFIRMATIONS))
    monkeypatch.setenv('MONITOR_REPLAY_BLOCKS', '25')
    monkeypatch.delenv('MONITOR_SAFE_BACKFILL', raising=False)

    from services.api.app.evm_activity_provider import fetch_evm_activity

    rpc = _BaseRpc(latest=BASE_LATEST_BLOCK)
    target = _make_target(cursor=None)
    fetch_evm_activity(target, None, rpc_client=rpc)

    block_calls = [int(str(p[0]), 16) for m, p in rpc.calls if m == 'eth_getBlockByNumber']
    min_block = min(block_calls)
    expected_from = BASE_SAFE_TO - 250  # safe_backfill_window=250 for Base

    assert min_block <= expected_from + 25, (
        f'first block scanned ({min_block}) should be near {expected_from} '
        f'(safe_to={BASE_SAFE_TO} - 250); may differ by chunk boundary'
    )


# ---------------------------------------------------------------------------
# B. Scan-to-block is persisted on target after an empty scan
# ---------------------------------------------------------------------------

def test_scan_to_block_set_on_target_after_empty_scan(monkeypatch):
    """After fetch_evm_activity with no wallet transfers, target['_evm_scan_to_block']
    must be set to safe_to so the runner can persist the cursor."""
    monkeypatch.setenv('EVM_RPC_URL', 'http://rpc')
    monkeypatch.setenv('LIVE_MONITORING_CHAINS', 'base')
    monkeypatch.setenv('EVM_CONFIRMATIONS_REQUIRED', str(BASE_CONFIRMATIONS))

    from services.api.app.evm_activity_provider import fetch_evm_activity

    rpc = _BaseRpc(latest=BASE_LATEST_BLOCK)
    target = _make_target(cursor=None)
    events = fetch_evm_activity(target, None, rpc_client=rpc)

    assert events == [], 'expect no events from empty blocks'
    scan_to = target.get('_evm_scan_to_block')
    assert scan_to is not None, '_evm_scan_to_block must be set on target after scan'
    assert scan_to == BASE_SAFE_TO, (
        f'_evm_scan_to_block should be safe_to={BASE_SAFE_TO}, got {scan_to}'
    )


def test_scan_to_block_set_on_target_when_events_found(monkeypatch):
    """fetch_evm_activity sets _evm_scan_to_block even when events are detected."""
    monkeypatch.setenv('EVM_RPC_URL', 'http://rpc')
    monkeypatch.setenv('LIVE_MONITORING_CHAINS', 'base')
    monkeypatch.setenv('EVM_CONFIRMATIONS_REQUIRED', str(BASE_CONFIRMATIONS))
    monkeypatch.setenv('MONITOR_SAFE_BACKFILL', '250')

    from services.api.app.evm_activity_provider import fetch_evm_activity

    tx_block = BASE_SAFE_TO - 10
    rpc = _RpcWithTransfer(tx_block=tx_block, tx_hash='0xabcdef01')
    target = _make_target(cursor=None)
    events = fetch_evm_activity(target, None, rpc_client=rpc)

    assert events, 'expect at least one event for the outbound transfer'
    assert target.get('_evm_scan_to_block') == BASE_SAFE_TO, (
        f'_evm_scan_to_block must equal safe_to={BASE_SAFE_TO} even with events'
    )


# ---------------------------------------------------------------------------
# C. No block gaps between consecutive polls
# ---------------------------------------------------------------------------

def test_no_gap_between_consecutive_polls(monkeypatch):
    """After a first poll, the cursor (monitoring_checkpoint_cursor) must be set
    so the second poll starts from the correct position with no gap."""
    monkeypatch.setenv('EVM_RPC_URL', 'http://rpc')
    monkeypatch.setenv('LIVE_MONITORING_CHAINS', 'base')
    monkeypatch.setenv('EVM_CONFIRMATIONS_REQUIRED', str(BASE_CONFIRMATIONS))
    monkeypatch.setenv('MONITOR_REPLAY_BLOCKS', '25')
    monkeypatch.setenv('MONITOR_SAFE_BACKFILL', '250')

    from services.api.app.evm_activity_provider import fetch_evm_activity

    # First poll: no cursor, chain at BASE_LATEST_BLOCK
    rpc1 = _BaseRpc(latest=BASE_LATEST_BLOCK)
    target = _make_target(cursor=None)
    fetch_evm_activity(target, None, rpc_client=rpc1)

    scan_to_1 = target.get('_evm_scan_to_block')
    assert scan_to_1 == BASE_SAFE_TO, f'First poll scan ceiling should be {BASE_SAFE_TO}'

    # Simulate runner advancing the cursor (as monitoring_runner does)
    target['monitoring_checkpoint_cursor'] = f'{scan_to_1}:checkpoint:-1'

    # Second poll: chain has advanced by 150 blocks (~300 seconds)
    poll2_latest = BASE_LATEST_BLOCK + 150
    poll2_safe_to = poll2_latest - BASE_CONFIRMATIONS
    rpc2 = _BaseRpc(latest=poll2_latest)
    target2 = dict(target)  # copy with the cursor set
    fetch_evm_activity(target2, None, rpc_client=rpc2)

    block_calls_2 = [int(str(p[0]), 16) for m, p in rpc2.calls if m == 'eth_getBlockByNumber']
    assert block_calls_2, 'second poll must scan blocks'
    min_block_2 = min(block_calls_2)
    max_block_2 = max(block_calls_2)

    # Second poll should cover from near (scan_to_1 - replay_blocks) to poll2_safe_to
    # and must include at least some blocks beyond scan_to_1
    assert max_block_2 > scan_to_1, (
        f'Second poll must scan beyond first poll ceiling ({scan_to_1}); '
        f'got max_block={max_block_2}'
    )
    # No gap: second poll must start at or before scan_to_1 (with replay overlap)
    assert min_block_2 <= scan_to_1, (
        f'Second poll must start at or before first poll ceiling ({scan_to_1}) '
        f'to avoid gaps; got min_block={min_block_2}'
    )


# ---------------------------------------------------------------------------
# D. Outbound wallet transfer in scanned range is detected
# ---------------------------------------------------------------------------

def test_outbound_wallet_transfer_in_scan_range_detected(monkeypatch):
    """A wallet transfer from the monitored address within the scanned block range
    must produce an event with wallet_transfer_direction=outbound."""
    monkeypatch.setenv('EVM_RPC_URL', 'http://rpc')
    monkeypatch.setenv('LIVE_MONITORING_CHAINS', 'base')
    monkeypatch.setenv('EVM_CONFIRMATIONS_REQUIRED', str(BASE_CONFIRMATIONS))
    monkeypatch.setenv('MONITOR_SAFE_BACKFILL', '250')

    from services.api.app.evm_activity_provider import fetch_evm_activity

    tx_block = BASE_SAFE_TO - 50  # well within the 250-block backfill window
    tx_hash = '0xoutbound01'
    rpc = _RpcWithTransfer(tx_block=tx_block, tx_hash=tx_hash)
    target = _make_target(cursor=None)
    events = fetch_evm_activity(target, None, rpc_client=rpc)

    wallet_events = [
        e for e in events
        if isinstance(e.payload, dict) and e.payload.get('wallet_transfer_direction') == 'outbound'
    ]
    assert wallet_events, (
        f'Expected at least one outbound wallet transfer event for tx in block {tx_block}; '
        f'got {len(events)} total events: {[e.payload.get("event_type") for e in events]}'
    )
    ev = wallet_events[0]
    assert str(ev.payload.get('from') or '').lower() == WALLET_ADDR.lower()
    assert ev.payload.get('block_number') == tx_block
    assert ev.payload.get('tx_hash') == tx_hash


# ---------------------------------------------------------------------------
# E. tx_hash in scanned range returns wallet_transfer_detected event type
# ---------------------------------------------------------------------------

def test_tx_hash_in_scan_range_produces_wallet_transfer_detected(monkeypatch):
    """A specific tx_hash within the scanned range must appear in the events
    with event_type=transaction and wallet_transfer_direction set (i.e. detected)."""
    monkeypatch.setenv('EVM_RPC_URL', 'http://rpc')
    monkeypatch.setenv('LIVE_MONITORING_CHAINS', 'base')
    monkeypatch.setenv('EVM_CONFIRMATIONS_REQUIRED', str(BASE_CONFIRMATIONS))
    monkeypatch.setenv('MONITOR_SAFE_BACKFILL', '250')

    from services.api.app.evm_activity_provider import fetch_evm_activity

    target_tx_hash = '0xabcdef1234567890abcdef1234567890abcdef1234567890abcdef1234567890'
    tx_block = BASE_SAFE_TO - 100
    rpc = _RpcWithTransfer(tx_block=tx_block, tx_hash=target_tx_hash)
    target = _make_target(cursor=None)
    events = fetch_evm_activity(target, None, rpc_client=rpc)

    matched = [
        e for e in events
        if isinstance(e.payload, dict) and e.payload.get('tx_hash') == target_tx_hash
    ]
    assert matched, (
        f'tx_hash {target_tx_hash} not found in scan events; '
        f'events: {[e.payload.get("tx_hash") for e in events]}'
    )
    ev = matched[0]
    assert ev.payload.get('wallet_transfer_direction') in {'outbound', 'inbound'}, (
        'event for monitored wallet tx must have wallet_transfer_direction set'
    )
    # Verify the cursor encodes the block so the runner can deduplicate
    assert ev.cursor.startswith(str(tx_block)), (
        f'event cursor must start with block_number={tx_block}; got {ev.cursor!r}'
    )
