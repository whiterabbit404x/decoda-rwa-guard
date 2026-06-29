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
import uuid

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
