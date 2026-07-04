"""
Tests for the manual block-range backfill endpoint.

Verifies:
1. backfill_target_block_range finds matching wallet transfers and persists them
2. Deduplication: re-running the same range does not produce duplicate rows
3. Chain mismatch fails closed - backfill refuses when RPC chain_id ≠ target chain
4. fetch_evm_activity sets _evm_chain_mismatch on ethereum-mainnet targets using Base RPC
"""
from __future__ import annotations

import hashlib
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from services.api.app.evm_activity_provider import (
    fetch_evm_activity,
)


WALLET_ADDR = '0xaabbccdd00000000000000000000000000001234'
OTHER_ADDR  = '0x9999999900000000000000000000000000009999'
TX_HASH     = '0xdeadbeef1234567890abcdef1234567890abcdef1234567890abcdef12345678'
BLOCK_FROM  = 47_286_000
BLOCK_TO    = 47_287_000
USER_BLOCK  = 47_286_578
BASE_CHAIN  = 8453


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Shared mock helpers
# ---------------------------------------------------------------------------

class _Rows:
    def __init__(self, rows=None, rowcount: int = 1):
        self._rows = list(rows or [])
        self.rowcount = rowcount

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)


class _CaptureConn:
    """Records INSERT calls and returns configurable rows for SELECT."""

    def __init__(self, target_row: dict | None = None, rowcount: int = 1):
        self.inserts: list[tuple[str, tuple]] = []
        self._target_row = target_row
        self._rowcount = rowcount

    def execute(self, query: str, params=None):
        q = query.strip().lower()
        if q.startswith('insert'):
            table = q.split('insert into')[1].strip().split('(')[0].strip().split()[0]
            self.inserts.append((table, tuple(params or ())))
            return _Rows(rowcount=self._rowcount)
        if 'from targets' in q and self._target_row:
            return _Rows([self._target_row])
        return _Rows([])

    @contextmanager
    def transaction(self):
        yield


def _make_target(*, target_id=None, workspace_id=None, asset_id=None, chain='base', wallet=WALLET_ADDR):
    return {
        'id': target_id or str(uuid.uuid4()),
        'workspace_id': workspace_id or str(uuid.uuid4()),
        'asset_id': asset_id or str(uuid.uuid4()),
        'monitored_system_id': str(uuid.uuid4()),
        'chain_network': chain,
        'wallet_address': wallet,
        'contract_identifier': None,
        'name': 'Test Wallet',
        'target_type': 'wallet',
        'monitoring_checkpoint_cursor': None,
        'updated_by_user_id': None,
        'created_by_user_id': None,
    }


def _mock_block_with_tx(block_number: int = USER_BLOCK, from_addr: str = WALLET_ADDR, to_addr: str = OTHER_ADDR):
    return {
        'hash': '0xblockhash' + str(block_number),
        'timestamp': hex(int(_utcnow().timestamp())),
        'transactions': [
            {
                'hash': TX_HASH,
                'from': from_addr,
                'to': to_addr,
                'value': hex(10 ** 17),
                'input': '0x',
                'blockHash': '0xblockhash' + str(block_number),
            }
        ],
    }


def _empty_block(block_number: int):
    return {
        'hash': '0xblockhash' + str(block_number),
        'timestamp': hex(int(_utcnow().timestamp())),
        'transactions': [],
    }


# ---------------------------------------------------------------------------
# 1. backfill finds wallet transfers and persists them as native_transfer rows
# ---------------------------------------------------------------------------

def test_backfill_finds_matching_transfer_and_persists_it():
    """When a block contains a tx matching the monitored wallet, backfill persists a native_transfer row."""
    from services.api.app import monitoring_runner

    target = _make_target()
    workspace_id = str(target['workspace_id'])
    target_id = str(target['id'])

    rpc_responses: dict[str, Any] = {
        'eth_chainId': hex(BASE_CHAIN),
    }

    def _rpc_call(method, params):
        if method == 'eth_chainId':
            return hex(BASE_CHAIN)
        if method == 'eth_getBlockByNumber':
            block_num = int(params[0], 16)
            if block_num == USER_BLOCK:
                return _mock_block_with_tx(USER_BLOCK)
            return _empty_block(block_num)
        return None

    mock_client = MagicMock()
    mock_client.call.side_effect = _rpc_call

    capture = _CaptureConn(target_row=target, rowcount=1)

    class _FakeRequest:
        headers = {'x-workspace-id': workspace_id}

    with (
        patch('services.api.app.monitoring_runner.pg_connection', return_value=_ctx(capture)),
        patch('services.api.app.monitoring_runner._load_target_asset_context', return_value={}),
        patch('services.api.app.monitoring_runner._json_safe_value', side_effect=lambda x: x),
        patch('services.api.app.monitoring_runner.normalize_workspace_header_value', return_value=workspace_id),
        patch('services.api.app.evm_activity_provider.resolve_chain_rpc', return_value={
            'rpc_url': 'http://rpc.test',
            'rpc_urls': ['http://rpc.test'],
            'expected_chain_id': BASE_CHAIN,
            'rpc_url_env': 'BASE_EVM_RPC_URL',
            'network': 'base',
        }),
        patch('services.api.app.evm_activity_provider.FailoverJsonRpcClient', return_value=mock_client),
    ):
        result = monitoring_runner.backfill_target_block_range(
            _FakeRequest(),
            target_id,
            BLOCK_FROM,
            BLOCK_TO,
        )

    assert result['wallet_transfers_found'] >= 1, 'Expected at least one wallet transfer found'
    assert len(result['persisted_telemetry_ids']) >= 1, 'Expected telemetry row persisted'
    assert result['monitored_wallet'] == WALLET_ADDR.lower()
    assert result['chain_id'] == BASE_CHAIN

    # Verify the INSERT was against telemetry_events with event_type=native_transfer
    telem_inserts = [t for (t, _) in capture.inserts if 'telemetry_events' in t]
    assert len(telem_inserts) >= 1, 'Expected INSERT into telemetry_events'
    # The params tuple should contain 'native_transfer'
    first_params = capture.inserts[[t for (t, _) in capture.inserts].index('telemetry_events')][1]
    assert 'native_transfer' in first_params, f'Expected native_transfer in params: {first_params}'
    assert 'live' in first_params, 'Expected evidence_source=live'
    # Detected By must never be blank for wallet-transfer rows: the block-range
    # replay scans over the stable HTTPS RPC, so it is tagged stable_rpc_polling.
    import json as _json
    payload_str = next((p for p in first_params if isinstance(p, str) and 'tx_hash' in p), None)
    assert payload_str, 'Expected payload_json param'
    payload = _json.loads(payload_str)
    assert payload['detected_by'] == 'stable_rpc_polling', f'Expected detected_by=stable_rpc_polling, got {payload.get("detected_by")}'


# ---------------------------------------------------------------------------
# 2. Deduplication: rowcount=0 means ON CONFLICT DO NOTHING fired
# ---------------------------------------------------------------------------

def test_backfill_deduplication_does_not_add_to_persisted_ids():
    """When the DB returns rowcount=0 (conflict), the tx is not added to persisted_ids."""
    from services.api.app import monitoring_runner

    target = _make_target()
    workspace_id = str(target['workspace_id'])
    target_id = str(target['id'])

    def _rpc_call(method, params):
        if method == 'eth_chainId':
            return hex(BASE_CHAIN)
        if method == 'eth_getBlockByNumber':
            block_num = int(params[0], 16)
            if block_num == USER_BLOCK:
                return _mock_block_with_tx(USER_BLOCK)
            return _empty_block(block_num)
        return None

    mock_client = MagicMock()
    mock_client.call.side_effect = _rpc_call

    # rowcount=0 simulates ON CONFLICT DO NOTHING (already inserted)
    capture = _CaptureConn(target_row=target, rowcount=0)

    class _FakeRequest:
        headers = {'x-workspace-id': workspace_id}

    with (
        patch('services.api.app.monitoring_runner.pg_connection', return_value=_ctx(capture)),
        patch('services.api.app.monitoring_runner._load_target_asset_context', return_value={}),
        patch('services.api.app.monitoring_runner._json_safe_value', side_effect=lambda x: x),
        patch('services.api.app.monitoring_runner.normalize_workspace_header_value', return_value=workspace_id),
        patch('services.api.app.evm_activity_provider.resolve_chain_rpc', return_value={
            'rpc_url': 'http://rpc.test',
            'rpc_urls': ['http://rpc.test'],
            'expected_chain_id': BASE_CHAIN,
            'rpc_url_env': 'BASE_EVM_RPC_URL',
            'network': 'base',
        }),
        patch('services.api.app.evm_activity_provider.FailoverJsonRpcClient', return_value=mock_client),
    ):
        result = monitoring_runner.backfill_target_block_range(
            _FakeRequest(),
            target_id,
            BLOCK_FROM,
            BLOCK_TO,
        )

    assert result['wallet_transfers_found'] >= 1, 'Transfer still found even on duplicate'
    assert result['persisted_telemetry_ids'] == [], 'No new IDs when rowcount=0 (duplicate)'
    assert result['skipped_duplicates'] >= 1


# ---------------------------------------------------------------------------
# 3. Chain mismatch: backfill refuses if RPC chain_id ≠ expected
# ---------------------------------------------------------------------------

def test_backfill_fails_closed_on_chain_mismatch():
    """Backfill must raise 400 when the RPC serves a different chain than the target."""
    from fastapi import HTTPException
    from services.api.app import monitoring_runner

    target = _make_target(chain='ethereum')  # expects chain_id=1
    workspace_id = str(target['workspace_id'])
    target_id = str(target['id'])

    def _rpc_call(method, params):
        if method == 'eth_chainId':
            return hex(BASE_CHAIN)  # RPC serves Base, not Ethereum
        return None

    mock_client = MagicMock()
    mock_client.call.side_effect = _rpc_call

    capture = _CaptureConn(target_row=target)

    class _FakeRequest:
        headers = {'x-workspace-id': workspace_id}

    with (
        patch('services.api.app.monitoring_runner.pg_connection', return_value=_ctx(capture)),
        patch('services.api.app.monitoring_runner._load_target_asset_context', return_value={}),
        patch('services.api.app.monitoring_runner._json_safe_value', side_effect=lambda x: x),
        patch('services.api.app.monitoring_runner.normalize_workspace_header_value', return_value=workspace_id),
        patch('services.api.app.evm_activity_provider.resolve_chain_rpc', return_value={
            'rpc_url': 'http://rpc.test',
            'rpc_urls': ['http://rpc.test'],
            'expected_chain_id': 1,  # ethereum
            'rpc_url_env': 'EVM_RPC_URL',
            'network': 'ethereum',
        }),
        patch('services.api.app.evm_activity_provider.FailoverJsonRpcClient', return_value=mock_client),
    ):
        with pytest.raises(HTTPException) as exc_info:
            monitoring_runner.backfill_target_block_range(
                _FakeRequest(),
                target_id,
                BLOCK_FROM,
                BLOCK_TO,
            )

    assert exc_info.value.status_code == 400
    assert 'chain_id' in str(exc_info.value.detail).lower() or 'chain' in str(exc_info.value.detail).lower()


# ---------------------------------------------------------------------------
# 4. fetch_evm_activity sets _evm_chain_mismatch for ethereum target on Base RPC
# ---------------------------------------------------------------------------

def test_fetch_evm_activity_marks_chain_mismatch_for_ethereum_target_on_base_rpc():
    """When an ethereum-mainnet target is polled against a Base RPC, _evm_chain_mismatch must be set."""
    target = _make_target(chain='ethereum')  # expects chain_id=1

    mock_client = MagicMock()

    def _rpc_call(method, params):
        if method == 'eth_chainId':
            return hex(BASE_CHAIN)  # Base, not Ethereum
        if method == 'eth_blockNumber':
            return hex(47_327_000)
        return None

    mock_client.call.side_effect = _rpc_call

    with (
        patch('services.api.app.evm_activity_provider._resolve_evm_rpc_url', return_value='http://rpc.test'),
        patch('services.api.app.evm_activity_provider._resolve_evm_rpc_urls', return_value=['http://rpc.test']),
        patch('services.api.app.evm_activity_provider.FailoverJsonRpcClient', return_value=mock_client),
        patch.dict('os.environ', {'LIVE_MONITORING_CHAINS': 'ethereum', 'EVM_CHAIN_ID': '1'}),
    ):
        events = fetch_evm_activity(target, None, rpc_client=mock_client)

    assert events == [], 'Chain-mismatched targets must return no events'
    assert target.get('_evm_chain_mismatch') is True, '_evm_chain_mismatch must be set on target'
    assert 'chain_mismatch_reason' in str(target.get('_evm_chain_mismatch_reason', '')).lower() \
        or target.get('_evm_chain_mismatch_reason') is not None, \
        '_evm_chain_mismatch_reason must be set'


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

@contextmanager
def _ctx(conn):
    yield conn
