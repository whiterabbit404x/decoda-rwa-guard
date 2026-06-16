"""
Tests for ingest_tx_by_hash — direct tx hash ingestion bypassing block scan window.

Covers:
- Happy path: Base (chain_id 8453) native ETH transfer matched and persisted
- tx not found on RPC → returns imported=False, reason=transaction_not_found
- wallet not in tx from/to → returns imported=False, reason=wallet_not_in_tx
- chain_id mismatch in tx.chainId → returns imported=False, reason=chain_id_mismatch
- duplicate import → returns imported=False, reason=duplicate
- required log markers: tx_hash_import_started, tx_hash_import_match_found,
  tx_hash_import_persisted, tx_hash_import_skipped_reason
"""
from __future__ import annotations

import json
import logging
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from services.api.app import monitoring_runner

# ---------------------------------------------------------------------------
# Constants matching the known missed transaction
# ---------------------------------------------------------------------------

WALLET_ADDR = '0x5f6f35fd8b10c5576089f99c7c8c351deb851d1f'
OTHER_ADDR = '0xb1a215ff1da8c91cb96b3b760a8451ce839cf464'
TX_HASH = '0x7f2686fe2e2752c329c862f2ff8b0ac8947fc614bbcd58819c5b3b54d140e2ba'
BLOCK_NUMBER = 47373543
BASE_CHAIN_ID = 8453
AMOUNT_WEI = 10_000_000_000_000


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Shared test helpers
# ---------------------------------------------------------------------------


class _Rows:
    def __init__(self, rows=None):
        self._rows = list(rows or [])
        self.rowcount = len(self._rows)

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)


def _make_target(*, target_id=None, workspace_id=None, asset_id=None, wallet=WALLET_ADDR):
    return {
        'id': target_id or str(uuid.uuid4()),
        'workspace_id': workspace_id or str(uuid.uuid4()),
        'asset_id': asset_id or str(uuid.uuid4()),
        'chain_network': 'base',
        'wallet_address': wallet,
        'contract_identifier': None,
        'target_type': 'wallet',
        'monitoring_checkpoint_cursor': None,
        'deleted_at': None,
    }


def _make_rpc_client(*, tx=None, receipt=None, chain_id=BASE_CHAIN_ID, block_ts=None):
    """Return a mock RPC client that serves a single tx by hash."""
    block_ts_hex = hex(int(block_ts or _utcnow().timestamp()))
    mock = MagicMock()

    def _call(method, params):
        if method == 'eth_chainId':
            return hex(chain_id)
        if method == 'eth_getTransactionByHash':
            return tx
        if method == 'eth_getTransactionReceipt':
            return receipt
        if method in ('eth_getBlockByHash', 'eth_getBlockByNumber'):
            return {'hash': '0xblockhash', 'timestamp': block_ts_hex, 'transactions': []}
        return None

    mock.call.side_effect = _call
    return mock


def _make_tx(*, from_addr=WALLET_ADDR, to_addr=OTHER_ADDR, chain_id=BASE_CHAIN_ID, value=AMOUNT_WEI):
    return {
        'hash': TX_HASH,
        'from': from_addr,
        'to': to_addr,
        'value': hex(value),
        'input': '0x',
        'blockNumber': hex(BLOCK_NUMBER),
        'blockHash': '0xblockhash',
        'chainId': hex(chain_id),
    }


def _make_receipt():
    return {
        'transactionHash': TX_HASH,
        'blockNumber': hex(BLOCK_NUMBER),
        'blockHash': '0xblockhash',
        'status': '0x1',
        'gasUsed': hex(21000),
    }


class _CaptureConn:
    """Captures INSERTs; returns configurable SELECT responses."""

    def __init__(self, target_row=None, insert_rowcount=1):
        self._target_row = target_row
        self._insert_rowcount = insert_rowcount
        self.inserts: list[tuple[str, tuple]] = []

    def execute(self, query: str, params=None):
        q = (query or '').strip().lower()
        if q.startswith('insert into'):
            table = q.split('insert into')[1].strip().split('(')[0].strip().split()[0]
            self.inserts.append((table, tuple(params or ())))
            r = _Rows([('dummy',)] if self._insert_rowcount > 0 else [])
            r.rowcount = self._insert_rowcount
            return r
        if q.startswith('select') and 'targets' in q:
            return _Rows([self._target_row] if self._target_row else [])
        return _Rows([])

    @contextmanager
    def transaction(self):
        yield


def _fake_request(workspace_id: str) -> MagicMock:
    req = MagicMock()
    req.headers = {'x-workspace-id': workspace_id}
    return req


def _run_ingest(target, rpc_client, insert_rowcount=1, tx_hash=TX_HASH):
    workspace_id = str(target['workspace_id'])
    target_id = str(target['id'])
    conn = _CaptureConn(target_row=target, insert_rowcount=insert_rowcount)

    # FailoverJsonRpcClient is imported locally inside ingest_tx_by_hash, so it
    # resolves from evm_activity_provider at call time. Patch there.
    with (
        patch('services.api.app.monitoring_runner.pg_connection') as mock_pg,
        patch('services.api.app.evm_activity_provider.FailoverJsonRpcClient', return_value=rpc_client),
        patch('services.api.app.evm_activity_provider.resolve_chain_rpc', return_value={
            'rpc_url': 'http://rpc.test',
            'rpc_urls': ['http://rpc.test'],
            'expected_chain_id': BASE_CHAIN_ID,
            'rpc_url_env': 'EVM_RPC_URL_8453',
            'network': 'base',
        }),
        patch.dict('os.environ', {'EVM_RPC_URL_8453': 'http://rpc.test', 'EVM_CHAIN_ID': str(BASE_CHAIN_ID)}),
    ):
        mock_pg.return_value.__enter__ = lambda s: conn
        mock_pg.return_value.__exit__ = MagicMock(return_value=False)
        result = monitoring_runner.ingest_tx_by_hash(
            _fake_request(workspace_id),
            target_id,
            tx_hash,
        )
    return result, conn


# ---------------------------------------------------------------------------
# 1. Happy path: matching Base native ETH transfer is persisted
# ---------------------------------------------------------------------------


def test_ingest_tx_by_hash_persists_matching_base_transfer():
    target = _make_target()
    tx = _make_tx()
    receipt = _make_receipt()
    rpc = _make_rpc_client(tx=tx, receipt=receipt)

    result, conn = _run_ingest(target, rpc)

    assert result['imported'] is True, f'Expected imported=True, got {result}'
    assert result['tx_hash'] == TX_HASH.lower()
    assert result['direction'] == 'outbound'
    assert result['chain_id'] == BASE_CHAIN_ID
    assert result['block_number'] == BLOCK_NUMBER
    assert result['amount_wei'] == str(AMOUNT_WEI)
    assert 'telemetry_id' in result

    telem_inserts = [(t, p) for t, p in conn.inserts if t == 'telemetry_events']
    assert telem_inserts, 'Expected telemetry_events INSERT'
    _, params = telem_inserts[0]
    assert 'wallet_transfer_detected' in params, f'Expected event_type=wallet_transfer_detected in {params}'
    assert 'live' in params, f'Expected evidence_source=live in {params}'
    payload_str = next((p for p in params if isinstance(p, str) and 'tx_hash' in p), None)
    assert payload_str, 'Expected payload_json with tx_hash'
    payload = json.loads(payload_str)
    assert payload['tx_hash'] == TX_HASH.lower()
    assert payload['source_type'] == 'tx_hash_import'
    assert payload['ingestion_method'] == 'tx_hash_import'
    assert payload['wallet_transfer_direction'] == 'outbound'


# ---------------------------------------------------------------------------
# 2. Inbound transfer: tx.to == monitored wallet
# ---------------------------------------------------------------------------


def test_ingest_tx_by_hash_inbound_direction():
    target = _make_target()
    tx = _make_tx(from_addr=OTHER_ADDR, to_addr=WALLET_ADDR)
    rpc = _make_rpc_client(tx=tx, receipt=_make_receipt())

    result, _ = _run_ingest(target, rpc)

    assert result['imported'] is True
    assert result['direction'] == 'inbound'


# ---------------------------------------------------------------------------
# 3. Transaction not found on RPC
# ---------------------------------------------------------------------------


def test_ingest_tx_by_hash_not_found():
    target = _make_target()
    rpc = _make_rpc_client(tx=None)  # RPC returns null

    result, conn = _run_ingest(target, rpc)

    assert result['imported'] is False
    assert result['reason'] == 'transaction_not_found'
    telem_inserts = [(t, p) for t, p in conn.inserts if t == 'telemetry_events']
    assert not telem_inserts, 'Must not persist telemetry when tx not found'


# ---------------------------------------------------------------------------
# 4. Wallet not in tx from/to
# ---------------------------------------------------------------------------


def test_ingest_tx_by_hash_wallet_not_in_tx():
    target = _make_target()
    unrelated_from = '0x' + 'a' * 40
    unrelated_to = '0x' + 'b' * 40
    tx = _make_tx(from_addr=unrelated_from, to_addr=unrelated_to)
    rpc = _make_rpc_client(tx=tx, receipt=_make_receipt())

    result, conn = _run_ingest(target, rpc)

    assert result['imported'] is False
    assert result['reason'] == 'wallet_not_in_tx'
    assert result['monitored_wallet'] == WALLET_ADDR.lower()
    telem_inserts = [(t, p) for t, p in conn.inserts if t == 'telemetry_events']
    assert not telem_inserts


# ---------------------------------------------------------------------------
# 5. chain_id mismatch in tx.chainId
# ---------------------------------------------------------------------------


def test_ingest_tx_by_hash_chain_id_mismatch():
    target = _make_target()
    WRONG_CHAIN = 1  # Ethereum mainnet, not Base
    tx = _make_tx(chain_id=WRONG_CHAIN)
    rpc = _make_rpc_client(tx=tx, receipt=_make_receipt())

    result, conn = _run_ingest(target, rpc)

    assert result['imported'] is False
    assert result['reason'] == 'chain_id_mismatch'
    assert result['tx_chain_id'] == WRONG_CHAIN
    assert result['expected_chain_id'] == BASE_CHAIN_ID
    telem_inserts = [(t, p) for t, p in conn.inserts if t == 'telemetry_events']
    assert not telem_inserts


# ---------------------------------------------------------------------------
# 6. Duplicate import is idempotent (ON CONFLICT DO NOTHING → rowcount=0)
# ---------------------------------------------------------------------------


def test_ingest_tx_by_hash_duplicate_is_idempotent():
    target = _make_target()
    tx = _make_tx()
    rpc = _make_rpc_client(tx=tx, receipt=_make_receipt())

    result, conn = _run_ingest(target, rpc, insert_rowcount=0)

    assert result['imported'] is False
    assert result['reason'] == 'duplicate'
    assert result['block_number'] == BLOCK_NUMBER
    assert result['direction'] == 'outbound'


# ---------------------------------------------------------------------------
# 7. Log markers are emitted at the right stages
# ---------------------------------------------------------------------------


def test_ingest_tx_by_hash_emits_started_and_match_found_logs(caplog):
    target = _make_target()
    tx = _make_tx()
    rpc = _make_rpc_client(tx=tx, receipt=_make_receipt())

    with caplog.at_level(logging.INFO, logger='services.api.app.monitoring_runner'):
        _run_ingest(target, rpc)

    messages = ' '.join(caplog.messages)
    assert 'tx_hash_import_started' in messages
    assert 'tx_hash_import_match_found' in messages
    assert 'tx_hash_import_persisted' in messages


def test_ingest_tx_by_hash_emits_skipped_reason_when_not_found(caplog):
    target = _make_target()
    rpc = _make_rpc_client(tx=None)

    with caplog.at_level(logging.INFO, logger='services.api.app.monitoring_runner'):
        _run_ingest(target, rpc)

    messages = ' '.join(caplog.messages)
    assert 'tx_hash_import_started' in messages
    assert 'tx_hash_import_skipped_reason' in messages


def test_ingest_tx_by_hash_emits_skipped_reason_when_wallet_mismatch(caplog):
    target = _make_target()
    tx = _make_tx(from_addr='0x' + 'a' * 40, to_addr='0x' + 'b' * 40)
    rpc = _make_rpc_client(tx=tx, receipt=_make_receipt())

    with caplog.at_level(logging.INFO, logger='services.api.app.monitoring_runner'):
        _run_ingest(target, rpc)

    messages = ' '.join(caplog.messages)
    assert 'tx_hash_import_skipped_reason' in messages
    assert 'wallet_not_in_tx' in messages


# ---------------------------------------------------------------------------
# 8. Invalid tx_hash format raises 400
# ---------------------------------------------------------------------------


def test_ingest_tx_by_hash_invalid_format_raises_400():
    from fastapi import HTTPException

    target = _make_target()
    workspace_id = str(target['workspace_id'])
    target_id = str(target['id'])
    conn = _CaptureConn(target_row=target)

    with (
        patch('services.api.app.monitoring_runner.pg_connection') as mock_pg,
        patch.dict('os.environ', {'EVM_CHAIN_ID': str(BASE_CHAIN_ID)}),
    ):
        mock_pg.return_value.__enter__ = lambda s: conn
        mock_pg.return_value.__exit__ = MagicMock(return_value=False)

        with pytest.raises(HTTPException) as exc_info:
            monitoring_runner.ingest_tx_by_hash(
                _fake_request(workspace_id),
                target_id,
                'not-a-valid-hash',
            )

    assert exc_info.value.status_code == 400
    assert 'tx_hash' in exc_info.value.detail.lower()


# ---------------------------------------------------------------------------
# 9. Payload fields: receipt status and gas_used included
# ---------------------------------------------------------------------------


def test_ingest_tx_by_hash_includes_receipt_fields():
    target = _make_target()
    tx = _make_tx()
    receipt = {**_make_receipt(), 'status': '0x1', 'gasUsed': hex(21_000)}
    rpc = _make_rpc_client(tx=tx, receipt=receipt)

    result, conn = _run_ingest(target, rpc)

    assert result['imported'] is True
    telem_inserts = [(t, p) for t, p in conn.inserts if t == 'telemetry_events']
    _, params = telem_inserts[0]
    payload_str = next((p for p in params if isinstance(p, str) and 'tx_hash' in p), None)
    payload = json.loads(payload_str)
    assert payload.get('tx_status') == 1
    assert payload.get('gas_used') == 21_000
