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

import pytest
from fastapi import HTTPException

from services.api.app import quicknode_streams as qn

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

    def __init__(self, *, targets: list[dict], existing_telemetry: dict | None = None):
        self._targets = targets
        self._existing_telemetry = existing_telemetry
        self.telemetry_inserts: list[tuple] = []
        self.commit_calls = 0

    def execute(self, query, params=None):
        q = (query or '').strip().lower()
        if 'from targets' in q:
            return _Rows(self._targets)
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
