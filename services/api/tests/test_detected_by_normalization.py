"""Detected By must never be blank for wallet-transfer telemetry.

Production symptom: the newest "Wallet transfer detected" row rendered a blank
"Detected By" column even while the realtime worker was healthy — the row was
persisted by a path (ops import-tx / block-range backfill / ERC-20 log scan)
that never wrote payload_json.detected_by, and list_target_telemetry had no
fallback normalization.

Covers:
1. worker_status.resolve_telemetry_detected_by — canonical resolution from
   payload detected_by / details / metadata / source_type / ingestion facts.
2. list_target_telemetry — rows are normalized with those fallbacks; a live
   wallet-transfer row is NEVER returned with an empty detected_by (explicit
   'unknown' / evidence_source instead); top_row_detection_debug is returned
   for the newest row.
3. _persist_raw_wallet_transfer_telemetry — stamps a resolvable canonical
   detected_by into payload_json before insert (live rows only; never invents).
4. evm_activity_provider ERC-20 log path — stable-poller log events now carry
   detected_by=stable_rpc_polling.
"""
from __future__ import annotations

import json
import uuid
from typing import Any
from unittest.mock import MagicMock, patch

from services.api.app.worker_status import (
    resolve_telemetry_detected_by,
    WALLET_TRANSFER_EVENT_TYPES,
)
from services.api.app.monitoring_runner import list_target_telemetry

WALLET_ADDR = '0x5f6f35fd8b10c5576089f99c7c8c351deb851d1f'
TX_HASH = '0x7f2686fe2e2752c329c862f2ff8b0ac8947fc614bbcd58819c5b3b54d140e2ba'


# ---------------------------------------------------------------------------
# 1. resolve_telemetry_detected_by (pure)
# ---------------------------------------------------------------------------


def test_resolver_prefers_top_level_detected_by():
    assert resolve_telemetry_detected_by({'detected_by': 'realtime_websocket'}) == 'realtime_websocket'


def test_resolver_reads_details_detected_by():
    payload = {'details': {'detected_by': 'realtime_backfill'}}
    assert resolve_telemetry_detected_by(payload) == 'realtime_backfill'


def test_resolver_reads_metadata_detected_by():
    payload = {'metadata': {'detected_by': 'stable_rpc_polling'}}
    assert resolve_telemetry_detected_by(payload) == 'stable_rpc_polling'


def test_resolver_maps_tx_hash_import_source_type():
    assert resolve_telemetry_detected_by({'source_type': 'tx_hash_import'}) == 'realtime_tx_import'


def test_resolver_maps_rpc_polling_source_type_to_stable():
    assert resolve_telemetry_detected_by({'source_type': 'rpc_polling'}) == 'stable_rpc_polling'


def test_resolver_maps_ingestion_method_when_source_type_missing():
    assert resolve_telemetry_detected_by({'ingestion_method': 'tx_hash_import'}) == 'realtime_tx_import'


def test_resolver_maps_details_source_type():
    payload = {'details': {'source_type': 'realtime_websocket'}}
    assert resolve_telemetry_detected_by(payload) == 'realtime_websocket'


def test_resolver_never_invents_a_path():
    assert resolve_telemetry_detected_by({}) is None
    assert resolve_telemetry_detected_by(None) is None
    assert resolve_telemetry_detected_by({'ingestion_source': 'demo'}) is None
    assert resolve_telemetry_detected_by({'source_type': 'something_else'}) is None


def test_wallet_transfer_event_types_constant():
    assert 'wallet_transfer_detected' in WALLET_TRANSFER_EVENT_TYPES
    assert 'native_transfer' in WALLET_TRANSFER_EVENT_TYPES


# ---------------------------------------------------------------------------
# 2. list_target_telemetry normalization (mock pg_connection)
# ---------------------------------------------------------------------------


def _make_request(workspace_id: str) -> Any:
    scope = {
        'type': 'http',
        'method': 'GET',
        'path': '/monitoring/targets/x/telemetry',
        'query_string': b'',
        'headers': [(b'x-workspace-id', workspace_id.encode())],
        'client': ('127.0.0.1', 9000),
    }
    from fastapi import Request
    return Request(scope)


def _make_row(
    workspace_id: str,
    target_id: str,
    *,
    event_type: str = 'wallet_transfer_detected',
    evidence_source: str = 'live',
    payload: dict | None = None,
) -> dict:
    return {
        'id': str(uuid.uuid4()), 'workspace_id': workspace_id, 'target_id': target_id,
        'provider_type': 'evm_rpc', 'source_type': event_type,
        'evidence_source': evidence_source, 'observed_at': '2026-07-01T10:00:00Z',
        'ingested_at': '2026-07-01T10:00:01Z',
        'payload_json': payload if payload is not None else {'tx_hash': TX_HASH, 'block_number': 1000},
        'chain_network': 'base', 'receipt_block_number': None,
    }


class _Conn:
    """Minimal fake DB connection returning fixed rows for the data query."""

    def __init__(self, rows: list[dict]):
        self._rows = rows
        self.executed_sqls: list[str] = []

    def execute(self, sql: str, params: Any = None):
        self.executed_sqls.append(sql)
        rows = self._rows
        count = len(rows)

        class _Result:
            def fetchone(inner_self):
                return {'cnt': count}

            def fetchall(inner_self):
                # Plain dicts: production code calls dict(row) on dict-like rows.
                return [dict(row) for row in rows]

        return _Result()


def _run_telemetry(rows: list[dict], workspace_id: str, target_id: str) -> dict:
    conn = _Conn(rows)
    mock_pg = MagicMock()
    mock_pg.return_value.__enter__ = lambda s: conn
    mock_pg.return_value.__exit__ = MagicMock(return_value=False)
    with (
        patch('services.api.app.monitoring_runner.pg_connection', mock_pg),
        patch('services.api.app.monitoring_runner.ensure_pilot_schema'),
        patch(
            'services.api.app.monitoring_runner.authenticate_with_connection',
            return_value={'id': str(uuid.uuid4())},
        ),
        patch(
            'services.api.app.monitoring_runner.resolve_workspace',
            return_value={'workspace_id': workspace_id, 'workspace': {}},
        ),
    ):
        return list_target_telemetry(_make_request(workspace_id), target_id=target_id)


def test_api_maps_tx_hash_import_rows_to_realtime_tx_import():
    """The production bug: import-tx rows had source_type=tx_hash_import but no
    detected_by — the API must map them so the UI shows Realtime Tx Import."""
    ws, tgt = str(uuid.uuid4()), str(uuid.uuid4())
    row = _make_row(ws, tgt, payload={
        'tx_hash': TX_HASH, 'block_number': 47373543,
        'source_type': 'tx_hash_import', 'ingestion_method': 'tx_hash_import',
    })
    result = _run_telemetry([row], ws, tgt)
    assert result['telemetry'][0]['detected_by'] == 'realtime_tx_import'


def test_api_maps_details_and_metadata_detected_by():
    ws, tgt = str(uuid.uuid4()), str(uuid.uuid4())
    row_details = _make_row(ws, tgt, payload={
        'tx_hash': TX_HASH, 'details': {'detected_by': 'realtime_websocket'},
    })
    row_metadata = _make_row(ws, tgt, payload={
        'tx_hash': TX_HASH, 'metadata': {'detected_by': 'stable_rpc_polling'},
    })
    result = _run_telemetry([row_details, row_metadata], ws, tgt)
    assert result['telemetry'][0]['detected_by'] == 'realtime_websocket'
    assert result['telemetry'][1]['detected_by'] == 'stable_rpc_polling'


def test_api_live_wallet_transfer_row_never_blank_detected_by():
    """Acceptance: no blank Detected By for wallet transfer rows — an
    unattributable live row returns the explicit 'unknown'."""
    ws, tgt = str(uuid.uuid4()), str(uuid.uuid4())
    row = _make_row(ws, tgt, payload={'tx_hash': TX_HASH, 'block_number': 1})
    result = _run_telemetry([row], ws, tgt)
    detected_by = result['telemetry'][0]['detected_by']
    assert detected_by == 'unknown'
    assert detected_by  # non-empty


def test_api_simulator_wallet_row_reports_evidence_source_not_live_path():
    ws, tgt = str(uuid.uuid4()), str(uuid.uuid4())
    row = _make_row(ws, tgt, evidence_source='simulator', payload={'tx_hash': TX_HASH})
    result = _run_telemetry([row], ws, tgt)
    assert result['telemetry'][0]['detected_by'] == 'simulator'


def test_api_native_transfer_rows_also_normalized():
    ws, tgt = str(uuid.uuid4()), str(uuid.uuid4())
    row = _make_row(ws, tgt, event_type='native_transfer', payload={
        'tx_hash': TX_HASH, 'source_type': 'rpc_polling', 'backfill': True,
    })
    result = _run_telemetry([row], ws, tgt)
    assert result['telemetry'][0]['detected_by'] == 'stable_rpc_polling'


def test_api_returns_top_row_detection_debug_fields():
    """Requirement: the top telemetry row's detection-path facts are returned
    (and logged) so a blank Detected By can be traced end to end."""
    ws, tgt = str(uuid.uuid4()), str(uuid.uuid4())
    row = _make_row(ws, tgt, payload={
        'tx_hash': TX_HASH,
        'source_type': 'tx_hash_import',
        'detection_method': 'native_transfer_match',
        'details': {'detected_by': None, 'source_type': 'tx_hash_import'},
        'metadata': {'detected_by': None},
    })
    result = _run_telemetry([row], ws, tgt)
    debug = result['top_row_detection_debug']
    assert debug is not None
    for field in (
        'event_type', 'tx_hash', 'detected_by', 'source_type', 'evidence_source',
        'detection_method', 'details_detected_by', 'details_source_type',
        'metadata_detected_by',
    ):
        assert field in debug, f'missing top_row_detection_debug.{field}'
    assert debug['event_type'] == 'wallet_transfer_detected'
    assert debug['tx_hash'] == TX_HASH
    assert debug['detected_by'] == 'realtime_tx_import'
    assert debug['source_type'] == 'tx_hash_import'
    assert debug['evidence_source'] == 'live'
    assert debug['detection_method'] == 'native_transfer_match'
    assert debug['details_source_type'] == 'tx_hash_import'


def test_api_top_row_detection_debug_none_when_no_rows():
    ws, tgt = str(uuid.uuid4()), str(uuid.uuid4())
    result = _run_telemetry([], ws, tgt)
    assert result['top_row_detection_debug'] is None


def test_api_rows_expose_event_type_and_tx_hash():
    ws, tgt = str(uuid.uuid4()), str(uuid.uuid4())
    row = _make_row(ws, tgt, payload={'tx_hash': TX_HASH, 'detected_by': 'realtime_websocket'})
    result = _run_telemetry([row], ws, tgt)
    item = result['telemetry'][0]
    assert item['event_type'] == 'wallet_transfer_detected'
    assert item['tx_hash'] == TX_HASH
    assert item['detected_by'] == 'realtime_websocket'


# ---------------------------------------------------------------------------
# 3. _persist_raw_wallet_transfer_telemetry stamps detected_by before insert
# ---------------------------------------------------------------------------


class _InsertCaptureConn:
    def __init__(self):
        self.inserts: list[tuple] = []

    def execute(self, sql: str, params: Any = None):
        if sql.strip().lower().startswith('insert'):
            self.inserts.append(tuple(params or ()))

        class _Result:
            def fetchone(inner_self):
                return {'c': 1}

        return _Result()

    def commit(self):
        pass


def _run_persist(payload: dict, *, event_type: str = 'wallet_transfer_detected',
                 evidence_source: str = 'live') -> dict | None:
    from services.api.app import monitoring_runner as mr
    conn = _InsertCaptureConn()
    mock_pg = MagicMock()
    mock_pg.return_value.__enter__ = lambda s: conn
    mock_pg.return_value.__exit__ = MagicMock(return_value=False)
    with patch('services.api.app.monitoring_runner.pg_connection', mock_pg):
        mr._persist_raw_wallet_transfer_telemetry(
            MagicMock(),
            telemetry_id=str(uuid.uuid4()),
            workspace_id=str(uuid.uuid4()),
            asset_id=None,
            target_id=str(uuid.uuid4()),
            provider_type='evm_rpc',
            event_type=event_type,
            observed_at='2026-07-01T10:00:00Z',
            evidence_source=evidence_source,
            payload=payload,
            idempotency_key='k',
        )
    if not conn.inserts:
        return None
    payload_str = next((p for p in conn.inserts[0] if isinstance(p, str) and 'tx_hash' in p), None)
    return json.loads(payload_str) if payload_str else None


def test_persist_stamps_detected_by_from_source_type():
    persisted = _run_persist({'tx_hash': TX_HASH, 'source_type': 'tx_hash_import'})
    assert persisted is not None
    assert persisted['detected_by'] == 'realtime_tx_import'


def test_persist_keeps_existing_detected_by():
    persisted = _run_persist({'tx_hash': TX_HASH, 'detected_by': 'realtime_websocket'})
    assert persisted is not None
    assert persisted['detected_by'] == 'realtime_websocket'


def test_persist_never_invents_detected_by_for_unknown_live_row(caplog):
    import logging
    with caplog.at_level(logging.WARNING):
        persisted = _run_persist({'tx_hash': TX_HASH})
    assert persisted is not None
    assert 'detected_by' not in persisted
    assert any('wallet_transfer_missing_detected_by' in r.message for r in caplog.records)


def test_persist_does_not_stamp_simulator_rows():
    persisted = _run_persist(
        {'tx_hash': TX_HASH, 'source_type': 'rpc_polling'},
        evidence_source='simulator',
    )
    assert persisted is not None
    assert 'detected_by' not in persisted


# ---------------------------------------------------------------------------
# 4. Stable poller ERC-20 log path tags detected_by=stable_rpc_polling
# ---------------------------------------------------------------------------


def test_stable_poller_erc20_log_event_carries_detected_by(monkeypatch):
    from services.api.app import evm_activity_provider as eap

    monkeypatch.setenv('EVM_RPC_URL', 'http://rpc.test')
    monkeypatch.setenv('EVM_CHAIN_ID', '8453')
    monkeypatch.setenv('EVM_RPC_MAX_RETRIES', '0')
    monkeypatch.delenv('MONITOR_REPLAY_BLOCKS', raising=False)
    eap.reset_rpc_provider_state()

    latest = 10_000
    log_block = latest - 10
    transfer_log = {
        'address': '0x' + 'c' * 40,
        'topics': [
            eap.TRANSFER_TOPIC,
            '0x' + '0' * 24 + 'a' * 40,
            '0x' + '0' * 24 + WALLET_ADDR[2:],
        ],
        'data': hex(10 ** 18),
        'blockNumber': hex(log_block),
        'blockHash': '0xlogblockhash',
        'transactionHash': TX_HASH,
        'logIndex': '0x0',
    }

    class _Rpc:
        def call(self, method: str, params: list) -> object:
            if method == 'eth_chainId':
                return hex(8453)
            if method == 'eth_blockNumber':
                return hex(latest)
            if method == 'eth_getLogs':
                return [transfer_log]
            if method == 'eth_getTransactionByHash':
                return {
                    'hash': TX_HASH, 'from': '0x' + 'a' * 40, 'to': '0x' + 'c' * 40,
                    'value': '0x0', 'input': '0x', 'blockNumber': hex(log_block),
                    'blockHash': '0xlogblockhash',
                }
            if method in ('eth_getBlockByNumber', 'eth_getBlockByHash'):
                return {'hash': '0xlogblockhash', 'timestamp': hex(1_780_000_000), 'transactions': []}
            return {}

    target = {
        'id': str(uuid.uuid4()),
        'workspace_id': str(uuid.uuid4()),
        'target_type': 'wallet',
        'chain_network': 'base',
        'wallet_address': WALLET_ADDR,
        'contract_identifier': None,
        'monitoring_checkpoint_cursor': f'{latest - 50}:cp:-1',
    }
    events = eap.fetch_evm_activity(target, None, rpc_client=_Rpc())

    log_events = [
        e for e in events
        if isinstance(e.payload, dict) and e.payload.get('event_type') in {'transfer', 'approval'}
    ]
    assert log_events, f'expected an ERC-20 log event; got kinds={[getattr(e, "kind", None) for e in events]}'
    for event in log_events:
        assert event.payload['detected_by'] == 'stable_rpc_polling'
        assert event.payload['source_type'] == 'rpc_polling'
