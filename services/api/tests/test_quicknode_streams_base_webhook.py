"""Tests for the QuickNode Streams (Base) webhook ingestion path.

POST /api/integrations/quicknode/streams/base
services/api/app/quicknode_streams.py

QuickNode Streams signs ``nonce + timestamp + raw_body`` with HMAC-SHA256
(hex digest) keyed by the Stream's security token, delivered via the
X-QN-Nonce / X-QN-Timestamp / X-QN-Signature headers. See:
https://www.quicknode.com/guides/quicknode-products/streams/validating-incoming-streams-webhook-messages

Covers:
  A. Valid HMAC signature (nonce+timestamp+body) is accepted.
  B. Invalid HMAC signature is rejected (401) and logs signature_failed — never
     a silent 200.
  C. Missing nonce/timestamp/signature header is rejected (400).
  D. Missing configured secret is rejected (503) — fails closed.
  E. A stale/future timestamp is rejected (400) — replay protection.
  F. A gzip-encoded body (Content-Encoding: gzip) is accepted, verified over
     the compressed wire bytes, then decompressed for parsing.
  G. A transaction matching a monitored wallet persists wallet_transfer_detected
     with detected_by=quicknode_stream and source_type=quicknode_stream.
  H. A transaction NOT matching any monitored wallet is not persisted.
  I. A duplicate transaction (already recorded, e.g. by stable_rpc_polling) is
     suppressed instead of creating a second row.
  J. UI label mapping: quicknode_stream => "QuickNode Stream" (Python-side
     canonical detected_by classification mirrors the frontend label map).
  K. No secret value ever appears in a raised HTTPException detail.
"""
from __future__ import annotations

import gzip
import hashlib
import hmac
import json
import time
import uuid
from contextlib import contextmanager
from unittest.mock import patch

import psycopg
import pytest
from fastapi import HTTPException

from services.api.app import quicknode_streams as qn


@pytest.fixture(autouse=True)
def _enable_realtime_streams_mode(monkeypatch):
    # This suite exercises the real-time QuickNode Streams processing path, which the
    # polling-only MVP (REALTIME_STREAMS_ENABLED unset) intentionally ignores. Opt into
    # real-time mode so the webhook processes payloads instead of short-circuiting; the
    # polling-only short-circuit itself is covered in test_quicknode_polling_only_mode.py.
    monkeypatch.setenv('REALTIME_STREAMS_ENABLED', 'true')


WALLET_ADDR = '0x5f6f35fd8b10c5576089f99c7c8c351deb851d1f'
COUNTERPARTY = '0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'
UNRELATED_ADDR = '0xbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb'
TX_HASH = '0x42eb6fb953a32dc80fef0f62b4eadfa0fed18c7129d68924cd65bdb37e25a51'
SECRET = 'whsec_test_secret_123'
NONCE = 'test-nonce-abc123'


def _sign(secret: str, *, nonce: str, timestamp: str, body: bytes) -> str:
    signing_input = nonce.encode('utf-8') + timestamp.encode('utf-8') + body
    return hmac.new(secret.encode('utf-8'), signing_input, hashlib.sha256).hexdigest()


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


class _FakeConnection:
    """Dispatches on SQL text, mirroring the fake-connection pattern used
    elsewhere in this test suite (see test_wallet_transfer_telemetry_durability.py).
    """

    def __init__(
        self,
        *,
        targets: list[dict],
        existing_telemetry: dict | None = None,
        asset_row: dict | None = None,
    ):
        self._targets = targets
        self._existing_telemetry = existing_telemetry
        # Served for _load_target_asset_context's `SELECT ... FROM assets` query,
        # so a wallet target whose monitored wallet lives on the linked asset
        # resolves through the real stable-polling resolver in these tests.
        self._asset_row = asset_row
        self.telemetry_inserts: list[tuple] = []
        self.commit_calls = 0

    def execute(self, query, params=None):
        q = (query or '').strip().lower()
        if 'from targets' in q:
            return _Rows(self._targets)
        if 'from assets' in q:
            return _Rows([self._asset_row] if self._asset_row else [])
        if 'from telemetry_events' in q and 'select' in q:
            return _Rows([self._existing_telemetry] if self._existing_telemetry else [])
        if q.startswith('insert into telemetry_events'):
            self.telemetry_inserts.append(tuple(params or ()))
            return _Rows([])
        return _Rows([])

    def commit(self):
        self.commit_calls += 1

    @contextmanager
    def transaction(self):
        yield


@contextmanager
def _mock_pg(connection: _FakeConnection):
    yield connection


def _body(tx: dict) -> bytes:
    return json.dumps(tx).encode('utf-8')


# ---------------------------------------------------------------------------
# Signature verification
# ---------------------------------------------------------------------------

def test_valid_hmac_signature_accepted(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv('QUICKNODE_STREAMS_SECRET', SECRET)
    raw = _body({'hash': TX_HASH})
    timestamp = _now_ts()
    signature = _sign(SECRET, nonce=NONCE, timestamp=timestamp, body=raw)
    qn.verify_quicknode_stream_signature(
        raw_body=raw, signature_header=signature, nonce_header=NONCE, timestamp_header=timestamp,
    )  # no raise


def test_invalid_hmac_signature_rejected(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv('QUICKNODE_STREAMS_SECRET', SECRET)
    raw = _body({'hash': TX_HASH})
    timestamp = _now_ts()
    with pytest.raises(HTTPException) as exc:
        qn.verify_quicknode_stream_signature(
            raw_body=raw, signature_header='deadbeef', nonce_header=NONCE, timestamp_header=timestamp,
        )
    # An invalid signature is an authentication failure (the signature is the
    # webhook's only credential), so it must be rejected as 401 — never 400 and
    # never a silent 200.
    assert exc.value.status_code == 401


def test_missing_signature_header_rejected(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv('QUICKNODE_STREAMS_SECRET', SECRET)
    raw = _body({'hash': TX_HASH})
    with pytest.raises(HTTPException) as exc:
        qn.verify_quicknode_stream_signature(
            raw_body=raw, signature_header=None, nonce_header=NONCE, timestamp_header=_now_ts(),
        )
    assert exc.value.status_code == 400


def test_missing_nonce_header_rejected(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv('QUICKNODE_STREAMS_SECRET', SECRET)
    raw = _body({'hash': TX_HASH})
    timestamp = _now_ts()
    signature = _sign(SECRET, nonce='', timestamp=timestamp, body=raw)
    with pytest.raises(HTTPException) as exc:
        qn.verify_quicknode_stream_signature(
            raw_body=raw, signature_header=signature, nonce_header=None, timestamp_header=timestamp,
        )
    assert exc.value.status_code == 400


def test_missing_timestamp_header_rejected(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv('QUICKNODE_STREAMS_SECRET', SECRET)
    raw = _body({'hash': TX_HASH})
    signature = _sign(SECRET, nonce=NONCE, timestamp='', body=raw)
    with pytest.raises(HTTPException) as exc:
        qn.verify_quicknode_stream_signature(
            raw_body=raw, signature_header=signature, nonce_header=NONCE, timestamp_header=None,
        )
    assert exc.value.status_code == 400


def test_missing_secret_rejected_fail_closed(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv('QUICKNODE_STREAMS_SECRET', raising=False)
    raw = _body({'hash': TX_HASH})
    with pytest.raises(HTTPException) as exc:
        qn.verify_quicknode_stream_signature(
            raw_body=raw, signature_header='anything', nonce_header=NONCE, timestamp_header=_now_ts(),
        )
    assert exc.value.status_code == 503


def test_old_timestamp_rejected_replay_protection(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv('QUICKNODE_STREAMS_SECRET', SECRET)
    raw = _body({'hash': TX_HASH})
    stale_timestamp = str(int(time.time()) - 3600)  # 1 hour old, well past default tolerance
    signature = _sign(SECRET, nonce=NONCE, timestamp=stale_timestamp, body=raw)
    with pytest.raises(HTTPException) as exc:
        qn.verify_quicknode_stream_signature(
            raw_body=raw, signature_header=signature, nonce_header=NONCE, timestamp_header=stale_timestamp,
        )
    assert exc.value.status_code == 400


def test_future_timestamp_rejected_replay_protection(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv('QUICKNODE_STREAMS_SECRET', SECRET)
    raw = _body({'hash': TX_HASH})
    future_timestamp = str(int(time.time()) + 3600)
    signature = _sign(SECRET, nonce=NONCE, timestamp=future_timestamp, body=raw)
    with pytest.raises(HTTPException) as exc:
        qn.verify_quicknode_stream_signature(
            raw_body=raw, signature_header=signature, nonce_header=NONCE, timestamp_header=future_timestamp,
        )
    assert exc.value.status_code == 400


def test_secret_never_appears_in_exception_detail(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv('QUICKNODE_STREAMS_SECRET', SECRET)
    raw = _body({'hash': TX_HASH})
    with pytest.raises(HTTPException) as exc:
        qn.verify_quicknode_stream_signature(
            raw_body=raw, signature_header='deadbeef', nonce_header=NONCE, timestamp_header=_now_ts(),
        )
    assert SECRET not in str(exc.value.detail)


_QN_LOGGER_NAME = 'services.api.app.quicknode_streams'


def test_invalid_signature_logs_signature_failed_and_returns_401(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
):
    """An invalid signature must (a) log quicknode_stream_signature_failed with a
    reason and (b) reject with 401 — the mirror of the signature_valid marker, so
    every rejected QuickNode POST is provable from Railway logs, not a silent 200."""
    monkeypatch.setenv('QUICKNODE_STREAMS_SECRET', SECRET)
    raw = _body({'hash': TX_HASH})
    with caplog.at_level('WARNING', logger=_QN_LOGGER_NAME):
        with pytest.raises(HTTPException) as exc:
            qn.verify_quicknode_stream_signature(
                raw_body=raw, signature_header='deadbeef', nonce_header=NONCE, timestamp_header=_now_ts(),
            )
    assert exc.value.status_code == 401
    assert 'quicknode_stream_signature_failed reason=signature_mismatch' in caplog.text
    assert 'quicknode_stream_signature_valid' not in caplog.text


@pytest.mark.parametrize(
    'signature_header,nonce_header,timestamp_header,expected_status,expected_reason',
    [
        (None, NONCE, _now_ts(), 400, 'missing_signature_headers'),
        ('sig', None, _now_ts(), 400, 'missing_signature_headers'),
        ('sig', NONCE, None, 400, 'missing_signature_headers'),
        ('sig', NONCE, str(int(time.time()) - 3600), 400, 'timestamp_out_of_tolerance'),
        ('sig', NONCE, 'not-a-number', 400, 'invalid_timestamp'),
    ],
)
def test_every_rejection_path_logs_signature_failed_reason(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
    signature_header, nonce_header, timestamp_header, expected_status, expected_reason,
):
    monkeypatch.setenv('QUICKNODE_STREAMS_SECRET', SECRET)
    raw = _body({'hash': TX_HASH})
    with caplog.at_level('WARNING', logger=_QN_LOGGER_NAME):
        with pytest.raises(HTTPException) as exc:
            qn.verify_quicknode_stream_signature(
                raw_body=raw,
                signature_header=signature_header,
                nonce_header=nonce_header,
                timestamp_header=timestamp_header,
            )
    assert exc.value.status_code == expected_status
    assert f'quicknode_stream_signature_failed reason={expected_reason}' in caplog.text


def test_missing_secret_logs_signature_failed(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture):
    monkeypatch.delenv('QUICKNODE_STREAMS_SECRET', raising=False)
    raw = _body({'hash': TX_HASH})
    with caplog.at_level('WARNING', logger=_QN_LOGGER_NAME):
        with pytest.raises(HTTPException) as exc:
            qn.verify_quicknode_stream_signature(
                raw_body=raw, signature_header='sig', nonce_header=NONCE, timestamp_header=_now_ts(),
            )
    assert exc.value.status_code == 503
    assert 'quicknode_stream_signature_failed reason=secret_not_configured' in caplog.text


def test_signature_failed_log_never_contains_secret_or_signature(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
):
    """The signature_failed diagnostic must carry only a reason token — never the
    configured secret nor the (attacker-supplied) signature header value."""
    monkeypatch.setenv('QUICKNODE_STREAMS_SECRET', SECRET)
    bogus_signature = 'abc123deadbeefsignaturevalue'
    raw = _body({'hash': TX_HASH})
    with caplog.at_level('INFO', logger=_QN_LOGGER_NAME):
        with pytest.raises(HTTPException):
            qn.verify_quicknode_stream_signature(
                raw_body=raw, signature_header=bogus_signature, nonce_header=NONCE, timestamp_header=_now_ts(),
            )
    assert SECRET not in caplog.text
    assert bogus_signature not in caplog.text


# ---------------------------------------------------------------------------
# Gzip body handling
# ---------------------------------------------------------------------------

def test_gzip_body_accepted_and_decompressed(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv('QUICKNODE_STREAMS_SECRET', SECRET)
    target = _make_target()
    conn = _FakeConnection(targets=[target])
    tx = {
        'tx_hash': TX_HASH,
        'from': WALLET_ADDR,
        'to': COUNTERPARTY,
        'value': '1000000000000000000',
        'block_number': 47286578,
        'chain_id': 8453,
    }
    plain_body = _body(tx)
    compressed_body = gzip.compress(plain_body)
    timestamp = _now_ts()
    # Signature is computed over the compressed wire bytes, not the plaintext.
    signature = _sign(SECRET, nonce=NONCE, timestamp=timestamp, body=compressed_body)

    with patch.object(qn, 'pg_connection', lambda: _mock_pg(conn)), \
         patch.object(qn, 'ensure_pilot_schema', lambda _c: None):
        result = qn.process_quicknode_base_stream_webhook(
            raw_body=compressed_body,
            signature_header=signature,
            nonce_header=NONCE,
            timestamp_header=timestamp,
            content_encoding='gzip',
        )

    assert result['received'] is True
    assert result['results'][0]['status'] == 'processed'
    assert len(conn.telemetry_inserts) == 1


def test_gzip_signature_over_plaintext_is_rejected(monkeypatch: pytest.MonkeyPatch):
    """Signing the decompressed body (instead of the wire bytes) must fail."""
    monkeypatch.setenv('QUICKNODE_STREAMS_SECRET', SECRET)
    plain_body = _body({'tx_hash': TX_HASH, 'from': WALLET_ADDR})
    compressed_body = gzip.compress(plain_body)
    timestamp = _now_ts()
    wrong_signature = _sign(SECRET, nonce=NONCE, timestamp=timestamp, body=plain_body)
    with pytest.raises(HTTPException) as exc:
        qn.verify_quicknode_stream_signature(
            raw_body=compressed_body, signature_header=wrong_signature, nonce_header=NONCE, timestamp_header=timestamp,
        )
    assert exc.value.status_code == 401


# ---------------------------------------------------------------------------
# Matching / persistence
# ---------------------------------------------------------------------------

def test_matching_tx_persists_wallet_transfer_detected(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv('QUICKNODE_STREAMS_SECRET', SECRET)
    target = _make_target()
    conn = _FakeConnection(targets=[target])
    tx = {
        'tx_hash': TX_HASH,
        'from': WALLET_ADDR,
        'to': COUNTERPARTY,
        'value': '1000000000000000000',
        'block_number': 47286578,
        'chain_id': 8453,
    }
    raw = _body(tx)
    timestamp = _now_ts()
    signature = _sign(SECRET, nonce=NONCE, timestamp=timestamp, body=raw)
    with patch.object(qn, 'pg_connection', lambda: _mock_pg(conn)), \
         patch.object(qn, 'ensure_pilot_schema', lambda _c: None):
        result = qn.process_quicknode_base_stream_webhook(
            raw_body=raw, signature_header=signature, nonce_header=NONCE, timestamp_header=timestamp,
        )

    assert result['received'] is True
    assert result['results'][0]['status'] == 'processed'
    assert result['results'][0]['detected_by'] == 'quicknode_stream'
    assert result['results'][0]['wallet_transfer_direction'] == 'outbound'
    assert conn.commit_calls == 1
    assert len(conn.telemetry_inserts) == 1
    inserted_payload_json = conn.telemetry_inserts[0][9]
    payload = json.loads(inserted_payload_json)
    assert payload['detected_by'] == 'quicknode_stream'
    assert payload['source_type'] == 'quicknode_stream'
    assert payload['event_type'] == 'wallet_transfer_detected'
    assert payload['tx_hash'] == TX_HASH


def test_non_matching_tx_is_not_persisted(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv('QUICKNODE_STREAMS_SECRET', SECRET)
    target = _make_target(wallet_address=WALLET_ADDR)
    conn = _FakeConnection(targets=[target])
    tx = {
        'tx_hash': TX_HASH,
        'from': UNRELATED_ADDR,
        'to': COUNTERPARTY,
        'value': '1',
        'block_number': 1,
        'chain_id': 8453,
    }
    raw = _body(tx)
    timestamp = _now_ts()
    signature = _sign(SECRET, nonce=NONCE, timestamp=timestamp, body=raw)
    with patch.object(qn, 'pg_connection', lambda: _mock_pg(conn)), \
         patch.object(qn, 'ensure_pilot_schema', lambda _c: None):
        result = qn.process_quicknode_base_stream_webhook(
            raw_body=raw, signature_header=signature, nonce_header=NONCE, timestamp_header=timestamp,
        )

    assert result['results'][0]['status'] == 'no_match'
    assert conn.telemetry_inserts == []
    assert conn.commit_calls == 0


def test_duplicate_tx_is_suppressed(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv('QUICKNODE_STREAMS_SECRET', SECRET)
    target = _make_target()
    existing_row = {'id': str(uuid.uuid4()), 'event_type': 'native_transfer', 'detected_by': 'stable_rpc_polling'}
    conn = _FakeConnection(targets=[target], existing_telemetry=existing_row)
    tx = {
        'tx_hash': TX_HASH,
        'from': WALLET_ADDR,
        'to': COUNTERPARTY,
        'value': '1',
        'block_number': 1,
        'chain_id': 8453,
    }
    raw = _body(tx)
    timestamp = _now_ts()
    signature = _sign(SECRET, nonce=NONCE, timestamp=timestamp, body=raw)
    with patch.object(qn, 'pg_connection', lambda: _mock_pg(conn)), \
         patch.object(qn, 'ensure_pilot_schema', lambda _c: None):
        result = qn.process_quicknode_base_stream_webhook(
            raw_body=raw, signature_header=signature, nonce_header=NONCE, timestamp_header=timestamp,
        )

    assert result['results'][0]['status'] == 'duplicate_suppressed'
    assert result['results'][0]['existing_detected_by'] == 'stable_rpc_polling'
    assert conn.telemetry_inserts == []
    assert conn.commit_calls == 0


# ---------------------------------------------------------------------------
# Real QuickNode "Block with Receipts" payload shape (Base Mainnet, batch
# size 1): [{"block": {..., "transactions": [...]}, "receipts": [...]}]
# ---------------------------------------------------------------------------

def _block_with_receipts_payload(*, tx: dict, block_number_hex: str = '0x2d1a2c6') -> list[dict]:
    return [
        {
            'block': {
                'hash': '0x' + 'ab' * 32,
                'number': block_number_hex,
                'parentHash': '0x' + 'cd' * 32,
                'timestamp': '0x64abcdef',
                'transactions': [tx],
            },
            'receipts': [
                {
                    'transactionHash': tx['hash'],
                    'status': '0x1',
                    'gasUsed': '0x5208',
                },
            ],
        },
    ]


def test_extract_tx_dicts_handles_block_with_receipts_shape():
    tx = {'hash': TX_HASH, 'from': WALLET_ADDR, 'to': COUNTERPARTY, 'value': '0x1'}
    payload = _block_with_receipts_payload(tx=tx)
    out = qn._extract_tx_dicts(payload)
    assert len(out) == 1
    assert out[0]['hash'] == TX_HASH
    assert out[0]['block_number'] == '0x2d1a2c6'
    assert out[0]['status'] == '0x1'
    assert out[0]['gas_used'] == '0x5208'


def test_block_with_receipts_shape_matched_and_persisted(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv('QUICKNODE_STREAMS_SECRET', SECRET)
    target = _make_target()
    conn = _FakeConnection(targets=[target])
    tx = {
        'hash': TX_HASH,
        'from': WALLET_ADDR,
        'to': COUNTERPARTY,
        'value': '0xde0b6b3a7640000',
        'gas': '0x5208',
        'gasPrice': '0x3b9aca00',
        'nonce': '0x1',
        'input': '0x',
        'transactionIndex': '0x0',
        'type': '0x2',
        'chainId': '0x2105',
    }
    payload = _block_with_receipts_payload(tx=tx)
    raw = json.dumps(payload).encode('utf-8')
    timestamp = _now_ts()
    signature = _sign(SECRET, nonce=NONCE, timestamp=timestamp, body=raw)

    with patch.object(qn, 'pg_connection', lambda: _mock_pg(conn)), \
         patch.object(qn, 'ensure_pilot_schema', lambda _c: None):
        result = qn.process_quicknode_base_stream_webhook(
            raw_body=raw, signature_header=signature, nonce_header=NONCE, timestamp_header=timestamp,
        )

    assert result['received'] is True
    assert result['results'][0]['status'] == 'processed'
    assert result['results'][0]['detected_by'] == 'quicknode_stream'
    assert len(conn.telemetry_inserts) == 1
    inserted_payload = json.loads(conn.telemetry_inserts[0][9])
    assert inserted_payload['block_number'] == 0x2d1a2c6
    assert inserted_payload['tx_hash'] == TX_HASH


# ---------------------------------------------------------------------------
# Mandatory diagnostic logging: every requirement below must be visible in
# logs so a QuickNode POST 200 with no useful quicknode_stream_* lines can
# never happen again.
# ---------------------------------------------------------------------------

_QN_LOGGER = 'services.api.app.quicknode_streams'


def test_signature_valid_log_emitted(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture):
    monkeypatch.setenv('QUICKNODE_STREAMS_SECRET', SECRET)
    raw = _body({'hash': TX_HASH})
    timestamp = _now_ts()
    signature = _sign(SECRET, nonce=NONCE, timestamp=timestamp, body=raw)
    with caplog.at_level('INFO', logger=_QN_LOGGER):
        qn.verify_quicknode_stream_signature(
            raw_body=raw, signature_header=signature, nonce_header=NONCE, timestamp_header=timestamp,
        )
    assert 'quicknode_stream_signature_valid' in caplog.text


def test_full_pipeline_logs_shape_normalized_targets_match_and_persist(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
):
    monkeypatch.setenv('QUICKNODE_STREAMS_SECRET', SECRET)
    target = _make_target()
    conn = _FakeConnection(targets=[target])
    tx = {'hash': TX_HASH, 'from': WALLET_ADDR, 'to': COUNTERPARTY, 'value': '0x1'}
    payload = _block_with_receipts_payload(tx=tx)
    raw = json.dumps(payload).encode('utf-8')
    timestamp = _now_ts()
    signature = _sign(SECRET, nonce=NONCE, timestamp=timestamp, body=raw)
    with patch.object(qn, 'pg_connection', lambda: _mock_pg(conn)), \
         patch.object(qn, 'ensure_pilot_schema', lambda _c: None), \
         caplog.at_level('INFO', logger=_QN_LOGGER):
        qn.process_quicknode_base_stream_webhook(
            raw_body=raw, signature_header=signature, nonce_header=NONCE, timestamp_header=timestamp,
        )
    assert 'quicknode_stream_handler_started' in caplog.text
    assert 'quicknode_stream_payload_parsed' in caplog.text
    assert 'decoded_type=list' in caplog.text
    assert 'quicknode_stream_payload_shape' in caplog.text
    assert 'top_level_type=list' in caplog.text
    assert 'first_block_keys=' in caplog.text
    assert 'first_tx_keys=' in caplog.text
    assert 'first_receipt_keys=' in caplog.text
    assert 'quicknode_stream_transactions_normalized count=1' in caplog.text
    assert 'sample_tx_hash_present=True' in caplog.text
    assert 'quicknode_stream_targets_loaded count=1 monitored_wallets_count=1' in caplog.text
    assert f'quicknode_stream_wallet_match tx_hash={TX_HASH} target_id={target["id"]} from_match=True' in caplog.text
    assert (
        f'quicknode_stream_event_persisted detected_by=quicknode_stream '
        f'tx_hash={TX_HASH} target_id={target["id"]}'
    ) in caplog.text


def test_no_targets_and_no_match_logs(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture):
    monkeypatch.setenv('QUICKNODE_STREAMS_SECRET', SECRET)
    conn = _FakeConnection(targets=[])
    tx = {'tx_hash': TX_HASH, 'from': WALLET_ADDR, 'to': COUNTERPARTY, 'value': '1'}
    raw = _body(tx)
    timestamp = _now_ts()
    signature = _sign(SECRET, nonce=NONCE, timestamp=timestamp, body=raw)
    with patch.object(qn, 'pg_connection', lambda: _mock_pg(conn)), \
         patch.object(qn, 'ensure_pilot_schema', lambda _c: None), \
         caplog.at_level('INFO', logger=_QN_LOGGER):
        result = qn.process_quicknode_base_stream_webhook(
            raw_body=raw, signature_header=signature, nonce_header=NONCE, timestamp_header=timestamp,
        )
    assert result['results'][0]['status'] == 'no_match'
    assert 'quicknode_stream_no_targets_loaded' in caplog.text
    assert 'quicknode_stream_no_match tx_count=1 target_count=0' in caplog.text


def test_no_transactions_normalized_log_when_payload_has_no_valid_tx(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
):
    monkeypatch.setenv('QUICKNODE_STREAMS_SECRET', SECRET)
    raw = _body({'unrelated': 'field'})
    timestamp = _now_ts()
    signature = _sign(SECRET, nonce=NONCE, timestamp=timestamp, body=raw)
    with caplog.at_level('INFO', logger=_QN_LOGGER):
        result = qn.process_quicknode_base_stream_webhook(
            raw_body=raw, signature_header=signature, nonce_header=NONCE, timestamp_header=timestamp,
        )
    assert result['results'] == []
    assert 'quicknode_stream_no_transactions_normalized reason=raw_transactions_missing_required_fields' in caplog.text


def test_duplicate_suppressed_log_includes_existing_detected_by(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
):
    monkeypatch.setenv('QUICKNODE_STREAMS_SECRET', SECRET)
    target = _make_target()
    existing_row = {'id': str(uuid.uuid4()), 'event_type': 'native_transfer', 'detected_by': 'stable_rpc_polling'}
    conn = _FakeConnection(targets=[target], existing_telemetry=existing_row)
    tx = {'tx_hash': TX_HASH, 'from': WALLET_ADDR, 'to': COUNTERPARTY, 'value': '1'}
    raw = _body(tx)
    timestamp = _now_ts()
    signature = _sign(SECRET, nonce=NONCE, timestamp=timestamp, body=raw)
    with patch.object(qn, 'pg_connection', lambda: _mock_pg(conn)), \
         patch.object(qn, 'ensure_pilot_schema', lambda _c: None), \
         caplog.at_level('INFO', logger=_QN_LOGGER):
        qn.process_quicknode_base_stream_webhook(
            raw_body=raw, signature_header=signature, nonce_header=NONCE, timestamp_header=timestamp,
        )
    assert f'quicknode_stream_duplicate_suppressed tx_hash={TX_HASH} existing_detected_by=stable_rpc_polling' in caplog.text


def test_handler_started_log_emitted_even_when_signature_fails(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
):
    """quicknode_stream_handler_started must fire before signature verification,
    so a rejected (bad-signature) request is still provable from the handler's
    own logs — not only the route-hit line."""
    monkeypatch.setenv('QUICKNODE_STREAMS_SECRET', SECRET)
    raw = _body({'hash': TX_HASH, 'from': WALLET_ADDR})
    with caplog.at_level('INFO', logger=_QN_LOGGER):
        with pytest.raises(HTTPException) as exc:
            qn.process_quicknode_base_stream_webhook(
                raw_body=raw, signature_header='not-the-right-signature',
                nonce_header=NONCE, timestamp_header=_now_ts(),
            )
    assert exc.value.status_code == 401
    assert 'quicknode_stream_handler_started' in caplog.text
    assert f'raw_body_bytes={len(raw)}' in caplog.text
    assert 'has_signature=True' in caplog.text
    # Fail-closed: a rejected signature must not emit the "valid" marker, but it
    # must emit the "failed" marker so the rejection is provable from logs.
    assert 'quicknode_stream_signature_valid' not in caplog.text
    assert 'quicknode_stream_signature_failed reason=signature_mismatch' in caplog.text


def test_json_parsed_log_emitted_after_signature_check(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
):
    monkeypatch.setenv('QUICKNODE_STREAMS_SECRET', SECRET)
    raw = _body({'unrelated': 'field'})
    timestamp = _now_ts()
    signature = _sign(SECRET, nonce=NONCE, timestamp=timestamp, body=raw)
    with caplog.at_level('INFO', logger=_QN_LOGGER):
        qn.process_quicknode_base_stream_webhook(
            raw_body=raw, signature_header=signature, nonce_header=NONCE, timestamp_header=timestamp,
        )
    assert 'quicknode_stream_payload_parsed' in caplog.text
    assert 'decoded_type=dict' in caplog.text
    assert f'decoded_bytes={len(raw)}' in caplog.text


def test_event_persisted_log_includes_target_id(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
):
    monkeypatch.setenv('QUICKNODE_STREAMS_SECRET', SECRET)
    target = _make_target()
    conn = _FakeConnection(targets=[target])
    tx = {'tx_hash': TX_HASH, 'from': WALLET_ADDR, 'to': COUNTERPARTY, 'value': '1'}
    raw = _body(tx)
    timestamp = _now_ts()
    signature = _sign(SECRET, nonce=NONCE, timestamp=timestamp, body=raw)
    with patch.object(qn, 'pg_connection', lambda: _mock_pg(conn)), \
         patch.object(qn, 'ensure_pilot_schema', lambda _c: None), \
         caplog.at_level('INFO', logger=_QN_LOGGER):
        qn.process_quicknode_base_stream_webhook(
            raw_body=raw, signature_header=signature, nonce_header=NONCE, timestamp_header=timestamp,
        )
    assert (
        f'quicknode_stream_event_persisted detected_by=quicknode_stream '
        f'tx_hash={TX_HASH} target_id={target["id"]}'
    ) in caplog.text


def test_qn_module_logger_pinned_to_info_survives_global_warning():
    """The quicknode_stream_* diagnostics must not be silenced by a global
    LOG_LEVEL=WARNING: the module logger is pinned to INFO at import."""
    import logging

    assert qn.logger.level == logging.INFO


def test_full_pipeline_logs_never_leak_secret_or_payload_values(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
):
    """End-to-end: a processed payload logs shapes/counts/hashes/ids only — never
    the configured secret and never raw payload *values* (task: "no secrets or
    full payloads in logs"). Key *names* may appear in the shape line; values
    must not."""
    monkeypatch.setenv('QUICKNODE_STREAMS_SECRET', SECRET)
    target = _make_target()
    conn = _FakeConnection(targets=[target])
    secret_value = 'DO_NOT_LOG_THIS_VALUE_9f8e7d'
    tx = {
        'hash': TX_HASH,
        'from': WALLET_ADDR,
        'to': COUNTERPARTY,
        'value': '0x1',
        'sensitive_memo': secret_value,  # a value the diagnostics must never echo
    }
    payload = _block_with_receipts_payload(tx=tx)
    raw = json.dumps(payload).encode('utf-8')
    timestamp = _now_ts()
    signature = _sign(SECRET, nonce=NONCE, timestamp=timestamp, body=raw)
    with patch.object(qn, 'pg_connection', lambda: _mock_pg(conn)), \
         patch.object(qn, 'ensure_pilot_schema', lambda _c: None), \
         caplog.at_level('INFO', logger=_QN_LOGGER):
        result = qn.process_quicknode_base_stream_webhook(
            raw_body=raw, signature_header=signature, nonce_header=NONCE, timestamp_header=timestamp,
        )
    assert result['results'][0]['status'] == 'processed'
    # The secret and the raw payload value must never appear in any log line.
    assert SECRET not in caplog.text
    assert secret_value not in caplog.text
    assert signature not in caplog.text


def test_200_response_always_coincides_with_route_hit_log(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
):
    """Task guard: if the endpoint returns 200, the route-hit marker MUST be
    present. A 200 with no quicknode_stream_route_hit line is the exact failure
    this test exists to catch (it would mean an un-instrumented build is live)."""
    from fastapi.testclient import TestClient

    from services.api.app import main as api_main

    monkeypatch.setattr(api_main, 'process_quicknode_base_stream_webhook', lambda **kw: {'received': True, 'results': []})
    monkeypatch.setattr(api_main, 'with_auth_schema_json', lambda handler: handler())
    client = TestClient(api_main.app, raise_server_exceptions=False)
    body = _body({'tx_hash': TX_HASH})
    with caplog.at_level('INFO', logger='services.api.app.main'):
        response = client.post(
            '/api/integrations/quicknode/streams/base',
            content=body,
            headers={
                'content-type': 'application/json',
                'x-qn-signature': 'anything',
                'x-qn-nonce': NONCE,
                'x-qn-timestamp': _now_ts(),
            },
        )
    assert response.status_code == 200
    # The invariant the production incident violated: 200 without a route-hit log.
    assert 'quicknode_stream_route_hit' in caplog.text


# ---------------------------------------------------------------------------
# Route-hit logging (services/api/app/main.py) — must fire before any return,
# including on signature/validation failures.
# ---------------------------------------------------------------------------

def test_route_post_logs_route_hit_on_success(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture):
    from fastapi.testclient import TestClient

    from services.api.app import main as api_main

    monkeypatch.setattr(api_main, 'process_quicknode_base_stream_webhook', lambda **kw: {'received': True, 'results': []})
    monkeypatch.setattr(api_main, 'with_auth_schema_json', lambda handler: handler())
    client = TestClient(api_main.app, raise_server_exceptions=False)
    body = _body({'tx_hash': TX_HASH})
    with caplog.at_level('INFO', logger='services.api.app.main'):
        response = client.post(
            '/api/integrations/quicknode/streams/base',
            content=body,
            headers={
                'content-type': 'application/json',
                'x-qn-signature': 'anything',
                'x-qn-nonce': NONCE,
                'x-qn-timestamp': _now_ts(),
            },
        )
    assert response.status_code == 200
    assert 'quicknode_stream_route_hit' in caplog.text
    assert 'has_nonce=True' in caplog.text
    assert 'has_timestamp=True' in caplog.text
    assert 'has_signature=True' in caplog.text
    assert f'content_length={len(body)}' in caplog.text


def test_route_post_logs_route_hit_even_when_signature_invalid(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
):
    from fastapi.testclient import TestClient

    from services.api.app import main as api_main

    monkeypatch.setenv('QUICKNODE_STREAMS_SECRET', SECRET)
    client = TestClient(api_main.app, raise_server_exceptions=False)
    body = _body({'tx_hash': TX_HASH, 'from': WALLET_ADDR})
    with caplog.at_level('INFO', logger='services.api.app.main'):
        response = client.post(
            '/api/integrations/quicknode/streams/base',
            content=body,
            headers={
                'content-type': 'application/json',
                'x-qn-signature': 'not-the-right-signature',
                'x-qn-nonce': NONCE,
                'x-qn-timestamp': _now_ts(),
            },
        )
    assert response.status_code == 401
    assert 'quicknode_stream_route_hit' in caplog.text


# ---------------------------------------------------------------------------
# Deployed-build marker: lets an operator confirm from Railway startup logs
# alone that the running API commit includes this webhook code (requirement:
# "Confirm the deployed API commit includes this code").
# ---------------------------------------------------------------------------

def test_startup_emits_quicknode_streams_webhook_version(caplog: pytest.LogCaptureFixture):
    from services.api.app import main as api_main

    with caplog.at_level('INFO', logger='services.api.app.main.quicknode_streams'):
        api_main.emit_quicknode_streams_webhook_version()
    assert 'quicknode_streams_webhook_version=' in caplog.text
    assert api_main.QUICKNODE_STREAMS_WEBHOOK_VERSION in caplog.text
    assert 'git_commit=' in caplog.text


def test_startup_version_marker_logger_pinned_to_info():
    """The route-hit and startup version markers ride an INFO-pinned child
    logger so they survive a global LOG_LEVEL=WARNING in production."""
    import logging

    from services.api.app import main as api_main

    assert api_main._quicknode_streams_logger.level == logging.INFO


# ---------------------------------------------------------------------------
# UI label (backend canonical classification stays in sync with the frontend
# DETECTED_BY_LABELS map — apps/web/.../telemetry/detected-by.ts)
# ---------------------------------------------------------------------------

def test_quicknode_stream_registered_as_realtime_detected_by():
    from services.api.app.worker_status import REALTIME_DETECTED_BY, detected_by_from_ingestion_source

    assert 'quicknode_stream' in REALTIME_DETECTED_BY
    assert detected_by_from_ingestion_source('quicknode_stream') == 'quicknode_stream'


# ---------------------------------------------------------------------------
# Route wiring (mirrors test_billing_paddle_webhook_route.py)
# ---------------------------------------------------------------------------

def test_route_get_health_check():
    from fastapi.testclient import TestClient

    from services.api.app import main as api_main

    client = TestClient(api_main.app, raise_server_exceptions=False)
    response = client.get('/api/integrations/quicknode/streams/base')
    assert response.status_code == 200
    assert response.json()['status'] == 'quicknode_streams_base_endpoint_ready'


def test_route_post_invalid_signature_returns_401_not_200(monkeypatch: pytest.MonkeyPatch):
    """A QuickNode POST with a bad signature must be rejected as 401 — never a
    silent 200 that would let an unverified payload look accepted."""
    from fastapi.testclient import TestClient

    from services.api.app import main as api_main

    monkeypatch.setenv('QUICKNODE_STREAMS_SECRET', SECRET)
    client = TestClient(api_main.app, raise_server_exceptions=False)
    body = _body({'tx_hash': TX_HASH, 'from': WALLET_ADDR})
    response = client.post(
        '/api/integrations/quicknode/streams/base',
        content=body,
        headers={
            'content-type': 'application/json',
            'x-qn-signature': 'not-the-right-signature',
            'x-qn-nonce': NONCE,
            'x-qn-timestamp': _now_ts(),
        },
    )
    assert response.status_code == 401
    assert response.status_code != 200


def test_route_post_missing_nonce_rejected(monkeypatch: pytest.MonkeyPatch):
    from fastapi.testclient import TestClient

    from services.api.app import main as api_main

    monkeypatch.setenv('QUICKNODE_STREAMS_SECRET', SECRET)
    client = TestClient(api_main.app, raise_server_exceptions=False)
    body = _body({'tx_hash': TX_HASH, 'from': WALLET_ADDR})
    timestamp = _now_ts()
    signature = _sign(SECRET, nonce=NONCE, timestamp=timestamp, body=body)
    response = client.post(
        '/api/integrations/quicknode/streams/base',
        content=body,
        headers={
            'content-type': 'application/json',
            'x-qn-signature': signature,
            'x-qn-timestamp': timestamp,
            # x-qn-nonce intentionally omitted
        },
    )
    assert response.status_code == 400


def test_route_post_valid_signature_reaches_handler(monkeypatch: pytest.MonkeyPatch):
    from fastapi.testclient import TestClient

    from services.api.app import main as api_main

    monkeypatch.setattr(api_main, 'process_quicknode_base_stream_webhook', lambda **kw: {'received': True, 'results': []})
    monkeypatch.setattr(api_main, 'with_auth_schema_json', lambda handler: handler())
    client = TestClient(api_main.app, raise_server_exceptions=False)
    body = _body({'tx_hash': TX_HASH})
    response = client.post(
        '/api/integrations/quicknode/streams/base',
        content=body,
        headers={
            'content-type': 'application/json',
            'x-qn-signature': 'anything',
            'x-qn-nonce': NONCE,
            'x-qn-timestamp': _now_ts(),
        },
    )
    assert response.status_code == 200
    assert response.json() == {'received': True, 'results': []}


# ---------------------------------------------------------------------------
# Envelope / nested-list payload shapes. QuickNode Streams wraps the dataset
# output in {"data": [...], "metadata": {...}} and, with some batch/filter
# configs, nests `data` one extra list level. Both must still yield
# transactions — a regression guard for the production incident where a POST
# returned 200 OK but silently normalized to tx_count=0, so detected_by stayed
# stable_rpc_polling and no QuickNode Stream row ever appeared.
# ---------------------------------------------------------------------------

def test_extract_tx_dicts_handles_data_envelope_shape():
    tx = {'hash': TX_HASH, 'from': WALLET_ADDR, 'to': COUNTERPARTY, 'value': '0x1'}
    payload = {'data': _block_with_receipts_payload(tx=tx), 'metadata': {'dataset': 'block_with_receipts'}}
    out = qn._extract_tx_dicts(payload)
    assert len(out) == 1
    assert out[0]['hash'] == TX_HASH
    assert out[0]['block_number'] == '0x2d1a2c6'


def test_extract_tx_dicts_handles_nested_list_data_envelope():
    tx = {'hash': TX_HASH, 'from': WALLET_ADDR, 'to': COUNTERPARTY, 'value': '0x1'}
    # data nested one extra list level: {"data": [[ {block, receipts} ]]}
    payload = {'data': [_block_with_receipts_payload(tx=tx)], 'metadata': {}}
    out = qn._extract_tx_dicts(payload)
    assert len(out) == 1
    assert out[0]['hash'] == TX_HASH


def test_extract_tx_dicts_does_not_descend_into_tx_data_calldata():
    # A flat tx whose own `data` is hex calldata must be treated as a tx, not an
    # envelope to unwrap (otherwise the tx would be dropped).
    tx = {'hash': TX_HASH, 'from': WALLET_ADDR, 'to': COUNTERPARTY, 'value': '0x1', 'data': '0xabcdef'}
    out = qn._extract_tx_dicts([tx])
    assert len(out) == 1
    assert out[0]['hash'] == TX_HASH


def test_data_envelope_shape_matched_and_persisted(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv('QUICKNODE_STREAMS_SECRET', SECRET)
    target = _make_target()
    conn = _FakeConnection(targets=[target])
    tx = {'hash': TX_HASH, 'from': WALLET_ADDR, 'to': COUNTERPARTY, 'value': '0xde0b6b3a7640000'}
    payload = {'data': _block_with_receipts_payload(tx=tx), 'metadata': {'network': 'base-mainnet'}}
    raw = json.dumps(payload).encode('utf-8')
    timestamp = _now_ts()
    signature = _sign(SECRET, nonce=NONCE, timestamp=timestamp, body=raw)
    with patch.object(qn, 'pg_connection', lambda: _mock_pg(conn)), \
         patch.object(qn, 'ensure_pilot_schema', lambda _c: None):
        result = qn.process_quicknode_base_stream_webhook(
            raw_body=raw, signature_header=signature, nonce_header=NONCE, timestamp_header=timestamp,
        )
    assert result['results'][0]['status'] == 'processed'
    assert result['results'][0]['detected_by'] == 'quicknode_stream'
    assert result['tx_count'] == 1
    assert result['persisted'] == 1
    assert len(conn.telemetry_inserts) == 1


# ---------------------------------------------------------------------------
# Response summary: every processed (200) QuickNode POST returns a safe
# aggregate {ok, tx_count, targets_loaded, matched, persisted, duplicates,
# skipped}, and logs the same as quicknode_stream_summary.
# ---------------------------------------------------------------------------

def test_summary_response_on_match_and_persist(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv('QUICKNODE_STREAMS_SECRET', SECRET)
    target = _make_target()
    conn = _FakeConnection(targets=[target])
    tx = {'tx_hash': TX_HASH, 'from': WALLET_ADDR, 'to': COUNTERPARTY, 'value': '1'}
    raw = _body(tx)
    timestamp = _now_ts()
    signature = _sign(SECRET, nonce=NONCE, timestamp=timestamp, body=raw)
    with patch.object(qn, 'pg_connection', lambda: _mock_pg(conn)), \
         patch.object(qn, 'ensure_pilot_schema', lambda _c: None):
        result = qn.process_quicknode_base_stream_webhook(
            raw_body=raw, signature_header=signature, nonce_header=NONCE, timestamp_header=timestamp,
        )
    assert result['ok'] is True
    assert result['tx_count'] == 1
    assert result['targets_loaded'] == 1
    assert result['matched'] == 1
    assert result['persisted'] == 1
    assert result['duplicates'] == 0
    assert result['skipped'] == 0


def test_summary_response_on_no_match(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv('QUICKNODE_STREAMS_SECRET', SECRET)
    target = _make_target()
    conn = _FakeConnection(targets=[target])
    tx = {'tx_hash': TX_HASH, 'from': UNRELATED_ADDR, 'to': COUNTERPARTY, 'value': '1'}
    raw = _body(tx)
    timestamp = _now_ts()
    signature = _sign(SECRET, nonce=NONCE, timestamp=timestamp, body=raw)
    with patch.object(qn, 'pg_connection', lambda: _mock_pg(conn)), \
         patch.object(qn, 'ensure_pilot_schema', lambda _c: None):
        result = qn.process_quicknode_base_stream_webhook(
            raw_body=raw, signature_header=signature, nonce_header=NONCE, timestamp_header=timestamp,
        )
    assert result['ok'] is True
    assert result['tx_count'] == 1
    assert result['targets_loaded'] == 1
    assert result['matched'] == 0
    assert result['persisted'] == 0
    assert result['skipped'] == 1


def test_summary_response_on_duplicate(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv('QUICKNODE_STREAMS_SECRET', SECRET)
    target = _make_target()
    existing_row = {'id': str(uuid.uuid4()), 'event_type': 'native_transfer', 'detected_by': 'stable_rpc_polling'}
    conn = _FakeConnection(targets=[target], existing_telemetry=existing_row)
    tx = {'tx_hash': TX_HASH, 'from': WALLET_ADDR, 'to': COUNTERPARTY, 'value': '1'}
    raw = _body(tx)
    timestamp = _now_ts()
    signature = _sign(SECRET, nonce=NONCE, timestamp=timestamp, body=raw)
    with patch.object(qn, 'pg_connection', lambda: _mock_pg(conn)), \
         patch.object(qn, 'ensure_pilot_schema', lambda _c: None):
        result = qn.process_quicknode_base_stream_webhook(
            raw_body=raw, signature_header=signature, nonce_header=NONCE, timestamp_header=timestamp,
        )
    assert result['matched'] == 1
    assert result['duplicates'] == 1
    assert result['persisted'] == 0
    assert result['skipped'] == 0


def test_summary_response_when_no_transactions(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv('QUICKNODE_STREAMS_SECRET', SECRET)
    raw = _body({'unrelated': 'field'})
    timestamp = _now_ts()
    signature = _sign(SECRET, nonce=NONCE, timestamp=timestamp, body=raw)
    result = qn.process_quicknode_base_stream_webhook(
        raw_body=raw, signature_header=signature, nonce_header=NONCE, timestamp_header=timestamp,
    )
    assert result['ok'] is True
    assert result['tx_count'] == 0
    assert result['targets_loaded'] == 0
    assert result['matched'] == 0
    assert result['persisted'] == 0
    assert result['results'] == []


def test_summary_log_emitted(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture):
    monkeypatch.setenv('QUICKNODE_STREAMS_SECRET', SECRET)
    target = _make_target()
    conn = _FakeConnection(targets=[target])
    tx = {'tx_hash': TX_HASH, 'from': WALLET_ADDR, 'to': COUNTERPARTY, 'value': '1'}
    raw = _body(tx)
    timestamp = _now_ts()
    signature = _sign(SECRET, nonce=NONCE, timestamp=timestamp, body=raw)
    with patch.object(qn, 'pg_connection', lambda: _mock_pg(conn)), \
         patch.object(qn, 'ensure_pilot_schema', lambda _c: None), \
         caplog.at_level('INFO', logger=_QN_LOGGER):
        qn.process_quicknode_base_stream_webhook(
            raw_body=raw, signature_header=signature, nonce_header=NONCE, timestamp_header=timestamp,
        )
    assert (
        'quicknode_stream_summary ok=True tx_count=1 targets_loaded=1 matched=1 '
        'persisted=1 duplicates=0 skipped=0'
    ) in caplog.text


# ---------------------------------------------------------------------------
# No-match diagnostics: on a no-match, log the normalized from/to (public
# on-chain addresses) and the target wallet fingerprint (last4/hash8) only —
# never the full monitored wallet.
# ---------------------------------------------------------------------------

def test_no_match_detail_logs_from_to_and_fingerprint_not_full_wallet(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
):
    monkeypatch.setenv('QUICKNODE_STREAMS_SECRET', SECRET)
    target = _make_target()  # monitors WALLET_ADDR
    conn = _FakeConnection(targets=[target])
    tx = {'tx_hash': TX_HASH, 'from': UNRELATED_ADDR, 'to': COUNTERPARTY, 'value': '1'}
    raw = _body(tx)
    timestamp = _now_ts()
    signature = _sign(SECRET, nonce=NONCE, timestamp=timestamp, body=raw)
    with patch.object(qn, 'pg_connection', lambda: _mock_pg(conn)), \
         patch.object(qn, 'ensure_pilot_schema', lambda _c: None), \
         caplog.at_level('DEBUG', logger=_QN_LOGGER):
        qn.process_quicknode_base_stream_webhook(
            raw_body=raw, signature_header=signature, nonce_header=NONCE, timestamp_header=timestamp,
        )
    # Per-tx no-match detail was DEMOTED to DEBUG (production flood removed); it is
    # still emitted — with public from/to and a wallet FINGERPRINT, never the full
    # wallet — and captured here by raising the module logger to DEBUG.
    assert 'quicknode_stream_no_match_detail' in caplog.text
    assert f'from={UNRELATED_ADDR}' in caplog.text
    assert f'to={COUNTERPARTY}' in caplog.text
    # The monitored wallet's last4 appears as a fingerprint, but the FULL
    # monitored wallet address must never be printed.
    assert WALLET_ADDR not in caplog.text
    assert f'{WALLET_ADDR[-4:]}/' in caplog.text


def test_no_match_detail_log_is_capped(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture):
    monkeypatch.setenv('QUICKNODE_STREAMS_SECRET', SECRET)
    target = _make_target()
    conn = _FakeConnection(targets=[target])
    txs = [
        {'hash': '0x' + f'{i:064x}', 'from': UNRELATED_ADDR, 'to': COUNTERPARTY, 'value': '0x1'}
        for i in range(qn._NO_MATCH_DETAIL_LOG_LIMIT + 5)
    ]
    payload = [{'block': {'number': '0x1', 'transactions': txs}, 'receipts': []}]
    raw = json.dumps(payload).encode('utf-8')
    timestamp = _now_ts()
    signature = _sign(SECRET, nonce=NONCE, timestamp=timestamp, body=raw)
    with patch.object(qn, 'pg_connection', lambda: _mock_pg(conn)), \
         patch.object(qn, 'ensure_pilot_schema', lambda _c: None), \
         caplog.at_level('DEBUG', logger=_QN_LOGGER):
        result = qn.process_quicknode_base_stream_webhook(
            raw_body=raw, signature_header=signature, nonce_header=NONCE, timestamp_header=timestamp,
        )
    assert result['skipped'] == qn._NO_MATCH_DETAIL_LOG_LIMIT + 5
    # Per-tx detail (now DEBUG) is still capped; the aggregate no_match line stays at INFO.
    assert caplog.text.count('quicknode_stream_no_match_detail') == qn._NO_MATCH_DETAIL_LOG_LIMIT
    assert 'quicknode_stream_no_match tx_count=' in caplog.text


def test_wallet_fingerprint_is_last4_slash_hash_and_hides_full_wallet():
    fp = qn._wallet_fingerprint(WALLET_ADDR)
    last4, sep, hash8 = fp.partition('/')
    assert sep == '/'
    assert last4 == WALLET_ADDR[-4:]
    assert len(hash8) == 8
    assert WALLET_ADDR not in fp


def test_wallet_fingerprint_none_for_empty():
    assert qn._wallet_fingerprint(None) == 'none'
    assert qn._wallet_fingerprint('') == 'none'


# ---------------------------------------------------------------------------
# Monitored-wallet resolution from the linked asset — the reported production
# defect. The active Base target loaded fine (targets_loaded count=1) but its
# canonical wallet_address column was empty and the monitored wallet lived on
# the linked asset, so QuickNode logged monitored_wallets_count=0 /
# target_wallets=['none'] / matched=0 while stable RPC polling resolved the same
# wallet. The fix reuses resolve_monitored_wallet + stable polling's
# _load_target_asset_context so both paths agree. These tests pin that behavior.
# ---------------------------------------------------------------------------

# The exact target/wallet from the production incident report.
PROD_TARGET_ID = 'e7851a52-8fb1-48cd-84a3-d033f591c5dd'


def _make_wallet_on_asset_target(*, target_id: str | None = None, asset_id: str | None = None) -> dict:
    """A wallet target with an EMPTY wallet_address whose monitored wallet is
    carried on the linked asset — the shape that produced monitored_wallets_count=0."""
    return {
        'id': target_id or str(uuid.uuid4()),
        'workspace_id': str(uuid.uuid4()),
        'name': 'Treasury Base Wallet',
        'target_type': 'wallet',
        'chain_network': 'base',
        'chain_id': 8453,
        'wallet_address': None,          # canonical column empty (the bug trigger)
        'contract_identifier': None,
        'asset_id': asset_id or str(uuid.uuid4()),
        'target_metadata': {},
        'monitoring_enabled': True,
        'enabled': True,
        'is_active': True,
    }


def _asset_row(*, asset_id: str, wallet: str = WALLET_ADDR) -> dict:
    """Asset row shaped like _load_target_asset_context's SELECT, carrying the
    monitored wallet in asset_identifier — the same fallback location stable RPC
    polling reads (see test_wallet_address_resolution.test_resolve_production_wallet_from_fallback)."""
    return {
        'id': asset_id,
        'name': 'Treasury Wallet',
        'asset_class': 'wallet',
        'asset_symbol': 'TRW',
        'identifier': wallet,
        'asset_identifier': wallet,
        'token_contract_address': None,
        'chain_network': 'base',
        'treasury_ops_wallets': [],
        'custody_wallets': [],
        'oracle_sources': [],
        'venue_labels': [],
        'expected_flow_patterns': [],
        'expected_counterparties': [],
        'expected_approval_patterns': {},
        'expected_liquidity_baseline': {},
        'expected_oracle_freshness_seconds': 0,
        'expected_oracle_update_cadence_seconds': 0,
        'baseline_status': None,
        'baseline_source': None,
        'baseline_updated_at': None,
        'baseline_confidence': None,
        'baseline_coverage': None,
    }


def test_resolve_target_wallet_from_linked_asset_matches_stable_polling_fixture():
    """QuickNode resolves the monitored wallet from the linked asset — the same
    asset_identifier fallback stable RPC polling uses — and writes it back onto
    the target so the matcher/persistence observe the identical wallet."""
    target = _make_wallet_on_asset_target(target_id=PROD_TARGET_ID)
    conn = _FakeConnection(targets=[target], asset_row=_asset_row(asset_id=target['asset_id']))
    # Empty canonical column: no direct resolution before the asset is loaded.
    assert qn.resolve_monitored_wallet(dict(target)) is None
    wallet, source, reason = qn._resolve_target_monitored_wallet(conn, target)
    assert wallet == WALLET_ADDR
    assert source == 'asset'
    assert reason is None
    assert target['wallet_address'] == WALLET_ADDR


def test_resolve_target_wallet_from_canonical_column_is_target_config():
    """A correctly configured wallet_address resolves directly (no asset load)."""
    target = _make_target()  # wallet_address=WALLET_ADDR
    conn = _FakeConnection(targets=[target], asset_row=None)
    wallet, source, reason = qn._resolve_target_monitored_wallet(conn, target)
    assert wallet == WALLET_ADDR
    assert source == 'target_config'
    assert reason is None


def test_monitored_wallets_count_is_one_for_active_base_target_with_wallet_on_asset(
    caplog: pytest.LogCaptureFixture,
):
    """Acceptance: targets_loaded count=1 monitored_wallets_count=1, and the
    per-target resolution line proves wallet_present=true wallet_source=asset —
    no longer the monitored_wallets_count=0 / target_wallets=['none'] incident."""
    target = _make_wallet_on_asset_target(target_id=PROD_TARGET_ID)
    conn = _FakeConnection(targets=[target], asset_row=_asset_row(asset_id=target['asset_id']))
    with caplog.at_level('INFO', logger=_QN_LOGGER):
        targets = qn._load_all_base_wallet_targets(conn)
    assert len(targets) == 1
    assert 'quicknode_stream_targets_loaded count=1 monitored_wallets_count=1' in caplog.text
    assert (
        f'quicknode_stream_target_wallet_resolution target_id={PROD_TARGET_ID} '
        f'asset_id={target["asset_id"]} wallet_present=true '
        f'wallet_last4={WALLET_ADDR[-4:]} wallet_source=asset reason=none'
    ) in caplog.text
    # The full monitored wallet must never be printed — only its last4.
    assert WALLET_ADDR not in caplog.text


def test_wallet_on_asset_matching_tx_persists_quicknode_stream(monkeypatch: pytest.MonkeyPatch):
    """After the fix, a MetaMask transfer involving the asset-resolved wallet
    matches and persists detected_by=quicknode_stream (UI would show
    Detected by = QuickNode Stream instead of Stable RPC Polling)."""
    monkeypatch.setenv('QUICKNODE_STREAMS_SECRET', SECRET)
    target = _make_wallet_on_asset_target(target_id=PROD_TARGET_ID)
    conn = _FakeConnection(targets=[target], asset_row=_asset_row(asset_id=target['asset_id']))
    tx = {
        'tx_hash': TX_HASH,
        'from': WALLET_ADDR,
        'to': COUNTERPARTY,
        'value': '1000000000000000000',
        'block_number': 47286578,
        'chain_id': 8453,
    }
    raw = _body(tx)
    timestamp = _now_ts()
    signature = _sign(SECRET, nonce=NONCE, timestamp=timestamp, body=raw)
    with patch.object(qn, 'pg_connection', lambda: _mock_pg(conn)), \
         patch.object(qn, 'ensure_pilot_schema', lambda _c: None):
        result = qn.process_quicknode_base_stream_webhook(
            raw_body=raw, signature_header=signature, nonce_header=NONCE, timestamp_header=timestamp,
        )
    assert result['targets_loaded'] == 1
    assert result['matched'] == 1
    assert result['persisted'] == 1
    assert result['results'][0]['status'] == 'processed'
    assert result['results'][0]['detected_by'] == 'quicknode_stream'
    assert len(conn.telemetry_inserts) == 1
    payload = json.loads(conn.telemetry_inserts[0][9])
    assert payload['detected_by'] == 'quicknode_stream'
    assert payload['source_type'] == 'quicknode_stream'
    assert payload['tx_hash'] == TX_HASH
    assert payload['from'] == WALLET_ADDR


def test_wallet_on_asset_wallet_match_and_persist_logs_emitted(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
):
    """Acceptance: after a matching transfer the logs show quicknode_stream_wallet_match
    and quicknode_stream_event_persisted for the asset-resolved target."""
    monkeypatch.setenv('QUICKNODE_STREAMS_SECRET', SECRET)
    target = _make_wallet_on_asset_target(target_id=PROD_TARGET_ID)
    conn = _FakeConnection(targets=[target], asset_row=_asset_row(asset_id=target['asset_id']))
    tx = {'tx_hash': TX_HASH, 'from': WALLET_ADDR, 'to': COUNTERPARTY, 'value': '1'}
    raw = _body(tx)
    timestamp = _now_ts()
    signature = _sign(SECRET, nonce=NONCE, timestamp=timestamp, body=raw)
    with patch.object(qn, 'pg_connection', lambda: _mock_pg(conn)), \
         patch.object(qn, 'ensure_pilot_schema', lambda _c: None), \
         caplog.at_level('INFO', logger=_QN_LOGGER):
        qn.process_quicknode_base_stream_webhook(
            raw_body=raw, signature_header=signature, nonce_header=NONCE, timestamp_header=timestamp,
        )
    assert f'quicknode_stream_wallet_match tx_hash={TX_HASH} target_id={PROD_TARGET_ID} from_match=True' in caplog.text
    assert (
        f'quicknode_stream_event_persisted detected_by=quicknode_stream '
        f'tx_hash={TX_HASH} target_id={PROD_TARGET_ID}'
    ) in caplog.text


def test_wallet_on_asset_no_match_detail_shows_fingerprint_not_none(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
):
    """Regression: the reported symptom was no_match_detail target_wallets=['none'].
    With the wallet resolved from the asset, the fingerprint list carries the real
    last4/hash8, never ['none']."""
    monkeypatch.setenv('QUICKNODE_STREAMS_SECRET', SECRET)
    target = _make_wallet_on_asset_target(target_id=PROD_TARGET_ID)
    conn = _FakeConnection(targets=[target], asset_row=_asset_row(asset_id=target['asset_id']))
    tx = {'tx_hash': TX_HASH, 'from': UNRELATED_ADDR, 'to': COUNTERPARTY, 'value': '1'}
    raw = _body(tx)
    timestamp = _now_ts()
    signature = _sign(SECRET, nonce=NONCE, timestamp=timestamp, body=raw)
    with patch.object(qn, 'pg_connection', lambda: _mock_pg(conn)), \
         patch.object(qn, 'ensure_pilot_schema', lambda _c: None), \
         caplog.at_level('DEBUG', logger=_QN_LOGGER):
        qn.process_quicknode_base_stream_webhook(
            raw_body=raw, signature_header=signature, nonce_header=NONCE, timestamp_header=timestamp,
        )
    # DEBUG capture of the demoted per-tx detail: the fingerprint carries the real
    # last4/hash8 (the regression was target_wallets=['none']), never ['none'].
    assert "target_wallets=['none']" not in caplog.text
    assert f'{WALLET_ADDR[-4:]}/' in caplog.text


def test_missing_wallet_logs_truthful_reason_and_does_not_persist(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
):
    """Fail-closed: when neither the target nor its asset carries a wallet, the
    target is NOT counted, a truthful reason is logged, and nothing is persisted —
    the miss is never faked as a detection."""
    monkeypatch.setenv('QUICKNODE_STREAMS_SECRET', SECRET)
    target = _make_wallet_on_asset_target(target_id=PROD_TARGET_ID)
    # No asset row served and no wallet anywhere → unresolvable.
    conn = _FakeConnection(targets=[target], asset_row=None)
    tx = {'tx_hash': TX_HASH, 'from': WALLET_ADDR, 'to': COUNTERPARTY, 'value': '1'}
    raw = _body(tx)
    timestamp = _now_ts()
    signature = _sign(SECRET, nonce=NONCE, timestamp=timestamp, body=raw)
    with patch.object(qn, 'pg_connection', lambda: _mock_pg(conn)), \
         patch.object(qn, 'ensure_pilot_schema', lambda _c: None), \
         caplog.at_level('INFO', logger=_QN_LOGGER):
        result = qn.process_quicknode_base_stream_webhook(
            raw_body=raw, signature_header=signature, nonce_header=NONCE, timestamp_header=timestamp,
        )
    assert 'quicknode_stream_targets_loaded count=1 monitored_wallets_count=0' in caplog.text
    assert (
        f'quicknode_stream_target_wallet_resolution target_id={PROD_TARGET_ID} '
        f'asset_id={target["asset_id"]} wallet_present=false wallet_last4=none '
        f'wallet_source=none reason=no_wallet_in_target_or_asset'
    ) in caplog.text
    assert result['persisted'] == 0
    assert conn.telemetry_inserts == []
    assert result['results'][0]['status'] == 'no_match'


def test_missing_wallet_no_asset_linked_reason(caplog: pytest.LogCaptureFixture):
    """A wallet target with no wallet_address and no asset_id reports the truthful
    reason=no_asset_linked (distinct from an asset that carried no wallet)."""
    target = _make_wallet_on_asset_target(target_id=PROD_TARGET_ID)
    target['asset_id'] = None
    conn = _FakeConnection(targets=[target], asset_row=None)
    wallet, source, reason = qn._resolve_target_monitored_wallet(conn, target)
    assert wallet is None
    assert source == 'none'
    assert reason == 'no_asset_linked'


def test_wallet_on_asset_duplicate_from_stable_polling_is_suppressed(monkeypatch: pytest.MonkeyPatch):
    """A transfer stable RPC polling already recorded (detected_by=stable_rpc_polling)
    is duplicate_suppressed for the asset-resolved target — never inserted twice."""
    monkeypatch.setenv('QUICKNODE_STREAMS_SECRET', SECRET)
    target = _make_wallet_on_asset_target(target_id=PROD_TARGET_ID)
    existing_row = {'id': str(uuid.uuid4()), 'event_type': 'native_transfer', 'detected_by': 'stable_rpc_polling'}
    conn = _FakeConnection(
        targets=[target],
        existing_telemetry=existing_row,
        asset_row=_asset_row(asset_id=target['asset_id']),
    )
    tx = {'tx_hash': TX_HASH, 'from': WALLET_ADDR, 'to': COUNTERPARTY, 'value': '1'}
    raw = _body(tx)
    timestamp = _now_ts()
    signature = _sign(SECRET, nonce=NONCE, timestamp=timestamp, body=raw)
    with patch.object(qn, 'pg_connection', lambda: _mock_pg(conn)), \
         patch.object(qn, 'ensure_pilot_schema', lambda _c: None):
        result = qn.process_quicknode_base_stream_webhook(
            raw_body=raw, signature_header=signature, nonce_header=NONCE, timestamp_header=timestamp,
        )
    assert result['matched'] == 1
    assert result['duplicates'] == 1
    assert result['persisted'] == 0
    assert result['results'][0]['status'] == 'duplicate_suppressed'
    assert result['results'][0]['existing_detected_by'] == 'stable_rpc_polling'
    assert conn.telemetry_inserts == []
    assert conn.commit_calls == 0


# ---------------------------------------------------------------------------
# Alert / incident chain: a persisted QuickNode Streams wallet_transfer_detected
# must drive the SAME alert (and incident, where the rule requires it) chain that
# Stable RPC Polling creates — reusing monitoring_runner's rule functions verbatim,
# deduped across sources by target_id + tx_hash, evidence_source=live,
# detected_by=quicknode_stream. Stable RPC Polling and the WebSocket fallback are
# untouched.
# ---------------------------------------------------------------------------

def _outbound_tx() -> dict:
    """An outbound Base ETH transfer from the monitored wallet with value > 0, so
    BOTH the direction-agnostic smoke rule and the outbound-only Strategic
    Infrastructure Guard rule fire."""
    return {
        'tx_hash': TX_HASH,
        'from': WALLET_ADDR,
        'to': COUNTERPARTY,
        'value': '1000000000000000000',
        'block_number': 47286578,
        'chain_id': 8453,
    }


def _run_webhook_with_patched_alert_rules(
    monkeypatch: pytest.MonkeyPatch,
    *,
    conn: _FakeConnection,
    tx: dict,
    smoke_return: str | None = 'smoke-alert-id',
    sig_return: str | None = 'sig-alert-id',
):
    """Drive the webhook with the two stable-polling alert-rule functions patched so
    the chain is observable without a live DB (they normally open their own
    committed connection). Returns (result, smoke_mock, sig_mock)."""
    from unittest.mock import MagicMock

    monkeypatch.setenv('QUICKNODE_STREAMS_SECRET', SECRET)
    smoke = MagicMock(return_value=smoke_return)
    sig = MagicMock(return_value=sig_return)
    raw = _body(tx)
    timestamp = _now_ts()
    signature = _sign(SECRET, nonce=NONCE, timestamp=timestamp, body=raw)
    with patch.object(qn, 'pg_connection', lambda: _mock_pg(conn)), \
         patch.object(qn, 'ensure_pilot_schema', lambda _c: None), \
         patch.object(qn, '_wallet_transfer_smoke_alert', smoke), \
         patch.object(qn, '_strategic_infrastructure_guard_alert', sig):
        result = qn.process_quicknode_base_stream_webhook(
            raw_body=raw, signature_header=signature, nonce_header=NONCE, timestamp_header=timestamp,
        )
    return result, smoke, sig


def test_quicknode_transfer_creates_telemetry_and_alert(monkeypatch: pytest.MonkeyPatch):
    """A matched QuickNode transfer persists telemetry AND invokes the stable-polling
    smoke + Strategic Infrastructure Guard alert rules, with the ids surfaced on the result."""
    target = _make_target()
    conn = _FakeConnection(targets=[target])
    result, smoke, sig = _run_webhook_with_patched_alert_rules(monkeypatch, conn=conn, tx=_outbound_tx())

    # Telemetry persisted.
    assert result['persisted'] == 1
    assert len(conn.telemetry_inserts) == 1
    # Both stable-polling rule functions were reused (not a forked path).
    assert smoke.call_count == 1
    assert sig.call_count == 1
    # Alert ids surfaced on the per-tx result entry.
    entry = result['results'][0]
    assert entry['status'] == 'processed'
    assert entry['smoke_alert_id'] == 'smoke-alert-id'
    assert entry['sig_alert_id'] == 'sig-alert-id'


def test_quicknode_alert_uses_live_evidence_and_quicknode_detected_by(monkeypatch: pytest.MonkeyPatch):
    """The alert chain is invoked with evidence_source=live and a payload carrying
    detected_by=quicknode_stream / source_type=quicknode_stream — never demo/fallback,
    never mislabeled as stable polling."""
    target = _make_target()
    conn = _FakeConnection(targets=[target])
    _result, smoke, sig = _run_webhook_with_patched_alert_rules(monkeypatch, conn=conn, tx=_outbound_tx())

    smoke_kwargs = smoke.call_args.kwargs
    assert smoke_kwargs['evidence_source'] == 'live'
    assert smoke_kwargs['telemetry_id']
    assert smoke_kwargs['payload']['detected_by'] == 'quicknode_stream'
    assert smoke_kwargs['payload']['source_type'] == 'quicknode_stream'
    assert smoke_kwargs['payload']['tx_hash'] == TX_HASH
    assert smoke_kwargs['payload']['chain_id'] == 8453
    assert smoke_kwargs['payload']['block_number'] == 47286578

    sig_kwargs = sig.call_args.kwargs
    assert sig_kwargs['evidence_source'] == 'live'
    # Outbound: the Strategic Infrastructure Guard rule receives the resolved
    # monitored wallet as the from-side owner so it can classify the movement.
    assert sig_kwargs['target_wallet_address'] == WALLET_ADDR
    assert sig_kwargs['payload']['detected_by'] == 'quicknode_stream'


def test_quicknode_alert_chain_passes_workspace_and_target_scope(monkeypatch: pytest.MonkeyPatch):
    """Workspace-scoped: the alert rules receive the target's own workspace_id/target_id
    and the target-owner user_id (no authenticated webhook user, no cross-tenant leak)."""
    target = _make_target()
    target['updated_by_user_id'] = str(uuid.uuid4())
    conn = _FakeConnection(targets=[target])
    _result, smoke, _sig = _run_webhook_with_patched_alert_rules(monkeypatch, conn=conn, tx=_outbound_tx())

    smoke_kwargs = smoke.call_args.kwargs
    assert smoke_kwargs['workspace_id'] == target['workspace_id']
    assert smoke_kwargs['target_id'] == target['id']
    assert smoke_kwargs['user_id'] == target['updated_by_user_id']


def test_duplicate_stable_polling_transfer_does_not_create_duplicate_alert(monkeypatch: pytest.MonkeyPatch):
    """If stable RPC polling already recorded this target + tx_hash, the QuickNode
    webhook suppresses the duplicate and NEVER invokes the alert chain a second time."""
    target = _make_target()
    existing_row = {'id': str(uuid.uuid4()), 'event_type': 'native_transfer', 'detected_by': 'stable_rpc_polling'}
    conn = _FakeConnection(targets=[target], existing_telemetry=existing_row)
    result, smoke, sig = _run_webhook_with_patched_alert_rules(monkeypatch, conn=conn, tx=_outbound_tx())

    assert result['duplicates'] == 1
    assert result['persisted'] == 0
    assert result['results'][0]['status'] == 'duplicate_suppressed'
    # No alert chain on a suppressed duplicate — no second alert row.
    assert smoke.call_count == 0
    assert sig.call_count == 0


def test_alert_chain_failure_does_not_break_webhook_200(monkeypatch: pytest.MonkeyPatch):
    """An alert-rule exception must not turn a verified, already-persisted webhook into
    a 5xx: the telemetry stays committed and the response is still a normal summary."""
    from unittest.mock import MagicMock

    monkeypatch.setenv('QUICKNODE_STREAMS_SECRET', SECRET)
    target = _make_target()
    conn = _FakeConnection(targets=[target])
    boom = MagicMock(side_effect=RuntimeError('alert engine down'))
    raw = _body(_outbound_tx())
    timestamp = _now_ts()
    signature = _sign(SECRET, nonce=NONCE, timestamp=timestamp, body=raw)
    with patch.object(qn, 'pg_connection', lambda: _mock_pg(conn)), \
         patch.object(qn, 'ensure_pilot_schema', lambda _c: None), \
         patch.object(qn, '_wallet_transfer_smoke_alert', boom), \
         patch.object(qn, '_strategic_infrastructure_guard_alert', boom):
        result = qn.process_quicknode_base_stream_webhook(
            raw_body=raw, signature_header=signature, nonce_header=NONCE, timestamp_header=timestamp,
        )
    assert result['received'] is True
    assert result['persisted'] == 1
    assert result['results'][0]['status'] == 'processed'
    # Alerts failed to create; ids are None but the transfer is still persisted.
    assert result['results'][0]['smoke_alert_id'] is None
    assert result['results'][0]['sig_alert_id'] is None


# ---------------------------------------------------------------------------
# Cross-source dedup + evidence package, asserted directly against the reused
# monitoring_runner rule functions (the same functions Stable RPC Polling calls).
# ---------------------------------------------------------------------------

class _AlertStubConn:
    """Minimal committed-connection stub for the monitoring_runner alert rules,
    mirroring test_wallet_transfer_alert_escalation._StubConn. Records INSERT tables
    and can simulate an already-existing detection (ON CONFLICT DO NOTHING) with a
    linked alert — the state stable RPC polling leaves behind for a given tx."""

    def __init__(self, *, detection_conflict: bool = False, linked_alert_id: str | None = None):
        self.inserts: list[tuple] = []
        self.commit_calls = 0
        self._detection_conflict = detection_conflict
        self._linked_alert_id = linked_alert_id

    class _R:
        def __init__(self, rows=None, rowcount=None):
            self._rows = list(rows or [])
            self.rowcount = rowcount if rowcount is not None else len(self._rows)

        def fetchone(self):
            return self._rows[0] if self._rows else None

        def fetchall(self):
            return list(self._rows)

    def execute(self, query, params=None):
        q = (query or '').strip().lower()
        if q.startswith('insert into'):
            table = q.split('insert into')[1].strip().split('(')[0].strip().split()[0]
            self.inserts.append((table, tuple(params or ())))
            if 'detections' in table and self._detection_conflict:
                return self._R(rowcount=0)
            return self._R(rowcount=1)
        if 'alert_suppression_rules' in q and 'select' in q:
            return self._R([])
        if q.startswith('select') and 'from alerts' in q:
            return self._R([])
        if 'select' in q and 'linked_alert_id' in q and 'from detections' in q:
            return self._R([{'linked_alert_id': self._linked_alert_id}])
        if q.startswith('update'):
            return self._R(rowcount=1)
        return self._R()

    def commit(self):
        self.commit_calls += 1


def _quicknode_alert_payload() -> dict:
    return {
        'chain_id': 8453,
        'chain_network': 'base',
        'block_number': 47286578,
        'tx_hash': TX_HASH,
        'from': WALLET_ADDR,
        'to': COUNTERPARTY,
        'from_address': WALLET_ADDR,
        'to_address': COUNTERPARTY,
        'amount': '1000000000000000000',
        'value_wei': 1000000000000000000,
        'wallet_transfer_direction': 'outbound',
        'event_type': 'wallet_transfer_detected',
        'source_type': 'quicknode_stream',
        'detected_by': 'quicknode_stream',
    }


def test_alert_evidence_package_includes_tx_hash_and_quicknode_detected_by(monkeypatch: pytest.MonkeyPatch):
    """Requirement 5.3: the alert evidence package (alerts.payload + detection
    raw_evidence) built from a QuickNode transfer carries tx_hash and
    detected_by=quicknode_stream — provable in the exported evidence."""
    from contextlib import contextmanager

    from services.api.app import monitoring_runner as mr

    stub = _AlertStubConn()
    captured_response: list[dict] = []
    original_upsert = mr._upsert_alert

    def _capturing_upsert(conn, *, response, **kwargs):
        captured_response.append(dict(response))
        return original_upsert(conn, response=response, **kwargs)

    @contextmanager
    def _fake_pg():
        yield stub

    with patch.object(mr, 'pg_connection', _fake_pg), \
         patch.object(mr, '_upsert_alert', _capturing_upsert):
        alert_id = mr._wallet_transfer_smoke_alert(
            workspace_id=str(uuid.uuid4()),
            user_id=str(uuid.uuid4()),
            target_id=str(uuid.uuid4()),
            target_name='Treasury Base Wallet',
            payload=_quicknode_alert_payload(),
            evidence_source='live',
            telemetry_id=str(uuid.uuid4()),
        )

    assert alert_id
    # Alert payload (the stored evidence package) carries tx_hash + detected_by.
    assert captured_response, 'the smoke rule must call _upsert_alert'
    r = captured_response[0]
    assert r['tx_hash'] == TX_HASH
    assert r['detected_by'] == 'quicknode_stream'
    assert r['evidence_source'] == 'live'
    # Detection raw_evidence (the proof-chain record) also carries them.
    detection_params = [p for t, p in stub.inserts if t == 'detections'][0]
    raw = json.loads(detection_params[11])
    assert raw['tx_hash'] == TX_HASH
    assert raw['detected_by'] == 'quicknode_stream'


def test_quicknode_and_stable_polling_share_alert_dedupe_signature():
    """Cross-source dedup foundation: the smoke and Strategic Infrastructure Guard
    dedupe signatures depend only on workspace_id + target_id + chain_id + tx_hash +
    rule — NOT on the detecting source. So a QuickNode alert and a stable-polling
    alert for the same target + tx collapse onto one signature (no duplicate)."""
    from services.api.app import monitoring_runner as mr

    ws, tid = str(uuid.uuid4()), str(uuid.uuid4())
    smoke_sig = mr._smoke_dedupe_signature(workspace_id=ws, target_id=tid, chain_id=8453, tx_hash=TX_HASH)
    sig_sig = mr._sig_dedupe_signature(workspace_id=ws, target_id=tid, chain_id=8453, tx_hash=TX_HASH)
    # Recomputing from the "other source" (identical scope) yields the identical key.
    assert mr._smoke_dedupe_signature(workspace_id=ws, target_id=tid, chain_id=8453, tx_hash=TX_HASH) == smoke_sig
    assert mr._sig_dedupe_signature(workspace_id=ws, target_id=tid, chain_id=8453, tx_hash=TX_HASH) == sig_sig
    # A different tx_hash is a different alert (never collapsed by target alone).
    other = mr._smoke_dedupe_signature(workspace_id=ws, target_id=tid, chain_id=8453, tx_hash='0x' + 'ff' * 32)
    assert other != smoke_sig


def test_quicknode_transfer_dedupes_against_prior_stable_polling_alert():
    """Requirement 3: when stable RPC polling already created the detection+alert for
    this target + tx_hash, the QuickNode run (same reused rule function) returns the
    existing alert id and inserts NO new alert row."""
    from contextlib import contextmanager

    from services.api.app import monitoring_runner as mr

    existing_alert_id = str(uuid.uuid4())
    # detection ON CONFLICT DO NOTHING + an already-linked alert = stable polling got here first.
    stub = _AlertStubConn(detection_conflict=True, linked_alert_id=existing_alert_id)

    @contextmanager
    def _fake_pg():
        yield stub

    with patch.object(mr, 'pg_connection', _fake_pg):
        alert_id = mr._wallet_transfer_smoke_alert(
            workspace_id=str(uuid.uuid4()),
            user_id=str(uuid.uuid4()),
            target_id=str(uuid.uuid4()),
            target_name='Treasury Base Wallet',
            payload=_quicknode_alert_payload(),
            evidence_source='live',
            telemetry_id=str(uuid.uuid4()),
        )

    assert alert_id == existing_alert_id, 'must return the stable-polling alert, not a duplicate'
    assert not [t for t, _ in stub.inserts if t == 'alerts'], 'no second alert row may be inserted'


# ---------------------------------------------------------------------------
# Production-schema regression guard for the monitored_system_id crash.
#
# The deploy incident: _BASE_WALLET_TARGETS_SQL selected `monitored_system_id`
# directly from `targets`, but that column does NOT exist on `targets` in the
# production schema — it lives on `monitored_systems` (keyed by target_id). The
# handler crashed in _load_all_base_wallet_targets with
#   psycopg.errors.UndefinedColumn: column "monitored_system_id" does not exist
# The fix derives monitored_system_id via a LEFT JOIN on monitored_systems, so a
# target with no monitored_systems row still loads (monitored_system_id = NULL)
# and still matches wallet transfers (the join is never required for matching).
#
# _ProductionSchemaConnection models that real schema: selecting a bare
# monitored_system_id off `targets` (the old query) raises UndefinedColumn exactly
# like production, while the fixed JOIN query loads targets and derives the id.
# ---------------------------------------------------------------------------

# Real production `targets` columns (migration 0006 + later ALTERs). Notably
# ABSENT: monitored_system_id — that column is only on `monitored_systems`.
_PROD_TARGET_COLUMNS = frozenset({
    'id', 'workspace_id', 'name', 'target_type', 'chain_network', 'chain_id',
    'wallet_address', 'contract_identifier', 'asset_id', 'asset_type',
    'target_metadata', 'monitoring_enabled', 'enabled', 'is_active',
    'updated_by_user_id', 'created_by_user_id', 'deleted_at',
})


class _ProductionSchemaConnection:
    """Fake connection whose `targets` table has NO monitored_system_id column.

    monitored_system_id is derivable only by LEFT JOINing `monitored_systems`
    (``target_id -> monitored_system id``). A query that selects monitored_system_id
    without that join raises ``psycopg.errors.UndefinedColumn``, exactly like the
    production incident; the fixed join query returns each target augmented with
    monitored_system_id (or None when the target has no monitored_systems row).
    Set ``fail_target_load=True`` to force a schema error even for the fixed query,
    exercising the defensive fail-closed path independently of the query shape.
    """

    def __init__(
        self,
        *,
        targets: list[dict],
        monitored_systems: dict | None = None,
        existing_telemetry: dict | None = None,
        asset_row: dict | None = None,
        fail_target_load: bool = False,
    ):
        self._targets = targets
        self._monitored_systems = dict(monitored_systems or {})  # target_id -> ms id
        self._existing_telemetry = existing_telemetry
        self._asset_row = asset_row
        self._fail_target_load = fail_target_load
        self.telemetry_inserts: list[tuple] = []
        self.commit_calls = 0
        self.rollback_calls = 0

    def _targets_result(self, q: str) -> _Rows:
        if self._fail_target_load:
            raise psycopg.errors.UndefinedColumn('simulated target-load schema error')
        joins_ms = 'join monitored_systems' in q
        if 'monitored_system_id' in q and not joins_ms:
            # Bare targets.monitored_system_id — the column does not exist here.
            raise psycopg.errors.UndefinedColumn('column "monitored_system_id" does not exist')
        rows = []
        for target in self._targets:
            unknown = set(target) - _PROD_TARGET_COLUMNS
            assert not unknown, f'test target carries non-production columns: {sorted(unknown)}'
            row = dict(target)
            if joins_ms:
                # LEFT JOIN semantics: NULL when the target has no monitored_systems row.
                row['monitored_system_id'] = self._monitored_systems.get(target['id'])
            rows.append(row)
        return _Rows(rows)

    def execute(self, query, params=None):
        q = (query or '').strip().lower()
        if 'from targets' in q:
            return self._targets_result(q)
        if 'from assets' in q:
            return _Rows([self._asset_row] if self._asset_row else [])
        if 'from telemetry_events' in q and 'select' in q:
            return _Rows([self._existing_telemetry] if self._existing_telemetry else [])
        if q.startswith('insert into telemetry_events'):
            self.telemetry_inserts.append(tuple(params or ()))
            return _Rows([])
        return _Rows([])

    def commit(self):
        self.commit_calls += 1

    def rollback(self):
        self.rollback_calls += 1

    @contextmanager
    def transaction(self):
        yield


def test_base_wallet_targets_sql_derives_monitored_system_id_via_join_not_bare_column():
    """The fix, locked in structurally: monitored_system_id must be derived via a
    LEFT JOIN on monitored_systems, never selected as a bare column off `targets`
    (which does not have it). This assertion fails against the crashing old query."""
    sql = qn._BASE_WALLET_TARGETS_SQL.lower()
    assert 'left join monitored_systems' in sql
    assert 'ms.id as monitored_system_id' in sql
    assert 'ms.target_id = t.id' in sql
    # A bare, unqualified `monitored_system_id` column selected from targets is the
    # exact regression — the only occurrence may be the aliased join projection.
    assert sql.count('monitored_system_id') == 1


def test_old_bare_column_query_reproduces_undefined_column_crash():
    """Guard the guard: the production-schema fake must actually reproduce the
    incident when handed the OLD query shape (bare monitored_system_id, no join),
    so a real regression cannot slip past these tests as a false pass."""
    conn = _ProductionSchemaConnection(targets=[_make_target()])
    old_query = (
        'SELECT id, workspace_id, monitored_system_id FROM targets WHERE is_active = TRUE'
    )
    with pytest.raises(psycopg.errors.UndefinedColumn):
        conn.execute(old_query).fetchall()


def test_load_targets_on_production_schema_without_monitored_system_id_column():
    """Requirement: a production-like targets table without a monitored_system_id
    column still loads (no crash), and wallet resolution still works."""
    target = _make_target()  # wallet_address set; no monitored_system_id key
    conn = _ProductionSchemaConnection(targets=[target])
    loaded = qn._load_all_base_wallet_targets(conn)
    assert len(loaded) == 1
    # monitored_system_id derived from the (empty) join is NULL, never a crash.
    assert loaded[0].get('monitored_system_id') is None
    # Wallet resolution is unaffected by the schema fix.
    assert qn.resolve_monitored_wallet(loaded[0]) == WALLET_ADDR


def test_load_targets_derives_monitored_system_id_from_join_when_present():
    """When a monitored_systems row exists for the target, the LEFT JOIN derives its
    id onto the loaded target (used downstream to link the detection)."""
    target = _make_target()
    ms_id = str(uuid.uuid4())
    conn = _ProductionSchemaConnection(
        targets=[target], monitored_systems={target['id']: ms_id},
    )
    loaded = qn._load_all_base_wallet_targets(conn)
    assert loaded[0]['monitored_system_id'] == ms_id


def test_webhook_no_500_and_no_match_on_production_schema(monkeypatch: pytest.MonkeyPatch):
    """End-to-end on the production schema (no monitored_system_id column): a tx that
    matches no monitored wallet returns a normal 200 summary with no_match — never a
    500 from the absent column."""
    monkeypatch.setenv('QUICKNODE_STREAMS_SECRET', SECRET)
    target = _make_target(wallet_address=WALLET_ADDR)
    conn = _ProductionSchemaConnection(targets=[target])
    tx = {'tx_hash': TX_HASH, 'from': UNRELATED_ADDR, 'to': COUNTERPARTY, 'value': '1', 'chain_id': 8453}
    raw = _body(tx)
    timestamp = _now_ts()
    signature = _sign(SECRET, nonce=NONCE, timestamp=timestamp, body=raw)
    with patch.object(qn, 'pg_connection', lambda: _mock_pg(conn)), \
         patch.object(qn, 'ensure_pilot_schema', lambda _c: None):
        result = qn.process_quicknode_base_stream_webhook(
            raw_body=raw, signature_header=signature, nonce_header=NONCE, timestamp_header=timestamp,
        )
    assert result['ok'] is True
    assert result['targets_loaded'] == 1
    assert result['matched'] == 0
    assert result['skipped'] == 1
    assert result['results'][0]['status'] == 'no_match'
    assert conn.telemetry_inserts == []


def test_webhook_persists_on_production_schema_when_tx_matches(monkeypatch: pytest.MonkeyPatch):
    """End-to-end on the production schema: a tx that matches a monitored wallet
    returns 200 and persists a quicknode_stream row — proving the schema fix restores
    the full detection path that the monitored_system_id crash had broken."""
    monkeypatch.setenv('QUICKNODE_STREAMS_SECRET', SECRET)
    target = _make_target(wallet_address=WALLET_ADDR)
    conn = _ProductionSchemaConnection(targets=[target])
    tx = {
        'tx_hash': TX_HASH, 'from': WALLET_ADDR, 'to': COUNTERPARTY,
        'value': '1000000000000000000', 'block_number': 47286578, 'chain_id': 8453,
    }
    raw = _body(tx)
    timestamp = _now_ts()
    signature = _sign(SECRET, nonce=NONCE, timestamp=timestamp, body=raw)
    with patch.object(qn, 'pg_connection', lambda: _mock_pg(conn)), \
         patch.object(qn, 'ensure_pilot_schema', lambda _c: None):
        result = qn.process_quicknode_base_stream_webhook(
            raw_body=raw, signature_header=signature, nonce_header=NONCE, timestamp_header=timestamp,
        )
    assert result['ok'] is True
    assert result['persisted'] == 1
    assert result['results'][0]['status'] == 'processed'
    assert result['results'][0]['detected_by'] == 'quicknode_stream'
    assert len(conn.telemetry_inserts) == 1
    payload = json.loads(conn.telemetry_inserts[0][9])
    assert payload['detected_by'] == 'quicknode_stream'
    assert payload['tx_hash'] == TX_HASH


def test_matched_target_passes_joined_monitored_system_id_to_alert_chain(monkeypatch: pytest.MonkeyPatch):
    """The monitored_system_id derived from the LEFT JOIN flows into the alert chain,
    so the detection is still linked to its monitored_system after the schema fix."""
    target = _make_target()
    ms_id = str(uuid.uuid4())
    conn = _ProductionSchemaConnection(targets=[target], monitored_systems={target['id']: ms_id})
    _result, smoke, sig = _run_webhook_with_patched_alert_rules(monkeypatch, conn=conn, tx=_outbound_tx())
    assert smoke.call_args.kwargs['monitored_system_id'] == ms_id
    assert sig.call_args.kwargs['monitored_system_id'] == ms_id


def test_match_and_persist_when_no_monitored_system_row_exists(monkeypatch: pytest.MonkeyPatch):
    """The join is never required for matching: a target with no monitored_systems row
    still matches and persists, with monitored_system_id=None passed to the alert chain."""
    target = _make_target()
    conn = _ProductionSchemaConnection(targets=[target], monitored_systems={})
    result, smoke, _sig = _run_webhook_with_patched_alert_rules(monkeypatch, conn=conn, tx=_outbound_tx())
    assert result['persisted'] == 1
    assert smoke.call_args.kwargs['monitored_system_id'] is None


# ---------------------------------------------------------------------------
# Defensive target-load handling: a database/schema error while loading targets
# (after signature verification) must fail closed with a truthful 200 — never a
# 500 that would make QuickNode Streams retry a non-auth/schema bug forever.
# ---------------------------------------------------------------------------

def test_target_load_schema_error_returns_fail_closed_200_not_500(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
):
    monkeypatch.setenv('QUICKNODE_STREAMS_SECRET', SECRET)
    target = _make_target()
    conn = _ProductionSchemaConnection(targets=[target], fail_target_load=True)
    tx = {'tx_hash': TX_HASH, 'from': WALLET_ADDR, 'to': COUNTERPARTY, 'value': '1', 'chain_id': 8453}
    raw = _body(tx)
    timestamp = _now_ts()
    signature = _sign(SECRET, nonce=NONCE, timestamp=timestamp, body=raw)
    with patch.object(qn, 'pg_connection', lambda: _mock_pg(conn)), \
         patch.object(qn, 'ensure_pilot_schema', lambda _c: None), \
         caplog.at_level('WARNING', logger=_QN_LOGGER):
        # Must NOT raise (no 500): the handler returns a safe body instead.
        result = qn.process_quicknode_base_stream_webhook(
            raw_body=raw, signature_header=signature, nonce_header=NONCE, timestamp_header=timestamp,
        )
    # Truthful and fail-closed: never a false "healthy".
    assert result['ok'] is False
    assert result['fail_closed'] is True
    assert result['error'] == 'target_load_failed'
    assert result['persisted'] == 0
    assert result['targets_loaded'] == 0
    assert result['results'] == []
    # Provable from logs, and the aborted transaction was rolled back cleanly.
    assert 'quicknode_stream_target_load_failed' in caplog.text
    assert 'error_type=UndefinedColumn' in caplog.text
    assert conn.rollback_calls == 1
    # Nothing was persisted and no alert row was written on the fail-closed path.
    assert conn.telemetry_inserts == []


def test_signature_failure_still_raises_not_swallowed_by_defensive_handler(monkeypatch: pytest.MonkeyPatch):
    """The fail-closed target-load handler must not soften auth failures: an invalid
    signature still rejects with 401 before target loading is ever reached."""
    monkeypatch.setenv('QUICKNODE_STREAMS_SECRET', SECRET)
    raw = _body({'tx_hash': TX_HASH, 'from': WALLET_ADDR})
    with pytest.raises(HTTPException) as exc:
        qn.process_quicknode_base_stream_webhook(
            raw_body=raw, signature_header='not-the-right-signature',
            nonce_header=NONCE, timestamp_header=_now_ts(),
        )
    assert exc.value.status_code == 401


# ---------------------------------------------------------------------------
# Duplicate dedupe key (requirement 6): wallet_transfer_detected is deduped by
# target_id + chain_id + tx_hash + event_type, REGARDLESS of detected_by, so a
# stable-polling row (any wallet-transfer event type, any source) suppresses a
# QuickNode row for the identical tx.
# ---------------------------------------------------------------------------

class _CaptureTelemetryConn:
    """Captures the dedupe SELECT's SQL + params so the WHERE composition can be
    asserted directly (the text-dispatching fakes elsewhere ignore the WHERE)."""

    def __init__(self, existing: dict | None = None):
        self._existing = existing
        self.telemetry_query: str | None = None
        self.telemetry_params: tuple = ()

    def execute(self, query, params=None):
        q = (query or '').strip().lower()
        if 'from telemetry_events' in q and 'select' in q:
            self.telemetry_query = q
            self.telemetry_params = tuple(params or ())
            return _Rows([self._existing] if self._existing else [])
        return _Rows([])


def test_dedupe_key_is_target_chain_tx_event_type_regardless_of_detected_by():
    conn = _CaptureTelemetryConn(existing=None)
    result = qn._existing_telemetry_for_tx(conn, target_id='tgt-1', tx_hash=TX_HASH, chain_id=8453)
    assert result is None
    q = conn.telemetry_query
    assert q is not None
    where_clause = q.split('where', 1)[1]
    # Dedupe identity: target_id + chain_id + tx_hash + event_type.
    assert 'target_id = %s' in where_clause
    assert "lower(payload_json->>'tx_hash') = lower(%s)" in where_clause
    assert 'event_type = any(%s)' in where_clause
    assert "payload_json->>'chain_id'" in where_clause
    # Regardless of detected_by: it must never appear in the WHERE (only SELECTed).
    assert 'detected_by' not in where_clause
    # Params carry the scope and the wallet-transfer event-type family.
    assert 'tgt-1' in conn.telemetry_params
    assert TX_HASH in conn.telemetry_params
    assert '8453' in conn.telemetry_params
    assert list(qn._WALLET_TRANSFER_EVENT_TYPES) in conn.telemetry_params


def test_dedupe_event_type_family_covers_native_transfer_and_wallet_transfer_detected():
    """Stable RPC polling writes 'native_transfer' for a plain ETH move and
    'wallet_transfer_detected' otherwise; both must dedupe a QuickNode transfer."""
    assert 'wallet_transfer_detected' in qn._WALLET_TRANSFER_EVENT_TYPES
    assert 'native_transfer' in qn._WALLET_TRANSFER_EVENT_TYPES


def test_stable_polling_native_transfer_suppresses_quicknode_regardless_of_detected_by(
    monkeypatch: pytest.MonkeyPatch,
):
    """Cross-source, cross-event-type: a stable-polling 'native_transfer' row for the
    same target + tx suppresses the QuickNode 'wallet_transfer_detected' — deduped on
    the on-chain identity, never on detected_by."""
    monkeypatch.setenv('QUICKNODE_STREAMS_SECRET', SECRET)
    target = _make_target()
    existing_row = {'id': str(uuid.uuid4()), 'event_type': 'native_transfer', 'detected_by': 'stable_rpc_polling'}
    conn = _ProductionSchemaConnection(targets=[target], existing_telemetry=existing_row)
    tx = {'tx_hash': TX_HASH, 'from': WALLET_ADDR, 'to': COUNTERPARTY, 'value': '1', 'chain_id': 8453}
    raw = _body(tx)
    timestamp = _now_ts()
    signature = _sign(SECRET, nonce=NONCE, timestamp=timestamp, body=raw)
    with patch.object(qn, 'pg_connection', lambda: _mock_pg(conn)), \
         patch.object(qn, 'ensure_pilot_schema', lambda _c: None):
        result = qn.process_quicknode_base_stream_webhook(
            raw_body=raw, signature_header=signature, nonce_header=NONCE, timestamp_header=timestamp,
        )
    assert result['duplicates'] == 1
    assert result['persisted'] == 0
    assert result['results'][0]['status'] == 'duplicate_suppressed'
    assert result['results'][0]['existing_detected_by'] == 'stable_rpc_polling'
    assert conn.telemetry_inserts == []


# ---------------------------------------------------------------------------
# Debug-tx tracing: QUICKNODE_STREAM_DEBUG_TX_HASH / QUICKNODE_STREAM_DEBUG_TX_BLOCK
#
# Instrumentation for the "QuickNode Stream missed a fresh tx" investigation.
# Every batch logs its block range + whether it carries the debug tx (req 2);
# a matched/unmatched debug tx logs debug_tx_seen (req 3); a batch whose range
# covers the debug tx's block but does not contain it logs debug_tx_not_seen
# (req 4). The four log states map 1:1 to acceptance cases A/B/C/D.
# ---------------------------------------------------------------------------

OTHER_HASH = '0x' + 'ee' * 32
DEBUG_BLOCK = 47286578
# The module's TX_HASH fixture is a short (65-char) stand-in that the webhook path
# never length-validates. The debug-tx endpoint DOES require a canonical 66-char
# 0x hash, so endpoint tests use the real production hash from the incident report.
VALID_TX_HASH = '0x7b09f621698842b1c04f66815318775662b7c48087a6cf6ae4e041c67049948a'


def test_debug_tx_hashes_parses_single_and_list(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv('QUICKNODE_STREAM_DEBUG_TX_HASH', raising=False)
    assert qn._debug_tx_hashes() == set()
    monkeypatch.setenv('QUICKNODE_STREAM_DEBUG_TX_HASH', TX_HASH.upper())
    assert qn._debug_tx_hashes() == {TX_HASH.lower()}
    monkeypatch.setenv('QUICKNODE_STREAM_DEBUG_TX_HASH', f'  {TX_HASH} , {OTHER_HASH} ')
    assert qn._debug_tx_hashes() == {TX_HASH.lower(), OTHER_HASH.lower()}


def test_debug_tx_block_number_parses_decimal_and_hex(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv('QUICKNODE_STREAM_DEBUG_TX_BLOCK', raising=False)
    assert qn._debug_tx_block_number() is None
    monkeypatch.setenv('QUICKNODE_STREAM_DEBUG_TX_BLOCK', str(DEBUG_BLOCK))
    assert qn._debug_tx_block_number() == DEBUG_BLOCK
    monkeypatch.setenv('QUICKNODE_STREAM_DEBUG_TX_BLOCK', hex(DEBUG_BLOCK))
    assert qn._debug_tx_block_number() == DEBUG_BLOCK


def test_batch_range_logged_for_every_batch_contains_debug_tx_false(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
):
    monkeypatch.setenv('QUICKNODE_STREAMS_SECRET', SECRET)
    monkeypatch.delenv('QUICKNODE_STREAM_DEBUG_TX_HASH', raising=False)
    target = _make_target()
    conn = _FakeConnection(targets=[target])
    tx = {'tx_hash': TX_HASH, 'from': WALLET_ADDR, 'to': COUNTERPARTY, 'value': '1', 'block_number': DEBUG_BLOCK}
    raw = _body(tx)
    timestamp = _now_ts()
    signature = _sign(SECRET, nonce=NONCE, timestamp=timestamp, body=raw)
    with patch.object(qn, 'pg_connection', lambda: _mock_pg(conn)), \
         patch.object(qn, 'ensure_pilot_schema', lambda _c: None), \
         caplog.at_level('INFO', logger=_QN_LOGGER):
        qn.process_quicknode_base_stream_webhook(
            raw_body=raw, signature_header=signature, nonce_header=NONCE, timestamp_header=timestamp,
        )
    assert (
        f'quicknode_stream_batch_range first_block={DEBUG_BLOCK} last_block={DEBUG_BLOCK} '
        f'tx_count=1 contains_debug_tx=false'
    ) in caplog.text


def test_batch_range_contains_debug_tx_true_when_configured(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
):
    monkeypatch.setenv('QUICKNODE_STREAMS_SECRET', SECRET)
    monkeypatch.setenv('QUICKNODE_STREAM_DEBUG_TX_HASH', TX_HASH)
    target = _make_target()
    conn = _FakeConnection(targets=[target])
    tx = {'tx_hash': TX_HASH, 'from': WALLET_ADDR, 'to': COUNTERPARTY, 'value': '1', 'block_number': DEBUG_BLOCK}
    raw = _body(tx)
    timestamp = _now_ts()
    signature = _sign(SECRET, nonce=NONCE, timestamp=timestamp, body=raw)
    with patch.object(qn, 'pg_connection', lambda: _mock_pg(conn)), \
         patch.object(qn, 'ensure_pilot_schema', lambda _c: None), \
         caplog.at_level('INFO', logger=_QN_LOGGER):
        qn.process_quicknode_base_stream_webhook(
            raw_body=raw, signature_header=signature, nonce_header=NONCE, timestamp_header=timestamp,
        )
    assert 'contains_debug_tx=true' in caplog.text


def test_debug_tx_seen_on_match_persisted(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture):
    """Debug tx matched a monitored wallet and persisted (the healthy path)."""
    monkeypatch.setenv('QUICKNODE_STREAMS_SECRET', SECRET)
    monkeypatch.setenv('QUICKNODE_STREAM_DEBUG_TX_HASH', TX_HASH)
    target = _make_target()
    conn = _FakeConnection(targets=[target])
    tx = {'tx_hash': TX_HASH, 'from': WALLET_ADDR, 'to': COUNTERPARTY, 'value': '1', 'block_number': DEBUG_BLOCK}
    raw = _body(tx)
    timestamp = _now_ts()
    signature = _sign(SECRET, nonce=NONCE, timestamp=timestamp, body=raw)
    with patch.object(qn, 'pg_connection', lambda: _mock_pg(conn)), \
         patch.object(qn, 'ensure_pilot_schema', lambda _c: None), \
         caplog.at_level('INFO', logger=_QN_LOGGER):
        qn.process_quicknode_base_stream_webhook(
            raw_body=raw, signature_header=signature, nonce_header=NONCE, timestamp_header=timestamp,
        )
    assert f'quicknode_stream_debug_tx_seen tx_hash={TX_HASH}' in caplog.text
    assert f'block_number={DEBUG_BLOCK}' in caplog.text
    assert f'target_wallet_last4={WALLET_ADDR[-4:]}' in caplog.text
    assert 'from_matches_target=true' in caplog.text
    assert 'duplicate_found=false persisted=true' in caplog.text


def test_debug_tx_seen_on_duplicate_suppressed(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture):
    """Case D: debug tx matched but a prior row already exists -> suppressed."""
    monkeypatch.setenv('QUICKNODE_STREAMS_SECRET', SECRET)
    monkeypatch.setenv('QUICKNODE_STREAM_DEBUG_TX_HASH', TX_HASH)
    target = _make_target()
    existing_row = {'id': str(uuid.uuid4()), 'event_type': 'native_transfer', 'detected_by': 'stable_rpc_polling'}
    conn = _FakeConnection(targets=[target], existing_telemetry=existing_row)
    tx = {'tx_hash': TX_HASH, 'from': WALLET_ADDR, 'to': COUNTERPARTY, 'value': '1', 'block_number': DEBUG_BLOCK}
    raw = _body(tx)
    timestamp = _now_ts()
    signature = _sign(SECRET, nonce=NONCE, timestamp=timestamp, body=raw)
    with patch.object(qn, 'pg_connection', lambda: _mock_pg(conn)), \
         patch.object(qn, 'ensure_pilot_schema', lambda _c: None), \
         caplog.at_level('INFO', logger=_QN_LOGGER):
        qn.process_quicknode_base_stream_webhook(
            raw_body=raw, signature_header=signature, nonce_header=NONCE, timestamp_header=timestamp,
        )
    assert f'quicknode_stream_debug_tx_seen tx_hash={TX_HASH}' in caplog.text
    assert 'duplicate_found=true persisted=false' in caplog.text


def test_debug_tx_seen_on_no_match(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture):
    """Case C: debug tx normalized fine but matched no monitored wallet."""
    monkeypatch.setenv('QUICKNODE_STREAMS_SECRET', SECRET)
    monkeypatch.setenv('QUICKNODE_STREAM_DEBUG_TX_HASH', TX_HASH)
    target = _make_target()  # monitors WALLET_ADDR
    conn = _FakeConnection(targets=[target])
    tx = {'tx_hash': TX_HASH, 'from': UNRELATED_ADDR, 'to': COUNTERPARTY, 'value': '1', 'block_number': DEBUG_BLOCK}
    raw = _body(tx)
    timestamp = _now_ts()
    signature = _sign(SECRET, nonce=NONCE, timestamp=timestamp, body=raw)
    with patch.object(qn, 'pg_connection', lambda: _mock_pg(conn)), \
         patch.object(qn, 'ensure_pilot_schema', lambda _c: None), \
         caplog.at_level('INFO', logger=_QN_LOGGER):
        qn.process_quicknode_base_stream_webhook(
            raw_body=raw, signature_header=signature, nonce_header=NONCE, timestamp_header=timestamp,
        )
    assert f'quicknode_stream_debug_tx_seen tx_hash={TX_HASH}' in caplog.text
    assert 'target_wallet_last4=none from_matches_target=false to_matches_target=false' in caplog.text
    assert 'duplicate_found=false persisted=false' in caplog.text


def test_debug_tx_not_seen_when_range_covers_block_but_tx_absent(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
):
    """Case B: the batch's block range covers the debug tx's block, but the tx is
    not in the batch -> QuickNode delivered the block without the tx."""
    monkeypatch.setenv('QUICKNODE_STREAMS_SECRET', SECRET)
    monkeypatch.setenv('QUICKNODE_STREAM_DEBUG_TX_HASH', TX_HASH)
    monkeypatch.setenv('QUICKNODE_STREAM_DEBUG_TX_BLOCK', str(DEBUG_BLOCK))
    target = _make_target()
    conn = _FakeConnection(targets=[target])
    other_tx = {'tx_hash': OTHER_HASH, 'from': UNRELATED_ADDR, 'to': COUNTERPARTY, 'value': '1', 'block_number': DEBUG_BLOCK}
    raw = _body(other_tx)
    timestamp = _now_ts()
    signature = _sign(SECRET, nonce=NONCE, timestamp=timestamp, body=raw)
    with patch.object(qn, 'pg_connection', lambda: _mock_pg(conn)), \
         patch.object(qn, 'ensure_pilot_schema', lambda _c: None), \
         caplog.at_level('INFO', logger=_QN_LOGGER):
        qn.process_quicknode_base_stream_webhook(
            raw_body=raw, signature_header=signature, nonce_header=NONCE, timestamp_header=timestamp,
        )
    assert (
        f'quicknode_stream_debug_tx_not_seen tx_hash={TX_HASH} tx_block_number={DEBUG_BLOCK} '
        f'batch_first_block={DEBUG_BLOCK} batch_last_block={DEBUG_BLOCK}'
    ) in caplog.text


def test_debug_tx_not_seen_skipped_when_block_out_of_batch_range(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
):
    """Case A distinction: the debug tx's block is NOT covered by the batch range,
    so no not_seen line is emitted (the block was simply never delivered here)."""
    monkeypatch.setenv('QUICKNODE_STREAMS_SECRET', SECRET)
    monkeypatch.setenv('QUICKNODE_STREAM_DEBUG_TX_HASH', TX_HASH)
    monkeypatch.setenv('QUICKNODE_STREAM_DEBUG_TX_BLOCK', str(DEBUG_BLOCK))
    target = _make_target()
    conn = _FakeConnection(targets=[target])
    # Batch is entirely below the debug tx's block.
    other_tx = {'tx_hash': OTHER_HASH, 'from': UNRELATED_ADDR, 'to': COUNTERPARTY, 'value': '1', 'block_number': DEBUG_BLOCK - 1000}
    raw = _body(other_tx)
    timestamp = _now_ts()
    signature = _sign(SECRET, nonce=NONCE, timestamp=timestamp, body=raw)
    with patch.object(qn, 'pg_connection', lambda: _mock_pg(conn)), \
         patch.object(qn, 'ensure_pilot_schema', lambda _c: None), \
         caplog.at_level('INFO', logger=_QN_LOGGER):
        qn.process_quicknode_base_stream_webhook(
            raw_body=raw, signature_header=signature, nonce_header=NONCE, timestamp_header=timestamp,
        )
    assert 'quicknode_stream_debug_tx_not_seen' not in caplog.text


# ---------------------------------------------------------------------------
# Ops token auth for the debug-tx endpoint (gated by QUICKNODE_STREAMS_SECRET).
# ---------------------------------------------------------------------------

def test_ops_token_missing_secret_rejected_503(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv('QUICKNODE_STREAMS_SECRET', raising=False)
    with pytest.raises(HTTPException) as exc:
        qn.verify_quicknode_ops_token(SECRET)
    assert exc.value.status_code == 503


def test_ops_token_wrong_or_missing_rejected_401(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv('QUICKNODE_STREAMS_SECRET', SECRET)
    with pytest.raises(HTTPException) as exc:
        qn.verify_quicknode_ops_token('wrong-token')
    assert exc.value.status_code == 401
    with pytest.raises(HTTPException) as exc2:
        qn.verify_quicknode_ops_token(None)
    assert exc2.value.status_code == 401


def test_ops_token_correct_accepted(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv('QUICKNODE_STREAMS_SECRET', SECRET)
    qn.verify_quicknode_ops_token(SECRET)  # no raise


def test_ops_token_error_never_leaks_secret(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv('QUICKNODE_STREAMS_SECRET', SECRET)
    with pytest.raises(HTTPException) as exc:
        qn.verify_quicknode_ops_token('wrong-token')
    assert SECRET not in str(exc.value.detail)


# ---------------------------------------------------------------------------
# Debug-tx ops endpoint: fetch tx/receipt from Base RPC and re-run the QuickNode
# matcher/dedupe logic. Read-only unless dry_run=false.
# ---------------------------------------------------------------------------

def _fake_rpc_client(*, tx, receipt=None, chain_id='0x2105'):
    """FailoverJsonRpcClient stand-in bound to a fixed tx/receipt/chain_id."""
    class _C:
        def __init__(self, urls):
            self.rpc_urls = urls
            self.active_host = 'fake-host'

        def call(self, method, params):
            if method == 'eth_chainId':
                return chain_id
            if method == 'eth_getTransactionByHash':
                return tx
            if method == 'eth_getTransactionReceipt':
                return receipt or {}
            return None
    return _C


def _rpc_tx(*, tx_from, tx_to=COUNTERPARTY, block='0x2d1a2c6', value='0xde0b6b3a7640000', chain_id='0x2105'):
    return {'hash': VALID_TX_HASH, 'from': tx_from, 'to': tx_to, 'value': value, 'blockNumber': block, 'chainId': chain_id}


def _patch_rpc(tx, receipt=None):
    from services.api.app import evm_activity_provider as eap
    return [
        patch.object(eap, 'resolve_chain_rpc', lambda net: {
            'network': net, 'expected_chain_id': 8453,
            'rpc_url': 'http://fake', 'rpc_urls': ['http://fake'],
        }),
        patch.object(eap, 'FailoverJsonRpcClient', _fake_rpc_client(tx=tx, receipt=receipt)),
    ]


def test_debug_tx_endpoint_matcher_no_wallet_match(monkeypatch: pytest.MonkeyPatch):
    target = _make_target()  # monitors WALLET_ADDR
    conn = _FakeConnection(targets=[target])
    tx = _rpc_tx(tx_from=UNRELATED_ADDR, tx_to=COUNTERPARTY)
    p1, p2 = _patch_rpc(tx)
    with p1, p2, \
         patch.object(qn, 'pg_connection', lambda: _mock_pg(conn)), \
         patch.object(qn, 'ensure_pilot_schema', lambda _c: None):
        result = qn.run_quicknode_debug_tx(tx_hash=VALID_TX_HASH, dry_run=True)
    assert result['tx_found'] is True
    assert result['matched_count'] == 0
    assert result['conclusion'] == 'matcher_no_wallet_match'
    assert conn.telemetry_inserts == []


def test_debug_tx_endpoint_would_match_and_persist_dry_run(monkeypatch: pytest.MonkeyPatch):
    target = _make_target()
    conn = _FakeConnection(targets=[target])
    tx = _rpc_tx(tx_from=WALLET_ADDR)
    p1, p2 = _patch_rpc(tx)
    with p1, p2, \
         patch.object(qn, 'pg_connection', lambda: _mock_pg(conn)), \
         patch.object(qn, 'ensure_pilot_schema', lambda _c: None):
        result = qn.run_quicknode_debug_tx(tx_hash=VALID_TX_HASH, dry_run=True)
    assert result['conclusion'] == 'would_match_and_persist'
    assert result['matched_count'] == 1
    assert result['persisted_count'] == 0
    entry = result['matched_targets'][0]
    assert entry['from_matches_target'] is True
    assert entry['duplicate_found'] is False
    assert entry['persisted'] is False
    assert entry['target_wallet_last4'] == WALLET_ADDR[-4:]
    # Dry-run must not write.
    assert conn.telemetry_inserts == []


def test_debug_tx_endpoint_duplicate_suppressed(monkeypatch: pytest.MonkeyPatch):
    target = _make_target()
    existing_row = {'id': str(uuid.uuid4()), 'event_type': 'native_transfer', 'detected_by': 'stable_rpc_polling'}
    conn = _FakeConnection(targets=[target], existing_telemetry=existing_row)
    tx = _rpc_tx(tx_from=WALLET_ADDR)
    # dry_run False, but a prior row exists -> still no insert.
    p1, p2 = _patch_rpc(tx)
    with p1, p2, \
         patch.object(qn, 'pg_connection', lambda: _mock_pg(conn)), \
         patch.object(qn, 'ensure_pilot_schema', lambda _c: None):
        result = qn.run_quicknode_debug_tx(tx_hash=VALID_TX_HASH, dry_run=False)
    assert result['conclusion'] == 'duplicate_suppressed'
    assert result['matched_count'] == 1
    assert result['duplicate_count'] == 1
    assert result['matched_targets'][0]['duplicate_found'] is True
    assert result['matched_targets'][0]['existing_detected_by'] == 'stable_rpc_polling'
    assert conn.telemetry_inserts == []


def test_debug_tx_endpoint_matched_and_persisted_when_dry_run_false(monkeypatch: pytest.MonkeyPatch):
    target = _make_target()
    conn = _FakeConnection(targets=[target])
    tx = _rpc_tx(tx_from=WALLET_ADDR)
    p1, p2 = _patch_rpc(tx)
    with p1, p2, \
         patch.object(qn, 'pg_connection', lambda: _mock_pg(conn)), \
         patch.object(qn, 'ensure_pilot_schema', lambda _c: None), \
         patch.object(qn, '_create_wallet_transfer_alert_chain', lambda **kw: {'smoke_alert_id': None, 'sig_alert_id': None}):
        result = qn.run_quicknode_debug_tx(tx_hash=VALID_TX_HASH, dry_run=False)
    assert result['conclusion'] == 'matched_and_persisted'
    assert result['persisted_count'] == 1
    assert result['matched_targets'][0]['persisted'] is True
    assert len(conn.telemetry_inserts) == 1
    inserted_payload = json.loads(conn.telemetry_inserts[0][9])
    # A manual debug-tx import is tagged distinctly from a live stream detection
    # (task requirement 4) so it is never mistaken for real-time QuickNode evidence.
    assert inserted_payload['detected_by'] == 'quicknode_stream_debug_import'
    assert inserted_payload['source_type'] == 'quicknode_stream_debug_import'
    assert inserted_payload['tx_hash'] == VALID_TX_HASH


def test_debug_tx_endpoint_tx_not_found_on_rpc(monkeypatch: pytest.MonkeyPatch):
    conn = _FakeConnection(targets=[_make_target()])
    p1, p2 = _patch_rpc(None)
    with p1, p2, \
         patch.object(qn, 'pg_connection', lambda: _mock_pg(conn)), \
         patch.object(qn, 'ensure_pilot_schema', lambda _c: None):
        result = qn.run_quicknode_debug_tx(tx_hash=VALID_TX_HASH, dry_run=True)
    assert result['tx_found'] is False
    assert result['conclusion'] == 'tx_not_found_on_rpc'
    assert conn.telemetry_inserts == []


def test_debug_tx_endpoint_rejects_bad_tx_hash():
    with pytest.raises(HTTPException) as exc:
        qn.run_quicknode_debug_tx(tx_hash='not-a-hash', dry_run=True)
    assert exc.value.status_code == 400


# ---------------------------------------------------------------------------
# Debug-tx route wiring + ops-token gate (services/api/app/main.py).
# ---------------------------------------------------------------------------

def test_debug_tx_route_requires_ops_token(monkeypatch: pytest.MonkeyPatch):
    from fastapi.testclient import TestClient

    from services.api.app import main as api_main

    monkeypatch.setenv('QUICKNODE_STREAMS_SECRET', SECRET)
    client = TestClient(api_main.app, raise_server_exceptions=False)
    response = client.get(
        '/api/integrations/quicknode/streams/base/debug-tx',
        params={'tx_hash': TX_HASH},
    )
    assert response.status_code == 401


def test_debug_tx_route_valid_token_reaches_handler(monkeypatch: pytest.MonkeyPatch):
    from fastapi.testclient import TestClient

    from services.api.app import main as api_main

    monkeypatch.setenv('QUICKNODE_STREAMS_SECRET', SECRET)
    monkeypatch.setattr(api_main, 'run_quicknode_debug_tx', lambda **kw: {'ok': True, 'echo': kw})
    monkeypatch.setattr(api_main, 'with_auth_schema_json', lambda handler: handler())
    client = TestClient(api_main.app, raise_server_exceptions=False)
    response = client.get(
        '/api/integrations/quicknode/streams/base/debug-tx',
        params={'tx_hash': TX_HASH, 'dry_run': 'false'},
        headers={'x-quicknode-ops-token': SECRET},
    )
    assert response.status_code == 200
    body = response.json()
    assert body['echo']['tx_hash'] == TX_HASH
    # dry_run query param parses to a real bool.
    assert body['echo']['dry_run'] is False


def test_debug_tx_route_defaults_to_dry_run_true(monkeypatch: pytest.MonkeyPatch):
    from fastapi.testclient import TestClient

    from services.api.app import main as api_main

    monkeypatch.setenv('QUICKNODE_STREAMS_SECRET', SECRET)
    monkeypatch.setattr(api_main, 'run_quicknode_debug_tx', lambda **kw: {'echo': kw})
    monkeypatch.setattr(api_main, 'with_auth_schema_json', lambda handler: handler())
    client = TestClient(api_main.app, raise_server_exceptions=False)
    response = client.get(
        '/api/integrations/quicknode/streams/base/debug-tx',
        params={'tx_hash': TX_HASH},
        headers={'x-quicknode-ops-token': SECRET},
    )
    assert response.status_code == 200
    assert response.json()['echo']['dry_run'] is True
