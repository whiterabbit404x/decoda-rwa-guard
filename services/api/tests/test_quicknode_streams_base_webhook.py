"""Tests for the QuickNode Streams (Base) webhook ingestion path.

POST /api/integrations/quicknode/streams/base
services/api/app/quicknode_streams.py

Covers:
  A. Valid HMAC signature is accepted.
  B. Invalid HMAC signature is rejected (400).
  C. Missing signature header is rejected (400).
  D. Missing configured secret is rejected (503) — fails closed.
  E. A transaction matching a monitored wallet persists wallet_transfer_detected
     with detected_by=quicknode_stream and source_type=quicknode_stream.
  F. A transaction NOT matching any monitored wallet is not persisted.
  G. A duplicate transaction (already recorded, e.g. by stable_rpc_polling) is
     suppressed instead of creating a second row.
  H. UI label mapping: quicknode_stream => "QuickNode Stream" (Python-side
     canonical detected_by classification mirrors the frontend label map).
"""
from __future__ import annotations

import hashlib
import hmac
import json
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


def _sign(secret: str, body: bytes) -> str:
    return hmac.new(secret.encode('utf-8'), body, hashlib.sha256).hexdigest()


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
    signature = _sign(SECRET, raw)
    qn.verify_quicknode_stream_signature(raw_body=raw, signature_header=signature)  # no raise


def test_invalid_hmac_signature_rejected(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv('QUICKNODE_STREAMS_SECRET', SECRET)
    raw = _body({'hash': TX_HASH})
    with pytest.raises(HTTPException) as exc:
        qn.verify_quicknode_stream_signature(raw_body=raw, signature_header='deadbeef')
    assert exc.value.status_code == 400


def test_missing_signature_header_rejected(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv('QUICKNODE_STREAMS_SECRET', SECRET)
    raw = _body({'hash': TX_HASH})
    with pytest.raises(HTTPException) as exc:
        qn.verify_quicknode_stream_signature(raw_body=raw, signature_header=None)
    assert exc.value.status_code == 400


def test_missing_secret_rejected_fail_closed(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv('QUICKNODE_STREAMS_SECRET', raising=False)
    raw = _body({'hash': TX_HASH})
    with pytest.raises(HTTPException) as exc:
        qn.verify_quicknode_stream_signature(raw_body=raw, signature_header='anything')
    assert exc.value.status_code == 503


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
    signature = _sign(SECRET, raw)
    with patch.object(qn, 'pg_connection', lambda: _mock_pg(conn)), \
         patch.object(qn, 'ensure_pilot_schema', lambda _c: None):
        result = qn.process_quicknode_base_stream_webhook(raw_body=raw, signature_header=signature)

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
    signature = _sign(SECRET, raw)
    with patch.object(qn, 'pg_connection', lambda: _mock_pg(conn)), \
         patch.object(qn, 'ensure_pilot_schema', lambda _c: None):
        result = qn.process_quicknode_base_stream_webhook(raw_body=raw, signature_header=signature)

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
    signature = _sign(SECRET, raw)
    with patch.object(qn, 'pg_connection', lambda: _mock_pg(conn)), \
         patch.object(qn, 'ensure_pilot_schema', lambda _c: None):
        result = qn.process_quicknode_base_stream_webhook(raw_body=raw, signature_header=signature)

    assert result['results'][0]['status'] == 'duplicate_suppressed'
    assert result['results'][0]['existing_detected_by'] == 'stable_rpc_polling'
    assert conn.telemetry_inserts == []
    assert conn.commit_calls == 0


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


def test_route_post_invalid_signature_rejected(monkeypatch: pytest.MonkeyPatch):
    from fastapi.testclient import TestClient

    from services.api.app import main as api_main

    monkeypatch.setenv('QUICKNODE_STREAMS_SECRET', SECRET)
    client = TestClient(api_main.app, raise_server_exceptions=False)
    body = _body({'tx_hash': TX_HASH, 'from': WALLET_ADDR})
    response = client.post(
        '/api/integrations/quicknode/streams/base',
        content=body,
        headers={'content-type': 'application/json', 'x-qn-signature': 'not-the-right-signature'},
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
        headers={'content-type': 'application/json', 'x-qn-signature': 'anything'},
    )
    assert response.status_code == 200
    assert response.json() == {'received': True, 'results': []}
