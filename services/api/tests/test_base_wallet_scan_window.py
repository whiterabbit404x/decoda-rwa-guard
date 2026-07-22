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

def test_base_no_cursor_initial_window_starts_at_live_tail(monkeypatch):
    """Datto USDC runaway fix (Section 2 + 13): a target with NO cursor must NOT backfill
    hundreds/thousands of blocks in its first health poll. In the polling-only MVP it starts
    at the recent LIVE TAIL — INITIAL_LIVE_TAIL_BLOCKS (default 10) ending at safe_head — so
    the first poll scans ~10 blocks near the head, never a 300/2000-block backfill."""
    monkeypatch.setenv('EVM_RPC_URL', 'http://rpc')
    monkeypatch.setenv('LIVE_MONITORING_CHAINS', 'base')
    monkeypatch.setenv('EVM_CONFIRMATIONS_REQUIRED', str(BASE_CONFIRMATIONS))
    monkeypatch.setenv('MONITOR_REPLAY_BLOCKS', '25')
    monkeypatch.delenv('MONITOR_SAFE_BACKFILL', raising=False)
    monkeypatch.delenv('HISTORICAL_BACKFILL_ENABLED', raising=False)

    from services.api.app.evm_activity_provider import fetch_evm_activity

    rpc = _BaseRpc(latest=BASE_LATEST_BLOCK)
    target = _make_target(cursor=None)
    fetch_evm_activity(target, None, rpc_client=rpc)

    block_calls = [int(str(p[0]), 16) for m, p in rpc.calls if m == 'eth_getBlockByNumber']
    assert block_calls, 'scan must request blocks via eth_getBlockByNumber'
    min_block = min(block_calls)
    max_block = max(block_calls)
    blocks_scanned = max_block - min_block + 1

    # Bounded live tail: 10–25 blocks near the head, ending at safe_to. Never a wide backfill.
    assert blocks_scanned <= 25, f'no-cursor poll must be live-tail bounded (<=25); got {blocks_scanned}'
    assert max_block == BASE_SAFE_TO, f'live tail must end at safe_to={BASE_SAFE_TO}; got {max_block}'
    assert min_block == BASE_SAFE_TO - 10 + 1, (
        f'live tail must start at safe_to - INITIAL_LIVE_TAIL_BLOCKS + 1; got {min_block}'
    )


def test_base_no_cursor_live_tail_default_is_ten_blocks(monkeypatch):
    """The polling-only MVP live-tail start is INITIAL_LIVE_TAIL_BLOCKS (default 10) — the
    wide safe_backfill_window is deep historical backfill, disabled by default."""
    monkeypatch.setenv('EVM_RPC_URL', 'http://rpc')
    monkeypatch.setenv('LIVE_MONITORING_CHAINS', 'base')
    monkeypatch.setenv('EVM_CONFIRMATIONS_REQUIRED', str(BASE_CONFIRMATIONS))
    monkeypatch.setenv('MONITOR_REPLAY_BLOCKS', '25')
    monkeypatch.delenv('MONITOR_SAFE_BACKFILL', raising=False)
    monkeypatch.delenv('HISTORICAL_BACKFILL_ENABLED', raising=False)

    from services.api.app.evm_activity_provider import fetch_evm_activity

    rpc = _BaseRpc(latest=BASE_LATEST_BLOCK)
    target = _make_target(cursor=None)
    fetch_evm_activity(target, None, rpc_client=rpc)

    block_calls = [int(str(p[0]), 16) for m, p in rpc.calls if m == 'eth_getBlockByNumber']
    min_block = min(block_calls)
    expected_from = BASE_SAFE_TO - 10 + 1  # INITIAL_LIVE_TAIL_BLOCKS=10 ending at safe_to

    assert min_block == expected_from, (
        f'first block scanned ({min_block}) must be safe_to - 10 + 1 = {expected_from}'
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
    monkeypatch.delenv('MONITOR_SAFE_BACKFILL', raising=False)

    from services.api.app.evm_activity_provider import fetch_evm_activity

    # Live-tail window is [safe_to-9, safe_to]; place the tx inside it.
    tx_block = BASE_SAFE_TO - 5
    rpc = _RpcWithTransfer(tx_block=tx_block, tx_hash='0xabcdef01')
    target = _make_target(cursor=None)
    events = fetch_evm_activity(target, None, rpc_client=rpc)

    assert events, 'expect at least one event for the outbound transfer'
    assert target.get('_evm_scan_to_block') == BASE_SAFE_TO, (
        f'_evm_scan_to_block must equal safe_to={BASE_SAFE_TO} even with events'
    )


# ---------------------------------------------------------------------------
# C. Live-tail sampling tracks the head across consecutive polls
# ---------------------------------------------------------------------------

def test_live_tail_tracks_head_across_polls(monkeypatch):
    """Polling-only MVP (Section 13): a scheduled poll scans the recent live tail near the
    head. When the chain advances more than one bounded window between polls (Base ~450
    blocks / 15 min), the second poll samples the live tail near the NEW head — coverage
    tracks the head instead of the cursor lagging 25 blocks/cycle behind. The skipped gap
    is deferred backfill (disabled by default), never a wide scheduled scan."""
    monkeypatch.setenv('EVM_RPC_URL', 'http://rpc')
    monkeypatch.setenv('LIVE_MONITORING_CHAINS', 'base')
    monkeypatch.setenv('EVM_CONFIRMATIONS_REQUIRED', str(BASE_CONFIRMATIONS))
    monkeypatch.setenv('MONITOR_REPLAY_BLOCKS', '25')
    monkeypatch.delenv('HISTORICAL_BACKFILL_ENABLED', raising=False)

    from services.api.app.evm_activity_provider import fetch_evm_activity

    # First poll: no cursor, chain at BASE_LATEST_BLOCK
    rpc1 = _BaseRpc(latest=BASE_LATEST_BLOCK)
    target = _make_target(cursor=None)
    fetch_evm_activity(target, None, rpc_client=rpc1)

    scan_to_1 = target.get('_evm_scan_to_block')
    assert scan_to_1 == BASE_SAFE_TO, f'First poll scan ceiling should be {BASE_SAFE_TO}'

    # Simulate runner advancing the cursor (as monitoring_runner does)
    target['monitoring_checkpoint_cursor'] = f'{scan_to_1}:checkpoint:-1'

    # Second poll: chain has advanced by 150 blocks (> one bounded window)
    poll2_latest = BASE_LATEST_BLOCK + 150
    poll2_safe_to = poll2_latest - BASE_CONFIRMATIONS
    rpc2 = _BaseRpc(latest=poll2_latest)
    target2 = dict(target)  # copy with the cursor set
    fetch_evm_activity(target2, None, rpc_client=rpc2)

    block_calls_2 = [int(str(p[0]), 16) for m, p in rpc2.calls if m == 'eth_getBlockByNumber']
    assert block_calls_2, 'second poll must scan blocks'
    min_block_2 = min(block_calls_2)
    max_block_2 = max(block_calls_2)

    # Coverage tracks the head: the second poll ends at the NEW safe_to and stays bounded.
    assert max_block_2 == poll2_safe_to, (
        f'Second poll must reach the new head safe_to={poll2_safe_to}; got {max_block_2}'
    )
    assert max_block_2 - min_block_2 + 1 <= 25, (
        f'Second poll must stay live-tail bounded (<=25 blocks); got {max_block_2 - min_block_2 + 1}'
    )
    # The scan cursor advances to the new head so freshness reflects the real chain tip.
    assert target2.get('_evm_scan_to_block') == poll2_safe_to


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

    tx_block = BASE_SAFE_TO - 5  # within the live-tail window [safe_to-9, safe_to]
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
    tx_block = BASE_SAFE_TO - 5  # within the live-tail window [safe_to-9, safe_to]
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


# ---------------------------------------------------------------------------
# F. The live-tail start is bounded regardless of MONITOR_SAFE_BACKFILL
# ---------------------------------------------------------------------------

def test_no_cursor_live_tail_bounded_regardless_of_safe_backfill(monkeypatch):
    """In the polling-only MVP the no-cursor start is always the bounded live tail
    (INITIAL_LIVE_TAIL_BLOCKS, <=25), regardless of MONITOR_SAFE_BACKFILL — which now
    only governs the operator-enabled historical backfill job. A large or small
    MONITOR_SAFE_BACKFILL can never inflate a scheduled health poll into a wide scan."""
    monkeypatch.setenv('EVM_RPC_URL', 'http://rpc')
    monkeypatch.setenv('LIVE_MONITORING_CHAINS', 'base')
    monkeypatch.setenv('EVM_CONFIRMATIONS_REQUIRED', str(BASE_CONFIRMATIONS))
    monkeypatch.setenv('MONITOR_REPLAY_BLOCKS', '25')
    monkeypatch.setenv('MONITOR_SAFE_BACKFILL', '5000')  # would be a huge backfill if honored
    monkeypatch.delenv('HISTORICAL_BACKFILL_ENABLED', raising=False)

    from services.api.app.evm_activity_provider import fetch_evm_activity

    rpc = _BaseRpc(latest=BASE_LATEST_BLOCK)
    target = _make_target(cursor=None)
    fetch_evm_activity(target, None, rpc_client=rpc)

    block_calls = [int(str(p[0]), 16) for m, p in rpc.calls if m == 'eth_getBlockByNumber']
    assert block_calls, 'scan must request blocks'
    blocks_scanned = max(block_calls) - min(block_calls) + 1
    assert blocks_scanned <= 25, (
        f'a scheduled health poll must stay live-tail bounded (<=25) even with '
        f'MONITOR_SAFE_BACKFILL=5000; got {blocks_scanned}'
    )
    assert max(block_calls) == BASE_SAFE_TO, 'live tail must end at safe_to'


# ---------------------------------------------------------------------------
# G. Scan ceiling (safe_to) is always the latest_block in ActivityProviderResult
# ---------------------------------------------------------------------------

def test_latest_block_equals_scan_ceiling_on_empty_scan(monkeypatch):
    """With no wallet transfers, activity_providers must set latest_block = safe_to
    (the scan ceiling), not None, so the runner can advance the cursor."""
    monkeypatch.setenv('EVM_RPC_URL', 'http://rpc')
    monkeypatch.setenv('LIVE_MODE_ENABLED', 'true')
    monkeypatch.setenv('LIVE_MONITORING_CHAINS', 'base')
    monkeypatch.setenv('EVM_CONFIRMATIONS_REQUIRED', str(BASE_CONFIRMATIONS))
    monkeypatch.setenv('MONITOR_SAFE_BACKFILL', '300')

    from services.api.app.activity_providers import fetch_target_activity_result

    rpc = _BaseRpc(latest=BASE_LATEST_BLOCK)
    target = _make_target(cursor=None)
    # Patch fetch_evm_activity to use our stub RPC
    import services.api.app.activity_providers as _ap
    _orig = _ap.fetch_evm_activity

    def _patched(t, since_ts):
        from services.api.app.evm_activity_provider import fetch_evm_activity as _real
        return _real(t, since_ts, rpc_client=rpc)

    monkeypatch.setattr(_ap, 'fetch_evm_activity', _patched)
    result = fetch_target_activity_result(target, None)

    assert result.latest_block is not None, 'latest_block must not be None after a successful scan'
    assert result.latest_block == BASE_SAFE_TO, (
        f'latest_block must equal safe_to={BASE_SAFE_TO}; got {result.latest_block}'
    )


def test_latest_block_equals_scan_ceiling_when_events_found(monkeypatch):
    """With wallet transfers detected, latest_block must still equal safe_to (not the
    highest event block).  This ensures the runner cursor advances to the full scan
    ceiling and does not leave a gap between the last event block and safe_to."""
    monkeypatch.setenv('EVM_RPC_URL', 'http://rpc')
    monkeypatch.setenv('LIVE_MODE_ENABLED', 'true')
    monkeypatch.setenv('LIVE_MONITORING_CHAINS', 'base')
    monkeypatch.setenv('EVM_CONFIRMATIONS_REQUIRED', str(BASE_CONFIRMATIONS))
    monkeypatch.setenv('MONITOR_SAFE_BACKFILL', '300')

    from services.api.app.activity_providers import fetch_target_activity_result

    tx_block = BASE_SAFE_TO - 5  # event block below the scan ceiling, within the live tail
    rpc = _RpcWithTransfer(tx_block=tx_block, tx_hash='0xcafe01')
    target = _make_target(cursor=None)
    import services.api.app.activity_providers as _ap

    def _patched(t, since_ts):
        from services.api.app.evm_activity_provider import fetch_evm_activity as _real
        return _real(t, since_ts, rpc_client=rpc)

    monkeypatch.setattr(_ap, 'fetch_evm_activity', _patched)
    result = fetch_target_activity_result(target, None)

    assert result.events, 'must detect the wallet transfer event'
    assert result.latest_block == BASE_SAFE_TO, (
        f'latest_block must equal scan ceiling safe_to={BASE_SAFE_TO}, '
        f'not event block={tx_block}; got {result.latest_block}'
    )


# ---------------------------------------------------------------------------
# H. Cursor advances to the live head so freshness reflects the real chain tip
# ---------------------------------------------------------------------------

def test_cursor_advances_to_live_head_across_polls_when_events_found(monkeypatch):
    """The scan cursor (latest_block) always advances to the current safe_to so runtime
    freshness reflects the real chain tip, even when a poll detects events below the head
    and the chain has advanced more than one bounded window since the last poll."""
    monkeypatch.setenv('EVM_RPC_URL', 'http://rpc')
    monkeypatch.setenv('LIVE_MODE_ENABLED', 'true')
    monkeypatch.setenv('LIVE_MONITORING_CHAINS', 'base')
    monkeypatch.setenv('EVM_CONFIRMATIONS_REQUIRED', str(BASE_CONFIRMATIONS))
    monkeypatch.setenv('MONITOR_REPLAY_BLOCKS', '25')
    monkeypatch.delenv('HISTORICAL_BACKFILL_ENABLED', raising=False)

    from services.api.app.activity_providers import fetch_target_activity_result
    import services.api.app.activity_providers as _ap

    tx_block = BASE_SAFE_TO - 5  # within the first poll's live tail
    rpc1 = _RpcWithTransfer(tx_block=tx_block, tx_hash='0xgap01')
    target = _make_target(cursor=None)

    def _patched1(t, since_ts):
        from services.api.app.evm_activity_provider import fetch_evm_activity as _real
        return _real(t, since_ts, rpc_client=rpc1)

    monkeypatch.setattr(_ap, 'fetch_evm_activity', _patched1)
    result1 = fetch_target_activity_result(target, None)

    assert result1.latest_block == BASE_SAFE_TO, (
        f'First poll: latest_block must be scan ceiling {BASE_SAFE_TO}, got {result1.latest_block}'
    )

    # Simulate what monitoring_runner does: advance cursor to scan ceiling
    cursor_after_poll1 = f'{result1.latest_block}:checkpoint:-1'
    target['monitoring_checkpoint_cursor'] = cursor_after_poll1

    # Second poll: chain advanced 150 blocks (> one bounded window)
    poll2_latest = BASE_LATEST_BLOCK + 150
    poll2_safe_to = poll2_latest - BASE_CONFIRMATIONS
    rpc2 = _BaseRpc(latest=poll2_latest)

    def _patched2(t, since_ts):
        from services.api.app.evm_activity_provider import fetch_evm_activity as _real
        return _real(t, since_ts, rpc_client=rpc2)

    monkeypatch.setattr(_ap, 'fetch_evm_activity', _patched2)
    result2 = fetch_target_activity_result(target, None)

    block_calls_2 = [int(str(p[0]), 16) for m, p in rpc2.calls if m == 'eth_getBlockByNumber']
    assert block_calls_2, 'second poll must scan blocks'
    # Coverage tracks the head: the second poll reaches the new safe_to, staying bounded.
    assert max(block_calls_2) == poll2_safe_to, (
        f'Second poll must reach the new head {poll2_safe_to}; got {max(block_calls_2)}'
    )
    assert result2.latest_block == poll2_safe_to, (
        f'latest_block must advance to the new head {poll2_safe_to}; got {result2.latest_block}'
    )
    assert max(block_calls_2) - min(block_calls_2) + 1 <= 25, 'second poll must stay bounded'
