"""Tests for native ETH transfer detection across realtime backfill and stable polling.

Native ETH transfers (a plain `eth_sendTransaction` with value) emit NO logs, so the
realtime worker's eth_getLogs scan could never see them — they were only caught by the
300 s polling worker minutes later. These tests lock in the fix: the realtime worker now
fetches full block transactions and matches tx.from / tx.to against the watched wallet via
the SAME shared matcher the polling worker uses.

Covers:
  A. Shared native_transfer_direction matcher (outbound / inbound / none, checksum-safe)
  B. Native ETH FROM monitored wallet detected by realtime backfill
  C. Native ETH TO monitored wallet detected by realtime backfill
  D. Realtime + polling produce the SAME idempotency key for one tx (dedupe)
  E. Non-watched wallet tx is ignored by the realtime native scan
  F. Duplicate tx is deduped by the realtime native scan
  G. _backfill integration scans native transactions (detected_by=realtime_backfill)
  H. Stable polling detects native ETH FROM and TO the monitored wallet
  I. UI telemetry page renders detected_by / source_type / full monitored address
"""
from __future__ import annotations

import asyncio
import json as _json
import logging
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


BASE_WALLET = '0xdeadbeefdeadbeefdeadbeefdeadbeefdeadbeef'
OTHER_WALLET = '0xcafe00000000000000000000000000000000feed'


def _wallet_target(wallet: str = BASE_WALLET) -> dict:
    return {
        'id': str(uuid.uuid4()),
        'workspace_id': str(uuid.uuid4()),
        'name': 'Base Wallet',
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


def _native_tx(*, tx_hash: str, from_addr: str, to_addr: str, value_wei: int = 10 ** 15) -> dict:
    return {
        'hash': tx_hash,
        'from': from_addr,
        'to': to_addr,
        'value': hex(value_wei),
        'input': '0x',
        'blockNumber': hex(100),
        'chainId': hex(8453),
    }


def _block_with(txs: list[dict], *, number: int = 100) -> dict:
    return {
        'hash': f'0xblock{number:064x}'[:66],
        'number': hex(number),
        'timestamp': hex(1_700_000_000),
        'transactions': txs,
    }


def _make_ingestor():
    from services.api.app.base_realtime_ingestor import BaseRealtimeIngestor
    return BaseRealtimeIngestor(
        rpc_url='http://rpc', ws_url='ws://ws', watcher_name='test-watcher',
        confirmations_required=1, max_events_per_minute=1000,
    )


# ---------------------------------------------------------------------------
# A. Shared matcher
# ---------------------------------------------------------------------------

def test_native_transfer_direction_outbound_checksum_safe():
    from services.api.app.evm_activity_provider import native_transfer_direction
    # Watched stored lowercase; tx.from checksum-cased — must still match.
    tx = _native_tx(tx_hash='0x1', from_addr=BASE_WALLET.upper().replace('0X', '0x'), to_addr=OTHER_WALLET)
    assert native_transfer_direction(BASE_WALLET, tx) == 'outbound'


def test_native_transfer_direction_inbound_checksum_safe():
    from services.api.app.evm_activity_provider import native_transfer_direction
    tx = _native_tx(tx_hash='0x1', from_addr=OTHER_WALLET, to_addr=BASE_WALLET.upper().replace('0X', '0x'))
    assert native_transfer_direction(BASE_WALLET.lower(), tx) == 'inbound'


def test_native_transfer_direction_none_for_unrelated():
    from services.api.app.evm_activity_provider import native_transfer_direction
    tx = _native_tx(tx_hash='0x1', from_addr='0x1111111111111111111111111111111111111111',
                    to_addr='0x2222222222222222222222222222222222222222')
    assert native_transfer_direction(BASE_WALLET, tx) is None


def test_native_transfer_direction_none_for_contract_creation():
    """tx.to is None for contract creation — must not raise, returns None."""
    from services.api.app.evm_activity_provider import native_transfer_direction
    tx = {'hash': '0x1', 'from': OTHER_WALLET, 'to': None, 'value': hex(0)}
    assert native_transfer_direction(BASE_WALLET, tx) is None


# ---------------------------------------------------------------------------
# B / C. Realtime native scan detects outbound + inbound
# ---------------------------------------------------------------------------

def test_native_eth_outbound_detected_by_realtime_backfill(monkeypatch):
    ing = _make_ingestor()
    target = _wallet_target()
    block = _block_with([_native_tx(tx_hash='0xnativeout', from_addr=BASE_WALLET, to_addr=OTHER_WALLET)])
    monkeypatch.setattr(ing, '_rpc_call', lambda method, params: block if method == 'eth_getBlockByNumber' else None)
    persisted: list = []

    def _persist(_t, e):
        persisted.append(e)
        return {'status': 'processed', 'event_id': e.event_id}

    monkeypatch.setattr(ing, '_persist_event', _persist)

    n = ing._scan_native_transfers(100, 100, [(target, BASE_WALLET)])
    assert n == 1
    ev = persisted[0]
    assert ev.payload['tx_hash'] == '0xnativeout'
    assert ev.payload['wallet_transfer_direction'] == 'outbound'
    assert ev.payload['detected_by'] == 'realtime_backfill'
    assert ev.payload['event_type'] == 'transaction'
    assert ev.payload['from'] == BASE_WALLET.lower()
    assert ev.cursor == '100:0xnativeout:-1'


def test_native_eth_inbound_detected_by_realtime_backfill(monkeypatch):
    ing = _make_ingestor()
    target = _wallet_target()
    # MetaMask sends with a checksum-cased recipient; watched stored lowercase.
    block = _block_with([
        _native_tx(tx_hash='0xnativein', from_addr=OTHER_WALLET, to_addr=BASE_WALLET.upper().replace('0X', '0x')),
    ])
    monkeypatch.setattr(ing, '_rpc_call', lambda method, params: block if method == 'eth_getBlockByNumber' else None)
    persisted: list = []
    monkeypatch.setattr(
        ing, '_persist_event',
        lambda _t, e: (persisted.append(e), {'status': 'processed', 'event_id': e.event_id})[1],
    )
    n = ing._scan_native_transfers(100, 100, [(target, BASE_WALLET.lower())])
    assert n == 1
    assert persisted[0].payload['wallet_transfer_direction'] == 'inbound'
    assert persisted[0].payload['detected_by'] == 'realtime_backfill'


# ---------------------------------------------------------------------------
# D. Realtime + polling converge on one idempotency key per tx
# ---------------------------------------------------------------------------

def test_realtime_and_polling_share_idempotency_key(monkeypatch):
    """The realtime native event and a polling event for the same tx must produce the
    same telemetry idempotency key so ON CONFLICT dedupes them to a single row."""
    from services.api.app.monitoring_runner import _telemetry_idempotency_key
    from services.api.app.evm_activity_provider import ActivityEvent

    ing = _make_ingestor()
    target = _wallet_target()
    tx = _native_tx(tx_hash='0xshared', from_addr=BASE_WALLET, to_addr=OTHER_WALLET)
    from datetime import datetime, timezone
    realtime_event = ing._build_native_transfer_event(
        target, tx, block_number=100, block_hash='0xb', observed_at=datetime.now(timezone.utc),
        direction='outbound', source_type='realtime_backfill',
    )
    # Polling builds the same cursor: block:tx_hash:-1
    polling_event = ActivityEvent(
        event_id='deadbeef', kind='transaction', observed_at=datetime.now(timezone.utc),
        ingestion_source='rpc_polling', cursor='100:0xshared:-1',
        payload={'tx_hash': '0xshared', 'block_number': 100},
    )
    k_realtime = _telemetry_idempotency_key(
        workspace_id=target['workspace_id'], target_id=target['id'], event=realtime_event)
    k_polling = _telemetry_idempotency_key(
        workspace_id=target['workspace_id'], target_id=target['id'], event=polling_event)
    assert k_realtime == k_polling


# ---------------------------------------------------------------------------
# E. Non-watched tx ignored
# ---------------------------------------------------------------------------

def test_non_watched_native_tx_is_ignored(monkeypatch):
    ing = _make_ingestor()
    target = _wallet_target()
    block = _block_with([
        _native_tx(tx_hash='0xunrelated', from_addr='0x1111111111111111111111111111111111111111',
                   to_addr='0x2222222222222222222222222222222222222222'),
    ])
    monkeypatch.setattr(ing, '_rpc_call', lambda method, params: block if method == 'eth_getBlockByNumber' else None)
    persisted: list = []
    monkeypatch.setattr(
        ing, '_persist_event',
        lambda _t, e: (persisted.append(e), {'status': 'processed', 'event_id': e.event_id})[1],
    )
    n = ing._scan_native_transfers(100, 100, [(target, BASE_WALLET)])
    assert n == 0
    assert persisted == []


# ---------------------------------------------------------------------------
# F. Duplicate tx deduped
# ---------------------------------------------------------------------------

def test_duplicate_native_tx_is_deduped(monkeypatch):
    ing = _make_ingestor()
    target = _wallet_target()
    block = _block_with([_native_tx(tx_hash='0xdup', from_addr=BASE_WALLET, to_addr=OTHER_WALLET)])
    monkeypatch.setattr(ing, '_rpc_call', lambda method, params: block if method == 'eth_getBlockByNumber' else None)

    calls = {'n': 0}

    def _persist(_t, e):
        calls['n'] += 1
        # First persist succeeds; the second (same event_id) is suppressed by the receipt dedupe.
        if calls['n'] == 1:
            return {'status': 'processed', 'event_id': e.event_id}
        return {'status': 'duplicate_suppressed', 'event_id': e.event_id}

    monkeypatch.setattr(ing, '_persist_event', _persist)

    first = ing._scan_native_transfers(100, 100, [(target, BASE_WALLET)])
    second = ing._scan_native_transfers(100, 100, [(target, BASE_WALLET)])
    assert first == 1
    assert second == 0  # duplicate_suppressed → not counted again
    assert ing.state['metrics']['events_ingested'] == 1


# ---------------------------------------------------------------------------
# G. _backfill integration runs the native scan
# ---------------------------------------------------------------------------

def test_backfill_scans_native_transactions(monkeypatch):
    ing = _make_ingestor()
    target = _wallet_target()
    block = _block_with([_native_tx(tx_hash='0xbackfillnative', from_addr=BASE_WALLET, to_addr=OTHER_WALLET)])

    def _rpc(method, params):
        if method == 'eth_getLogs':
            return []  # native transfers emit no logs
        if method == 'eth_getBlockByNumber':
            return block
        return None

    monkeypatch.setattr(ing, '_rpc_call', _rpc)
    monkeypatch.setattr(ing, '_watched_targets', lambda: [target])
    monkeypatch.setattr(ing, '_persist_checkpoint', lambda block: None)
    persisted: list = []
    monkeypatch.setattr(
        ing, '_persist_event',
        lambda _t, e: (persisted.append(e), {'status': 'processed', 'event_id': e.event_id})[1],
    )

    processed = asyncio.run(ing._backfill(100, 100))
    assert processed == 1
    assert any(e.payload.get('detected_by') == 'realtime_backfill' for e in persisted)
    assert persisted[0].payload['tx_hash'] == '0xbackfillnative'


# ---------------------------------------------------------------------------
# H. Stable polling detects native ETH (FROM and TO)
# ---------------------------------------------------------------------------

def _run_fetch_for_native(monkeypatch, *, from_addr: str, to_addr: str) -> list:
    from unittest.mock import MagicMock
    monkeypatch.delenv('LIVE_MONITORING_CHAINS', raising=False)
    monkeypatch.delenv('EVM_CHAIN_ID', raising=False)
    monkeypatch.delenv('STAGING_EVM_CHAIN_ID', raising=False)
    monkeypatch.setenv('EVM_RPC_URL', 'https://base-mainnet.example.com/v2/key')
    monkeypatch.delenv('STAGING_EVM_RPC_URL', raising=False)
    monkeypatch.setenv('EVM_CONFIRMATIONS_REQUIRED', '0')
    monkeypatch.setenv('MONITOR_REPLAY_BLOCKS', '1')
    monkeypatch.setenv('MONITOR_BATCH_BLOCKS', '1')
    monkeypatch.setenv('BASE_LIVE_TAIL_BLOCKS', '0')

    target = {
        'id': str(uuid.uuid4()),
        'workspace_id': str(uuid.uuid4()),
        'chain_network': 'base',
        'target_type': 'wallet',
        'wallet_address': BASE_WALLET.lower(),
        'contract_identifier': None,
        'monitoring_checkpoint_cursor': None,
    }
    block = _block_with([_native_tx(tx_hash='0xpollnative', from_addr=from_addr, to_addr=to_addr, value_wei=10 ** 16)])

    mock_client = MagicMock()
    mock_client.call.side_effect = lambda method, params: {
        'eth_chainId': '0x2105',
        'eth_blockNumber': '0xf0000',
        'eth_getBlockByNumber': block,
        'eth_getLogs': [],
    }.get(method)

    from services.api.app.evm_activity_provider import fetch_evm_activity
    return fetch_evm_activity(target, None, rpc_client=mock_client)


def test_stable_polling_detects_native_eth_outbound(monkeypatch):
    events = _run_fetch_for_native(monkeypatch, from_addr=BASE_WALLET.upper().replace('0X', '0x'), to_addr=OTHER_WALLET)
    wallet_events = [e for e in events if e.payload.get('wallet_transfer_direction')]
    assert wallet_events, 'stable polling must detect the native ETH transfer'
    assert wallet_events[0].payload['wallet_transfer_direction'] == 'outbound'
    assert wallet_events[0].payload['detected_by'] == 'stable_rpc_polling'
    assert wallet_events[0].payload['tx_hash'] == '0xpollnative'


def test_stable_polling_detects_native_eth_inbound(monkeypatch):
    events = _run_fetch_for_native(monkeypatch, from_addr=OTHER_WALLET, to_addr=BASE_WALLET.upper().replace('0X', '0x'))
    wallet_events = [e for e in events if e.payload.get('wallet_transfer_direction')]
    assert wallet_events, 'stable polling must detect the inbound native ETH transfer'
    assert wallet_events[0].payload['wallet_transfer_direction'] == 'inbound'
    assert wallet_events[0].payload['detected_by'] == 'stable_rpc_polling'


def test_stable_polling_ignores_unrelated_native_tx(monkeypatch):
    events = _run_fetch_for_native(
        monkeypatch,
        from_addr='0x1111111111111111111111111111111111111111',
        to_addr='0x2222222222222222222222222222222222222222',
    )
    assert [e for e in events if e.payload.get('wallet_transfer_direction')] == []


# ---------------------------------------------------------------------------
# J. Realtime ingest path persists the customer-visible wallet-transfer row
# ---------------------------------------------------------------------------

def _build_realtime_native_event(direction: str, *, from_addr: str, to_addr: str, tx_hash: str = '0xpersist'):
    from datetime import datetime, timezone
    ing = _make_ingestor()
    target = _wallet_target()
    tx = _native_tx(tx_hash=tx_hash, from_addr=from_addr, to_addr=to_addr)
    event = ing._build_native_transfer_event(
        target, tx, block_number=200, block_hash='0xb', observed_at=datetime.now(timezone.utc),
        direction=direction, source_type='realtime_backfill',
    )
    return target, event


def test_realtime_native_event_persists_native_transfer_row(monkeypatch):
    """process_ingested_event's helper must persist a native_transfer telemetry row
    for a directioned native ETH event detected by the realtime worker."""
    from services.api.app import monitoring_runner

    captured: dict = {}

    def _fake_persist_raw(connection, *, telemetry_id, workspace_id, asset_id, target_id,
                          provider_type, event_type, observed_at, evidence_source, payload, idempotency_key):
        captured['event_type'] = event_type
        captured['idempotency_key'] = idempotency_key
        captured['evidence_source'] = evidence_source
        captured['detected_by'] = payload.get('detected_by')
        return True

    monkeypatch.setattr(monitoring_runner, '_persist_raw_wallet_transfer_telemetry', _fake_persist_raw)

    target, event = _build_realtime_native_event('outbound', from_addr=BASE_WALLET, to_addr=OTHER_WALLET)
    result = monitoring_runner._maybe_persist_ingested_wallet_transfer(object(), target=target, event=event)

    assert result == 'native_transfer'
    assert captured['event_type'] == 'native_transfer'
    assert captured['evidence_source'] == 'live'
    assert captured['detected_by'] == 'realtime_backfill'
    # Idempotency key uses the shared cursor so polling dedupes against it.
    assert captured['idempotency_key'].endswith(':200:0xpersist:-1')


def test_realtime_erc20_event_without_direction_is_not_persisted_here(monkeypatch):
    """An event without wallet_transfer_direction (e.g. an ERC-20 log event) must NOT
    be persisted by this helper — that path is unchanged."""
    from services.api.app import monitoring_runner
    from services.api.app.evm_activity_provider import ActivityEvent
    from datetime import datetime, timezone

    called = {'n': 0}
    monkeypatch.setattr(
        monitoring_runner, '_persist_raw_wallet_transfer_telemetry',
        lambda *a, **k: called.__setitem__('n', called['n'] + 1) or True,
    )
    target = _wallet_target()
    erc20_event = ActivityEvent(
        event_id='e', kind='transaction', observed_at=datetime.now(timezone.utc),
        ingestion_source='realtime_websocket', cursor='200:0xerc20:0',
        payload={'tx_hash': '0xerc20', 'from': BASE_WALLET, 'to': OTHER_WALLET, 'block_number': 200},
    )
    result = monitoring_runner._maybe_persist_ingested_wallet_transfer(object(), target=target, event=erc20_event)
    assert result is None
    assert called['n'] == 0


# ---------------------------------------------------------------------------
# I. UI renders detected_by / source_type / full monitored address
# ---------------------------------------------------------------------------

def test_ui_telemetry_page_renders_detected_by_and_source_type():
    src = open(
        'apps/web/app/(product)/monitoring-sources/[targetId]/telemetry/page.tsx',
        encoding='utf-8',
    ).read()
    # Columns / labels the row + detail panel render.
    assert "'Detected By'" in src
    assert "['Source type', row.source_type ?? null]" in src
    assert 'formatDetectedBy' in src
    assert 'realtime_backfill' in src
    # Full monitored address surfaced in the header and the detail panel.
    assert 'monitoredAddressFull' in src
    assert "['Monitored address (full)', monitoredAddressFull]" in src
    assert 'Monitored address:' in src
    assert "['Latency (s)', latencySeconds]" in src


def test_ui_telemetry_page_renders_realtime_active_state():
    """Requirement 5: the Telemetry worker-status strip renders "Active" when the
    canonical realtime_state is 'active' (worker delivering heads), not just the
    binary Enabled/Paused. Prevents the "Realtime WebSocket Paused / Disabled"
    label while the WSS is demonstrably active."""
    src = open(
        'apps/web/app/(product)/monitoring-sources/[targetId]/telemetry/page.tsx',
        encoding='utf-8',
    ).read()
    assert 'realtimeState' in src
    assert 'payload.realtime_state' in src
    assert "realtimeState === 'active'" in src
    assert "'Active'" in src


# ---------------------------------------------------------------------------
# K. New-head realtime_websocket native scan (the production fix)
#
# Native ETH transfers in the most recent blocks were never wide enough to trip
# the gap backfill, so realtime only ever caught them via stable polling minutes
# later. _scan_head_native_transfers scans each newly confirmed head block
# directly, tagged detected_by=realtime_websocket.
# ---------------------------------------------------------------------------

def test_new_head_native_scan_detects_realtime_websocket(monkeypatch):
    """A native ETH transfer in the freshly confirmed head block is detected and
    persisted with detected_by=realtime_websocket, advancing the checkpoint."""
    ing = _make_ingestor()  # confirmations_required=1
    target = _wallet_target()
    block = _block_with(
        [_native_tx(tx_hash='0xheadnative', from_addr=BASE_WALLET, to_addr=OTHER_WALLET)],
        number=100,
    )
    monkeypatch.setattr(
        ing, '_rpc_call',
        lambda method, params: block if method == 'eth_getBlockByNumber' else None,
    )
    monkeypatch.setattr(ing, '_watched_targets', lambda: [target])
    persisted: list = []
    monkeypatch.setattr(
        ing, '_persist_event',
        lambda _t, e: (persisted.append(e), {'status': 'processed', 'event_id': e.event_id})[1],
    )
    ing.state['last_processed_block'] = 99

    # head=101, confirmations=1 → safe_to=100 → scans block 100.
    processed = asyncio.run(ing._scan_head_native_transfers(101))

    assert processed == 1
    ev = persisted[0]
    assert ev.payload['detected_by'] == 'realtime_websocket'
    assert ev.payload['source_type'] == 'realtime_websocket'
    assert ev.payload['tx_hash'] == '0xheadnative'
    assert ev.payload['wallet_transfer_direction'] == 'outbound'
    assert ev.cursor == '100:0xheadnative:-1'
    # Checkpoint advanced to the last scanned (confirmed) block.
    assert ing.state['last_processed_block'] == 100


def test_new_head_native_scan_respects_confirmations(monkeypatch):
    """Only blocks at or below head - confirmations_required are scanned; the
    unconfirmed head block itself is not fetched yet."""
    ing = _make_ingestor()  # confirmations_required=1
    target = _wallet_target()
    requested: list[int] = []

    def _rpc(method, params):
        if method == 'eth_getBlockByNumber':
            n = int(params[0], 16)
            requested.append(n)
            return _block_with([], number=n)
        return None

    monkeypatch.setattr(ing, '_rpc_call', _rpc)
    monkeypatch.setattr(ing, '_watched_targets', lambda: [target])
    monkeypatch.setattr(ing, '_persist_event', lambda _t, e: {'status': 'processed', 'event_id': e.event_id})
    ing.state['last_processed_block'] = 98

    asyncio.run(ing._scan_head_native_transfers(100))  # safe_to = 99

    assert requested == [99], f'must scan only the confirmed block 99, got {requested}'
    assert ing.state['last_processed_block'] == 99


def test_new_head_native_scan_noop_when_caught_up(monkeypatch):
    """When nothing new has been confirmed since the last scan, no block is fetched."""
    ing = _make_ingestor()  # confirmations_required=1
    rpc_calls = {'n': 0}
    monkeypatch.setattr(
        ing, '_rpc_call',
        lambda m, p: rpc_calls.__setitem__('n', rpc_calls['n'] + 1),
    )
    monkeypatch.setattr(ing, '_watched_targets', lambda: [_wallet_target()])
    ing.state['last_processed_block'] = 100

    # head=101 → safe_to=100, from=101 > 100 → nothing to do.
    processed = asyncio.run(ing._scan_head_native_transfers(101))

    assert processed == 0
    assert rpc_calls['n'] == 0


def test_new_head_native_scan_failure_does_not_advance_checkpoint(monkeypatch):
    """An RPC failure mid-scan must NOT advance the checkpoint so the range is
    retried on the next head (no transfers skipped)."""
    ing = _make_ingestor()  # confirmations_required=1
    target = _wallet_target()

    def _rpc(method, params):
        if method == 'eth_getBlockByNumber':
            raise RuntimeError('rpc_http_error:429 method=eth_getBlockByNumber')
        return None

    monkeypatch.setattr(ing, '_rpc_call', _rpc)
    monkeypatch.setattr(ing, '_watched_targets', lambda: [target])
    ing.state['last_processed_block'] = 99

    processed = asyncio.run(ing._scan_head_native_transfers(101))

    assert processed == 0
    assert ing.state['last_processed_block'] == 99  # unchanged


def test_new_head_native_scan_advances_when_no_targets(monkeypatch):
    """With no watched Base wallets, the cursor still advances so an empty range is
    not re-scanned forever, and no block is fetched."""
    ing = _make_ingestor()
    rpc_calls = {'n': 0}
    monkeypatch.setattr(ing, '_rpc_call', lambda m, p: rpc_calls.__setitem__('n', rpc_calls['n'] + 1))
    monkeypatch.setattr(ing, '_watched_targets', lambda: [])
    ing.state['last_processed_block'] = 99

    processed = asyncio.run(ing._scan_head_native_transfers(101))

    assert processed == 0
    assert rpc_calls['n'] == 0
    assert ing.state['last_processed_block'] == 100


def test_realtime_websocket_and_polling_share_idempotency_key():
    """The realtime_websocket native event and a stable polling event for the same
    tx share both the telemetry idempotency key AND the receipt event_id, so the
    later stable-polling row is deduped instead of duplicating the transfer."""
    from datetime import datetime, timezone

    from services.api.app.evm_activity_provider import ActivityEvent, _make_event_id
    from services.api.app.monitoring_runner import _telemetry_idempotency_key

    ing = _make_ingestor()
    target = _wallet_target()
    tx = _native_tx(tx_hash='0xshared2', from_addr=BASE_WALLET, to_addr=OTHER_WALLET)
    ws_event = ing._build_native_transfer_event(
        target, tx, block_number=100, block_hash='0xb', observed_at=datetime.now(timezone.utc),
        direction='outbound', source_type='realtime_websocket',
    )
    polling_event = ActivityEvent(
        event_id='ignored', kind='transaction', observed_at=datetime.now(timezone.utc),
        ingestion_source='rpc_polling', cursor='100:0xshared2:-1',
        payload={'tx_hash': '0xshared2', 'block_number': 100},
    )
    k_ws = _telemetry_idempotency_key(
        workspace_id=target['workspace_id'], target_id=target['id'], event=ws_event)
    k_poll = _telemetry_idempotency_key(
        workspace_id=target['workspace_id'], target_id=target['id'], event=polling_event)
    assert k_ws == k_poll
    # Receipt dedupe (process_ingested_event) keys on event_id: identical too.
    assert ws_event.event_id == _make_event_id(str(target['id']), '100:0xshared2:-1', 'transaction')


def test_native_scan_emits_required_log_names(monkeypatch, caplog):
    """Requirement 9 log names are emitted, each carrying detected_by."""
    ing = _make_ingestor()
    target = _wallet_target()
    block = _block_with([_native_tx(tx_hash='0xloggednative', from_addr=BASE_WALLET, to_addr=OTHER_WALLET)])
    monkeypatch.setattr(
        ing, '_rpc_call',
        lambda method, params: block if method == 'eth_getBlockByNumber' else None,
    )
    monkeypatch.setattr(ing, '_persist_event', lambda _t, e: {'status': 'processed', 'event_id': e.event_id})

    with caplog.at_level(logging.INFO, logger='services.api.app.base_realtime_ingestor'):
        ing._scan_native_transfers(100, 100, [(target, BASE_WALLET)], source_type='realtime_websocket')

    msgs = [r.getMessage() for r in caplog.records]
    assert any('realtime_native_transfer_scan_started' in m for m in msgs), msgs
    assert any('realtime_native_transfer_candidate' in m for m in msgs), msgs
    assert any('realtime_native_transfer_match' in m for m in msgs), msgs
    assert any('realtime_event_persisted' in m for m in msgs), msgs
    # detected_by threaded through the scan logs so an operator can tell the path apart.
    assert any('detected_by=realtime_websocket' in m for m in msgs), msgs


# ---------------------------------------------------------------------------
# L. newHeads dispatch: small lag → realtime_websocket head scan;
#    large lag → realtime_backfill catch-up. Driven through _ws_subscribe.
# ---------------------------------------------------------------------------

def _drive_one_newhead(ing, *, head: int) -> None:
    """Run _ws_subscribe against a fake websocket that delivers a single newHeads
    notification (block ``head``) and then cancels."""
    messages = [
        {'id': 1, 'result': '0xnh'},   # newHeads subscription ack
        {'id': 2, 'result': '0xlg'},   # logs subscription ack
        {'params': {'subscription': '0xnh', 'result': {'number': hex(head)}}},
    ]
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


def test_new_head_in_steady_state_persists_realtime_websocket(monkeypatch):
    """End-to-end through _ws_subscribe: a newHeads message with a small lag scans
    the confirmed block and persists detected_by=realtime_websocket — proving the
    transfer no longer has to wait for Stable RPC Polling."""
    ing = _make_ingestor()  # confirmations_required=1
    target = _wallet_target()
    ing.state['last_processed_block'] = 99  # lag 2 → steady-state head scan

    block = _block_with(
        [_native_tx(tx_hash='0xwsnative', from_addr=BASE_WALLET, to_addr=OTHER_WALLET)],
        number=100,
    )
    monkeypatch.setattr(
        ing, '_rpc_call',
        lambda method, params: block if method == 'eth_getBlockByNumber' else None,
    )
    monkeypatch.setattr(ing, '_watched_targets', lambda: [target])
    persisted: list = []
    monkeypatch.setattr(
        ing, '_persist_event',
        lambda _t, e: (persisted.append(e), {'status': 'processed', 'event_id': e.event_id})[1],
    )

    _drive_one_newhead(ing, head=101)

    assert len(persisted) == 1, f'expected 1 realtime_websocket native event, got {len(persisted)}'
    assert persisted[0].payload['detected_by'] == 'realtime_websocket'
    assert persisted[0].payload['tx_hash'] == '0xwsnative'
    assert ing.state['last_processed_block'] == 100


def test_new_head_with_large_gap_runs_live_tail_and_backfill(monkeypatch):
    """Requirement A: a large lag runs BOTH the live-tail head scan (current blocks,
    detected_by=realtime_websocket) AND the separate bounded gap backfill. The two
    are no longer mutually exclusive — a failing/paused backfill must never block
    detection of the current head block."""
    ing = _make_ingestor()
    monkeypatch.setattr(ing, '_watched_targets', lambda: [_wallet_target()])
    ing.state['last_processed_block'] = 10  # head 101 → lag 91 > gap_threshold (24)

    backfill_calls: list[tuple[int, int]] = []
    head_scan_calls: list[int] = []

    async def _bf(a, b):
        backfill_calls.append((a, b))
        return 0

    async def _hs(h):
        head_scan_calls.append(h)
        return 0

    monkeypatch.setattr(ing, '_backfill', _bf)
    monkeypatch.setattr(ing, '_scan_head_native_transfers', _hs)

    _drive_one_newhead(ing, head=101)

    # Live-tail ALWAYS runs on the current head (requirement A) ...
    assert head_scan_calls == [101], head_scan_calls
    # ... and the deep-gap backfill runs separately, sized from the pre-scan checkpoint.
    assert backfill_calls == [(11, 101)], backfill_calls


def test_failed_backfill_does_not_block_live_tail(monkeypatch):
    """Requirement A: even when the gap backfill raises, the live-tail head scan for
    the current block still runs and persists — a failing backfill can never block
    realtime detection of current blocks."""
    ing = _make_ingestor()
    monkeypatch.setattr(ing, '_watched_targets', lambda: [_wallet_target()])
    ing.state['last_processed_block'] = 10  # large gap → backfill path is exercised

    head_scan_calls: list[int] = []

    async def _hs(h):
        head_scan_calls.append(h)
        return 0

    async def _boom(a, b):
        raise RuntimeError('backfill exploded (413/429/whatever)')

    # Live-tail runs first; the backfill blows up afterwards. The exception must not
    # prevent the live-tail scan that already ran, and must not crash the ws loop.
    monkeypatch.setattr(ing, '_scan_head_native_transfers', _hs)
    monkeypatch.setattr(ing, '_backfill', _boom)

    _drive_one_newhead(ing, head=101)

    assert head_scan_calls == [101], (
        f'live-tail must run even when backfill fails; got {head_scan_calls}'
    )


# ---------------------------------------------------------------------------
# M. Stale system-health text: 'provider closes before first event' must not
#    persist once the worker is receiving heads (requirement 10).
# ---------------------------------------------------------------------------

def test_effective_degraded_reason_keeps_text_before_first_event():
    ing = _make_ingestor()
    ing.state['degraded_reason'] = 'provider_closes_before_first_event'
    ing.state['metrics']['heads_received'] = 0
    ing.state['metrics']['events_ingested'] = 0
    # No head/event yet → the reason is still truthful and is kept.
    assert ing._effective_degraded_reason() == 'provider_closes_before_first_event'


def test_effective_degraded_reason_cleared_once_heads_flow_on_wss():
    ing = _make_ingestor()
    ing.state['degraded_reason'] = 'provider_closes_before_first_event'
    ing.state['metrics']['heads_received'] = 5
    ing._ingestion_mode = 'realtime'
    assert ing._effective_degraded_reason() is None
    assert ing.state['degraded_reason'] is None


def test_effective_degraded_reason_http_fast_tail_when_heads_flow():
    ing = _make_ingestor()
    ing.state['degraded_reason'] = 'provider_closes_before_first_event'
    ing.state['metrics']['heads_received'] = 3
    ing._ingestion_mode = 'http_fast_tail'
    # Still a fallback mode, but heads are flowing — reason is accurate, not stale.
    assert ing._effective_degraded_reason() == 'http_fast_tail_active'
    assert ing.state['degraded_reason'] == 'http_fast_tail_active'


# ---------------------------------------------------------------------------
# N. Scan-complete observability + enriched match/persisted logs + payload
#    address fields. These are the production-incident diagnostics: a
#    "scan_started" line with no follow-up must now always be explained by a
#    "scan_complete" line carrying blocks_scanned / txs_seen / matches.
# ---------------------------------------------------------------------------

def test_native_scan_emits_scan_complete_with_counts(monkeypatch, caplog):
    """A matched scan emits realtime_native_transfer_scan_complete with
    blocks_scanned / txs_seen / matches so an operator can see the scan ran."""
    ing = _make_ingestor()
    target = _wallet_target()
    block = _block_with(
        [_native_tx(tx_hash='0xcomplete', from_addr=BASE_WALLET, to_addr=OTHER_WALLET)],
        number=100,
    )
    monkeypatch.setattr(
        ing, '_rpc_call',
        lambda method, params: block if method == 'eth_getBlockByNumber' else None,
    )
    monkeypatch.setattr(ing, '_persist_event', lambda _t, e: {'status': 'processed', 'event_id': e.event_id})

    with caplog.at_level(logging.INFO, logger='services.api.app.base_realtime_ingestor'):
        ing._scan_native_transfers(100, 100, [(target, BASE_WALLET)], source_type='realtime_websocket')

    complete = [m for m in (r.getMessage() for r in caplog.records)
                if 'realtime_native_transfer_scan_complete' in m]
    assert complete, 'scan_complete line must be emitted'
    line = complete[0]
    assert 'from_block=100' in line and 'to_block=100' in line
    assert 'blocks_scanned=1' in line
    assert 'txs_seen=1' in line
    assert 'watched_targets=1' in line
    assert 'matches=1' in line
    assert 'detected_by=realtime_websocket' in line


def test_native_scan_complete_reports_zero_txs_for_hash_only_block(monkeypatch, caplog):
    """The root cause the scan_complete line must expose: a hash-only block (the
    shape eth_getBlockByNumber returns WITHOUT full=True) yields str entries, so
    txs_seen=0 / matches=0 even though a block was scanned. This is exactly why a
    native ETH send can be invisible while scan_started still logs."""
    ing = _make_ingestor()
    target = _wallet_target()
    # transactions as bare hash strings — no full transaction objects.
    hash_only_block = {
        'hash': '0xabc', 'number': hex(100), 'timestamp': hex(1_700_000_000),
        'transactions': ['0xhash1', '0xhash2'],
    }
    monkeypatch.setattr(
        ing, '_rpc_call',
        lambda method, params: hash_only_block if method == 'eth_getBlockByNumber' else None,
    )
    persisted: list = []
    monkeypatch.setattr(
        ing, '_persist_event',
        lambda _t, e: (persisted.append(e), {'status': 'processed', 'event_id': e.event_id})[1],
    )

    with caplog.at_level(logging.INFO, logger='services.api.app.base_realtime_ingestor'):
        n = ing._scan_native_transfers(100, 100, [(target, BASE_WALLET)], source_type='realtime_websocket')

    assert n == 0
    assert persisted == []
    line = next(m for m in (r.getMessage() for r in caplog.records)
                if 'realtime_native_transfer_scan_complete' in m)
    assert 'blocks_scanned=1' in line
    assert 'txs_seen=0' in line
    assert 'matches=0' in line


def test_native_transfer_match_log_includes_from_to_value_block(monkeypatch, caplog):
    """realtime_native_transfer_match carries from / to / value / block_number so the
    matched transfer is fully described in one line (requirement 3)."""
    ing = _make_ingestor()
    target = _wallet_target()
    block = _block_with(
        [_native_tx(tx_hash='0xmatchlog', from_addr=BASE_WALLET, to_addr=OTHER_WALLET, value_wei=10 ** 15)],
        number=100,
    )
    monkeypatch.setattr(
        ing, '_rpc_call',
        lambda method, params: block if method == 'eth_getBlockByNumber' else None,
    )
    monkeypatch.setattr(ing, '_persist_event', lambda _t, e: {'status': 'processed', 'event_id': e.event_id})

    with caplog.at_level(logging.INFO, logger='services.api.app.base_realtime_ingestor'):
        ing._scan_native_transfers(100, 100, [(target, BASE_WALLET)], source_type='realtime_websocket')

    match = next(m for m in (r.getMessage() for r in caplog.records)
                 if 'realtime_native_transfer_match' in m)
    assert f'from={BASE_WALLET.lower()}' in match
    assert f'to={OTHER_WALLET.lower()}' in match
    assert f'value={10 ** 15}' in match
    assert 'block_number=100' in match
    assert 'detected_by=realtime_websocket' in match


def test_realtime_event_persisted_log_has_event_type_and_source(monkeypatch, caplog):
    """realtime_event_persisted names the customer-facing event class and the
    detected_by / source_type tag (requirement 3)."""
    ing = _make_ingestor()
    target = _wallet_target()
    block = _block_with(
        [_native_tx(tx_hash='0xpersistlog', from_addr=BASE_WALLET, to_addr=OTHER_WALLET)],
        number=100,
    )
    monkeypatch.setattr(
        ing, '_rpc_call',
        lambda method, params: block if method == 'eth_getBlockByNumber' else None,
    )
    monkeypatch.setattr(ing, '_persist_event', lambda _t, e: {'status': 'processed', 'event_id': e.event_id})

    with caplog.at_level(logging.INFO, logger='services.api.app.base_realtime_ingestor'):
        ing._scan_native_transfers(100, 100, [(target, BASE_WALLET)], source_type='realtime_websocket')

    persisted = next(m for m in (r.getMessage() for r in caplog.records)
                     if 'realtime_event_persisted' in m)
    assert 'event_type=wallet_transfer_detected' in persisted
    assert 'detected_by=realtime_websocket' in persisted
    assert 'source_type=realtime_websocket' in persisted
    assert 'tx_hash=0xpersistlog' in persisted


def test_native_transfer_event_payload_has_from_and_to_address():
    """The persisted native-transfer payload carries explicit from_address / to_address
    aliases (requirement 4) matching the normalised from / to."""
    from datetime import datetime, timezone
    ing = _make_ingestor()
    target = _wallet_target()
    tx = _native_tx(tx_hash='0xaddr', from_addr=BASE_WALLET.upper().replace('0X', '0x'), to_addr=OTHER_WALLET)
    event = ing._build_native_transfer_event(
        target, tx, block_number=100, block_hash='0xb', observed_at=datetime.now(timezone.utc),
        direction='outbound', source_type='realtime_websocket',
    )
    p = event.payload
    assert p['from_address'] == BASE_WALLET.lower()
    assert p['to_address'] == OTHER_WALLET.lower()
    # from_address / to_address stay consistent with the existing from / to fields.
    assert p['from_address'] == p['from']
    assert p['to_address'] == p['to']
    # Canonical realtime evidence tags remain intact.
    assert p['detected_by'] == 'realtime_websocket'
    assert p['source_type'] == 'realtime_websocket'
    assert p['evidence_source'] == 'live'
    assert p['chain_id'] == 8453


def test_ui_telemetry_page_renders_realtime_websocket_label():
    """Requirement 7: the Telemetry view maps detected_by=realtime_websocket to the
    human label 'Realtime WebSocket' so a customer sees the transfer was caught by
    the realtime socket, not stable polling."""
    src = open(
        'apps/web/app/(product)/monitoring-sources/[targetId]/telemetry/page.tsx',
        encoding='utf-8',
    ).read()
    assert "realtime_websocket: 'Realtime WebSocket'" in src
    # from_address / to_address are read by the row classifier so the aliased payload
    # fields are surfaced, not ignored.
    assert "'from', 'from_address'" in src
    assert "'to', 'to_address'" in src


# ---------------------------------------------------------------------------
# O. tx-hash debug mode + below-checkpoint bounded backfill (requirements 1-2)
#
# Realtime is scanning (txs_seen > 0) but not matching (matches=0) because the
# monitored wallet's tx sits in a block BELOW the realtime checkpoint, which the
# forward head scan never re-visits. These tests lock in the operator-triggered
# tx-hash debug (eth_getTransactionByHash → realtime_tx_debug) and the bounded
# ±2-block backfill that closes exactly that gap without moving the live cursor.
# ---------------------------------------------------------------------------

def test_debug_tx_env_parser_filters_bogus_and_dedupes(monkeypatch):
    from services.api.app.base_realtime_ingestor import _resolve_tx_hash_list_env
    good = '0x' + 'a' * 64
    other = '0x' + 'b' * 64
    # Mixed case + duplicates + junk + wrong-length + non-hex — only valid 0x hashes,
    # lowercased and deduped in first-seen order, survive.
    monkeypatch.setenv(
        'BASE_REALTIME_DEBUG_TX_HASHES',
        f'{good.upper()}, not-a-hash 0x123 {good} {other} 0xZZ{"z" * 62}',
    )
    assert _resolve_tx_hash_list_env('BASE_REALTIME_DEBUG_TX_HASHES') == [good, other]


def test_debug_tx_env_parser_empty_is_empty(monkeypatch):
    from services.api.app.base_realtime_ingestor import _resolve_tx_hash_list_env
    monkeypatch.delenv('BASE_REALTIME_DEBUG_TX_HASHES', raising=False)
    assert _resolve_tx_hash_list_env('BASE_REALTIME_DEBUG_TX_HASHES') == []


def test_debug_tx_match_reports_from_to_value_block(monkeypatch, caplog):
    """Requirement 1 + 6: eth_getTransactionByHash → realtime_tx_debug carrying the
    exact from / to / value / block_number / chain_id and per-target match flags."""
    ing = _make_ingestor()
    target = _wallet_target()
    # MetaMask sends with a checksum-cased from; watched stored lowercase — must match.
    tx = _native_tx(
        tx_hash='0xdbg',
        from_addr=BASE_WALLET.upper().replace('0X', '0x'),
        to_addr=OTHER_WALLET,
        value_wei=10 ** 17,
    )  # blockNumber = 100
    # Checkpoint below the tx block → forward scan would reach it → no skip/backfill,
    # isolating the requirement-1 diagnostic.
    ing.state['last_processed_block'] = 50
    monkeypatch.setattr(
        ing, '_rpc_call',
        lambda method, params: tx if method == 'eth_getTransactionByHash' else None,
    )

    with caplog.at_level(logging.INFO, logger='services.api.app.base_realtime_ingestor'):
        result = ing._debug_tx_match('0xdbg', [(target, BASE_WALLET.lower())])

    assert result['found'] is True
    assert result['block_number'] == 100
    assert result['from'] == BASE_WALLET.lower()
    assert result['to'] == OTHER_WALLET.lower()
    assert result['value_wei'] == 10 ** 17
    assert result['chain_id'] == 8453
    assert result['matched_target_count'] == 1
    assert result['skipped_by_checkpoint'] is False
    assert result['backfill_triggered'] is False

    dbg = next(m for m in (r.getMessage() for r in caplog.records) if 'realtime_tx_debug ' in m)
    assert 'block_number=100' in dbg
    assert f'value={10 ** 17}' in dbg
    assert 'chain_id=8453' in dbg
    assert 'from_matches=True' in dbg
    assert 'to_matches=False' in dbg
    assert f'normalized_from={BASE_WALLET.lower()}' in dbg
    assert f'normalized_to={OTHER_WALLET.lower()}' in dbg
    assert f'normalized_target={BASE_WALLET.lower()}' in dbg


def test_debug_tx_match_reports_not_found(monkeypatch, caplog):
    """A tx hash the provider does not know yields found=False and never backfills."""
    ing = _make_ingestor()
    target = _wallet_target()
    monkeypatch.setattr(ing, '_rpc_call', lambda method, params: None)
    with caplog.at_level(logging.WARNING, logger='services.api.app.base_realtime_ingestor'):
        result = ing._debug_tx_match('0xmissing', [(target, BASE_WALLET.lower())])
    assert result == {'tx_hash': '0xmissing', 'found': False}
    assert any('found=False' in r.getMessage() for r in caplog.records)


def test_debug_tx_below_checkpoint_triggers_bounded_backfill(monkeypatch, caplog):
    """Requirement 2 + 6: a tx in a block BELOW the checkpoint logs
    realtime_tx_skipped_by_checkpoint and runs a bounded tx_block-2..tx_block+2
    native scan (detected_by=realtime_websocket) WITHOUT advancing the checkpoint."""
    ing = _make_ingestor()
    target = _wallet_target()
    tx = _native_tx(tx_hash='0xold', from_addr=BASE_WALLET, to_addr=OTHER_WALLET)  # block 100
    block_100 = _block_with(
        [_native_tx(tx_hash='0xold', from_addr=BASE_WALLET, to_addr=OTHER_WALLET)], number=100,
    )
    # Checkpoint AHEAD of the tx block → the live forward head scan never reaches it.
    ing.state['last_processed_block'] = 500

    scanned_blocks: list[int] = []

    def _rpc(method, params):
        if method == 'eth_getTransactionByHash':
            return tx
        if method == 'eth_getBlockByNumber':
            num = int(params[0], 16)
            scanned_blocks.append(num)
            return block_100 if num == 100 else _block_with([], number=num)
        return None

    monkeypatch.setattr(ing, '_rpc_call', _rpc)
    persisted: list = []
    monkeypatch.setattr(
        ing, '_persist_event',
        lambda _t, e: (persisted.append(e), {'status': 'processed', 'event_id': e.event_id})[1],
    )

    with caplog.at_level(logging.INFO, logger='services.api.app.base_realtime_ingestor'):
        result = ing._debug_tx_match('0xold', [(target, BASE_WALLET.lower())])

    assert result['skipped_by_checkpoint'] is True
    assert result['backfill_triggered'] is True
    assert result['backfill_from_block'] == 98
    assert result['backfill_to_block'] == 102
    # The bounded backfill scanned exactly the ±2 window around the tx block.
    assert scanned_blocks == [98, 99, 100, 101, 102]
    # Forward cursor MUST NOT move for an old-block backfill.
    assert ing.state['last_processed_block'] == 500
    # The previously-missed transfer is now persisted, tagged realtime_backfill — it is
    # a recovery scan of an OLD block, not a live WebSocket detection.
    assert len(persisted) == 1
    assert persisted[0].payload['tx_hash'] == '0xold'
    assert persisted[0].payload['detected_by'] == 'realtime_backfill'

    skip = next(m for m in (r.getMessage() for r in caplog.records)
                if 'realtime_tx_skipped_by_checkpoint' in m)
    assert 'tx_block=100' in skip
    assert 'checkpoint_block=500' in skip
    # The bounded backfill announces itself with the canonical marker.
    assert any('realtime_bounded_backfill_started' in m for m in (r.getMessage() for r in caplog.records))


def test_debug_tx_above_checkpoint_does_not_backfill(monkeypatch):
    """A tx above the checkpoint is still reachable by the forward head scan, so no
    skipped-by-checkpoint backfill fires (avoids redundant scans)."""
    ing = _make_ingestor()
    target = _wallet_target()
    tx = _native_tx(tx_hash='0xfresh', from_addr=BASE_WALLET, to_addr=OTHER_WALLET)  # block 100
    ing.state['last_processed_block'] = 99  # tx block 100 > checkpoint → forward scan reaches it

    block_calls = {'n': 0}

    def _rpc(method, params):
        if method == 'eth_getTransactionByHash':
            return tx
        if method == 'eth_getBlockByNumber':
            block_calls['n'] += 1
            return _block_with([], number=int(params[0], 16))
        return None

    monkeypatch.setattr(ing, '_rpc_call', _rpc)
    result = ing._debug_tx_match('0xfresh', [(target, BASE_WALLET.lower())])
    assert result['skipped_by_checkpoint'] is False
    assert result['backfill_triggered'] is False
    assert block_calls['n'] == 0  # no bounded backfill scan issued


def test_run_configured_tx_debug_inert_without_env(monkeypatch):
    """With BASE_REALTIME_DEBUG_TX_HASHES unset the debug path is fully inert — it
    never even loads targets, so normal operation is unaffected."""
    monkeypatch.delenv('BASE_REALTIME_DEBUG_TX_HASHES', raising=False)
    ing = _make_ingestor()
    loaded = {'n': 0}
    monkeypatch.setattr(ing, '_watched_wallet_pairs', lambda *a, **k: (loaded.__setitem__('n', loaded['n'] + 1), [])[1])
    ing._run_configured_tx_debug()
    assert loaded['n'] == 0


def test_run_configured_tx_debug_runs_once(monkeypatch):
    """When the env var is set the debug runs for each hash exactly once, even if
    invoked again on a WSS reconnect (guarded by _tx_debug_completed)."""
    monkeypatch.setenv('BASE_REALTIME_DEBUG_TX_HASHES', '0x' + 'c' * 64)
    ing = _make_ingestor()
    target = _wallet_target()
    monkeypatch.setattr(ing, '_watched_wallet_pairs', lambda *a, **k: [(target, BASE_WALLET.lower())])
    calls: list[str] = []
    monkeypatch.setattr(ing, '_debug_tx_match', lambda h, w, **k: calls.append(h))
    ing._run_configured_tx_debug()
    ing._run_configured_tx_debug()  # reconnect — must not re-run
    assert calls == ['0x' + 'c' * 64]


# ---------------------------------------------------------------------------
# P. realtime_websocket detections surface in the Target Telemetry API
#    (requirement 5 + 6). A native_transfer row tagged detected_by=realtime_websocket
#    must be returned by list_target_telemetry under both "All" and "Wallet transfers".
# ---------------------------------------------------------------------------

class _TelemetryRows:
    def __init__(self, rows):
        self._rows = list(rows or [])

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)


def _run_list_target_telemetry_with_row(monkeypatch, telemetry_row, *, event_type_filter=None):
    from datetime import datetime, timezone
    from services.api.app import monitoring_runner

    workspace_id = telemetry_row['workspace_id']
    target_id = telemetry_row['target_id']
    captured: list[str] = []

    class _MockConn:
        def execute(self, query, params=None):
            captured.append(query or '')
            q = (query or '').lower()
            if 'from targets' in q and 'wallet_address' in q:
                return _TelemetryRows([{
                    'wallet_address': BASE_WALLET.lower(), 'contract_identifier': None,
                    'target_metadata': None, 'chain_network': 'base', 'target_type': 'wallet',
                }])
            if 'monitoring_watcher_state' in q:
                return _TelemetryRows([])
            if 'count(*)' in q and 'telemetry_events' in q:
                return _TelemetryRows([{'cnt': 1}])
            if 'telemetry_events' in q and 'filter' in q:
                return _TelemetryRows([{'last_stable_poll_at': None,
                                        'last_realtime_event_at': datetime.now(timezone.utc)}])
            if 'telemetry_events' in q and 'source_type' in q:
                return _TelemetryRows([telemetry_row])
            return _TelemetryRows([])

    fake_user = {'id': str(uuid.uuid4()), 'workspace_id': workspace_id}
    fake_workspace = {'workspace_id': workspace_id, 'workspace': {'id': workspace_id}}
    fake_request = MagicMock()
    fake_request.headers = {'x-workspace-id': workspace_id}

    with (
        patch('services.api.app.monitoring_runner.pg_connection') as mock_pg,
        patch('services.api.app.monitoring_runner.ensure_pilot_schema'),
        patch('services.api.app.monitoring_runner.authenticate_with_connection', return_value=fake_user),
        patch('services.api.app.monitoring_runner.resolve_workspace', return_value=fake_workspace),
    ):
        mock_pg.return_value.__enter__ = lambda s: _MockConn()
        mock_pg.return_value.__exit__ = MagicMock(return_value=False)
        result = monitoring_runner.list_target_telemetry(
            fake_request, target_id=target_id, limit=50, event_type_filter=event_type_filter,
        )
    return result, captured


def _realtime_native_row():
    from datetime import datetime, timezone
    target_id = str(uuid.uuid4())
    workspace_id = str(uuid.uuid4())
    payload_json = {
        'tx_hash': '0xrt', 'from': BASE_WALLET.lower(), 'to': OTHER_WALLET.lower(),
        'block_number': 100, 'chain_id': 8453,
        'detected_by': 'realtime_websocket', 'source_type': 'realtime_websocket',
        'provider_mode': 'realtime_websocket', 'wallet_transfer_direction': 'outbound',
    }
    return {
        'id': str(uuid.uuid4()), 'workspace_id': workspace_id, 'target_id': target_id,
        'provider_type': 'realtime_websocket',
        'source_type': 'native_transfer',  # te.event_type AS source_type
        'evidence_source': 'live',
        'observed_at': datetime.now(timezone.utc), 'ingested_at': datetime.now(timezone.utc),
        'payload_json': payload_json, 'chain_network': 'base', 'receipt_block_number': 100,
    }


def test_list_target_telemetry_returns_realtime_websocket_row_all(monkeypatch):
    """A native_transfer row tagged detected_by=realtime_websocket is returned under
    the default 'All' view with detected_by / provider_mode preserved for the UI."""
    row = _realtime_native_row()
    result, _ = _run_list_target_telemetry_with_row(monkeypatch, row)
    assert result['telemetry'], 'realtime_websocket row must be returned'
    item = result['telemetry'][0]
    assert item['source_type'] == 'native_transfer'
    assert item['detected_by'] == 'realtime_websocket'
    assert item['provider_mode'] == 'realtime_websocket'
    assert item['block_number'] == 100
    # Detection-path freshness classifies it as a realtime (not stable) event.
    assert result['last_realtime_event_at'] is not None
    assert result['last_stable_poll_at'] is None


def test_list_target_telemetry_wallet_transfers_filter_includes_native_transfer(monkeypatch):
    """The 'Wallet transfers' quick filter maps to an event_type IN clause that
    includes native_transfer, so realtime native ETH detections are not filtered out."""
    row = _realtime_native_row()
    result, captured = _run_list_target_telemetry_with_row(
        monkeypatch, row, event_type_filter='wallet_transfers',
    )
    assert result['telemetry'], 'wallet_transfers filter must still return the realtime row'
    assert result['telemetry'][0]['detected_by'] == 'realtime_websocket'
    # The SQL applied for this filter must whitelist native_transfer explicitly.
    assert any("'native_transfer'" in q and 'wallet_transfer_detected' in q for q in captured), (
        'wallet_transfers filter SQL must include native_transfer'
    )


# ---------------------------------------------------------------------------
# Q. Requirement 1 completion: receipt status + realtime scan-window context
#    (checkpoint_block / scan_start_block / was_block_scanned) in BOTH the
#    env-var _debug_tx_match path and the read-only /ops/monitoring/diagnose-tx
#    endpoint. This is the smoking-gun diagnostic for the production incident:
#    a MetaMask ETH transfer whose block sits FAR below the realtime cold-start
#    checkpoint (tx block 47_373_543 vs cold start 48_094_524) was never
#    forward-scanned, so realtime structurally could not catch it — recover via
#    import-tx. was_block_scanned=False makes that unambiguous.
# ---------------------------------------------------------------------------

def test_debug_tx_match_reports_receipt_status_and_scan_window(monkeypatch, caplog):
    """_debug_tx_match fetches the receipt (status) and reports the scan window:
    a tx block inside a range the worker actually native-scanned is
    was_block_scanned=True. (Coverage is span-recorded, never inferred from
    [scan_start_block, checkpoint] — that inference over-claimed across
    rate-limit cooldown gaps.)"""
    ing = _make_ingestor()
    target = _wallet_target()
    tx = _native_tx(tx_hash='0xscanned', from_addr=BASE_WALLET, to_addr=OTHER_WALLET)  # block 100

    def _rpc(method, params):
        if method == 'eth_getTransactionByHash':
            return tx
        if method == 'eth_getTransactionReceipt':
            return {'status': '0x1'}
        return None

    monkeypatch.setattr(ing, '_rpc_call', _rpc)
    ing.state['scan_start_block'] = 90
    ing.state['last_processed_block'] = 120
    ing._note_scanned_range(90, 120)  # worker actually scanned 90..120 → covers block 100

    with caplog.at_level(logging.INFO, logger='services.api.app.base_realtime_ingestor'):
        result = ing._debug_tx_match('0xscanned', [(target, BASE_WALLET.lower())], run_backfill=False)

    assert result['status'] == 1
    assert result['checkpoint_block'] == 120
    assert result['scan_start_block'] == 90
    assert result['was_block_scanned'] is True

    dbg = next(m for m in (r.getMessage() for r in caplog.records) if 'realtime_tx_debug ' in m)
    assert 'status=1' in dbg
    assert 'checkpoint_block=120' in dbg
    assert 'scan_start_block=90' in dbg
    assert 'was_block_scanned=True' in dbg


def test_debug_tx_match_below_scan_start_is_not_scanned(monkeypatch):
    """The production case: a tx block BELOW the cold-start floor was never
    forward-scanned (was_block_scanned=False) even though it is below the checkpoint,
    and the bounded recovery backfill still runs."""
    ing = _make_ingestor()
    target = _wallet_target()
    tx = _native_tx(tx_hash='0xcold', from_addr=BASE_WALLET, to_addr=OTHER_WALLET)  # block 100
    block_100 = _block_with(
        [_native_tx(tx_hash='0xcold', from_addr=BASE_WALLET, to_addr=OTHER_WALLET)], number=100,
    )

    def _rpc(method, params):
        if method == 'eth_getTransactionByHash':
            return tx
        if method == 'eth_getTransactionReceipt':
            return {'status': '0x1'}
        if method == 'eth_getBlockByNumber':
            num = int(params[0], 16)
            return block_100 if num == 100 else _block_with([], number=num)
        return None

    monkeypatch.setattr(ing, '_rpc_call', _rpc)
    monkeypatch.setattr(ing, '_persist_event', lambda _t, e: {'status': 'processed', 'event_id': e.event_id})
    # Cold-start floor ABOVE the tx block → block 100 was skipped at cold start.
    ing.state['scan_start_block'] = 500
    ing.state['last_processed_block'] = 800

    result = ing._debug_tx_match('0xcold', [(target, BASE_WALLET.lower())])

    assert result['status'] == 1
    assert result['was_block_scanned'] is False       # 100 < scan_start 500 → never scanned
    assert result['skipped_by_checkpoint'] is True    # 100 <= checkpoint 800
    assert result['backfill_triggered'] is True        # bounded recovery scan ran


def test_realtime_native_event_persists_when_address_in_fallback_location(monkeypatch):
    """A realtime native transfer for a target whose monitored address is stored in a
    FALLBACK location (contract_identifier, not wallet_address) is still classified and
    persisted as native_transfer. The resolved monitored wallet — not the raw
    wallet_address column — drives classification, so realtime detections are not
    silently dropped for such targets."""
    from services.api.app import monitoring_runner
    from datetime import datetime, timezone

    captured: dict = {}

    def _fake_persist_raw(connection, *, telemetry_id, workspace_id, asset_id, target_id,
                          provider_type, event_type, observed_at, evidence_source, payload, idempotency_key):
        captured['event_type'] = event_type
        return True

    monkeypatch.setattr(monitoring_runner, '_persist_raw_wallet_transfer_telemetry', _fake_persist_raw)

    target = _wallet_target()
    target['wallet_address'] = None                 # empty canonical column
    target['contract_identifier'] = BASE_WALLET.lower()  # address lives here instead

    ing = _make_ingestor()
    tx = _native_tx(tx_hash='0xfallback', from_addr=BASE_WALLET, to_addr=OTHER_WALLET)
    event = ing._build_native_transfer_event(
        target, tx, block_number=100, block_hash='0xb', observed_at=datetime.now(timezone.utc),
        direction='outbound', source_type='realtime_websocket',
    )
    result = monitoring_runner._maybe_persist_ingested_wallet_transfer(object(), target=target, event=event)

    assert result == 'native_transfer'
    assert captured['event_type'] == 'native_transfer'


# --- diagnose-tx endpoint: production scenario (tx below cold-start checkpoint) ---

_PROD_TX_HASH = '0x' + '7f' * 32  # 66-char 0x hash


def _run_diagnose_tx(monkeypatch, *, tx, receipt, target_row, checkpoint_row):
    from services.api.app import monitoring_runner

    workspace_id = str(target_row['workspace_id'])

    class _Conn:
        def execute(self, query, params=None):
            q = (query or '').lower()
            if 'from targets' in q:
                return _TelemetryRows([target_row])
            if 'monitoring_watcher_state' in q:
                return _TelemetryRows([checkpoint_row] if checkpoint_row is not None else [])
            if 'from telemetry_events' in q:
                return _TelemetryRows([])  # not yet persisted
            return _TelemetryRows([])

    rpc = MagicMock()
    rpc.call.side_effect = lambda method, params: (
        tx if method == 'eth_getTransactionByHash'
        else receipt if method == 'eth_getTransactionReceipt'
        else None
    )
    fake_request = MagicMock()
    fake_request.headers = {'x-workspace-id': workspace_id}

    with (
        patch('services.api.app.monitoring_runner.pg_connection') as mock_pg,
        patch('services.api.app.monitoring_runner.ensure_pilot_schema'),
        patch('services.api.app.evm_activity_provider.FailoverJsonRpcClient', return_value=rpc),
        patch('services.api.app.evm_activity_provider.resolve_chain_rpc', return_value={
            'rpc_url': 'http://rpc', 'rpc_urls': ['http://rpc'], 'expected_chain_id': 8453,
        }),
    ):
        mock_pg.return_value.__enter__ = lambda s: _Conn()
        mock_pg.return_value.__exit__ = MagicMock(return_value=False)
        return monitoring_runner.diagnose_wallet_transaction(fake_request, tx['hash'])


def _prod_diagnose_target():
    return {
        'id': str(uuid.uuid4()),
        'workspace_id': str(uuid.uuid4()),
        'name': 'Base Wallet',
        'target_type': 'wallet',
        'chain_network': 'base',
        'wallet_address': BASE_WALLET.lower(),
        'contract_identifier': None,
        'target_metadata': None,
        'asset_id': str(uuid.uuid4()),
        'monitoring_enabled': True,
        'enabled': True,
        'is_active': True,
    }


def test_diagnose_tx_reports_below_checkpoint_and_receipt_status(monkeypatch):
    """Read-only diagnose-tx endpoint reproduces the production incident: a matched
    outbound transfer whose block (47_373_543) is far below the realtime cold-start
    checkpoint (48_094_524) reports was_block_scanned=False, receipt status, and a
    persist_reason pointing the operator at import-tx."""
    target_row = _prod_diagnose_target()
    tx = {
        'hash': _PROD_TX_HASH,
        'from': BASE_WALLET.upper().replace('0X', '0x'),  # checksum-cased from MetaMask
        'to': OTHER_WALLET,
        'value': hex(10 ** 15),
        'input': '0x',
        'blockNumber': hex(47_373_543),
        'chainId': hex(8453),
    }
    receipt = {'status': '0x1'}
    checkpoint_row = {'last_processed_block': 48_094_524, 'metrics': {'scan_start_block': 48_094_523}}

    result = _run_diagnose_tx(
        monkeypatch, tx=tx, receipt=receipt, target_row=target_row, checkpoint_row=checkpoint_row,
    )

    assert result['tx_found'] is True
    assert result['block_number'] == 47_373_543
    assert result['receipt_status'] == 1
    assert result['realtime_checkpoint_block'] == 48_094_524
    assert result['realtime_scan_start_block'] == 48_094_523
    assert result['was_block_scanned'] is False
    assert result['below_realtime_checkpoint'] is True
    assert result['matched_target_count'] == 1

    match = result['matches'][0]
    assert match['matched'] is True
    assert match['from_matches'] is True
    assert match['to_matches'] is False
    assert match['normalized_from'] == BASE_WALLET.lower()
    assert match['normalized_to'] == OTHER_WALLET.lower()
    assert match['normalized_target'] == BASE_WALLET.lower()
    assert match['was_block_scanned'] is False
    assert match['persist_reason'] == 'matched_below_realtime_checkpoint_run_import_tx'


def test_diagnose_tx_reverted_transfer_surfaces_status_zero(monkeypatch):
    """A reverted send (receipt.status=0x0) is surfaced as receipt_status=0 so an
    operator can see the transfer failed on-chain (not a Decoda detection bug)."""
    target_row = _prod_diagnose_target()
    tx = {
        'hash': _PROD_TX_HASH,
        'from': BASE_WALLET.lower(),
        'to': OTHER_WALLET,
        'value': hex(0),
        'input': '0x',
        'blockNumber': hex(48_094_600),  # above checkpoint → forward scan will reach it
        'chainId': hex(8453),
    }
    receipt = {'status': '0x0'}
    checkpoint_row = {'last_processed_block': 48_094_524, 'metrics': {'scan_start_block': 48_094_523}}

    result = _run_diagnose_tx(
        monkeypatch, tx=tx, receipt=receipt, target_row=target_row, checkpoint_row=checkpoint_row,
    )

    assert result['receipt_status'] == 0
    assert result['below_realtime_checkpoint'] is False  # block above checkpoint
    assert result['was_block_scanned'] is False           # not yet within [start, checkpoint]
