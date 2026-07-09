"""Tests for QuickNode Stream checkpoint tracking, gap detection, and backfill.

services/api/app/quicknode_streams.py

Covers the "QuickNode Stream missed a fresh tx" fix — the stream was alive but
already far past the missed tx's block, so only stable RPC polling caught it:

  1. Checkpoint tracking (latest_stream_block / last_processed_block /
     missed_block_gap / stream_started_at_block / webhook_received_at).
  2. Gap detection: a jump from block A to B where B > A + 1 logs
     quicknode_stream_gap_detected and returns the missing range.
  3. Backfill-on-gap: the skipped blocks are fetched from Base RPC and run
     through the same matcher, persisting detected_by=quicknode_stream_backfill,
     with no duplicate telemetry rows.
  4. Debug-tx endpoint stream_miss_reason classification (task requirement 5):
     stream_not_at_block_yet / stream_already_past_block / gap_detected /
     duplicate_suppressed / matcher_failed.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from services.api.app import quicknode_streams as qn

WALLET_ADDR = '0x5f6f35fd8b10c5576089f99c7c8c351deb851d1f'
COUNTERPARTY = '0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'
UNRELATED_ADDR = '0xbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb'
# The real production hash + block from the incident report.
MISSED_TX_HASH = '0x7b09f621698842b1c04f66815318775662b7c48087a6cf6ae4e041c67049948a'
MISSED_BLOCK = 48365342
SECRET = 'whsec_test_secret_123'
NONCE = 'test-nonce-abc123'
STREAM_KEY = qn.QUICKNODE_STREAM_KEY_BASE


def _sign(secret: str, *, nonce: str, timestamp: str, body: bytes) -> str:
    return hmac.new(secret.encode('utf-8'), nonce.encode('utf-8') + timestamp.encode('utf-8') + body, hashlib.sha256).hexdigest()


def _now_ts() -> str:
    return str(int(time.time()))


def _make_target(*, target_id: str | None = None, wallet_address: str = WALLET_ADDR) -> dict:
    return {
        'id': target_id or str(uuid.uuid4()),
        'workspace_id': str(uuid.uuid4()),
        'name': 'Treasury Base Wallet',
        'target_type': 'wallet',
        'chain_network': 'base',
        'chain_id': 8453,
        'wallet_address': wallet_address,
        'contract_identifier': None,
        'asset_id': str(uuid.uuid4()),
        'target_metadata': {},
        'monitoring_enabled': True,
        'enabled': True,
        'is_active': True,
    }


class _Rows:
    def __init__(self, rows=None):
        self._rows = list(rows or [])

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)


class _CheckpointConn:
    """Fake connection with STATEFUL quicknode_stream_checkpoints upsert semantics."""

    def __init__(self, *, targets=None, existing_telemetry=None, checkpoint=None):
        self.targets = targets or []
        self.existing_telemetry = existing_telemetry
        self.checkpoint = dict(checkpoint) if checkpoint else None
        self.telemetry_inserts: list[tuple] = []
        self.commit_calls = 0
        self.rollback_calls = 0

    def execute(self, query, params=None):
        q = (query or '').strip().lower()
        if q.startswith('create table'):
            return _Rows([])
        if 'from quicknode_stream_checkpoints' in q:
            return _Rows([self.checkpoint] if self.checkpoint else [])
        if q.startswith('insert into quicknode_stream_checkpoints'):
            stream_key, latest, last_processed, gap, started, received_at = params
            self.checkpoint = {
                'stream_key': stream_key,
                'latest_stream_block': latest,
                'last_processed_block': last_processed,
                'missed_block_gap': gap,
                'stream_started_at_block': started,
                'webhook_received_at': received_at,
            }
            return _Rows([])
        if 'from targets' in q:
            return _Rows(self.targets)
        if 'from assets' in q:
            return _Rows([])
        if 'from telemetry_events' in q and 'select' in q:
            return _Rows([self.existing_telemetry] if self.existing_telemetry else [])
        if q.startswith('insert into telemetry_events'):
            self.telemetry_inserts.append(tuple(params or ()))
            return _Rows([])
        return _Rows([])

    def commit(self):
        self.commit_calls += 1

    def rollback(self):
        self.rollback_calls += 1


@contextmanager
def _mock_pg(connection):
    yield connection


def _block_with_txs(block_number: int, txs: list[dict]) -> dict:
    """An eth_getBlockByNumber(full=True) result shape."""
    return {'number': hex(block_number), 'hash': '0x' + 'ab' * 32, 'transactions': txs}


def _rpc_tx(*, tx_hash: str, tx_from: str, tx_to: str = COUNTERPARTY, block: int, value: str = '0xde0b6b3a7640000') -> dict:
    return {'hash': tx_hash, 'from': tx_from, 'to': tx_to, 'value': value, 'blockNumber': hex(block)}


def _fake_rpc_client(blocks_by_number: dict[int, dict]):
    """FailoverJsonRpcClient stand-in serving eth_getBlockByNumber from a map."""

    class _C:
        def call(self, method, params):
            if method == 'eth_getBlockByNumber':
                block_number = int(params[0], 16)
                return blocks_by_number.get(block_number)
            return None

    return _C()


# ---------------------------------------------------------------------------
# _collect_batch_block_numbers
# ---------------------------------------------------------------------------

def test_collect_block_numbers_from_block_with_receipts_shape():
    payload = [{'block': {'number': hex(MISSED_BLOCK), 'transactions': []}, 'receipts': []}]
    assert qn._collect_batch_block_numbers(payload, []) == [MISSED_BLOCK]


def test_collect_block_numbers_from_normalized_flat_txs():
    normalized = [{'tx_hash': MISSED_TX_HASH, 'from_address': WALLET_ADDR, 'block_number': MISSED_BLOCK}]
    assert qn._collect_batch_block_numbers({'unrelated': 'x'}, normalized) == [MISSED_BLOCK]


def test_collect_block_numbers_empty_when_no_block_info():
    assert qn._collect_batch_block_numbers({'unrelated': 'x'}, []) == []


# ---------------------------------------------------------------------------
# Checkpoint tracking + gap detection
# ---------------------------------------------------------------------------

def test_first_batch_sets_started_and_detects_no_gap():
    conn = _CheckpointConn()
    gap = qn._track_stream_checkpoint_and_detect_gap(
        conn, stream_key=STREAM_KEY, batch_first_block=100, batch_last_block=100,
        received_at=datetime.now(timezone.utc),
    )
    assert gap is None
    assert conn.checkpoint['stream_started_at_block'] == 100
    assert conn.checkpoint['last_processed_block'] == 100
    assert conn.checkpoint['latest_stream_block'] == 100
    assert conn.checkpoint['missed_block_gap'] == 0


def test_contiguous_next_batch_has_no_gap():
    conn = _CheckpointConn(checkpoint={
        'latest_stream_block': 100, 'last_processed_block': 100, 'stream_started_at_block': 90,
    })
    gap = qn._track_stream_checkpoint_and_detect_gap(
        conn, stream_key=STREAM_KEY, batch_first_block=101, batch_last_block=101,
        received_at=datetime.now(timezone.utc),
    )
    assert gap is None
    assert conn.checkpoint['last_processed_block'] == 101


def test_gap_detected_returns_missing_range_and_logs(caplog: pytest.LogCaptureFixture):
    conn = _CheckpointConn(checkpoint={
        'latest_stream_block': 100, 'last_processed_block': 100, 'stream_started_at_block': 90,
    })
    with caplog.at_level('INFO', logger='services.api.app.quicknode_streams'):
        gap = qn._track_stream_checkpoint_and_detect_gap(
            conn, stream_key=STREAM_KEY, batch_first_block=105, batch_last_block=105,
            received_at=datetime.now(timezone.utc),
        )
    # Missing blocks are 101..104 (A=100, B=105, missing_count=4).
    assert gap == (101, 104)
    assert 'quicknode_stream_gap_detected from_block=100 to_block=105 missing_count=4' in caplog.text
    # Checkpoint still advances past the gap so it is never re-detected.
    assert conn.checkpoint['last_processed_block'] == 105
    assert conn.checkpoint['missed_block_gap'] == 4
    # stream_started_at_block is preserved (COALESCE) across the update.
    assert conn.checkpoint['stream_started_at_block'] == 90


def test_out_of_order_old_batch_never_regresses_and_no_gap():
    conn = _CheckpointConn(checkpoint={
        'latest_stream_block': 200, 'last_processed_block': 200, 'stream_started_at_block': 90,
    })
    gap = qn._track_stream_checkpoint_and_detect_gap(
        conn, stream_key=STREAM_KEY, batch_first_block=150, batch_last_block=150,
        received_at=datetime.now(timezone.utc),
    )
    # 150 <= 200 + 1 so no gap; high-water mark does not regress.
    assert gap is None
    assert conn.checkpoint['last_processed_block'] == 200
    assert conn.checkpoint['latest_stream_block'] == 200


# ---------------------------------------------------------------------------
# _classify_stream_coverage
# ---------------------------------------------------------------------------

def test_classify_coverage_no_checkpoint():
    assert qn._classify_stream_coverage(None, MISSED_BLOCK) == 'no_checkpoint'
    assert qn._classify_stream_coverage({'latest_stream_block': 100}, None) == 'no_checkpoint'


def test_classify_coverage_not_at_block_yet():
    cp = {'latest_stream_block': 100, 'stream_started_at_block': 50}
    assert qn._classify_stream_coverage(cp, 150) == 'stream_not_at_block_yet'


def test_classify_coverage_already_past_block():
    # The incident shape: stream started far ahead of the missed tx block.
    cp = {'latest_stream_block': 48388130, 'stream_started_at_block': 48388128}
    assert qn._classify_stream_coverage(cp, MISSED_BLOCK) == 'stream_already_past_block'


def test_classify_coverage_within_range():
    cp = {'latest_stream_block': 200, 'stream_started_at_block': 50}
    assert qn._classify_stream_coverage(cp, 120) == 'within_stream_range'


# ---------------------------------------------------------------------------
# Backfill-on-gap
# ---------------------------------------------------------------------------

def test_backfill_fetches_missing_blocks_and_persists_missed_tx(monkeypatch: pytest.MonkeyPatch):
    target = _make_target()
    conn = _CheckpointConn(targets=[target])
    # Blocks 101..104; the missed matching tx lives in block 103.
    blocks = {
        101: _block_with_txs(101, [_rpc_tx(tx_hash='0x' + '11' * 32, tx_from=UNRELATED_ADDR, block=101)]),
        102: _block_with_txs(102, []),
        103: _block_with_txs(103, [_rpc_tx(tx_hash=MISSED_TX_HASH, tx_from=WALLET_ADDR, block=103)]),
        104: _block_with_txs(104, []),
    }
    with patch.object(qn, '_make_base_rpc_client', lambda: _fake_rpc_client(blocks)), \
         patch.object(qn, '_create_wallet_transfer_alert_chain', lambda **kw: {'smoke_alert_id': None, 'sig_alert_id': None}):
        stats = qn._backfill_stream_gap(conn, [target], gap_from=101, gap_to=104)
    assert stats['blocks_scanned'] == 4
    assert stats['matched'] == 1
    assert stats['persisted'] == 1
    assert stats['duplicates'] == 0
    assert stats['truncated'] is False
    assert len(conn.telemetry_inserts) == 1
    payload = json.loads(conn.telemetry_inserts[0][9])
    assert payload['detected_by'] == 'quicknode_stream_backfill'
    assert payload['source_type'] == 'quicknode_stream_backfill'
    assert payload['tx_hash'] == MISSED_TX_HASH
    assert payload['block_number'] == 103


def test_backfill_suppresses_duplicate_no_double_row(monkeypatch: pytest.MonkeyPatch):
    """A tx stable polling already recorded is duplicate_suppressed — no second row."""
    target = _make_target()
    existing = {'id': str(uuid.uuid4()), 'event_type': 'native_transfer', 'detected_by': 'stable_rpc_polling'}
    conn = _CheckpointConn(targets=[target], existing_telemetry=existing)
    blocks = {103: _block_with_txs(103, [_rpc_tx(tx_hash=MISSED_TX_HASH, tx_from=WALLET_ADDR, block=103)])}
    with patch.object(qn, '_make_base_rpc_client', lambda: _fake_rpc_client(blocks)):
        stats = qn._backfill_stream_gap(conn, [target], gap_from=103, gap_to=103)
    assert stats['persisted'] == 0
    assert stats['duplicates'] == 1
    assert conn.telemetry_inserts == []


def test_backfill_truncates_gap_larger_than_cap(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv('QUICKNODE_STREAM_BACKFILL_MAX_BLOCKS', '3')
    target = _make_target()
    conn = _CheckpointConn(targets=[target])
    # 10-block gap, cap 3 -> only blocks 101..103 fetched.
    blocks = {n: _block_with_txs(n, []) for n in range(101, 111)}
    with patch.object(qn, '_make_base_rpc_client', lambda: _fake_rpc_client(blocks)):
        stats = qn._backfill_stream_gap(conn, [target], gap_from=101, gap_to=110)
    assert stats['requested_blocks'] == 10
    assert stats['blocks_scanned'] == 3
    assert stats['truncated'] is True


def test_backfill_no_rpc_configured_is_safe(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture):
    target = _make_target()
    conn = _CheckpointConn(targets=[target])
    with patch.object(qn, '_make_base_rpc_client', lambda: None), \
         caplog.at_level('WARNING', logger='services.api.app.quicknode_streams'):
        stats = qn._backfill_stream_gap(conn, [target], gap_from=101, gap_to=104)
    assert stats['blocks_scanned'] == 0
    assert stats['persisted'] == 0
    assert 'quicknode_stream_backfill_no_rpc' in caplog.text
    assert conn.telemetry_inserts == []


# ---------------------------------------------------------------------------
# Full webhook: a live batch that jumps past blocks triggers gap backfill that
# catches the missed tx — the end-to-end acceptance scenario.
# ---------------------------------------------------------------------------

def test_webhook_gap_triggers_backfill_that_catches_missed_tx(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
):
    monkeypatch.setenv('QUICKNODE_STREAMS_SECRET', SECRET)
    target = _make_target()
    # Pre-seed a checkpoint at block 100; the incoming batch is at block 105.
    conn = _CheckpointConn(targets=[target], checkpoint={
        'latest_stream_block': 100, 'last_processed_block': 100, 'stream_started_at_block': 90,
    })
    # The incoming (streamed) batch at block 105 — an unrelated tx, no match.
    streamed_tx = {'tx_hash': '0x' + 'cc' * 32, 'from': UNRELATED_ADDR, 'to': COUNTERPARTY, 'value': '1', 'block_number': 105}
    raw = json.dumps(streamed_tx).encode('utf-8')
    timestamp = _now_ts()
    signature = _sign(SECRET, nonce=NONCE, timestamp=timestamp, body=raw)
    # The skipped block 103 carries the missed matching tx, recovered via RPC.
    blocks = {
        101: _block_with_txs(101, []),
        102: _block_with_txs(102, []),
        103: _block_with_txs(103, [_rpc_tx(tx_hash=MISSED_TX_HASH, tx_from=WALLET_ADDR, block=103)]),
        104: _block_with_txs(104, []),
    }
    with patch.object(qn, 'pg_connection', lambda: _mock_pg(conn)), \
         patch.object(qn, 'ensure_pilot_schema', lambda _c: None), \
         patch.object(qn, '_make_base_rpc_client', lambda: _fake_rpc_client(blocks)), \
         patch.object(qn, '_create_wallet_transfer_alert_chain', lambda **kw: {'smoke_alert_id': None, 'sig_alert_id': None}), \
         caplog.at_level('INFO', logger='services.api.app.quicknode_streams'):
        result = qn.process_quicknode_base_stream_webhook(
            raw_body=raw, signature_header=signature, nonce_header=NONCE, timestamp_header=timestamp,
        )
    assert 'quicknode_stream_gap_detected from_block=100 to_block=105 missing_count=4' in caplog.text
    assert result['backfill']['persisted'] == 1
    # Exactly one row written — the backfilled missed tx (streamed tx did not match).
    assert len(conn.telemetry_inserts) == 1
    payload = json.loads(conn.telemetry_inserts[0][9])
    assert payload['detected_by'] == 'quicknode_stream_backfill'
    assert payload['tx_hash'] == MISSED_TX_HASH


def test_webhook_empty_block_batch_advances_checkpoint_without_targets(monkeypatch: pytest.MonkeyPatch):
    """A filtered block with no normalizable txs still advances the checkpoint and
    does NOT load targets or backfill when there is no gap."""
    monkeypatch.setenv('QUICKNODE_STREAMS_SECRET', SECRET)
    conn = _CheckpointConn(targets=[_make_target()], checkpoint={
        'latest_stream_block': 100, 'last_processed_block': 100, 'stream_started_at_block': 90,
    })
    payload = [{'block': {'number': hex(101), 'transactions': []}, 'receipts': []}]
    raw = json.dumps(payload).encode('utf-8')
    timestamp = _now_ts()
    signature = _sign(SECRET, nonce=NONCE, timestamp=timestamp, body=raw)
    with patch.object(qn, 'pg_connection', lambda: _mock_pg(conn)), \
         patch.object(qn, 'ensure_pilot_schema', lambda _c: None):
        result = qn.process_quicknode_base_stream_webhook(
            raw_body=raw, signature_header=signature, nonce_header=NONCE, timestamp_header=timestamp,
        )
    assert result['tx_count'] == 0
    assert result['targets_loaded'] == 0
    assert conn.checkpoint['last_processed_block'] == 101
    assert conn.telemetry_inserts == []


# ---------------------------------------------------------------------------
# Debug-tx endpoint stream_miss_reason classification (task requirement 5).
# ---------------------------------------------------------------------------

def _patch_rpc(tx, receipt=None):
    from services.api.app import evm_activity_provider as eap

    def _client_factory(tx_val, receipt_val):
        class _C:
            def __init__(self, urls):
                self.rpc_urls = urls

            def call(self, method, params):
                if method == 'eth_chainId':
                    return '0x2105'
                if method == 'eth_getTransactionByHash':
                    return tx_val
                if method == 'eth_getTransactionReceipt':
                    return receipt_val or {}
                return None
        return _C

    return [
        patch.object(eap, 'resolve_chain_rpc', lambda net: {
            'network': net, 'expected_chain_id': 8453, 'rpc_url': 'http://fake', 'rpc_urls': ['http://fake'],
        }),
        patch.object(eap, 'FailoverJsonRpcClient', _client_factory(tx, receipt)),
    ]


def _debug_rpc_tx(*, tx_from, block: int):
    return {'hash': MISSED_TX_HASH, 'from': tx_from, 'to': COUNTERPARTY, 'value': '0xde0b6b3a7640000', 'blockNumber': hex(block)}


def test_debug_tx_miss_reason_already_past_block():
    """The incident: stream started far ahead of the missed tx block."""
    target = _make_target()
    conn = _CheckpointConn(targets=[target], checkpoint={
        'latest_stream_block': 48388130, 'last_processed_block': 48388130, 'stream_started_at_block': 48388128,
    })
    tx = _debug_rpc_tx(tx_from=WALLET_ADDR, block=MISSED_BLOCK)
    p1, p2 = _patch_rpc(tx)
    with p1, p2, \
         patch.object(qn, 'pg_connection', lambda: _mock_pg(conn)), \
         patch.object(qn, 'ensure_pilot_schema', lambda _c: None):
        result = qn.run_quicknode_debug_tx(tx_hash=MISSED_TX_HASH, dry_run=True)
    assert result['stream_coverage'] == 'stream_already_past_block'
    assert result['stream_miss_reason'] == 'stream_already_past_block'
    assert result['conclusion'] == 'would_match_and_persist'


def test_debug_tx_miss_reason_not_at_block_yet():
    target = _make_target()
    conn = _CheckpointConn(targets=[target], checkpoint={
        'latest_stream_block': 100, 'last_processed_block': 100, 'stream_started_at_block': 50,
    })
    tx = _debug_rpc_tx(tx_from=WALLET_ADDR, block=500)
    p1, p2 = _patch_rpc(tx)
    with p1, p2, \
         patch.object(qn, 'pg_connection', lambda: _mock_pg(conn)), \
         patch.object(qn, 'ensure_pilot_schema', lambda _c: None):
        result = qn.run_quicknode_debug_tx(tx_hash=MISSED_TX_HASH, dry_run=True)
    assert result['stream_miss_reason'] == 'stream_not_at_block_yet'


def test_debug_tx_miss_reason_gap_detected_within_range():
    target = _make_target()
    conn = _CheckpointConn(targets=[target], checkpoint={
        'latest_stream_block': 600, 'last_processed_block': 600, 'stream_started_at_block': 50,
    })
    tx = _debug_rpc_tx(tx_from=WALLET_ADDR, block=300)
    p1, p2 = _patch_rpc(tx)
    with p1, p2, \
         patch.object(qn, 'pg_connection', lambda: _mock_pg(conn)), \
         patch.object(qn, 'ensure_pilot_schema', lambda _c: None):
        result = qn.run_quicknode_debug_tx(tx_hash=MISSED_TX_HASH, dry_run=True)
    assert result['stream_coverage'] == 'within_stream_range'
    assert result['stream_miss_reason'] == 'gap_detected'


def test_debug_tx_miss_reason_matcher_failed():
    target = _make_target()  # monitors WALLET_ADDR
    conn = _CheckpointConn(targets=[target], checkpoint={
        'latest_stream_block': 600, 'last_processed_block': 600, 'stream_started_at_block': 50,
    })
    tx = _debug_rpc_tx(tx_from=UNRELATED_ADDR, block=300)  # no match
    p1, p2 = _patch_rpc(tx)
    with p1, p2, \
         patch.object(qn, 'pg_connection', lambda: _mock_pg(conn)), \
         patch.object(qn, 'ensure_pilot_schema', lambda _c: None):
        result = qn.run_quicknode_debug_tx(tx_hash=MISSED_TX_HASH, dry_run=True)
    assert result['matched_count'] == 0
    assert result['stream_miss_reason'] == 'matcher_failed'


def test_debug_tx_miss_reason_duplicate_suppressed():
    target = _make_target()
    existing = {'id': str(uuid.uuid4()), 'event_type': 'native_transfer', 'detected_by': 'stable_rpc_polling'}
    conn = _CheckpointConn(targets=[target], existing_telemetry=existing, checkpoint={
        'latest_stream_block': 600, 'last_processed_block': 600, 'stream_started_at_block': 50,
    })
    tx = _debug_rpc_tx(tx_from=WALLET_ADDR, block=300)
    p1, p2 = _patch_rpc(tx)
    with p1, p2, \
         patch.object(qn, 'pg_connection', lambda: _mock_pg(conn)), \
         patch.object(qn, 'ensure_pilot_schema', lambda _c: None):
        result = qn.run_quicknode_debug_tx(tx_hash=MISSED_TX_HASH, dry_run=True)
    assert result['stream_miss_reason'] == 'duplicate_suppressed'


# ---------------------------------------------------------------------------
# Backend classification: the recovery tags must resolve to themselves (truthful
# UI label) and rank between the live paths and stable polling for dedupe.
# Mirrors apps/web/.../telemetry/detected-by.ts.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize('tag', ['quicknode_stream_backfill', 'quicknode_stream_debug_import'])
def test_recovery_tag_resolves_to_itself_not_unknown(tag: str):
    from services.api.app.worker_status import resolve_telemetry_detected_by

    assert resolve_telemetry_detected_by({'detected_by': tag}) == tag


@pytest.mark.parametrize('tag', ['quicknode_stream_backfill', 'quicknode_stream_debug_import'])
def test_recovery_tag_not_counted_as_realtime_proof(tag: str):
    from services.api.app.worker_status import is_realtime_detection_proof

    # A recovered (after-the-fact) detection must never be claimed as realtime proof.
    assert is_realtime_detection_proof(tag) is False


def test_recovery_tags_rank_between_realtime_and_stable():
    from services.api.app.worker_status import transfer_source_priority

    stream = transfer_source_priority('quicknode_stream')
    backfill = transfer_source_priority('quicknode_stream_backfill')
    debug_import = transfer_source_priority('quicknode_stream_debug_import')
    stable = transfer_source_priority('stable_rpc_polling')
    assert stream < backfill < debug_import < stable
