"""
Tests for EVM block scanning catch-up mode.

Covers:
  A. Far-behind cursor scans at most MAX_BLOCKS_PER_CYCLE, not 67k+ blocks
  B. Cursor advances to chunk ceiling after each cycle (not chain head)
  C. Native ETH transfer detection works even when eth_getLogs returns 400
  D. eth_getLogs 400 is logged once per cycle, not per chunk (no log spam)
  E. Catch-up proceeds incrementally over multiple cycles
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from urllib.error import HTTPError
from io import BytesIO

import pytest


WALLET_ADDR = '0x5f6f35fd8b10c5576089f99c7c8c351deb851d1f'
BASE_CONFIRMATIONS = 3

# Simulate worker being 67k blocks behind (reproduces the Railway blocker)
CHAIN_LATEST = 47_353_613
CURSOR_BLOCK = 47_286_496  # previous_cursor from the failing logs
CHAIN_SAFE_TO = CHAIN_LATEST - BASE_CONFIRMATIONS  # 47_353_610


def _now() -> datetime:
    return datetime(2026, 6, 15, 4, 0, tzinfo=timezone.utc)


def _make_target(cursor_block: int | None = None) -> dict:
    cursor = f'{cursor_block}:checkpoint:-1' if cursor_block is not None else None
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
    """Minimal RPC stub returning empty blocks."""

    def __init__(self, latest: int = CHAIN_LATEST) -> None:
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


class _RpcLogsError(_BaseRpc):
    """RPC that raises HTTP 400 for eth_getLogs but returns blocks normally."""

    def call(self, method: str, params: list) -> object:
        self.calls.append((method, params))
        if method == 'eth_getLogs':
            raise RuntimeError('HTTP Error 400: Bad Request')
        return super().call(method, params)


class _RpcWithNativeTransfer(_BaseRpc):
    """RPC that returns a native ETH transfer in a specific block."""

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
                txs = [{
                    'hash': self.tx_hash,
                    'from': WALLET_ADDR,
                    'to': '0xcafe00000000000000000000000000000000feed',
                    'value': hex(10 ** 18),
                    'input': '0x',
                    'blockNumber': hex(block_number),
                    'blockHash': f'0xblock{block_number}',
                }]
            return {
                'hash': f'0xblock{block_number}',
                'number': hex(block_number),
                'timestamp': hex(int(_now().timestamp()) + block_number),
                'transactions': txs,
            }
        if method == 'eth_getLogs':
            return []
        return {}


class _RpcLogsErrorWithNativeTransfer(_RpcWithNativeTransfer):
    """eth_getLogs raises 400 but native transfer is still in a specific block."""

    def call(self, method: str, params: list) -> object:
        self.calls.append((method, params))
        if method == 'eth_getLogs':
            raise RuntimeError('HTTP Error 400: Bad Request')
        # Delegate to parent for block fetching
        return _RpcWithNativeTransfer.call(self, method, params)


# ---------------------------------------------------------------------------
# A. Far-behind cursor: scan is capped to MAX_BLOCKS_PER_CYCLE
# ---------------------------------------------------------------------------

def test_far_behind_cursor_capped_to_max_blocks_per_cycle(monkeypatch):
    """When the cursor is 67k blocks behind, the catch-up backfill must scan at most
    BASE_CATCHUP_MAX_BLOCKS_PER_CYCLE blocks (default 100 for Base), not 67k+.

    The live-tail window is disabled here so the measurement isolates the catch-up
    backfill cap (the live-tail scans recent blocks in a separate range)."""
    monkeypatch.setenv('EVM_RPC_URL', 'http://rpc')
    monkeypatch.setenv('LIVE_MONITORING_CHAINS', 'base')
    monkeypatch.setenv('EVM_CONFIRMATIONS_REQUIRED', str(BASE_CONFIRMATIONS))
    monkeypatch.setenv('MONITOR_REPLAY_BLOCKS', '25')
    monkeypatch.setenv('EVM_LIVE_TAIL_BLOCKS', '0')
    monkeypatch.delenv('MAX_BLOCKS_PER_CYCLE', raising=False)
    monkeypatch.delenv('BASE_CATCHUP_MAX_BLOCKS_PER_CYCLE', raising=False)

    from services.api.app.evm_activity_provider import fetch_evm_activity

    target = _make_target(cursor_block=CURSOR_BLOCK)
    rpc = _BaseRpc(latest=CHAIN_LATEST)
    fetch_evm_activity(target, None, rpc_client=rpc)

    block_calls = [int(str(p[0]), 16) for m, p in rpc.calls if m == 'eth_getBlockByNumber']
    assert block_calls, 'scan must call eth_getBlockByNumber'
    blocks_scanned = max(block_calls) - min(block_calls) + 1

    assert blocks_scanned <= 100, (
        f'Far-behind cursor must scan at most 100 blocks per catch-up cycle (Base default); '
        f'got {blocks_scanned} (min={min(block_calls)}, max={max(block_calls)}). '
        f'blocks_behind={(CHAIN_SAFE_TO - CURSOR_BLOCK)}'
    )


def test_max_blocks_per_cycle_env_var_respected(monkeypatch):
    """MAX_BLOCKS_PER_CYCLE=500 must limit the catch-up backfill to 500 blocks."""
    monkeypatch.setenv('EVM_RPC_URL', 'http://rpc')
    monkeypatch.setenv('LIVE_MONITORING_CHAINS', 'base')
    monkeypatch.setenv('EVM_CONFIRMATIONS_REQUIRED', str(BASE_CONFIRMATIONS))
    monkeypatch.setenv('MONITOR_REPLAY_BLOCKS', '25')
    monkeypatch.setenv('EVM_LIVE_TAIL_BLOCKS', '0')
    monkeypatch.setenv('MAX_BLOCKS_PER_CYCLE', '500')

    from services.api.app.evm_activity_provider import fetch_evm_activity

    target = _make_target(cursor_block=CURSOR_BLOCK)
    rpc = _BaseRpc(latest=CHAIN_LATEST)
    fetch_evm_activity(target, None, rpc_client=rpc)

    block_calls = [int(str(p[0]), 16) for m, p in rpc.calls if m == 'eth_getBlockByNumber']
    assert block_calls
    blocks_scanned = max(block_calls) - min(block_calls) + 1

    assert blocks_scanned <= 500, (
        f'MAX_BLOCKS_PER_CYCLE=500 must cap scan to 500 blocks; got {blocks_scanned}'
    )


# ---------------------------------------------------------------------------
# B. Cursor advances to chunk ceiling (not chain head) during catch-up
# ---------------------------------------------------------------------------

def test_cursor_advances_to_chunk_ceiling_not_chain_head(monkeypatch):
    """After a catch-up cycle, _evm_scan_to_block must be the chunk ceiling, not the
    chain head. The per-target hard ceiling MAX_BLOCKS_PER_TARGET_PER_CYCLE (default 25)
    caps the cycle even when the legacy MAX_BLOCKS_PER_CYCLE asks for 1000 — the exact
    bypass the Datto USDC runaway hit. The reorg overlap is shrunk when it would consume
    the whole budget so catch-up still advances."""
    monkeypatch.setenv('EVM_RPC_URL', 'http://rpc')
    monkeypatch.setenv('LIVE_MONITORING_CHAINS', 'base')
    monkeypatch.setenv('EVM_CONFIRMATIONS_REQUIRED', str(BASE_CONFIRMATIONS))
    monkeypatch.setenv('MONITOR_REPLAY_BLOCKS', '25')
    # Legacy knob asks for 1000, but the hard 25-block ceiling wins.
    monkeypatch.setenv('MAX_BLOCKS_PER_CYCLE', '1000')
    # Cursor-based catch-up is historical-backfill behavior (gated); enable it here.
    monkeypatch.setenv('HISTORICAL_BACKFILL_ENABLED', 'true')

    from services.api.app.evm_activity_provider import fetch_evm_activity

    target = _make_target(cursor_block=CURSOR_BLOCK)
    rpc = _BaseRpc(latest=CHAIN_LATEST)
    fetch_evm_activity(target, None, rpc_client=rpc)

    scan_to = target.get('_evm_scan_to_block')
    assert scan_to is not None, '_evm_scan_to_block must be set on target after scan'
    assert scan_to < CHAIN_SAFE_TO, (
        f'In catch-up mode, _evm_scan_to_block ({scan_to}) must be less than '
        f'chain head safe_to ({CHAIN_SAFE_TO}); should equal chunk ceiling'
    )
    # MAX_BLOCKS_PER_TARGET_PER_CYCLE=25 hard-caps the window; the reorg overlap (25)
    # equals the budget so it shrinks to 25//3=8 to keep forward progress.
    # from_block = CURSOR_BLOCK - 8; scan_ceiling = from_block + 25 - 1 = CURSOR_BLOCK + 16.
    expected_from = CURSOR_BLOCK - 8
    expected_ceiling = expected_from + 25 - 1
    assert scan_to == expected_ceiling, (
        f'scan_ceiling must be from_block ({expected_from}) + 25 - 1 = {expected_ceiling}; '
        f'got {scan_to}'
    )
    # Never more than the hard 25-block ceiling of eth_getBlockByNumber backfill blocks.
    backfill_calls = sorted({int(str(p[0]), 16) for m, p in rpc.calls if m == 'eth_getBlockByNumber'})
    backfill_window = [b for b in backfill_calls if expected_from <= b <= expected_ceiling]
    assert len(backfill_window) <= 25, f'catch-up backfill scanned {len(backfill_window)} blocks (>25)'


def test_catchup_proceeds_incrementally_over_multiple_cycles(monkeypatch):
    """Each cycle must advance the cursor by the hard 25-block ceiling until caught up."""
    monkeypatch.setenv('EVM_RPC_URL', 'http://rpc')
    monkeypatch.setenv('LIVE_MONITORING_CHAINS', 'base')
    monkeypatch.setenv('EVM_CONFIRMATIONS_REQUIRED', str(BASE_CONFIRMATIONS))
    monkeypatch.setenv('MONITOR_REPLAY_BLOCKS', '25')
    monkeypatch.setenv('MAX_BLOCKS_PER_CYCLE', '1000')
    # Cursor-based catch-up is historical-backfill behavior (gated); enable it here.
    monkeypatch.setenv('HISTORICAL_BACKFILL_ENABLED', 'true')

    from services.api.app.evm_activity_provider import fetch_evm_activity

    target = _make_target(cursor_block=CURSOR_BLOCK)

    # Cycle 1
    rpc1 = _BaseRpc(latest=CHAIN_LATEST)
    fetch_evm_activity(target, None, rpc_client=rpc1)
    scan_to_1 = target['_evm_scan_to_block']
    assert scan_to_1 < CHAIN_SAFE_TO, 'Cycle 1: still in catch-up mode'

    # Advance cursor (as runner would)
    target['monitoring_checkpoint_cursor'] = f'{scan_to_1}:checkpoint:-1'

    # Cycle 2
    rpc2 = _BaseRpc(latest=CHAIN_LATEST)
    fetch_evm_activity(target, None, rpc_client=rpc2)
    scan_to_2 = target['_evm_scan_to_block']

    assert scan_to_2 > scan_to_1, 'Cycle 2: cursor must advance beyond cycle 1 ceiling'
    # Hard 25-block ceiling, reorg overlap shrunk to 25//3=8 for progress.
    # from_block_2 = scan_to_1 - 8, ceiling_2 = from_block_2 + 25 - 1 = scan_to_1 + 16.
    expected_from_2 = scan_to_1 - 8
    expected_ceiling_2 = min(expected_from_2 + 25 - 1, CHAIN_SAFE_TO)
    assert scan_to_2 == expected_ceiling_2, (
        f'Cycle 2 ceiling should be {expected_ceiling_2}; got {scan_to_2}'
    )


# ---------------------------------------------------------------------------
# C. Native ETH transfer detection works without eth_getLogs
# ---------------------------------------------------------------------------

def test_native_eth_transfer_detected_without_eth_get_logs(monkeypatch):
    """Native ETH transfers must be detected via eth_getBlockByNumber even when
    eth_getLogs is completely unavailable (returns 400)."""
    monkeypatch.setenv('EVM_RPC_URL', 'http://rpc')
    monkeypatch.setenv('LIVE_MONITORING_CHAINS', 'base')
    monkeypatch.setenv('EVM_CONFIRMATIONS_REQUIRED', str(BASE_CONFIRMATIONS))
    monkeypatch.setenv('MAX_BLOCKS_PER_CYCLE', '1000')
    # Cursor-based catch-up is historical-backfill behavior (gated); enable it here.
    monkeypatch.setenv('HISTORICAL_BACKFILL_ENABLED', 'true')

    from services.api.app.evm_activity_provider import fetch_evm_activity

    # The catch-up window is now hard-capped at 25 blocks: from_block = cursor - 8
    # (overlap shrunk for progress), scan_ceiling = cursor + 16. Put the native tx inside
    # that bounded window (further-ahead blocks are reached over subsequent cycles).
    tx_block = CURSOR_BLOCK + 5
    tx_hash = '0xnativetransfer01'

    target = _make_target(cursor_block=CURSOR_BLOCK)
    rpc = _RpcLogsErrorWithNativeTransfer(tx_block=tx_block, tx_hash=tx_hash)
    events = fetch_evm_activity(target, None, rpc_client=rpc)

    native_events = [
        e for e in events
        if isinstance(e.payload, dict) and e.payload.get('wallet_transfer_direction') == 'outbound'
    ]
    assert native_events, (
        f'Native ETH transfer in block {tx_block} must be detected even when '
        f'eth_getLogs returns 400; got {len(events)} total events'
    )
    ev = native_events[0]
    assert ev.payload.get('tx_hash') == tx_hash
    assert ev.payload.get('block_number') == tx_block


def test_native_eth_transfer_detected_with_no_logs_endpoint(monkeypatch):
    """eth_getLogs failing must not prevent native transfer detection.
    Verifies the two code paths are independent."""
    monkeypatch.setenv('EVM_RPC_URL', 'http://rpc')
    monkeypatch.setenv('LIVE_MONITORING_CHAINS', 'base')
    monkeypatch.setenv('EVM_CONFIRMATIONS_REQUIRED', str(BASE_CONFIRMATIONS))
    monkeypatch.setenv('MONITOR_SAFE_BACKFILL', '300')
    monkeypatch.delenv('MAX_BLOCKS_PER_CYCLE', raising=False)

    from services.api.app.evm_activity_provider import fetch_evm_activity

    # No cursor now starts at the LIVE TAIL (INITIAL_LIVE_TAIL_BLOCKS=10), NOT a 300-block
    # historical backfill (that is disabled by default). Window: [safe_to-9, safe_to].
    safe_to = CHAIN_LATEST - BASE_CONFIRMATIONS
    tx_block = safe_to - 5
    tx_hash = '0xnative02'

    target = _make_target(cursor_block=None)
    rpc = _RpcLogsErrorWithNativeTransfer(tx_block=tx_block, tx_hash=tx_hash)
    events = fetch_evm_activity(target, None, rpc_client=rpc)

    native_events = [
        e for e in events
        if isinstance(e.payload, dict) and e.payload.get('wallet_transfer_direction') is not None
    ]
    assert native_events, (
        f'Native ETH transfer must be detected via block scan even when eth_getLogs raises; '
        f'got {len(events)} total events'
    )


# ---------------------------------------------------------------------------
# D. eth_getLogs 400 logged once per cycle, not per chunk
# ---------------------------------------------------------------------------

def test_eth_get_logs_400_logged_once_not_per_chunk(monkeypatch, caplog):
    """When eth_getLogs returns 400, evm_logs_fetch_failed must appear at most
    once per cycle regardless of how many chunks were attempted."""
    import logging
    monkeypatch.setenv('EVM_RPC_URL', 'http://rpc')
    monkeypatch.setenv('LIVE_MONITORING_CHAINS', 'base')
    monkeypatch.setenv('EVM_CONFIRMATIONS_REQUIRED', str(BASE_CONFIRMATIONS))
    monkeypatch.setenv('MONITOR_SAFE_BACKFILL', '300')
    monkeypatch.setenv('MONITOR_BATCH_BLOCKS', '25')
    monkeypatch.delenv('MAX_BLOCKS_PER_CYCLE', raising=False)

    from services.api.app.evm_activity_provider import fetch_evm_activity

    target = _make_target(cursor_block=None)
    rpc = _RpcLogsError(latest=CHAIN_LATEST)

    with caplog.at_level(logging.WARNING, logger='services.api.app.evm_activity_provider'):
        fetch_evm_activity(target, None, rpc_client=rpc)

    fetch_failed_count = sum(
        1 for r in caplog.records
        if 'evm_logs_fetch_failed' in r.getMessage()
    )
    assert fetch_failed_count <= 1, (
        f'evm_logs_fetch_failed must be logged at most once per cycle; '
        f'got {fetch_failed_count} log entries (one per chunk is log spam)'
    )


def test_eth_get_logs_400_does_not_stop_block_scan(monkeypatch):
    """Block-by-block scanning must continue after eth_getLogs returns 400
    so native ETH transfers are not silently missed."""
    monkeypatch.setenv('EVM_RPC_URL', 'http://rpc')
    monkeypatch.setenv('LIVE_MONITORING_CHAINS', 'base')
    monkeypatch.setenv('EVM_CONFIRMATIONS_REQUIRED', str(BASE_CONFIRMATIONS))
    monkeypatch.setenv('MONITOR_SAFE_BACKFILL', '300')
    monkeypatch.delenv('MAX_BLOCKS_PER_CYCLE', raising=False)

    from services.api.app.evm_activity_provider import fetch_evm_activity

    # No cursor starts at the live tail [safe_to-9, safe_to]; place the native tx there.
    safe_to = CHAIN_LATEST - BASE_CONFIRMATIONS
    tx_block = safe_to - 5
    tx_hash = '0xnative03'

    target = _make_target(cursor_block=None)
    rpc = _RpcLogsErrorWithNativeTransfer(tx_block=tx_block, tx_hash=tx_hash)
    events = fetch_evm_activity(target, None, rpc_client=rpc)

    block_scan_calls = [p for m, p in rpc.calls if m == 'eth_getBlockByNumber']
    assert block_scan_calls, 'eth_getBlockByNumber must still be called after eth_getLogs 400'

    found = any(
        isinstance(e.payload, dict) and e.payload.get('tx_hash') == tx_hash
        for e in events
    )
    assert found, (
        f'Native tx {tx_hash} at block {tx_block} must be found via block scan '
        f'even though eth_getLogs returned 400'
    )
