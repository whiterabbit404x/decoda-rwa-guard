"""Exact tx-hash diagnosis for the realtime Base ETH detection path.

Production symptom: realtime was active (heads flowing, txs_seen in the thousands,
matches=0) yet the latest MetaMask Base ETH transfer only ever appeared as
"Detected by = Stable RPC Polling" — the provider later flipped to
provider_mode=rate_limited with realtime_scanning_active=False, so the tx likely
landed during the cooldown or outside the live-tail window.

These tests lock in the required debug/fix behaviour:

  1. Given a TX_HASH the debug fetches eth_getTransactionByHash +
     eth_getTransactionReceipt and logs realtime_tx_debug with block/from/to/value/
     status, the normalized addresses + per-side match flags, the live-tail window,
     the span-truthful was_block_scanned, and provider_mode_at_time /
     rate_limited_at_time.
  2. A tx whose block was never scanned logs realtime_tx_not_in_scanned_window and
     is recovered via a bounded tx_block±2 import persisting
     detected_by=realtime_backfill (realtime_tx_import for the import endpoint).
  3. Matching is the canonical shared matcher: normalized from OR to, no ERC20 log
     requirement, no minimum ETH value.
  4. A tx already detected by stable polling logs
     realtime_duplicate_existing_tx existing_detected_by=stable_rpc_polling and the
     UI/API stays truthful (detected_by keeps the first detector).
  5. A tx that landed during a provider rate-limit cooldown logs
     realtime_tx_missed_due_to_rate_limit with next_retry_at; stable polling
     remains the fallback (no realtime import fires).
  6. Acceptance: exactly one realtime_tx_verdict naming one of the canonical
     outcomes, shared between the worker debug and /ops/monitoring/diagnose-tx.
"""
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest


BASE_WALLET = '0x5f6f35fd8b10c5576089f99c7c8c351deb851d1f'
OTHER_WALLET = '0xcafe00000000000000000000000000000000feed'
TX_HASH = '0x' + 'ab' * 32

INGESTOR_LOGGER = 'services.api.app.base_realtime_ingestor'
RUNNER_LOGGER = 'services.api.app.monitoring_runner'


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


def _native_tx(*, tx_hash: str = TX_HASH, from_addr: str = BASE_WALLET,
               to_addr: str = OTHER_WALLET, value_wei: int = 10 ** 15,
               block: int = 100) -> dict:
    return {
        'hash': tx_hash,
        'from': from_addr,
        'to': to_addr,
        'value': hex(value_wei),
        'input': '0x',
        'blockNumber': hex(block),
        'chainId': hex(8453),
    }


def _block_with(txs: list[dict], *, number: int = 100, ts: int = 1_700_000_000) -> dict:
    return {
        'hash': f'0xblock{number:064x}'[:66],
        'number': hex(number),
        'timestamp': hex(ts),
        'transactions': txs,
    }


def _make_ingestor():
    from services.api.app.base_realtime_ingestor import BaseRealtimeIngestor
    return BaseRealtimeIngestor(
        rpc_url='http://rpc', ws_url='ws://ws', watcher_name='test-watcher',
        confirmations_required=1, max_events_per_minute=1000,
    )


def _messages(caplog) -> list[str]:
    return [r.getMessage() for r in caplog.records]


# ---------------------------------------------------------------------------
# Requirement 6.1 — tx hash debug shows from/to/value/block/status plus the
# live-tail window, provider mode and rate-limit state at the time.
# ---------------------------------------------------------------------------

def test_tx_debug_logs_block_from_to_value_status_and_window_fields(monkeypatch, caplog):
    ing = _make_ingestor()
    target = _wallet_target()
    tx = _native_tx(value_wei=7)   # tiny value must NOT be ignored

    def _rpc(method, params):
        if method == 'eth_getTransactionByHash':
            return tx
        if method == 'eth_getTransactionReceipt':
            return {'status': '0x1'}
        return None

    monkeypatch.setattr(ing, '_rpc_call', _rpc)
    monkeypatch.setattr(ing, '_existing_telemetry_detected_by', lambda *a, **k: None)
    ing.state['live_tail_from_block'] = 480
    ing.state['live_tail_to_block'] = 505
    ing.state['last_processed_block'] = 50  # tx block above → pending forward scan

    with caplog.at_level(logging.INFO, logger=INGESTOR_LOGGER):
        result = ing._debug_tx_match(TX_HASH, [(target, BASE_WALLET)])

    assert result['found'] is True
    assert result['block_number'] == 100
    assert result['from'] == BASE_WALLET.lower()
    assert result['to'] == OTHER_WALLET.lower()
    assert result['value_wei'] == 7
    assert result['status'] == 1
    assert result['live_tail_from_block'] == 480
    assert result['live_tail_to_block'] == 505
    assert result['rate_limited_at_time'] is False
    assert result['verdict'] == 'pending_forward_scan'

    dbg = next(m for m in _messages(caplog) if 'realtime_tx_debug ' in m)
    assert f'tx_hash={TX_HASH}' in dbg
    assert 'block_number=100' in dbg
    assert 'value=7' in dbg
    assert 'status=1' in dbg
    assert f'monitored_address={BASE_WALLET}' in dbg
    assert f'normalized_from={BASE_WALLET}' in dbg
    assert f'normalized_to={OTHER_WALLET.lower()}' in dbg
    assert f'normalized_target={BASE_WALLET}' in dbg
    assert 'from_matches=True' in dbg
    assert 'to_matches=False' in dbg
    assert 'live_tail_from_block=480' in dbg
    assert 'live_tail_to_block=505' in dbg
    assert 'was_block_scanned=False' in dbg
    assert 'provider_mode_at_time=' in dbg
    assert 'rate_limited_at_time=False' in dbg
    assert any('realtime_tx_verdict' in m and 'verdict=pending_forward_scan' in m
               for m in _messages(caplog))


# ---------------------------------------------------------------------------
# Requirement 6.2 — tx inside the live-tail window and matching the wallet
# persists detected_by=realtime_websocket, and the scan records its span.
# ---------------------------------------------------------------------------

def test_tx_inside_live_tail_window_persists_realtime_websocket(monkeypatch):
    ing = _make_ingestor()
    target = _wallet_target()
    block = _block_with([_native_tx(block=100)], number=100)
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

    processed = asyncio.run(ing._scan_head_native_transfers(101))

    assert processed == 1
    assert persisted[0].payload['detected_by'] == 'realtime_websocket'
    assert persisted[0].payload['tx_hash'] == TX_HASH
    # The live-tail window is recorded as a canonical fact for the tx debug…
    assert ing.state['live_tail_from_block'] == 100
    assert ing.state['live_tail_to_block'] == 100
    # …and the scanned span makes was_block_scanned truthful.
    assert ing._was_block_scanned(100) is True
    assert ing._was_block_scanned(99) is False


def test_matching_uses_from_or_to_and_ignores_no_minimum_value(monkeypatch):
    """Requirement 3: normalized from OR to matches; tiny ETH values and
    checksum-cased addresses are matched; no ERC20 logs are required."""
    ing = _make_ingestor()
    target = _wallet_target()
    # 1 wei inbound to a checksum-cased form of the watched wallet.
    tiny_inbound = _native_tx(
        tx_hash='0x' + 'cd' * 32, from_addr=OTHER_WALLET,
        to_addr=BASE_WALLET.upper().replace('0X', '0x'), value_wei=1, block=100,
    )
    block = _block_with([tiny_inbound], number=100)
    monkeypatch.setattr(
        ing, '_rpc_call',
        lambda method, params: block if method == 'eth_getBlockByNumber' else None,
    )
    persisted: list = []
    monkeypatch.setattr(
        ing, '_persist_event',
        lambda _t, e: (persisted.append(e), {'status': 'processed', 'event_id': e.event_id})[1],
    )

    n = ing._scan_native_transfers(100, 100, [(target, BASE_WALLET)])

    assert n == 1
    assert persisted[0].payload['wallet_transfer_direction'] == 'inbound'
    assert persisted[0].payload['value_wei'] == 1


# ---------------------------------------------------------------------------
# Requirement 6.3 — tx outside the scanned window triggers the bounded import.
# ---------------------------------------------------------------------------

def test_tx_outside_scanned_window_triggers_bounded_import(monkeypatch, caplog):
    ing = _make_ingestor()
    target = _wallet_target()
    tx = _native_tx(block=100)
    block_100 = _block_with([_native_tx(block=100)], number=100)

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
    monkeypatch.setattr(ing, '_existing_telemetry_detected_by', lambda *a, **k: None)
    persisted: list = []
    monkeypatch.setattr(
        ing, '_persist_event',
        lambda _t, e: (persisted.append(e), {'status': 'processed', 'event_id': e.event_id})[1],
    )
    # Worker scanned 480..505 only; checkpoint is ahead of the tx block, so the
    # forward scan will never reach block 100 again.
    ing._note_scanned_range(480, 505)
    ing.state['last_processed_block'] = 505

    with caplog.at_level(logging.INFO, logger=INGESTOR_LOGGER):
        result = ing._debug_tx_match(TX_HASH, [(target, BASE_WALLET)])

    assert result['was_block_scanned'] is False
    assert result['backfill_triggered'] is True
    assert result['backfill_from_block'] == 98
    assert result['backfill_to_block'] == 102
    assert result['imported_by'] == 'realtime_backfill'
    assert result['verdict'] == 'outside_scanned_window_imported_by_realtime_backfill'
    # The recovered transfer is persisted as realtime_backfill — never claimed as
    # a live WebSocket detection, and the forward cursor is unchanged.
    assert persisted and persisted[0].payload['detected_by'] == 'realtime_backfill'
    assert ing.state['last_processed_block'] == 505

    window = next(m for m in _messages(caplog) if 'realtime_tx_not_in_scanned_window' in m)
    assert f'tx_hash={TX_HASH}' in window
    assert 'tx_block=100' in window
    assert 'scanned_from=480' in window
    assert 'scanned_to=505' in window
    assert any('realtime_tx_verdict' in m
               and 'verdict=outside_scanned_window_imported_by_realtime_backfill' in m
               for m in _messages(caplog))


def test_scanned_spans_do_not_overclaim_cooldown_gap():
    """The truthfulness fix: after a rate-limit cooldown the checkpoint is
    fast-forwarded past blocks that were never scanned. was_block_scanned must be
    False for those blocks even though they sit inside [scan_start, checkpoint]."""
    ing = _make_ingestor()
    ing.state['scan_start_block'] = 90
    ing._note_scanned_range(90, 95)      # scanned before the cooldown
    ing._note_scanned_range(150, 200)    # live-tail resumed near head afterwards
    ing.state['last_processed_block'] = 200

    # Block 120 is inside [scan_start=90, checkpoint=200] but was skipped.
    assert ing._was_block_scanned(120) is False
    assert ing._was_block_scanned(93) is True
    assert ing._was_block_scanned(160) is True
    assert ing._scanned_window_bounds() == (90, 200)
    # Adjacent/overlapping ranges merge so the span list stays bounded.
    ing._note_scanned_range(96, 149)
    assert ing._scanned_spans == [[90, 200]]


# ---------------------------------------------------------------------------
# Requirement 6.4 — tx during provider_rate_limited logs missed_due_to_rate_limit.
# ---------------------------------------------------------------------------

def test_tx_during_rate_limit_cooldown_logs_missed_due_to_rate_limit(monkeypatch, caplog):
    ing = _make_ingestor()
    target = _wallet_target()
    ing.rate_limit_cooldown_seconds = 900
    ing._enter_provider_rate_limit_cooldown()   # opens a cooldown window "now"

    tx_ts = int(time.time()) + 5                 # tx landed inside the cooldown
    tx = _native_tx(block=100)
    header = {'number': hex(100), 'timestamp': hex(tx_ts)}

    block_fetches: list[int] = []

    def _rpc(method, params):
        if method == 'eth_getTransactionByHash':
            return tx
        if method == 'eth_getTransactionReceipt':
            return {'status': '0x1'}
        if method == 'eth_getBlockByNumber':
            block_fetches.append(int(params[0], 16))
            return header
        return None

    monkeypatch.setattr(ing, '_rpc_call', _rpc)
    monkeypatch.setattr(ing, '_existing_telemetry_detected_by', lambda *a, **k: None)
    # Checkpoint fast-forwarded past the tx block by the post-cooldown live tail,
    # but the block itself was never scanned (no span covers it).
    ing.state['last_processed_block'] = 500

    with caplog.at_level(logging.INFO, logger=INGESTOR_LOGGER):
        result = ing._debug_tx_match(TX_HASH, [(target, BASE_WALLET)])

    assert result['rate_limited_at_time'] is True
    assert result['provider_mode_at_time'] == 'rate_limited'
    assert result['verdict'] == 'missed_provider_rate_limited'
    # Requirement 5: stable polling remains the fallback — no realtime import fires.
    assert result['backfill_triggered'] is False
    assert result['imported_by'] is None

    missed = next(m for m in _messages(caplog) if 'realtime_tx_missed_due_to_rate_limit' in m)
    assert f'tx_hash={TX_HASH}' in missed
    assert 'block_number=100' in missed
    assert 'next_retry_at=' in missed and 'next_retry_at=none' not in missed


def test_rate_limit_window_closes_on_resume():
    """A tx that lands AFTER the cooldown cleared is not blamed on the rate limit."""
    ing = _make_ingestor()
    ing.rate_limit_cooldown_seconds = 900
    ing._enter_provider_rate_limit_cooldown()
    during_cooldown = datetime.now(timezone.utc)
    ing._resume_after_rate_limit_cooldown()

    after_window = datetime.now(timezone.utc) + timedelta(seconds=60)
    assert ing._rate_limit_window_covering(after_window) is None
    assert ing._rate_limit_window_covering(during_cooldown) is not None


# ---------------------------------------------------------------------------
# Requirement 6.5 — a tx already detected by stable polling logs duplicate skipped.
# ---------------------------------------------------------------------------

def test_scan_logs_duplicate_existing_tx_from_stable_polling(monkeypatch, caplog):
    ing = _make_ingestor()
    target = _wallet_target()
    block = _block_with([_native_tx(block=100)], number=100)
    monkeypatch.setattr(
        ing, '_rpc_call',
        lambda method, params: block if method == 'eth_getBlockByNumber' else None,
    )
    monkeypatch.setattr(
        ing, '_persist_event',
        lambda _t, e: {
            'status': 'duplicate_suppressed', 'event_id': e.event_id,
            'existing_detected_by': 'stable_rpc_polling',
        },
    )

    with caplog.at_level(logging.INFO, logger=INGESTOR_LOGGER):
        n = ing._scan_native_transfers(100, 100, [(target, BASE_WALLET)])

    assert n == 0  # duplicate → not re-counted as a realtime detection
    dup = next(m for m in _messages(caplog) if 'realtime_duplicate_existing_tx' in m)
    assert f'tx_hash={TX_HASH}' in dup
    assert 'existing_detected_by=stable_rpc_polling' in dup


def test_process_ingested_event_receipt_duplicate_names_existing_detector():
    """The receipt-based dedupe reports WHO wrote the existing receipt."""
    from services.api.app.monitoring_runner import process_ingested_event
    from services.api.app.base_realtime_ingestor import BaseRealtimeIngestor

    ing = BaseRealtimeIngestor(rpc_url='http://rpc', ws_url='ws://ws', watcher_name='t')
    target = _wallet_target()
    log = {
        'blockNumber': hex(400), 'transactionHash': TX_HASH, 'logIndex': hex(0),
        'topics': ['0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef', None, None],
        'address': '0x0000000000000000000000000000000000000000',
    }
    event = ing._build_event_from_log(target, log)

    conn_mock = MagicMock()
    conn_mock.execute.return_value.fetchone.return_value = {
        'id': 'existing-receipt', 'ingestion_source': 'polling',
    }
    result = process_ingested_event(conn_mock, target=target, event=event, ingestion_mode='live')

    assert result['status'] == 'duplicate_suppressed'
    assert result['existing_detected_by'] == 'stable_rpc_polling'


def test_process_ingested_event_telemetry_duplicate_from_stable_polling(caplog):
    """Cross-worker dedupe: stable polling persists telemetry but writes NO receipt.
    The realtime worker must recognise the existing telemetry row (same idempotency
    key), return duplicate_suppressed naming stable_rpc_polling, and must NOT re-run
    analysis or claim realtime persistence."""
    from services.api.app.monitoring_runner import process_ingested_event
    from services.api.app.base_realtime_ingestor import BaseRealtimeIngestor
    from datetime import datetime as _dt, timezone as _tz

    ing = BaseRealtimeIngestor(rpc_url='http://rpc', ws_url='ws://ws', watcher_name='t')
    target = _wallet_target()
    tx = _native_tx(block=100)
    event = ing._build_native_transfer_event(
        target, tx, block_number=100, block_hash='0xb',
        observed_at=_dt.now(_tz.utc), direction='outbound',
        source_type='realtime_websocket',
    )

    class _Result:
        def __init__(self, row):
            self._row = row

        def fetchone(self):
            return self._row

    class _Conn:
        def __init__(self):
            self.queries: list[str] = []

        def execute(self, query, params=None):
            self.queries.append(query or '')
            q = (query or '').lower()
            if 'from workspaces' in q:
                return _Result({'id': target['workspace_id'], 'name': 'W'})
            if 'monitoring_event_receipts' in q:
                return _Result(None)  # stable polling wrote no receipt
            if 'from telemetry_events' in q:
                return _Result({'detected_by': 'stable_rpc_polling'})
            return _Result(None)

    conn = _Conn()
    with caplog.at_level(logging.INFO, logger=RUNNER_LOGGER):
        result = process_ingested_event(conn, target=target, event=event, ingestion_mode='live')

    assert result['status'] == 'duplicate_suppressed'
    assert result['existing_detected_by'] == 'stable_rpc_polling'
    # No INSERTs ran — the duplicate is fully suppressed, never re-processed.
    assert not any('insert into' in q.lower() for q in conn.queries)
    dup = next(m for m in _messages(caplog) if 'realtime_duplicate_existing_tx' in m)
    assert 'existing_detected_by=stable_rpc_polling' in dup


def test_debug_tx_with_existing_stable_row_reports_duplicate_verdict(monkeypatch, caplog):
    """Acceptance outcome 4: 'already exists from Stable RPC Polling and realtime
    duplicate was skipped' — and no import fires for it."""
    ing = _make_ingestor()
    target = _wallet_target()
    tx = _native_tx(block=100)
    monkeypatch.setattr(
        ing, '_rpc_call',
        lambda method, params: tx if method == 'eth_getTransactionByHash' else None,
    )
    monkeypatch.setattr(
        ing, '_existing_telemetry_detected_by', lambda *a, **k: 'stable_rpc_polling',
    )
    backfills: list = []
    monkeypatch.setattr(
        ing, '_scan_native_transfers',
        lambda *a, **k: backfills.append(a) or 0,
    )
    ing.state['last_processed_block'] = 500  # below checkpoint — would import if new

    with caplog.at_level(logging.INFO, logger=INGESTOR_LOGGER):
        result = ing._debug_tx_match(TX_HASH, [(target, BASE_WALLET)])

    assert result['existing_detected_by'] == 'stable_rpc_polling'
    assert result['verdict'] == 'already_exists_stable_rpc_polling_realtime_duplicate_skipped'
    assert result['backfill_triggered'] is False
    assert backfills == []
    dup = next(m for m in _messages(caplog) if 'realtime_duplicate_existing_tx' in m)
    assert 'existing_detected_by=stable_rpc_polling' in dup


# ---------------------------------------------------------------------------
# Requirement 6.6 — UI/API label the imported detection paths truthfully.
# ---------------------------------------------------------------------------

def test_ui_telemetry_page_labels_realtime_backfill_and_tx_import():
    src = open(
        'apps/web/app/(product)/monitoring-sources/[targetId]/telemetry/page.tsx',
        encoding='utf-8',
    ).read()
    assert "realtime_backfill: 'Realtime Backfill'" in src
    assert "realtime_tx_import: 'Realtime Tx Import'" in src
    assert "stable_rpc_polling: 'Stable RPC Polling'" in src


def test_api_classifies_import_tags_as_realtime_detection():
    from services.api.app.worker_status import REALTIME_DETECTED_BY
    assert 'realtime_backfill' in REALTIME_DETECTED_BY
    assert 'realtime_tx_import' in REALTIME_DETECTED_BY
    assert 'realtime_websocket' in REALTIME_DETECTED_BY


# ---------------------------------------------------------------------------
# Acceptance — the shared verdict classifier answers with exactly one of the
# canonical outcomes.
# ---------------------------------------------------------------------------

def test_verdict_matched_and_persisted_by_realtime_websocket():
    from services.api.app.worker_status import classify_realtime_tx_verdict
    assert classify_realtime_tx_verdict(
        tx_found=True, matched=True, existing_detected_by='realtime_websocket',
        was_block_scanned=True, rate_limited_at_tx_time=False, below_checkpoint=True,
    ) == 'matched_and_persisted_by_realtime_websocket'


def test_verdict_missed_provider_rate_limited():
    from services.api.app.worker_status import classify_realtime_tx_verdict
    assert classify_realtime_tx_verdict(
        tx_found=True, matched=True, existing_detected_by=None,
        was_block_scanned=False, rate_limited_at_tx_time=True, below_checkpoint=True,
    ) == 'missed_provider_rate_limited'


def test_verdict_outside_scanned_window_imported_by_realtime_backfill():
    from services.api.app.worker_status import classify_realtime_tx_verdict
    assert classify_realtime_tx_verdict(
        tx_found=True, matched=True, existing_detected_by=None,
        was_block_scanned=False, rate_limited_at_tx_time=False, below_checkpoint=True,
        imported_by='realtime_backfill',
    ) == 'outside_scanned_window_imported_by_realtime_backfill'
    # An existing row imported earlier reports the same family of verdicts.
    assert classify_realtime_tx_verdict(
        tx_found=True, matched=True, existing_detected_by='realtime_tx_import',
        was_block_scanned=False, rate_limited_at_tx_time=False, below_checkpoint=True,
    ) == 'outside_scanned_window_imported_by_realtime_tx_import'


def test_verdict_stable_duplicate_skipped():
    from services.api.app.worker_status import classify_realtime_tx_verdict
    assert classify_realtime_tx_verdict(
        tx_found=True, matched=True, existing_detected_by='stable_rpc_polling',
        was_block_scanned=True, rate_limited_at_tx_time=True, below_checkpoint=True,
    ) == 'already_exists_stable_rpc_polling_realtime_duplicate_skipped'


def test_verdict_fallback_cases():
    from services.api.app.worker_status import classify_realtime_tx_verdict
    assert classify_realtime_tx_verdict(
        tx_found=False, matched=False, existing_detected_by=None,
        was_block_scanned=False, rate_limited_at_tx_time=False, below_checkpoint=False,
    ) == 'transaction_not_found'
    assert classify_realtime_tx_verdict(
        tx_found=True, matched=False, existing_detected_by=None,
        was_block_scanned=False, rate_limited_at_tx_time=False, below_checkpoint=False,
    ) == 'not_matched_no_watched_wallet_in_tx'
    # Scanned + matched + nothing persisted = matching/persistence defect, loud.
    assert classify_realtime_tx_verdict(
        tx_found=True, matched=True, existing_detected_by=None,
        was_block_scanned=True, rate_limited_at_tx_time=False, below_checkpoint=True,
    ) == 'scanned_but_not_persisted_check_matching'
    assert classify_realtime_tx_verdict(
        tx_found=True, matched=True, existing_detected_by=None,
        was_block_scanned=False, rate_limited_at_tx_time=False, below_checkpoint=True,
    ) == 'outside_scanned_window_not_yet_imported'


def test_detected_by_from_ingestion_source_mapping():
    from services.api.app.worker_status import detected_by_from_ingestion_source
    assert detected_by_from_ingestion_source('polling') == 'stable_rpc_polling'
    assert detected_by_from_ingestion_source('evm_rpc') == 'stable_rpc_polling'
    assert detected_by_from_ingestion_source('realtime_websocket') == 'realtime_websocket'
    assert detected_by_from_ingestion_source('realtime_tx_import') == 'realtime_tx_import'
    assert detected_by_from_ingestion_source(None) == 'unknown'
    assert detected_by_from_ingestion_source('') == 'unknown'


# ---------------------------------------------------------------------------
# Heartbeat persists the diagnosis facts so the read-only endpoint can use them.
# ---------------------------------------------------------------------------

def test_heartbeat_persists_scanned_spans_and_rate_limit_windows():
    import json as _json

    ing = _make_ingestor()
    ing._note_scanned_range(100, 120)
    ing.rate_limit_cooldown_seconds = 900
    ing._enter_provider_rate_limit_cooldown()
    ing.state['live_tail_from_block'] = 118
    ing.state['live_tail_to_block'] = 120

    executed: list = []
    with (
        patch('services.api.app.base_realtime_ingestor.pg_connection') as mock_pg,
        patch('services.api.app.base_realtime_ingestor.ensure_pilot_schema', lambda c: None),
    ):
        mock_conn = MagicMock()
        mock_conn.__enter__ = lambda s: s
        mock_conn.__exit__ = MagicMock(return_value=False)
        mock_conn.execute = lambda q, p=None: executed.append((q, p)) or MagicMock()
        mock_pg.return_value = mock_conn
        ing._record_heartbeat()

    upsert = next((p for q, p in executed if p and 'monitoring_watcher_state' in (q or '')), None)
    assert upsert is not None
    metrics_json = next(v for v in upsert if isinstance(v, str) and 'scanned_spans' in v)
    metrics = _json.loads(metrics_json)
    assert metrics['scanned_spans'] == [[100, 120]]
    assert metrics['live_tail_from_block'] == 118
    assert metrics['live_tail_to_block'] == 120
    assert metrics['rate_limit_windows'], 'rate-limit window history must persist'
    assert metrics['rate_limit_windows'][-1]['next_retry_at']


# ---------------------------------------------------------------------------
# Read-only /ops/monitoring/diagnose-tx endpoint mirrors the worker verdicts.
# ---------------------------------------------------------------------------

class _Rows:
    def __init__(self, rows):
        self._rows = list(rows or [])

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)


def _run_diagnose_tx(*, tx, receipt, target_row, checkpoint_row,
                     telemetry_row=None, block_header=None):
    from services.api.app import monitoring_runner

    workspace_id = str(target_row['workspace_id'])

    class _Conn:
        def execute(self, query, params=None):
            q = (query or '').lower()
            if 'from targets' in q:
                return _Rows([target_row])
            if 'monitoring_watcher_state' in q:
                return _Rows([checkpoint_row] if checkpoint_row is not None else [])
            if 'from telemetry_events' in q:
                return _Rows([telemetry_row] if telemetry_row is not None else [])
            return _Rows([])

    rpc = MagicMock()
    rpc.call.side_effect = lambda method, params: (
        tx if method == 'eth_getTransactionByHash'
        else receipt if method == 'eth_getTransactionReceipt'
        else block_header if method == 'eth_getBlockByNumber'
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


def _diagnose_target():
    t = _wallet_target()
    t['target_metadata'] = None
    return t


def test_diagnose_tx_reports_stable_duplicate_skipped():
    tx = _native_tx(block=100)
    result = _run_diagnose_tx(
        tx=tx, receipt={'status': '0x1'}, target_row=_diagnose_target(),
        checkpoint_row={'last_processed_block': 500,
                        'metrics': {'scan_start_block': 90, 'scanned_spans': [[90, 500]]}},
        telemetry_row={'detected_by': 'stable_rpc_polling'},
    )
    assert result['realtime_verdict'] == 'already_exists_stable_rpc_polling_realtime_duplicate_skipped'
    assert result['existing_detected_by'] == 'stable_rpc_polling'
    assert result['realtime_duplicate_skipped'] is True
    match = result['matches'][0]
    assert match['already_persisted'] is True
    assert match['existing_detected_by'] == 'stable_rpc_polling'
    assert match['realtime_duplicate_skipped'] is True


def test_diagnose_tx_missed_due_to_rate_limit():
    now = datetime.now(timezone.utc)
    tx_ts = int(now.timestamp())
    window = {
        'started_at': (now - timedelta(seconds=60)).isoformat(),
        'ended_at': None,
        'next_retry_at': (now + timedelta(seconds=840)).isoformat(),
    }
    tx = _native_tx(block=100)
    result = _run_diagnose_tx(
        tx=tx, receipt={'status': '0x1'}, target_row=_diagnose_target(),
        checkpoint_row={'last_processed_block': 500,
                        'metrics': {'scan_start_block': 90,
                                    'scanned_spans': [[90, 95], [480, 500]],
                                    'rate_limit_windows': [window]}},
        block_header={'number': hex(100), 'timestamp': hex(tx_ts)},
    )
    assert result['was_block_scanned'] is False
    assert result['rate_limited_at_time'] is True
    assert result['rate_limit_next_retry_at'] == window['next_retry_at']
    assert result['realtime_verdict'] == 'missed_provider_rate_limited'


def test_diagnose_tx_spans_do_not_overclaim_cooldown_gap():
    """A tx block inside [scan_start, checkpoint] but in a span the worker never
    scanned (the cooldown gap) reports was_block_scanned=False and points at the
    import recovery — the legacy inference falsely claimed it was scanned."""
    tx = _native_tx(block=100)
    result = _run_diagnose_tx(
        tx=tx, receipt={'status': '0x1'}, target_row=_diagnose_target(),
        checkpoint_row={'last_processed_block': 500,
                        'metrics': {'scan_start_block': 90,
                                    'scanned_spans': [[90, 95], [480, 500]]}},
    )
    assert result['below_realtime_checkpoint'] is True
    assert result['was_block_scanned'] is False
    assert result['realtime_verdict'] == 'outside_scanned_window_not_yet_imported'
    assert result['realtime_scanned_spans'] == [[90, 95], [480, 500]]


def test_diagnose_tx_legacy_fallback_without_spans():
    """Watcher rows persisted before span tracking still diagnose via the legacy
    [scan_start, checkpoint] inference rather than failing."""
    tx = _native_tx(block=100)
    result = _run_diagnose_tx(
        tx=tx, receipt={'status': '0x1'}, target_row=_diagnose_target(),
        checkpoint_row={'last_processed_block': 500, 'metrics': {'scan_start_block': 90}},
    )
    assert result['was_block_scanned'] is True
    assert result['realtime_verdict'] == 'scanned_but_not_persisted_check_matching'


# ---------------------------------------------------------------------------
# Import endpoint tag stays realtime_tx_import (requirement 6, last case).
# ---------------------------------------------------------------------------

def test_worker_tx_import_persists_realtime_tx_import(monkeypatch):
    ing = _make_ingestor()
    target = _wallet_target()
    tx = _native_tx(block=100)
    block_100 = _block_with([_native_tx(block=100)], number=100)

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
    persisted: list = []
    monkeypatch.setattr(
        ing, '_persist_event',
        lambda _t, e: (persisted.append(e), {'status': 'processed', 'event_id': e.event_id})[1],
    )
    ing.state['last_processed_block'] = 500

    result = ing._backfill_tx_by_hash(TX_HASH, [(target, BASE_WALLET)])

    assert result['found'] is True
    assert result['imported'] == 1
    assert result['detected_by'] == 'realtime_tx_import'
    assert persisted[0].payload['detected_by'] == 'realtime_tx_import'
    # Importing an old block never moves the live forward cursor.
    assert ing.state['last_processed_block'] == 500
