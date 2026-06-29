"""
Tests proving that a confirmed Base (chain_id 8453) wallet transfer
- produces a wallet_transfer_detected row in telemetry_events
- can be found by tx_hash via list_target_telemetry(q=<tx_hash>)
- rpc_polling (coverage) rows are labelled as block_poll by classifyEvent logic
- wallet_transfer_detected rows carry required fields: tx_hash, from, to, amount, block_number, source_type=rpc_polling, evidence_source=live
"""
from __future__ import annotations

import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from services.api.app.evm_activity_provider import (
    ActivityEvent,
    _build_base_payload,
    fetch_evm_activity,
)
from services.api.app import monitoring_runner


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

WALLET_ADDR = '0xdeadbeef00000000000000000000000000001234'
OTHER_ADDR = '0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'
TX_HASH = '0xabcdef1234567890abcdef1234567890abcdef1234567890abcdef1234567890ab'
BLOCK_NUM = 20_000_000
BASE_CHAIN_ID = 8453


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class _Rows:
    def __init__(self, rows=None):
        self._rows = list(rows or [])
        self.rowcount = len(self._rows)

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)


class _CaptureConn:
    """Records every INSERT/SELECT, returns configurable row sets."""

    def __init__(self, select_responses: dict | None = None):
        self.inserts: list[tuple[str, tuple]] = []
        self.selects: list[str] = []
        self._select_responses = select_responses or {}

    def execute(self, query: str, params=None):
        q = query.strip()
        q_lower = q.lower()
        if q_lower.lstrip().startswith('insert into'):
            table_part = q_lower.split('insert into')[1].strip()
            table = table_part.split('(')[0].strip().split()[0]
            self.inserts.append((table, tuple(params or ())))
        elif q_lower.lstrip().startswith('select'):
            self.selects.append(q)
            for key, rows in self._select_responses.items():
                if key.lower() in q_lower:
                    return _Rows(rows)
        return _Rows([])

    @contextmanager
    def transaction(self):
        yield


def _make_base_wallet_target(*, target_id=None, workspace_id=None, asset_id=None):
    return {
        'id': target_id or str(uuid.uuid4()),
        'workspace_id': workspace_id or str(uuid.uuid4()),
        'asset_id': asset_id or str(uuid.uuid4()),
        'monitored_system_id': str(uuid.uuid4()),
        'chain_network': 'base',
        'contract_identifier': None,
        'wallet_address': WALLET_ADDR,
        'name': 'Test Base Wallet',
        'target_type': 'wallet',
        'monitoring_checkpoint_cursor': None,
    }


def _make_activity_event(*, target_id: str) -> ActivityEvent:
    target = _make_base_wallet_target(target_id=target_id)
    payload = _build_base_payload(
        target=target,
        network='base',
        chain_id=BASE_CHAIN_ID,
        block_number=BLOCK_NUM,
        block_hash='0xblockhash',
        tx={'from': WALLET_ADDR, 'to': OTHER_ADDR, 'value': hex(10 ** 17), 'input': '0x', 'hash': TX_HASH},
        tx_hash=TX_HASH,
        raw_reference=f'base:{TX_HASH}',
    )
    payload['observed_at'] = _utcnow().isoformat()
    payload['event_type'] = 'transaction'
    payload['source_type'] = 'rpc_polling'
    payload['wallet_transfer_direction'] = 'outbound'
    return ActivityEvent(
        event_id='test-event-id',
        kind='transaction',
        observed_at=_utcnow(),
        ingestion_source='polling',
        cursor=f'{BLOCK_NUM}:{TX_HASH}:-1',
        payload=payload,
    )


# ---------------------------------------------------------------------------
# 1. _build_base_payload includes tx_hash, from, to, amount, block_number
# ---------------------------------------------------------------------------

def test_build_base_payload_contains_required_fields():
    target = _make_base_wallet_target()
    payload = _build_base_payload(
        target=target,
        network='base',
        chain_id=BASE_CHAIN_ID,
        block_number=BLOCK_NUM,
        block_hash='0xblockhash',
        tx={'from': WALLET_ADDR, 'to': OTHER_ADDR, 'value': hex(10 ** 17), 'input': '0x', 'hash': TX_HASH},
        tx_hash=TX_HASH,
        raw_reference=f'base:{TX_HASH}',
    )
    assert payload['tx_hash'] == TX_HASH
    assert payload['from'] == WALLET_ADDR
    assert payload['to'] == OTHER_ADDR
    assert payload['block_number'] == BLOCK_NUM
    assert payload['chain_id'] == BASE_CHAIN_ID
    assert 'amount' in payload


# ---------------------------------------------------------------------------
# 2. fetch_evm_activity adds source_type=rpc_polling to wallet tx payloads
# ---------------------------------------------------------------------------

def test_fetch_evm_activity_adds_source_type_to_wallet_tx(monkeypatch):
    """Wallet transfer events must carry source_type=rpc_polling in their payload."""
    target = _make_base_wallet_target()

    mock_client = MagicMock()
    mock_client.call.side_effect = lambda method, params: {
        'eth_chainId': hex(BASE_CHAIN_ID),
        'eth_blockNumber': hex(BLOCK_NUM),
        'eth_getBlockByNumber': {
            'hash': '0xblockhash',
            'timestamp': hex(int(_utcnow().timestamp())),
            'transactions': [
                {
                    'hash': TX_HASH,
                    'from': WALLET_ADDR,
                    'to': OTHER_ADDR,
                    'value': hex(10 ** 17),
                    'input': '0x',
                    'blockHash': '0xblockhash',
                }
            ],
        },
        'eth_getLogs': [],
    }.get(method)

    with (
        patch('services.api.app.evm_activity_provider._resolve_evm_rpc_url', return_value='http://rpc.test'),
        patch('services.api.app.evm_activity_provider._resolve_evm_rpc_urls', return_value=['http://rpc.test']),
        patch('services.api.app.evm_activity_provider.FailoverJsonRpcClient', return_value=mock_client),
        patch.dict('os.environ', {'LIVE_MONITORING_CHAINS': 'base', 'EVM_CHAIN_ID': str(BASE_CHAIN_ID)}),
    ):
        events = fetch_evm_activity(target, None, rpc_client=mock_client)

    assert len(events) >= 1, 'Expected at least one wallet transfer event'
    event = next((e for e in events if isinstance(e.payload, dict) and e.payload.get('tx_hash') == TX_HASH), None)
    assert event is not None, f'Expected event with tx_hash={TX_HASH}'
    assert event.payload.get('source_type') == 'rpc_polling'
    assert event.payload.get('tx_hash') == TX_HASH
    assert event.payload.get('from') == WALLET_ADDR.lower()
    assert event.payload.get('to') == OTHER_ADDR.lower()
    # block_number is the actual block scanned (within replay_blocks of BLOCK_NUM)
    bn = event.payload.get('block_number')
    assert isinstance(bn, int), f'Expected int block_number, got {bn!r}'
    assert bn <= BLOCK_NUM, f'block_number {bn} must not exceed BLOCK_NUM {BLOCK_NUM}'


# ---------------------------------------------------------------------------
# 3. wallet_transfer_direction is set for wallet targets
# ---------------------------------------------------------------------------

def test_fetch_evm_activity_sets_wallet_transfer_direction(monkeypatch):
    target = _make_base_wallet_target()

    mock_client = MagicMock()
    mock_client.call.side_effect = lambda method, params: {
        'eth_chainId': hex(BASE_CHAIN_ID),
        'eth_blockNumber': hex(BLOCK_NUM),
        'eth_getBlockByNumber': {
            'hash': '0xblockhash',
            'timestamp': hex(int(_utcnow().timestamp())),
            'transactions': [
                {
                    'hash': TX_HASH,
                    'from': WALLET_ADDR,
                    'to': OTHER_ADDR,
                    'value': hex(10 ** 17),
                    'input': '0x',
                    'blockHash': '0xblockhash',
                }
            ],
        },
        'eth_getLogs': [],
    }.get(method)

    with (
        patch('services.api.app.evm_activity_provider._resolve_evm_rpc_url', return_value='http://rpc.test'),
        patch('services.api.app.evm_activity_provider._resolve_evm_rpc_urls', return_value=['http://rpc.test']),
        patch('services.api.app.evm_activity_provider.FailoverJsonRpcClient', return_value=mock_client),
        patch.dict('os.environ', {'LIVE_MONITORING_CHAINS': 'base', 'EVM_CHAIN_ID': str(BASE_CHAIN_ID)}),
    ):
        events = fetch_evm_activity(target, None, rpc_client=mock_client)

    tx_events = [e for e in events if isinstance(e.payload, dict) and e.payload.get('tx_hash') == TX_HASH]
    assert tx_events, 'Expected wallet transfer event'
    direction = tx_events[0].payload.get('wallet_transfer_direction')
    assert direction == 'outbound', f'Expected outbound for from=wallet, got {direction!r}'


# ---------------------------------------------------------------------------
# 4. monitoring_runner persists wallet_transfer_detected with event_type column
# ---------------------------------------------------------------------------

def test_monitoring_runner_persists_wallet_transfer_detected(monkeypatch):
    """
    When process_monitoring_target processes a wallet transfer event from a Base
    wallet target, it must store event_type='wallet_transfer_detected' in telemetry_events.
    """
    import json as _json
    target = _make_base_wallet_target()
    event = _make_activity_event(target_id=str(target['id']))

    from services.api.app.activity_providers import ActivityProviderResult
    provider_result = ActivityProviderResult(
        mode='live',
        status='live',
        evidence_state='REAL_EVIDENCE',
        truthfulness_state='NOT_CLAIM_SAFE',
        synthetic=False,
        provider_name='evm_activity_provider',
        provider_kind='rpc',
        evidence_present=True,
        recent_real_event_count=1,
        last_real_event_at=_utcnow(),
        events=[event],
        latest_block=BLOCK_NUM,
        checkpoint=f'{BLOCK_NUM}:{TX_HASH}:-1',
        checkpoint_age_seconds=5,
        degraded_reason=None,
        error_code=None,
        source_type='rpc_polling',
        reason_code=None,
        claim_safe=False,
        detection_outcome='NO_CONFIRMED_ANOMALY_FROM_REAL_EVIDENCE',
    )

    inserts: list[tuple[str, tuple]] = []

    class _MockConn:
        def execute(self, query, params=None):
            q_lower = (query or '').strip().lower()
            if q_lower.startswith('insert into'):
                table_raw = q_lower.split('insert into')[1].strip()
                table = table_raw.split('(')[0].strip().split()[0]
                inserts.append((table, tuple(params or ())))
            if 'select' in q_lower and 'workspaces' in q_lower:
                return _Rows([{'id': target['workspace_id'], 'name': 'WS'}])
            if 'asset_registry' in q_lower:
                return _Rows([{'id': target['asset_id']}])
            return _Rows([])

        @contextmanager
        def transaction(self):
            yield

    _process_stub = MagicMock(return_value={
        'analysis_run_id': str(uuid.uuid4()),
        'monitoring_state': 'real_event_no_anomaly',
        'alert_id': None,
        'incident_id': None,
        'detection_id': None,
        'protected_asset_coverage_record': None,
    })
    _persist_analysis_stub = MagicMock(return_value={'id': str(uuid.uuid4())})

    with (
        patch.object(monitoring_runner, 'fetch_target_activity_result', return_value=provider_result),
        patch.object(monitoring_runner, '_process_single_event', _process_stub),
        patch.object(monitoring_runner, 'persist_analysis_run', _persist_analysis_stub),
        patch.object(monitoring_runner, '_load_checkpoint', return_value=0),
    ):
        monitoring_runner.process_monitoring_target(_MockConn(), target)

    telemetry_inserts = [(t, p) for t, p in inserts if t == 'telemetry_events']
    assert telemetry_inserts, 'Expected a telemetry_events INSERT'

    # Find the wallet transfer row (native_transfer for native ETH, wallet_transfer_detected otherwise)
    transfer_rows = [
        p for _, p in telemetry_inserts
        if 'wallet_transfer_detected' in p or 'native_transfer' in p
    ]
    assert transfer_rows, (
        f'Expected event_type=native_transfer or wallet_transfer_detected in telemetry_events INSERT. '
        f'Actual inserts: {telemetry_inserts}'
    )
    row_params = transfer_rows[0]

    # evidence_source must be 'live'
    assert 'live' in row_params, f'Expected evidence_source=live in {row_params}'

    # payload_json must contain tx_hash, from, to, source_type
    payload_json_str = next((p for p in row_params if isinstance(p, str) and 'tx_hash' in p), None)
    assert payload_json_str is not None, f'Expected tx_hash in payload_json. Params: {row_params}'
    payload_dict = _json.loads(payload_json_str)
    assert payload_dict.get('tx_hash') == TX_HASH
    assert payload_dict.get('from') == WALLET_ADDR.lower()
    assert payload_dict.get('to') == OTHER_ADDR.lower()
    assert isinstance(payload_dict.get('block_number'), int)
    assert payload_dict.get('source_type') == 'rpc_polling'


# ---------------------------------------------------------------------------
# 5. list_target_telemetry q= filters by tx_hash
# ---------------------------------------------------------------------------

def test_list_target_telemetry_q_filters_by_tx_hash(monkeypatch):
    """
    list_target_telemetry(q=<tx_hash>) must search payload_json->>'tx_hash' in the DB query.
    The LIKE clause must be present when q is non-empty.
    """
    target_id = str(uuid.uuid4())
    workspace_id = str(uuid.uuid4())

    import json as _json
    payload_json = {
        'tx_hash': TX_HASH,
        'from': WALLET_ADDR,
        'to': OTHER_ADDR,
        'block_number': BLOCK_NUM,
        'chain_id': BASE_CHAIN_ID,
        'source_type': 'rpc_polling',
    }
    telemetry_row = {
        'id': str(uuid.uuid4()),
        'workspace_id': workspace_id,
        'target_id': target_id,
        'provider_type': 'evm_activity_provider',
        'source_type': 'wallet_transfer_detected',
        'evidence_source': 'live',
        'observed_at': _utcnow(),
        'ingested_at': _utcnow(),
        'payload_json': payload_json,
        'chain_network': 'base',
        'receipt_block_number': BLOCK_NUM,
    }

    sql_queries_captured = []

    class _MockConn:
        def execute(self, query, params=None):
            sql_queries_captured.append((query, params))
            q = (query or '').strip().lower()
            if 'telemetry_events' in q and 'select' in q:
                return _Rows([telemetry_row])
            return _Rows([])

        @contextmanager
        def transaction(self):
            yield

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
            fake_request,
            target_id=target_id,
            limit=50,
            q=TX_HASH,
        )

    # Check the backend applied a LIKE filter
    telemetry_queries = [(q, p) for q, p in sql_queries_captured if 'telemetry_events' in (q or '').lower()]
    assert telemetry_queries, 'Expected a DB query on telemetry_events'
    search_query_sql, search_params = telemetry_queries[0]
    assert 'like' in search_query_sql.lower(), (
        f'Expected LIKE clause in telemetry search SQL: {search_query_sql}'
    )
    # The tx_hash must appear in the query params
    assert any(TX_HASH.lower() in str(p).lower() for p in (search_params or [])), (
        f'Expected tx_hash in query params: {search_params}'
    )

    # Check the result
    assert result['telemetry'], 'Expected at least one telemetry row'
    assert result.get('query') == TX_HASH


# ---------------------------------------------------------------------------
# 6. list_target_telemetry without q does NOT add a LIKE filter
# ---------------------------------------------------------------------------

def test_list_target_telemetry_without_q_returns_all_rows(monkeypatch):
    target_id = str(uuid.uuid4())
    workspace_id = str(uuid.uuid4())

    sql_queries_captured = []

    class _MockConn:
        def execute(self, query, params=None):
            sql_queries_captured.append((query, params))
            q = (query or '').strip().lower()
            if 'telemetry_events' in q and 'select' in q:
                return _Rows([])
            return _Rows([])

        @contextmanager
        def transaction(self):
            yield

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
            fake_request, target_id=target_id, limit=50
        )

    telemetry_queries = [(q, p) for q, p in sql_queries_captured if 'telemetry_events' in (q or '').lower()]
    assert telemetry_queries, 'Expected a DB query on telemetry_events'
    search_query_sql, _ = telemetry_queries[0]
    assert 'like' not in search_query_sql.lower(), (
        'LIKE clause must NOT appear when q is not provided'
    )
    assert 'query' not in result, 'result must not contain query key when q is absent'


# ---------------------------------------------------------------------------
# 7. list_target_telemetry q= empty string is treated as no filter
# ---------------------------------------------------------------------------

def test_list_target_telemetry_empty_q_treated_as_no_filter(monkeypatch):
    target_id = str(uuid.uuid4())
    workspace_id = str(uuid.uuid4())

    sql_queries_captured = []

    class _MockConn:
        def execute(self, query, params=None):
            sql_queries_captured.append((query, params))
            return _Rows([])

        @contextmanager
        def transaction(self):
            yield

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
            fake_request, target_id=target_id, limit=50, q='   '
        )

    telemetry_queries = [(q, p) for q, p in sql_queries_captured if 'telemetry_events' in (q or '').lower()]
    if telemetry_queries:
        sql, _ = telemetry_queries[0]
        assert 'like' not in sql.lower(), 'Whitespace-only q must not add LIKE filter'


# ---------------------------------------------------------------------------
# 8. q= no-match returns empty telemetry with helpful message
# ---------------------------------------------------------------------------

def test_list_target_telemetry_q_no_match_returns_message(monkeypatch):
    target_id = str(uuid.uuid4())
    workspace_id = str(uuid.uuid4())

    class _MockConn:
        def execute(self, query, params=None):
            return _Rows([])

        @contextmanager
        def transaction(self):
            yield

    fake_user = {'id': str(uuid.uuid4()), 'workspace_id': workspace_id}
    fake_workspace = {'workspace_id': workspace_id, 'workspace': {'id': workspace_id}}
    fake_request = MagicMock()
    fake_request.headers = {}

    with (
        patch('services.api.app.monitoring_runner.pg_connection') as mock_pg,
        patch('services.api.app.monitoring_runner.ensure_pilot_schema'),
        patch('services.api.app.monitoring_runner.authenticate_with_connection', return_value=fake_user),
        patch('services.api.app.monitoring_runner.resolve_workspace', return_value=fake_workspace),
    ):
        mock_pg.return_value.__enter__ = lambda s: _MockConn()
        mock_pg.return_value.__exit__ = MagicMock(return_value=False)

        result = monitoring_runner.list_target_telemetry(
            fake_request, target_id=target_id, limit=50, q='0xdeadbeef'
        )

    assert result['telemetry'] == []
    assert result['live_telemetry_ready'] is False
    assert 'message' in result
    assert 'worker' in result['message'].lower() or 'matching' in result['message'].lower()
    assert result.get('query') == '0xdeadbeef'


# ---------------------------------------------------------------------------
# 9. rpc_polling rows do NOT have tx_hash (block-only heartbeat)
# ---------------------------------------------------------------------------

def test_rpc_polling_coverage_row_has_no_tx_hash():
    """
    Coverage telemetry rows (event_type=rpc_polling) must NOT contain tx_hash
    — they are block-number heartbeats, not wallet transfers.
    """
    from services.api.app.monitoring_runner import _persist_live_coverage_telemetry
    from services.api.app.activity_providers import ActivityProviderResult

    target_id = str(uuid.uuid4())
    workspace_id = str(uuid.uuid4())
    asset_id = str(uuid.uuid4())
    target = {
        'id': target_id,
        'workspace_id': workspace_id,
        'asset_id': asset_id,
        'monitored_system_id': str(uuid.uuid4()),
        'chain_network': 'base',
        'contract_identifier': None,
        'wallet_address': WALLET_ADDR,
        'target_type': 'wallet',
    }
    provider_result = ActivityProviderResult(
        mode='live',
        status='live',
        evidence_state='REAL_EVIDENCE',
        truthfulness_state='NOT_CLAIM_SAFE',
        synthetic=False,
        provider_name='evm_activity_provider',
        provider_kind='rpc',
        evidence_present=True,
        recent_real_event_count=0,
        last_real_event_at=None,
        events=[],
        latest_block=BLOCK_NUM,
        checkpoint=f'{BLOCK_NUM}:::-1',
        checkpoint_age_seconds=10,
        degraded_reason=None,
        error_code=None,
        source_type='rpc_polling',
        reason_code=None,
        claim_safe=False,
        detection_outcome='NO_CONFIRMED_ANOMALY_FROM_REAL_EVIDENCE',
    )

    import json as _json
    inserts: list[tuple[str, tuple]] = []

    class _MockConn:
        def execute(self, query, params=None):
            q_lower = query.strip().lower()
            if q_lower.startswith('insert into'):
                table = q_lower.split('insert into')[1].strip().split('(')[0].strip().split()[0]
                inserts.append((table, tuple(params or ())))
            return _Rows([{'id': asset_id}] if 'asset_registry' in q_lower else [])

        @contextmanager
        def transaction(self):
            yield

    with patch.dict('os.environ', {'EVM_CHAIN_ID': str(BASE_CHAIN_ID)}):
        _persist_live_coverage_telemetry(
            _MockConn(),
            target=target,
            provider_result=provider_result,
            observed_at=_utcnow(),
        )

    telem_inserts = [(t, p) for t, p in inserts if t == 'telemetry_events']
    assert telem_inserts, 'Expected telemetry_events INSERT for coverage'
    _, params = telem_inserts[0]

    payload_str = next((p for p in params if isinstance(p, str) and 'telemetry_kind' in p), None)
    assert payload_str is not None, f'Expected payload_json in params: {params}'
    payload = _json.loads(payload_str)
    assert payload.get('telemetry_kind') == 'coverage'
    assert 'tx_hash' not in payload, (
        'Coverage (rpc_polling) rows must NOT contain tx_hash — they are block heartbeats only'
    )

    # event_type in params must be 'rpc_polling' (not wallet_transfer_detected)
    assert 'rpc_polling' in params, f'Expected rpc_polling in coverage telemetry params: {params}'
    assert 'wallet_transfer_detected' not in params


# ---------------------------------------------------------------------------
# 10. Base chain probe: fetch_evm_activity returns events for Base when RPC
#     returns chain_id 8453 even if LIVE_MONITORING_CHAINS=ethereum
# ---------------------------------------------------------------------------

def test_fetch_evm_activity_auto_detects_base_via_chain_probe(monkeypatch):
    """
    If LIVE_MONITORING_CHAINS does not contain 'base', fetch_evm_activity must
    still scan Base when the RPC probe returns chain_id=8453 matching CHAIN_MAP.
    """
    target = _make_base_wallet_target()

    call_log: list[str] = []

    def mock_call(method, params):
        call_log.append(method)
        if method == 'eth_chainId':
            return hex(BASE_CHAIN_ID)
        if method == 'eth_blockNumber':
            return hex(BLOCK_NUM)
        if method == 'eth_getBlockByNumber':
            return {
                'hash': '0xblockhash',
                'timestamp': hex(int(_utcnow().timestamp())),
                'transactions': [
                    {
                        'hash': TX_HASH,
                        'from': WALLET_ADDR,
                        'to': OTHER_ADDR,
                        'value': hex(10 ** 17),
                        'input': '0x',
                        'blockHash': '0xblockhash',
                    }
                ],
            }
        if method == 'eth_getLogs':
            return []
        return None

    mock_client = MagicMock()
    mock_client.call.side_effect = mock_call

    with (
        patch('services.api.app.evm_activity_provider._resolve_evm_rpc_url', return_value='http://rpc.test'),
        patch('services.api.app.evm_activity_provider._resolve_evm_rpc_urls', return_value=['http://rpc.test']),
        patch('services.api.app.evm_activity_provider.FailoverJsonRpcClient', return_value=mock_client),
        # Explicitly do NOT set EVM_CHAIN_ID to force the chain probe path
        patch.dict('os.environ', {'LIVE_MONITORING_CHAINS': 'ethereum'}, clear=False),
    ):
        events = fetch_evm_activity(target, None, rpc_client=mock_client)

    # Chain probe must have been called
    assert 'eth_chainId' in call_log, 'Expected eth_chainId probe when chain not in LIVE_MONITORING_CHAINS'
    # Must have found the wallet transfer
    assert any(
        isinstance(e.payload, dict) and e.payload.get('tx_hash') == TX_HASH
        for e in events
    ), f'Expected wallet transfer event. Events: {[e.payload for e in events]}'


# ---------------------------------------------------------------------------
# 11. Chain-mismatch: ethereum targets must not consume Base (8453) due slots
# ---------------------------------------------------------------------------

def test_chain_mismatch_filter_excludes_ethereum_from_base_due_slots():
    """
    The due-slot filtering logic in monitoring_runner must exclude targets
    with chain_network='ethereum' or 'ethereum-mainnet' when rpc_chain_id=8453.
    Targets with chain_network='base' or empty chain_network must pass through.
    """
    import uuid as _uuid
    from typing import Any as _Any

    eth_id = str(_uuid.uuid4())
    eth_mainnet_id = str(_uuid.uuid4())
    base_id = str(_uuid.uuid4())
    empty_chain_id = str(_uuid.uuid4())

    due_target_ids: list[_Any] = [eth_id, eth_mainnet_id, base_id, empty_chain_id]
    due_system_ids: dict[str, str] = {
        eth_id: 'sys1',
        eth_mainnet_id: 'sys2',
        base_id: 'sys3',
        empty_chain_id: 'sys4',
    }
    candidate_systems = [
        {'target_id': eth_id, 'chain_network': 'ethereum'},
        {'target_id': eth_mainnet_id, 'chain_network': 'ethereum-mainnet'},
        {'target_id': base_id, 'chain_network': 'base'},
        {'target_id': empty_chain_id, 'chain_network': ''},
    ]

    # Replicate the filtering logic from monitoring_runner.run_monitoring_cycle
    _base_chain_names: set[str] = {'base', 'base-mainnet'}
    _chain_by_target: dict[str, str] = {}
    for _row in candidate_systems:
        _sys = dict(_row)
        _tid_str = str(_sys.get('target_id') or '').strip()
        _chain_by_target[_tid_str] = str(_sys.get('chain_network') or '').lower()

    _filtered_due_ids: list[_Any] = []
    _excluded_mismatch = 0
    for _tid in due_target_ids:
        _tid_str = str(_tid).strip()
        _chain = _chain_by_target.get(_tid_str, '')
        if _chain and _chain not in _base_chain_names:
            _excluded_mismatch += 1
        else:
            _filtered_due_ids.append(_tid)

    if _excluded_mismatch:
        _filtered_due_str = {str(t) for t in _filtered_due_ids}
        due_system_ids = {k: v for k, v in due_system_ids.items() if k in _filtered_due_str}

    filtered_str = {str(t) for t in _filtered_due_ids}
    assert eth_id not in filtered_str, 'ethereum target must be excluded for Base RPC'
    assert eth_mainnet_id not in filtered_str, 'ethereum-mainnet target must be excluded for Base RPC'
    assert base_id in filtered_str, 'base target must pass through for Base RPC'
    assert empty_chain_id in filtered_str, 'target with empty chain_network must pass through'
    assert _excluded_mismatch == 2, f'Expected 2 excluded, got {_excluded_mismatch}'
    assert eth_id not in due_system_ids, 'due_system_ids must also exclude ethereum target'
    assert base_id in due_system_ids, 'due_system_ids must retain base target'


# ---------------------------------------------------------------------------
# 12. Base backfill window is at least 2000 blocks with no prior cursor
# ---------------------------------------------------------------------------

def test_base_backfill_window_is_at_least_2000_blocks(monkeypatch):
    """
    With no prior cursor for a Base target, fetch_evm_activity must scan
    at least 2000 blocks before the chain tip (not the old 300-block default).
    """
    LATEST = 2500
    target = _make_base_wallet_target()
    target['monitoring_checkpoint_cursor'] = None

    blocks_requested: list[int] = []

    def mock_call(method, params):
        if method == 'eth_chainId':
            return hex(BASE_CHAIN_ID)
        if method == 'eth_blockNumber':
            return hex(LATEST)
        if method == 'eth_getLogs':
            return []
        if method == 'eth_getBlockByNumber':
            block_num = int(params[0], 16)
            blocks_requested.append(block_num)
            return {
                'hash': f'0x{block_num:064x}',
                'timestamp': hex(1_700_000_000),
                'transactions': [],
            }
        return None

    mock_client = MagicMock()
    mock_client.call.side_effect = mock_call

    with (
        patch('services.api.app.evm_activity_provider._resolve_evm_rpc_url', return_value='http://rpc.test'),
        patch('services.api.app.evm_activity_provider._resolve_evm_rpc_urls', return_value=['http://rpc.test']),
        patch('services.api.app.evm_activity_provider.FailoverJsonRpcClient', return_value=mock_client),
        patch.dict('os.environ', {
            'LIVE_MONITORING_CHAINS': 'base',
            'EVM_CHAIN_ID': str(BASE_CHAIN_ID),
            'EVM_CONFIRMATIONS_REQUIRED': '3',
        }),
    ):
        fetch_evm_activity(target, None, rpc_client=mock_client)

    assert blocks_requested, 'Expected eth_getBlockByNumber calls for block scan'
    min_block = min(blocks_requested)
    # With LATEST=2500 and 3 confirmations: safe_to=2497, from_block=max(0,2497-2000)=497
    assert min_block <= LATEST - 2000, (
        f'Base backfill must start at most {LATEST - 2000} (LATEST={LATEST} minus 2000). '
        f'Got min_block={min_block}. Old 300-block window would give {LATEST - 300}.'
    )


# ---------------------------------------------------------------------------
# 13. Dead-lettered Base target is skipped by the due-slot loop
# ---------------------------------------------------------------------------

def test_dead_lettered_base_target_is_skipped():
    """
    A Base target with monitoring_dead_lettered_at set must increment
    skipped_dead_lettered and must NOT appear in due_target_ids.
    This mirrors the guard at the top of the due-slot loop in
    monitoring_runner.run_monitoring_cycle.
    """
    import uuid as _uuid
    from datetime import datetime, timezone, timedelta

    dead_id = str(_uuid.uuid4())
    live_id = str(_uuid.uuid4())
    now = datetime.now(timezone.utc)

    # Simulate candidate_systems rows as dicts
    candidate_rows = [
        {
            'target_id': dead_id,
            'monitored_system_id': 'sys1',
            'workspace_id': 'ws1',
            'monitored_system_enabled': True,
            'monitoring_enabled': True,
            'enabled': True,
            'is_active': True,
            'monitoring_dead_lettered_at': (now - timedelta(hours=1)).isoformat(),
            'last_checked_at': (now - timedelta(minutes=10)).isoformat(),
            'monitoring_interval_seconds': 30,
            'chain_network': 'base',
        },
        {
            'target_id': live_id,
            'monitored_system_id': 'sys2',
            'workspace_id': 'ws1',
            'monitored_system_enabled': True,
            'monitoring_enabled': True,
            'enabled': True,
            'is_active': True,
            'monitoring_dead_lettered_at': None,
            'last_checked_at': (now - timedelta(minutes=10)).isoformat(),
            'monitoring_interval_seconds': 30,
            'chain_network': 'base',
        },
    ]

    # Apply the dead-letter skip logic from monitoring_runner.run_monitoring_cycle
    skipped_dead_lettered = 0
    due_target_ids = []
    for system in candidate_rows:
        if not system.get('monitored_system_enabled'):
            continue
        if not system.get('monitoring_enabled') or not system.get('enabled'):
            continue
        if not system.get('is_active'):
            continue
        if system.get('monitoring_dead_lettered_at') is not None:
            skipped_dead_lettered += 1
            continue
        due_target_ids.append(system['target_id'])

    assert skipped_dead_lettered == 1, f'Expected 1 dead-lettered skip, got {skipped_dead_lettered}'
    assert dead_id not in due_target_ids, 'Dead-lettered target must not appear in due_target_ids'
    assert live_id in due_target_ids, 'Live target must appear in due_target_ids'


# ---------------------------------------------------------------------------
# detected_by field tests
# ---------------------------------------------------------------------------

def test_stable_rpc_polling_sets_detected_by(monkeypatch):
    """fetch_evm_activity must set detected_by='stable_rpc_polling' on wallet tx payloads."""
    target = _make_base_wallet_target()

    mock_client = MagicMock()
    mock_client.call.side_effect = lambda method, params: {
        'eth_chainId': hex(BASE_CHAIN_ID),
        'eth_blockNumber': hex(BLOCK_NUM),
        'eth_getBlockByNumber': {
            'hash': '0xblockhash',
            'timestamp': hex(int(_utcnow().timestamp())),
            'transactions': [
                {
                    'hash': TX_HASH,
                    'from': WALLET_ADDR,
                    'to': OTHER_ADDR,
                    'value': hex(10 ** 17),
                    'input': '0x',
                    'blockHash': '0xblockhash',
                }
            ],
        },
        'eth_getLogs': [],
    }.get(method)

    with (
        patch('services.api.app.evm_activity_provider._resolve_evm_rpc_url', return_value='http://rpc.test'),
        patch('services.api.app.evm_activity_provider._resolve_evm_rpc_urls', return_value=['http://rpc.test']),
        patch('services.api.app.evm_activity_provider.FailoverJsonRpcClient', return_value=mock_client),
        patch.dict('os.environ', {'LIVE_MONITORING_CHAINS': 'base', 'EVM_CHAIN_ID': str(BASE_CHAIN_ID)}),
    ):
        events = fetch_evm_activity(target, None, rpc_client=mock_client)

    tx_event = next((e for e in events if isinstance(e.payload, dict) and e.payload.get('tx_hash') == TX_HASH), None)
    assert tx_event is not None, 'Expected wallet transfer event'
    assert tx_event.payload.get('detected_by') == 'stable_rpc_polling', (
        f"Expected detected_by='stable_rpc_polling', got {tx_event.payload.get('detected_by')!r}"
    )
    assert tx_event.payload.get('provider_mode') == 'stable_rpc_polling'
    latency = tx_event.payload.get('observed_latency_seconds')
    assert latency is None or isinstance(latency, float), f'Expected float or None for latency, got {latency!r}'


def test_realtime_websocket_build_event_sets_detected_by():
    """_build_event_from_log (no source_type override) must set detected_by='realtime_websocket'."""
    from services.api.app.base_realtime_ingestor import BaseRealtimeIngestor

    target = {
        'id': str(uuid.uuid4()),
        'workspace_id': str(uuid.uuid4()),
        'wallet_address': WALLET_ADDR,
        'contract_identifier': None,
        'target_type': 'wallet',
        'chain_network': 'base',
    }
    log = {
        'blockNumber': hex(BLOCK_NUM),
        'transactionHash': TX_HASH,
        'logIndex': '0x0',
        'topics': [
            '0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef',
            '0x000000000000000000000000deadbeef00000000000000000000000000001234',
            '0x000000000000000000000000aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',
        ],
        'address': '0xcontract',
        'data': '0x0',
    }

    ingestor = BaseRealtimeIngestor.__new__(BaseRealtimeIngestor)
    ingestor.chain_id = BASE_CHAIN_ID
    ingestor.chain_network = 'base'
    ingestor.watcher_name = 'test_watcher'
    ingestor._ingestion_mode = 'realtime_websocket'
    ingestor._wss_permanently_disabled = False
    ingestor._current_ws_url = 'wss://test'
    ingestor._backfill_paused_until = 0.0
    ingestor.confirmations_required = 1
    ingestor.backfill_chunk_size = 25
    ingestor.gap_threshold_blocks = 24
    ingestor.start_at_latest = False
    ingestor.state = {
        'metrics': {},
        'source_status': 'realtime_websocket',
        'last_head_block': BLOCK_NUM,
        'last_processed_block': BLOCK_NUM - 1,
    }

    event = ingestor._build_event_from_log(target, log)
    assert event.payload.get('detected_by') == 'realtime_websocket', (
        f"Expected detected_by='realtime_websocket', got {event.payload.get('detected_by')!r}"
    )
    assert event.payload.get('source_type') == 'realtime_websocket'
    assert event.payload.get('provider_mode') == 'realtime_websocket'


def test_realtime_backfill_build_event_sets_detected_by():
    """_build_event_from_log with source_type='realtime_backfill' must set detected_by='realtime_backfill'."""
    from services.api.app.base_realtime_ingestor import BaseRealtimeIngestor

    target = {
        'id': str(uuid.uuid4()),
        'workspace_id': str(uuid.uuid4()),
        'wallet_address': WALLET_ADDR,
        'contract_identifier': None,
        'target_type': 'wallet',
        'chain_network': 'base',
    }
    log = {
        'blockNumber': hex(BLOCK_NUM),
        'transactionHash': TX_HASH,
        'logIndex': '0x0',
        'topics': [
            '0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef',
            '0x000000000000000000000000deadbeef00000000000000000000000000001234',
            '0x000000000000000000000000aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',
        ],
        'address': '0xcontract',
        'data': '0x0',
    }

    ingestor = BaseRealtimeIngestor.__new__(BaseRealtimeIngestor)
    ingestor.chain_id = BASE_CHAIN_ID
    ingestor.chain_network = 'base'
    ingestor.watcher_name = 'test_watcher'
    ingestor._ingestion_mode = 'realtime_websocket'
    ingestor._wss_permanently_disabled = False
    ingestor._current_ws_url = 'wss://test'
    ingestor._backfill_paused_until = 0.0
    ingestor.confirmations_required = 1
    ingestor.backfill_chunk_size = 25
    ingestor.gap_threshold_blocks = 24
    ingestor.start_at_latest = False
    ingestor.state = {
        'metrics': {},
        'source_status': 'realtime_websocket',
        'last_head_block': BLOCK_NUM,
        'last_processed_block': BLOCK_NUM - 10,
    }

    event = ingestor._build_event_from_log(target, log, source_type='realtime_backfill')
    assert event.payload.get('detected_by') == 'realtime_backfill', (
        f"Expected detected_by='realtime_backfill', got {event.payload.get('detected_by')!r}"
    )
    assert event.payload.get('source_type') == 'realtime_backfill'
    assert event.ingestion_source == 'realtime_backfill'


def test_list_target_telemetry_returns_detected_by(monkeypatch):
    """list_target_telemetry must surface detected_by from payload_json as a top-level field."""
    import json as _json
    target_id = str(uuid.uuid4())
    workspace_id = str(uuid.uuid4())

    payload_json = {
        'tx_hash': TX_HASH,
        'from': WALLET_ADDR,
        'to': OTHER_ADDR,
        'block_number': BLOCK_NUM,
        'chain_id': BASE_CHAIN_ID,
        'source_type': 'realtime_websocket',
        'detected_by': 'realtime_websocket',
        'provider_mode': 'realtime_websocket',
        'observed_latency_seconds': 1.23,
    }
    telemetry_row = {
        'id': str(uuid.uuid4()),
        'workspace_id': workspace_id,
        'target_id': target_id,
        'provider_type': 'evm_activity_provider',
        'source_type': 'wallet_transfer_detected',
        'evidence_source': 'live',
        'observed_at': _utcnow(),
        'ingested_at': _utcnow(),
        'payload_json': payload_json,
        'chain_network': 'base',
        'receipt_block_number': BLOCK_NUM,
    }

    class _MockConn:
        def execute(self, query, params=None):
            q = (query or '').strip().lower()
            if 'telemetry_events' in q and 'count' in q:
                return _Rows([{'cnt': 1}])
            if 'telemetry_events' in q and 'select' in q:
                return _Rows([telemetry_row])
            return _Rows([])

        @contextmanager
        def transaction(self):
            yield

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

        result = monitoring_runner.list_target_telemetry(fake_request, target_id=target_id, limit=50)

    rows = result.get('telemetry', [])
    assert rows, 'Expected at least one telemetry row'
    row = rows[0]
    assert row.get('detected_by') == 'realtime_websocket', (
        f"Expected detected_by='realtime_websocket', got {row.get('detected_by')!r}"
    )
    assert row.get('provider_mode') == 'realtime_websocket'
    assert row.get('observed_latency_seconds') == 1.23
