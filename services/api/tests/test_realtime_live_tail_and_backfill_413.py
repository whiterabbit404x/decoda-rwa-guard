"""Acceptance tests for realtime live-tail detection + 413-safe gap backfill.

These lock in the production fix where realtime kept missing new MetaMask Base ETH
transfers because a large historical gap routed every newHeads to the gap backfill
(which then failed with HTTP 413 / rate limits) and the current head block was never
scanned. The fix separates the two paths:

  A. Live-tail ALWAYS scans the newest confirmed block(s) on every newHeads,
     independent of the gap backfill, tagged detected_by=realtime_websocket.
  B. Native ETH transfers are detected via eth_getBlockByNumber (full transactions),
     never eth_getLogs.
  C. Single-flight + coalescing keeps at most one block scan active per head.
  D. During a provider rate-limit the heartbeat truthfully reports
     realtime_scanning_active=False (stable polling covers the range).
  E. An eth_getLogs 413 in the gap backfill reduces the chunk and continues with the
     native scan instead of looping on the same failing chunk.
"""
from __future__ import annotations

import asyncio
import json as _json
import logging
import time as _time
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


BASE_WALLET = '0x5f6f35fd8b10c5576089f99c7c8c351deb851d1f'
OTHER_WALLET = '0xcafe00000000000000000000000000000000feed'


def _wallet_target(wallet: str = BASE_WALLET) -> dict:
    return {
        'id': 'e785' + uuid.uuid4().hex[:8],
        'workspace_id': str(uuid.uuid4()),
        'name': 'MetaMask Base Wallet',
        'target_type': 'wallet',
        'chain_network': 'base',
        'wallet_address': wallet.lower(),
        'contract_identifier': None,
        'asset_id': str(uuid.uuid4()),
        'monitoring_enabled': True,
        'enabled': True,
        'is_active': True,
        'updated_by_user_id': None,
        'created_by_user_id': None,
        'severity_threshold': None,
    }


def _native_tx(*, tx_hash: str, from_addr: str, to_addr: str, value_wei: int = 10 ** 16) -> dict:
    return {
        'hash': tx_hash,
        'from': from_addr,
        'to': to_addr,
        'value': hex(value_wei),
        'input': '0x',
    }


def _block_with(txs: list[dict], *, number: int) -> dict:
    return {
        'hash': f'0xblock{number:060x}'[:66],
        'number': hex(number),
        'timestamp': hex(1_700_000_000),
        'transactions': txs,
    }


def _make_ingestor(**kw):
    from services.api.app.base_realtime_ingestor import BaseRealtimeIngestor
    return BaseRealtimeIngestor(
        rpc_url='http://rpc', ws_url='ws://ws', watcher_name='base-realtime-worker',
        confirmations_required=1, max_events_per_minute=1000, **kw,
    )


def _drive_newheads(ing, *, heads: list[int]) -> None:
    """Run _ws_subscribe against a fake websocket that delivers the given newHeads
    block numbers in order, then cancels."""
    messages: list[dict] = [
        {'id': 1, 'result': '0xnh'},
        {'id': 2, 'result': '0xlg'},
    ]
    for h in heads:
        messages.append({'params': {'subscription': '0xnh', 'result': {'number': hex(h)}}})
    idx = {'i': 0}

    async def _recv():
        if idx['i'] >= len(messages):
            raise asyncio.CancelledError()
        msg = messages[idx['i']]
        idx['i'] += 1
        return _json.dumps(msg)

    mock_ws = MagicMock()
    mock_ws.send = AsyncMock(return_value=None)
    mock_ws.recv = AsyncMock(side_effect=_recv)
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=mock_ws)
    cm.__aexit__ = AsyncMock(return_value=False)
    fake_ws_module = MagicMock()
    fake_ws_module.connect.return_value = cm

    import sys

    async def _run():
        with patch.dict(sys.modules, {'websockets': fake_ws_module}):
            try:
                await ing._ws_subscribe()
            except asyncio.CancelledError:
                pass

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# A. New MetaMask ETH transfer creates wallet_transfer_detected within 1-2 heads
# ---------------------------------------------------------------------------

def test_new_metamask_eth_transfer_detected_within_two_newheads(monkeypatch):
    """A native ETH transfer TO the monitored wallet, appearing in the newest
    confirmed block, is detected and persisted with detected_by=realtime_websocket
    on the very next newHeads — not minutes later via stable polling."""
    ing = _make_ingestor()
    target = _wallet_target()
    ing.state['last_processed_block'] = 99  # steady state; safe_to=100 scans block 100

    tx = _native_tx(tx_hash='0xmetamask', from_addr=OTHER_WALLET, to_addr=BASE_WALLET)
    block = _block_with([tx], number=100)

    def _rpc(method, params):
        if method == 'eth_getBlockByNumber':
            return block
        return None

    monkeypatch.setattr(ing, '_rpc_call', _rpc)
    monkeypatch.setattr(ing, '_watched_targets', lambda: [target])
    persisted: list = []
    monkeypatch.setattr(
        ing, '_persist_event',
        lambda _t, e: (persisted.append(e), {'status': 'processed', 'event_id': e.event_id})[1],
    )

    _drive_newheads(ing, heads=[101])

    assert len(persisted) == 1, f'transfer must be detected on the newHeads; got {len(persisted)}'
    ev = persisted[0]
    assert ev.payload['detected_by'] == 'realtime_websocket'
    assert ev.payload['source_type'] == 'realtime_websocket'
    assert ev.payload['tx_hash'] == '0xmetamask'
    assert ev.payload['wallet_transfer_direction'] == 'inbound'


def test_live_tail_detects_current_block_despite_large_gap(monkeypatch):
    """Requirement A: after a rate-limit cooldown the checkpoint is far behind head,
    yet the live-tail still scans the NEWEST block and detects the current transfer —
    it does not wait for the gap backfill to crawl up from the old checkpoint."""
    ing = _make_ingestor()
    target = _wallet_target()
    ing.state['last_processed_block'] = 48_140_910          # 455-block gap
    head = 48_141_366
    tx_block = head - ing.confirmations_required             # newest confirmed block

    tx = _native_tx(tx_hash='0xcurrent', from_addr=BASE_WALLET, to_addr=OTHER_WALLET)

    def _rpc(method, params):
        if method == 'eth_getBlockByNumber':
            n = int(params[0], 16)
            return _block_with([tx] if n == tx_block else [], number=n)
        if method == 'eth_getLogs':
            return []
        return None

    monkeypatch.setattr(ing, '_rpc_call', _rpc)
    monkeypatch.setattr(ing, '_watched_targets', lambda: [target])
    monkeypatch.setattr(ing, '_persist_checkpoint', lambda b: None)
    persisted: list = []
    monkeypatch.setattr(
        ing, '_persist_event',
        lambda _t, e: (persisted.append(e), {'status': 'processed', 'event_id': e.event_id})[1],
    )

    _drive_newheads(ing, heads=[head])

    assert any(e.payload['tx_hash'] == '0xcurrent' for e in persisted), (
        'live-tail must detect the current-head transfer even with a large historical gap'
    )
    detected = [e for e in persisted if e.payload['tx_hash'] == '0xcurrent'][0]
    assert detected.payload['detected_by'] == 'realtime_websocket'
    # Lag collapses to ~confirmations: the live-tail advanced the checkpoint to head.
    lag = int(ing.state['last_head_block']) - int(ing.state['last_processed_block'])
    assert 0 <= lag <= 2, f'lag_blocks must return near 0-2 after live-tail; got {lag}'


# ---------------------------------------------------------------------------
# B. Native detection uses eth_getBlockByNumber, NEVER eth_getLogs
# ---------------------------------------------------------------------------

def test_live_tail_native_scan_never_calls_eth_getlogs(monkeypatch):
    ing = _make_ingestor()
    target = _wallet_target()
    ing.state['last_processed_block'] = 99

    tx = _native_tx(tx_hash='0xnolog', from_addr=BASE_WALLET, to_addr=OTHER_WALLET)
    methods: list[str] = []

    def _rpc(method, params):
        methods.append(method)
        if method == 'eth_getBlockByNumber':
            return _block_with([tx], number=100)
        return None

    monkeypatch.setattr(ing, '_rpc_call', _rpc)
    monkeypatch.setattr(ing, '_watched_targets', lambda: [target])
    monkeypatch.setattr(ing, '_persist_event', lambda _t, e: {'status': 'processed', 'event_id': e.event_id})

    asyncio.run(ing._scan_head_native_transfers(101))

    assert 'eth_getBlockByNumber' in methods, 'native scan must use eth_getBlockByNumber'
    assert 'eth_getLogs' not in methods, 'native ETH detection must NOT depend on eth_getLogs'
    # It also must not fall back to eth_blockNumber — the newHeads number is authoritative.
    assert 'eth_blockNumber' not in methods


def test_live_tail_emits_all_required_log_markers(monkeypatch, caplog):
    """Requirement A acceptance log sequence."""
    ing = _make_ingestor()
    target = _wallet_target()
    ing.state['last_processed_block'] = 99
    tx = _native_tx(tx_hash='0xacc', from_addr=BASE_WALLET, to_addr=OTHER_WALLET)
    monkeypatch.setattr(
        ing, '_rpc_call',
        lambda m, p: _block_with([tx], number=100) if m == 'eth_getBlockByNumber' else None,
    )
    monkeypatch.setattr(ing, '_watched_targets', lambda: [target])
    monkeypatch.setattr(ing, '_persist_event', lambda _t, e: {'status': 'processed', 'event_id': e.event_id})

    with caplog.at_level(logging.INFO, logger='services.api.app.base_realtime_ingestor'):
        asyncio.run(ing._scan_head_native_transfers(101))

    msgs = [r.getMessage() for r in caplog.records]
    assert any('realtime_live_tail_scan_started' in m for m in msgs), msgs
    assert any('realtime_live_tail_match' in m for m in msgs), msgs
    assert any('realtime_live_tail_persisted' in m and 'detected_by=realtime_websocket' in m
               for m in msgs), msgs
    assert any('realtime_live_tail_scan_complete' in m for m in msgs), msgs
    assert any('wallet_transfer_detected' in m and 'detected_by=realtime_websocket' in m
               for m in msgs), msgs


# ---------------------------------------------------------------------------
# C. Single-flight + coalescing (max one active block scan at a time)
# ---------------------------------------------------------------------------

def test_handle_new_head_coalesces_and_is_single_flight(monkeypatch):
    """A newHeads arriving while a scan is in flight is coalesced into the current
    single-flight loop — never a second concurrent scan, and heads are processed in
    order without fanning out."""
    ing = _make_ingestor()
    ing.state['last_processed_block'] = 100  # no gap → no backfill noise

    order: list[int] = []
    max_concurrent = {'v': 0, 'cur': 0}

    async def _fake_scan(h):
        max_concurrent['cur'] += 1
        max_concurrent['v'] = max(max_concurrent['v'], max_concurrent['cur'])
        order.append(h)
        if len(order) == 1:
            # Simulate a newHeads arriving mid-scan: must be coalesced, not run now.
            await ing._handle_new_head(h + 1)
            assert order == [h], 'reentrant head must NOT start a nested scan'
        max_concurrent['cur'] -= 1
        return 0

    monkeypatch.setattr(ing, '_scan_head_native_transfers', _fake_scan)

    asyncio.run(ing._handle_new_head(200))

    assert order == [200, 201], f'heads must be processed in order once each; got {order}'
    assert max_concurrent['v'] == 1, 'at most one active block scan at a time'
    assert ing._head_scan_in_flight is False


# ---------------------------------------------------------------------------
# D. Rate-limit does not permanently stop live-tail; heartbeat is truthful
# ---------------------------------------------------------------------------

def test_rate_limit_does_not_permanently_stop_live_tail(monkeypatch):
    """A provider rate-limit pauses realtime, but once the cooldown clears the
    live-tail resumes and detects the current transfer — the pause is temporary."""
    ing = _make_ingestor()
    target = _wallet_target()

    # Trip the rate-limit breaker: realtime scanning is not possible right now.
    ing._enter_provider_rate_limit_cooldown()
    assert ing._rate_limit_cooldown_active() is True

    # Cooldown elapses and the worker resumes.
    ing._rate_limit_cooldown_until = _time.monotonic() - 1.0
    assert ing._rate_limit_cooldown_active() is False
    ing._resume_after_rate_limit_cooldown()
    assert ing._provider_rate_limited is False

    # Live-tail now detects a fresh transfer (proves realtime is not permanently off).
    ing.state['last_processed_block'] = 99
    tx = _native_tx(tx_hash='0xafterrl', from_addr=BASE_WALLET, to_addr=OTHER_WALLET)
    monkeypatch.setattr(
        ing, '_rpc_call',
        lambda m, p: _block_with([tx], number=100) if m == 'eth_getBlockByNumber' else None,
    )
    monkeypatch.setattr(ing, '_watched_targets', lambda: [target])
    persisted: list = []
    monkeypatch.setattr(
        ing, '_persist_event',
        lambda _t, e: (persisted.append(e), {'status': 'processed', 'event_id': e.event_id})[1],
    )

    processed = asyncio.run(ing._scan_head_native_transfers(101))
    assert processed == 1
    assert persisted[0].payload['detected_by'] == 'realtime_websocket'


def test_heartbeat_reports_realtime_scanning_inactive_during_rate_limit(monkeypatch):
    """Requirement D: during a rate-limit cooldown (no fast-tail) the heartbeat must
    report realtime_scanning_active=False so fallback_active=False is never ambiguous.
    After the cooldown clears it flips back to True."""
    ing = _make_ingestor()
    ing.fast_tail_enabled = False
    captured: dict = {}

    def _capture(sql, params=None):
        # Grab the metrics json from the heartbeat upsert.
        if params and isinstance(params, tuple):
            for p in params:
                if isinstance(p, str) and 'realtime_scanning_active' in p:
                    captured.update(_json.loads(p))
        return MagicMock(fetchone=lambda: None, fetchall=lambda: [])

    class _Conn:
        def execute(self, sql, params=None):
            return _capture(sql, params)

        def commit(self):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(
        'services.api.app.base_realtime_ingestor.pg_connection', lambda: _Conn(),
    )
    monkeypatch.setattr(
        'services.api.app.base_realtime_ingestor.ensure_pilot_schema', lambda c: None,
    )

    ing._enter_provider_rate_limit_cooldown()
    ing._record_heartbeat()
    assert captured.get('realtime_scanning_active') is False, (
        'realtime scanning must read inactive during a rate-limit cooldown'
    )
    assert captured.get('rate_limited') is True

    captured.clear()
    ing._rate_limit_cooldown_until = _time.monotonic() - 1.0
    ing._resume_after_rate_limit_cooldown()
    ing._record_heartbeat()
    assert captured.get('realtime_scanning_active') is True, (
        'realtime scanning must read active again once the cooldown clears'
    )


# ---------------------------------------------------------------------------
# E. Gap backfill 413 reduces chunk size and continues (never loops)
# ---------------------------------------------------------------------------

def test_backfill_413_reduces_chunk_and_runs_native_scan(monkeypatch):
    """Requirement E: an eth_getLogs 413 must shrink backfill_chunk_size, still run
    the native scan, advance the checkpoint, and log realtime_backfill_payload_too_large
    — never fail the whole chunk nor loop on the same from_block."""
    from services.api.app import base_realtime_ingestor as mod

    ing = _make_ingestor()
    ing.backfill_chunk_size = 24
    ing.state['last_processed_block'] = 1000
    target = _wallet_target()

    native_scanned: list[int] = []

    def _rpc(method, params):
        if method == 'eth_getLogs':
            raise RuntimeError('rpc_http_error:413 method=eth_getLogs')
        if method == 'eth_getBlockByNumber':
            native_scanned.append(int(params[0], 16))
            return _block_with([], number=int(params[0], 16))
        return None

    monkeypatch.setattr(ing, '_watched_targets', lambda: [target])
    monkeypatch.setattr(ing, '_rpc_call', _rpc)
    monkeypatch.setattr(ing, '_persist_checkpoint', lambda b: None)

    records: list[str] = []

    class _Cap(logging.Handler):
        def emit(self, r):
            records.append(r.getMessage())

    handler = _Cap()
    mod.logger.addHandler(handler)
    mod.logger.setLevel(logging.INFO)
    try:
        processed = asyncio.run(ing._backfill(1001, 5000))
    finally:
        mod.logger.removeHandler(handler)

    # eth_getLogs 413 did NOT stop the native scan (eth_getBlockByNumber ran) ...
    assert native_scanned, 'native scan must run even after eth_getLogs 413'
    assert native_scanned == list(range(1001, 1025)), native_scanned
    # ... the chunk was reduced ...
    assert ing.backfill_chunk_size == 12, f'chunk must halve on 413; got {ing.backfill_chunk_size}'
    # ... the checkpoint advanced (verified native scan) so the chunk is never retried ...
    assert ing.state['last_processed_block'] == 1024
    assert processed == 0
    assert ing.state['metrics'].get('backfill_payload_too_large') == 1
    assert any('realtime_backfill_payload_too_large' in m for m in records), records
    # Crucially, it must NOT emit the old realtime_backfill_scan_failed loop marker.
    assert not any('realtime_backfill_scan_failed' in m for m in records), records


def test_backfill_413_does_not_loop_across_calls(monkeypatch):
    """Across successive backfill calls a persistent 413 must keep advancing the
    from_block (never re-scanning the same failing chunk) and eventually disable the
    log scan once the chunk cannot shrink further — native detection continues."""
    ing = _make_ingestor()
    ing.backfill_chunk_size = 8
    ing.state['last_processed_block'] = 1000
    target = _wallet_target()

    getlogs_calls: list[int] = []
    native_from: list[int] = []

    def _rpc(method, params):
        if method == 'eth_getLogs':
            getlogs_calls.append(int(params[0]['fromBlock'], 16))
            raise RuntimeError('rpc_http_error:413 method=eth_getLogs')
        if method == 'eth_getBlockByNumber':
            n = int(params[0], 16)
            if n not in native_from:
                native_from.append(n)
            return _block_with([], number=n)
        return None

    monkeypatch.setattr(ing, '_watched_targets', lambda: [target])
    monkeypatch.setattr(ing, '_rpc_call', _rpc)
    monkeypatch.setattr(ing, '_persist_checkpoint', lambda b: None)

    starts: list[int] = []
    for _ in range(6):
        last = int(ing.state['last_processed_block'])
        starts.append(last + 1)
        asyncio.run(ing._backfill(last + 1, 999_999))

    # from_block advances every call — the same failing chunk is never retried.
    assert starts == sorted(starts) and len(set(starts)) == len(starts), starts
    # Chunk shrinks 8 -> 4 -> 2 -> 1, then the log scan is disabled (can't shrink more)
    assert ing.backfill_chunk_size == 1
    assert ing._backfill_log_scan_disabled is True
    # Once disabled, eth_getLogs is no longer even attempted (native scan still runs).
    calls_when_disabled = len(getlogs_calls)
    asyncio.run(ing._backfill(int(ing.state['last_processed_block']) + 1, 999_999))
    assert len(getlogs_calls) == calls_when_disabled, 'log scan must stop once disabled'
    assert ing.state['last_processed_block'] > 1000, 'checkpoint keeps advancing (no loop)'


# ---------------------------------------------------------------------------
# F. No duplicate alerts: live-tail native event dedupes with stable polling
# ---------------------------------------------------------------------------

def test_live_tail_event_dedupes_with_stable_polling():
    """A live-tail native event and a later stable-polling event for the same tx
    share the same event_id, so the stable poll is deduped (no duplicate alert)."""
    from services.api.app.evm_activity_provider import _make_event_id
    from services.api.app.monitoring_runner import process_ingested_event

    ing = _make_ingestor()
    target = _wallet_target()
    tx = _native_tx(tx_hash='0xdedupe', from_addr=BASE_WALLET, to_addr=OTHER_WALLET)

    from datetime import datetime, timezone
    ws_event = ing._build_native_transfer_event(
        target, tx, block_number=500, block_hash='0xb', observed_at=datetime.now(timezone.utc),
        direction='outbound', source_type='realtime_websocket',
    )
    # Same idempotency key the 300 s stable polling worker computes for this tx.
    assert ws_event.event_id == _make_event_id(str(target['id']), '500:0xdedupe:-1', 'transaction')

    conn = MagicMock()
    conn.execute.return_value.fetchone.return_value = {'id': 'existing-receipt'}
    conn.__enter__ = lambda s: s
    conn.__exit__ = MagicMock(return_value=False)
    result = process_ingested_event(conn, target=target, event=ws_event, ingestion_mode='live')
    assert result['status'] == 'duplicate_suppressed', (
        'stable polling seeing the same tx must be deduped, not duplicated'
    )
